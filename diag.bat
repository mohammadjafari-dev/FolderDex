@echo off
setlocal
@rem SPDX-License-Identifier: GPL-3.0-or-later
@rem SPDX-FileCopyrightText: 2026 Mohammad Jafari
REM FolderDex diagnostics - run when the app does not start.
REM Writes folderdex-diag.log next to this file and opens it at the end.
REM Send that file back for analysis. ASCII only (Persian breaks cmd.exe).
chcp 65001 >nul
cd /d "%~dp0"
set "DIAG=%~dp0folderdex-diag.log"

echo Collecting diagnostics, please wait...
echo FolderDex launcher diagnostics > "%DIAG%"
echo ================================ >> "%DIAG%"
echo date: %date% %time% >> "%DIAG%"
echo folder: %CD% >> "%DIAG%"

echo. >> "%DIAG%"
echo --- [1] Python on this machine --- >> "%DIAG%"
where py >> "%DIAG%" 2>&1
where python >> "%DIAG%" 2>&1
py -0p >> "%DIAG%" 2>&1

echo. >> "%DIAG%"
echo --- [2] .venv state --- >> "%DIAG%"
if exist .venv\Scripts\python.exe (echo venv python.exe: present >> "%DIAG%") else (echo venv python.exe: MISSING >> "%DIAG%")
if exist .venv\pyvenv.cfg type .venv\pyvenv.cfg >> "%DIAG%"
.venv\Scripts\python.exe --version >> "%DIAG%" 2>&1
echo [2a] venv python.exe exitcode: %errorlevel% >> "%DIAG%"
.venv\Scripts\pythonw.exe -c "print('pythonw-ok')" >> "%DIAG%" 2>&1
echo [2b] pythonw.exe exitcode: %errorlevel% >> "%DIAG%"
echo note: a NEGATIVE exitcode like -1073741515 means the venv is broken >> "%DIAG%"
echo note: a NEGATIVE exitcode like -1073741515 means the venv is broken

echo. >> "%DIAG%"
echo --- [3] imports inside the venv --- >> "%DIAG%"
.venv\Scripts\python.exe -c "import sys; print(sys.version)" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "import PySide6; print('PySide6', PySide6.__version__)" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "import PIL; print('Pillow', PIL.__version__)" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "import py7zr, rarfile, pypdfium2; print('thumbnail backends (7z/rar/pdf): OK')" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "import imgcache, tempfile, pathlib; from PIL import Image; d=pathlib.Path(tempfile.mkdtemp()); p=imgcache.save_cache_image(Image.new('RGB',(64,64),(10,20,30)), imgcache.cache_file(d,'selftest')); print('cache format:', p.suffix, '| wrote', p.name, '| readback', imgcache.read_cache_pil(p).size)" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "import main; print('import main: OK')" >> "%DIAG%" 2>&1
.venv\Scripts\python.exe -c "from PySide6.QtWidgets import QApplication; a=QApplication([]); print('QApplication: OK')" >> "%DIAG%" 2>&1

echo. >> "%DIAG%"
echo --- [4] launcher log (folderdex-launcher.log) --- >> "%DIAG%"
if exist folderdex-launcher.log type folderdex-launcher.log >> "%DIAG%"
if exist covershelf-launcher.log type covershelf-launcher.log >> "%DIAG%"

echo. >> "%DIAG%"
echo --- [5] app error log (folderdex-error.log) --- >> "%DIAG%"
if exist folderdex-error.log type folderdex-error.log >> "%DIAG%"
if exist covershelf-error.log type covershelf-error.log >> "%DIAG%"

echo. >> "%DIAG%"
echo --- [6] app debug log, last 40 lines --- >> "%DIAG%"
if exist folderdex-debug.log powershell -NoProfile -Command "Get-Content -LiteralPath 'folderdex-debug.log' -Tail 40 -Encoding UTF8" >> "%DIAG%" 2>&1
if exist covershelf-debug.log powershell -NoProfile -Command "Get-Content -LiteralPath 'covershelf-debug.log' -Tail 40 -Encoding UTF8" >> "%DIAG%" 2>&1

echo.
echo ============ DONE ============
echo Everything was written to:
echo   %DIAG%
echo Send that file back for analysis. Opening it now...
start "" notepad "%DIAG%"
pause
exit /b 0
