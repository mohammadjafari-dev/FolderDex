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
  tag must exist there. An empty repository (or a bare rename redirect
  without the history) is **not a valid GPL source offer** — finish the
  migration/transfer first, then build.

## 2. Build on Windows

- Build machine: Windows 10/11 x64 with Python 3.11, 3.12 or 3.13 x64 (build
  machines only — the finished package needs nothing installed).
- Run:

  ```bat
  BUILD.bat
  ```

  Add `/with-ffmpeg` only if you also want `ffmpeg.exe` bundled — it requires a
  `ffmpeg-LICENSE.txt` next to the exe on PATH or the build aborts (see
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) §6).
  A `/with-ffmpeg` build distributes FFmpeg: publishing it publicly also
  requires the **exact corresponding source and build configuration** of
  that FFmpeg build, published next to the release. Until those exist, keep
  `/with-ffmpeg` builds for private/testing use only — the default build
  ships without FFmpeg and is unaffected.

- Result: `dist\FolderDex\` (one-dir) and `FolderDex-win64-portable.zip`.

## 3. Verify the package contents

Open the zip and confirm every item is there:

- [ ] `FolderDex.exe` (+ the `_internal\` runtime folder)
- [ ] `LICENSE`
- [ ] `THIRD_PARTY_NOTICES.md`
- [ ] `SOURCE.txt` — open it; the URL inside must be the public repository
- [ ] `licenses\` — at minimum:
      `Qt-PySide6-LGPL-3.0.txt`, `Qt-PySide6-LGPL-note.txt`, `Python-PSF-2.0.txt`,
      `Python-LICENSE.txt`, `Pillow-MIT-CMU.txt`, `py7zr-LGPL-2.1.txt`,
      `pycryptodomex-BSD-2.txt`, `rarfile-ISC.txt`,
      `pypdfium2-Apache-2.0.txt`, `README.txt` — plus `licenses\Pillow\LICENSE`
      and `licenses\py7zr\`, `licenses\rarfile\`, `licenses\pypdfium2\`
      when built from a build venv
- [ ] `bin\7zip-License.txt` and `bin\README-7zip.txt` (the SHA-256 of
      `7za.exe` is written in the README there) — re-check that hash against
      the official 7-Zip download once, not only against the recorded value
- [ ] `README.md` and `README.fa.md`

## 4. Smoke test

- Unzip into a **writable** folder (never Program Files) and run
  `FolderDex.exe` once.
- Add one root folder, run a scan, open a title's details page, quit cleanly.
- An un-signed build shows a one-time **SmartScreen** prompt ("More info" →
  "Run anyway") on each new machine. That is normal for unsigned binaries and
  belongs in the release notes.

## 5. Hash, tag, release

- Compute the zip's SHA-256 and put the hash into the release notes:

  ```powershell
  Get-FileHash .\FolderDex-win64-portable.zip -Algorithm SHA256
  ```

- Before tagging, set the `[<VERSION>] - <date>` line at the top of
  [CHANGELOG.md](../CHANGELOG.md) and [CHANGELOG.fa.md](../CHANGELOG.fa.md)
  to the **actual release date** — the tag, the zip and both CHANGELOGs must
  name the same day, and the tag must sit on the exact commit the zip was
  built from.

- Tag the release commit:

  ```bat
  git tag -a v<VERSION> -m "FolderDex <VERSION>"
  git push origin v<VERSION>
  ```

- Create the GitHub release for the tag and upload
  `FolderDex-win64-portable.zip`. Release notes: the version summary from
  [CHANGELOG.md](../CHANGELOG.md), the SHA-256 from the step above, and the
  one-time SmartScreen note.

## 6. After publishing

- Reload the repository page: README renders, links resolve, and the
  Screenshots section shows the six PNGs once the owner has committed them to
  `screenshots/`.
- Keep the repository public as long as any distributed zip exists.
