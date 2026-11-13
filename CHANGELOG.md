# Changelog

All notable changes to FolderDex are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**فارسی:** [CHANGELOG.fa.md](CHANGELOG.fa.md)
**Internal development history (Persian only):** [docs/HISTORY-pre-1.0.fa.md](docs/HISTORY-pre-1.0.fa.md)
**Official repository:** [mohammadjafari-dev/FolderDex](https://github.com/mohammadjafari-dev/FolderDex)

Before the first stable release this file stays one short summary: the single
dated section below describes the current development state — not one section
per internal build. Detailed per-version entries begin with the first stable
release.

## [0.54.0] - 2026-11-12

### Summary

- The app is renamed to **FolderDex** (formerly CoverShelf) — the display
  name, window titles, portable executable and package, log files and the
  official repository all follow the new brand; legacy names keep working
- Security hardening — restoring a stranger's backup can no longer plant a
  custom player executable, backup archives pass a ZIP-bomb check before
  anything is extracted, and player window titles are redacted in the debug log
- Data safety — covers survive a corrupt-database rebuild and a temporarily
  offline drive, stale cover files are garbage-collected instead of growing
  forever, and partial backup restores can no longer replay over newer data
- Covers and previews — EXIF-rotated photos import upright, animated
  GIF/WebP covers play aspect-correct with honest error messages, and a
  failed import no longer deletes the previous cover
- Four natural themes (forest, sunset, ocean, paper) join the four original
  ones — eight themes in total, locked by a WCAG contrast contract
- Stability — the junction guard and the PySide6 version cap fix the Python
  3.11 scanning and shutdown crashes, long UNC paths scan again, and a
  read-only app folder falls back to a temporary cache with a clear message
  instead of dying at startup
- Documentation and guards — both READMEs rewritten around accurate claims,
  a stricter SPDX header guard, a new guard that keeps fixed typos and
  retired wording out of the docs, and more precise release rules

## Early versions (0.1.0 to 0.54.0)

FolderDex was developed as internal builds for a long time, numbered
retroactively as 0.1.0–0.51.0 (internal vN = 0.N.0); none of them was ever
publicly released. Persian notes for the builds that have them
(0.21.0–0.48.0) live in [docs/HISTORY-pre-1.0.fa.md](docs/HISTORY-pre-1.0.fa.md).

0.52.0–0.54.0 were the first public releases. The main work of this period:

- **0.52.0** — the first public release: the multi-drive offline library for
  folders and loose files, the cover and auto-preview chain, watch-state
  tracking with ratings and favorites, the missing-path manager, automatic
  backups in the single-file `.mcbak` format, and the bilingual
  English/Persian interface with four themes
- **0.53.0** — custom folder covers (animated GIF/WebP included), the
  WebP-only thumbnail cache (JPEG XL removed), and a lighter install via
  `PySide6-Essentials`
- **0.54.0** — security and stability hardening plus the rename to FolderDex
  (see the section above)

From the first stable release (1.0.0) on, every release gets its own
detailed entry (Added / Changed / Fixed / Security) in this file.
