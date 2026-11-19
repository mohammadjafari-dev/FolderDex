# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Custom folder covers — normalized copies kept inside the app folder.

Invariant rules (must never be violated):
- nothing is ever written inside the user's folder (no desktop.ini, no
  icon.ico) — this app promises "it will never touch your files";
- the user's image is copied into APP_DIR/.covers/ and normalized to WebP,
  so the cover survives if they move/delete the original file;
- animated covers: max 512px, 60 frames, 5 s, 3 MB; frame 0 is always saved
  as a static file for the card grid (animation plays only on the details
  page).

File names are content hashes: re-importing the same image creates no new
file, two different covers never fight over one name, and deletion is
idempotent.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from PIL import Image

from i18n import T
# The replace-retry helper lives in one place, in imgcache, and both atomic
# writers (covers and the thumbnail cache) use the same implementation so
# their behavior under a transient Windows lock stays identical.
from imgcache import apply_exif_transpose, replace_with_retry
from portable import app_dir
from watchtime import dbg_log

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # older Pillow
    RESAMPLE = Image.LANCZOS

COVERS_DIR_NAME = ".covers"
STATIC_MAX_PX = 1024        # static cover source (high quality for hero/zoom)
ANIM_MAX_PX = 512
ANIM_MAX_FRAMES = 60
ANIM_MAX_MS = 5000
ANIM_MAX_BYTES = 3 * 1024 * 1024
SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
# Cover-import input caps — a giant PNG/multi-hundred-MB TIFF must not grab
# gigabytes of RAM (full file read + full-size decode; "app closed" reports
# on Windows). Images above the caps are rejected, not crashed on.
MAX_SOURCE_BYTES = 32 * 1024 * 1024     # source file size cap: 32 MB
# Real GIFs (screen recordings/downloads) are often larger than 32 MB. A GIF
# is decoded frame by frame and we cap 60 frames/5 s, so RAM stays controlled
# even with a higher cap — only GIFs get a separate cap.
MAX_GIF_SOURCE_BYTES = 128 * 1024 * 1024  # GIF file size cap: 128 MB
MAX_SOURCE_PIXELS = 40_000_000          # source dimensions cap: 40 megapixels
TMP_MAX_AGE_S = 3600                    # a .tmp-* older than one hour = definite leftover
# the save-error message — identical in covers and in the UI (import worker)
WRITE_ERR_TEXT = "ذخیره‌ی کاور ممکن نشد (فضای دیسک یا دسترسیِ پوشه‌ی برنامه)."
READ_ERR_TEXT = "این فایل تصویرِ معتبری نیست."
TOO_BIG_ERR_TEXT = "تصویر برای کاور بیش از حد بزرگ است."
# size cap — the message includes the cap number so the user knows how much to shrink
TOO_BIG_BYTES_ERR_TEXT = ("تصویر برای کاور بیش از حد بزرگ است."
                         " (سقف: {0} مگابایت).")
DEGRADED_MSG_TEXT = ("انیمیشنِ این عکس برای کاور سنگین بود — فریمِ اول "
                     "به‌صورت ساکن ذخیره شد.")

# static cover quality is 92 because this file is the "source" of all derived thumbnails
STATIC_QUALITY = 92
ANIM_QUALITY = 80           # softening ladder: 80 → 60 → 40 + fewer frames/smaller size


class CoverError(Exception):
    """A user-facing error (message text ready for the UI)."""


class CoverReadError(CoverError):
    """The *source* file has the problem (corrupt / missing / too large) —
    not saving.

    Derived from CoverError so every existing `except CoverError` keeps
    working; only the message differs — a healthy image is no longer reported
    as corrupt.
    """


class CoverWriteError(CoverError):
    """Saving into .covers/ failed (folder permissions / disk full)."""


def covers_dir(app_dir_path=None) -> Path:
    """The covers folder: <app_dir>/.covers — one meaning in both branches.

    (Previous bug: when app_dir_path was passed, the same path was returned
    instead of its .covers/ subfolder — so covers_dir(fake_app) returned the
    fake app folder and tests that counted files through this function proved
    nothing. Never created — path only.)
    """
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    return base / COVERS_DIR_NAME


def is_animated(path) -> bool:
    """Is this image file multi-frame? (animated GIF/WebP and the like)"""
    try:
        with Image.open(path) as im:
            return bool(getattr(im, "is_animated", False)) \
                or int(getattr(im, "n_frames", 1)) > 1
    except Exception as _exc_dbg:
        dbg_log(f"is_animated failed: {type(_exc_dbg).__name__}")
        return False


def _err_note(e: BaseException) -> str:
    """Error note for the log — type + errno/winerror + destination file name
    (no path).

    Previously only the exception type reached the log; with "could not save
    the cover" nobody could tell whether the cause was disk space,
    permissions, or a transient Windows lock. Now the failing stage + error
    code go into the anonymous log (dbg_log redacts absolute paths; the
    destination file name is only a content hash)."""
    return (f"{type(e).__name__} errno={getattr(e, 'errno', None)} "
            f"winerror={getattr(e, 'winerror', None)}")


def _atomic_save(im: Image.Image, out: Path, *, save_all=False,
                 append_images=None, duration=None, loop=None, quality=None,
                 method=None):
    """Atomic WebP write — a unique temp file + os.replace.

    method=None means the default (6 static / 4 animated); the quality ladder
    switches to a lighter method (2) on later attempts so the total time
    stays within seconds. Encode and replace are separate steps — each logs
    its own errno/winerror so the real cause shows up in the log."""
    tmp = out.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{out.name}")
    kw: dict = {"quality": quality if quality is not None else STATIC_QUALITY,
                "method": 6}
    if save_all:
        kw.update(save_all=True, append_images=append_images or [],
                  duration=duration or 100, loop=0 if loop is None else loop,
                  method=4)
    if method is not None:
        kw["method"] = method
    try:
        im.save(tmp, "WEBP", **kw)
    except OSError as e:
        # encode/write-to-temp error (disk full can happen right here too)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        dbg_log(f"_atomic_save encode failed: {_err_note(e)} file={out.name}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        replace_with_retry(tmp, out)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        dbg_log(f"_atomic_save replace failed: {_err_note(e)} file={out.name}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None


def _webp_decodes(path: Path) -> bool:
    """Does this WebP *actually* decode?

    A valid header is not enough: the payload can be corrupt mid-file and
    Pillow raises "could not create decoder object" on decode — such a file
    used to pass the idempotence check and was never repaired. Now the first
    frame (and, for animated files, the second frame if present) is decoded;
    the cost for a cover of a few MB is milliseconds and it is paid only on
    the idempotent path."""
    try:
        with Image.open(path) as im:
            im.load()
            if int(getattr(im, "n_frames", 1) or 1) > 1:
                try:
                    im.seek(1)
                    im.load()
                except EOFError:
                    pass                      # effectively single-frame — that is enough
        return True
    except Exception as _exc_dbg:
        dbg_log(f"_webp_decodes failed: {type(_exc_dbg).__name__}")
        return False


def _looks_like_webp(path: Path) -> bool:
    """Is the existing destination a healthy WebP? (import idempotence)

    The destination file name is the hash of the *source* content, so
    re-picking the same image targets the same destination. Re-importing
    used to re-encode and os.replace unconditionally — while the animated
    cover is playing on the details page, QMovie keeps the .anim.webp file
    open and Windows refuses the replacement with PermissionError → the
    misleading "disk space or access" message. Now a healthy destination
    means no write at all (exactly what this module's docstring promised
    from day one). A truncated/empty file (crash leftover) is rewritten.
    "Healthy" = a valid header *and* a real decode — a corrupt payload with
    a healthy header gets repaired, not re-accepted."""
    try:
        if path.stat().st_size < 32:
            return False
        with open(path, "rb") as f:
            head = f.read(12)
        if not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
            return False
        return _webp_decodes(path)
    except OSError:
        return False


def _validate_source(src: Path) -> Image.Image:
    """Validated opening of the source file — extension, content and the
    size cap are all checked.

    A source error is a CoverReadError (corrupt/oversized image); any later
    write error in import_cover is raised separately as CoverWriteError —
    this is where the misleading "calls a healthy image corrupt" message is
    eradicated.
    """
    if src.suffix.lower() not in SUPPORTED_EXT:
        raise CoverReadError(T(READ_ERR_TEXT))
    try:
        im = Image.open(src)
        if im.width * im.height > MAX_SOURCE_PIXELS:
            im.close()
            raise CoverReadError(T(TOO_BIG_ERR_TEXT))
        im.verify()                     # content — not just the extension
        im.close()
    except CoverReadError:
        raise
    except Exception as _exc_dbg:
        dbg_log(f"_validate_source failed: {type(_exc_dbg).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    try:
        im = Image.open(src)
        if im.width * im.height > MAX_SOURCE_PIXELS:
            im.close()
            raise CoverReadError(T(TOO_BIG_ERR_TEXT))
        if (im.format or "").upper() == "JPEG":
            # cover-scale fast decode — a 40-megapixel JPEG is never loaded
            # full-size into RAM; draft is header-level only and does not
            # reduce the quality of the 1024px output
            try:
                im.draft("RGB", (STATIC_MAX_PX, STATIC_MAX_PX))
            except Exception as _exc_dbg:
                dbg_log(f"_validate_source draft failed: {type(_exc_dbg).__name__}")
        im.load()
        # EXIF rotation — a phone photo with orientation != 1 used to be saved
        # lying on its side (a 400x200 landscape JPEG rotated 90° must come
        # out as 200x400). The helper returns animated images untouched
        # (exif_transpose drops frames) and falls back safely on any error.
        im = apply_exif_transpose(im)
    except CoverReadError:
        raise
    except Exception as _exc_dbg:
        dbg_log(f"_validate_source load failed: {type(_exc_dbg).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    return im


# Pillow decode errors mid-way through a multi-frame file (truncated/corrupt
# GIF): EOFError/Truncated/OSError plus IndexError/unpack errors from
# defective frames
_DECODE_ERRS = (OSError, EOFError, SyntaxError, ValueError, IndexError)


def _frame_iter(im: Image.Image):
    """Yield an image's frames as RGBA — stopping softly on a decode error.

    A truncated GIF raises OSError/EOFError on later frames; that
    error used to escape import_cover raw and the UI showed the wrong
    "could not save" message. Now frame iteration stops at the first decode
    error and the healthy frames read so far are returned (a product
    decision: a partial cover is usable; if no frame was read, import_cover
    raises a read error)."""
    n = int(getattr(im, "n_frames", 1) or 1)
    for i in range(n):
        try:
            im.seek(i)
        except EOFError:
            break
        except _DECODE_ERRS as _exc_dbg:
            dbg_log(f"_frame_iter seek failed at frame {i}: {type(_exc_dbg).__name__}")
            break
        try:
            fr = im.convert("RGBA")
        except _DECODE_ERRS as _exc_dbg:
            dbg_log(f"_frame_iter convert failed at frame {i}: {type(_exc_dbg).__name__}")
            break
        yield fr, int(im.info.get("duration", 100) or 100)


# Total time budget for the animated-encode ladder — after it is spent the
# first frame is saved statically (a noisy 60-frame GIF used to take
# ~13 s and then only say "too heavy", leaving the user no way out. Now the
# total time is bounded and a usable cover always remains)
ANIM_ENCODE_BUDGET_S = 8.0


def _encode_animated(frames: list[Image.Image], durations: list[int],
                     out: Path, *, t0: float | None = None) -> int | None:
    """Encode the animated cover with a softening ladder → final byte size
    or None (give up).

    Attempt order: quality 80→60→40 (later attempts use a lighter method) →
    skipping every other frame (durations ×2 to keep the rhythm) → shrinking
    to 384 and then 256 pixels. If no configuration fits under the byte cap
    or the time budget runs out, None is returned — import_cover then saves
    the first frame as a static cover (degraded) instead of raising a
    dead-end error."""
    start = time.monotonic() if t0 is None else t0
    half = frames[::2]
    half_durs = [d * 2 for d in durations[::2]]
    attempts: list[tuple[list, list, int, int]] = [
        (frames, durations, ANIM_QUALITY, 4),
        (frames, durations, 60, 4),
        (frames, durations, 40, 2),
        (half, half_durs, 40, 2),
    ]
    for px in (384, 256):
        small = [f.copy() for f in half]
        for f in small:
            f.thumbnail((px, px), RESAMPLE)
        attempts.append((small, half_durs, 40, 2))
    for frs, durs, q, method in attempts:
        if time.monotonic() - start > ANIM_ENCODE_BUDGET_S:
            dbg_log("_encode_animated: time budget exhausted -> degraded")
            return None
        _atomic_save(frs[0], out, save_all=True, append_images=frs[1:],
                     duration=durs, loop=0, quality=q, method=method)
        try:
            size = out.stat().st_size
        except OSError:
            size = ANIM_MAX_BYTES + 1
        if size <= ANIM_MAX_BYTES:
            return size
    dbg_log("_encode_animated: every rung over budget -> degraded")
    return None


def _digest_of(path: Path) -> str:
    """Block-wise (1 MB) SHA-1 — the source file is never read into RAM in one piece."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def import_cover(src, app_dir_path=None) -> dict:
    """Normalize + copy a cover into APP_DIR/.covers/ → info about it.

    Returns: {"poster": "<rel>", "poster_static": "<rel>|None",
              "animated": bool, "bytes": int}
    Paths are relative to the app folder (with /) — if the whole portable
    folder moves, the covers are still found.

    GIFs too heavy to fit under the 3 MB cap degrade to a static cover
    (frame 0) instead of erroring — the "degraded": True key in the returned
    dict tells the UI to show the "animation dropped" message.

    Input caps (MAX_SOURCE_BYTES / MAX_GIF_SOURCE_BYTES /
    MAX_SOURCE_PIXELS) are rejected with CoverReadError; write errors
    (permissions/disk full) are raised separately as CoverWriteError.
    """
    src = Path(src)
    if not src.is_file():
        raise CoverReadError(T(READ_ERR_TEXT))
    try:
        # GIFs have their own size cap — they decode frame by frame and we
        # cap frames/duration; 32 MB was too small for real GIFs
        cap = (MAX_GIF_SOURCE_BYTES if src.suffix.lower() == ".gif"
               else MAX_SOURCE_BYTES)
        if src.stat().st_size > cap:
            raise CoverReadError(T(TOO_BIG_BYTES_ERR_TEXT).format(
                max(1, round(cap / (1024 * 1024)))))
    except OSError as e:
        dbg_log(f"import_cover stat failed: {type(e).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    dest = base / COVERS_DIR_NAME
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        dbg_log(f"import_cover mkdir failed: {type(e).__name__}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None

    im = _validate_source(src)
    digest = _digest_of(src)
    animated = bool(getattr(im, "is_animated", False)) \
        or int(getattr(im, "n_frames", 1)) > 1

    static_path = dest / f"{digest}.webp"
    static_rel = f"{COVERS_DIR_NAME}/{static_path.name}"

    if not animated:
        if not _looks_like_webp(static_path):      # idempotent
            st = im.convert("RGBA")
            st.thumbnail((STATIC_MAX_PX, STATIC_MAX_PX), RESAMPLE)
            try:
                _atomic_save(st, static_path, quality=STATIC_QUALITY)
            except CoverError:
                raise
            except OSError as e:
                # write error (disk full / permissions) — must not be reported as "corrupt image"
                dbg_log(f"import_cover save failed: {_err_note(e)}")
                raise CoverWriteError(T(WRITE_ERR_TEXT)) from None
        try:
            size = static_path.stat().st_size
        except OSError:
            size = 0
        return {"poster": static_rel, "poster_static": None,
                "animated": False, "bytes": size}

    # ---- animated cover: frames are shrunk and trimmed to the caps ----
    frames: list[Image.Image] = []
    durations: list[int] = []
    total_ms = 0
    for fr, dur in _frame_iter(im):
        if len(frames) >= ANIM_MAX_FRAMES or total_ms >= ANIM_MAX_MS:
            break
        fr.thumbnail((ANIM_MAX_PX, ANIM_MAX_PX), RESAMPLE)
        frames.append(fr)
        durations.append(max(10, min(int(dur), 500)))
        total_ms += durations[-1]
    if not frames:
        # no healthy frame decoded — an explicit read error, not a raw
        # error (which the UI showed as "could not save") and not silence
        raise CoverReadError(T(READ_ERR_TEXT))
    # Frame 0 is saved statically too (the card grid and hero view read it)
    # made_static = only the file *this attempt* created — a pre-existing
    # static of the same GIF (previous meta or another title may reference
    # it) must not be wiped if the next stage fails
    made_static = False
    if not _looks_like_webp(static_path):
        st0 = frames[0].copy()
        st0.thumbnail((STATIC_MAX_PX, STATIC_MAX_PX), RESAMPLE)
        try:
            _atomic_save(st0, static_path, quality=STATIC_QUALITY)
            made_static = True
        except CoverError:
            raise
        except OSError as e:
            dbg_log(f"import_cover save failed: {_err_note(e)}")
            raise CoverWriteError(T(WRITE_ERR_TEXT)) from None

    anim_path = dest / f"{digest}.anim.webp"
    # Idempotence: a healthy animated destination means no re-encode —
    # exactly the "same GIF, playing on the details page, picked again"
    # scenario: on Windows a rewrite of QMovie's open file is impossible.
    if _looks_like_webp(anim_path):
        try:
            size = anim_path.stat().st_size
        except OSError:
            size = ANIM_MAX_BYTES + 1
        if size <= ANIM_MAX_BYTES:
            anim_rel = f"{COVERS_DIR_NAME}/{anim_path.name}"
            return {"poster": anim_rel, "poster_static": static_rel,
                    "animated": True, "bytes": size}
    t0 = time.monotonic()
    try:
        size = _encode_animated(frames, durations, anim_path, t0=t0)
    except CoverError:
        # the animated cover failed — only the *freshly built* static must not be orphaned
        if made_static:
            try:
                static_path.unlink(missing_ok=True)
            except OSError as _exc_dbg:
                dbg_log(f"import_cover cleanup failed: {type(_exc_dbg).__name__}")
        raise
    if size is None:
        # no animated configuration fit under the cap — instead of a
        # dead-end error, the first frame (already saved statically) becomes
        # the final cover and the degraded key tells the UI to show the
        # "animation dropped" message
        try:
            anim_path.unlink(missing_ok=True)
        except OSError as _exc_dbg:
            dbg_log(f"import_cover cleanup failed: {type(_exc_dbg).__name__}")
        dbg_log("import_cover: animated cover degraded to static frame")
        try:
            st_size = static_path.stat().st_size
        except OSError:
            st_size = 0
        return {"poster": static_rel, "poster_static": None,
                "animated": False, "bytes": st_size, "degraded": True}
    anim_rel = f"{COVERS_DIR_NAME}/{anim_path.name}"
    return {"poster": anim_rel, "poster_static": static_rel,
            "animated": True, "bytes": size}


def collect_garbage(referenced: set[str], app_dir_path=None, store=None) -> int:
    """Delete .covers/ files that no longer have meta referencing them.

    Replaced covers and covers of deleted titles used to stay forever (and
    the backup ballooned). Idempotent — after every full scan it is called
    with the set of poster/poster_static values of all items.
    Returns: the number of deleted files.
    If store is given, the reference set is re-read *under the Store lock*
    before every unlink — a GC running on a stale snapshot (taken when the
    thread started) can no longer delete a cover that has meanwhile been
    linked into meta.
    Two guards against mass deletion:
    1) Quarantined database — the Store is fresh and empty; without a guard,
       *every* recoverable healthy cover would be wiped. GC does not run at
       all on a quarantined DB.
    2) DB with no items but a populated .covers/ — the same scenario one run
       later (after the app reopens and the quarantine flag has reset) and
       also a genuinely empty library. Mass deletion is forbidden here too;
       possible orphans survive harmlessly (data safety is worth a few
       hundred KB of leftover files). If items exist but reference no covers,
       GC still cleans up as before — those orphans are real ones.
    The reference check + unlink now both happen under the same Store lock
    (fully atomic; the unlink used to be outside the lock), and if refs
    cannot be read the file is *not* deleted (fail-safe: a leftover file is
    harmless; a deleted cover is not)."""
    d = covers_dir(app_dir_path)
    if not d.is_dir():
        return 0
    # guard 1 — quarantine: while the flag is set, no deletion at all (not even temp files)
    if store is not None and bool(getattr(store, "db_quarantined", False)):
        dbg_log("collect_garbage skipped: db is quarantined — covers protected")
        return 0
    refs = {str(v).replace("\\", "/") for v in referenced if v}
    n = 0
    now = time.time()
    try:
        files = [p for p in d.iterdir() if p.is_file()]
    except OSError as _exc_dbg:
        dbg_log(f"collect_garbage iterdir failed: {type(_exc_dbg).__name__}")
        return 0
    for p in files:
        if not p.name.startswith(".tmp-"):
            continue
        # atomic-write temp file — if the app closes mid-_atomic_save
        # (power cut/kill) it stays forever and the backup balloons;
        # older than one hour = definite leftover, deleted
        try:
            if now - p.stat().st_mtime < TMP_MAX_AGE_S:
                continue
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage stat failed: {type(_exc_dbg).__name__}")
            continue
        try:
            p.unlink()
            n += 1
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage tmp unlink failed: {type(_exc_dbg).__name__}")
    # guard 2 — DB with no items but a populated .covers/: mass deletion forbidden
    if store is not None:
        try:
            with store._lock:
                fresh0 = store.cover_refs()
                n_items = len(store.data.get("items", {}))
        except Exception as _exc_dbg:
            dbg_log(f"collect_garbage fresh refs failed: {type(_exc_dbg).__name__}")
            fresh0 = None
        if fresh0 is not None and not fresh0 and n_items == \
                0 and any(not p.name.startswith(".tmp-") for p in files):
            dbg_log("collect_garbage skipped: store has no items but .covers "
                    "is not empty (fresh/rebuilt db?) — covers protected")
            return n
    for p in files:
        if p.name.startswith(".tmp-"):
            continue
        if f"{COVERS_DIR_NAME}/{p.name}" in refs:
            continue
        # Grace-period guard: a *fresh* file is spared — the user may have
        # imported a cover just now and its meta may not be written to the
        # database yet; deleting it would lose the freshly built cover.
        try:
            if now - p.stat().st_mtime < 60:   # younger than 60 s → maybe an import is in flight
                continue
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage stat failed: {type(_exc_dbg).__name__}")
            continue
        if store is None:
            try:
                p.unlink()
                n += 1
            except OSError as _exc_dbg:
                dbg_log(f"collect_garbage unlink failed: {type(_exc_dbg).__name__}")
            continue
        # Fresh check + unlink atomic under the lock — the cover mutators
        # (set_custom_poster/clear_custom_poster) take the same lock; there
        # is no longer any window between "linked into meta" and "deleted"
        try:
            with store._lock:
                fresh = store.cover_refs()
                if f"{COVERS_DIR_NAME}/{p.name}" in fresh:
                    continue
                try:
                    p.unlink()
                    n += 1
                except OSError as _exc_dbg:
                    dbg_log(f"collect_garbage unlink failed: {type(_exc_dbg).__name__}")
        except Exception as _exc_dbg:
            # fail-safe: refs could not be read → do not delete
            dbg_log(f"collect_garbage fresh refs failed: {type(_exc_dbg).__name__}")
    return n


def delete_cover_files(meta_poster, meta_static, app_dir_path=None) -> None:
    """Delete saved cover files — idempotent and strictly inside .covers/.

    Meta values are relative (.covers/<name>); any other path (like the old
    absolute paths from the previous edit form) is deliberately left alone —
    the app never touches the user's files.
    """
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    dest = base / COVERS_DIR_NAME
    for v in (meta_poster, meta_static):
        if not v:
            continue
        v = str(v).replace("\\", "/")
        if not v.startswith(COVERS_DIR_NAME + "/"):
            continue
        try:
            p = (base / v).resolve()
            if dest.resolve() in p.parents and p.is_file():
                p.unlink()
        except OSError as _exc_dbg:
            dbg_log(f"delete_cover_files failed: {type(_exc_dbg).__name__}")
            continue
