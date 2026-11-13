# Third-Party Notices / اطلاعات نرم‌افزارهای شخص ثالث

This project is licensed under the **GNU GPL v3 or later** (see
[LICENSE](LICENSE)). The third-party open-source components used or bundled with
this application (and with the portable package built via `BUILD.bat`) remain under
their own licenses, listed below. All of them are compatible with being used in
a GPL-licensed application (the only AGPL-licensed item — the *optional*, not
installed-by-default PyMuPDF PDF fallback — is described in §5):

این پروژه تحت **GNU GPL نسخه‌ی ۳ یا جدیدتر** منتشر شده (مجوز کامل در [LICENSE](LICENSE)).
مؤلفه‌های متن‌باز شخص ثالثی که این برنامه استفاده یا همراه می‌کند (و بسته‌ی
قابل‌حملِ ساخته‌شده با `BUILD.bat`) مجوز خودشان را دارند که در ادامه فهرست شده —
همه با استفاده در برنامه‌ی GPL سازگارند (تنها موردِ AGPL — روشِ جایگزینِ
اختیاریِ PDF با PyMuPDF که به‌صورت پیش‌فرض نصب/همراه نمی‌شود — در §5
توضیح داده شده):

> **نکته:** فقط همین مقدمه دوزبانه است؛ بخش‌های ۱ تا ۶ عمداً به
> انگلیسی‌اند — متنِ حقوقیِ معتبر همانی است که در متنِ انگلیسی و در فایل‌های
> مجوزِ اصلی آمده و ترجمه‌ی آن بخشِ حکمی نیست.

---

## 1. 7-Zip — standalone console `7za.exe` (`bin/7za.exe`, v25.01)

- Project: https://www.7-zip.org — Copyright (c) Igor Pavlov. Binary version
  string: `7-Zip (a) 25.01 (x64)`.
- License of **this binary**, exactly as stated in the bundled
  [`bin/7zip-License.txt`](bin/7zip-License.txt): **GNU LGPL (2.1 or later)** as
  the main license for most of the code, plus **BSD 3-clause** (the ZSTD
  decompression code) and **BSD 2-clause** (the XXH64 code) parts.
- `7za.exe` is the reduced standalone console build taken from the official
  "7-Zip Extra" 25.01 package (`7z2501-extra.7z`, `x64/7za.exe`) and contains
  **no RAR codec**.
- SHA-256 of the bundled binary
  `574bb90d17732f3cc4145fd4ba12d8f29b9d63400881c0e6fe3110c88b0485de`
  (1,334,784 bytes) — verified byte-identical to `x64/7za.exe` inside the
  official https://www.7-zip.org/a/7z2501-extra.7z package. The well-known
  "unRAR restriction" therefore does **not** apply to this binary — it only
  applies to the RAR-capable modules of the full 7-Zip package (`7z.exe`,
  `7z.dll`), which are not bundled here. This app only *extracts* archives.
- Source code is freely available at https://www.7-zip.org/download.html

## 2. Vazirmatn Font (`assets/Vazirmatn-Regular.ttf`)

- Project: https://github.com/rastikerdar/vazirmatn
- License: **SIL Open Font License 1.1** — full text: [`assets/OFL.txt`](assets/OFL.txt)

## 3. Material Icon Theme icons (`assets/icons/*.png`)

- Project: https://github.com/PKief/vscode-material-icon-theme
- License: **MIT** — copyright line exactly as in the upstream `LICENSE` file:
  `Copyright (c) 2025 Material Extensions`. Full text:
  [`assets/icons/LICENSE.txt`](assets/icons/LICENSE.txt)
- Note: the PNGs shipped here are rasterized renders (with white padding) of that
  pack so the app needs no QtSvg dependency.
- **Trademarks:** some icons depict third-party brands (Word, PowerPoint,
  Python, Java, Go, Rust, PHP, TypeScript, …). The MIT license covers the icon
  *artwork*, not the brands themselves — all product names, logos and brands
  mentioned or depicted in the icons are property of their respective owners;
  their use does not imply affiliation or endorsement.

## 3-b. Application icon (`assets/icon.png`)

- The application icon is **the author's own work** (Mohammad Jafari) and is
  licensed under the project license, **GPL-3.0-or-later**, like the rest of
  the code. It is not taken from any third-party icon pack.

## 4. JPEG XL support (removed in 0.53.0 — historical note)

Versions before 0.53.0 pip-installed `pillow-jxl-plugin` (GPL-3.0-or-later) and
bundled the license texts of the native codec libraries inside its wheel
(libjxl, brotli, lcms2, highway, GCC runtime, winpthreads). **The application
no longer ships libjxl or any of its codec libraries**: measurements on real
thumbnails (160–512 px) showed WebP is both smaller and faster at these sizes
and decodes natively in Qt, so the thumbnail cache is WebP-only now and the
plugin (plus its ~30 bundled Rust crates) is gone from `requirements.txt`,
`BUILD.bat` and the portable package. The historical license texts were
removed from `licenses/` with the same change; see `CHANGELOG.md` for the
exact version.

## 5. Python dependencies installed at runtime

Installed from `requirements.txt` — all of them **core requirements**, not
optional (thumbnails are a core feature) — not redistributed in this
repository, but they accompany the portable build:

| Package | License | Project |
|---|---|---|
| PySide6 (Qt for Python) | LGPL-3.0 (with Qt's additional permission allowing use in GPL apps) | https://www.qt.io/qt-for-python |
| shiboken6 (PySide6 binding generator, travels with PySide6) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://www.qt.io/qt-for-python |
| Pillow | MIT-CMU | https://python-pillow.org |
| py7zr | LGPL-2.1-or-later | https://github.com/miurahr/py7zr |
| pyppmd (py7zr dependency) | LGPL-2.1-or-later | https://github.com/miurahr/pyppmd |
| pybcj (py7zr dependency) | LGPL-2.1-or-later | https://github.com/miurahr/pybcj |
| inflate64 (py7zr dependency) | LGPL-2.1-or-later | https://github.com/miurahr/inflate64 |
| multivolumefile (py7zr dependency) | LGPL-2.1-or-later | https://github.com/miurahr/multivolumefile |
| texttable (py7zr dependency) | MIT | https://github.com/fizista/py-texttable |
| brotli (py7zr dependency) | MIT | https://github.com/google/brotli |
| psutil (py7zr dependency) | BSD-3-Clause | https://github.com/giampaolo/psutil |
| backports.zstd (py7zr dependency) | PSF-2.0 (plus the bundled libzstd — BSD-3-Clause, dual) | https://github.com/Rogdham/backports.zstd |
| pycryptodomex (py7zr dependency) | BSD-2-Clause | https://github.com/Legrandin/pycryptodome |
| rarfile | ISC | https://github.com/markokr/rarfile |
| pypdfium2 | Apache-2.0 + PDFium BSD-3-Clause | https://github.com/pypdfium2-team/pypdfium2 |
| PyInstaller (build-time only) | GPL-2.0 with bootloader exception | https://pyinstaller.org |
| Python runtime (inside the portable package) | PSF-2.0 | https://www.python.org |
| PyMuPDF (`fitz`) — optional secondary PDF fallback, **not** installed by default | AGPL-3.0 (or a commercial license from Artifex) | https://github.com/pymupdf/pymupdf |

Notes:

- **PyMuPDF** is never installed by `run.bat` / `BUILD.bat` and never bundled. It
  is only *used if the user installed it on their own* — and only when the
  default PDF backend (`pypdfium2`) is missing — to render first-page PDF
  thumbnails (see `filethumbs.py`). Because PyMuPDF is AGPL-3.0 (not GPL-3.0),
  anyone redistributing a build that includes it must comply with the AGPL for
  that combination; otherwise don't install it and nothing changes.
- **Exact license texts now ship with every package** in the `licenses/`
  folder (see [`licenses/README.txt`](licenses/README.txt)):
  `Qt-PySide6-LGPL-3.0.txt` (the LGPL-3.0 text followed by the GPL-3.0 text
  it incorporates by reference; the PySide6 wheels
  themselves embed no license file in their dist-info — verified on
  PySide6_Essentials 6.12.0 — and are "LGPL-3.0-only OR GPL-2.0-only OR
  GPL-3.0-only" per the wheel's METADATA) plus the bilingual
  `Qt-PySide6-LGPL-note.txt` (Qt source location, one-dir rationale),
  `Python-PSF-2.0.txt` (the canonical PSF-2.0 wording) plus the FULL CPython
  license file `Python-LICENSE.txt` (HISTORY OF THE SOFTWARE + complete PSF /
  BeOpen / CNRI / CWI agreements — verbatim from the CPython installation),
  `Pillow-MIT-CMU.txt` (the verbatim LICENSE file shipped inside the Pillow
  wheel), `py7zr-LGPL-2.1.txt` (verbatim LGPL-2.1),
  `pycryptodomex-BSD-2.txt` (verbatim BSD-2-Clause — the py7zr encryption
  backend, added so the copyright notice travels with the
  redistribution as BSD-2 requires), `rarfile-ISC.txt` (verbatim ISC) and
  `pypdfium2-Apache-2.0.txt` (verbatim Apache-2.0; the PDFium BSD-3-Clause
  text ships inside the pypdfium2 wheel's `dist-info/licenses/LICENSES/`
  folder). For every build `BUILD.bat` additionally copies the exact
  `licenses/LICENSE` of the build venv's Pillow wheel into the package as
  `licenses/Pillow/LICENSE`, and the `licenses/` folders of the build venv's
  py7zr / pycryptodomex / rarfile / pypdfium2 wheels into
  `licenses/<package>/`.
- The portable package also contains the **Python runtime** itself (PSF-2.0
  plus the companion agreements covering every CPython release — exact full
  text: `licenses/Python-LICENSE.txt`) and the native libraries that travel
  with the embedded CPython distribution (OpenSSL, libffi, sqlite3, zlib,
  liblzma — permissive/FOSS licenses), and
  the native codec libraries bundled inside the Pillow wheel (zlib,
  libjpeg-turbo, libtiff, libwebp, openjpeg, lcms2, freetype — permissive/FOSS
  licenses; the exact texts are all included in the Pillow LICENSE file
  described above and also ship inside the Pillow wheel's
  `*.dist-info/licenses/` folder of your build venv).
- **py7zr's LGPL-2.1+ backends ship as separate, swappable files**: the
  `pyppmd`, `pybcj`, `inflate64` and `multivolumefile` modules are compiled
  extensions distributed as individual `.pyd` files inside the one-dir bundle
  (never statically linked into the app), so they can be replaced or removed
  by the user — the same rationale as the Qt/PySide6 LGPL note
  ([`licenses/Qt-PySide6-LGPL-note.txt`](licenses/Qt-PySide6-LGPL-note.txt)).
- **Qt's own third-party components**: Qt itself bundles many third-party
  libraries (zlib, libpng, freetype, …) with their own permissive licenses.
  The authoritative per-component list lives at
  https://doc.qt.io/qt-6/licenses-used-in-qt.html — only the components
  actually shipped apply.

## 6. ffmpeg (optional, ONLY bundled on explicit request)

`BUILD.bat` does **not** bundle `ffmpeg.exe` silently. ffmpeg is included in
the portable package **only** when you pass the explicit flag:

    BUILD.bat /with-ffmpeg

Even then, the build aborts unless a `ffmpeg-LICENSE.txt` sits next to the
`ffmpeg.exe` on your PATH (copy the license file from the ffmpeg distribution
you downloaded). The license of that binary (LGPL-2.1+ or GPL, depending on
its build configuration) is *yours to verify* — check
https://www.ffmpeg.org/legal.html before distributing. If ffmpeg is not
bundled, the app falls back to Windows shell thumbnails. Upstream/source:
https://www.ffmpeg.org

If you distribute the portable package, its `SOURCE.txt` names the project's
repository where the corresponding source of this application is available, as
required by the GPL.
