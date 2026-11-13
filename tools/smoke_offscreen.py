#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""اسموکِ UI بدون نمایشگر — پنجره ساخته می‌شود، ریشه‌ی دمو اسکن می‌شود،
جزئیات باز و بسته می‌شود؛ هیچ نمایشگری لازم نیست (QT_QPA_PLATFORM=offscreen).

استفاده از ریشه‌ی ریپو:
    python tools/smoke_offscreen.py .
همه‌ی نوشتن‌ها (کش، دیتابیس، ‎.covers) داخل یک پوشه‌ی موقت انجام می‌شود —
هیچ‌چیز داخل ریپو یا پوشه‌ی کاربر دست نمی‌خورد.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    sys.path.insert(0, str(repo))

    from PySide6.QtWidgets import QApplication

    import main as app_main
    from scanner import scan_roots
    from store import Store

    app = QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="folderdex-smoke-"))
    try:
        # کش/دیتابیس در پوشه‌ی موقت — نه کنار سورس
        app_main.CACHE_DIR = tmp / ".thumb_cache"

        # ریشه‌ی دمو: دو عنوان، یکی با پوستر واقعی (آیکونِ خود برنامه)
        demo = tmp / "demo"
        (demo / "Movie A").mkdir(parents=True)
        (demo / "Movie B").mkdir()
        icon = repo / "assets" / "icon.png"
        if icon.is_file():
            shutil.copy(str(icon), str(demo / "Movie A" / "poster.png"))
        (demo / "Movie B" / "readme.txt").write_text("demo", encoding="utf-8")

        store = Store(tmp / "config.json")
        store.set_roots([{"path": str(demo), "category": ""}])

        w = app_main.MainWindow(store)
        items = scan_roots(store.roots(),
                           include_image_fallback=store.include_image_fallback)
        assert items, "scan of the demo root produced no items"
        w.on_scan_finished((items, None))
        app.processEvents()

        # صفحه‌ی جزئیات باز شود (پوستر + hero + بخش فایل‌ها)
        w.details.set_item(items[0])
        w.stack.setCurrentWidget(w.details)
        app.processEvents()
        for _ in range(3):                     # چند دورِ رویداد برای کارهای صف‌شده
            app.processEvents()

        w.close()
        store.close()
        print(f"[ok] smoke: {len(items)} items scanned; "
              "MainWindow constructed; details opened and closed; offscreen")
        return 0
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
