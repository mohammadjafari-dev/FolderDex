# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Portable paths — for running from source and from the exe (PyInstaller)
without any prerequisites.

The app must work on any Windows machine without installing anything:
bundled binaries (e.g. bin/ffmpeg.exe) and vendor/ packages are found next
to the app itself (or in sys._MEIPASS once frozen into an exe).
"""
from __future__ import annotations

import sys
from pathlib import Path
from watchtime import dbg_log


def app_dir() -> Path:
    """App folder — next to the exe when frozen, next to the source otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(rel: str) -> Path:
    """Path of a bundled resource file — first in sys._MEIPASS (PyInstaller
    bundle), then next to the exe/source."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / rel
    return app_dir() / rel


def bundled_binary(name: str) -> str | None:
    """Bundled binary (e.g. ffmpeg.exe) — bin/ next to the exe/package wins.

    On non-Windows, bundled .exe files are meaningless (not executable) —
    skipping them lets the Linux tests fall through to the system-installed
    tools (7z/ffmpeg).
    """
    if sys.platform != "win32" and name.lower().endswith(".exe"):
        return None
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    bases.append(app_dir())
    for base in bases:
        try:
            p = base / "bin" / name
            if p.exists():
                return str(p)
        except Exception as _exc_dbg:
            dbg_log(f"bundled_binary failed: {type(_exc_dbg).__name__}")
            continue
    return None
