@echo off
setlocal
@rem SPDX-License-Identifier: GPL-3.0-or-later
@rem SPDX-FileCopyrightText: 2026 Mohammad Jafari
REM FolderDex launcher (ASCII only - Persian text breaks cmd.exe parsing)
REM Every step is logged to folderdex-launcher.log and every failure stops
REM with a visible message. If this window still closes at once, run diag.bat.
cd /d "%~dp0"
set "LOG=%~dp0folderdex-launcher.log"
echo [launcher] ---- start %date% %time% ---- > "%LOG%"

REM --- 1) venv: missing or broken? ---
REM NOTE: a .venv left over from an uninstalled/upgraded Python exits with a
REM NEGATIVE Windows code. The string compare "if not %errorlevel%==0" used
REM below catches negative codes on purpose; "if errorlevel 1" would not.
.venv\Scripts\python.exe -c "print('venv-ok')" >nul 2>nul
if "%errorlevel%"=="0" goto venv_ok
echo [setup] The .venv is missing or its Python does not run - recreating it...
echo [setup] venv broken/missing - recreating it >> "%LOG%"
if exist .venv rmdir /s /q .venv
echo [setup] Creating virtual environment...
(py -3 -m venv .venv) 2>nul || python -m venv .venv

:venv_ok
.venv\Scripts\python.exe -c "print('venv-ok')" >nul 2>nul
if not "%errorlevel%"=="0" goto fail_venv
echo [launcher] venv ok >> "%LOG%"

REM --- 2) requirements ---
REM PySide6 + Pillow (+WebP codec) are HARD requirements - without them
REM nothing works, so a pip failure always stops here with a message.
REM py7zr / rarfile / pypdfium2 are core requirements too, but on a
REM permanently offline machine the retry must not run on EVERY launch:
REM a failed pip writes the marker file .requirements_failed next to this
REM script, later launches skip pip until it succeeds once or the marker
REM is deleted - the app still starts (archive/PDF thumbnails are skipped).
.venv\Scripts\python.exe -c "import PySide6, PIL, PIL.features; assert PIL.features.check('webp')" >nul 2>nul
if not "%errorlevel%"=="0" goto do_pip
.venv\Scripts\python.exe -c "import py7zr, rarfile, pypdfium2" >nul 2>nul
if "%errorlevel%"=="0" goto reqs_ok
if exist .requirements_failed goto skip_pip

:do_pip
echo [setup] Installing requirements from PyPI (needs internet, ~100MB)...
echo [setup] installing requirements from PyPI >> "%LOG%"
.venv\Scripts\python.exe -m pip --disable-pip-version-check install -r requirements.txt
if not "%errorlevel%"=="0" (
    type nul > .requirements_failed
    goto fail_pip
)
if exist .requirements_failed del .requirements_failed
goto reqs_ok

:skip_pip
echo [warn] Archive/PDF backends are missing - archive and PDF thumbnails will be skipped.
echo [warn] The app will still start. To retry the install, delete the file
echo [warn] .requirements_failed next to this script and run it again.
echo [launcher] requirements incomplete - archive/PDF backends missing >> "%LOG%"

:reqs_ok
echo [launcher] requirements ok >> "%LOG%"

REM --- 3) launch the GUI without a console window ---
start "FolderDex" .venv\Scripts\pythonw.exe main.py
echo [launcher] pythonw started - waiting 5s to verify it stays alive >> "%LOG%"
timeout /t 5 /nobreak >nul
powershell -NoProfile -Command "if (-not (Get-CimInstance Win32_Process -Filter 'Name=''pythonw.exe''' | Where-Object { $_.CommandLine -like '*main.py*' })) { exit 1 }" >nul 2>nul
if not "%errorlevel%"=="0" goto fail_launch
echo [launcher] app is running >> "%LOG%"
exit /b 0

:fail_venv
echo.
echo [error] Could not create a working virtual environment.
echo         Make sure Python 3.11+ is installed and on PATH, then run again.
echo         If it still fails, delete the .venv folder and run once more.
echo [error] venv still broken after repair attempt >> "%LOG%"
pause
exit /b 1

:fail_pip
echo.
echo [error] pip install failed.
echo         Check your internet connection (PyPI may need VPN) and run again.
echo         Manual retry:
echo           .venv\Scripts\python.exe -m pip install -r requirements.txt
echo [error] pip install failed >> "%LOG%"
pause
exit /b 1

:fail_launch
echo.
echo [error] The app exited right after launch - pythonw hides the reason.
echo         Run diag.bat and send folderdex-diag.log back for analysis,
echo         or run debug.bat to see the error directly in a console.
echo [error] pythonw died right after start >> "%LOG%"
pause
exit /b 1
