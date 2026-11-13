# Features — full list

Language: [English](FEATURES.md) · [فارسی](FEATURES.fa.md)

## Table of contents

- [Library & scanning](#library--scanning)
- [Posters & thumbnails](#posters--thumbnails)
- [Watch tracking & players](#watch-tracking--players)
- [Organization & personalization](#organization--personalization)
- [Backup & data safety](#backup--data-safety)
- [Performance](#performance)
- [Interface](#interface)
- [Portable & storage](#portable--storage)
- [Legacy names & compatibility](#legacy-names--compatibility)
- [Future work](#future-work)

## Library & scanning

- One folder = one title: every immediate subfolder inside a category root is
  shown as a separate library item — movies, series, anime, games, courses,
  books, magazines, comics — anything you keep as a folder
- Category roots with custom colors; sub-categories show up as a tree in the sidebar
- Loose files sitting directly inside a root become standalone titles automatically
- Smart scanner reads the folder icon (`desktop.ini` → `icon.ico`) as the poster,
  falling back to ordinary images inside the folder
- Custom display name and custom poster per title — rename or re-cover a title
  without touching anything on disk
- Per-title file/episode counts, total duration and size, multi-line info on cards
- Missing-path manager: titles whose drive is not attached stay in the library and
  can be re-pointed or removed from a single dialog
- Long-path (`\\?\`) support for deep Windows paths

## Posters & thumbnails

- Cover chain per title — a custom cover you set inside the app wins; otherwise
  the Windows folder icon (`desktop.ini` → `icon.ico`) is used when present,
  then images inside the folder, then generated previews (video, PDF, archive)
  and finally a clean file-type card — no scraping, no lookups, and a cover is
  never required
- Video thumbnails via the Windows shell handler; the optional bundled ffmpeg
  (only in the `/with-ffmpeg` build) is the fallback
- Archive thumbnails from inside 7z/RAR/ZIP (bundled `7za.exe`, py7zr, rarfile plus
  any installed UnRAR/WinRAR)
- First-page preview for PDFs (pypdfium2), previews for images, and a vector
  placeholder for unknown types
- Local thumbnail cache next to the app (path and size cap configurable), written
  as WebP (quality 84) and decoded natively by Qt — at thumbnail sizes (160–512 px)
  WebP is smaller *and* faster than JPEG XL, which needed a native plugin + six
  DLLs and only ever beat PNG; legacy `.png` cache files are still read, `.jxl`
  cache files are treated as missing and silently rebuilt as WebP
- Cache resolution follows the Windows display scale (`devicePixelRatio`):
  256 px at 100 %, 384 px at 150 %, 512 px at 200 % — the card is 186×248 logical
  points, so a fixed 256 px cache looked soft on scaled displays
- Manifest-based rebuild skipping: unchanged files are never re-rendered, even
  after a full backup restore; capped retries for broken archives
- Optional background prebuild after scan, running at low priority so the UI stays
  responsive

## Watch tracking & players

- Watch state (Unwatched / In progress / Watched) for **all** categories — video,
  books, compressed manga (zip/cbz), images and documents; subtitles never count as
  "fully watched"
- Read progress for documents (page number)
- Player-aware tracking and resume: mpv (precise positions), PotPlayer (real
  position read every 1.5 s), VLC (resume via `--start-time` + estimated tracking);
  the Windows default player can't be tracked and the app tells you so
- Playback resumes from the last position
- Per-episode durations, so a series total is never wrong
- "Mark all as watched" for a whole title

## Organization & personalization

- Personal 1–5 star ratings, shown on cards and the details page
- Favorites with a dedicated filter
- Custom watch-state profiles per category — keep the default watch/read states or
  define your own (e.g. "Workout: done / in progress / not done")
- Category openers: choose which program opens each file type in each category
- A description/synopsis field per title, with a dedicated editor
- Search across name, path and description — normalized for Persian (Arabic ي/ك,
  half-space and diacritics don't affect results)
- Eight sort orders (name, content type, duration, size, episode count, file count,
  date modified, rating), each ascending or descending, remembered between runs
- Watch-state filter in the header, independent of content type
- Recents sidebar: the last 15 opened titles, one click away
- "What to watch tonight?" lucky pick and per-category stats

## Backup & data safety

- Export/import everything as a single `.mcbak` file (ZIP-based)
- Automatic backup: created the moment you enable it, then rewritten only on real
  changes — atomic, in the background, never blocks the UI; target path configurable
- Non-destructive merge on import; security snapshots before risky operations; roots
  that moved between drives are auto-rewritten after a merge
- Config sanitization on import: opener programs from foreign backups are never
  imported (security measure)
- Self-healing startup: a corrupted database never crashes the app — it starts
  fresh and quarantines the broken file
- "Full wipe & reset" in settings cleans settings, database, cache and leftovers,
  then restarts the app

## Performance

- Performance modes: automatic (disk-type detection), max speed, cautious
- Parallel thumbnail rendering with hardware-aware worker counts (SSD vs spinning
  disk detected automatically)
- Cards are prepared before they appear and swapped in one step — browsing stays
  smooth on libraries with thousands of titles
- Deferred card rendering while scrolling

## Interface

- Complete English and Persian UI with automatic RTL/LTR switching; English is the
  default for new installs and your choice is remembered
- Eight themes: Dark Blue, Dark Gray, Pure Black (AMOLED), Light, Forest, Sunset, Ocean, Paper — all with the same tested contrast contract (WCAG luminance)
- Splash screen, tree and grid detail views, category filters in the header
- Keyboard shortcuts on the details page: `W` watched, `F` favorite, `Space` play,
  `Alt+←/→` history; mouse back/forward buttons work like a browser
- Vazirmatn font bundled — no system font requirements

## Portable & storage

- Portable package builder (`BUILD.bat`): zero-prerequisite Windows 10/11 x64 build,
  one-dir layout so the LGPL Qt libraries stay replaceable
- Everything (config, database, cache, backups) lives next to the executable —
  nothing in `%APPDATA%`, nothing in the registry; works well on external drives
- SQLite (WAL) storage with automatic migration from the old JSON format
- Debug and error logs (`folderdex-debug.log` / `folderdex-error.log`) next to
  the app
- Run from source with Python 3.11+ (`run.bat` / `debug.bat`)

## Legacy names & compatibility

FolderDex was released as CoverShelf («قفسه‌ی کاور»), which itself was renamed
from an older internal name ("Media Cataloger"). A few names from those eras
are still in use **on purpose** — renaming them would break existing
installations and old backups. Nothing here is new data; these are kept
so that files written by older versions keep working:

- `.mcbak` — the single-file extension of the automatic backup
  (`backups/auto-backup.mcbak`), unchanged since the backup format switched to
  one file
- `MediaCataloger` — the old global data folder name
  (`%LOCALAPPDATA%\MediaCataloger` of internal 0.34.0–0.38.0). Current versions keep
  everything next to the app; the legacy folder is only **read** for the
  one-time migration and is removed once it is empty
- `media-cataloger-debug.log` / `media-cataloger-error.log` — the oldest log
  file names, still cleaned up alongside the previous-era `covershelf-*.log`
  files and the current `folderdex-debug.log` / `folderdex-error.log`
- `covershelf-debug.log` / `covershelf-error.log` — the previous brand's log
  names (kept for "full wipe & reset" cleanups and `diag.bat` collection); the
  read-only cache fallback folder is now `%TEMP%\FolderDex-cache`
- `.mc_import_*` — temporary names used while importing a `.mcbak` backup:
  the `.mc_import_pending` staging folder, the `._mc_import` flag file, and
  the `.mc_import_bad-<timestamp>` quarantines for unhealthy backups

## Future work

- Writing the real Windows folder icon for a custom cover (`desktop.ini` +
  `icon.ico` + Hidden/System attributes + a shell refresh notification) is
  deliberately **out of scope** for now: it would conflict with the promise
  that nothing ever touches the user's files, and it needs an opt-in setting
  plus Windows-only testing. Custom covers live in `.covers/` inside the app
  folder instead.
