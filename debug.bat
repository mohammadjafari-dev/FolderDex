@echo off
@rem SPDX-License-Identifier: GPL-3.0-or-later
@rem SPDX-FileCopyrightText: 2026 Mohammad Jafari
REM Debug run - keeps console open to show errors
cd /d "%~dp0"
.venv\Scripts\python.exe main.py
if not "%errorlevel%"=="0" (
  echo.
  echo [error] The app exited with code %errorlevel%.
  echo         A negative code means the .venv itself is broken: delete the
  echo         .venv folder and run run.bat again so it is rebuilt.
)
pause
