#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Headless UI smoke test — builds the window, scans the demo root, opens and
closes the details page; no display required (QT_QPA_PLATFORM=offscreen).

Usage on a repo root:
    python tools/smoke_offscreen.py .
All writes (cache, database, .covers) go into a temporary folder — nothing
inside the repo or the user's folders is touched.
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
        # cache/database in the temp folder — not next to the source
        app_main.CACHE_DIR = tmp / ".thumb_cache"

        # demo root: two titles, one with a real poster (the app's own icon)
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
        w.on_scan_finished((items, None, {}))
        app.processEvents()

        # open the details page (poster + hero + files section)
        w.details.set_item(items[0])
        w.stack.setCurrentWidget(w.details)
        app.processEvents()
        for _ in range(3):                     # a few event-loop turns for queued work
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
