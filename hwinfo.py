# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Automatic parallelism based on hardware specs.

Worker counts derive from the machine's resources: CPU core count, RAM
size (on low-RAM machines a pile of concurrent ffmpeg/7z processes
choked the whole system), and the type of the disk hosting the files.
The MC_VIDEO_WORKERS and MC_ARCHIVE_WORKERS environment variables keep
manual overrides available for troubleshooting.

The disk type is auto-detected too — on spinning HDDs the
heavy workers go conservative (video
~8, archives ~4), on SSD/NVMe the previous fast caps remain. Detection on
Windows uses IOCTL_STORAGE_QUERY_PROPERTY (IncursSeekPenalty) with no admin
rights needed, on Linux /sys/block/<disk>/queue/rotational; unknown = the
previous fast behavior (none of this is hardcoded — every machine measures
itself).
"""
from __future__ import annotations

from i18n import T

import os
from watchtime import dbg_log

_RAM_GB: float | None = None


def cpu_cores() -> int:
    return max(2, os.cpu_count() or 4)


def ram_gb() -> float:
    """Installed RAM in GB — stdlib only, no packages (ctypes / /proc)."""
    global _RAM_GB
    if _RAM_GB is not None:
        return _RAM_GB
    ram = 8.0                                   # conservative default when detection fails
    try:
        if os.name == "nt":
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTAT()
            st.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                ram = st.ullTotalPhys / (1024 ** 3)
        else:
            with open("/proc/meminfo", encoding="ascii",
                      errors="replace") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        ram = int(line.split()[1]) / (1024 ** 2)
                        break
    except Exception as _exc_dbg:
        dbg_log(f"ram_gb failed: {type(_exc_dbg).__name__}")
        pass
    _RAM_GB = max(1.0, ram)
    return _RAM_GB


def _env_int(name: str) -> int | None:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- disk type
_SEEK_CACHE: dict = {}

# ----------------------------------------------------------------- performance mode
# From the settings review: manual control over how conservative
# or aggressive the worker counts are — so that if disk auto-detection is
# wrong/unstable on some machine (the "fast one run, slow the next" puzzle),
# the user can pin it down.
_PERF_MODE = "auto"          # auto | high | safe
_PERF_OK = ("auto", "high", "safe")


def set_perf_mode(mode: str) -> None:
    """User override: high → always fast, safe → always conservative, auto → detect."""
    global _PERF_MODE
    _PERF_MODE = mode if mode in _PERF_OK else "auto"


def perf_mode() -> str:
    return _PERF_MODE


def _resolve_rotational(detected):
    """Apply the performance mode to the auto-detected result (None stays None)."""
    if _PERF_MODE == "high":
        return False
    if _PERF_MODE == "safe":
        return True
    return detected


def load_kinds(d) -> None:
    """Inject persisted disk-detection results from config at boot.

    Goal: run-to-run stability — the low-level drive query (IOCTL) sometimes
    answers differently depending on the driver's cache memory/USB state,
    which made the worker count swing between two runs; the first successful
    answer becomes the permanent baseline."""
    try:
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(k, str) and isinstance(v, bool) and k not in _SEEK_CACHE:
                    _SEEK_CACHE[k] = v
    except Exception as _exc_dbg:
        dbg_log(f"load_kinds failed: {type(_exc_dbg).__name__}")
        pass


def persisted_kinds() -> dict:
    """Results available for saving into config (only definite True/False answers)."""
    return {k: v for k, v in _SEEK_CACHE.items() if isinstance(v, bool)}


def _win_seek_penalty(drv: str) -> "bool | None":
    """Ask Windows for IncursSeekPenalty — True = spinning (HDD), False = SSD.

    IOCTL_STORAGE_QUERY_PROPERTY with StorageDeviceSeekPenaltyProperty; the
    handle opens with zero access so no admin rights are needed. Any error /
    unreporting drive -> None.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW(
        "\\\\.\\" + drv, 0, 7, None, 3, 0, None)   # access=0, share=R/W/DEL, OPEN_EXISTING
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        query = (ctypes.c_ulong * 3)(7, 0, 0)      # PropertyId=7 (SeekPenalty), StandardQuery
        out = ctypes.create_string_buffer(16)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(
            h, 0x2D1400, query, ctypes.sizeof(query), out, 16,
            ctypes.byref(ret), None)               # IOCTL_STORAGE_QUERY_PROPERTY
        if not ok or ret.value < 9:
            return None
        return bool(out.raw[8])                    # DEVICE_SEEK_PENALTY_DESCRIPTOR.IncursSeekPenalty
    except Exception as _exc_dbg:
        dbg_log(f"_win_seek_penalty failed: {type(_exc_dbg).__name__}")
        return None
    finally:
        k32.CloseHandle(h)


def _posix_seek_penalty(path: str) -> "bool | None":
    """On Linux/macOS: /sys/block/<disk>/queue/rotational via the hosting device."""
    try:
        import re
        import sys
        p = str(path)
        while p and not os.path.exists(p):
            nxt = os.path.dirname(p)
            if nxt == p:
                return None
            p = nxt
        if not p:
            return None
        dev = os.stat(p).st_dev
        if sys.platform == "darwin":                # macOS: modern disks are SSDs; unknown
            return None
        lnk = f"/dev/block/{os.major(dev)}:{os.minor(dev)}"
        name = os.path.basename(os.path.realpath(lnk)) or None
        if not name:
            return None
        base = name
        if not os.path.exists(f"/sys/block/{base}"):
            m = re.match(r"^(nvme\d+n\d+|mmcblk\d+|[a-z]+?)", name)
            # partitions: sda1->sda, nvme0n1p2->nvme0n1, mmcblk0p1->mmcblk0
            base = re.sub(r"(p\d+|\d+)$", "", name)
            if not os.path.exists(f"/sys/block/{base}") and m:
                base = m.group(1)
        try:
            with open(f"/sys/block/{base}/queue/rotational",
                      encoding="ascii", errors="replace") as fh:
                return fh.read().strip() == "1"
        except OSError:
            return None
    except Exception as _exc_dbg:
        dbg_log(f"_posix_seek_penalty failed: {type(_exc_dbg).__name__}")
        return None


def seek_penalty(path) -> "bool | None":
    """True = spinning disk, False = SSD/NVMe, None = unknown — cached in
    memory per drive/device so thousands of paths are measured only once."""
    try:
        if os.name == "nt":
            drv = os.path.splitdrive(os.path.abspath(str(path)))[0].rstrip("\\/")
            if len(drv) != 2 or not drv.endswith(":"):
                return None                          # UNC/odd path -> unknown
            key = "w:" + drv.lower()
            if key not in _SEEK_CACHE:
                _SEEK_CACHE[key] = _win_seek_penalty(drv)
        else:
            import sys
            p = str(path)
            while p and not os.path.exists(p):
                nxt = os.path.dirname(p)
                if nxt == p:
                    return None
                p = nxt
            if not p:
                return None
            dev = os.stat(p).st_dev
            if sys.platform == "darwin":
                return None
            key = "p:%d:%d" % (os.major(dev), os.minor(dev))
            if key not in _SEEK_CACHE:
                _SEEK_CACHE[key] = _posix_seek_penalty(p)
        return _SEEK_CACHE.get(key)
    except Exception as _exc_dbg:
        dbg_log(f"seek_penalty failed: {type(_exc_dbg).__name__}")
        return None


def any_rotational(paths) -> bool:
    """True if at least one path sits on a spinning disk; unknowns get no vote."""
    for p in paths:
        if seek_penalty(p) is True:
            return True
    return False


def video_workers(rotational: "bool | None" = None) -> int:
    """Worker count for the video-thumbnail phase.

    Each ffmpeg process with "-threads 1" takes only one core and most of its
    wall time is spent waiting on drive I/O (external drives especially) — so
    on an SSD 2-3x the core count is the sweet spot (cap 28: beyond that USB
    gains nothing and it only eats memory). On a spinning disk,
    dozens of parallel readers thrash the head -> conservative mode (4-8).
    RAM < 6 GB lowers the cap further so a low-RAM system does not choke.
    """
    rotational = _resolve_rotational(rotational)  # user performance mode
    if rotational:
        n = max(4, min(8, cpu_cores()))
        if ram_gb() < 6:
            n = min(n, 6)
    else:
        n = max(12, min(28, cpu_cores() * 3))
        if ram_gb() < 6:
            n = min(n, 16)
    return _env_int("MC_VIDEO_WORKERS") or n


def archive_workers(rotational: "bool | None" = None) -> int:
    """Worker count for the archive phase (7z is process-light, disk-heavy).

    On a spinning disk stay conservative (2-4) — heavy parallel extraction
    on an HDD only swings the head around; on SSD/NVMe keep the previous
    range (6-14)."""
    rotational = _resolve_rotational(rotational)  # user performance mode
    if rotational:
        n = max(2, min(4, cpu_cores() // 2))
        if ram_gb() < 6:
            n = min(n, 3)
    else:
        n = max(6, min(14, cpu_cores() * 2))
        if ram_gb() < 6:
            n = min(n, 8)
    return _env_int("MC_ARCHIVE_WORKERS") or n


def poster_workers(rotational: "bool | None" = None) -> int:
    """Worker count for the folder-poster phase.

    Each folder's poster is built with the same full chain (icon -> image ->
    video -> PDF -> archive): cheap for folders that have an icon, but for
    the rest it runs the same ffmpeg frame extraction — I/O/subprocess-bound
    work, just like the video phase. The phase used to be pinned to a fixed
    3 workers regardless of core count, and profiling showed the poster
    phase ate a large share of the total time. Now:
    SSD/NVMe -> up to 20 workers; spinning disk -> conservative (3-6).
    RAM < 6 GB lowers the cap; MC_POSTER_WORKERS overrides manually."""
    rotational = _resolve_rotational(rotational)  # user performance mode
    if rotational:
        n = max(3, min(6, cpu_cores() // 2 + 1))
        if ram_gb() < 6:
            n = min(n, 4)
    else:
        n = max(10, min(20, cpu_cores() * 2))
        if ram_gb() < 6:
            n = min(n, 12)
    return _env_int("MC_POSTER_WORKERS") or n


def list_workers(rotational: "bool | None" = None) -> int:
    """Worker count for listing folder contents.

    Listing (reading drive metadata) is lighter than rendering but still
    slow on external/network drives; it used to be pinned to 4 workers.
    SSD/NVMe -> up to 12 workers; spinning -> 4 (previous behavior).
    MC_LIST_WORKERS overrides manually."""
    rotational = _resolve_rotational(rotational)  # user performance mode
    if rotational:
        n = 4
    else:
        n = max(6, min(12, cpu_cores()))
        if ram_gb() < 6:
            n = min(n, 8)
    return _env_int("MC_LIST_WORKERS") or n


def hw_summary() -> str:
    """One-line summary for the log/status: "12 cores · 16 GB RAM"."""
    return T("{0} هسته · {1:.0f} گیگ رم").format(cpu_cores(), ram_gb())
