# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""مسیرهای قابل‌حمل — برای اجرای سورس و exe (PyInstaller) بدون پیش‌نیاز.

برنامه باید روی هر ویندوزی بدون نصب چیزی کار کند: باینری‌های همراه (مثل
bin/ffmpeg.exe) و بسته‌های vendor/ کنار خود برنامه (یا در sys._MEIPASS وقتی
به exe تبدیل شد) پیدا می‌شوند.
"""
from __future__ import annotations

import sys
from pathlib import Path
from watchtime import dbg_log


def app_dir() -> Path:
    """پوشه‌ی برنامه — کنار exe در حالت فریز‌شده، کنار سورس در حالت عادی."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(rel: str) -> Path:
    """مسیر یک فایل همراه برنامه — قبل از همه در sys._MEIPASS (بسته‌ی PyInstaller)،
    بعد کنار exe/سورس."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / rel
    return app_dir() / rel


def bundled_binary(name: str) -> str | None:
    """باینری همراه برنامه (مثل ffmpeg.exe) — اولویت با bin/ کنار exe و بسته.

    روی غیرویندوز فایل‌های .exe همراه معنا ندارند (قابل‌اجرا نیستند) — گذر از
    آن‌ها باعث می‌شود تست‌های لینوکسی به ابزار نصب‌شده‌ی سیستم (7z/ffmpeg) برسند.
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
