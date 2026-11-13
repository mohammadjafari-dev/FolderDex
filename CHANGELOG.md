# Changelog

All notable changes to FolderDex are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**فارسی:** [CHANGELOG.fa.md](CHANGELOG.fa.md)
**Internal development history (Persian only):** [docs/HISTORY-pre-1.0.fa.md](docs/HISTORY-pre-1.0.fa.md)
**Official repository:** [mohammadjafari-dev/FolderDex](https://github.com/mohammadjafari-dev/FolderDex)

Before the first stable release this file stays short: a single dated section
describes the current development state — not one section per internal build —
plus one explanatory section about the internal builds' version numbering.
Detailed per-version entries begin with the first stable release.

## [0.55.1] - 2026-11-14

### Summary

- A bug-fix round from the user's real-world report — every issue root-caused
  in code and locked with regression tests
- The English UI is now fully English — the four theme names (forest, sunset,
  ocean, paper) that still showed Persian are translated, and a runtime audit
  now locks every dynamically-translated string (theme and track-profile
  labels) on top of the existing AST guard for literal calls
- The Recents sidebar tells the truth — path comparison is case/separator
  insensitive and orphaned recents (moved/renamed/deleted folders) are pruned
  after each scan, so the stored list and the sidebar count always agree
  (reported as “it says 15 but shows 9”)
- The cover image stays in the details file list with a “Cover”/“Folder icon”
  badge instead of vanishing, and the card counter now uses the same
  visible-file rule as the details counter — a folder whose only file is its
  cover shows 1/1 instead of 1/0
- Book progress shows pages, not seconds — the ◐ badge on a half-read
  book/manga file renders «صفحه‌ی N» the way its tooltip already did; video
  badges keep showing the resume time
- Category colors are editable — a color swatch next to every category in
  Settings → Categories opens choose / reset-to-auto / copy-hex; the choice
  persists in `category_colors` and repaints the sidebar, cards and details
  badge
- Layout robustness — the four-button row in Settings → Categories wraps to
  two short rows and the add-category dialog sizes itself from its content,
  so nothing clips at 100–150% display scaling (reported clipping of the
  word «دسته»)
- The real fix for the clipped «دسته» — the absolute offset `right: 12px` on
  `QGroupBox::title` in the shared stylesheet made Qt cleanly clip the FIRST
  glyph of every RTL group-box title (and the first letter of LTR titles) at
  narrow widths — a Qt subcontrol-offset behavior, not a dialog-size problem,
  which is why the 0.55.0 width fix could not remove it. The stylesheet now
  insets the title with the box-model `margin-right: 12px` and pixel
  regression tests pin the rendered title ink in both RTL and LTR (reported
  as “still invisible on 0.55.0”)
- The card counter behavior is documented as intentional — for a video
  folder the badge counts episodes («N قسمت») and the cover image, shown as
  the thumbnail, is not an episode; it stays in the total file count and the
  details file list
- Theme-name mismatch between the READMEs/FEATURES and the actual in-app
  labels fixed in all four docs

## Early versions (0.1.0 to 0.55.1)

FolderDex was developed as internal builds for a long time, numbered
retroactively as 0.1.0–0.51.0 (internal vN = 0.N.0); none of them was ever
publicly released. Persian notes for the builds that have them
(0.21.0–0.48.0) live in [docs/HISTORY-pre-1.0.fa.md](docs/HISTORY-pre-1.0.fa.md).

0.52.0–0.55.1 were the first public releases. The main work of this period:

- **0.52.0** — the first public release: the multi-drive offline library for
  folders and loose files, the cover and auto-preview chain, watch-state
  tracking with ratings and favorites, the missing-path manager, automatic
  backups in the single-file `.mcbak` format, and the bilingual
  English/Persian interface with four themes
- **0.53.0** — custom folder covers (animated GIF/WebP included), the
  WebP-only thumbnail cache (JPEG XL removed), and a lighter install via
  `PySide6-Essentials`
- **0.54.0** — security and stability hardening plus the rename to FolderDex:
  the display name, window titles, portable executable and package, log files
  and the official repository all follow the new brand (legacy names keep
  working), backup restore can no longer plant a custom player executable,
  backups pass a ZIP-bomb check, covers survive a corrupt-database rebuild
  and an offline drive, four natural themes (forest, sunset, ocean, paper)
  joined the originals, and a read-only app folder falls back to a temporary
  cache with a clear message instead of dying at startup
- **0.55.0** — bug-fix round from the user's report (see the section above)
- **0.55.1** — the root-cause fix for the clipped group-box titles (see the
  section above)

From the first stable release (1.0.0) on, every release gets its own
detailed entry (Added / Changed / Fixed / Security) in this file.
