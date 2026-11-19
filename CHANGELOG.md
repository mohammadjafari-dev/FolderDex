# Changelog

All notable changes to FolderDex are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**فارسی:** [CHANGELOG.fa.md](CHANGELOG.fa.md)
**Official repository:** [mohammadjafari-dev/FolderDex](https://github.com/mohammadjafari-dev/FolderDex)

## [1.0.0] - 2026-11-19

The first stable release — everything below is what FolderDex 1.0.0 is.
Fixes found before publication are appended as one-line bullets under this
section; after publication every fix gets its own version number
(Semantic Versioning: 1.0.1, 1.1.0, ...).

### Added

- A portable, offline library on multiple drives — movie, series, anime,
  game, book and course folders (plus loose files) become browsable title
  cards; everything stays next to the app, with no installer and no internet
  at run time
- Covers and automatic previews — video, archive, PDF and image thumbnails
  in a WebP cache next to the app; custom folder covers (animated GIF/WebP
  included); only changed files are rebuilt
- Watch/read tracking with resume — mpv, PotPlayer and VLC playback resumes
  exactly where you left it
- Ratings, favorites, watch-state filters, the recents list and the
  "what to watch tonight" pick
- Editable per-category colors that repaint the sidebar, cards and the
  details badge
- The missing-path manager — after merging a backup, folders that moved to
  another drive are re-pointed automatically; missing items can be re-pathed
  or removed, and unplugged drives are skipped
- Automatic single-file `.mcbak` backups — written only when something
  actually changed, with a non-destructive merge restore and a safety
  snapshot before any full replace
- A bilingual English/Persian interface (full RTL) with eight themes, and
  Persian-localized standard buttons
- Library stats per category — real watched/read/marked percentages with
  in-progress counts
- Smooth display on large libraries — cards are prepared before they are
  shown and performance modes follow the disk type (SSD or HDD)

### Fixed

- The smart "what to watch tonight" pick died silently on a fragile module
  import — the dialog now reads the watched total straight from the library
  store, and a smoke test constructs the dialog on every test run
- Stopping the poster worker also invalidates in-flight jobs now, so closing
  the app right after a long video-watching session no longer lingers while
  stale thumbnail work drains
- The startup splash screen ignored the selected theme — it now wears the
  active theme in all eight palettes (panel, border, texts and the progress
  track) and floats as a rounded card on a translucent window, so light
  themes no longer show a white edge around it
- A dedicated FolderDex logo — a folder holding a title-index grid with a
  play marker (vector source: assets/logo.svg) — replaces the placeholder
  icon; used as the app icon, in the splash screen, for the EXE icon and
  in the README headers
- Brand icon refined — the play marker is gone (it read as a video player),
  the folder is larger and one grid tile now carries the amber "in-progress"
  accent used across the app; the vector source and the PNG are finally the
  same design and render clean with no gray edge ring
- The rectangular halo around the rounded logo in the sidebar and on the
  splash card — the generic widget background painted behind the transparent
  label corners; both logo labels are now explicitly transparent

### Security

- Backup restore hardened: ZIP-bomb and path-traversal checks, no planted
  player executables, and an exact report of which sensitive parts were
  skipped
- Archive extraction is capped during the write and isolated in a private
  staging folder; malicious member paths and UNC escapes are blocked
- Scans skip symlinks and junctions; `desktop.ini` icon paths must stay
  inside the title's own folder (no outbound network connections)
- Win64 memory-safety fix in the ctypes/GDI layer — pointer-sized structs,
  correct handle release and 64-bit-safe return types
- Dependency floors pinned to the security-fix versions and verified at
  launch

## Pre-1.0 development summary

1.0.0 is the first published version and this repository intentionally
starts as a single commit, so the road that led here is summarized in this
section instead of being kept as commit history.

- The project began as a personal folder explorer and grew, across dozens
  of numbered review rounds, into a complete offline media catalog.
- Core engine: the folder scanner, the SQLite store, the WebP thumbnail
  cache and automatic covers/previews for video, PDF, archive and image
  files.
- Interface: title cards with per-category colors, eight themes, the fully
  bilingual English/Persian UI (RTL), the details panel and per-file
  episode progress.
- Tracking and curation: watch/read state, ratings, favorites, watch-state
  filters, the recents list, per-category stats and the "what to watch
  tonight" pick.
- Safety hardening: ZIP-bomb and path-traversal checks in backup restore,
  capped and staged archive extraction, symlink/junction-safe scans, the
  Win64 ctypes memory-safety fix and pinned dependency security floors.
- Packaging: the one-click BUILD.bat (PyInstaller, bundled 7-Zip, optional
  ffmpeg) with mandatory license verification and an exact source offer.
- Quality: a 370-test suite with GitHub Actions CI on Windows (Python
  3.11-3.13) and Ubuntu, including a repository-integrity suite; the tree
  was frozen at 1.0.0 before publication.
- Branding and docs: the bilingual README/docs suite, six README
  screenshots, the dedicated vector logo and app icon, and the
  social-preview image.
