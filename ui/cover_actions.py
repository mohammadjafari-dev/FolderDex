# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""عملیاتِ کاورِ سفارشی — مشترک بین منوی کارت و صفحه‌ی جزئیات.

نرمال‌سازیِ عکسِ کاربر (خواندن/دیکد/انکد WebP) هرگز روی نخِ UI انجام نمی‌شود:
QFileDialog انتخاب را می‌گیرد، CoverImportWorker در QThread کار می‌کند و
نتیجه/خطا با سیگنال به نخِ UI برمی‌گردد. on_done(changed) همیشه در نخِ UI صدا
زده می‌شود.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import covers
from i18n import T
from portable import app_dir
from watchtime import dbg_log

# آخرین پوشه‌ای که کاربر از آن عکس انتخاب کرد — فقط در حافظه‌ی همین نشست
_last_dir = ""


class _CoverImportWorker(QThread):
    """import_cover در نخِ جدا — دیکد/انکدِ WebP روی نخِ UI ممنوع."""

    ok = Signal(object)          # info dict از covers.import_cover
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
            # دیسکِ پر / دسترسیِ نوشتن — با «عکسِ خراب» اشتباه گرفته نشود
            dbg_log(f"_CoverImportWorker OSError: {type(e).__name__}")
            self.fail.emit(T(covers.WRITE_ERR_TEXT))
        except Exception as e:
            dbg_log(f"_CoverImportWorker failed: {type(e).__name__}")
            self.fail.emit(T(covers.READ_ERR_TEXT))


def _run_import(parent, store, item, src: Path, on_done) -> None:
    """کارگرِ import را با مکان‌نمای منتظر راه می‌اندازی و نتیجه را اعمال می‌کند."""
    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(Qt.CursorShape.WaitCursor)
    worker = _CoverImportWorker(src, app_dir(), parent)
    # QThread.finished → deleteLater = الگویِ رسمیِ Qt (C2). قبلاً slotهای
    # _finish/_bad مستقیم deleteLater می‌زدند؛ سیگنالِ ok/fail از *انتهای*
    # run() emit می‌شود و slot در نخِ UI اجرا می‌شود، پس deleteLater ممکن بود
    # قبل از تمام‌شدنِ کاملِ نخِ کارگر پردازش شود → "QThread: Destroyed while
    # thread is still running" → qFatal و بسته‌شدنِ کلِ برنامه.
    worker.finished.connect(worker.deleteLater)
    # مرجعِ قوی تا پایتون کارگرِ در حالِ اجرا را GC نکند
    parent._cover_worker = worker          # type: ignore[attr-defined]

    def _finish(ok_info):
        if app is not None:
            app.restoreOverrideCursor()
        if ok_info is not None:
            try:
                store.set_custom_poster(str(item.path), ok_info)
                changed = True
            except Exception as e:
                # (C5) شکستِ ذخیره‌ی متا (مثل قفلِ SQLite) پیامِ غلطِ «عکسِ
                # خراب» نمی‌گیرد — عکسِ کاربر سالم است؛ متنِ جدا + لاگ
                dbg_log(f"pick_cover.apply failed: {type(e).__name__}")
                QMessageBox.warning(parent, T("کاور سفارشی"),
                                    T("ذخیره‌ی اطلاعاتِ کاور ممکن نشد."))
                changed = False
        else:
            changed = False
        if ok_info is not None and changed and ok_info.get("degraded"):
            # (C4-b) انیمیشن زیرِ سقف جا نشد — کاور ساکن (فریمِ اول) ذخیره شد
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
    """دیالوگ انتخاب فایل + نرمال‌سازی در نخِ جدا + به‌روزرسانی UI.

    on_done(changed: bool) در نخِ UI صدا زده می‌شود.
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
    """تأیید + حذف کاور سفارشی و بازگشت به آیکون پوشه."""
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
    """پوشه‌ی ‎.covers/‎ را در اکسپلورر باز می‌کند (در صورت نبود می‌سازد)."""
    from ui.widgets import open_path
    d = covers.covers_dir()               # همان تابعِ واحدِ مسیرِ کاورها
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        dbg_log(f"open_covers_folder failed: {type(e).__name__}")
    open_path(d)
