# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Library scan (FolderDex).

Expected layout:
    <root (category)> / <title folder> / files...
    e.g.:  D:\\Anime / Attack on Titan / E01.mkv ...

Each title's poster is found at these paths (in priority order):
    1) the icon defined in desktop.ini (IconResource / IconFile)
    2) any .ico file inside the folder
    3) (optional) images such as poster.jpg / cover.jpg / folder.jpg
"""
from __future__ import annotations

from i18n import T

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from watchtime import dbg_log

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m2ts", ".ts",
    ".flv", ".webm", ".mpg", ".mpeg", ".m4v",
}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus"}
IMAGE_EXTS_SET = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
# .jxl was dropped: with the JPEG XL plugin removed there is no decoder for a
# .jxl poster/icon — listing it only built an empty placeholder.
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".cbz", ".cbr", ".cb7", ".epub",
                ".tgz", ".tbz2", ".txz", ".tzst", ".cab", ".iso",
                # Compound tar bundles: Path.suffix sees only the last extension
                # ("book.tar.bz2" reads as ".bz2"), so the full suffixes must be
                # listed or these archives silently fall out of every
                # `ext in ARCHIVE_EXTS` check.
                ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4"}
DOC_EXTS = {".pdf", ".txt", ".md", ".doc", ".docx", ".srt", ".ass", ".ssa", ".vtt"}


def archive_ext_of(name) -> str:
    """Effective lowercase extension for archive-aware classification.

    Identical to Path.suffix for every ordinary name; for the compound tar
    bundles (.tar.bz2, .tar.xz, ...) it returns the full suffix so that the
    members of ARCHIVE_EXTS / _ARC_PREF match. Without this, a book.tar.bz2
    would be classified by its ".bz2" tail and never reach the archive card,
    the archive thumbnail or the by-type stats."""
    low = str(name).lower()
    if low.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4")):
        i = low.find(".tar.")
        return low[i:]
    i = low.rfind(".")
    return low[i:] if i >= 0 else ""


# "Document" files that are grouped loosely at the root itself, like videos
# (a folder full of archives/PDFs → one card named after the
# folder, not zero and not dozens of cards)
LOOSE_DOC_EXTS = ARCHIVE_EXTS | {".pdf"}

# Only "reserved poster" files are removed from the file list
# (poster/cover/folder/... and .ico icons) because they are truly metadata —
# otherwise the shown count reads one short of the real file count. An
# ordinary fallback image (when no reserved poster exists and the
# folder's first picture becomes the thumbnail) is content, and removing it
# from the list was a bug.
RESERVED_COVER_STEMS = frozenset({
    "poster", "cover", "folder", "front", "thumb", "banner", "icon",
})


def is_reserved_cover(p) -> bool:
    """Is this file a reserved poster (metadata, not content)?"""
    try:
        p = Path(p)
        return p.suffix.lower() in (".ico", ".icl") or \
            p.stem.strip().lower() in RESERVED_COVER_STEMS
    except Exception as _exc_dbg:
        dbg_log(f"is_reserved_cover failed: {type(_exc_dbg).__name__}")
        return False
CODE_EXTS = {".py", ".js", ".html", ".css", ".json", ".ini", ".xml", ".bat", ".ps1"}
EXE_EXTS = {".exe", ".msi", ".bat", ".lnk"}

# "Watched status must be smart": "consumable" files used to
# compute a title's completion state — across all categories, not just video.
#   · Subtitles are never watched/read → never part of the "total".
#   · Archives (zip/rar/cbz…) count as "read/seen" → they count.
#   · Documents/images/audio and executables are consumable too.
#   · Machine sidecars (nfo/xml/json/lnk/…) are excluded from the total so
#     "mark all watched" always reflects real completion.
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}
WATCHABLE_EXTS = (VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS_SET | ARCHIVE_EXTS
                  | {".pdf", ".txt", ".md", ".doc", ".docx", ".rtf", ".odt",
                     ".exe", ".msi"}) - SUBTITLE_EXTS

ICON_EXTS = {".ico", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
POSTER_NAMES = ("poster", "cover", "folder", "front", "thumbnail")
POSTER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
IGNORED_DIRS = {"$recycle.bin", "system volume information", "found.000", ".thumb_cache"}
HIDDEN_FILES = {"desktop.ini", "thumbs.db"}

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(text) -> str:
    """Convert digits to Persian for display — Persian mode only; Latin
    digits in English.

    This single choke point makes hundreds of counters in the UI (and logs)
    bilingual without touching their call sites."""
    s = str(text)
    from i18n import is_rtl
    return s.translate(FA_DIGITS) if is_rtl() else s


def fa_mmss(sec: float) -> str:
    """Format elapsed/remaining time with Persian digits: "02:19", or "1:00:03"
    past one hour.

    The progress bar should show "how long it took and
    how long is left" — the user senses the time and, while testing, sees
    exactly how long it took. Negative/invalid input → "00:00".
    """
    try:
        s = int(max(0.0, float(sec)))
    except (TypeError, ValueError):
        s = 0
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return fa(f"{h}:{m:02d}:{s:02d}")
    return fa(f"{m:02d}:{s:02d}")


# Unify Persian/Arabic text for search: map the Arabic yeh/kaf variants to
# their Persian forms, strip kashida and ZWNJ. Without this, a search would
# miss folders spelled with Arabic letter variants of the same word.
_SEARCH_CHARS = str.maketrans({
    "ي": "ی", "ى": "ی",
    "ك": "ک",
    "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "ٱ": "ا", "أ": "ا", "إ": "ا",
})


def fa_norm(text) -> str:
    """Normalize text for search/matching (Arabic→Persian forms, strip
    kashida and ZWNJ, casefold)."""
    s = str(text or "").translate(_SEARCH_CHARS)
    # Note: the characters here are "semantic" (search rules), not
    # display text — they must never be translated (the stray T() wrapper was
    # reverted during manual review).
    return s.replace("ـ", "").replace("‌", " ").casefold()


def _winfix(p) -> str:
    """Windows: support paths longer than MAX_PATH (260 chars) via the \\\\?\\ prefix.

    Used only for os/open calls; stored data keeps the raw path. A UNC path
    (\\\\NAS\\share\\...) needs its proper \\\\?\\UNC\\ prefix, not simple
    concatenation — \\\\?\\NAS\\share is invalid and breaks walking/reading."""
    s = str(p)
    if sys.platform == "win32" and len(s) > 240 and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            # UNC: \\\\server\\share\\... → \\\\?\\UNC\\server\\share\\...
            return "\\\\?\\UNC\\" + s.lstrip("\\")
        return "\\\\?\\" + s
    return s


# Windows reparse-point constants — the stat module only defines them in the
# Windows build of Python (stat.FILE_ATTRIBUTE_REPARSE_POINT /
# IO_REPARSE_TAG_*), so they are written as literals to keep this file
# importable on Linux too.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400      # the st_file_attributes bit
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003   # junction — the symlink tag
                                           # (0xA000000C) is deliberately not counted


def _is_junction(path) -> bool:
    """Windows: junction/reparse-point — walking must not descend into it.

    os.path.isjunction exists only from Python 3.12; it is absent on 3.11, so
    we detect it ourselves with a Windows fallback via os.lstat: the
    REPARSE_POINT bit plus the MOUNT_POINT tag. The symlink tag (0xA000000C)
    is deliberately not counted as a junction — symlinks are already handled
    by os.walk's followlinks=False. Any error → False: this guard must never
    kill the scan (on Windows 3.11 an empty getattr call raised TypeError and
    the whole scan died)."""
    if sys.platform != "win32":
        return False
    try:
        isj = getattr(os.path, "isjunction", None)
        if callable(isj):                       # Python 3.12+ — fast API path
            return bool(isj(str(path)))
        # Python 3.11: detect via lstat (st_reparse_tag exists on Windows since 3.8)
        st = os.lstat(str(path))
        attrs = getattr(st, "st_file_attributes", 0)
        tag = getattr(st, "st_reparse_tag", 0)
        return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT) \
            and tag == _IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def natural_key(text):
    """Natural sort key: E2 sorts before E10."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", str(text))]


def _walkable_dirs(root: str, dirs: list) -> None:
    """The one shared os.walk dir filter : ignored names, dot/hidden
    folders and junctions are dropped IN PLACE. The old per-caller copies of
    this filter drifted apart — item_watchables/list_videos had no junction
    guard and could walk in circles on NTFS junction loops (endless scan,
    wrong watch counts, files from outside the library counted)."""
    dirs[:] = [d for d in dirs
               if d.lower() not in IGNORED_DIRS
               and not d.startswith(".")
               and not _is_junction(os.path.join(root, d))]


def _unwrap_longpath(p: str) -> str:
    r"""Map a \\?\-prefixed path back to its raw form.

    _winfix adds \\?\ (or \\?\UNC\) for walking only; output objects keep the
    raw path so string comparisons (exclude sets, stored refs, UI display)
    behave exactly as before."""
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[len("\\\\?\\UNC\\"):]
    if p.startswith("\\\\?\\"):
        return p[len("\\\\?\\"):]
    return p


def _walk_fixed(path, filter_dirs: bool = True):
    r"""os.walk with long-path support (_winfix) + the shared dir filter.

    The junction filter runs on the real (possibly \\?\-prefixed) walk root
    so junction detection also works beyond MAX_PATH; the yielded root is
    unwrapped back to the raw form so callers' Path objects and string
    comparisons (exclude sets, stored refs, UI display) keep their old
    shape. Used by every recursive walk  — previously only the
    main scan had _winfix and a >260-char title silently produced empty
    trees, while item_watchables/list_videos had no junction guard at all."""
    base = _winfix(path)
    for root, dirs, names in os.walk(base):
        if filter_dirs:
            _walkable_dirs(root, dirs)      # in place — the consumer sees the pruned list
        yield _unwrap_longpath(root), dirs, names


def human_size(n: int) -> str:
    if n >= 1 << 30:
        return T("{0:.1f} گیگابایت").format(n / (1 << 30))
    if n >= 1 << 20:
        return T("{0:.1f} مگابایت").format(n / (1 << 20))
    if n >= 1 << 10:
        return T("{0:.0f} کیلوبایت").format(n / (1 << 10))
    return T("{0} بایت").format(n)


def file_icon(path: Path) -> str:
    ext = archive_ext_of(path)
    if ext in VIDEO_EXTS:
        return "🎬"
    if ext in AUDIO_EXTS:
        return "🎵"
    if ext in IMAGE_EXTS_SET:
        return "🖼"
    if ext in ARCHIVE_EXTS:
        return "🗜"
    if ext in DOC_EXTS:
        return "📄"
    if ext in CODE_EXTS:
        return "💻"
    if ext in EXE_EXTS:
        return "⚙"
    return "📦"


@dataclass
class MediaItem:
    name: str
    path: Path
    category: str
    icon_path: Path | None = None
    file_count: int = 0
    video_count: int = 0
    size_bytes: int = 0
    modified: float = 0.0   # folder's last-modified time
    preview_video: Path | None = None  # video whose thumbnail replaces the poster (no icon)
    preview_pdf: Path | None = None    # PDF whose first page replaces the poster (no image/video)
    preview_archive: Path | None = None  # archive content replaces the poster (no image/video/PDF)
    loose_group: bool = False          # "root direct files" group as a single card
    loose_docs_only: bool = False      # root group for docs only: show only archives/PDF
    dom_type: str = ""                 # dominant content type: video/image/archive/doc/audio/other

    @property
    def size_text(self) -> str:
        return human_size(self.size_bytes)

    @property
    def info_text(self) -> str:
        # The cover is displayed but deliberately not counted: for a video
        # folder the meaningful number is "episodes", and the cover image is
        # not an episode; counting it would make "3 episodes" a lie. The cover
        # has *not* been removed from file_count (the "file count" sort and a
        # video-less folder show the same number) and it stays in the details
        # page list.
        if self.video_count:
            return T("{0} قسمت • {1}").format(fa(self.video_count), self.size_text)
        return T("{0} فایل • {1}").format(fa(self.file_count), self.size_text)

    @property
    def info_short(self) -> str:
        if self.video_count:
            return f"🎬 {fa(self.video_count)} · {self.size_text}"
        return f"📦 {fa(self.file_count)} · {self.size_text}"


# ---------------------------------------------------------------- desktop.ini

def _read_desktop_ini(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore").replace("\x00", "")
    for enc in ("utf-8-sig", "cp1256", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


def _resolve_icon_ref(ref: str, folder: Path) -> Path | None:
    ref = ref.strip().strip('"')
    if not ref:
        return None
    if "," in ref:  # strip a trailing index such as C:\x\icon.ico,0
        head, _, tail = ref.rpartition(",")
        if tail.strip().lstrip("-").isdigit():
            ref = head
    ref = os.path.expandvars(ref.strip().strip('"'))
    p = Path(ref)
    # Security: only a relative reference (resolved INSIDE the
    # title's own folder) is honored. A crafted desktop.ini pointing at
    # \\attacker\share\x.ico (absolute or via %ENV%) used to make a mere
    # scan of a downloaded folder open an outbound SMB connection — leaking
    # NTLMv2 credentials on Windows. Absolute/UNC/parent-escaping references
    # are ignored.
    if p.is_absolute() or str(p).startswith(("\\\\", "/")):
        return None
    candidates = [folder / ref]
    for c in candidates:
        try:
            cc = c.resolve()
            if not cc.is_relative_to(folder.resolve()):
                continue               # ".." escaped the folder — ignore
            if cc.is_file() and cc.suffix.lower() in ICON_EXTS:
                return c
        except OSError:
            continue
    return None


def find_desktop_ini_icon(folder: Path) -> Path | None:
    ini = folder / "desktop.ini"
    if not ini.is_file():
        return None
    try:
        text = _read_desktop_ini(ini)
    except OSError:
        return None
    icon_resource = icon_file = None
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith(("[", ";")):
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key == "iconresource":
            icon_resource = value
        elif key == "iconfile":
            icon_file = value
    for ref in (icon_resource, icon_file):
        p = _resolve_icon_ref(ref or "", folder)
        if p:
            return p
    return None


def find_loose_icon(folder: Path) -> Path | None:
    try:
        icos = sorted(p for p in folder.glob("*.ico") if p.is_file())
    except OSError:
        return None
    if not icos:
        return None
    low = folder.name.lower()
    for key in (low, "icon", "folder", "poster", "cover"):
        for ico in icos:
            if key and key in ico.stem.lower():
                return ico
    return icos[0]


def find_poster_image(folder: Path) -> Path | None:
    for stem in POSTER_NAMES:
        for ext in POSTER_EXTS:
            p = folder / (stem + ext)
            if p.is_file():
                return p
    return None


def find_any_image(folder: Path, deep: bool = False, _breadcrumb: set | None = None) -> Path | None:
    """Any image (not just poster/cover): inside the folder itself, and one level
    down in deep mode.

    For icon-less folders: the first available picture is displayed as the
    poster ("if there's a picture in the folder, show it").
    """
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_file() and e.suffix.lower() in IMAGE_EXTS_SET:
                return e
            if deep and e.is_dir() and e.name.lower() not in IGNORED_DIRS:
                subs.append(e)
        except OSError:
            continue
    if deep:  # one level only — a deep search is costly and unwanted
        for sub in subs[:24]:
            img = find_any_image(sub, deep=False)
            if img:
                return img
    return None


def find_preview_video(folder: Path) -> Path | None:
    """First video for thumbnail building when no image exists (folder
    itself + one subfolder level)."""
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        try:
            if e.is_file() and e.suffix.lower() in VIDEO_EXTS:
                return e
            if e.is_dir() and e.name.lower() not in IGNORED_DIRS and not e.name.startswith("."):
                subs.append(e)
        except OSError:
            continue
    for sub in subs[:24]:
        try:
            vids = sorted((p for p in sub.iterdir()
                           if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
                          key=lambda p: natural_key(p.name))
        except OSError:
            continue
        if vids:
            return vids[0]
    return None


def find_loose_video_files(folder: Path) -> list[Path]:
    """A folder's direct (non-recursive) video files, in natural order."""
    try:
        out = [p for p in folder.iterdir()
               if p.is_file() and not p.name.startswith(".")
               and p.suffix.lower() in VIDEO_EXTS]
    except OSError:
        return []
    out.sort(key=lambda p: natural_key(p.name))
    return out


def find_preview_pdf(folder: Path) -> Path | None:
    """First PDF for thumbnail building (folder itself + one subfolder
    level) — before the archive fallback."""
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        try:
            if e.is_file() and e.suffix.lower() == ".pdf":
                return e
            if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in IGNORED_DIRS:
                subs.append(e)
        except OSError:
            continue
    for sub in subs[:24]:
        try:
            pdfs = sorted((p for p in sub.iterdir()
                           if p.is_file() and p.suffix.lower() == ".pdf"),
                          key=lambda p: natural_key(p.name))
        except OSError:
            continue
        if pdfs:
            return pdfs[0]
    return None


# Archive readability preference: the ZIP/TAR family is native to Python;
# RAR/7z need a tool/package (the bundled 7za.exe reads all of them — no user
# setup required)
_ARC_PREF = {".zip": 0, ".cbz": 0, ".epub": 0, ".tar": 1, ".gz": 1, ".tgz": 1,
             ".tbz2": 1, ".txz": 1, ".rar": 2, ".cbr": 2, ".7z": 3, ".cb7": 3,
             ".cab": 3, ".iso": 4,
             # compound tar bundles — the same native tier as plain .tar.gz
             ".tar.gz": 1, ".tar.bz2": 1, ".tar.xz": 1, ".tar.zst": 1,
             ".tar.lz4": 1}


def _arc_entry(folder: Path) -> list[Path]:
    try:
        return [p for p in folder.iterdir()
                if p.is_file() and not p.name.startswith(".")
                and archive_ext_of(p) in _ARC_PREF]
    except OSError:
        return []


def find_preview_archive(folder: Path) -> Path | None:
    """First usable archive for thumbnail building — for folders with neither
    pictures nor videos (folder itself + one subfolder level; like the rest of
    the fallback chain)."""
    cands = _arc_entry(folder)
    if not cands:
        subs: list[Path] = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        except OSError:
            return None
        for e in entries:
            try:
                if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in IGNORED_DIRS:
                    subs.append(e)
            except OSError:
                continue
        for sub in subs[:24]:
            cands.extend(_arc_entry(sub))
            if cands:
                break
    if not cands:
        return None
    cands.sort(key=lambda p: (_ARC_PREF[archive_ext_of(p)], natural_key(p.name)))
    return cands[0]


def find_icon(folder: Path, include_image_fallback: bool = True) -> Path | None:
    """Poster priority chain: desktop.ini → any .ico → poster/cover → any image (depth 1)."""
    icon = find_desktop_ini_icon(folder) or find_loose_icon(folder)
    if icon is None and include_image_fallback:
        icon = (find_poster_image(folder)
                or find_any_image(folder, deep=True))
    return icon


# ------------------------------------------------------------- stats and tree

def file_type_group(name) -> str:
    """A file's broad category from its extension — for "by file type" sorting.

    A file's name can be in any language; everything keys off the
    "extension" only — the name itself plays no role.
    """
    ext = archive_ext_of(name)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS_SET:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in DOC_EXTS:
        return "doc"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


# Category order for the "file type / content type" option — both the main page and details
TYPE_ORDER = ("video", "image", "archive", "doc", "audio", "other")


def gather_stats(folder: Path) -> tuple[int, int, int, dict]:
    """(file count, video count, size, per-type counts) — the type counts feed
    the "dominant content type" used by main-page sorting, and since
    they come from the folder's usual walk the extra cost is nearly zero.

    The main page once said 1 file while the details page said 0 — the
    file filter now exactly matches get_file_tree's: dot files and Windows
    system files (desktop.ini/thumbs.db) are excluded from the count and size
    so the card counter and the details counter always follow one rule. (The
    cover image is no longer removed from the details list, so both report
    the same number.)"""
    files = videos = size = 0
    type_counts = {"video": 0, "image": 0, "archive": 0,
                   "doc": 0, "audio": 0, "other": 0}
    # _winfix: very long Windows paths (>240) are read via the \\?\ prefix too
    for _root, dirs, names in os.walk(_winfix(folder)):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS
                   and not d.startswith(".")
                   and not _is_junction(os.path.join(_root, d))]
        for name in names:
            if name.startswith(".") or name.lower() in HIDDEN_FILES:
                continue                       # in sync with get_file_tree
            files += 1
            try:
                size += (Path(_root) / name).lstat().st_size
            except OSError:
                pass
            ext = archive_ext_of(name)
            type_counts[file_type_group(name)] += 1
            if ext in VIDEO_EXTS:
                videos += 1
    return files, videos, size, type_counts


def dominant_type(type_counts: dict) -> str:
    """Dominant type from the counts; ties resolve by TYPE_ORDER."""
    best, best_n = "", 0
    for t in TYPE_ORDER:
        n = int((type_counts or {}).get(t, 0) or 0)
        if n > best_n:
            best, best_n = t, n
    return best or "other"


def _direct_files(path: Path, exclude: set[str], only_exts=None) -> list[Path]:
    """A folder's direct (non-recursive) files, without hidden/system ones —
    natural order.

    only_exts: when given, only files with those extensions (e.g.
    LOOSE_DOC_EXTS for the root docs group — so videos that already have their
    own card are not duplicated).
    """
    try:
        out = [
            p for p in Path(path).iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.name.lower() not in HIDDEN_FILES
            and str(p) not in exclude
            and (only_exts is None or p.suffix.lower() in only_exts)
        ]
    except OSError:
        return []
    out.sort(key=lambda p: natural_key(p.name))
    return out


def get_file_tree(path: Path, exclude: set[Path] | None = None, loose: bool = False,
                  only_exts=None):
    """Return a title's files as sections.

    Output: a list of {"title": str, "files": [Path, ...]} with files and
    sections in natural order. Each nested folder becomes its own section.
    loose=True → direct files only (for the "root loose files" group).
    only_exts → in loose mode, only these extensions (docs group: archives/PDF).
    """
    path = Path(path)
    exclude = {str(p) for p in (exclude or set())}
    if path.is_file():
        return [{"title": T("فایل"), "files": [path]}]   # display title of the group (not a key)
    if loose:
        return [{"title": T("فایل‌ها"), "files": _direct_files(path, exclude, only_exts)}]

    sections: list[dict] = []
    for root, dirs, names in _walk_fixed(path):
        dirs[:] = sorted(dirs, key=natural_key)
        files = [
            Path(root) / n for n in names
            if n.lower() not in HIDDEN_FILES
            and str(Path(root) / n) not in exclude
            and not n.startswith(".")
        ]
        files.sort(key=lambda p: natural_key(p.name))
        if not files:
            continue
        rel = os.path.relpath(root, path)
        title = T("فایل‌های اصلی") if rel == "." else rel
        sections.append({"title": title, "files": files})

    sections.sort(key=lambda s: (s["title"] != T("فایل‌های اصلی"), natural_key(s["title"])))
    return sections


def thumb_manifest(path, loose: bool = False):
    """A title's content fingerprint + its flat file list (smart skip).

    Contract: "when the backup file is complete it must not re-check —
    only when a file has actually changed". The manifest is a map
    {relative path: [size, mtime_sec]} (the same pattern as the durations
    manifest). It is compared against the stored manifest; equality means no
    file was added/removed/edited and the prebuilder skips without any extra
    work.

    On Windows, os.scandir provides stat data as part of enumeration, so this
    step costs only "listing the folders" — no extra disk reads; the second
    output (the flat list) is kept for the same run so get_file_tree does not
    run again. Filter rules exactly match get_file_tree: skip IGNORED_DIRS/
    hidden/dot folders and HIDDEN_FILES/dot files.
    """
    path = Path(path)
    man: dict = {}
    files: list = []
    if path.is_file():
        try:
            st = path.stat()
            man[path.name] = [st.st_size, int(st.st_mtime_ns)]
        except OSError:
            pass
        return man, [path]

    def _note(e, rel: str):
        if e.name.startswith(".") or e.name.lower() in HIDDEN_FILES:
            return
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            return
        man[rel + e.name] = [st.st_size, int(st.st_mtime_ns)]
        files.append(Path(e.path))

    if loose:
        # The "root direct files" group — same-level files only (no subfolders)
        try:
            with os.scandir(path) as it:
                ents = sorted(it, key=lambda e: natural_key(e.name))
        except OSError:
            return man, files
        for e in ents:
            try:
                if e.is_file(follow_symlinks=False):
                    _note(e, "")
            except OSError:
                continue
        return man, files

    def rec(d: str, rel: str):
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: natural_key(e.name))
        except OSError:
            return
        for e in ents:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in IGNORED_DIRS:
                        continue
                    if _is_junction(e.path):
                        continue
                    rec(e.path, rel + e.name + "/")
                elif e.is_file(follow_symlinks=False):
                    _note(e, rel)
            except OSError:
                continue

    rec(str(path), "")
    return man, files


def get_file_hierarchy(path: Path, exclude: set[Path] | None = None, loose: bool = False,
                       only_exts=None):
    """Hierarchical file tree (for the collapsible tree view).

    Output: {"path": Path, "dirs": [node, ...], "files": [Path, ...]}
    loose=True → direct files only, no subfolders (the root files group).
    only_exts → in loose mode, only these extensions (docs group: archives/PDF).
    """
    path = Path(path)
    exclude = {str(p) for p in (exclude or set())}
    if path.is_file():
        return {"path": path.parent, "dirs": [], "files": [path]}
    if loose:
        return {"path": path, "dirs": [], "files": _direct_files(path, exclude, only_exts)}

    def build(p: Path) -> dict:
        node = {"path": p, "dirs": [], "files": []}
        try:
            kids = list(p.iterdir())
        except OSError:
            return node
        dirs, files = [], []
        for c in kids:
            n = c.name
            if n.startswith(".") or n.lower() in HIDDEN_FILES or str(c) in exclude:
                continue
            if c.is_dir():
                # Symlinks are skipped like junctions: the main scan walks
                # with followlinks=False, so the details tree must not
                # either — a folder symlink would escape the collection
                # root (files from outside show up in the tree) and a
                # symlink cycle would recurse until ELOOP kills the tree.
                if (n.lower() not in IGNORED_DIRS and not _is_junction(c)
                        and not c.is_symlink()):
                    dirs.append(c)
            else:
                files.append(c)
        dirs.sort(key=lambda c: natural_key(c.name))
        files.sort(key=lambda c: natural_key(c.name))
        node["dirs"] = [build(d) for d in dirs]
        node["files"] = files
        return node

    return build(path)


def count_hierarchy_files(node: dict) -> int:
    return len(node["files"]) + sum(count_hierarchy_files(d) for d in node["dirs"])


def item_videos(item) -> list[Path]:
    """A title's videos, honoring loose_group (the root direct-files group)."""
    return list_videos(item.path, loose=getattr(item, "loose_group", False))


def item_watchables(item) -> list[Path]:
    """A title's "consumable" files — the basis of the smart "fully watched"
    state.

    Rule: if the title has videos, those are the criterion
    (subtitles and side documents do not matter); otherwise every consumable
    file — book/document, archive (manga), image, audio, program — counts
    toward the total, while subtitles/sidecars do not. With loose_group only
    direct files are counted (in step with list_videos).
    """
    vids = item_videos(item)
    if vids or (getattr(item, "video_count", 0) or 0) > 0:
        return vids
    path = Path(item.path)

    def _ok48(p: Path) -> bool:
        return (p.is_file() and not p.name.startswith(".")
                and archive_ext_of(p) in WATCHABLE_EXTS)

    if path.is_file():
        return [path] if _ok48(path) else []
    if getattr(item, "loose_group", False):
        try:
            out = [p for p in path.iterdir() if _ok48(p)]
        except OSError:
            return []
        out.sort(key=lambda p: natural_key(p.name))
        return out
    out: list[Path] = []
    for root, dirs, names in _walk_fixed(path):
        for n in names:
            if not n.startswith(".") and archive_ext_of(n) in WATCHABLE_EXTS:
                out.append(Path(root) / n)
    out.sort(key=lambda p: natural_key(p.name))
    return out


def list_videos(path, loose: bool = False) -> list[Path]:
    """All of a title's video files (recursive, without hidden/system folders).

    loose=True → direct videos only (for the root loose-files group).
    """
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTS else []
    if loose:
        return find_loose_video_files(path)
    out: list[Path] = []
    for root, dirs, names in _walk_fixed(path):
        for n in names:
            if not n.startswith(".") and Path(n).suffix.lower() in VIDEO_EXTS:
                out.append(Path(root) / n)
    out.sort(key=lambda p: natural_key(p.name))
    return out


# ---------------------------------------------------------------- main scan

def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def scan_roots(roots, include_image_fallback=True, progress=None, counts=None,
               status=None):
    """roots: a list of {"path": ..., "category": ...} dictionaries.

    Each direct subfolder of a root = one title. Loose video files at the root
    itself (the common case for movies) are registered as a separate title
    too.

    counts(done, total): a light initial counting pass (reading each
    root's entries only, no poster/time probing) so the scan progress bar is
    "real" and shows an "x of y" number (previously it just
    spun in an indeterminate mode).

    status: an optional {"ok": [...], "failed": [...]} dictionary that
    records each root's path after a real read attempt — main cleans the
    recents of ok roots only, so a temporarily disconnected drive keeps its
    history (partial multi-root scan).
    """
    if status is not None:
        try:
            status.setdefault("ok", [])
            status.setdefault("failed", [])
        except Exception as _exc_dbg:
            dbg_log(f"scan_roots failed: {type(_exc_dbg).__name__}")
    items: list[MediaItem] = []

    def say(msg):
        if progress:
            progress(msg)

    total_titles = 0
    _seen_count_paths: set[str] = set()      # nested roots must not be counted twice
    if counts:
        try:
            for root in roots:
                rp = Path(os.path.expandvars(str(root.get("path", "")).strip()))
                if not rp.is_dir():
                    continue
                try:
                    ch = list(rp.iterdir())
                except OSError:
                    continue
                subs = [c for c in ch
                        if c.is_dir() and not c.name.startswith(".")
                        and c.name.lower() not in IGNORED_DIRS]
                lvid = [c for c in ch
                        if c.is_file() and c.suffix.lower() in VIDEO_EXTS]
                ldoc = [c for c in ch
                        if c.is_file() and not c.name.startswith(".")
                        and c.suffix.lower() in LOOSE_DOC_EXTS]
                # If both D:\Movies and D:\Movies\Marvel are roots, the
                # Marvel card appears under both — progress counting must not
                # double-count it either
                def _fresh(paths):
                    out2 = []
                    for c in paths:
                        k = os.path.normcase(os.path.abspath(str(c)))
                        if k in _seen_count_paths:
                            continue
                        _seen_count_paths.add(k)
                        out2.append(c)
                    return out2
                subs, lvid, ldoc = _fresh(subs), _fresh(lvid), _fresh(ldoc)
                if subs:
                    total_titles += len(subs)
                    if lvid or ldoc:
                        total_titles += 1          # the root direct-files group card
                else:
                    total_titles += len(lvid)
                    if ldoc:
                        total_titles += 1          # the direct doc/archive group card
        except Exception as _exc_dbg:
            dbg_log(f"scan_roots failed: {type(_exc_dbg).__name__}")
            total_titles = 0
    done_titles = [0]

    def tick():
        done_titles[0] += 1
        if counts:
            try:
                counts(done_titles[0], max(total_titles, done_titles[0]))
            except Exception as _exc_dbg:
                dbg_log(f"scan_roots.tick failed: {type(_exc_dbg).__name__}")
                pass

    for root in roots:
        rpath = Path(os.path.expandvars(str(root.get("path", "")).strip()))
        category = str(root.get("category", "")).strip() or rpath.name
        if not rpath.is_dir():
            say(T("⚠ مسیر پیدا نشد: {0}").format(rpath))
            if status is not None:
                status["failed"].append(str(rpath))
            continue
        say(T("اسکن {0} …").format(rpath))

        try:
            children = sorted(rpath.iterdir(), key=lambda p: natural_key(p.name))
        except OSError as e:
            say(T("⚠ خطا در خواندن {0}: {1}").format(rpath, e))
            if status is not None:
                status["failed"].append(str(rpath))
            continue
        if status is not None:
            status["ok"].append(str(rpath))

        subdirs = [
            c for c in children
            if c.is_dir() and not c.name.startswith(".")
            and c.name.lower() not in IGNORED_DIRS
        ]

        for child in subdirs:  # each folder = one title
            # Poster chain (fixed preference order):
            # icon/image → video inside the folder → PDF inside the folder →
            # archive content. All "candidates" are always filled (not only
            # when the previous one is missing) so that at thumbnail-build time
            # a broken icon/video with no thumbnail lets the chain move on to
            # the next step automatically (a broken icon used to mean no poster at all).
            # Note: before this fix, when an icon existed the other candidates
            # were recorded as None and a broken icon had no replacement —
            # i.e. no real "fall-through" ever happened.
            icon = find_icon(child, include_image_fallback)
            preview = find_preview_video(child)
            prevpdf = find_preview_pdf(child)
            prevarc = find_preview_archive(child)
            files, videos, size, tcounts = gather_stats(child)
            items.append(MediaItem(
                name=child.name, path=child, category=category,
                icon_path=icon, file_count=files, video_count=videos,
                size_bytes=size, modified=_mtime(child),
                preview_video=preview, preview_pdf=prevpdf, preview_archive=prevarc,
                dom_type=dominant_type(tcounts),
))
            tick()
            say(f"✔ {category} / {child.name}")

        # The root's own loose direct files
        loose_vids = [f for f in children
                      if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
        loose_imgs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() in IMAGE_EXTS_SET]
        loose_imgs.sort(key=lambda p: natural_key(p.name))
        # Root direct docs: archives and PDFs
        loose_pdfs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() == ".pdf"]
        loose_arcs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() in _ARC_PREF]
        loose_arcs.sort(key=lambda p: (_ARC_PREF[p.suffix.lower()],
                                       natural_key(p.name)))
        loose_docs = loose_pdfs + loose_arcs

        # All root direct files (image, video, doc) in one group card
        all_loose = loose_vids + loose_imgs + loose_docs
        all_loose.sort(key=lambda p: natural_key(p.name))

        def _root_icon(root: Path):
            # Parameter instead of closing over the loop variable: the closure
            # only worked because it was invoked immediately — confusing for
            # the reader.
            return (find_desktop_ini_icon(root) or find_loose_icon(root)
                    or (find_poster_image(root) if include_image_fallback else None))

        if all_loose:
            # The root = one group card named after itself (like subfolders),
            # covering all direct files: image, video, archive, PDF
            dfiles = len(all_loose)
            dvids = len(loose_vids)
            dsize = 0
            dcounts = {t: 0 for t in TYPE_ORDER}
            for f in all_loose:
                try:
                    dsize += f.lstat().st_size
                except OSError:
                    pass
                dcounts[file_type_group(f)] += 1
            icon = _root_icon(rpath)
            # A root direct image is a real cover: when no
            # explicit icon (desktop.ini/ico/poster) exists, the root's first
            # image goes to icon_path just like ordinary folders (find_icon
            # with image_fallback). Previously the same image only landed in
            # preview_video — where the video thumbnail chain rejects an image
            # extension — and the card stayed on a lettered placeholder.
            if icon is None and loose_imgs:
                icon = loose_imgs[0]
            preview = loose_vids[0] if loose_vids else None
            prevpdf = loose_pdfs[0] if loose_pdfs else None
            prevarc = loose_arcs[0] if loose_arcs else None
            # No video but an image exists → the first image becomes preview
            # (only when that image did not become the icon — removes pointless
            # duplicate thumbnail work)
            if not preview and loose_imgs and icon is not loose_imgs[0]:
                preview = loose_imgs[0]
            items.append(MediaItem(
                name=rpath.name, path=rpath, category=category,
                icon_path=icon, file_count=dfiles, video_count=dvids,
                size_bytes=dsize, modified=_mtime(rpath),
                preview_video=preview, preview_pdf=prevpdf,
                preview_archive=prevarc, loose_group=True,
                dom_type=dominant_type(dcounts),
))
            tick()
            from i18n import disp_cat as _dc45
            say(T("✔ {0} / {1} (گروه {2} فایل مستقیم)").format(_dc45(category), rpath.name, fa(dfiles)))

    items.sort(key=lambda i: (i.category, natural_key(i.name)))
    # Rooting both D:\Movies and D:\Movies\Marvel shows Marvel twice (two
    # independent cards sharing metadata). Dedupe by
    # normcase+abspath (not resolve — the same Windows/network performance
    # convention).
    seen: set[str] = set()
    out: list[MediaItem] = []
    for it in items:
        key = os.path.normcase(os.path.abspath(str(it.path)))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
