# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Custom cover actions — shared between the card menu and the details page.

Normalizing the user's image (read/decode/WebP encode) never happens on the
UI thread: QFileDialog takes the choice, CoverImportWorker runs in a QThread,
and the result/error comes back to the UI thread via signals. on_done(changed)
is always called on the UI thread.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import covers
from i18n import T
from portable import app_dir
from watchtime import dbg_log

# Last folder the user picked an image from — memory of this session only
_last_dir = ""


class _CoverImportWorker(QThread):
    """import_cover on a separate thread — WebP decode/encode is forbidden on the UI thread."""

    ok = Signal(object)          # info dict from covers.import_cover
    fail = Signal(str)

    def __init__(self, src: Path, dest_dir: Path, parent=None):
        super().__init__(parent)
        self._src = src
        self._dest = dest_dir

    def run(self):
        try:
            self.ok.emit(covers.import_cover(self._src, self._dest))
        except covers.CoverError as e:
            self.fail.emit(str(e))
        except OSError as e:
            # full disk / write permission — must not be mistaken for a bad image
            dbg_log(f"_CoverImportWorker OSError: {type(e).__name__}")
            self.fail.emit(T(covers.WRITE_ERR_TEXT))
        except Exception as e:
            dbg_log(f"_CoverImportWorker failed: {type(e).__name__}")
            self.fail.emit(T(covers.READ_ERR_TEXT))


def _run_import(parent, store, item, src: Path, on_done) -> None:
    """Runs the import worker with a wait cursor and applies the result."""
    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(Qt.CursorShape.WaitCursor)
    worker = _CoverImportWorker(src, app_dir(), parent)
    # QThread.finished → deleteLater = the official Qt pattern. The
    # _finish/_bad slots used to deleteLater directly; the ok/fail signal is
    # emitted from the *end* of run() but the slot runs on the UI thread, so
    # the deleteLater could be processed before the worker thread fully
    # finished → "QThread: Destroyed while thread is still running" → qFatal
    # and the whole app goes down.
    worker.finished.connect(worker.deleteLater)
    # Strong reference so Python does not GC the running worker
    parent._cover_worker = worker          # type: ignore[attr-defined]

    def _finish(ok_info):
        if app is not None:
            app.restoreOverrideCursor()
        if ok_info is not None:
            try:
                store.set_custom_poster(str(item.path), ok_info)
                changed = True
            except Exception as e:
                # a meta save failure (e.g. a SQLite lock) must not get
                # the wrong "invalid image" message — the user's image is
                # fine; separate text + log
                dbg_log(f"pick_cover.apply failed: {type(e).__name__}")
                QMessageBox.warning(parent, T("کاور سفارشی"),
                                    T("ذخیره‌ی اطلاعاتِ کاور ممکن نشد."))
                changed = False
        else:
            changed = False
        if ok_info is not None and changed and ok_info.get("degraded"):
            # the animation did not fit under the cap — a static cover
            # (first frame) was saved
            QMessageBox.information(parent, T("کاور سفارشی"),
                                    T(covers.DEGRADED_MSG_TEXT))
        parent._cover_worker = None           # type: ignore[attr-defined]
        on_done(changed)

    def _ok(info):
        _finish(info if isinstance(info, dict) else None)

    def _bad(msg: str):
        if app is not None:
            app.restoreOverrideCursor()
        QMessageBox.warning(parent, T("کاور سفارشی"), str(msg))
        parent._cover_worker = None           # type: ignore[attr-defined]
        on_done(False)

    worker.ok.connect(_ok)
    worker.fail.connect(_bad)
    worker.start()


def pick_cover(parent, store, item, on_done) -> None:
    """File-open dialog + normalization on a separate thread + UI update.

    on_done(changed: bool) is called on the UI thread.
    """
    global _last_dir
    start = _last_dir or str(Path.home())
    fname, _ = QFileDialog.getOpenFileName(
        parent, T("🖼 انتخاب کاور…"), start,
        T("تصویر") + " (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
    if not fname:
        on_done(False)
        return
    _last_dir = str(Path(fname).parent)
    _run_import(parent, store, item, Path(fname), on_done)


def clear_cover(parent, store, item, on_done) -> None:
    """Confirm + remove the custom cover and return to the folder icon."""
    from PySide6.QtWidgets import QMessageBox as _QMB
    ans = _QMB.question(
        parent, T("کاور سفارشی"),
        T("کاور سفارشی حذف شود و به آیکونِ پوشه برگردد؟"),
        _QMB.StandardButton.Yes | _QMB.StandardButton.No,
        _QMB.StandardButton.No)
    if ans != _QMB.StandardButton.Yes:
        on_done(False)
        return
    changed = False
    try:
        store.clear_custom_poster(str(item.path))
        changed = True
    except Exception as e:
        dbg_log(f"clear_cover failed: {type(e).__name__}")
    on_done(changed)


def open_covers_folder(parent) -> None:
    """Opens the .covers/ folder in the file manager (creates it if missing)."""
    from ui.widgets import open_path
    d = covers.covers_dir()               # the single source of truth for the covers path
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        dbg_log(f"open_covers_folder failed: {type(e).__name__}")
    open_path(d)
