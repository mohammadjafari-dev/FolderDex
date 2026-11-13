@echo off
@rem SPDX-License-Identifier: GPL-3.0-or-later
@rem SPDX-FileCopyrightText: 2026 Mohammad Jafari
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title FolderDex - Build portable package
chcp 65001 >nul 2>&1

REM ============================================================================
REM  FolderDex - BUILD.bat
REM
REM  Builds a fully PORTABLE, zero-prerequisite Windows package:
REM
REM      dist\FolderDex\FolderDex.exe   (+ _internal\ runtime files)
REM      FolderDex-win64-portable.zip
REM
REM  The OUTPUT runs on any Windows 10/11 x64 with NOTHING installed
REM  (Python runtime, 7-Zip and the PDF library are bundled inside).
REM
REM  Build-time needs (only on the build machine, not on end users'):
REM    - Python 3.11, 3.12 or 3.13 x64 on PATH  (or the "py" launcher)
REM    - Internet (pip downloads PyInstaller + build deps once)
REM
REM  Usage:   BUILD.bat              -> one-dir build (the only supported mode)
REM           BUILD.bat /with-ffmpeg -> ALSO bundle ffmpeg.exe (see below)
REM
REM  Why no /onefile anymore? PySide6/Qt is LGPL-3.0; a one-file exe makes it
REM  practically impossible for a user to REPLACE the Qt libraries as the LGPL
REM  allows. One-dir keeps them replaceable in _internal\ - so we ship one-dir
REM  only. (And one-dir starts faster anyway.)
REM
REM  ffmpeg is NOT bundled by default. Pass /with-ffmpeg to bundle it: then a
REM  ffmpeg-LICENSE.txt must sit NEXT TO the ffmpeg.exe on PATH (BUILD aborts
REM  without it) - the license of your ffmpeg build (LGPL or GPL) is yours to
REM  verify, see THIRD_PARTY_NOTICES.md section 6.
REM ============================================================================

set "APPNAME=FolderDex"
REM Official project repository - this URL is written into the package's
REM SOURCE.txt and GPL requires recipients to be able to reach the source.
REM All references (README, notices, SOURCE.txt) always point here, so moving
REM the code anywhere else never breaks a link:
REM *** The URL below MUST be PUBLIC and REACHABLE before any zip built
REM *** here is distributed - SOURCE.txt inside the zip points to it
REM *** (GPL source offer). Check it in a fresh browser session first.
set "REPO_URL=https://github.com/mohammadjafari-dev/FolderDex"
set "VENV=.build-venv"
set "STAGING=build\_bin_staging"
set "MODE=--onedir"

REM ---- parse flags -----------------------------------------------------------
set "WITH_FFMPEG=0"
for %%A in (%*) do (
    if /i "%%~A"=="/onefile" (
        echo [ERROR] /onefile is no longer supported. PySide6/Qt is LGPL-3.0 and a
        echo         single-file exe makes replacing the Qt libraries impossible.
        echo         One-dir builds keep them replaceable - this is intentional.
        pause
        exit /b 1
    )
    if /i "%%~A"=="/with-ffmpeg" set "WITH_FFMPEG=1"
)

echo.
echo === [1/7] Finding Python (3.11+ x64 required for build) ===
set "PY="
where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%V in ('python -c "import sys;print(f'{sys.version_info.major}{sys.version_info.minor}')" 2^>nul') do set "PYVER=%%V"
) else (
    set "PYVER=00"
)
if not defined PYVER set "PYVER=00"
if !PYVER! GEQ 311 (
    set "PY=python"
) else (
    py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
    if not defined PY py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
    if not defined PY py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
    echo [ERROR] Python 3.11+ not found. Install it from https://www.python.org
    echo         and re-run BUILD.bat ^(build-time only; the package needs nothing^).
    pause
    exit /b 1
)
for /f "delims=" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do echo Using Python %%V
REM Window title carries the real APP_VERSION read from appinfo.py (never stale):
for /f "delims=" %%V in ('%PY% -c "from appinfo import APP_VERSION;print(APP_VERSION)" 2^>nul') do title FolderDex - Build portable package (v%%V)

echo.
echo === [2/7] Preparing clean build venv: %VENV% ===
if exist "%VENV%" rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%" || (echo [ERROR] venv creation failed & pause & exit /b 1)
set "PYP=%VENV%\Scripts\python.exe"
"%PYP%" -m pip install --upgrade pip --quiet
"%PYP%" -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo [ERROR] installing requirements.txt failed & pause & exit /b 1 )
REM Build tool only - all thumbnail backends (pypdfium2 / py7zr / rarfile)
REM are core requirements.txt entries now, installed above:
"%PYP%" -m pip install --quiet pyinstaller
if errorlevel 1 ( echo [ERROR] installing build tools failed & pause & exit /b 1 )
echo Build venv ready.

echo.
echo === [3/7] Generating app icon (assets\icon.png -^> build\icon.ico) ===
if not exist build mkdir build
"%PYP%" -c "from PIL import Image; im=Image.open('assets/icon.png').convert('RGBA'); im.save('build/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if errorlevel 1 ( echo [ERROR] icon generation failed ^(Pillow missing?^) & pause & exit /b 1 )
echo Icon ready.

echo.
echo === [4/7] Staging bundled binaries (7za.exe + ffmpeg only on /with-ffmpeg) ===
if exist "%STAGING%" rmdir /s /q "%STAGING%"
mkdir "%STAGING%"
copy /y "bin\7za.exe" "%STAGING%\" >nul
copy /y "bin\7zip-License.txt" "%STAGING%\" >nul
copy /y "bin\README-7zip.txt" "%STAGING%\" >nul
if not "%WITH_FFMPEG%"=="1" (
    echo ffmpeg NOT bundled ^(default^) - video thumbnails on end-user machines
    echo                    will use Windows shell thumbnails instead.
    goto ffmpeg_done
)
REM /with-ffmpeg: bundle ffmpeg.exe from PATH - but ONLY with its license text
set "FFEXE="
for /f "delims=" %%F in ('where ffmpeg.exe 2^>nul') do (
    if not defined FFEXE set "FFEXE=%%~fF"
)
if not defined FFEXE (
    echo [ERROR] /with-ffmpeg given but ffmpeg.exe was not found on PATH.
    echo         Put ffmpeg.exe on PATH and re-run, or build without the flag.
    pause
    exit /b 1
)
set "FFDIR="
for %%F in ("%FFEXE%") do set "FFDIR=%%~dpF"
if exist "%FFDIR%ffmpeg-LICENSE.txt" (
    copy /y "%FFEXE%" "%STAGING%\ffmpeg.exe" >nul
    copy /y "%FFDIR%ffmpeg-LICENSE.txt" "%STAGING%\" >nul
    echo ffmpeg.exe bundled: %STAGING%\ffmpeg.exe  ^(with its ffmpeg-LICENSE.txt^)
) else (
    echo [ERROR] ffmpeg-LICENSE.txt was NOT found next to: %FFEXE%
    echo         Copy the license file from the ffmpeg distribution you use
    echo         ^(its license depends on the build: LGPL or GPL^) next to the
    echo         exe and re-run. Bundling a binary without its license text is
    echo         not something this script will do.
    pause
    exit /b 1
)
:ffmpeg_done

echo.
echo === [5/7] Running PyInstaller (%MODE%) - this takes 1-3 minutes ===
"%PYP%" -m PyInstaller --noconfirm --clean %MODE% --windowed ^
  --name %APPNAME% ^
  --icon build\icon.ico ^
  --add-data "assets;assets" ^
  --add-data "%STAGING%;bin" ^
  --add-data "README.md;." ^
  --collect-all pypdfium2 ^
  --hidden-import py7zr ^
  --hidden-import rarfile ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtTest ^
  --exclude-module tkinter ^
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 ^
  main.py
if errorlevel 1 ( echo [ERROR] PyInstaller failed. Scroll up for the reason. & pause & exit /b 1 )

echo.
echo === [5b/7] Verifying bundled native pieces ===
REM A broken package must never ship silently: without the PDFium DLL the
REM PDF thumbnails fall back to icons, and without Pillow-WebP the
REM thumbnail cache degrades to PNG.
dir /s /b "dist\%APPNAME%\_internal\pdfium*.dll" >nul 2>nul || (
  echo [ERROR] PDFium DLL missing from the package - PDF thumbnails would fall back to icons.
  pause & exit /b 1
)
"%VENV%\Scripts\python.exe" -c "from PIL import features; assert features.check('webp'), 'Pillow without WebP'; print('[ok] Pillow WebP codec present')"
if errorlevel 1 ( echo [ERROR] The bundled Pillow has no WebP support - the thumbnail cache would be PNG-only. & pause & exit /b 1 )
echo [ok] PDFium DLL + Pillow WebP verified inside the package.

echo.
echo === [6/7] Package notes + license files (GPL requirement) ===
REM config.json is created by the app on first run NEXT TO the exe (portable layout),
REM so nothing is written to %APPDATA% and the folder is fully self-contained.
REM Any distributed package must also carry the license, third-party notices
REM and a pointer to the source repository:
for /f "delims=" %%V in ('%PY% -c "from appinfo import APP_VERSION;print(APP_VERSION)" 2^>nul') do set "APPVER=%%V"
REM Fallback only if APP_VERSION could not be read from appinfo.py —
REM keep it in sync with the released 0.n.m version.
if not defined APPVER set "APPVER=0.52.0"
set "PKGDIR=dist\%APPNAME%"
if not exist "%PKGDIR%" mkdir "%PKGDIR%"
copy /y "LICENSE" "%PKGDIR%\" >nul
copy /y "README.md" "%PKGDIR%\" >nul
copy /y "README.fa.md" "%PKGDIR%\" >nul
copy /y "THIRD_PARTY_NOTICES.md" "%PKGDIR%\" >nul
if exist "licenses" xcopy /y /i "licenses" "%PKGDIR%\licenses" >nul
REM License texts shipped by the wheels of the thumbnail backends and of
REM py7zr's transitive dependencies (L2: pyppmd/pybcj/inflate64/
REM multivolumefile are LGPL-2.1+ and DO travel inside the portable bundle,
REM so their notices must travel too; texttable/brotli MIT, psutil BSD-3,
REM backports_zstd PSF-2.0). Exact texts of THIS build - see
REM THIRD_PARTY_NOTICES.md section 5. NOTE: dist-info folder names are the
REM NORMALIZED package names (backports.zstd -> backports_zstd), and some
REM wheels place LICENSE/COPYING at the dist-info ROOT instead of a
REM licenses\ subfolder - both shapes are copied:
for %%P in ("py7zr" "rarfile" "pypdfium2" "pycryptodomex" "pyppmd" "pybcj" "inflate64" "multivolumefile" "texttable" "brotli" "psutil" "backports_zstd") do (
    set "PKG_DI="
    for /d %%D in ("%VENV%\Lib\site-packages\%%~P-*.dist-info") do set "PKG_DI=%%~fD"
    if defined PKG_DI (
        if not exist "%PKGDIR%\licenses\%%~P" mkdir "%PKGDIR%\licenses\%%~P"
        if exist "!PKG_DI!\licenses" xcopy /y /i "!PKG_DI!\licenses" "%PKGDIR%\licenses\%%~P" >nul
        if exist "!PKG_DI!\LICENSE" copy /y "!PKG_DI!\LICENSE" "%PKGDIR%\licenses\%%~P\" >nul
        if exist "!PKG_DI!\LICENSE.txt" copy /y "!PKG_DI!\LICENSE.txt" "%PKGDIR%\licenses\%%~P\" >nul
        if exist "!PKG_DI!\COPYING*" copy /y "!PKG_DI!\COPYING*" "%PKGDIR%\licenses\%%~P\" >nul
        echo %%~P license files processed from the build venv ^(exact version^).
    )
)
REM The Pillow wheel's own LICENSE (MIT-CMU + bundled codecs) - exact text of
REM THIS build's Pillow version. PySide6 wheels carry no license file inside
REM their dist-info (verified), so the canonical LGPL-3.0 / PSF-2.0 texts ship
REM from the repo's licenses\ folder (already copied above):
set "PILLOW_DI="
for /d %%D in ("%VENV%\Lib\site-packages\pillow-*.dist-info") do set "PILLOW_DI=%%~fD"
if defined PILLOW_DI if exist "%PILLOW_DI%\licenses\LICENSE" (
    if not exist "%PKGDIR%\licenses\Pillow" mkdir "%PKGDIR%\licenses\Pillow"
    copy /y "%PILLOW_DI%\licenses\LICENSE" "%PKGDIR%\licenses\Pillow\LICENSE" >nul
    echo Pillow license copied from the build venv ^(exact version^).
)
> "%PKGDIR%\SOURCE.txt" echo %APPNAME% %APPVER% - source code: %REPO_URL%
>> "%PKGDIR%\SOURCE.txt" echo License: GNU GPL v3 or later - see LICENSE and THIRD_PARTY_NOTICES.md in this folder.
>> "%PKGDIR%\SOURCE.txt" echo Bundled third-party license texts: THIRD_PARTY_NOTICES.md + the licenses folder.
if exist "%PKGDIR%\bin\ffmpeg.exe" (
>> "%PKGDIR%\SOURCE.txt" echo ffmpeg is bundled - its license text is in bin\ffmpeg-LICENSE.txt and its source is at https://www.ffmpeg.org
) else if exist "%PKGDIR%\_internal\bin\ffmpeg.exe" (
>> "%PKGDIR%\SOURCE.txt" echo ffmpeg is bundled - its license text is in _internal\bin\ffmpeg-LICENSE.txt and its source is at https://www.ffmpeg.org
)
echo One-dir mode: dist\%APPNAME%\%APPNAME%.exe

echo.
echo === [7/7] Zipping portable package via PowerShell ===
set "ZIPNAME=%APPNAME%-win64-portable.zip"
if exist "%ZIPNAME%" del /q "%ZIPNAME%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%APPNAME%' -DestinationPath '%ZIPNAME%' -Force"
if errorlevel 1 (
    echo [WARN] PowerShell Compress-Archive failed - you can zip dist\%APPNAME% manually.
) else (
    echo Created: %ZIPNAME%
)

echo.
echo ============================================================================
echo  DONE. Distribute:
echo    * %ZIPNAME%               (users unzip ^& run %APPNAME%\%APPNAME%.exe^)
echo  Notes:
echo    - Unzip somewhere WRITABLE ^(not Program Files^) or on an external drive.
echo    - First run of a new/un-signed exe may show SmartScreen once:
echo      "More info" -^> "Run anyway". This is normal for un-signed binaries.
echo    - To uninstall: just delete the folder. Nothing else is written anywhere.
if "%WITH_FFMPEG%"=="1" (
echo    - ffmpeg IS bundled: its license depends on the build you used
echo      ^(LGPL or GPL^) - THIRD_PARTY_NOTICES.md section 6 and bin\ffmpeg-LICENSE.txt.
) else (
echo    - ffmpeg is NOT bundled - fine for most users; Windows shell thumbnails
echo      are used. Build with /with-ffmpeg if you want it embedded.
)
echo ============================================================================

pause
endlocal
