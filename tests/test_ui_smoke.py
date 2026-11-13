# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Headless UI regression test (round 11).

Every GUI bug in the changelog was found by users after the release — this
smoke test at least proves the window still CONSTRUCTS, a demo root still
SCANS, and the details page still OPENS/CLOSES on every push. It needs
PySide6 (skipped automatically where Qt is not installed, e.g. the
repo-integrity job) and runs fully offscreen; all writes stay in tmp_path.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402
from scanner import scan_roots  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _demo_root(tmp: Path) -> Path:
    demo = tmp / "demo"
    (demo / "Movie A").mkdir(parents=True)
    (demo / "Movie B").mkdir()
    icon = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
    if icon.is_file():
        shutil.copy(str(icon), str(demo / "Movie A" / "poster.png"))
    (demo / "Movie B" / "readme.txt").write_text("demo", encoding="utf-8")
    return demo


def test_main_window_smoke(qapp, tmp_path, monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="folderdex-uis-"))
    monkeypatch.setattr(app_main, "CACHE_DIR", tmp / ".thumb_cache")
    demo = _demo_root(tmp)
    store = Store(tmp / "config.json")
    try:
        store.set_roots([{"path": str(demo), "category": ""}])
        w = app_main.MainWindow(store)
        items = scan_roots(store.roots(),
                           include_image_fallback=store.include_image_fallback)
        assert items, "demo scan produced no items"
        w.on_scan_finished((items, None))
        qapp.processEvents()
        w.details.set_item(items[0])
        w.stack.setCurrentWidget(w.details)
        qapp.processEvents()
        w.close()
        qapp.processEvents()
    finally:
        store.close()
        shutil.rmtree(str(tmp), ignore_errors=True)
