# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Video file durations.

Three-tier strategy (fastest first):
    1) Read a few KB of the file's own header directly (MP4/MKV/WebM/AVI/WMV)
       — pure Python, no external program; ~1 ms per file. For 1,400 videos
       that means seconds, not minutes.
    2) ffprobe (if present on the system) for the odder formats (TS/FLV/MPG…)
    3) ffmpeg (last resort)

- Results are cached in durations.db (plain SQLite), persistent across runs —
  each save writes only fresh keys instead of rewriting one big JSON;
  a legacy durations.json migrates automatically on first run
- All subprocess calls are fully hidden (no console window)
- DurationJob for background computation (QThreadPool)
"""
from __future__ import annotations

from i18n import T, fix_en_singular

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time

from portable import bundled_binary
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from watchtime import dbg_log

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
    _QT = True
except Exception as _exc_dbg:  # pragma: no cover
    dbg_log(f"media_info.<module> failed: {type(_exc_dbg).__name__}")
    _QT = False

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_MAX_SANE = 40 * 86400   # anything over 40 days = parser garbage output


def _d(x) -> str:
    """Digits matching the UI language: Persian gets Persian numerals, English Latin ones."""
    s = str(x)
    from i18n import is_rtl
    return s.translate(FA_DIGITS) if is_rtl() else s


def format_duration(sec) -> str:
    """E.g. "2 hours and 35 minutes" / "45 minutes" / "30 seconds" (English: "1 minute")."""
    if not sec or sec <= 0:
        return ""
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    def n(x): return _d(x)
    if h and m:
        return fix_en_singular(T("{0} ساعت و {1} دقیقه").format(n(h), n(m)))
    if h:
        return fix_en_singular(T("{0} ساعت").format(n(h)))
    if m:
        return fix_en_singular(T("{0} دقیقه").format(n(m)))
    return fix_en_singular(T("{0} ثانیه").format(n(s)))


def format_duration_short(sec) -> str:
    """Compact format for cards: "2:25" or "45 minutes"."""
    if not sec or sec <= 0:
        return ""
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{_d(h)}:{_d(str(m).zfill(2))}"
    if m:
        return fix_en_singular(T("{0} دقیقه").format(_d(m)))
    return fix_en_singular(T("{0} ثانیه").format(_d(s)))


def hidden_kwargs(timeout=30) -> dict:
    """subprocess arguments that open no window at all (Windows)."""
    kw = {"capture_output": True, "timeout": timeout}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


# ============================================================ fast header reading
# ============================================================ MKV / WebM (EBML)

_E_MASTERS = (0x1A45DFA3, 0x18538067, 0x1549A966)   # EBML, Segment, Info
_E_TIMESCALE = 0x2AD7B1
_E_DURATION = 0x4489


def _vint_len(buf: bytes, pos: int) -> int:
    """VINT length from its first byte (shared by IDs and sizes)."""
    if pos >= len(buf):
        return 0
    b0 = buf[pos]
    if b0 == 0:
        return 0
    n, mask = 1, 0x80
    while n <= 8 and not (b0 & mask):
        mask >>= 1
        n += 1
    return n if pos + n <= len(buf) else 0


def _read_id(buf: bytes, pos: int):
    n = _vint_len(buf, pos)
    if not n:
        return None, 0
    return int.from_bytes(buf[pos:pos + n], "big"), n


def _read_size(buf: bytes, pos: int):
    """(value, length); a None value means "unknown size" (all bits set)."""
    n = _vint_len(buf, pos)
    if not n:
        return None, 0
    v = int.from_bytes(buf[pos:pos + n], "big") & ((1 << (7 * n)) - 1)
    if v == (1 << (7 * n)) - 1:
        return None, n      # unknown size
    return v, n


def _mkv_scan(buf: bytes, start: int, end: int, scale, dur, depth: int):
    """Scan EBML elements for TimecodeScale and Duration (inside Info)."""
    pos = start
    while pos + 2 <= end and (scale is None or dur is None):
        eid, il = _read_id(buf, pos)
        if eid is None:
            break
        size, sl = _read_size(buf, pos + il)
        if sl == 0:
            break
        dstart = pos + il + sl
        dend = end if size is None else min(dstart + size, end)
        if dend < dstart:
            break
        if eid == _E_TIMESCALE and 0 < dend - dstart <= 8:
            scale = int.from_bytes(buf[dstart:dend], "big")
        elif eid == _E_DURATION and dend - dstart in (4, 8):
            fmt = ">f" if dend - dstart == 4 else ">d"
            dur = struct.unpack(fmt, buf[dstart:dend])[0]
        elif eid in _E_MASTERS and depth < 3:
            scale, dur = _mkv_scan(buf, dstart, dend, scale, dur, depth + 1)
        pos = dend if dend > pos else dstart + 1
    return scale, dur



def _rb(path):
    r"""Open a file for reading — with Windows long-path support (\\?\ prefix)."""
    p = str(path)
    if sys.platform == "win32" and len(p) > 240 and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return open(p, "rb")


def _mkv_duration(path) -> float | None:
    for chunk in (64 * 1024, 512 * 1024, 4 * 1024 * 1024):
        with _rb(path) as f:
            buf = f.read(chunk)
        if len(buf) < 32:
            return None
        scale, dur = _mkv_scan(buf, 0, len(buf), None, None, 0)
        if dur is not None:
            if chunk > 64 * 1024 and scale is None and dur == 0:
                return None
            scale = scale or 1_000_000          # Matroska default: 1 ms
            sec = dur * scale / 1e9
            if 0 < sec < _MAX_SANE:
                return sec
            return None
        if len(buf) < chunk:                    # file smaller than the buffer — done
            return None
    return None


# ------------------------------------------------------------------- MP4/MOV

def _mvhd_parse(buf: bytes) -> float | None:
    if len(buf) < 20:
        return None
    ver = buf[0]
    if ver == 1:
        if len(buf) < 32:
            return None
        timescale = int.from_bytes(buf[20:24], "big")
        duration = int.from_bytes(buf[24:32], "big")
    else:
        timescale = int.from_bytes(buf[12:16], "big")
        duration = int.from_bytes(buf[16:20], "big")
    if not timescale or duration in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return None
    sec = duration / timescale
    return sec if 0 < sec < _MAX_SANE else None


def _mp4_walk(f, start: int, end: int, depth: int) -> float | None:
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size = int.from_bytes(hdr[:4], "big")
        typ = hdr[4:8]
        hsize = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size, hsize = int.from_bytes(ext, "big"), 16
        elif size == 0:
            size = end - pos
        if size < hsize:
            break
        cstart, cend = pos + hsize, min(pos + size, end)
        if typ == b"mvhd":
            f.seek(cstart)
            d = _mvhd_parse(f.read(min(cend - cstart, 64)))
            if d:
                return d
        elif typ in (b"moov", b"moof") and depth < 2:
            d = _mp4_walk(f, cstart, cend, depth + 1)
            if d:
                return d
        pos += size
    return None


def _mp4_duration(path) -> float | None:
    with _rb(path) as f:
        f.seek(0, os.SEEK_END)
        fsize = f.tell()
        if fsize < 16:
            return None
        return _mp4_walk(f, 0, fsize, 0)


# ------------------------------------------------------------------- AVI

def _avi_duration(path) -> float | None:
    with _rb(path) as f:
        head = f.read(256 * 1024)
    if len(head) < 40 or head[:4] != b"RIFF" or head[8:12] != b"AVI ":
        return None
    idx = head.find(b"avih", 12)
    if idx < 0 or idx + 8 + 20 > len(head):
        return None
    data = idx + 8
    usec = int.from_bytes(head[data:data + 4], "little")
    frames = int.from_bytes(head[data + 16:data + 20], "little")
    if not usec or not frames:
        return None
    sec = usec * frames / 1e6
    return sec if 0 < sec < _MAX_SANE else None


# ------------------------------------------------------------------- ASF/WMV

_ASF_HEADER_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_FILE_PROPS_GUID = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")


def _asf_duration(path) -> float | None:
    with _rb(path) as f:
        head = f.read(128 * 1024)
    if len(head) < 30 or head[:16] != _ASF_HEADER_GUID:
        return None
    hsize = int.from_bytes(head[16:24], "little")
    obj_count = int.from_bytes(head[24:28], "little")
    pos, hend = 30, min(hsize, len(head))
    for _ in range(min(obj_count, 200)):
        if pos + 24 > hend:
            break
        guid = head[pos:pos + 16]
        osize = int.from_bytes(head[pos + 16:pos + 24], "little")
        if osize < 24:
            break
        if guid == _ASF_FILE_PROPS_GUID and osize >= 24 + 64 and pos + osize <= hend:
            d0 = pos + 24
            play = int.from_bytes(head[d0 + 40:d0 + 48], "little")      # unit: 100 ns
            preroll = int.from_bytes(head[d0 + 56:d0 + 64], "little")   # milliseconds
            sec = play / 1e7 - preroll / 1000.0
            return sec if 0 < sec < _MAX_SANE else None
        pos += osize
    return None


# ------------------------------------------------------------------- single entry point

def fast_duration(path) -> float | None:
    """Read the headers of common formats directly — ~1 ms per file, no subprocess."""
    ext = Path(path).suffix.lower()
    try:
        if ext in (".mkv", ".webm"):
            return _mkv_duration(path)
        if ext in (".mp4", ".m4v", ".mov", ".3gp"):
            return _mp4_duration(path)
        if ext == ".avi":
            return _avi_duration(path)
        if ext in (".wmv", ".asf", ".wma"):
            return _asf_duration(path)
    except (OSError, ValueError, struct.error, OverflowError, IndexError):
        return None
    return None


def probe_duration(path) -> float | None:
    """Duration in seconds; None if it could not be determined."""
    d = fast_duration(path)
    if d:
        return d
    # Prefer the binary bundled with the app (bin/ next to the exe — portable
    # runs need no prerequisites)
    ffprobe = bundled_binary("ffprobe.exe") or shutil.which("ffprobe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                **hidden_kwargs(20),
            )
            txt = r.stdout.decode("utf-8", "ignore").strip()
            if txt:
                return float(txt)
        except Exception as _exc_dbg:
            dbg_log(f"probe_duration failed: {type(_exc_dbg).__name__}")
            pass
    ffmpeg = bundled_binary("ffmpeg.exe") or shutil.which("ffmpeg")
    if ffmpeg:
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)], **hidden_kwargs(20),
            )
            m = re.search(r"Duration\s*:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                          r.stderr.decode("utf-8", "ignore"))
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return h * 3600 + mi * 60 + s
        except Exception as _exc_dbg:
            dbg_log(f"probe_duration failed: {type(_exc_dbg).__name__}")
            pass
    return None


class DurationStore:
    """Persistent duration cache (SQLite inside the app's cache folder).

    Interface: get/set/save/clear/close — same as before, but each save
    writes only the dirty keys; no more multi-second stalls with thousands
    of files.
    """

    def __init__(self, cache_dir):
        # Read-only fallback — same as ThumbnailCache/FileThumbs; previously
        # a direct mkdir inside a read-only app folder killed startup.
        # Lazy import: media_info gains no new module-level dependency.
        from imgcache import writable_cache_dir
        d, ro = writable_cache_dir(cache_dir)
        self.dir = d
        self.read_only_fallback = ro
        self.path = self.dir / "durations.json"      # legacy — kept only for migration
        self.db_path = self.dir / "durations.db"
        self._lock = threading.RLock()
        # Same malformed-file story: the duration cache self-heals too — a
        # corrupt file is quarantined and recreated (worst case = durations
        # probed again, not a dead app).
        self._con = self._open_con_safe(self.db_path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS durations (k TEXT PRIMARY KEY, v REAL NOT NULL)")
        self._data: dict[str, float] = {}
        self._dirty: set[str] = set()
        try:
            for k, v in self._con.execute("SELECT k, v FROM durations"):
                self._data[k] = v
        except sqlite3.Error:
            pass
        self._migrate_legacy()

    @staticmethod
    def _open_con_safe(db_path: Path):
        """Safe WAL connection with self-healing — corrupt → quarantine + recreate (no crash)."""
        def _fresh():
            con = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=5000")
            res = con.execute("PRAGMA quick_check").fetchone()
            if not res or str(res[0]).lower() != "ok":
                con.close()
                raise sqlite3.DatabaseError("durations quick_check failed")
            return con
        try:
            return _fresh()
        except sqlite3.DatabaseError:
            import datetime as _dt
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            db_path = Path(db_path)
            for cand in (db_path,) + tuple(
                    Path(str(db_path) + sfx) for sfx in ("-wal", "-shm", "-journal")):
                try:
                    if cand.exists():
                        cand.rename(cand.with_name(cand.name + ".corrupt-" + stamp))
                except OSError:
                    pass
            return _fresh()

    def _migrate_legacy(self):
        """Import a legacy durations.json into SQLite (once), then rename it to .bak."""
        if not self.path.exists():
            return
        if self._data:                       # database already populated — set the JSON aside
            try:
                self.path.rename(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            return
        try:
            legacy = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as _exc_dbg:
            dbg_log(f"_migrate_legacy failed: {type(_exc_dbg).__name__}")
            legacy = {}
        if isinstance(legacy, dict) and legacy:
            self._data.update(legacy)
            try:
                self._con.executemany(
                    "INSERT OR REPLACE INTO durations (k, v) VALUES (?, ?)",
                    [(str(k), float(v)) for k, v in legacy.items()])
                self._con.commit()
            except sqlite3.Error:
                pass
        try:
            self.path.rename(self.path.with_suffix(".json.bak"))
        except OSError:
            pass

    def _key(self, path) -> str:
        try:
            st = Path(path).stat()
            return f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            return str(path)

    def get(self, path):
        """Seconds, or -1 (unknown), or None (not computed yet)."""
        return self._data.get(self._key(path))

    def set(self, path, value):
        """Memory only + dirty queue — disk saving happens via save() (scheduled in probe_many)."""
        k = self._key(path)
        self._data[k] = value
        self._dirty.add(k)

    def save(self):
        """Flush the dirty keys to SQLite — one small transaction, no full-cache rewrite."""
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        rows = [(k, float(self._data[k])) for k in dirty if k in self._data]
        if not rows:
            return
        with self._lock:
            try:
                self._con.executemany(
                    "INSERT OR REPLACE INTO durations (k, v) VALUES (?, ?)", rows)
                self._con.commit()
            except sqlite3.Error:
                self._dirty.update(dirty)

    def clear(self):
        """Wipe the whole duration cache (for "recompute all")."""
        self._data = {}
        self._dirty = set()
        with self._lock:
            try:
                self._con.execute("DELETE FROM durations")
                self._con.commit()
            except sqlite3.Error:
                pass
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def close(self):
        """Final flush + close the connection (on app exit)."""
        try:
            self.save()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._con.close()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass


def probe_many(paths, store: DurationStore, workers: int = 16,
               progress=None, save_interval: float = 2.0) -> int:
    """Probe several files in parallel with a ThreadPool.

    - Header reading (MKV/MP4/AVI/WMV) is instant; subprocess only for other
      formats.
    - Only files missing from the cache get probed.
    - Disk saving is time-scheduled (once every few seconds) rather than per
      handful of files, so save time stays flat even with thousands of files.
    Returns: the number of files probed.
    """
    todo = [p for p in paths if store.get(p) is None]
    total = len(todo)
    if not total:
        return 0
    done = 0
    last_save = time.monotonic()

    def _one(p):
        dur = probe_duration(p)
        return p, (dur if dur else -1)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p, val in ex.map(_one, todo):
            store.set(p, val)
            done += 1
            now = time.monotonic()
            if now - last_save >= save_interval:
                store.save()
                last_save = now
            if progress:
                progress(done, total)
    store.save()
    return done


if _QT:
    class DurationSignals(QObject):
        ready = Signal(str, float)   # file path, duration (0/negative = unknown)

    class DurationJob(QRunnable):
        def __init__(self, store: DurationStore, path: str, signals: DurationSignals):
            super().__init__()
            self.store = store
            self.path = path
            self.signals = signals

        def run(self):
            d = probe_duration(self.path)
            self.store.set(self.path, d if d else -1)
            self.store.save()
            try:
                self.signals.ready.emit(self.path, d if d else -1.0)
            except RuntimeError:
                pass          # app shutting down — signal source already destroyed
else:  # pragma: no cover
    DurationSignals = DurationJob = None
