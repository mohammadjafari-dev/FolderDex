# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Thumbnail cache: .ico files and images are converted once into a small WebP
and afterwards read straight from the cache (subsequent scans get very fast)."""
from __future__ import annotations

import hashlib
import time
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image

from imgcache import apply_exif_transpose, cache_file, save_cache_image, writable_cache_dir
from watchtime import dbg_log

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # older Pillow
    RESAMPLE = Image.LANCZOS


# User data that must never be wiped by a cache clear (same protection as
# enforce_cache_size_limit): durations.db is the video-duration database and
# watch_later/ holds mpv resume state — the "clear image cache" button used
# to delete both and silently reset every duration + resume position.
_PROTECTED_RELPARTS = frozenset({"watch_later"})
_PROTECTED_NAMES = frozenset({
    "durations.db", "durations.db-journal", "durations.db-wal", "durations.db-shm",
})


def clear_cache(cache_dir) -> int:
    """Wipe the image/thumbnail cache. Returns: bytes freed.

    user data (durations.db + watch_later/) is protected — the
    button's promise is "image cache", not "my watch state"; previously the
    whole cache folder including the duration database was emptied while the
    DurationStore still held an open handle on the deleted file."""
    cache_dir = Path(cache_dir)
    freed = 0
    if not cache_dir.exists():
        return 0
    for p in sorted(cache_dir.rglob("*"), reverse=True):
        try:
            if p.is_file():
                if p.name in _PROTECTED_NAMES:
                    continue
                rel = p.relative_to(cache_dir)
                if rel.parts and rel.parts[0] in _PROTECTED_RELPARTS:
                    continue
                freed += p.stat().st_size
                p.unlink()
            elif p.is_dir():
                # rmdir only succeeds when the dir is already empty —
                # protected content keeps its parent folder alive
                p.rmdir()
        except OSError:
            pass
    cache_dir.mkdir(parents=True, exist_ok=True)
    return freed


def enforce_cache_size_limit(cache_dir, max_mb) -> tuple[int, int]:
    """Cache size limit, LRU style: once the total exceeds the cap, the oldest
    files (by mtime) are deleted until it is back under the cap.
    max_mb <= 0 means unlimited (no-op).

    Returns: (bytes freed, files removed). User data is exempt:
    durations.db (duration cache) and watch_later (mpv resume state) are
    never wiped.
    """
    cache_dir = Path(cache_dir)
    if not max_mb or max_mb <= 0 or not cache_dir.exists():
        return (0, 0)
    cap = int(float(max_mb) * 1024 * 1024)   # float: a fractional value must not wipe the whole cache!
    skip_names = {"durations.db", "durations.db-journal", "durations.db-wal", "durations.db-shm"}
    files = []
    total = 0
    for p in cache_dir.rglob("*"):
        try:
            if not p.is_file():
                continue
            rel = p.relative_to(cache_dir)
            if rel.parts[0] == "watch_later" or p.name in skip_names:
                continue
            st = p.stat()
            total += st.st_size
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    if total <= cap:
        return (0, 0)
    files.sort(key=lambda t: t[0])          # oldest first
    # In capped mode, *file* thumbnails (the files/ subfolder)
    # go first and *folder posters* last — posters are far more valuable
    def _is_file_thumb(t):
        try:
            return t[2].relative_to(cache_dir).parts[0] == "files"
        except Exception as _exc_dbg:
            dbg_log(f"enforce_cache_size_limit._is_file_thumb failed: {type(_exc_dbg).__name__}")
            return False
    ordered = [t for t in files if _is_file_thumb(t)] + \
              [t for t in files if not _is_file_thumb(t)]
    freed = removed = 0
    for _mt, sz, p in ordered:
        if total <= cap:
            break
        try:
            p.unlink()
            total -= sz
            freed += sz
            removed += 1
        except OSError:
            continue
    return (freed, removed)


def prune_scale_generation(cache_dir, active_px: int,
                           max_age_days: float = 7.0) -> tuple[int, int]:
    """Prune the previous display-scale generation of the cache.

    The cache key includes cache_px, but the px value is not part of the
    *file name* — so mapping a file back to its scale is impossible and only
    mtime can serve as the criterion. To stay safe, this only runs when this
    boot's scale differs from the previous one (the .scale_gen marker inside
    the cache folder): image files older than max_age days are removed.
    Later boots with the same scale delete nothing — even the old part of
    the current cache stays untouched (no pointless rebuild of every
    poster).
    Returns: (bytes freed, files removed).
    """
    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as _exc_dbg:
        dbg_log(f"prune_scale_generation mkdir failed: {type(_exc_dbg).__name__}")
        return (0, 0)
    marker = cache_dir / ".scale_gen"
    prev: int | None
    try:
        prev = int(marker.read_text(encoding="ascii", errors="ignore").strip() or 0)
    except (OSError, ValueError):
        prev = None
    try:
        marker.write_text(str(int(active_px)), encoding="ascii")
    except OSError as _exc_dbg:
        dbg_log(f"prune_scale_generation marker failed: {type(_exc_dbg).__name__}")
    if prev is None or prev == int(active_px):
        return (0, 0)                 # first boot or same scale as before — do nothing
    cutoff = time.time() - max_age_days * 86400
    freed = removed = 0
    for p in cache_dir.rglob("*"):
        try:
            if not p.is_file() or p.suffix.lower() not in (".webp", ".png"):
                continue
            if p.name.startswith(".tmp-"):
                continue
            st = p.stat()
            if st.st_mtime >= cutoff:
                continue
            p.unlink()
            removed += 1
            freed += st.st_size
        except OSError as _exc_dbg:
            dbg_log(f"prune_scale_generation unlink failed: {type(_exc_dbg).__name__}")
            continue
    return (freed, removed)


def cache_px_for_app(app) -> int:
    """Cache size from the Windows display scale — 100% -> 256, 150% -> 384,
    200% -> 512.

    The card is 186x248 logical points; at 200% scaling that is 372x496
    physical pixels and the 256 cache looked stretched (visible quality
    loss). We never go above 512 so the cache does not grow for no reason.
    """
    try:
        scr = app.primaryScreen() if app is not None else None
        dpr = float(scr.devicePixelRatio()) if scr is not None else 1.0
    except Exception as _exc_dbg:
        dbg_log(f"cache_px_for_app failed: {type(_exc_dbg).__name__}")
        dpr = 1.0
    if dpr <= 1.1:
        return 256
    if dpr <= 1.6:
        return 384
    return 512


def cache_size(cache_dir) -> int:
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    total = 0
    for p in cache_dir.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


class ThumbnailCache:
    # memory cache cap for paths (LRU) — huge libraries must not swallow RAM
    MEM_LIMIT = 2000

    def __init__(self, cache_dir, size=(256, 256)):
        # Read-only app folder? (Program Files / locked flash drive) — a
        # PermissionError used to escape main() and the user just saw "it
        # won't open"; now the cache moves to %TEMP% and a fallback flag
        # reaches the status bar.
        d, ro = writable_cache_dir(cache_dir)
        self.cache_dir = d
        self.read_only_fallback = ro
        self.size = size
        self._memory = OrderedDict()
        self._mlock = threading.Lock()   # the cache is read/written from two threads (scanner + poster loader)

    def _cache_path(self, src: Path) -> Path:
        st = src.stat()
        # normcase+abspath instead of resolve: on Windows resolve() hits the
        # disk to follow links, which made caching every file (network
        # drives/HDDs especially) slow.
        # New writes are WebP; old .png caches are still read so that
        # prebuilt posters are not rebuilt.
        p = os.path.normcase(os.path.abspath(str(src)))
        # Generation suffix |v5: EXIF rotation applied at render time —
        # caches of earlier generations (including stale thumbnails of
        # rotated photos) rebuild automatically (no migration code; a
        # one-time rebuild).
        raw = f"{p}|{st.st_mtime_ns}|{st.st_size}|{self.size}|v5"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return cache_file(self.cache_dir, key)

    def get(self, src) -> Path | None:
        if not src:
            return None
        src = Path(src)
        key = str(src)
        with self._mlock:
            hit = self._memory.get(key)
            if hit is not None:
                if hit.exists():
                    self._memory.move_to_end(key)          # LRU: freshest at the end
                    return hit
                self._memory.pop(key, None)                # cache file gone — rebuild it
        try:
            out = self._cache_path(src)
        except OSError:
            return None
        if not out.exists():
            try:
                with Image.open(src) as im:
                    ico = getattr(im, "ico", None)
                    if ico:  # pick the largest size inside the .ico file
                        best = max(ico.sizes(), key=lambda s: s[0] * s[1])
                        im.size = best
                        im.load()
                    else:
                        # Large JPEGs: decode directly at a smaller size
                        # (draft) — full decode + downscale of 4K posters
                        # was far slower
                        try:
                            if (im.format or "") == "JPEG":
                                im.draft("RGB", (self.size[0] * 2, self.size[1] * 2))
                        except Exception as _exc_dbg:
                            dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                            pass
                    im = apply_exif_transpose(im)   # EXIF-rotated photo
                    im = im.convert("RGBA")
                    # Very large images (multi-megapixel PNG/JPEG): one quick
                    # integer-factor shrink before the precise resize —
                    # several times faster, visually identical
                    _m0 = max(im.size)
                    _t0 = max(self.size)
                    if _m0 > _t0 * 4:
                        try:
                            im = im.reduce(max(1, _m0 // (_t0 * 4)))
                        except Exception as _exc_dbg:
                            dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                            pass
                    im.thumbnail(self.size, RESAMPLE)
                    # WebP with a PNG fallback — atomic and free of the
                    # source's possibly-broken color profile (the cause of
                    # libpng warnings in Qt)
                    out = save_cache_image(im, out)
            except Exception as _exc_dbg:
                dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                return None
        with self._mlock:
            while len(self._memory) >= self.MEM_LIMIT:
                try:
                    self._memory.popitem(last=False)  # drop the oldest (FIFO)
                except KeyError:
                    break
            self._memory[key] = out
        return out
