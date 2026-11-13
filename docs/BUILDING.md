# Building & packaging

Language: [English](BUILDING.md) · [فارسی](BUILDING.fa.md)

## Table of contents

- [Run from source](#run-from-source)
- [Build the portable package](#build-the-portable-package)
- [Thumbnail backends](#thumbnail-backends)
- [Common problems](#common-problems)

## Run from source

Requirements: **Windows 10/11 x64** and **Python 3.11–3.13** on PATH.

```bat
run.bat        :: creates .venv, installs requirements.txt, launches the GUI
debug.bat      :: same, but keeps the console open so you can see errors
diag.bat       :: when nothing starts: collects venv/Python/import state
                  into folderdex-diag.log for a bug report
```

Both scripts are idempotent: the venv and packages are only installed once.
Internet is needed for the first pip install (~100 MB, PyPI). `requirements.txt`
installs `PySide6-Essentials` — not the full `PySide6` meta-package — because
the app only imports QtCore/QtGui/QtWidgets; the meta-package would additionally
pull the Addons, Pdf and WebEngine wheels (~170 MB more, including a full
bundled Chromium) that are never imported. The core also carries every
thumbnail backend — `py7zr`, `rarfile` and `pypdfium2`: thumbnails are a
core feature, so if pip cannot install any of them the launcher stops with
a visible error.
`run.bat` also
verifies that the `.venv`'s Python actually runs and rebuilds it automatically
if broken — e.g., after you uninstall or upgrade Python, when the old venv
would otherwise fail invisibly.

## Build the portable package

Run:

```bat
BUILD.bat                 :: one-dir build (the only supported mode)
BUILD.bat /with-ffmpeg    :: also bundle ffmpeg.exe (needs ffmpeg-LICENSE.txt next to it)
```

What it does:

1. Finds a Python 3.11+ (uses the `py` launcher if needed)
2. Creates a clean build venv `.build-venv/` and installs requirements + build tools
3. Renders `assets/icon.png` into a multi-size `icon.ico`
4. Stages bundled binaries: `bin/7za.exe` (+ its license files); `ffmpeg.exe`
   **only** when `/with-ffmpeg` is passed (and its `ffmpeg-LICENSE.txt` is found
   next to it — the build aborts otherwise)
5. Runs PyInstaller → `dist\FolderDex\FolderDex.exe`
6. Copies `LICENSE`, `THIRD_PARTY_NOTICES.md`, the `licenses/` folder (the
   canonical Qt/Python/Pillow texts plus the py7zr / rarfile / pypdfium2
   license texts) and a generated `SOURCE.txt` (repo link + version) into
   the package — required by the GPL for any distribution. It also verifies
   the PDFium DLL and the Pillow WebP codec really made it into the package

7. Zips the result → `FolderDex-win64-portable.zip`

The output runs on any Windows 10/11 x64 with **nothing** installed: no Python,
no ffmpeg (bundled only on `/with-ffmpeg`), no admin rights, no registry.

Build-time internet is required once (pip downloads PyInstaller and dependencies).

Note — **no /onefile mode**: PySide6/Qt is LGPL-3.0; a one-file exe makes it
practically impossible for users to replace the Qt libraries as the LGPL allows.
One-dir builds keep them replaceable in `_internal\`, so that is what we ship.

## Thumbnail backends

Nothing in `requirements.txt` is optional anymore — every thumbnail backend is
a core requirement, because thumbnails are a core feature (the cache itself
is WebP, see `imgcache.py` and the note in `requirements.txt`):

| Backend | Purpose |
|---|---|
| `py7zr` | in-process 7z extraction backend (the safety net when `bin/7za.exe` or the system 7-Zip is unavailable) |
| `rarfile` | pure-Python RAR reader (listing and uncompressed members work without any external tool) |
| `pypdfium2` | renders real first-page PDF thumbnails |

ZIP/CBZ/EPUB/TAR thumbnails work with zero installs (built-in backends) and
`bin/7za.exe` is always bundled, covering archive listing for every format.
RAR extraction of compressed members additionally needs UnRAR/WinRAR on the
system. Video thumbnails use the Windows shell handler; the **only** optional
piece remains `ffmpeg.exe`, bundled solely via `BUILD.bat /with-ffmpeg` for
machines whose shell has no codec — it is not bundled by default because it
would add hundreds of megabytes to the package.


## Common problems

- **SmartScreen warning** — the build is not code-signed. "More info" → "Run
  anyway" once per machine. Expected for unsigned open-source builds.
- **Antivirus flags the exe** — a known false-positive pattern with PyInstaller
  builds (FolderDex ships one-dir only). Submit the file to your antivirus
  vendor as a false positive.
- **pip fails / PyPI unreachable** — if PyPI is blocked or slow on your network
  (VPN needed in some regions), retry later or configure a mirror before running
  the scripts.
- **`run.bat` finishes and no window appears** — a `.venv` left over from an
  uninstalled/upgraded Python exits with a Windows DLL error (a negative exit
  code that `if errorlevel 1` silently accepted as success), so the old launcher
  reached its `start` line and died invisibly. The current `run.bat` checks the
  venv first and rebuilds it automatically, logs every step to
  `folderdex-launcher.log`, and verifies the app is still alive a few seconds
  after launch. If the window still closes at once, run `diag.bat` and send the
  resulting `folderdex-diag.log`; `debug.bat` always shows the real error.
- **"Unzip somewhere writable"** — the portable app writes its config, database
  and cache next to the exe. Do not run it from `Program Files` or a read-only
  mount; an external drive is fine.
- **ffmpeg license** — ffmpeg is bundled only with `/with-ffmpeg`, and only
  together with a `ffmpeg-LICENSE.txt` you provide from your ffmpeg
  distribution; its license (LGPL-2.1+ or GPL) depends on that build's
  configuration. Check your ffmpeg build before distributing it — see
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
- **Linux system dependencies (dev/tests only)** — the shipped package is
  Windows-only, but running the test suite on Linux (a dev machine or a CI
  runner) needs the system GL stack that the bundled PySide6 links against
  (`libEGL.so.1` / `libGL.so.1`). Fresh Ubuntu/Debian systems and GitHub
  runner images (24.04 and 26.04 alike) do not install it, and since pytest 8
  even `importorskip` cannot mask it: collection dies with
  `Interrupted: N errors during collection` (pytest exit code 2). Fix once
  per machine:
  `sudo apt-get update && sudo apt-get install -y --no-install-recommends libegl1 libgl1`.
  `tests/conftest.py` preflights the Qt runtime and names the exact missing
  library when it is absent.
