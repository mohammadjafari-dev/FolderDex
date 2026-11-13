# FolderDex

**English** | [فارسی](README.fa.md)

FolderDex is an offline, portable Windows app for organizing folders and loose
files into a browsable library. Each immediate subfolder under a category root
appears as an independent title; loose files placed directly in a root are
shown as separate entries too.

A custom cover is optional. When available, FolderDex can use the Windows
folder icon or an image inside the folder as the title artwork, and folders
without any image fall back to generated previews (video, PDF, archive) or a
clean file-type card. You can set, replace, or clear a custom cover at any
time. The SQLite database, custom covers, and thumbnails are stored locally.
FolderDex does not modify your original collection files or folders.

![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.11--3.13-blue)
[![tests](https://github.com/mohammadjafari-dev/FolderDex/actions/workflows/tests.yml/badge.svg)](https://github.com/mohammadjafari-dev/FolderDex/actions/workflows/tests.yml)

> **Note:** The application code and this project's Markdown files were written with the help of AI.

## Table of contents

- [About](#about)
- [Features](#features)
- [Screenshots](#screenshots)
- [Installation](#installation)
- [Quick start](#quick-start)
- [What's new](#whats-new)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [Feedback](#feedback)
- [License](#license)

## About

I built FolderDex to organize my own collection — movies, series, anime, games,
courses, books and comics kept across several drives for years — and prepared
it for this public release.

## Features

- **Drive-aware library** — roots per category (movies, series, anime, games, courses, books, comics…), loose files automatically become titles too
- **Visual cards, not a file list** — every title shows an image or a simple card: a custom cover if you set one, otherwise the folder's Windows icon, an image from inside the folder, an auto-generated preview, or a file-type card
- **Watch-state for everything** — Unwatched / In progress / Watched for video, books, archives and documents; personal 1–5 star ratings and favorites
- **Player-aware tracking & resume** — mpv, PotPlayer and VLC are tracked (precise / real / estimated); playback continues where you left off
- **Missing-path handling** — after a backup merge, folder paths that moved to another drive are fixed automatically; missing items can be re-pointed or removed
- **Automatic atomic backup** — single-file `.mcbak`, written only on real changes, non-destructive merge with security snapshots
- **Recents sidebar** — your last 15 opened titles, one click away
- **Bilingual + 8 themes** — complete English/Persian UI, Light / Dim / Dark / AMOLED / Forest / Sunset / Ocean / Paper
- **Local thumbnail cache** — video, archive, PDF and image previews cached as WebP (decoded natively by Qt), rebuilt only when needed
- **Smooth display on huge libraries** — cards are prepared before they appear and speed modes adapt to the disk type (SSD or spinning drive)

➡️ Full feature list: [docs/FEATURES.md](docs/FEATURES.md)

## Screenshots

<!-- Screenshots are temporarily hidden until real captures replace them.
<p align="center">
  <img src="screenshots/library-dark.png" width="49%" alt="FolderDex — dark theme">
  <img src="screenshots/details.png" width="49%" alt="Details page of a title">
</p>
<p align="center">
  <img src="screenshots/filters-recents.png" width="49%" alt="Watch-state filter and the recents sidebar">
  <img src="screenshots/settings.png" width="49%" alt="Settings — paths and categories">
</p>
<p align="center">
  <img src="screenshots/stats.png" width="49%" alt="Library stats">
  <img src="screenshots/light-theme.png" width="49%" alt="FolderDex — light theme">
</p>

-->

## Installation

**Portable (recommended):** download `FolderDex-win64-portable.zip` from
[Releases](../../releases/latest), unzip anywhere **writable** (not Program Files) and
run `FolderDex.exe`. No Python, no admin rights, no registry — uninstall by
deleting the folder. Unsigned builds may show a one-time SmartScreen prompt
("More info" → "Run anyway").

> **Offline use:** the ready-made portable package needs no internet at all —
> download it once and it runs fully offline, and it never sends any data
> online. Running from source and building the package are different: they
> need internet once for the first `pip install` of the dependencies.

**From source:**

```bat
:: Python 3.11–3.13 required
run.bat        :: creates .venv, installs requirements, launches GUI
debug.bat      :: same, but keeps the console open to see errors
```

> **Python 3.11 note:** the first `pip install` automatically picks
> `PySide6-Essentials` **6.11.x** instead of 6.12+ — shiboken6 6.12.0 has an
> upstream crash at interpreter shutdown on 3.11 (runtime is unaffected;
> see the changelog). Nothing to configure, 3.12/3.13 get the latest release.

> **If the first install fails** (no connection to PyPI), later `run.bat`
> launches skip the install and start with reduced capabilities (archive and
> PDF thumbnails are skipped). Delete the `.requirements_failed` marker file
> next to the script and run again to retry.

**Build your own portable package:** run `BUILD.bat` — details in
[docs/BUILDING.md](docs/BUILDING.md).

## Quick start

1. **Add your root folders** — in settings, point the app at one folder per category
   (e.g. `D:/فیلم`, `E:/بازی`). See [`config.example.json`](config.example.json)
   (Persian category names) or [`config.example.en.json`](config.example.en.json)
   (English) for the full config format.
2. **Folder layout** — one folder per title; covers are optional:

   ```
   D:\فیلم\
   └── Some Movie\             ← each immediate subfolder = one title
       ├── desktop.ini         ← folder icon = the default cover (if set)
       ├── icon.ico
       └── files…
   ```

   Loose files directly inside a root automatically become titles of their own.
3. **Browse & track** — mark watched with `W`, rate with the stars, and everything is
   saved to the local database. Backups live in `backups/` next to the app.

## What's new

**0.54.0** — in preparation for its official release: hardens security and stability, and renames the
app to **FolderDex** (see [CHANGELOG.md](CHANGELOG.md)). The first public
release was **0.52.0**; 0.53.0 added the WebP thumbnail cache and custom folder
covers. Released under the GPL-3.0-or-later license, with a restructured
documentation set and a cleaned repository (demo data removed from git; the
one bundled binary, [`bin/7za.exe`](bin/7za.exe), ships with its license text
— see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

➡️ Full details: [CHANGELOG.md](CHANGELOG.md) · dev history (Persian only): [docs/HISTORY-pre-1.0.fa.md](docs/HISTORY-pre-1.0.fa.md)

## Roadmap

- **Performance optimization** — making the app even smoother on very large libraries
- **Linux port** — planned, no date yet; Windows 10/11 stays the primary platform

The app is usable today; these are the next steps and they have no set dates.

## Documentation

- [docs/FEATURES.md](docs/FEATURES.md) — complete feature list
- [docs/BUILDING.md](docs/BUILDING.md) — build & packaging guide
- [CHANGELOG.md](CHANGELOG.md) — release changes · [CHANGELOG.fa.md](CHANGELOG.fa.md)
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — bundled third-party software
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to report bugs and suggest features
- [SECURITY.md](SECURITY.md) — how to report a vulnerability privately · [SECURITY.fa.md](SECURITY.fa.md) (Persian)

## Feedback

Code contributions and pull requests are not accepted at this time; bug reports
and feature suggestions are welcome through the project's GitHub Issues section
— pick the English or Persian template when you
[open an issue](../../issues/new/choose).

## License

Released under the [GNU GPL v3 or later](LICENSE) — Copyright (C) 2026 Mohammad Jafari.

Bundled third-party software (7-Zip, Vazirmatn font, Material Icon Theme icons, …)
keeps its own license — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
