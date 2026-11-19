# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""File thumbnails — priority chain:

1) Image files → the image itself
2) Videos → Windows Shell thumbnail (the same one Explorer shows)
3) Videos → ffmpeg (single mid-frame shot + at most one black-frame retry)
4) Other files → gradient file-type card (e.g. MKV / PDF / ZIP)

Two usage modes:
- get(path, px): synchronous (for background work)
- peek(path, px) + card(path, px) + ThumbJob: async pattern — the file-type
  card is shown first, and the real thumbnail is built in the background and
  swapped in when ready.

Everything is cached atomically.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from imgcache import (apply_exif_transpose, cache_file, read_cache_pil,
                      replace_with_retry, save_cache_image)
from portable import bundled_binary, resource_path

from scanner import (
    ARCHIVE_EXTS, AUDIO_EXTS, CODE_EXTS, DOC_EXTS, EXE_EXTS,
    IMAGE_EXTS_SET, VIDEO_EXTS, archive_ext_of,
)
from winthumbs import get_shell_thumbnail, shell_thumbnail_stats
from media_info import hidden_kwargs, fast_duration
from watchtime import dbg_log

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
    from PySide6.QtGui import QImage, QPixmap
    _QT = True
except Exception as _exc_dbg:  # pragma: no cover — outside the app environment
    dbg_log(f"filethumbs.<module> failed: {type(_exc_dbg).__name__}")
    _QT = False

GROUP_STYLE = {
    "video":   ("#5b7cf5", "#33247e", "VIDEO"),
    "audio":   ("#2dd4bf", "#0f766e", "AUDIO"),
    "image":   ("#38bdf8", "#1e3a8a", "IMAGE"),
    "archive": ("#fbbf24", "#92400e", "ARCHIVE"),
    "doc":     ("#fb7185", "#9f1239", "DOC"),
    "sub":     ("#e879a9", "#9d2457", "SUB"),          # subtitles get their own style
    "code":    ("#a3e635", "#3f6212", "CODE"),
    "exe":     ("#94a3b8", "#334155", "APP"),
    "other":   ("#8b98ad", "#25334a", "FILE"),
}

# Subtitles have no intrinsic thumbnail; they get a dedicated card
_SUBTITLE_EXTS = frozenset({
    "srt", "ass", "ssa", "vtt", "sub", "idx", "stl", "smi", "sbv", "usf",
})

# Card style upgraded to "real downloaded icons" (open-source Material Icon
# Theme pack — MIT, in assets/icons/) → cache tag "|c3", and "sub2" for
# subtitles, so that previous-generation cards (drawn glyphs) are rebuilt
# only once, cheaply; real cached thumbnails (image/video/pdf/archive) keep
# the same key untouched. The doc group is for non-pdf only (pdf has a real
# thumbnail).
_CARD_STYLE_GROUPS = frozenset({"other", "code", "exe", "audio", "doc"})

# Downloaded icon pack — one that matches the
# app theme — extension/group → name of the downloaded PNG file in
# assets/icons (30 icons from the open-source Material Icon Theme pack,
# 512px raster on a white cloud — the same style the user had picked on
# flaticon/vscode-icons/catppuccin; those sites are captcha/anti-bot walled
# and cannot be scripted, so the icons came from the official GitHub repo =
# works offline).
_EXT_ICON = {
    # code
    ".py": "python", ".pyc": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".html": "html", ".htm": "html", ".css": "css",
    ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".rs": "rust",
    ".cpp": "cpp", ".cxx": "cpp", ".cc": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".go": "go", ".php": "php", ".rb": "ruby", ".java": "java",
    ".log": "log",
    # executables / scripts
    ".exe": "exe", ".msi": "exe", ".lnk": "exe",
    ".bat": "console", ".cmd": "console", ".ps1": "console", ".sh": "console",
    # documents
    ".txt": "document", ".pdf": "pdf",
    ".doc": "word", ".docx": "word",
    ".ppt": "powerpoint", ".pptx": "powerpoint",
    ".xls": "table", ".xlsx": "table", ".csv": "table",
    ".epub": "epub",
}
for _se in _SUBTITLE_EXTS:
    _EXT_ICON["." + _se] = "subtitles"

_GRP_ICON = {
    "video": "video", "audio": "audio", "image": "image",
    "archive": "zip", "sub": "subtitles", "doc": "document",
    "code": "console", "code2": "console",
    "exe": "exe", "other": "document",
}

_ICON_MEM: dict = {}                        # name → Image (RGBA) or None (miss)


def _card_icon_name(suf: str, grp: str) -> str | None:
    """Downloaded icon name for this file — exact extension mapping first,
    then group."""
    suf = suf.lower()
    if not suf.startswith("."):
        suf = "." + suf
    return _EXT_ICON.get(suf) or _GRP_ICON.get(grp)


def _load_card_icon(name: str | None) -> Image.Image | None:
    """Downloaded PNG icon (assets/icons/<name>.png) → PIL RGBA; cached.

    Missing file/error → None so the built-in vector glyph (permanent
    fallback) is drawn instead — the app must also work without the icons
    folder.
    """
    if not name:
        return None
    hit = _ICON_MEM.get(name, False)
    if hit is not False:
        return hit
    try:
        p = resource_path(f"assets/icons/{name}.png")
        with Image.open(p) as src:
            im = src.convert("RGBA")
        _ICON_MEM[name] = im
        return im
    except Exception as _exc_dbg:
        dbg_log(f"_load_card_icon failed: {type(_exc_dbg).__name__}")
        _ICON_MEM[name] = None
        return None

# Cache of drawn cards (key: (group, ext, size)) — thousands of same-extension files drawn once
_TC_CACHE: dict = {}

_FFMPEG_FALLBACK_SEEKS = ("30", "3")   # when duration cannot be read at all — two fixed seek points
_BLACK_MEAN = 16                        # threshold for a near-black frame

# Safety/performance limits for probing inside archives (prevent freezes on
# giant archives and compression bombs)
_ARC_IMG_CAP = 80 * 1024 * 1024       # skip inner images larger than this
# One of three archives with movie parts once got no thumbnail: the
# 2GB cap was not enough for full-length movie parts — a 2.2GB video was
# silently skipped, and since that archive contained nothing but the video,
# no thumbnail was built at all. The cap went up to ~4GB and the extraction
# timeout now scales with member size (see the same function below).
_ARC_VID_CAP = 4_000_000_000          # cap for extracting video from an archive (~4GB; cached)
_ARC_PDF_CAP = 800 * 1024 * 1024      # cap for extracting a PDF from an archive
_ARC_LIST_CAP = 8000                  # max members scanned (raised from 4k for busy archives)
_UNRAR_TOOL: str | None = None        # UnRAR tool lookup result is cached ("" = not found)
_SEVENZ_TOOL: str | None = None       # 7-Zip tool lookup result is cached


def _find_unrar() -> str | None:
    """Locate UnRAR — the app-bundled binary (bin/) first, then PATH and
    common Windows install locations.

    Reading RAR contents is done with rarfile, but extraction needs an
    external tool; a standard WinRAR install always ships UnRAR.exe.
    """
    global _UNRAR_TOOL
    if _UNRAR_TOOL is not None:
        return _UNRAR_TOOL or None
    t = bundled_binary("UnRAR.exe") or bundled_binary("unrar.exe")
    if not t:
        t = shutil.which("unrar") or shutil.which("rar")
    if not t and sys.platform == "win32":
        for cand in (
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
            r"C:\Program Files\WinRAR\RAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
):
            if os.path.isfile(cand):
                t = cand
                break
    _UNRAR_TOOL = t or ""
    return t or None


def _find_7z() -> str | None:
    """Locate an installed 7-Zip — PATH first, then the common Windows
    install location.

    7z.exe reads nearly every archive format (zip/rar/7z/tar/cab/...) and is
    installed on most user systems; it also has no Python package
    dependency.
    """
    global _SEVENZ_TOOL
    if _SEVENZ_TOOL is not None:
        return _SEVENZ_TOOL or None
    # Prefer the app-bundled 7-Zip (bin/7za.exe) — without the user having to
    # install anything, all archive formats (7z/rar/zip/tar/...) get
    # thumbnails.
    t = bundled_binary("7za.exe") or bundled_binary("7z.exe")
    if not t:
        t = (shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
             or shutil.which("7zz"))
    if not t and sys.platform == "win32":
        for cand in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            r"C:\Program Files\7-Zip\7za.exe",
):
            if os.path.isfile(cand):
                t = cand
                break
    _SEVENZ_TOOL = t or ""
    return t or None


def _parse_7z_slt(text: str) -> list:
    """`7z l -slt` output → list of (name, size) members.

    Note: 7-Zip 25.01 no longer prints the `Folder` field for
    regular members, and the old parser only accepted blocks carrying that
    field — so the whole listing came back empty and archive probing via the
    CLI effectively never worked. Sound rule: a block that has a "Size"
    field and is not `Folder = +` (a folder) is a file member. The archive's
    own summary block has only "Physical Size", not "Size", so it is
    rejected.
    """
    members = []
    path = size = folder = None
    has_size = False

    def flush():
        nonlocal path, size, folder, has_size
        if has_size and path and folder != "+" and not path.endswith("/"):
            members.append((path, size or 0))
        path = size = folder = None
        has_size = False

    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            flush()
            continue
        if " = " not in line:
            continue
        key, val = line.split(" = ", 1)
        key = key.strip()
        if key == "Path":
            flush()
            path = val
        elif key == "Size":
            has_size = True
            try:
                size = int(val.strip())
            except ValueError:
                size = 0
        elif key == "Folder":
            folder = val.strip()
    flush()
    return members[:_ARC_LIST_CAP]


def _parse_unrar_lt(text: str) -> list:
    """`UnRAR lt -c-` output → list of (name, size) members.

    Verified against real `unrar lt` output: every field line is
    INDENTED ("        Name: folder/x.jpg"), so the old
    `line.startswith("Name: ")` never matched and the whole fallback returned
    an empty list. Field checks run on the stripped line; directories are
    recognized by "Type: Directory" (in this format they carry no trailing
    slash) and skipped."""
    members = []
    name = size = None
    is_dir = False

    def flush():
        nonlocal name, size, is_dir
        if name and not is_dir and not name.endswith("/"):
            members.append((name, size or 0))
        name = size = None
        is_dir = False

    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("Name: "):
            flush()
            name = s[6:].strip()
        elif s.startswith("Size: "):
            try:
                size = int(s[6:].strip().replace(",", "").replace(" ", ""))
            except ValueError:
                size = 0
        elif s.lower().startswith("type:"):
            is_dir = "directory" in s.lower()
    flush()
    return members[:_ARC_LIST_CAP]


# Hard cap for ANY archive-member extraction: archive headers can lie
# (compression-bomb shapes), so the copy itself is capped, not just the
# member selection. Cap = largest currently allowed member (_ARC_VID_CAP) -
# anything bigger was never permitted anyway.
_ARC_EXTRACT_CAP = _ARC_VID_CAP


def _copy_capped(s, d, cap: int | None = None,
                 chunk: int = 1024 * 256) -> int:
    """Chunked copy with a hard byte cap - raises when the member exceeds it.

    The openers below used to copy with shutil.copyfileobj
    and no limit: a member whose header-declared size was small but whose
    real size was huge could eat the temp disk and stall the worker. The
    selection caps (image/pdf/video) still apply on top by the callers.
    cap=None → the module-level _ARC_EXTRACT_CAP (resolved at call time so
    the cap stays patchable)."""
    cap = _ARC_EXTRACT_CAP if cap is None else cap
    n = 0
    while True:
        b = s.read(chunk)
        if not b:
            return n
        n += len(b)
        if n > cap:
            raise OSError(f"archive member exceeds extraction cap ({cap} bytes)")
        d.write(b)


class _CapWriter:
    """Write-through wrapper that enforces the extraction cap mid-stream.

    Backends that stream a subprocess' stdout (UnRAR "p" — drained from a
    PIPE, since Popen needs a real fd and cannot write through a wrapper)
    have no Python-side copy loop — the cap must ride ON the sink, otherwise
    a lying archive header lets the member eat the disk before anyone looks
    at the result."""

    def __init__(self, sink, cap: int | None = None):
        self._sink = sink
        self._cap = _ARC_EXTRACT_CAP if cap is None else cap
        self._n = 0

    def write(self, b) -> int:
        self._n += len(b)
        if self._n > self._cap:
            raise OSError(f"archive member exceeds extraction cap ({self._cap} bytes)")
        return self._sink.write(b)

    def flush(self):
        try:
            return self._sink.flush()
        except Exception:
            pass

    def close(self):
        try:
            return self._sink.close()
        except Exception:
            pass


def _capped_copy_file(src, dest, cap: int | None = None) -> int:
    """_copy_capped into dest — and on a cap trip the partial dest is removed:
    a half-written bomb must not stay behind for the next reader."""
    try:
        with open(src, "rb") as s, open(dest, "wb") as d:
            return _copy_capped(s, d, cap)
    except OSError:
        try:
            Path(dest).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _quiet_kw(timeout: int) -> dict:
    """Kwargs that hide the subprocess window without capture (stdout is managed by us)."""
    kw = {"timeout": timeout}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


class _CliArc:
    """Archive backend built on system-installed tools — no Python packages.

    kind="7z": `7z l -slt -sccUTF-8` for listing, `7z e -o<tmp>` (flat) for
    extraction.
    kind="unrar": `UnRAR lt -c-` for listing, `UnRAR p -inul` (to stdout) for
    extraction.
    Every job runs in a fresh, windowless process.

    list_members=False → "direct" mode: skip the listing (saves seconds on
    large archives) when extracting a member the pick cache
    (_archive_picks) already selected.
    """

    def __init__(self, tool: str, path, kind: str, list_members: bool = True):
        self.tool = tool
        self.path = Path(path)
        self.kind = kind
        self.members: list = self._list() if list_members else []

    def close(self):
        pass                                         # nothing stays open

    def _list(self) -> list:
        if self.kind == "7z":
            out = subprocess.run(
                [self.tool, "l", "-slt", "-sccUTF-8", str(self.path)],
                **hidden_kwargs(40))
            return _parse_7z_slt((out.stdout or b"").decode("utf-8", errors="replace"))
        out = subprocess.run(
            [self.tool, "lt", "-c-", str(self.path)],
            **hidden_kwargs(40))
        return _parse_unrar_lt((out.stdout or b"").decode("utf-8", errors="replace"))

    def extract(self, name: str, dest, timeout: int = 240):
        dest = Path(dest)
        if self.kind == "7z":
            # Per-call private staging directory — NEVER the shared cache root.
            # 7z e always writes the member under its own basename, so two
            # concurrent extractions of different archives ("cover.jpg" in
            # both) used to overwrite each other right inside the shared
            # thumbnail cache, and the old missing-file fallback then grabbed
            # the first *any* file of that shared folder — another item's
            # cached thumbnail — as the cover and deleted it afterwards.
            dest.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="ax7z-", dir=dest.parent))
            try:
                # "--": an archive member name after this point is never read
                # as a switch — a member whose name starts with "-" can no
                # longer break extraction. "-spd": wildcard matching is
                # disabled so a member name containing * or ? is matched
                # literally, not as a pattern.
                proc = subprocess.run(
                    [self.tool, "e", str(self.path), f"-o{staging}", "-y",
                     "-spd", "--", name],
                    **hidden_kwargs(timeout))
                if proc.returncode not in (0, 1):     # 0 ok, 1 warnings
                    dbg_log(f"_CliArc 7z extract rc={proc.returncode}")
                base = name.replace("\\", "/").rsplit("/", 1)[-1]
                got = staging / base
                if not got.exists():
                    # 7z sometimes renames it — look ONLY inside this call's
                    # private staging folder (a shared-folder glob here was
                    # how unrelated cached thumbnails got kidnapped).
                    cands = sorted(p for p in staging.glob("*") if p.is_file())
                    if cands:
                        got = cands[0]
                if got.exists():
                    # Capped copy, not a move: a header that lied about the
                    # member size cannot fill the disk unchecked
                    # (compression-bomb guard — same hard cap as every
                    # other backend). A cap trip leaves no partial dest.
                    _capped_copy_file(got, dest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            return
        # unrar p — member content on stdout, drained through the cap: the
        # stream used to land on the file directly with no limit at all.
        # "--": like the 7z backend — a member name starting with "-" is
        # never read as an UnRAR switch.
        kw = _quiet_kw(timeout)
        kw.pop("timeout", None)          # Popen takes no timeout=; wait() does
        proc = subprocess.Popen(
            [self.tool, "p", "-inul", str(self.path), "--", name],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)
        try:
            with open(dest, "wb") as d:
                capped = _CapWriter(d)
                while True:
                    b = proc.stdout.read(1024 * 256)
                    if not b:
                        break
                    capped.write(b)
            rc = proc.wait(timeout=timeout)
            if rc not in (0, 1):         # 0 ok, 1 warnings
                dbg_log(f"_CliArc unrar extract rc={rc}")
        except OSError:
            # cap trip (or a broken pipe): no half-written member stays behind
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if proc.poll() is None:      # never leave a tool process hanging
                proc.kill()
                proc.wait()
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()


def _is_tar_bundle(path) -> bool:
    """Detect multi-extension tar archives — Path.suffix returns only the
    last extension and sees .tar.gz as ".gz" (hint). Native tarfile reads
    each of them."""
    try:
        n = Path(path).name.lower()
    except Exception as _exc_dbg:
        dbg_log(f"_is_tar_bundle failed: {type(_exc_dbg).__name__}")
        return False
    return n.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                       ".tar.xz", ".txz", ".tar.zst", ".tzst", ".tar.lz4"))


def _arc_open(path: Path):
    """Open an archive → (handle, members, opener) or None.

    members: [(internal name, uncompressed size)] · opener(name, dest) dumps
    the member to disk. Backend order ("thumbnails for all
    archives"):
    1) ZIP/EPUB/CBZ and TAR via Python native (instant, no process)
    2) The system's own 7z.exe / UnRAR.exe (no package needed — Iranian users
       usually need a VPN for pip; a critical dependency must not rely on
       pip)
    3) The optional rarfile / py7zr packages if installed
    Any error = None (the file-type card stays and the negative result is
    cached).
    """
    suf = path.suffix.lower()
    # 1) native backends
    try:
        if suf in (".zip", ".cbz", ".epub"):
            import zipfile
            zf = zipfile.ZipFile(path)
            try:
                members = [
                    (i.filename, i.file_size) for i in zf.infolist()[:_ARC_LIST_CAP]
                    if not i.is_dir()
                ]
            except Exception:
                zf.close()          # don't leak the handle (Windows would keep it locked)
                raise

            def opener(name, dest, timeout=None):
                with zf.open(name) as s, open(dest, "wb") as d:
                    _copy_capped(s, d)   # hard cap - headers can lie
            return zf, members, opener
        # Multi-extension detection: .tar.gz / .tgz / .tar.bz2 /
        # .tar.xz — previously only the suffix was seen (e.g. ".gz") and the
        # native backend could not open them.
        _tar_like = suf == ".tar" or _is_tar_bundle(path)
        if _tar_like:
            import tarfile
            tf = tarfile.open(path, "r:*")     # r:* auto-detects gz/bz2/xz itself
            try:
                members = [
                    (m.name, m.size) for m in tf.getmembers()[:_ARC_LIST_CAP] if m.isfile()
                ]
            except Exception:
                tf.close()          # bad member/corrupt archive — don't leak the handle
                raise

            def opener(name, dest, timeout=None):
                with tf.extractfile(name) as s, open(dest, "wb") as d:
                    _copy_capped(s, d)   # hard cap - headers can lie
            return tf, members, opener
    except Exception as _exc_dbg:
        dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
        pass                            # corrupt/encrypted → fall through to the next backend

    # 2) the system's own CLI tools (7z handles every format; UnRAR for RAR)
    try:
        tool = _find_7z()
        if tool:
            arc = _CliArc(tool, path, "7z")
            if arc.members:
                return arc, arc.members, arc.extract
        else:
            tool = None
        if suf in (".rar", ".cbr"):
            toolu = _find_unrar()
            if toolu:
                arc = _CliArc(toolu, path, "unrar")
                if arc.members:
                    return arc, arc.members, arc.extract
    except Exception as e:
        # public release: an archive-open failure must not go silent (no path — safe_ref)
        try:
            from watchtime import _dbg, safe_ref
            _dbg(f"archive open (cli) failed: {safe_ref(path)}: {type(e).__name__}")
        except Exception as _exc_dbg:
            dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
            pass

    # 3) optional packages
    try:
        if suf in (".rar", ".cbr"):
            import rarfile
            tool = _find_unrar()
            if tool:
                rarfile.UNRAR_TOOL = tool
            rf = rarfile.RarFile(path)
            try:
                members = [
                    (i.filename, i.file_size) for i in rf.infolist()[:_ARC_LIST_CAP]
                    if not i.isdir()
                ]
            except Exception:
                rf.close()          # don't leak the handle
                raise

            def opener(name, dest, timeout=None):
                with rf.open(name) as s, open(dest, "wb") as d:
                    _copy_capped(s, d)   # hard cap - headers can lie
            return rf, members, opener
        if suf in (".7z", ".cb7"):
            import py7zr
            # max_extract_size (py7zr >= 1.1.3, the floor below): the library
            # refuses to write more than this many bytes TOTAL — the hard
            # cap is now enforced during extraction by the backend itself,
            # not only after the file already lies on disk.
            z = py7zr.SevenZipFile(path, max_extract_size=_ARC_EXTRACT_CAP)
            try:
                names = z.getnames()[:_ARC_LIST_CAP]
                sizes = {}
                for info in z.list():
                    sizes[info.filename] = getattr(info, "uncompressed", 0)
            except Exception:
                z.close()           # don't leak the handle
                raise
            members = [(n, sizes.get(n, 0)) for n in names]
            def opener(name, dest, timeout=None):
                # The header-declared size is checked BEFORE extraction and
                # the REAL bytes during the copy — a bomb is stopped either way.
                if sizes.get(name, 0) > _ARC_EXTRACT_CAP:
                    raise OSError(
                        f"archive member exceeds extraction cap "
                        f"({_ARC_EXTRACT_CAP} bytes)")
                # Private per-call staging folder: a name-derived shared
                # folder ("ax7_<sha1(member)>") collided across archives that
                # both contain "cover.jpg" — with up to 10 concurrent poster
                # workers the extractions raced and the rmtree of one call
                # deleted the other call's output.
                dest.parent.mkdir(parents=True, exist_ok=True)
                sub = Path(tempfile.mkdtemp(prefix="ax7-", dir=dest.parent))
                sub_res = sub.resolve()
                try:
                    z.extract(path=sub, targets=[name])
                    got = sub / name
                    # Containment (defence-in-depth): py7zr 1.1.4
                    # accepts member names starting with "/" or "//" (its own
                    # helper only lstrips them) — `sub / name` then resolves
                    # to ANY absolute path (or a UNC share → an SMB
                    # connection) instead of the staging folder. Anything
                    # outside `sub` is ignored; the rglob fallback below
                    # finds the copy py7zr actually wrote inside `sub`.
                    try:
                        if not got.resolve().is_relative_to(sub_res):
                            got = None
                    except (OSError, ValueError):
                        got = None
                    if got is None or not got.is_file():
                        cand = [p for p in sorted(sub.rglob("*"))
                                if p.is_file()]     # a folder must not reach the copier
                        got = cand[0] if cand else None
                    if got is not None and got.is_file():
                        # Capped copy — the real size check happens WHILE
                        # copying, not after the file already exists; a cap
                        # trip leaves no partial dest behind.
                        _capped_copy_file(got, dest)
                finally:
                    # Cleanup must also run on failure — the
                    # temp subfolder used to survive exceptions.
                    shutil.rmtree(sub, ignore_errors=True)
            return z, members, opener
    except Exception as _exc_dbg:
        dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
        return None
    return None


def _arc_direct(path: Path):
    """Open an archive directly to extract the "cached member" → (handle,
    opener) or None.

    Difference from _arc_open: no member listing is read — on archives with
    thousands of members (archive-only folders get thumbnails
    late") every later visit is a few seconds faster, because we know which
    member was picked last time. The process-free backends (zip/tar) as
    usual; the py7zr fallback is deliberately left out — without
    7z.exe/UnRAR the full _arc_open path does that job itself.
    """
    suf = path.suffix.lower()
    try:
        if suf in (".zip", ".cbz", ".epub"):
            import zipfile
            zf = zipfile.ZipFile(path)
            def opener(name, dest, timeout=None):
                with zf.open(name) as s, open(dest, "wb") as d:
                    _copy_capped(s, d)   # hard cap - headers can lie
            return zf, opener
        if suf == ".tar" or _is_tar_bundle(path):
            import tarfile
            tf = tarfile.open(path, "r:*")
            def opener(name, dest, timeout=None):
                with tf.extractfile(name) as s, open(dest, "wb") as d:
                    _copy_capped(s, d)   # hard cap - headers can lie
            return tf, opener
    except Exception as e:
        # public release: a ZIP/tar open failure must not go silent (no path — safe_ref)
        try:
            from watchtime import _dbg, safe_ref
            _dbg(f"archive open (native) failed: {safe_ref(path)}: {type(e).__name__}")
        except Exception as _exc_dbg:
            dbg_log(f"_arc_direct failed: {type(_exc_dbg).__name__}")
            pass
    try:
        tool = _find_7z()
        if tool:
            arc = _CliArc(tool, path, "7z", list_members=False)
            return arc, arc.extract
        if suf in (".rar", ".cbr"):
            toolu = _find_unrar()
            if toolu:
                arc = _CliArc(toolu, path, "unrar", list_members=False)
                return arc, arc.extract
    except Exception as _exc_dbg:
        dbg_log(f"_arc_direct failed: {type(_exc_dbg).__name__}")
        return None
    return None


_ARC_CAND_LIMIT = 2           # max candidates attempted per archive (lowered from 5 — see below)

# Some archives got no thumbnail in other folders too — the
# library's extension superset is considered when picking the video member
# inside an archive — ffmpeg reads them all (rm/rmvb/vob/3gp/mts/ogv/divx/...).
# Used only for this member pick; the rest of the app (scan/playback) is
# untouched.
_ARC_MEMBER_VIDEO_EXTS = frozenset(
    VIDEO_EXTS | {".rm", ".rmvb", ".vob", ".3gp", ".3g2", ".mts",
                  ".ogv", ".divx", ".f4v", ".mxf", ".asf"})

# Well-known cover names — policy: try the famous
# name first, then the first image): if the archive has cover/folder/poster,
# try that first; experience says that one single extraction is usually
# enough and the remaining candidates are never needed.
_COVERISH = ("cover", "folder", "poster", "thumb", "front", "banner")


def _pick_arc_members(members, limit: int = _ARC_CAND_LIMIT):
    """Thumbnail candidates in preference order: images → videos → PDFs.

    Originally only *one* member of each type was
    picked; if that one was corrupt, encrypted or unextractable, the whole
    archive stayed thumbnail-less — even with ten healthy images after it.
    The consumer now tries candidates one by one until the first success.
    With only the files inside the archive considered, in priority order,
    and the candidate list stopping at the first success, the per-archive
    attempt cap dropped from 5 to 2 (worst
    case per archive went from at most 15 extractions to 6 — and in practice
    usually just 1 extraction), and well-known cover names
    (cover/folder/poster/... come first automatically) so the very first
    attempt succeeds.
    (Junk subfolders like __MACOSX are rejected; extensions are
    case-insensitive; member names may be in any language — only the
    extension matters.)
    """
    imgs, vids, pdfs = [], [], []
    for name, size in members:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if base.startswith(".") or name.startswith("__MACOSX"):
            continue
        suf = Path(base).suffix.lower()
        if suf in IMAGE_EXTS_SET and 256 < size <= _ARC_IMG_CAP:
            imgs.append((name, size, "image"))
        elif suf in _ARC_MEMBER_VIDEO_EXTS and 0 < size <= _ARC_VID_CAP:
            vids.append((name, size, "video"))
        elif suf == ".pdf" and 0 < size <= _ARC_PDF_CAP:
            pdfs.append((name, size, "pdf"))
    imgs.sort(key=lambda it: 0 if it[0].replace("\\", "/").rsplit("/", 1)[-1]
              .lower().startswith(_COVERISH) else 1)
    return imgs[:limit] + vids[:limit] + pdfs[:limit]


def _pick_arc_member(members):
    """First candidate (compat with older callers) — same logic as _pick_arc_members."""
    cands = _pick_arc_members(members, 1)
    return cands[0] if cands else None


# ---------------------------------------------------------------- PDF: real rendering
# The Windows Shell thumbnail only works when a PDF reader with a "thumbnail
# handler" (e.g. Acrobat) is installed; otherwise it always returns None
# Fallback: render the first page for real with pypdfium2
# (light, dependency-free package — run.bat tries to install it), else
# PyMuPDF/fitz if already installed (AGPL-3.0/commercial —
# THIRD_PARTY_NOTICES.md §5). Neither present → None (file-type card).
_PDF_LIB: str | None = None      # "pypdfium2" / "fitz" / "" — one-shot detection cache


def _pdf_lib() -> str | None:
    global _PDF_LIB
    if _PDF_LIB is not None:
        return _PDF_LIB or None
    import importlib.util
    for name in ("pypdfium2", "fitz"):
        try:
            if importlib.util.find_spec(name) is not None:
                _PDF_LIB = name
                return name
        except Exception as _exc_dbg:
            dbg_log(f"_pdf_lib failed: {type(_exc_dbg).__name__}")
            continue
    _PDF_LIB = ""
    return None


# PDFium/fitz are not thread-safe (concurrent rendering from several threads
# crashes) — since thumbnail loaders went parallel, every PDF render is
# serialized behind this shared lock.
_PDF_LOCK = threading.Lock()


def _pdf_render_thumb(path: Path, px: int) -> Image.Image | None:
    """Real render of the PDF's first page — fallback for when the Windows
    Shell gives no thumbnail."""
    with _PDF_LOCK:
        return _pdf_render_thumb_inner(path, px)


def _pdf_render_thumb_inner(path: Path, px: int) -> Image.Image | None:
    lib = _pdf_lib()
    try:
        if lib == "pypdfium2":
            import pypdfium2 as pdfium
            try:
                pdf = pdfium.PdfDocument(str(path))
            except TypeError:                       # very old versions without close
                return None
            try:
                if len(pdf) < 1:
                    return None
                page = pdf[0]
                w, h = page.get_size()
                base = max(w, h) or 600
                im = page.render(scale=max(px, 256) / base).to_pil()
                return _cover_square(im.convert("RGB"), px)
            finally:
                try:
                    pdf.close()
                except Exception as _exc_dbg:
                    dbg_log(f"_pdf_render_thumb_inner failed: {type(_exc_dbg).__name__}")
                    pass
        elif lib == "fitz":
            import fitz
            doc = fitz.open(str(path))
            try:
                if doc.page_count < 1:
                    return None
                page = doc.load_page(0)
                rect = page.rect
                base = max(rect.width, rect.height) or 600
                zoom = max(px, 256) / base
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                return _cover_square(im, px)
            finally:
                doc.close()
    except Exception as _exc_dbg:
        dbg_log(f"_pdf_render_thumb_inner failed: {type(_exc_dbg).__name__}")
        return None
    return None


_MAGIC_OK_HEADS = (b"RIFF", b"GIF8")   # 4-byte heads only — JPEG (3 bytes) and BMP (2 bytes) are prefix-checked below
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _cache_decodable(path) -> bool:
    """Does the cache file actually decode? (An incompatible cache file
    must never stay a placeholder forever.)

    Fast path: only the byte signature is read (12 bytes) — previously
    every cached card did a full decode (0.67–0.9ms × number of cards ≈ half
    a second for 500 cards), and PosterWorker would later decode the same
    file again. Our cache is only WebP/PNG; an unknown/legacy extension goes
    to the full decode only in that rare case (the guarantee is
    preserved).

    Signature lengths are NOT uniform (PNG 8, RIFF/GIF 4, JPEG 3, BMP 2
    bytes): every format is compared against its own real length. The
    former gate tested ALL of them as `head[:4] in _MAGIC_OK_HEADS`, which
    a 3-byte JPEG signature can never match — the EOI tail check was dead
    code and every JPEG/BMP fell through to the lenient full decode, where
    Qt accepts a truncated JPEG (libjpeg pads the missing scan and reports
    success).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            size = f.seek(0, 2)
            f.seek(max(0, size - 16))
            tail = f.read()
    except OSError as _exc_dbg:
        dbg_log(f"_cache_decodable failed: {type(_exc_dbg).__name__}")
        return False
    if head[:8] == _PNG_SIG:
        # A truncated PNG still carries the 8-byte signature: only the fixed
        # IEND chunk (length + name + CRC) at the very end proves the stream
        # is complete — a half-written cache file must be rebuilt, not kept
        # as a "valid header only" forever.
        return tail.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if head[:3] == b"\xff\xd8\xff":
        # JPEG SOI is 3 bytes and can never match the 4-byte gate below —
        # the check must stand on its own. EOI (FFD9) within the last 4
        # bytes (up to 2 trailing pad bytes tolerated) proves the scan is
        # complete; Qt would happily decode a truncated JPEG and keep the
        # half-written cache file forever.
        return b"\xff\xd9" in tail[-4:]
    if head[:2] == b"BM":
        # BMP: same gate problem with its 2-byte signature. No cheap
        # completeness marker exists — header-only fast accept (save_cache_image
        # never writes BMP anyway; this only serves legacy cache files).
        return True
    if head[:4] in _MAGIC_OK_HEADS:
        # RIFF is only valid when it is WebP — a non-WebP RIFF (WAV/AVI) is worthless here
        if head[:4] == b"RIFF":
            if head[8:12] != b"WEBP":
                return False
            # A truncated WebP carries a perfect RIFF/WEBP header; the RIFF
            # chunk size (bytes 4-8, little-endian, excluding the 8-byte
            # RIFF header itself) must fit the real file length.
            return int.from_bytes(head[4:8], "little") + 8 <= size
        if head[:4] == b"GIF8":
            return tail.endswith(b"\x3b")         # GIF: trailer byte at the end
        return True
    # Unknown signature/legacy format — full decode (the heavy path is the exception only)
    try:
        if _QT:
            q = load_cache_qimage(path)
            return q is not None and not q.isNull()
        return read_cache_pil(Path(path)) is not None
    except Exception as _exc_dbg:
        dbg_log(f"_cache_decodable failed: {type(_exc_dbg).__name__}")
        return False


def poster_png_fallback(cache_get, fthumbs, src, vid, pdf, arc, px: int = 256,
                        force: bool = False):
    """Full folder-poster chain, automatically stepping past any failed stage
    in this order: icon/image → real video thumbnail → PDF first page →
    archive contents.

    Important: only a *real thumbnail* is accepted here (not the gradient
    file-type card); if e.g. the video is broken and gives no thumbnail, the
    chain moves on to PDF and then the archive, until "one" image is finally
    built for the folder.
    cache_get: image cache lookup (e.g. ThumbnailCache.get) → Path or None.
    force: for scheduled retries only — the stages' negative caches are
    ignored.
    Returns: the ready cache-file path (WebP/PNG) or None (the lettered
    placeholder stays).
    """
    if src:
        png = cache_get(src)
        if png is not None and not _cache_decodable(png):
            # Stale cache that no longer decodes (truncated file/format with
            # no decoder): delete and rebuild once — otherwise the next retry
            # hits the same broken file and the card stays a placeholder
            # forever.
            try:
                Path(png).unlink(missing_ok=True)
            except OSError:
                pass
            png = cache_get(src)              # now a miss → rebuild from source
            if png is not None and not _cache_decodable(png):
                png = None                    # the rebuild failed too — let the chain continue
        if png:
            return png
    for cand, fn in ((vid, fthumbs.video_thumb_only),
                     (pdf, fthumbs.pdf_thumb_only),
                     (arc, fthumbs.archive_thumb_only)):
        if not cand:
            continue
        try:
            # force is only passed on scheduled retries — the legacy
            # two-argument signature (and the test mocks) stays untouched in
            # the normal case
            png = fn(cand, px, force=True) if force else fn(cand, px)
        except Exception as _exc_dbg:
            dbg_log(f"poster_png_fallback failed: {type(_exc_dbg).__name__}")
            png = None
        if png:
            return png
    return None


def group_of(path) -> str:
    # archive_ext_of (not Path.suffix —): a compound tar bundle
    # ("book.tar.xz") reads as ".xz" through Path.suffix and fell into
    # "other" — no archive thumbnail, wrong group. The scanner's canonical
    # archive-aware extension resolves the full suffix.
    ext = archive_ext_of(path)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS_SET:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in DOC_EXTS:
        return "doc"
    if ext in CODE_EXTS:
        return "code"
    if ext in EXE_EXTS:
        return "exe"
    return "other"


def _cover_square(im: Image.Image, px: int) -> Image.Image:
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    # Very large image (e.g. a multi-thousand-pixel PNG): LANCZOS directly on
    # all the pixels takes seconds. reduce (C box filter, integer factor)
    # first brings the size down to a few times px — visually the same
    # thumbnail, several times faster.
    if side > px * 4:
        try:
            im = im.reduce(max(1, side // (px * 4)))
        except Exception as _exc_dbg:
            dbg_log(f"_cover_square failed: {type(_exc_dbg).__name__}")
            pass
    return im.resize((px, px), Image.Resampling.LANCZOS)


def _load_cover_image(path: Path, px: int) -> Image.Image:
    """Fast open + square crop of an image from disk.

    draft() lets the JPEG decoder decode straight to 1/2, 1/4 or 1/8 size
    (instead of full decode + downscale) — several times faster.
    EXIF rotation is applied here too — a folder poster that was a rotated
    phone photo used to be cached lying on its side.
    """
    with Image.open(path) as src:
        try:
            if (src.format or "") == "JPEG":
                src.draft("RGB", (px * 2, px * 2))
        except Exception as _exc_dbg:
            dbg_log(f"_load_cover_image failed: {type(_exc_dbg).__name__}")
            pass
        im = apply_exif_transpose(src)
        return _cover_square(im.convert("RGB"), px)


def _is_black(im: Image.Image) -> bool:
    try:
        return ImageStat.Stat(im.convert("L").resize((8, 8))).mean[0] < _BLACK_MEAN
    except Exception as _exc_dbg:
        dbg_log(f"_is_black failed: {type(_exc_dbg).__name__}")
        return False


# Gradient background cache for file-type cards — the px×px gradient used to
# be built pixel-by-pixel in pure Python (~25ms per card!); now a 12×12
# gradient is resized (visually identical) and cached for all same-group
# cards → a fraction of a millisecond.
_GRAD_CACHE: dict = {}


def _grad_bg(c1: tuple, c2: tuple, px: int) -> Image.Image:
    key = (c1, c2, px)
    im = _GRAD_CACHE.get(key)
    if im is None:
        small = Image.new("RGB", (12, 12))
        m = small.load()
        for y in range(12):
            for x in range(12):
                t = (x + y) / 22.0
                m[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        im = small.resize((px, px), Image.Resampling.BILINEAR)
        if len(_GRAD_CACHE) >= 64:
            _GRAD_CACHE.clear()
        _GRAD_CACHE[key] = im
    return im.copy()      # draw on a copy — the cached original is never touched


def _draw_card_glyph(d: ImageDraw.ImageDraw, grp: str, cx: int, cy: int,
                     g: int, sw: int, sh: int, x0: int, y0: int,
                     ac: tuple, lw: int, lighten: tuple, px: int) -> None:
    """Built-in vector glyphs per category — permanent fallback when the
    downloaded icon is unavailable (the app must work without the
    assets/icons folder too)."""
    # ---------- per-category glyph (vector shapes — sharp at both 132 and 256) ----------
    if grp == "video":
        t = int(g * 0.27)
        d.polygon([(cx - int(t * 0.55), cy - t), (cx - int(t * 0.55), cy + t),
                   (cx + t, cy)], fill=ac)
    elif grp == "audio":
        r2 = int(g * 0.13) or 2
        d.ellipse((cx - int(g * 0.24), cy + int(g * 0.02),
                   cx - int(g * 0.24) + 2 * r2, cy + int(g * 0.02) + 2 * r2), fill=ac)
        d.rectangle((cx - max(1, lw // 2), cy - int(g * 0.30), cx, cy + int(g * 0.12)),
                    fill=ac)
        d.polygon([(cx, cy - int(g * 0.30)), (cx + int(g * 0.24), cy - int(g * 0.20)),
                   (cx, cy - int(g * 0.10))], fill=ac)
    elif grp == "image":
        sr = int(g * 0.10) or 2
        d.ellipse((cx - int(g * 0.26), cy - int(g * 0.28),
                   cx - int(g * 0.26) + 2 * sr, cy - int(g * 0.28) + 2 * sr),
                  fill=(246, 190, 70))
        d.polygon([(cx - int(g * 0.28), cy + int(g * 0.24)),
                   (cx - int(g * 0.05), cy - int(g * 0.05)),
                   (cx + int(g * 0.09), cy + int(g * 0.24))], fill=ac)
        d.polygon([(cx - int(g * 0.01), cy + int(g * 0.24)),
                   (cx + int(g * 0.16), cy + int(g * 0.01)),
                   (cx + int(g * 0.31), cy + int(g * 0.24))], fill=lighten)
    elif grp == "archive":
        bw, bh = int(g * 0.56), int(g * 0.42)
        bx0, by0 = cx - bw // 2, cy - bh // 2
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(2, px // 56),
                            outline=ac, width=lw)
        d.line((bx0 + lw, cy, bx0 + bw - lw, cy), fill=ac, width=max(1, lw // 2))
        d.rectangle((cx - int(g * 0.09), by0 - max(1, lw // 2),
                     cx + int(g * 0.09), by0 + int(g * 0.12)), fill=ac)
    elif grp == "sub":
        bw, bh = int(g * 0.60), int(g * 0.36)
        bx0, by0 = cx - bw // 2, cy - int(bh * 0.62)
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(3, px // 36),
                            outline=ac, width=lw)
        d.polygon([(cx - int(g * 0.05), by0 + bh - 1), (cx + int(g * 0.11), by0 + bh - 1),
                   (cx - int(g * 0.01), by0 + bh + int(g * 0.17))], fill=ac)
        for i, frac in enumerate((0.34, 0.62)):
            lw2 = int(bw * (0.60 - i * 0.16))
            d.line((bx0 + int(bw * 0.18), by0 + int(bh * frac),
                    bx0 + int(bw * 0.18) + lw2, by0 + int(bh * frac)),
                   fill=ac, width=max(1, lw // 2 + 1))
    elif grp == "doc":
        for i, frac in enumerate((0.30, 0.48, 0.66)):
            lw2 = int(sw * (0.60 - i * 0.11))
            d.line((x0 + int(sw * 0.19), y0 + int(sh * frac),
                    x0 + int(sw * 0.19) + lw2, y0 + int(sh * frac)),
                   fill=ac, width=max(2, lw // 2 + 1))
    elif grp == "code":
        try:
            f_code = ImageFont.load_default(size=max(8, int(g * 0.34)))
        except Exception as _exc_dbg:
            dbg_log(f"_draw_card_glyph failed: {type(_exc_dbg).__name__}")
            f_code = ImageFont.load_default()
        tagc = "</>"
        bb = d.textbbox((0, 0), tagc, font=f_code)
        d.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - bb[1]),
               tagc, fill=ac, font=f_code)
    elif grp == "exe":
        bw, bh = int(g * 0.56), int(g * 0.44)
        bx0, by0 = cx - bw // 2, cy - bh // 2
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(3, px // 44),
                            outline=ac, width=lw)
        d.rectangle((bx0 + lw, by0 + lw, bx0 + bw - lw, by0 + int(bh * 0.30)),
                    fill=lighten)
        d.rectangle((bx0 + int(bw * 0.12), by0 + int(bh * 0.50),
                     bx0 + int(bw * 0.55), by0 + int(bh * 0.50) + max(2, lw // 2)),
                    fill=ac)
        d.rectangle((bx0 + int(bw * 0.12), by0 + int(bh * 0.68),
                     bx0 + int(bw * 0.40), by0 + int(bh * 0.68) + max(2, lw // 2)),
                    fill=lighten)
    else:   # other — the ※ star
        r3 = int(g * 0.22)
        d.line((cx - r3, cy, cx + r3, cy), fill=ac, width=lw)
        d.line((cx - int(r3 * 0.7), cy - int(r3 * 0.7), cx + int(r3 * 0.7),
                cy + int(r3 * 0.7)), fill=ac, width=lw)
        d.line((cx - int(r3 * 0.7), cy + int(r3 * 0.7), cx + int(r3 * 0.7),
                cy - int(r3 * 0.7)), fill=ac, width=lw)



class FileThumbs:
    # Class-level counter for the prewarm summary — counts exactly how many
    # videos fell through to ffmpeg (the slow fallback path).
    ffmpeg_video_uses: int = 0
    # Measured on real hardware: GPU acceleration gained nothing for
    # extracting "one frame" — with a fast seek, CPU decode is a few
    # milliseconds, and the GPU only added CUDA context creation and
    # VRAM→RAM copy overhead; worse, the mode-trying loop re-ran ffmpeg
    # several times for the same file when the output came back empty.
    # The GPU path was removed entirely; the GPU learning counter (the
    # v_gpu file in the cache) and the acceleration-mode probe no longer
    # exist. The hard cap of
    # "at most 2 processes per video" is real again.
    # (in a normal run ffmpeg should run ≈1
    # per video): count probes per file within this run. Any repeated probe
    # of the same file means a successful cache was not saved or some other
    # path is going around the cache — logged to the debug log from the
    # third time on, and the repeat total appears in the prewarm summary to
    # localize the root cause of that astronomical number.
    _probe_counts: dict = {}          # (file path, px) → probe count in this run
    _force_counts: dict = {}          # (file path, px) → forced-retry count for this run
    _REPROBE_LOG_AT = 3               # shout into the debug log from this repeat onward
    # In-flight lock — at most one real probe per key at any moment (fixes
    # simultaneous duplicates from the poster/prewarm pools of the same run)
    _probe_inflight: dict = {}
    _probe_inflight_lock = threading.Lock()
    # Count of archives that had candidates but every candidate failed —
    # these get no negative cache and are reported in the prewarm summary
    _archive_soft_fails: int = 0
    # Exact paths of this run's soft-failed archives — the prewarmer builds
    # its titled "bounded retry" from it (ft.get returns the file-type card,
    # not None, on failure, so the return value alone does not signal
    # failure)
    _archive_soft_files: set = set()
    # Symptom: cache 0, render 0 — the thumbnail handler is missing on
    # their system — if the Windows Shell gear never answers, it should no
    # longer be called blindly for each of thousands of videos — every failed
    # render call costs hundreds of milliseconds, which on 23k files is
    # minutes of pure waste by itself. On systems that do have a handler
    # (Windows-native MP4, Icaros, ...) hits keep the flag alive.
    _shell_video_dead: bool = False
    _SHELL_KILL_AFTER = 8              # miss/error streak, zero hits → off (lowered from 16)
    _SHELL_RATIO_AFTER = 40            # 100←40 — reach ffmpeg sooner without a handler
    _SHELL_RATIO_MIN = 0.02
    # Per-extension learning — the Windows cache works for MP4 but
    # not for MKV; MP4's fate must not be tied to MKV. Keep (miss×hit) per
    # extension separately and retire a useless extension for that extension
    # only.
    _shell_ext_stats: dict = {}        # ext → [misses, hits]
    _shell_ext_dead: set = set()
    _SHELL_EXT_KILL = 6                # this many misses in a row for one ext (no hits) → dead
    _SHELL_EXT_RATIO_AFTER = 30

    @classmethod
    def _shell_for_video_alive(cls) -> bool:
        if cls._shell_video_dead:
            return False
        try:
            s = shell_thumbnail_stats()
        except Exception as _exc_dbg:
            dbg_log(f"_shell_for_video_alive failed: {type(_exc_dbg).__name__}")
            return True                  # stats unavailable → conservatively keep it alive
        hits = s.get("cache_hit", 0) + s.get("render_hit", 0)
        tries = hits + s.get("miss", 0) + s.get("error", 0)
        dead = False
        if s.get("miss", 0) + s.get("error", 0) >= cls._SHELL_KILL_AFTER and hits == 0:
            dead = True
        elif tries >= cls._SHELL_RATIO_AFTER and hits / max(tries, 1) < cls._SHELL_RATIO_MIN:
            dead = True                  # 5 hits out of ~6000 tries — effectively dead
        if dead:
            cls._shell_video_dead = True
            try:
                from watchtime import _dbg
                _dbg(f"video thumbs: دنده‌ی Shell ویندوز روی این سیستم بی‌فایده است "
                     f"({s.get('miss', 0)} miss/{s.get('error', 0)} err، "
                     f"{hits} hit از {tries}) "
                     f"— ادامه‌ی ویدیوها مستقیم به ffmpeg می‌روند")
            except Exception as _exc_dbg:
                dbg_log(f"_shell_for_video_alive failed: {type(_exc_dbg).__name__}")
                pass
        return not cls._shell_video_dead

    @classmethod
    def _shell_for_ext_alive(cls, ext: str) -> bool:
        """Is Shell still worth trying for this specific extension?"""
        return ext not in cls._shell_ext_dead

    @classmethod
    def _shell_ext_note(cls, ext: str, ok: bool):
        """Learn the outcome of one Shell attempt for an extension."""
        st = cls._shell_ext_stats.get(ext)
        if st is None:
            st = cls._shell_ext_stats[ext] = [0, 0]
        st[0 if not ok else 1] += 1
        misses, hits = st
        exts_tried = misses + hits
        if ext in cls._shell_ext_dead:
            return
        if misses >= cls._SHELL_EXT_KILL and hits == 0:
            cls._shell_ext_dead.add(ext)
        elif exts_tried >= cls._SHELL_EXT_RATIO_AFTER and \
                hits / max(exts_tried, 1) < cls._SHELL_RATIO_MIN:
            cls._shell_ext_dead.add(ext)
        else:
            return
        try:
            from watchtime import _dbg
            _dbg(f"video thumbs: Shell برای «{ext}» مرده اعلام شد "
                 f"({misses} miss/{hits} hit) — این پسوند دیگر مستقیم به ffmpeg می‌رود")
        except Exception as _exc_dbg:
            dbg_log(f"_shell_ext_note failed: {type(_exc_dbg).__name__}")
            pass

    def __init__(self, cache_dir):
        # Same read-only fallback as ThumbnailCache — "files/" is kept
        # under the fallback folder too, so it does not mix with the poster
        # cache
        from imgcache import writable_cache_dir
        d, ro = writable_cache_dir(Path(cache_dir) / "files",
                                   fallback_name="FolderDex-cache/files")
        self.dir = d
        self.read_only_fallback = ro
        # Prefer the app-bundled ffmpeg (bin/ next to the exe — portable run with no prerequisites)
        self.ffmpeg = bundled_binary("ffmpeg.exe") or shutil.which("ffmpeg")
        # Thumbnail from inside archives — fed from app settings (pages set the value)
        self.archive_enabled = True
        # "Archive member picked" cache — later visits go straight in without listing members
        self._arc_picks: dict | None = None
        self._arc_picks_lock = threading.Lock()
        # Thread-local state for the "had candidates but all failed" flag —
        # with it set, _probe_cached skips writing the sticky negative marker
        # (retry on the next app run — one of three archives
        # got no thumbnail")
        self._tls = threading.local()

    # ------------------------------------------------- archive picked-member cache
    @staticmethod
    def _arc_key(path: Path) -> str | None:
        try:
            st = path.stat()
        except OSError:
            return None
        p = os.path.normcase(os.path.abspath(str(path)))
        return hashlib.sha1(
            f"{p}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()

    def _arc_picks_load(self) -> dict:
        if self._arc_picks is not None:
            return self._arc_picks              # fast path, no lock (dict swapped, not mutated)
        with self._arc_picks_lock:
            if self._arc_picks is None:
                try:
                    fp = self.dir / "archive_picks.json"
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    self._arc_picks = data if isinstance(data, dict) else {}
                except Exception as _exc_dbg:
                    dbg_log(f"_arc_picks_load failed: {type(_exc_dbg).__name__}")
                    self._arc_picks = {}
            return self._arc_picks

    def _arc_picks_save(self):
        """Atomically write the cache to disk — assumes it is called under the lock."""
        try:
            tmp = self.dir / (f"ap_{os.getpid()}_{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self._arc_picks or {}, ensure_ascii=False),
                           encoding="utf-8")
            # Ride out transient Windows locks for the picks cache too
            replace_with_retry(tmp, self.dir / "archive_picks.json")
        except Exception as e:
            # public release: a cache-write failure must not go silent
            try:
                from watchtime import _dbg
                _dbg(f"archive_picks save failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_arc_picks_save failed: {type(_exc_dbg).__name__}")
                pass

    def _arc_pick_get(self, path: Path) -> dict | None:
        key = self._arc_key(path)
        if not key:
            return None
        rec = self._arc_picks_load().get(key)
        if not rec or not rec.get("n") or rec.get("k") not in ("image", "video", "pdf"):
            return None
        return rec

    def _arc_pick_put(self, path: Path, name: str, kind: str, size: int = 0):
        key = self._arc_key(path)
        if not key:
            return
        picks = self._arc_picks_load()          # load *outside* the lock (avoids deadlock)
        with self._arc_picks_lock:
            # The member size is stored too, so later extraction timeouts
            # (without listing) can still scale with member size
            picks[key] = {"n": name, "k": kind, "s": int(size or 0)}
            while len(picks) > 4000:            # cache cap — simple LRU (insertion order)
                try:
                    picks.pop(next(iter(picks)))
                except StopIteration:
                    break
            self._arc_picks_save()

    def _arc_pick_drop(self, path: Path):
        key = self._arc_key(path)
        if not key:
            return
        picks = self._arc_picks_load()          # load *outside* the lock (avoids deadlock)
        with self._arc_picks_lock:
            if picks.pop(key, None) is not None:
                self._arc_picks_save()

    # ---------------------------------------------------------------- cache
    def _cache_path(self, src: Path, tag: str, px: int) -> Path:
        try:
            st = src.stat()
            # normcase+abspath instead of resolve: on Windows resolve() hits
            # the disk to resolve links and was slow for every file's cache
            # entry (network drives/HDDs are slow). Generation tag |v5:
            # EXIF rotation at render time — sideways thumbnails of the
            # previous generation are rebuilt automatically (once).
            p = os.path.normcase(os.path.abspath(str(src)))
            raw = f"{p}|{st.st_mtime_ns}|{st.st_size}|{tag}|{px}|v5"
        except OSError:
            raw = f"{src}|{tag}|{px}|v5"
        # An old .png cache file also counts as a hit so pre-built thumbnails
        # are not reprocessed (cache_file).
        stem = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return cache_file(self.dir, stem)

    @staticmethod
    def _save(im: Image.Image, out: Path) -> Path:
        """Atomic save (WebP/PNG fallback) — no color-profile leak;
        returns the final path."""
        return save_cache_image(im, out)

    # ---------------------------------------------------------------- API
    @staticmethod
    def _card_tag(path: Path, grp: str) -> str:
        """Result cache tag for a file — the rule shared by peek/_get_inner:
        subtitles get "sub3", card-style groups get "|c4"; everything else keeps its group."""
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            return "sub3"
        if grp in _CARD_STYLE_GROUPS and suf != ".pdf":
            return grp + "|c4"
        return grp

    def peek(self, path, px: int) -> Path | None:
        """Return only if already present in the cache (no generation)."""
        path = Path(path)
        out = self._cache_path(path, self._card_tag(path, group_of(path)), px)
        return out if out.exists() else None

    def card(self, path, px: int) -> Path | None:
        """File-type card (delayed) — for temporary display until the real thumbnail is ready."""
        path = Path(path)
        # "|c4" = the "icon-only" generation (placeholders of the previous
        # generation are rebuilt once, cheaply; this tag's content is always
        # a drawn card)
        out = self._cache_path(path, "card" + group_of(path) + "|c4", px)
        if not out.exists():
            try:
                out = self._save(self._type_card(path, group_of(path), px), out)
            except Exception as _exc_dbg:
                dbg_log(f"card failed: {type(_exc_dbg).__name__}")
                return None
        return out

    def get(self, path, px: int = 160) -> Path | None:
        """Real thumbnail (synchronous, for use on a background thread).

        Very slow thumbnails (>0.8s) are logged to the debug log so the
        problematic file (broken PDF, heavy video, archive on a slow drive)
        can be traced.
        """
        import time as _time
        _t0 = _time.monotonic()
        try:
            return self._get_inner(path, px)
        finally:
            _dt = _time.monotonic() - _t0
            if _dt > 0.8:
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"slow thumb {_dt:.2f}s: {safe_ref(path)}")
                except Exception as _exc_dbg:
                    dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                    pass

    def _get_inner(self, path, px: int = 160) -> Path | None:
        path = Path(path)
        grp = group_of(path)
        # Cache tag for card-style groups + subtitles (the "icon-only"
        # generation) — the new key means the previous-generation framed cards
        # are rebuilt once, cheaply; real thumbnails (image/video/pdf/archive)
        # keep the same previous key.
        card_tag = self._card_tag(path, grp)
        if grp == "archive" and self.archive_enabled:
            hit = self.archive_thumb_only(path, px)
            if hit:
                return hit                      # otherwise: file-type card (rest of the function)
        out = self._cache_path(path, card_tag, px)
        if out.exists():
            return out
        # Real thumbnail for PDFs too (not only in title posters — the files'
        # own cards should show the first page as well).
        # Failure → file-type card as before.
        if grp == "doc" and path.suffix.lower() == ".pdf":
            hit = self.pdf_thumb_only(path, px)
            if hit:
                return hit
        # The video path now goes through the same shared probe wrapper
        # (negative cache + size adoption + reprobe counting) — previously
        # _video_thumb was called directly here and the 256 poster cache was
        # left unused for the 132 grid; i.e. every video ran ffmpeg twice.
        # Failure → file-type card as before.
        if grp == "video":
            hit = self.video_thumb_only(path, px)
            if hit:
                return hit
        im = None
        try:
            if grp == "image":
                im = _load_cover_image(path, px)   # draft for JPEG → several times faster
        except Exception as _exc_dbg:
            dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
            im = None
        if im is None:
            try:
                im = self._type_card(path, grp, px)
            except Exception as _exc_dbg:
                dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
                return None
        try:
            return self._save(im, out)
        except Exception as _exc_dbg:
            dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
            return None

    # ------------------------------------------------- capability signature for the negative cache
    def _capability_key(self) -> str:
        """Fingerprint of the available tools — for validating the negative marker (.bad).

        If a file's probe failed while 7za/ffmpeg/pypdfium2 were
        unavailable, the .bad marker used to stay forever and even after the
        tools shipped with the app (a newer version) that file's thumbnail
        was never retried — e.g. a folder with "a broken video + a healthy
        archive" never got to the archive because the archive was already
        .bad. The marker is now valid only when the capability signature
        matches.
        """
        try:
            bits = [
                "7" if _find_7z() else "-",
                "u" if _find_unrar() else "-",
                "f" if self.ffmpeg else "-",
                "p" if _pdf_lib() else "-",
                "a" if self.archive_enabled else "-",
            ]
        except Exception as _exc_dbg:
            dbg_log(f"_capability_key failed: {type(_exc_dbg).__name__}")
            bits = ["?"]
        # The archive logic now tries several candidates instead of "one" —
        # failure markers of previous versions (|v2) are no longer valid and
        # those thumbnail-less 7z files are probed again.
        # (|v4): the video chain changed — Explorer cache ← Shell render of
        # THUMBNAILONLY ← one-shot keyframe ffmpeg; files that had previously
        # failed (e.g. with old ffmpeg and fixed seeks) are probed again via
        # the new path, and files that had been wrongly cached as "the
        # Windows icon" also benefit from the real thumbnail.
        # (|v5): the ffmpeg logic changed to a "single mid-video shot" and
        # archive candidates were reduced — previously failed files (.bad
        # markers) are re-probed exactly once via the new path; but existing
        # successful thumbnails stay untouched and valid (the cache path
        # version |v3 is deliberately constant — rebuilding all eighteen
        # thousand of the user's cache files is against their wishes).
        # (|v6): the real root cause of the "547MB archive still has no
        # thumbnail" report — now that the cache is *stable* across versions
        # and inherited, negative markers written with the old logic
        # (2GB cap, fixed timeout, fewer extensions) come along with the
        # cache and blocked fresh probes forever. With every logic change the
        # era must change, so old failures are probed exactly once with the
        # new logic; cached successes (real thumbnails) remain untouched.
        return "+".join(bits) + "|v6"

    def _probe_cached(self, path, px: int, grp_req: str, probe,
                      force: bool = False) -> Path | None:
        """The shared "probe + disk cache + negative cache" pattern for real thumbnails.

        A negative result is recorded next to the cache entry as a .bad marker
        (the cache key includes mtime/size, so a changed file = a fresh probe)
        so a broken/unknown file is not re-probed on every rebuild. The
        marker's content = the capability signature: if a new tool ships with
        the app, the stale marker is ignored and a fresh probe runs. Success
        clears the marker. force=True is only for the poster chain's
        "scheduled retry": the negative marker (which may have resulted from a
        transient failure) is ignored and the probe definitely runs again —
        success clears the stale sticky marker for good ("the
        archive-only folder never gets a poster")."""
        path = Path(path)
        grp = group_of(path)
        if grp != grp_req:
            return None
        out = self._cache_path(path, grp, px)
        if out.exists():
            return out
        # Cross-size cache adoption — if a thumbnail of this very file is
        # cached at a larger size (e.g. the 256 poster), the requested size
        # (e.g. the 132 grid) is derived from it. Each file is probed *once*
        # in its lifetime; until today every video ran ffmpeg twice: once for
        # the poster and once for the grid.
        donor = self._adopt_from_other_px(path, grp, px)
        if donor is not None:
            out.with_suffix(".bad").unlink(missing_ok=True)
            return donor
        bad = out.with_suffix(".bad")
        if bad.exists():
            try:
                same_cap = bad.read_text(encoding="ascii", errors="replace") == \
                    self._capability_key()
            except OSError:
                same_cap = False              # unreadable → conservatively probe again
            if same_cap:
                if not force:
                    return None               # impossible with these tools — the result is valid
                # "Reprobe 128" in the lazy run — the poster
                # chain's forced retry must also run *once* per run — the
                # poster retry timer kept re-probing hundreds of this run's
                # failed files (.bad markers with the same capabilities);
                # each time up to 2 ffmpeg processes with an 8s timeout →
                # the bulk of the heavy run's slowness.
                # The forced-retry counter is separate from the regular probe
                # counter: a regular attempt in this run (the prewarm poster
                # phase without force) bumped the regular counter to 1 and
                # the >= 1 check allowed no real forced retry; now each path
                # gets one regular attempt + one real forced attempt per run.
                if FileThumbs._force_counts.get((str(path), px), 0) >= 1:
                    return None               # a forced attempt already ran (and failed) — enough
        # Probe counting to hunt down duplicated work (normal behavior = each
        # file once; a repeat means broken cache saving or several parallel
        # paths circling the same key).
        rkey = (str(path), px)
        # Reprobe 128 in the lazy run — even after suppressing
        # force — two independent pools (the prewarm poster phase with
        # POSTER_WORKERS threads + the library page's poster worker) hit the
        # same file at the same time; since there is neither a cache entry
        # nor a .bad marker yet, both fired real probes. Now the second probe
        # waits for the first and picks the result up from the cache — at
        # most one real probe per key at any moment.
        with FileThumbs._probe_inflight_lock:
            ev = FileThumbs._probe_inflight.get(rkey)
            if ev is None:
                FileThumbs._probe_inflight[rkey] = ev = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(timeout=180)         # large archives on slow drives are covered too
            if out.exists():
                return out
            try:
                if bad.exists() and bad.read_text(
                        encoding="ascii", errors="replace") == self._capability_key():
                    return None
            except OSError:
                pass
            return None                  # the probe owner failed — do not repeat
        try:
            rn = FileThumbs._probe_counts.get(rkey, 0) + 1
            FileThumbs._probe_counts[rkey] = rn
            if force:
                fkey = (str(path), px)
                FileThumbs._force_counts[fkey] = FileThumbs._force_counts.get(fkey, 0) + 1
            if rn == FileThumbs._REPROBE_LOG_AT:
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"reprobe x{rn}: {safe_ref(path)} (px={px}) — علی‌رغم قفل در-پرواز!")
                except Exception as _exc_dbg:
                    dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                    pass
            try:
                im = probe(path, px)
            except Exception as e:
                # Failures must be explainable —
                # the probe error is logged — no path/name, just safe_ref.
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"خطای بازتولید: {safe_ref(path)} (px={px}): {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                    pass
                im = None
            if im is None:
                # A "soft" archive failure (had candidates but failed
                # transiently) gets no negative marker — the next run tries
                # again (one of three archives got no
                # thumbnail")
                _soft = (grp_req == "archive"
                         and getattr(self._tls, "arc_soft", False))
                if not _soft:
                    try:
                        bad.write_text(self._capability_key(), encoding="ascii")
                    except Exception as _exc_dbg:
                        dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                        pass
                return None
            try:
                out = self._save(im, out)
                bad.unlink(missing_ok=True)
                return out
            except Exception as _exc_dbg:
                dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                return None
        finally:
            with FileThumbs._probe_inflight_lock:
                FileThumbs._probe_inflight.pop(rkey, None)
            ev.set()

    @classmethod
    def reprobe_total(cls) -> int:
        """Total repeated probes of this run (normal behavior = zero)."""
        return sum(n - 1 for n in cls._probe_counts.values() if n > 1)

    # "Priority goes to what the user is looking at" —
    # the timestamp of the last thumbnail request from the UI (details-grid
    # viewport / library cards / suggestion dialog). Prewarm phases pause on
    # fresh UI activity so the thumbnails of the very page the user is
    # looking at are built immediately.
    _last_ui_request: float = 0.0

    @classmethod
    def ui_request_tick(cls):
        """Record one UI request (cheap — just a timestamp)."""
        cls._last_ui_request = time.monotonic()

    @classmethod
    def ui_active(cls, window: float = 2.5) -> bool:
        """Is the user browsing right now (a UI request within the last few seconds)?"""
        return (time.monotonic() - float(cls._last_ui_request or 0.0)) < window

    # the real sizes used by the app (library poster 256, details grid 132)
    _ADOPT_PX = (256, 160, 132)

    def _adopt_from_other_px(self, path, grp: str, px: int) -> Path | None:
        """Derive the requested size from a *larger* cached size of the *same file*.
        Downscaling 256→132 is clean; upscaling (derived from a smaller size) goes
        blurry — so only a larger donor is accepted, and the smallest sufficient one."""
        for dpx in sorted(p for p in self._ADOPT_PX if p > px):
            try:
                d = self._cache_path(path, grp, dpx)
            except Exception as _exc_dbg:
                dbg_log(f"_adopt_from_other_px failed: {type(_exc_dbg).__name__}")
                continue
            if not d.exists():
                continue
            try:
                im = read_cache_pil(d)
                if im is None:
                    continue
                small = _cover_square(im.convert("RGB"), px)
                return self._save(small, self._cache_path(path, grp, px))
            except Exception as _exc_dbg:
                dbg_log(f"_adopt_from_other_px failed: {type(_exc_dbg).__name__}")
                continue
        return None

    # ------------------------------------------------ another means first — video as last resort
    def _sidecar_thumb(self, path: Path, px: int) -> Image.Image | None:
        """"If another means exists, prefer it over the video itself" (user
        request): for a video that gives no healthy frame, first try
        the images beside the file: the same-named image (movie.jpg beside
        movie.mkv), then poster names (cover/poster/folder/...) in the same
        folder. Only if none of them exists is the video itself the last
        resort."""
        try:
            d = path.parent
            names = [path.stem] + [n for n in _COVERISH if n != path.stem.lower()]
            for n in names:
                for e in sorted(IMAGE_EXTS_SET):
                    try:
                        cand = d / (n + e)
                        if cand.exists():
                            return _load_cover_image(cand, px)
                    except Exception as _exc_dbg:
                        dbg_log(f"_sidecar_thumb failed: {type(_exc_dbg).__name__}")
                        continue
        except Exception as _exc_dbg:
            dbg_log(f"_sidecar_thumb failed: {type(_exc_dbg).__name__}")
            pass
        return None

    def video_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """Real video thumbnail — *without* falling back to the file-type
        card (for title posters)."""
        return self._probe_cached(path, px, "video", self._video_thumb, force=force)

    def pdf_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """PDF first page as the thumbnail (Windows Shell thumbnail) — cache + negative cache.

        A bridge in the title-poster chain: "a PDF inside the
        folder ← before the archive". If the system has no PDF handler it
        returns None so the next bridge (archive) is tried."""
        if Path(str(path)).suffix.lower() != ".pdf":
            return None
        return self._probe_cached(path, px, "doc", self._pdf_thumb, force=force)

    def _pdf_thumb(self, path: Path, px: int) -> Image.Image | None:
        # 1) Windows Shell thumbnail (fast — when a PDF reader has installed a handler)
        png = get_shell_thumbnail(path, max(px, 256))
        if png:
            try:
                with Image.open(io.BytesIO(png)) as src:
                    return _cover_square(src.convert("RGB"), px)
            except Exception as _exc_dbg:
                dbg_log(f"_pdf_thumb failed: {type(_exc_dbg).__name__}")
                pass
        # 2) Real render of the first page with pypdfium2 / PyMuPDF (fallback —
        # proper thumbnails for PDF files too — even
        # without a Windows Shell handler)
        return _pdf_render_thumb(path, px)

    def archive_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """Real thumbnail of an archive's contents — *without* falling back to
        the file-type card.

        First suitable member inside the archive: image → video → PDF. On
        absence/error returns None so the placeholder stays. Disabled setting
        → None (without writing a negative marker)."""
        if not self.archive_enabled:
            return None
        return self._probe_cached(path, px, "archive", self._archive_thumb, force=force)

    def _archive_thumb(self, path: Path, px: int) -> Image.Image | None:
        """Extract the first image/video/PDF inside an archive and build its
        thumbnail.

        Two paths: if the "picked member" is already cached (later visits),
        that member is extracted directly without listing members — on
        archives with thousands of members that listing used to take seconds
        (archive-only folders are slow). Direct-path failure → wipe
        the pick cache and take the full path.
        """
        path = Path(path)
        self._tls.arc_soft = False            # default; set below when needed
        pick = self._arc_pick_get(path)
        if pick:
            im = self._archive_member_thumb(path, pick["n"], pick["k"], px,
                                            size=pick.get("s", 0))
            if im is not None:
                return im
            self._arc_pick_drop(path)         # stale/broken cache — full path from scratch
        opened = _arc_open(path)
        if not opened:
            return None
        handle, members, _op = opened
        try:
            # Several candidates instead of "one candidate" — if the first is
            # corrupt/encrypted we move on to the next (image-packed
            # 7z files with no thumbnail).
            cands = _pick_arc_members(members)
            for name, size, kind in cands:
                im = self._archive_member_thumb(path, name, kind, px,
                                                opened=opened, size=size)
                if im is not None:
                    self._arc_pick_put(path, name, kind, size)   # next time without listing members
                    return im
            # One of three archives once got no thumbnail — failure
            # after a candidate was found is almost always transient
            # (extraction timeout on a slow drive, I/O contention of the
            # parallel phase, ffmpeg fainting under pressure) — do not write
            # a sticky negative marker so the next app run retries. Within
            # this run, the in-flight lock collapses simultaneous repeats.
            if cands:
                self._tls.arc_soft = True
                FileThumbs._archive_soft_fails += 1
                try:
                    FileThumbs._archive_soft_files.add(str(path))
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_thumb failed: {type(_exc_dbg).__name__}")
                    pass
            return None
        finally:
            try:
                handle.close()
            except Exception as _exc_dbg:
                dbg_log(f"_archive_thumb failed: {type(_exc_dbg).__name__}")
                pass

    def _archive_member_thumb(self, path: Path, name: str, kind: str, px: int,
                              opened=None, size: int = 0) -> Image.Image | None:
        """Extract a specific member from an archive and build its thumbnail
        (image/video/PDF).

        If opened=(handle,members,opener) came from _arc_open, closing the
        handle is the caller's job; otherwise it opens with _arc_direct
        (without listing members) and closes it itself.

        The extraction timeout for video now scales with member size —
        a multi-GB movie part inside a zip on an external drive needed more
        than 240s to extract and failed silently (that "one of three archives"
        case). Estimate: at least 300s, then a conservative
        ~8MB/s up to a 20-minute cap.
        """
        own = None
        if opened is not None:
            opener = opened[2]
        else:
            d = _arc_direct(path)
            if not d:
                return None
            own, opener = d
        if kind == "video":
            tout = 300 if size <= 0 else max(300, min(1200, size // (8 * 1024 * 1024)))
        else:
            tout = 240
        tmp = self.dir / (f"ax_{os.getpid()}_{threading.get_ident()}_"
                          f"{time.monotonic_ns():x}"
                          + Path(name.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower())
        try:
            try:
                opener(name, tmp, timeout=tout)
            except Exception as _exc_dbg:
                dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                return None
            if not (tmp.exists() and tmp.stat().st_size > 0):
                return None
            if kind == "image":
                try:
                    return _load_cover_image(tmp, px)   # with draft for JPEG
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                    return None
            if kind == "video":
                return self._video_thumb(tmp, px)
            # pdf → the same shared first-page path (Windows Shell thumbnail + pypdfium2)
            return self._pdf_thumb(tmp, px)
        finally:
            tmp.unlink(missing_ok=True)
            if own is not None:
                try:
                    own.close()
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                    pass

    # ---------------------------------------------------------------- video
    def _video_thumb(self, path: Path, px: int) -> Image.Image | None:
        # Target feel: "immediately, like Explorer itself" — three stages in
        # order of speed — 1) the ready-made Windows cache (a few ms — what
        # Explorer reads when it feels "instant"); 2) the Windows Shell
        # render (hundreds of ms, and it also fills the system cache for
        # next time); 3) optimized ffmpeg (keyframe only, no header probe
        # when the duration is not needed) as the final fallback.
        # The Shell gears are tried only while they have answered at least
        # once in this run — with no handler installed for an extension, every
        # render call is hundreds of ms of pure waste.
        # Per-extension learning — when there is no handler for
        # MKV, MP4 must not be sacrificed with it (the Windows cache is
        # native for MP4). Each extension learns separately; a useless
        # extension is retired after several consecutive misses.
        ext = path.suffix.lower()
        if self._shell_for_video_alive() and self._shell_for_ext_alive(ext):
            for gear in (True, False):        # True = ready cache only, False = real render
                png = get_shell_thumbnail(path, max(px, 256), cached_only=gear)
                if png:
                    try:
                        with Image.open(io.BytesIO(png)) as src:
                            im = _cover_square(src.convert("RGB"), px)
                        self._shell_ext_note(ext, True)
                        return im
                    except Exception as _exc_dbg:
                        dbg_log(f"_video_thumb failed: {type(_exc_dbg).__name__}")
                        pass
            self._shell_ext_note(ext, False)  # each file casts one miss vote (not one per gear)
        # "If another means exists, use it — only when forced,
        # the video itself" — a sidecar/same-named image is preferred over
        # the emergency ffmpeg shot.
        if self.ffmpeg:
            return self._ffmpeg_thumb(path, px, last_resort=self._sidecar_thumb)
        return self._sidecar_thumb(path, px)   # without ffmpeg: the other means is the only path

    def _ffmpeg_thumb(self, path: Path, px: int,
                      last_resort=None) -> Image.Image | None:
        # Goal: video thumbnails as fast as image thumbnails — three
        # real bottlenecks of the previous version were removed:
        #  1) ffmpeg without -threads went multi-threaded per process; with
        #     16 parallel workers the CPU collapsed several times over → now
        #     "-threads 1": each process is exactly one core; 16 processes =
        #     16 threads — no more internal self-bottlenecking.
        #  2) Every shot wrote a JPG to disk and read it back → now the
        #     output is streamed straight to stdout via image2pipe and
        #     decoded in memory; no temp file involved.
        #  3) ffmpeg's big probe was overkill for one frame → probesize 32k /
        #     analyzeduration 0, and each shot's timeout from 20 to 8s.
        # Hard cap: at most 2 processes per video (the normal case is exactly
        # 1).
        FileThumbs.ffmpeg_video_uses += 1        # counted for the prewarm summary
        kwargs = hidden_kwargs(8)                # 20s→8s — a stuck shot dies sooner
        scale = f"scale={max(px, 256)}:-2:flags=fast_bilinear"

        def _decode(buf: bytes) -> Image.Image | None:
            if not buf:
                return None
            try:
                with Image.open(io.BytesIO(buf)) as src:
                    return src.convert("RGB")
            except Exception as _exc_dbg:
                dbg_log(f"_ffmpeg_thumb._decode failed: {type(_exc_dbg).__name__}")
                return None

        def _run(argv_extra: list) -> tuple:
            """One ffmpeg run → (cover image|None, status).
            status: ok (healthy image) / black (black frame) / empty (no output).
            The GPU path (-hwaccel) was removed — for extracting one
            frame with a fast seek, CPU decode is faster and more stable
            and measurements confirmed it)."""
            try:
                proc = subprocess.run([
                    self.ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-probesize", "32k", "-analyzeduration", "0",
                    *argv_extra,
                    "-i", str(path), "-frames:v", "1", "-threads", "1",
                    "-an", "-sn", "-dn", "-vf", scale,
                    "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                ], **kwargs)
            except Exception as _exc_dbg:
                dbg_log(f"_ffmpeg_thumb._run failed: {type(_exc_dbg).__name__}")
                return None, "empty"
            im = _decode(proc.stdout)
            if im is None:
                return None, "empty"
            if _is_black(im):
                return None, "black"
            return _cover_square(im, px), "ok"

        def _shot(ss: str) -> tuple:
            """One frame at the ss point — keyframes only (skip_frame nokey)."""
            return _run(["-ss", ss, "-noaccurate_seek", "-skip_frame", "nokey"])

        def _rescue() -> Image.Image | None:
            """Emergency fallback: real decode from the start of the file (no
            seek and no skip_frame) — for truncated/broken files where the
            keyframe seek gives no output; at most once and only after the
            first shot's failure."""
            im, _ = _run([])
            return im

        # Duration read in-process from the container header (fast_duration —
        # a few ms, no subprocess at all; covers MKV/MP4/AVI/ASF).
        dur = None
        try:
            dur = fast_duration(path)
        except Exception as _exc_dbg:
            dbg_log(f"_ffmpeg_thumb failed: {type(_exc_dbg).__name__}")
            dur = None
        if dur and dur >= 20:
            primary, alt = f"{dur * 0.5:.1f}", f"{dur * 0.22:.1f}"
        elif dur and dur > 0:
            primary, alt = f"{max(dur * 0.4, 0.5):.1f}", None
        else:
            primary, alt = _FFMPEG_FALLBACK_SEEKS
        # GPU path removed — straight CPU decode.
        im, st = _shot(primary)             # the always-works CPU path
        if im is not None:
            return im
        if st == "black" and alt is not None:
            im2, _ = _shot(alt)            # black frame → one other point; 2 processes total
            if im2 is not None:
                return im2
            # Two different points came out black/empty — another
            # means (a sidecar image), not a forced decode; the hard cap of
            # 2 processes is preserved.
            return last_resort(path, px) if last_resort is not None else None
        # No output (or black with no substitute) → another means
        # first; only if there is no sidecar image at all, the last resort =
        # forced decode from the video itself (we have no choice).
        if last_resort is not None:
            im3 = last_resort(path, px)
            if im3 is not None:
                return im3
        return _rescue()

    # ---------------------------------------------------------------- file-type card
    def _type_card(self, path: Path, grp: str, px: int) -> Image.Image:
        """File-type thumbnail = just the downloaded icon itself, nothing else.

        Instead of the framed card
        (document+label+extension badge), the thumbnail is just the
        extension's real icon, contained inside the px frame — clean and
        close to the feel of Explorer's own icons. If the icon file is
        unavailable, the framed fallback card is drawn as the
        fallback.
        """
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            grp = "sub"
        ext = path.suffix.lstrip(".").upper()[:6] or "FILE"
        key = (grp, ext, px)
        img = _TC_CACHE.get(key)
        if img is not None:
            return img
        ico = _load_card_icon(_card_icon_name(suf, grp))
        if ico is None:                       # permanent fallback — the previous framed card
            return self._type_card_framed(path, grp, px)
        # the icons are white-background squares (512px with a 6% margin) — quality resize only
        img = ico.convert("RGB").resize((px, px), Image.Resampling.LANCZOS)
        if len(_TC_CACHE) >= 160:
            _TC_CACHE.clear()
        _TC_CACHE[key] = img
        return img

    def _type_card_framed(self, path: Path, grp: str, px: int) -> Image.Image:
        """The framed fallback card (document+icon+badge) — only a fallback
        when the icon is missing.

        The designed file-type card (real downloaded icon from the
        open-source pack).

        Spec: "download the icons from the internet — a pack that
        matches the app theme, like flaticon / vscode-icons / catppuccin".
        Since those sites cannot be scripted (captcha/anti-bot), 30 official
        icons of the open-source Material Icon Theme pack were taken from the
        GitHub repo and bundled inside assets/icons/ — the same visual feel
        as the requested packs, with no on-the-fly downloads at all (zero
        prerequisites, fully offline).

        Layout: the *document* icon with a folded corner + the real
        extension/group icon centered on the document (the Python snake for
        .py, the subtitle bubble for .srt, ...; full mapping in
        _EXT_ICON/_GRP_ICON) + the extension badge in the dark bottom bar. If
        the icon file is unavailable, the vector glyph (_draw_card_glyph) is
        the fallback."""
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            grp = "sub"
        c1, c2, label = GROUP_STYLE.get(grp, GROUP_STYLE["other"])
        c1 = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
        c2 = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
        ext = path.suffix.lstrip(".").upper()[:6] or "FILE"
        key = (grp + "|f", ext, px)
        img = _TC_CACHE.get(key)
        if img is not None:
            return img
        img = _grad_bg(c1, c2, px)
        d = ImageDraw.Draw(img)
        ac = c2                                        # dark accent on the white document
        # ---------- document: a white sheet with a folded corner ----------
        sw, sh = int(px * 0.46), int(px * 0.52)
        x0, y0 = (px - sw) // 2, int(px * 0.105)
        r = max(3, px // 40)
        fold = max(5, int(sw * 0.26))
        d.rounded_rectangle((x0 + 2, y0 + 3, x0 + sw + 2, y0 + sh + 3), r,
                            fill=(30, 38, 52))          # soft shadow
        d.rounded_rectangle((x0, y0, x0 + sw, y0 + sh), r, fill=(248, 250, 253))
        d.polygon([(x0 + sw - fold, y0), (x0 + sw, y0 + fold),
                   (x0 + sw - fold, y0 + fold)], fill=(199, 209, 225))
        cx, cy = x0 + sw // 2, y0 + sh // 2 + int(px * 0.012)
        g = min(sw, sh)
        lw = max(2, px // 48)
        lighten = tuple(min(255, v + 42) for v in ac)
        # ---------- real downloaded icon (fallback: vector glyph) ----------
        ico = _load_card_icon(_card_icon_name(suf, grp))
        if ico is not None:
            tgt = max(10, int(min(sw, sh) * 0.80))
            ic = ico.resize((tgt, tgt), Image.Resampling.LANCZOS)
            img.paste(ic, (cx - ic.width // 2, cy - ic.height // 2), ic)
        else:
            _draw_card_glyph(d, grp, cx, cy, g, sw, sh, x0, y0,
                             ac, lw, lighten, px)
        # ---------- group label (small, above the bar) ----------
        try:
            f_lab = ImageFont.load_default(size=max(7, int(px * 0.085)))
            bb = d.textbbox((0, 0), label, font=f_lab)
            d.text(((px - (bb[2] - bb[0])) / 2, px * 0.655 - bb[1]), label,
                   fill=(236, 241, 250), font=f_lab)
        except Exception as _exc_dbg:
            dbg_log(f"_type_card_framed failed: {type(_exc_dbg).__name__}")
            pass
        # ---------- extension badge bar at the bottom ----------
        by = int(px * 0.755)
        badge_c = tuple(int(v * 0.52) for v in c2)
        d.rectangle((0, by, px, px), fill=badge_c)
        try:
            f_ext = ImageFont.load_default(size=max(8, int(px * 0.125)))
            bb = d.textbbox((0, 0), ext, font=f_ext)
            d.text(((px - (bb[2] - bb[0])) / 2, (by + px) / 2 - (bb[3] - bb[1]) / 2 - bb[1]),
                   ext, fill=(255, 255, 255), font=f_ext)
        except Exception as _exc_dbg:
            dbg_log(f"_type_card_framed failed: {type(_exc_dbg).__name__}")
            pass
        if len(_TC_CACHE) >= 160:
            _TC_CACHE.clear()
        _TC_CACHE[key] = img
        return img

    # ---------------------------------------------------------------- blurred header
    def hero_backdrop(self, poster_png, w: int = 1200, h: int = 420):
        poster_png = Path(poster_png)
        stem = hashlib.sha1(f"hero|{poster_png}|{w}x{h}".encode()).hexdigest()[:16]
        out = cache_file(self.dir, stem)
        if out.exists():
            return out
        try:
            with Image.open(poster_png) as src:
                im = src.convert("RGB")
            scale = max(w / im.width, h / im.height)
            im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1),
                           Image.Resampling.LANCZOS)
            x = (im.width - w) // 2
            y = (im.height - h) // 2
            im = im.crop((max(x, 0), max(y, 0), max(x, 0) + w, max(y, 0) + h))
            im = im.filter(ImageFilter.GaussianBlur(28))
            dark = Image.new("RGB", im.size, (12, 17, 26))
            im = Image.blend(im, dark, 0.55)
            return save_cache_image(im, out)
        except Exception as _exc_dbg:
            dbg_log(f"hero_backdrop failed: {type(_exc_dbg).__name__}")
            return None


# ---------------------------------------------------------------- async loading
if _QT:
    def load_cache_qimage(path) -> "QImage | None":
        """Read a cache file into a QImage — Qt's own native decoder first
        (WebP/PNG/...), and if Qt does not recognize it, PIL (only if the
        format is supported)."""
        p = Path(str(path))
        try:
            q = QImage(str(p))
            if not q.isNull():
                return q
            im = read_cache_pil(p)
            if im is None:
                return None
            im = im.convert("RGBA")
            data = im.tobytes("raw", "RGBA")
            q = QImage(data, im.width, im.height, im.width * 4,
                       QImage.Format.Format_RGBA8888)
            return q.copy()          # detach from Python's temporary buffer
        except Exception as _exc_dbg:
            dbg_log(f"load_cache_qimage failed: {type(_exc_dbg).__name__}")
            return None

    def load_cache_pixmap(path) -> "QPixmap | None":
        """The function above as a QPixmap (for display in widgets)."""
        q = load_cache_qimage(path)
        if q is None or q.isNull():
            return None
        return QPixmap.fromImage(q)

    class ThumbSignals(QObject):
        ready = Signal(str, str, int)   # file path, ready png path, px

    class ThumbJob(QRunnable):
        """Generate the thumbnail on a background thread."""

        def __init__(self, ft: FileThumbs, path: str, px: int, signals: ThumbSignals):
            super().__init__()
            self.ft = ft
            self.path = path
            self.px = px
            self.signals = signals

        def run(self):
            # this is a UI request — prewarm phases must respect it
            try:
                FileThumbs.ui_request_tick()
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                pass
            try:
                out = self.ft.get(self.path, self.px)
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                out = None
            # Always signal — even on failure (e.g. a broken video): it used
            # to stay silent and the job key stuck in the "running" queue
            # forever while its card stayed a placeholder
            try:
                self.signals.ready.emit(self.path, str(out) if out else "", self.px)
            except RuntimeError:
                pass          # the app/page is closing — the signal's receiver is gone
else:  # pragma: no cover
    ThumbSignals = ThumbJob = None
