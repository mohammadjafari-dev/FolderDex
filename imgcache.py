# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Image cache file format — **WebP** (the only format we write).

History: this module once also wrote the cache as JPEG XL. Measurement on
real thumbnails (160–512px) showed that at these sizes WebP is both smaller
and faster (native decode in Qt) — JXL was only smaller than PNG. Legacy
.png caches are still read; .jxl caches are no longer read (no decoder ships
with the app) and behave like a missing file so they get rebuilt as WebP
automatically.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

from watchtime import dbg_log

WEBP_QUALITY = 84      # method 4 — several times faster than JXL and smaller at thumbnail sizes
WEBP_METHOD = 4


def apply_exif_transpose(im: Image.Image) -> Image.Image:
    """Applies the EXIF (orientation tag) rotation to the image — single-frame
    images only.

    A phone photo with orientation≠1 (most portrait mobile shots) used to be
    saved rotated/flipped without this — import_cover, folder posters and
    file thumbnails all go through this helper.

    Why single-frame only? exif_transpose on a multi-frame image drops
    frames on various Pillow versions (measured with Pillow 11.3:
    n_frames 2→1) — an animated cover would silently become static.
    Animated GIF/WebP/APNG effectively carry no orientation, so we leave
    them untouched. Any error = the image is returned unmodified (safe
    play — a missing EXIF tag must never break the import).
    """
    try:
        if getattr(im, "is_animated", False) \
                or int(getattr(im, "n_frames", 1) or 1) > 1:
            return im
        if int((im.getexif() or {}).get(0x0112, 1) or 1) == 1:
            return im
        fixed = ImageOps.exif_transpose(im)
        return fixed if fixed is not None else im
    except Exception as _exc_dbg:
        dbg_log(f"apply_exif_transpose failed: {type(_exc_dbg).__name__}")
        return im


def _webp_ok() -> bool:
    """Does the installed Pillow write WebP? (always true in official wheels)"""
    try:
        import io
        Image.new("RGB", (2, 2)).save(io.BytesIO(), "WEBP")
        return True
    except Exception as _exc_dbg:
        dbg_log(f"_webp_ok failed: {type(_exc_dbg).__name__}")
        return False


WEBP_OK = _webp_ok()
CACHE_EXT = ".webp" if WEBP_OK else ".png"
# Only formats we can actually read — .jxl is deliberately absent (see the
# docstring above): handing back an unreadable cache would leave the card
# stuck on the letter placeholder forever.
_LEGACY_EXTS = (".webp", ".png")

_CACHE_FALLBACK_NAME = "FolderDex-cache"


def writable_cache_dir(cache_dir, fallback_name: str = _CACHE_FALLBACK_NAME
                       ) -> tuple[Path, bool]:
    """Ensures a **writable** cache folder — falls back to %TEMP%.

    A portable app may be extracted into a read-only location (Program
    Files, a locked drive, a write-protected stick). Previously a direct
    `mkdir` raised `PermissionError` that escaped `main()`, and the user
    only saw "the app won't open". Now: mkdir plus a real write probe; on
    failure the cache folder moves to %TEMP%.
    Returns: (effective cache path, whether the temp fallback was engaged).
    """
    import tempfile

    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / f".wprobe-{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            raise PermissionError(f"not writable: {e}") from None
        return cache_dir, False
    except OSError as _exc_dbg:
        dbg_log(f"cache dir not writable ({type(_exc_dbg).__name__}) "
                f"— falling back to temp ({fallback_name})")
    fb = Path(tempfile.gettempdir()) / fallback_name
    try:
        fb.mkdir(parents=True, exist_ok=True)
        return fb, True
    except OSError as _exc_dbg:
        dbg_log(f"cache temp fallback failed: {type(_exc_dbg).__name__}")
        # Even %TEMP% had no write access — keep the original path (later
        # errors are caught at the point of use; no silent death at startup)
        return cache_dir, False


def cache_file(directory, stem: str) -> Path:
    """Cache file path: the current-format one if present, otherwise the oldest
    still-**readable** format."""
    d = Path(directory)
    cur = d / (stem + CACHE_EXT)
    try:
        if cur.exists():
            return cur
        for ext in _LEGACY_EXTS:
            if ext == CACHE_EXT:
                continue
            q = d / (stem + ext)
            if q.exists():
                return q
    except OSError:
        pass
    return cur


def replace_with_retry(tmp: Path, out: Path, max_attempts: int = 8) -> None:
    """os.replace with a short retry loop against Windows transient file locks.

    Windows refuses to replace an *open* file (sharing violation —
    winerror 32/33): antivirus/indexers briefly scan freshly written files
    and several concurrent workers race for the same destination, so a
    back-to-back replacement can fail transiently and surface a save error.
    Originally only covers.py had a retry while imgcache's own replacement
    stayed bare — in a storm of 50 concurrent workers a few replacements hit
    locks and the error path climbed all the way to a QMessageBox.
    Only PermissionError is retried, and only a bounded number of times
    (8 attempts, ~0.8s total — on the worker thread, not the UI); all other
    errors (full disk / permanent denial) propagate immediately and the
    previous file stays intact. Linux has no such lock, so the loop exits
    on the first attempt; its guard in tests/test_transient_locks.py works by
    fault injection, not a real Windows race."""
    last: PermissionError | None = None
    for attempt in range(max_attempts):
        try:
            os.replace(tmp, out)
            return
        except PermissionError as e:
            last = e
            dbg_log(f"replace-retry {attempt + 1}/{max_attempts}: "
                    f"{type(e).__name__} errno={getattr(e, 'errno', None)} "
                    f"winerror={getattr(e, 'winerror', None)}")
            time.sleep(0.03 * (attempt + 1))
        except OSError as e:
            dbg_log(f"replace failed: {type(e).__name__} "
                    f"errno={getattr(e, 'errno', None)} "
                    f"winerror={getattr(e, 'winerror', None)} on {out.name}")
            raise
    raise last  # type: ignore[misc]


def save_cache_image(im: Image.Image, out: Path):
    """Atomically save a cache image (WebP; PNG fallback).

    Writes to a unique temp file plus an atomic replace with a retry
    against Windows transient locks: two parallel workers on one output
    cannot produce a corrupt file, and a momentary replace lock no longer
    silently demotes the cache to PNG. The source color profile is not
    carried over. If WebP fails, a PNG is written next to the same path
    and the actual path is returned — callers must always use the return
    value, never `out`.
    """
    out = Path(out)
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    try:
        im.info.pop("icc_profile", None)
    except Exception as _exc_dbg:
        dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
        pass
    tmp = out.with_name(f"{out.stem}.tmp{os.getpid()}{threading.get_ident()}{out.suffix}")
    try:
        if out.suffix.lower() != ".png":
            im.save(tmp, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
        else:
            im.save(tmp, "PNG", optimize=False, compress_level=1)
        replace_with_retry(tmp, out)
        return out
    except Exception as _exc_dbg:
        dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        alt = out.with_suffix(".png")
        atmp = alt.with_name(f"{alt.stem}.tmp{os.getpid()}{threading.get_ident()}.png")
        try:
            im.save(atmp, "PNG", optimize=False, compress_level=1)
            replace_with_retry(atmp, alt)
            return alt
        except Exception as _exc_dbg:
            dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
            try:
                atmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise


def read_cache_pil(path: Path) -> Image.Image | None:
    """Read a cache file into a PIL image — for non-Qt paths (HTML export etc.)."""
    try:
        im = Image.open(path)
        im.load()
        return im
    except Exception as _exc_dbg:
        dbg_log(f"read_cache_pil failed: {type(_exc_dbg).__name__}")
        return None
