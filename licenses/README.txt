License texts shipped with the portable package (licenses/)
============================================================

BUILD.bat copies this folder into every portable package so distributed
binaries always ship the notices. See THIRD_PARTY_NOTICES.md for the
overview of every component and its license.

Qt for Python (PySide6) — LGPL-3.0-only
---------------------------------------
  Qt-PySide6-LGPL-3.0.txt     LGPL-3.0-only: the LGPL-3.0 text followed by
                              the GPL-3.0 text it incorporates by reference
                              (the canonical GNU wording as published at
                              https://www.gnu.org/licenses/lgpl-3.0.txt).
                              PySide6 wheels are "LGPL-3.0-only OR
                              GPL-2.0-only OR GPL-3.0-only" per the wheel's
                              dist-info/METADATA (verified on
                              PySide6_Essentials 6.12.0); this application
                              uses the LGPL-3.0 option. The wheels do NOT
                              embed a license file in dist-info, so the
                              canonical text is tracked in this repo.
                              https://www.qt.io/qt-for-python
  Qt-PySide6-LGPL-note.txt    Short bilingual (EN/FA) note: where the Qt
                              source comes from, and why the portable
                              package is built one-dir so the Qt libraries
                              stay replaceable (LGPL requirement).

Python runtime — PSF-2.0
------------------------
  Python-PSF-2.0.txt          PSF License Version 2, the canonical text
                              (SPDX license-list text; the same terms as
                              https://docs.python.org/3/license.html). The
                              portable package embeds the Python runtime,
                              so this text travels with it.
                              https://www.python.org

Pillow — MIT-CMU (+ bundled codecs)
-----------------------------------
  Pillow-MIT-CMU.txt          The LICENSE file shipped inside the Pillow
                              wheel itself, copied verbatim from
                              pillow-13.0.0.dist-info/licenses/LICENSE
                              (repo snapshot — for every build BUILD.bat
                              also copies the exact dist-info LICENSE of
                              the build venv's Pillow into the package as
                              licenses/Pillow/LICENSE): Pillow is MIT-CMU
                              (top of the file), followed by the licenses
                              of the native codecs bundled in the wheel
                              (AOM, brotli, OpenJPEG, zlib, libjpeg-turbo,
                              libtiff, libwebp, freetype, lcms2, Tcl/Tk, …).
                              https://python-pillow.org

Thumbnail backends (core requirements.txt entries)
--------------------------------------------------
  py7zr-LGPL-2.1.txt         The GNU LGPL-2.1 text — py7zr is LGPL-2.1-or-later.
                             Copied verbatim from
                             py7zr-1.1.4.dist-info/licenses/LICENSE.
                             https://github.com/miurahr/py7zr
  pycryptodomex-BSD-2.txt    The BSD-2-Clause license text of pycryptodomex
                             (the py7zr encryption backend).
                             Copied verbatim from
                             pycryptodomex-3.24.2.dist-info/LICENSE.rst:
                             the code is partially public domain and
                             partially BSD-2-Clause; BSD-2 requires keeping
                             the copyright notice in redistributions, so the
                             full text ships here. For every build BUILD.bat
                             also copies the exact dist-info license of the
                             build venv's pycryptodomex into the package as
                             licenses/pycryptodomex/.
                             https://github.com/Legrandin/pycryptodome
  rarfile-ISC.txt            The ISC license text — Copyright (c) 2005-2024
                             Marko Kreen. Copied verbatim from
                             rarfile-4.5.dist-info/licenses/LICENSE.
                             https://github.com/markokr/rarfile
  pypdfium2-Apache-2.0.txt   The Apache-2.0 license text — pypdfium2 is
                             Apache-2.0; the bundled PDFium library is
                             BSD-3-Clause (its text also ships inside the
                             wheel's dist-info/licenses/LICENSES/ folder,
                             which BUILD.bat copies for every build).
                             https://github.com/pypdfium2-team/pypdfium2

Python runtime — full license file
----------------------------------
  Python-LICENSE.txt         The FULL CPython license file: the
                             "HISTORY OF THE SOFTWARE" table plus the complete
                             PSF License Version 2, BeOpen, CNRI and CWI
                             agreements — verbatim from the CPython 3.13
                             installation (repo reference snapshot). For
                             every build BUILD.bat overwrites this file inside
                             the package with the exact LICENSE.txt of the
                             build runtime itself (read from the venv's base
                             prefix), so a 3.11 / 3.12 / 3.13 build each ships
                             its own text. Complements Python-PSF-2.0.txt,
                             which carries only the canonical PSF-2.0 wording.
                             The native libraries that travel with the
                             embedded Python runtime (OpenSSL, libffi, sqlite3,
                             zlib, liblzma) are listed in
                             THIRD_PARTY_NOTICES.md §6.
                             https://www.python.org

PyInstaller — build tool (bootloader + runtime hooks travel in the package)
---------------------------------------------------------------------------
  PyInstaller-COPYING.txt    The verbatim "The PyInstaller licensing terms"
                             file (COPYING.txt) from the pyinstaller 6.22.3
                             source distribution — the wheel's dist-info
                             carries NO license file (verified), so the
                             canonical text is tracked in this repo. Contents:
                             GPL-2.0-or-later for the tool and the bootloader
                             WITH the Bootloader Exception (allows embedding
                             and redistributing the compiled bootloader in
                             any program), plus the Apache-2.0 grant for the
                             run-time hooks (e.g. pyi_rth_pyside6.py) and the
                             run-time modules (pyi_splash, _pyi_rth_utils)
                             that are embedded in every PyInstaller package.
                             https://pyinstaller.org
                             https://github.com/pyinstaller/pyinstaller

For every build BUILD.bat also copies the exact dist-info license files of
THIS build's twelve core wheels (py7zr, rarfile, pypdfium2, pycryptodomex,
pyppmd, pybcj, inflate64, multivolumefile, texttable, brotli, psutil and
backports.zstd) into the package as licenses/<package>/, so the shipped texts always match the shipped
binaries. This step is mandatory, not best effort: if any of
the expected license files or <package> folders is missing from the package,
BUILD.bat aborts with an error instead of shipping a half-documented zip.
