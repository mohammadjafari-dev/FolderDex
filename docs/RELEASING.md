# Release checklist — FolderDex

Language: [English](RELEASING.md) · [فارسی](RELEASING.fa.md)

Follow every step below, in order, each time a portable zip is published. The
checklist exists so no release ever ships with a dead source link, a missing
license text or an untested binary.

## 1. Repository public and reachable

- The repository must be **public** at
  <https://github.com/mohammadjafari-dev/FolderDex>.
- `BUILD.bat` writes this URL into the package's `SOURCE.txt` (the GPL source
  offer). Open the URL in a **fresh browser session (logged out)** and confirm
  it resolves before building anything.
- The `tests` workflow must be green for the release commit — **Actions** tab:
  pytest on windows-latest (Python 3.11/3.12/3.13) and ubuntu-latest (3.12),
  plus the `repo-integrity` job.

- The repository must actually **contain this version's code** — the page
  has to show the file tree, `LICENSE` and `README.md`, and the `v<VERSION>`
  tag must exist there (created and pushed in step 2 below — before anything
  is built). An empty repository (or a bare rename redirect
  without the history) is **not a valid GPL source offer** — finish the
  migration/transfer first, then build.

## 2. Tag the release commit (before building)

The package's `SOURCE.txt` names the `v<VERSION>` tag and the exact commit
SHA as the GPL source offer — so the tag must exist and be pushed **before**
the build:

- Set the `[<VERSION>] - <date>` line at the top of
  [CHANGELOG.md](../CHANGELOG.md) and [CHANGELOG.fa.md](../CHANGELOG.fa.md)
  to the **actual release date** — the tag, the zip and both CHANGELOGs must
  name the same day.
- Commit and push, then tag exactly that commit:

  ```bat
  git tag -a v<VERSION> -m "FolderDex <VERSION>"
  git push origin main v<VERSION>
  ```

## 3. Build on Windows

- Build machine: Windows 10/11 x64 with Python 3.11, 3.12 or 3.13 x64 (build
  machines only — the finished package needs nothing installed).
- Build **from the tagged commit** — `git log --oneline -1` must show the
  commit the tag sits on; its SHA goes into `SOURCE.txt` verbatim.
- Run:

  ```bat
  BUILD.bat
  ```

  Add `/with-ffmpeg` only if you also want `ffmpeg.exe` bundled — it requires a
  `ffmpeg-LICENSE.txt` next to the exe on PATH or the build aborts (see
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) §7).
  A `/with-ffmpeg` build distributes FFmpeg: publishing it publicly also
  requires the **exact corresponding source and build configuration** of
  that FFmpeg build, published next to the release. Until those exist, keep
  `/with-ffmpeg` builds for private/testing use only — the default build
  ships without FFmpeg and is unaffected.

- Result: `dist\FolderDex\` (one-dir) and `FolderDex-win64-portable.zip`.

## 4. Verify the package contents

Open the zip and confirm every item is there:

- [ ] `FolderDex.exe` (+ the `_internal\` runtime folder)
- [ ] `LICENSE`
- [ ] `THIRD_PARTY_NOTICES.md`
- [ ] `SOURCE.txt` — open it; the URL inside must be the public repository, and
      the exact build commit and the `v<VERSION>` tag it names must be **pushed**
      to that repository before distribution (open the commit link in a fresh
      browser session — a source offer that 404s is not a source offer)
- [ ] `licenses\` — at minimum:
      `Qt-PySide6-LGPL-3.0.txt`, `Qt-PySide6-LGPL-note.txt`, `Python-PSF-2.0.txt`,
      `Python-LICENSE.txt`, `Pillow-MIT-CMU.txt`, `py7zr-LGPL-2.1.txt`,
      `pycryptodomex-BSD-2.txt`, `rarfile-ISC.txt`,
      `pypdfium2-Apache-2.0.txt`, `PyInstaller-COPYING.txt`, `README.txt` — plus
      `licenses\Pillow\LICENSE` and the exact-version license folders of the
      twelve core packages: `licenses\py7zr\`,
      `licenses\rarfile\`, `licenses\pypdfium2\`, `licenses\pycryptodomex\`,
      `licenses\pyppmd\`, `licenses\pybcj\`, `licenses\inflate64\`,
      `licenses\multivolumefile\`, `licenses\texttable\`, `licenses\brotli\`,
      `licenses\psutil\`, `licenses\backports_zstd\`
      (the exact-version texts of THIS build — BUILD.bat aborts if any of them
      is missing)
- [ ] the 7-Zip license files `7zip-License.txt` and `README-7zip.txt` next to
      the bundled `7za.exe` — inside the package they sit in `_internal\bin\`
      (the SHA-256 of
      `7za.exe` is written in the README there) — re-check that hash against
      the official 7-Zip download once, not only against the recorded value
- [ ] `README.md` and `README.fa.md`
- [ ] the third-party components **actually shipped** inside
      `_internal\PySide6\Qt\` are covered by the Qt licenses page referenced
      in `licenses\Qt-PySide6-LGPL-note.txt` — spot-check one bundled
      component against https://doc.qt.io/qt-6/licenses-used-in-qt.html

## 5. Smoke test

- Unzip into a **writable** folder (never Program Files) and run
  `FolderDex.exe` once.
- Add one root folder, run a scan, open a title's details page, quit cleanly.
- An un-signed build shows a one-time **SmartScreen** prompt ("More info" →
  "Run anyway") on each new machine. That is normal for unsigned binaries and
  belongs in the release notes.

## 6. Hash, release

- Compute the zip's SHA-256 and put the hash into the release notes:

  ```powershell
  Get-FileHash .\FolderDex-win64-portable.zip -Algorithm SHA256
  ```

- Create the GitHub release for the tag and upload
  `FolderDex-win64-portable.zip`. Release notes: the version summary from
  [CHANGELOG.md](../CHANGELOG.md), the SHA-256 from the step above, and the
  one-time SmartScreen note.

## 7. After publishing

- Reload the repository page: README renders and all links resolve. When you
  later add your own screenshots (EN and FA) to `screenshots/` and embed them
  in both READMEs, re-run `pytest tests/test_repo_integrity.py` first.
- Keep the repository public as long as any distributed zip exists.
