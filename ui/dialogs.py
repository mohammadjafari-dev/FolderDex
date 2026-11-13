# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""دیالوگ‌ها: انتخاب/ساخت دسته (با پشتیبانی زیردسته)، مدیریت مسیرها، ویرایش عنوان."""
from __future__ import annotations

from i18n import T

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from store import Store
from watchtime import dbg_log

_NEW = "__new__"
_NO_PARENT = "__root__"


class NoWheelComboBox(QComboBox):
    """کامبوباکسی که با چرخِ موس مقدارش عوض نمی‌شود (0.39.0 — درخواست کاربر).

    گزارشِ کاربر: «اسکرول می‌کنم پایین که به تنظیمات پایین برسم، هی تنظیماتِ
    دیگه تغییر می‌کنه — فقط با کلیک کردن روشون بشه تغییر داد». رفتارِ پیش‌فرضِ
    Qt این است که اگر نشانگر روی یک کامبوباکس باشد (حتی بدون کلیک/فوکوس)،
    چرخِ موس مقدارش را عوض می‌کند — در دیالوگِ اسکرول‌دارِ تنظیمات یعنی
    تغییرِ ناخواسته‌ی theme/بازیکن/… حینِ اسکرول.

    قاعده: پاپ‌آپِ بسته → رویدادِ چرخ نادیده گرفته می‌شود تا به اسکرولِ بیرونی
    برسد؛ پاپ‌آپِ باز → رفتارِ پیش‌فرض (حرکت در فهرستِ باز) حفظ می‌شود."""

    def wheelEvent(self, event):            # noqa: N802 (امضای Qt)
        try:
            v = self.view()
            if v is not None and v.isVisible():
                return super().wheelEvent(event)
        except Exception as _exc_dbg:
            dbg_log(f"wheelEvent failed: {type(_exc_dbg).__name__}")
            pass
        event.ignore()


def _relaunch_app(argv: list) -> bool:
    """راه‌اندازیِ دوباره‌ی برنامه در فرآیندی مستقل و کاملاً پنهان (0.37.0).

    برای «پاکسازی کامل» استفاده می‌شود: پرچمِ بازنشانی نوشته شده، این فرآیندِ
    تازه در بوتش همه‌ی داده‌ها را پاک می‌کند. بدون هیچ پنجره‌ی کنسولِ اضافی.
    در تست‌ها patch می‌شود تا فرآیندِ واقعی ساخته نشود. برمی‌گرداند: موفقیت؟"""
    try:
        import subprocess
        import sys
        kw = {"cwd": str(Path(argv[0]).parent), "close_fds": True,
              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = \
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | \
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(list(argv), **kw)
        return True
    except Exception as _exc_dbg:
        dbg_log(f"_relaunch_app failed: {type(_exc_dbg).__name__}")
        return False


def _display(path: str) -> str:
    """نمایش مسیر دسته: انیمه/تماشا‌شده → انیمه › تماشا‌شده
    0.46.0: نام‌های رایج در حالت انگلیسی ترجمه می‌شوند (مقدارِ ذخیره ثابت می‌ماند)."""
    from i18n import disp_cat
    return disp_cat(path).replace("/", " › ")


# ---------------------------------------------------------------- دیالوگ دسته

class CategoryDialog(QDialog):
    """انتخاب از دسته‌های موجود، یا ساخت دسته/زیردسته‌ی جدید."""

    def __init__(self, store: Store, current: str = "", parent=None, create_only: bool = False):
        super().__init__(parent)
        self.store = store
        self.create_only = create_only
        self.setWindowTitle(T("ساخت دسته/زیردسته") if create_only else T("انتخاب دسته‌بندی"))
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # --- انتخاب از موجود ---
        self.pick_combo = None
        if not create_only:
            lay.addWidget(QLabel(T("از دسته‌های موجود انتخاب کن:")))
            self.pick_combo = NoWheelComboBox()
            for c in self.store.categories():
                self.pick_combo.addItem("📂  " + _display(c), c)
            self.pick_combo.addItem(T("➕  ساخت دسته‌ی جدید…"), _NEW)
            if current:
                idx = self.pick_combo.findData(current)
                self.pick_combo.setCurrentIndex(idx if idx >= 0 else self.pick_combo.count() - 1)
            else:
                self.pick_combo.setCurrentIndex(max(0, self.pick_combo.count() - 2))
            self.pick_combo.currentIndexChanged.connect(self._on_pick_change)
            lay.addWidget(self.pick_combo)

        # --- ساخت جدید ---
        self.grp_new = QGroupBox(T("دسته یا زیردسته‌ی جدید"))
        form = QFormLayout(self.grp_new)
        form.setSpacing(8)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(T("مثلاً: تماشا‌شده"))
        self.parent_combo = NoWheelComboBox()
        self.parent_combo.addItem(T("— بدون والد —"), _NO_PARENT)
        for c in self.store.categories():
            self.parent_combo.addItem(_display(c), c)
        self.parent_combo.setEditable(False)
        if current:
            idx = self.parent_combo.findData(current)
            if idx >= 0:
                self.parent_combo.setCurrentIndex(idx)
        form.addRow(T("نام:"), self.ed_name)
        form.addRow(T("زیرمجموعه‌ی:"), self.parent_combo)
        lay.addWidget(self.grp_new)

        if create_only:
            self.grp_new.setVisible(True)
        else:
            self.grp_new.setVisible(self.pick_combo.currentData() == _NEW)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText(T("تأیید"))
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف"))
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_pick_change(self, _i):
        creating = self.pick_combo.currentData() == _NEW
        self.grp_new.setVisible(creating)
        if creating:
            self.ed_name.setFocus()

    def value(self) -> str:
        if self.create_only or (self.pick_combo and self.pick_combo.currentData() == _NEW):
            name = self.ed_name.text().strip().strip("/")
            if not name:
                return ""
            parent = self.parent_combo.currentData()
            return name if parent == _NO_PARENT else f"{parent}/{name}"
        return self.pick_combo.currentData() if self.pick_combo else ""

    def _accept(self):
        if self.value():
            self.store.ensure_category(self.value())
            self.accept()
        else:
            self.ed_name.setFocus()

    @staticmethod
    def pick(store: Store, current: str = "", parent=None):
        dlg = CategoryDialog(store, current, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.value():
            return dlg.value()
        return None


class CategoryPicker(NoWheelComboBox):
    """دراپ‌داون دسته: انتخاب از موجود + «➕ ساخت دسته‌ی جدید…»."""

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setEditable(False)
        self._value = ""
        self.refresh()
        self.activated.connect(self._on_activated)

    def refresh(self):
        self.blockSignals(True)
        self.clear()
        for c in self.store.categories():
            self.addItem("📂  " + _display(c), c)
        self.addItem(T("➕  ساخت دسته‌ی جدید…"), _NEW)
        self.blockSignals(False)
        if self._value:
            self.set_value(self._value)

    def set_value(self, path: str):
        self._value = path
        idx = self.findData(path)
        if idx >= 0:
            self.setCurrentIndex(idx)
        elif path:
            self.addItem("📂  " + _display(path), path)
            self.setCurrentIndex(self.count() - 1)

    def value(self) -> str:
        return self._value

    def _on_activated(self, _i):
        if self.currentData() == _NEW:
            dlg = CategoryDialog(self.store, parent=self, create_only=True)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.value():
                v = dlg.value()
                self.store.ensure_category(v)
                self.refresh()
                self.set_value(v)
            else:
                self.set_value(self._value)  # برگرد به انتخاب قبلی
        else:
            self._value = self.currentData()


# ---------------------------------------------------------------- الگوهای وضعیت

class TrackProfileEditDialog(QDialog):
    """ساخت/ویرایش یک الگوی وضعیت سفارشی — نام + برچسب سه حالت.

    مثال: «تمرین ورزشی» ← انجام شد / در حال انجام / انجام نشده
    """

    def __init__(self, store: Store, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(T("ویرایش الگوی وضعیت") if data else T("الگوی وضعیت جدید"))
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        form = QFormLayout()
        form.setSpacing(8)
        self.ed_title = QLineEdit((data or {}).get("title", ""))
        self.ed_title.setPlaceholderText(T("مثل: تمرین ورزشی"))
        self.ed_done = QLineEdit((data or {}).get("done", ""))
        self.ed_done.setPlaceholderText(T("مثل: انجام شد"))
        self.ed_part = QLineEdit((data or {}).get("part", ""))
        self.ed_part.setPlaceholderText(T("مثل: در حال انجام"))
        self.ed_none = QLineEdit((data or {}).get("none", ""))
        self.ed_none.setPlaceholderText(T("مثل: انجام نشده (اختیاری)"))
        form.addRow(T("نام الگو:"), self.ed_title)
        form.addRow(T("حالت تمام ✓:"), self.ed_done)
        form.addRow(T("حالت نیمه ◐:"), self.ed_part)
        form.addRow(T("شروع‌نشده:"), self.ed_none)
        lay.addLayout(form)
        hint = QLabel(
            T("این نام‌ها روی منوی راست‌کلیک فایل‌ها، نشان کارت‌ها و چیپ پیشرفتِ صفحه‌ی "
            "جزئیات دیده می‌شوند.\n"
            "مثال برای فیلم «دیده شده/نیمه‌تمام» و برای کتاب «خوانده شده/نیمه‌خوانده» است.")
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText(T("ذخیره"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف"))
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def value(self) -> dict:
        return {
            "title": self.ed_title.text().strip(),
            "done": self.ed_done.text().strip(),
            "part": self.ed_part.text().strip(),
            "none": self.ed_none.text().strip(),
        }

    def _accept(self):
        if self.ed_title.text().strip() and self.ed_done.text().strip() \
                and self.ed_part.text().strip():
            self.accept()
        else:
            self.ed_title.setFocus()


class TrackProfilesDialog(QDialog):
    """مدیریت الگوهای وضعیت سفارشی: فهرست + افزودن/ویرایش/حذف."""

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(T("الگوهای وضعیت سفارشی"))
        self.setMinimumSize(520, 380)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(QLabel(
            T("الگوهای داخلی (فیلم/کتاب/بدون ترکینگ) همیشه هستند؛ اینجا الگوهای "
            "خودِ خودت را می‌سازی — مثل «تمرین ورزشی: انجام شد / در حال انجام». "
            "بعد در تب «دسته‌ها» برای هر دسته انتخابش می‌کنی.")
        ))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        b_add = QPushButton(T("➕ الگوی جدید…"))
        b_add.setObjectName("PrimaryBtn")
        b_edit = QPushButton(T("✏ ویرایش…"))
        b_del = QPushButton(T("🗑 حذف"))
        b_add.clicked.connect(self._add)
        b_edit.clicked.connect(self._edit)
        b_del.clicked.connect(self._delete)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(T("بستن"))
        box.rejected.connect(self.reject)
        box.clicked.connect(lambda _b: self.accept())
        lay.addWidget(box)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for pid, p in self.store.custom_profiles().items():
            li = QListWidgetItem(
                f"🧩 {p.get('title', pid)}   —   ✓ {p.get('done', '')} "
                f"· ◐ {p.get('part', '')} · ✕ {p.get('none') or '—'}")
            li.setData(Qt.ItemDataRole.UserRole, pid)
            self.list.addItem(li)

    def _add(self):
        dlg = TrackProfileEditDialog(self.store, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.value()
            self.store.save_custom_profile(v["title"], v["done"], v["part"], v["none"])
            self._refresh()

    def _selected_pid(self) -> str | None:
        li = self.list.currentItem()
        return li.data(Qt.ItemDataRole.UserRole) if li else None

    def _edit(self):
        pid = self._selected_pid()
        if not pid:
            return
        dlg = TrackProfileEditDialog(
            self.store, self, data=self.store.custom_profiles().get(pid))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.value()
            self.store.save_custom_profile(
                v["title"], v["done"], v["part"], v["none"], pid=pid)
            self._refresh()

    def _delete(self):
        from PySide6.QtWidgets import QMessageBox
        pid = self._selected_pid()
        if not pid:
            return
        title = (self.store.custom_profiles().get(pid) or {}).get("title", pid)
        ans = QMessageBox.question(
            self, T("حذف الگو"),
            T("الگوی «{0}» حذف شود؟\nدسته‌هایی که آن را داشتند به «خودکار» برمی‌گردند.").format(title))
        if ans == QMessageBox.StandardButton.Yes:
            self.store.delete_custom_profile(pid)
            self._refresh()


# ---------------------------------------------------------------- برنامه‌های بازکننده‌ی دسته

class _AppPickerDialog(QDialog):
    """0.50.0: دیالوگ انتخاب برنامه — لیست تمام برنامه‌های نصب‌شده با آیکون + جستجو."""

    def __init__(self, parent, ext: str, programs: list):
        super().__init__(parent)
        self.ext = ext
        self.selected_app = None
        self.setWindowTitle(T("انتخاب برنامه برای فایل‌های .{0}").format(ext))
        self.setMinimumSize(500, 500)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        # جستجو
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(T("🔍 جستجو:")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(T("نام برنامه را تایپ کن…"))
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)
        lay.addLayout(search_row)

        # لیست برنامه‌ها با آیکون
        from PySide6.QtCore import QSize
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setSpacing(1)
        self.list.itemDoubleClicked.connect(self._accept_selection)
        lay.addWidget(self.list, 1)

        # QFileIconProvider — یک‌بار ساخته و برای همه استفاده می‌شود
        # نکته‌ی مهم: QFileIconProvider در ماژول QtWidgets است، نه QtCore.
        # ایمپورت اشتباه از QtCore باعث ImportError در همین لحظه‌ی ساختِ دیالوگ
        # می‌شد — یعنی کل دیالوگِ «انتخاب برنامه‌ی پیش‌فرض» (با اسم+آیکون برنامه‌ها)
        # هیچ‌وقت باز نمی‌شد (خطا خاموش در clicked.connect گم می‌شد).
        from PySide6.QtWidgets import QFileIconProvider
        self._icon_provider = QFileIconProvider()
        self._icon_cache = {}  # کش آیکون — جلوگیری از استخراج مجدد

        # پر کردن لیست
        self._all_programs = list(programs)
        self._populate()

        # دکمه‌ها
        btns = QHBoxLayout()
        b_browse = QPushButton(T("📂 انتخاب فایل exe…"))
        b_browse.clicked.connect(self._browse)
        b_select = QPushButton(T("✅ انتخاب"))
        b_select.setObjectName("PrimaryBtn")
        b_select.clicked.connect(self._accept_selection)
        b_cancel = QPushButton(T("انصراف"))
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_browse)
        btns.addStretch(1)
        btns.addWidget(b_select)
        btns.addWidget(b_cancel)
        lay.addLayout(btns)

    def _get_icon(self, path: str):
        """0.50.0: استخراج آیکون exe — با کش + QFileIconProvider + SHGetFileInfoW fallback."""
        import os, sys
        from PySide6.QtCore import QFileInfo
        from PySide6.QtGui import QIcon, QPixmap, QImage
        if not path or not os.path.isfile(path):
            return QIcon()
        # کش — اگر قبلاً استخراج شده، مستقیم برگردان
        if path in self._icon_cache:
            return self._icon_cache[path]
        icon = QIcon()
        # روش ۱: QFileIconProvider
        try:
            icon = self._icon_provider.icon(QFileInfo(path))
            if not icon.isNull() and icon.availableSizes():
                self._icon_cache[path] = icon
                return icon
        except Exception as _exc_dbg:
            dbg_log(f"_get_icon failed: {type(_exc_dbg).__name__}")
            pass
        # روش ۲: SHGetFileInfoW (ویندوز — قابل‌اعتمادتر برای exe)
        if sys.platform == "win32":
            try:
                import ctypes
                class SHFILEINFO(ctypes.Structure):
                    _fields_ = [
                        ("hIcon", ctypes.c_void_p),
                        ("iIcon", ctypes.c_int),
                        ("dwAttributes", ctypes.c_uint),
                        ("szDisplayName", ctypes.c_wchar * 260),
                        ("szTypeName", ctypes.c_wchar * 80),
                    ]
                shinfo = SHFILEINFO()
                SHGFI_ICON = 0x100
                SHGFI_SMALLICON = 0x1
                shell32 = ctypes.windll.shell32
                ret = shell32.SHGetFileInfoW(
                    ctypes.c_wchar_p(path), 0,
                    ctypes.byref(shinfo), ctypes.sizeof(shinfo),
                    SHGFI_ICON | SHGFI_SMALLICON)
                if ret and shinfo.hIcon:
                    # تبدیل HICON به QPixmap از طریق DC حافظه
                    user32 = ctypes.windll.user32
                    gdi32 = ctypes.windll.gdi32
                    sz = 32
                    hdc = user32.GetDC(0)
                    hdc_mem = gdi32.CreateCompatibleDC(hdc)
                    hbm = gdi32.CreateCompatibleBitmap(hdc, sz, sz)
                    old = gdi32.SelectObject(hdc_mem, hbm)
                    # رسم آیکون روی bitmap
                    user32.FillRect(hdc_mem,
                                    ctypes.byref((ctypes.c_long * 4)(0, 0, sz, sz)),
                                    gdi32.GetStockObject(0))  # WHITE_BRUSH
                    user32.DrawIconEx(hdc_mem, 0, 0, shinfo.hIcon, sz, sz,
                                      0, 0, 0x0003)  # DI_NORMAL
                    # خواندن پیکسل‌ها به QImage
                    class BITMAPINFOHEADER(ctypes.Structure):
                        _fields_ = [
                            ("biSize", ctypes.c_uint32),
                            ("biWidth", ctypes.c_int32),
                            ("biHeight", ctypes.c_int32),
                            ("biPlanes", ctypes.c_ushort),
                            ("biBitCount", ctypes.c_ushort),
                            ("biCompression", ctypes.c_uint32),
                            ("biSizeImage", ctypes.c_uint32),
                        ]
                    class BITMAPINFO(ctypes.Structure):
                        _fields_ = [("bmiHeader", BITMAPINFOHEADER)]
                    bi = BITMAPINFO()
                    bi.bmiHeader.biSize = 40
                    bi.bmiHeader.biWidth = sz
                    bi.bmiHeader.biHeight = -sz  # top-down
                    bi.bmiHeader.biPlanes = 1
                    bi.bmiHeader.biBitCount = 32
                    buf = (ctypes.c_ubyte * (sz * sz * 4))()
                    gdi32.GetDIBits(hdc_mem, hbm, 0, sz, buf, ctypes.byref(bi), 0)
                    img = QImage(bytes(buf), sz, sz, QImage.Format.Format_ARGB32)
                    pm = QPixmap.fromImage(img)
                    # پاکسازی
                    gdi32.SelectObject(hdc_mem, old)
                    gdi32.DeleteObject(hbm)
                    gdi32.DeleteDC(hdc_mem)
                    user32.ReleaseDC(0, hdc)
                    user32.DestroyIcon(shinfo.hIcon)
                    result = QIcon(pm)
                    self._icon_cache[path] = result
                    return result
            except Exception as _exc_dbg:
                dbg_log(f"_get_icon failed: {type(_exc_dbg).__name__}")
                pass
        result = QIcon()
        self._icon_cache[path] = result
        return result

    def _populate(self, filter_text: str = ""):
        self.list.clear()
        flt = filter_text.strip().lower()
        count = 0
        for name, path in self._all_programs:
            if flt and flt not in name.lower() and flt not in path.lower():
                continue
            icon = self._get_icon(path)
            item = QListWidgetItem(icon, "  " + name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list.addItem(item)
            count += 1
            if count > 300:
                break
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _filter(self, text: str):
        self._populate(text)

    def _browse(self):
        from PySide6.QtWidgets import QFileDialog
        app, _ = QFileDialog.getOpenFileName(
            self, T("برنامه‌ی بازکننده‌ی فایل‌های .{0}").format(self.ext),
            "", T("برنامه (*.exe);;همه فایل‌ها (*)"))
        if app:
            self.selected_app = app
            self.accept()

    def _accept_selection(self, *_):
        item = self.list.currentItem()
        if item:
            self.selected_app = item.data(Qt.ItemDataRole.UserRole)
            if self.selected_app:
                self.accept()


def _scan_installed_programs() -> list:
    """0.50.0: اسکن برنامه‌های نصب‌شده ویندوز — Start Menu + Program Files.
    برمی‌گرداند: [(نام, مسیر_exe), ...] مرتب‌شده."""
    import sys, os
    if sys.platform != "win32":
        return []
    progs = {}  # name_lower → (name, path)

    # ۱) Program Files — سریع‌ترین و مطمئن‌ترین منبع
    for pf_env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        pf = os.environ.get(pf_env, "")
        if pf_env == "LocalAppData":
            pf = os.path.join(pf, "Programs")
        if not pf or not os.path.isdir(pf):
            continue
        try:
            for entry in sorted(os.scandir(pf), key=lambda e: e.name.lower()):
                try:
                    if entry.is_dir():
                        for f2 in os.scandir(entry.path):
                            if f2.is_file() and f2.name.lower().endswith(".exe"):
                                name = os.path.splitext(f2.name)[0]
                                key = name.lower()
                                if key not in progs:
                                    progs[key] = (name, f2.path)
                                break
                    elif entry.is_file() and entry.name.lower().endswith(".exe"):
                        name = os.path.splitext(entry.name)[0]
                        key = name.lower()
                        if key not in progs:
                            progs[key] = (name, entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    # ۲) Start Menu — شورت‌کات‌ها
    for sm in (os.path.join(os.environ.get("APPDATA", ""),
                            r"Microsoft\Windows\Start Menu\Programs"),
               os.path.join(os.environ.get("ProgramData", ""),
                            r"Microsoft\Windows\Start Menu\Programs")):
        if not sm or not os.path.isdir(sm):
            continue
        try:
            for root, _dirs, files in os.walk(sm):
                depth = root[len(sm):].count(os.sep)
                if depth > 3:
                    continue
                for f in files:
                    if f.lower().endswith(".lnk"):
                        name = os.path.splitext(f)[0]
                        key = name.lower()
                        if key not in progs:
                            # مسیر تقریبی: خودش lnk است ولی نام خوب است
                            lnk_path = os.path.join(root, f)
                            # سعی در خواندن target
                            target = _read_lnk_target(lnk_path)
                            if target and target.lower().endswith(".exe") and os.path.isfile(target):
                                progs[key] = (name, target)
                            elif target and os.path.isfile(target):
                                progs[key] = (name, target)
        except OSError:
            pass

    return sorted(progs.values(), key=lambda x: x[0].lower())[:150]


def _read_lnk_target(lnk_path: str) -> str:
    """خواندن مسیر هدف یک شورت‌کات .lnk ویندوز — بدون نیاز به pywin32."""
    try:
        import struct
        with open(lnk_path, "rb") as f:
            data = f.read()
        if len(data) < 76:
            return ""
        if struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", data, 20)[0]
        if not (flags & 0x01):  # HasLinkTarget
            return ""
        id_list_size = struct.unpack_from("<H", data, 76)[0]
        offset = 78 + id_list_size
        if offset + 4 > len(data):
            return ""
        link_info_size = struct.unpack_from("<I", data, offset)[0]
        if link_info_size < 0x20 or offset + 20 > len(data):
            return ""
        local_base_offset = struct.unpack_from("<I", data, offset + 16)[0]
        if local_base_offset == 0:
            return ""
        target_start = offset + local_base_offset
        if target_start >= len(data):
            return ""
        end = data.find(b"\x00", target_start)
        if end < 0:
            end = min(target_start + 260, len(data))
        return data[target_start:end].decode("utf-8", errors="replace").strip()
    except Exception as _exc_dbg:
        dbg_log(f"_read_lnk_target failed: {type(_exc_dbg).__name__}")
        return ""

class CatOpenersDialog(QDialog):
    """تعیین نرم‌افزار پیش‌فرض برای باز کردن فایل‌های هر پسوند در هر دسته.

    0.50.0 (درخواست کاربر): دو مرحله‌ای — اول دسته انتخاب می‌شود، بعد پسوند‌های
    واقعی موجود در آن دسته نمایش داده می‌شود. برای هر پسوند کاربر می‌تواند
    برنامه انتخاب کند یا «پیش‌فرض ویندوز» بگذارد."""

    def __init__(self, store: Store, parent=None, items=None):
        super().__init__(parent)
        self.store = store
        self.items = list(items) if items else []
        self.setWindowTitle(T("برنامه‌های بازکننده‌ی دسته‌ها"))
        self.setMinimumSize(700, 480)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        info = QLabel(T(
            "اول یک دسته انتخاب کن، بعد پسوند‌های واقعی آن دسته نمایش داده می‌شود.\n"
            "برای هر پسوند می‌توانی برنامه‌ی خاصی تعیین کنی — در غیر این صورت از پیش‌فرض ویندوز استفاده می‌شود."))
        info.setWordWrap(True)
        info.setObjectName("MutedLabel")
        lay.addWidget(info)

        # ----- ردیف بالا: انتخاب دسته -----
        cat_row = QHBoxLayout()
        cat_row.setSpacing(8)
        cat_row.addWidget(QLabel(T("دسته:")))
        self.cat_combo = NoWheelComboBox()
        cats = self.store.categories()
        for c in cats:
            from i18n import disp_cat as _dc
            self.cat_combo.addItem("📂  " + _dc(c).replace("/", " › "), c)
        self.cat_combo.currentIndexChanged.connect(self._on_cat_changed)
        cat_row.addWidget(self.cat_combo, 1)
        lay.addLayout(cat_row)

        # ----- جدول پسوند‌ها -----
        self.ext_table = QTableWidget(0, 3)
        self.ext_table.setHorizontalHeaderLabels([
            T("پسوند"), T("برنامه"), T("")])
        self.ext_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.ext_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.ext_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.ext_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.ext_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ext_table.verticalHeader().setVisible(False)
        lay.addWidget(self.ext_table, 1)

        self.empty_lbl = QLabel(T("این دسته هنوز فایلی ندارد."))
        self.empty_lbl.setObjectName("MutedLabel")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("padding: 40px;")
        self.empty_lbl.setVisible(False)
        lay.addWidget(self.empty_lbl)

        # ----- دکمه‌ها -----
        btns = QHBoxLayout()
        b_set_all = QPushButton(T("📂 انتخاب برنامه برای همه…"))
        b_set_all.setToolTip(T("یک برنامه را برای همه‌ی پسوند‌های این دسته تنظیم کن"))
        b_set_all.clicked.connect(self._set_all_to_one)
        b_reset = QPushButton(T("↺ بازگشت به پیش‌فرض ویندوز"))
        b_reset.setToolTip(T("همه‌ی پسوند‌های این دسته از برنامه‌ی پیش‌فرض ویندوز استفاده کنند"))
        b_reset.clicked.connect(self._reset_all)
        btns.addWidget(b_set_all)
        btns.addWidget(b_reset)
        btns.addStretch(1)
        lay.addLayout(btns)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(T("بستن"))
        box.rejected.connect(self.accept)
        lay.addWidget(box)

        # نمایش اولیه
        if self.cat_combo.count() > 0:
            self._on_cat_changed(0)

    def _on_cat_changed(self, _idx):
        """دسته عوض شد → پسوند‌های واقعی آن دسته را جمع و جدول را بساز."""
        cat = self.cat_combo.currentData()
        if not cat:
            return
        exts = self._collect_exts(cat)
        self.ext_table.setRowCount(0)
        if not exts:
            self.ext_table.setVisible(False)
            self.empty_lbl.setVisible(True)
            return
        self.ext_table.setVisible(True)
        self.empty_lbl.setVisible(False)

        # قواعد فعلی این دسته
        openers = self.store.cat_openers(cat)
        ext_app = {}   # ext → app path
        for rule in openers:
            for e in rule.get("exts", []):
                ext_app[e.lower().lstrip(".")] = rule.get("app", "")

        for ext in sorted(exts):
            row = self.ext_table.rowCount()
            self.ext_table.insertRow(row)
            # پسوند
            ext_item = QTableWidgetItem("." + ext)
            ext_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.ext_table.setItem(row, 0, ext_item)
            # برنامه
            app_path = ext_app.get(ext, "")
            if app_path:
                from pathlib import Path as _P
                app_name = _P(app_path).stem
                app_text = f"{app_name}  ({app_path})"
            else:
                app_name = ""
                app_text = T("پیش‌فرض ویندوز")
            app_item = QTableWidgetItem(app_text)
            app_item.setData(Qt.ItemDataRole.UserRole, ext)
            app_item.setToolTip(app_text)
            self.ext_table.setItem(row, 1, app_item)
            # دکمه تغییر
            btn = QPushButton(T("تغییر…"))
            btn.setFixedWidth(90)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, _e=ext, _r=row: self._pick_app(_e, _r))
            self.ext_table.setCellWidget(row, 2, btn)

    def _collect_exts(self, cat: str) -> set:
        """جمع‌آوری همه‌ی پسوند‌های فایل‌های موجود در آیتم‌های یک دسته."""
        exts = set()
        # روش ۱: از لیست items (اگر موجود باشد — سریع)
        for item in self.items:
            item_cat = self.store.category_of(item)
            if not Store.matches(item_cat, cat):
                continue
            path = Path(item.path)
            if path.is_file():
                s = path.suffix.lower().lstrip(".")
                if s:
                    exts.add(s)
            elif path.is_dir():
                try:
                    for root, _dirs, names in __import__("os").walk(path):
                        for n in names:
                            s = Path(n).suffix.lower().lstrip(".")
                            if s and not n.startswith("."):
                                exts.add(s)
                except OSError:
                    pass
        # روش ۲: اگر items خالی است، مستقیم از roots اسکن کن
        if not self.items:
            for root_info in self.store.roots():
                root_cat = str(root_info.get("category", ""))
                if not Store.matches(root_cat, cat):
                    continue
                base = Path(str(root_info.get("path", "")))
                if not base.exists():
                    continue
                try:
                    if base.is_file():
                        s = base.suffix.lower().lstrip(".")
                        if s:
                            exts.add(s)
                    else:
                        for dr, _ds, ns in __import__("os").walk(base):
                            for n in ns:
                                s = Path(n).suffix.lower().lstrip(".")
                                if s and not n.startswith("."):
                                    exts.add(s)
                except OSError:
                    pass
        return exts

    @staticmethod
    def _scan_installed_programs() -> list[tuple[str, str]]:
        """0.50.0: اسکن تمام برنامه‌های نصب‌شده — شورت‌کات‌های Start Menu + Program Files.
        برمی‌گرداند: [(نام, مسیر_exe), ...] مرتب‌شده نامی."""
        import sys, os
        if sys.platform != "win32":
            return []
        progs = {}  # name_lower → (name, path)
        # ۱) Start Menu shortcuts (بهترین منبع — همه برنامه‌های نصب‌شده)
        for sm in (os.environ.get("APPDATA", "") + r"\Microsoft\Windows\Start Menu\Programs",
                    os.environ.get("ProgramData", "") + r"\Microsoft\Windows\Start Menu\Programs"):
            if not sm or not os.path.isdir(sm):
                continue
            try:
                for root, _dirs, files in os.walk(sm):
                    depth = root[len(sm):].count(os.sep)
                    if depth > 3:
                        continue
                    for f in files:
                        if f.lower().endswith(".lnk"):
                            try:
                                # خواندن target از shortcut
                                lnk_path = os.path.join(root, f)
                                target = _read_lnk_target(lnk_path)
                                if target and target.lower().endswith(".exe") and os.path.isfile(target):
                                    name = os.path.splitext(f)[0]
                                    key = name.lower()
                                    if key not in progs:
                                        progs[key] = (name, target)
                            except Exception as _exc_dbg:
                                dbg_log(f"_scan_installed_programs failed: {type(_exc_dbg).__name__}")
                                pass
            except OSError:
                pass
        # ۲) Program Files — exe های مستقیم
        for pf in (os.environ.get("ProgramFiles", ""),
                    os.environ.get("ProgramFiles(x86)", ""),
                    os.environ.get("LocalAppData", "") + r"\Programs"):
            if not pf or not os.path.isdir(pf):
                continue
            try:
                for entry in sorted(os.scandir(pf), key=lambda e: e.name.lower()):
                    if entry.is_dir():
                        # پیدا کردن exe اصلی در پوشه (معمولاً هم‌نام پوشه)
                        try:
                            for f2 in os.scandir(entry.path):
                                if f2.is_file() and f2.name.lower().endswith(".exe"):
                                    name = os.path.splitext(f2.name)[0]
                                    key = name.lower()
                                    if key not in progs:
                                        progs[key] = (name, f2.path)
                                    break  # فقط اولین exe
                        except OSError:
                            pass
                    elif entry.is_file() and entry.name.lower().endswith(".exe"):
                        name = os.path.splitext(entry.name)[0]
                        key = name.lower()
                        if key not in progs:
                            progs[key] = (name, entry.path)
            except OSError:
                pass
        # ۳) Uninstall registry
        try:
            import winreg as _wr
            for hive_path in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                              r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                for hive in (_wr.HKEY_CURRENT_USER, _wr.HKEY_LOCAL_MACHINE):
                    try:
                        key = _wr.OpenKey(hive, hive_path)
                        i = 0
                        while True:
                            try:
                                sub_name = _wr.EnumKey(key, i)
                                i += 1
                            except OSError:
                                break
                            try:
                                sub = _wr.OpenKey(key, sub_name)
                                disp_name, _ = _wr.QueryValueEx(sub, "DisplayName")
                                inst_loc, _ = _wr.QueryValueEx(sub, "InstallLocation")
                                _wr.CloseKey(sub)
                                if disp_name and inst_loc and os.path.isdir(inst_loc):
                                    # پیدا کردن exe در محل نصب
                                    for f3 in os.scandir(inst_loc):
                                        if f3.is_file() and f3.name.lower().endswith(".exe"):
                                            name = disp_name
                                            key2 = name.lower()
                                            if key2 not in progs:
                                                progs[key2] = (name, f3.path)
                                            break
                            except OSError:
                                pass
                        _wr.CloseKey(key)
                    except OSError:
                        pass
        except Exception as _exc_dbg:
            dbg_log(f"_scan_installed_programs failed: {type(_exc_dbg).__name__}")
            pass
        result = sorted(progs.values(), key=lambda x: x[0].lower())
        return result[:100]  # حداکثر ۱۰۰ برنامه

    def _pick_app(self, ext: str, row: int):
        """0.50.0: انتخاب برنامه — دیالوگ سفارشی با لیست برنامه‌های نصب‌شده + آیکون."""
        cat = self.cat_combo.currentData()
        if not cat:
            return
        # اسکن برنامه‌ها
        all_progs = self._scan_installed_programs()
        # دیالوگ انتخاب برنامه
        dlg = _AppPickerDialog(self, ext, all_progs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        app = dlg.selected_app
        if not app:
            return
        # به‌روزرسانی قواعد
        rules = list(self.store.cat_openers(cat))
        new_rules = []
        for r in rules:
            old_exts = [e for e in r.get("exts", []) if e.lower().lstrip(".") != ext]
            if old_exts:
                new_rules.append({"exts": old_exts, "app": r["app"]})
        merged = False
        for r in new_rules:
            if r["app"] == app:
                if ext not in [e.lower().lstrip(".") for e in r["exts"]]:
                    r["exts"].append(ext)
                merged = True
                break
        if not merged:
            new_rules.append({"exts": [ext], "app": app})
        self.store.set_cat_openers(cat, new_rules)
        # به‌روزرسانی ردیف جدول
        from pathlib import Path as _P
        app_name = _P(app).stem
        app_item = self.ext_table.item(row, 1)
        if app_item:
            app_item.setText(f"{app_name}  ({app})")
            app_item.setToolTip(f"{app_name}  ({app})")

    def _set_all_to_one(self):
        """یک برنامه برای همه‌ی پسوند‌های این دسته."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        cat = self.cat_combo.currentData()
        if not cat:
            return
        if self.ext_table.rowCount() == 0:
            QMessageBox.information(self, T("توجه"),
                                    T("پسوندی در این دسته نیست."))
            return
        app, _ = QFileDialog.getOpenFileName(
            self, T("برنامه‌ی بازکننده برای همه‌ی پسوند‌ها"),
            "", T("برنامه (*.exe);;همه فایل‌ها (*)"))
        if not app:
            return
        all_exts = []
        for row in range(self.ext_table.rowCount()):
            ext_item = self.ext_table.item(row, 0)
            if ext_item:
                all_exts.append(ext_item.text().lstrip("."))
        self.store.set_cat_openers(cat, [{"exts": all_exts, "app": app}])
        # به‌روزرسانی جدول
        from pathlib import Path as _P
        app_name = _P(app).stem
        for row in range(self.ext_table.rowCount()):
            app_item = self.ext_table.item(row, 1)
            if app_item:
                app_item.setText(f"{app_name}  ({app})")
                app_item.setToolTip(f"{app_name}  ({app})")

    def _reset_all(self):
        """بازگشت همه‌ی پسوند‌های این دسته به پیش‌فرض ویندوز."""
        cat = self.cat_combo.currentData()
        if not cat:
            return
        if self.ext_table.rowCount() == 0:
            return
        self.store.set_cat_openers(cat, [])
        # به‌روزرسانی جدول
        for row in range(self.ext_table.rowCount()):
            app_item = self.ext_table.item(row, 1)
            if app_item:
                app_item.setText(T("پیش‌فرض ویندوز"))
                app_item.setToolTip(T("پیش‌فرض ویندوز"))


def pick_track_profile(store: Store, cat: str, parent=None) -> str | None:
    """دیالوگ کوچک انتخاب الگوی وضعیت هنگام افزودن مسیر → کلید یا None (انصراف)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(T("الگوی وضعیت این دسته"))
    dlg.setMinimumWidth(420)
    lay = QVBoxLayout(dlg)
    lay.setSpacing(10)
    lay.addWidget(QLabel(
        T("برای عناوین دسته‌ی «{0}» چه نوع علامت وضعیتی می‌خواهی؟\n«دیده شده» برای فیلم‌ها (با ترک خودکار)، «خوانده شده» برای کتاب/مانگا، یا الگوی سفارشی خودت.").format(_display(cat))
    ))
    combo = NoWheelComboBox()
    opts = store.profile_options()
    for key, title in opts:
        combo.addItem(T(title), key)   # عنوان الگوهای داخلی هم ترجمه می‌شود (سفارشیِ کاربر همان می‌ماند)
    guess = store.guess_profile_key(cat)
    idx = combo.findData(guess)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    lay.addWidget(combo)
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(T("تأیید"))
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف (خودکار)"))
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    lay.addWidget(QLabel(T("بعداً از «تنظیمات ← دسته‌ها» همیشه قابل تغییر است.")))
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return combo.currentData()
    return None


# ---------------------------------------------------------------- تنظیمات (همه در یک جا)

class SettingsDialog(QDialog):
    """پنجره‌ی واحد تنظیمات با سه تب: مسیرها، دسته‌ها (با افزودن دسته‌ی جدید)، عمومی."""

    def __init__(self, store: Store, parent=None, cache_dir=None, durations=None):
        super().__init__(parent)
        self.store = store
        self.cache_dir = cache_dir
        self.durations = durations
        self.setWindowTitle(T("تنظیمات"))
        self.setMinimumSize(700, 560)

        # آیا اسکن مجدد لازم است؟ (فقط با تغییر مسیرها/فال‌بک/روشن‌شدن زمان‌ها)
        self.paths_changed = False
        self.cats_changed = False
        self._orig_roots_sig = self._roots_signature(store.roots())
        self._orig_fallback = store.include_image_fallback
        self._orig_durations = store.compute_durations

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        self._build_paths_tab()
        self._build_cats_tab()
        self._build_general_tab()

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.button(QDialogButtonBox.StandardButton.Ok).setText(T("💾 ذخیره"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    # ------------------------------------------------------------ تب ۱: مسیرها
    def _build_paths_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.addWidget(QLabel(
            T("پوشه‌های اصلی (روت) هر دسته — می‌تواند روی درایوهای مختلف باشد.")
        ))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([T("دسته‌بندی"), T("مسیر پوشه")])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)

        for root in self.store.roots():
            self._append(str(root.get("category", "")), str(root.get("path", "")))

        btns = QHBoxLayout()
        b_add = QPushButton(T("➕ افزودن پوشه…"))
        b_edit = QPushButton(T("✏ تغییر دسته"))
        b_del = QPushButton(T("🗑 حذف"))
        b_add.clicked.connect(self._add)
        b_edit.clicked.connect(self._edit)
        b_del.clicked.connect(self._delete)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

        self.chk_fallback = QCheckBox(T("اگر آیکون نبود، عکس داخل پوشه (poster/cover) استفاده شود"))
        self.chk_fallback.setChecked(self.store.include_image_fallback)
        lay.addWidget(self.chk_fallback)

        self.table.itemDoubleClicked.connect(lambda _i: self._edit())
        self.tabs.addTab(tab, T("📁 مسیرها"))

    # ------------------------------------------------------------ تب ۲: دسته‌ها
    def _build_cats_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.addWidget(QLabel(
            T("همه‌ی دسته‌ها و زیردسته‌ها — برای کار روی «خانواده»ها مثل انیمه › تماشا‌شده.")
        ))

        self.cat_list = QListWidget()
        self.cat_list.setObjectName("CatListBox")
        lay.addWidget(self.cat_list, 1)

        btns = QHBoxLayout()
        b_addcat = QPushButton(T("➕ افزودن دسته/زیردسته‌ی جدید…"))
        b_addcat.setObjectName("PrimaryBtn")
        b_addcat.clicked.connect(self._add_category)
        self.btn_delcat = QPushButton(T("🗑 حذف دسته‌ی بلااستفاده"))
        self.btn_delcat.setObjectName("FlatBtn")
        self.btn_delcat.clicked.connect(self._del_category_confirm)
        b_profs = QPushButton(T("🧩 الگوهای وضعیت سفارشی…"))
        b_profs.clicked.connect(self._manage_track_profiles)
        b_openers = QPushButton(T("📂 برنامه‌های بازکننده‌ی دسته‌ها…"))
        b_openers.setObjectName("FlatBtn")
        b_openers.clicked.connect(self._manage_cat_openers)
        btns.addWidget(b_addcat)
        btns.addWidget(self.btn_delcat)
        btns.addWidget(b_profs)
        btns.addWidget(b_openers)
        btns.addStretch(1)
        lay.addLayout(btns)

        hint = QLabel(
            T("دسته‌هایی که مسیر یا عنوانی مستقیماً از آن‌ها استفاده می‌کند قابل حذف نیستند. "
            "ترتیب نمایش در سایدبار = ترتیب همین فهرست.")
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        track_hint = QLabel(
            T("🏷 باکسِ روبه‌روی هر دسته = «الگوی وضعیت» علائم صفحه‌ی جزئیات: "
            "«فیلم/سریال ← دیده شده» (ترک خودکار هم دارد)، «کتاب/مانگا ← خوانده شده»، "
            "«بدون ترکینگ» برای دسته‌هایی مثل بازی، یا الگوی سفارشی خودِ تو "
            "(مثل «تمرین ورزشی: انجام شد / در حال انجام» — با دکمه‌ی 🧩 بالا می‌سازی). "
            "زیردسته‌ها نزدیک‌ترین والدِ تنظیم‌شده را ارث می‌برند و «خودکار» همان "
            "تشخیص بر اساس نوع فایل است.")
        )
        track_hint.setObjectName("MutedLabel")
        track_hint.setWordWrap(True)
        lay.addWidget(track_hint)

        self._refresh_cats()
        self.tabs.addTab(tab, T("🗂 دسته‌ها"))

    def _manage_track_profiles(self):
        TrackProfilesDialog(self.store, self).exec()
        self._refresh_cats()   # باکس‌های الگو با سفارشی‌های جدید/حذف‌شده تازه شوند

    def _manage_cat_openers(self):
        # انتقال لیست آیتم‌ها برای جمع‌آوری هوشمند پسوند‌ها
        _items = None
        try:
            mw = self.parent()
            if mw and hasattr(mw, "items"):
                _items = mw.items
        except Exception as _exc_dbg:
            dbg_log(f"_manage_cat_openers failed: {type(_exc_dbg).__name__}")
            pass
        CatOpenersDialog(self.store, self, items=_items).exec()

    @staticmethod
    def _cat_row_combo(cat_list: QListWidget, li: QListWidgetItem) -> QComboBox | None:
        """باکس الگوی وضعیتِ یک ردیف دسته (ردیف = ویجت «اسم + باکس»)."""
        w = cat_list.itemWidget(li)
        if w is None:
            return None
        if isinstance(w, QComboBox):      # سازگاری با ردیف‌های بدون ویجت ترکیبی
            return w
        return w.findChild(QComboBox)

    def _refresh_cats(self):
        # باکس‌های فعلی را نگه دار تا انتخاب‌های ذخیره‌نشده هنگام اضافه/حذف دسته نپرد
        prev = {}
        for i in range(self.cat_list.count()):
            li0 = self.cat_list.item(i)
            c0 = self._cat_row_combo(self.cat_list, li0)
            if c0 is not None:
                prev[li0.data(Qt.ItemDataRole.UserRole)] = c0.currentData()
        self.cat_list.clear()
        for c in self.store.categories():
            used = self.store.category_in_use(c)
            li = QListWidgetItem(_display(c) + ("" if used else T("   (بلااستفاده)")))
            li.setData(Qt.ItemDataRole.UserRole, c)
            li.setForeground(QBrush(QColor(self.store.color(c))))
            # ردیف ترکیبی «اسم دسته + باکس الگو» — قبلاً فقط باکس داخل ردیف بود و
            # setItemWidget متن خود آیتم را می‌پوشاند، پس اسم دسته دیده نمی‌شد
            # (گزارش کاربر: «نمی‌فهمیم چی به چیه»)
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2)
            hl.setSpacing(10)
            name_lbl = QLabel(_display(c) + ("" if used else T("   (بلااستفاده)")))
            name_lbl.setStyleSheet(
                f"color: {self.store.color(c)}; font-weight: bold;")
            hl.addWidget(name_lbl, 1)
            combo = NoWheelComboBox()
            combo.setMinimumWidth(220)
            for key, title in self.store.profile_options():
                combo.addItem(T(title), key)   # عنوان الگوها دوزبانه در محل نمایش
            cur = prev.get(c) or self.store.cat_track_profile_key(c) or "auto"
            idx = combo.findData(cur)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setToolTip(
                T("الگوی علائم وضعیت عناوین این دسته — «خودکار» یعنی بر اساس نوع فایل "
                "(ویدیو ← دیده شده، سند ← خوانده شده)"))
            hl.addWidget(combo)
            li.setSizeHint(row.sizeHint())
            self.cat_list.addItem(li)
            self.cat_list.setItemWidget(li, row)

    def _add_category(self):
        current = self.cat_list.currentItem()
        dlg = CategoryDialog(
            self.store, parent=self, create_only=True,
            current=(current.data(Qt.ItemDataRole.UserRole) if current else ""))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.value():
            self.store.ensure_category(dlg.value())
            self.cats_changed = True
            self._refresh_cats()

    def _delete_category(self, cat: str) -> bool:
        """حذف واقعی (فقط بلااستفاده) — بدون دیالوگ، برای تست/بازاستفاده."""
        if self.store.category_in_use(cat):
            return False
        self.store.remove_category(cat)
        self.cats_changed = True
        self._refresh_cats()
        return True

    def _del_category_confirm(self):
        from PySide6.QtWidgets import QMessageBox
        li = self.cat_list.currentItem()
        if li is None:
            return
        cat = li.data(Qt.ItemDataRole.UserRole)
        if self.store.category_in_use(cat):
            QMessageBox.information(
                self, T("در حال استفاده"),
                T("این دسته توسط مسیر یا عنوانی استفاده می‌شود و قابل حذف نیست."))
            return
        ans = QMessageBox.question(self, T("حذف دسته"), T("«{0}» حذف شود؟").format(_display(cat)))
        if ans == QMessageBox.StandardButton.Yes:
            self._delete_category(cat)

    # ------------------------------------------------------------ تب ۳: عمومی
    def _build_general_tab(self):
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        inner = QWidget()
        tab.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)

        # --- پوسته ---
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        theme_row.addWidget(QLabel(T("🎨 پوسته‌ی برنامه:")))
        from qss import THEMES, theme_label
        self.theme_combo = NoWheelComboBox()
        for key in THEMES:
            self.theme_combo.addItem(T(theme_label(key)), key)   # برچسب تم دوزبانه، کلید ثابت
        idx = self.theme_combo.findData(self.store.theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        theme_row.addWidget(self.theme_combo, 1)
        theme_row.addStretch(1)
        lay.addLayout(theme_row)

        # --- زبان (0.45.0 — درخواست کاربر «دوزبانه: پیش‌فرض انگلیسی + ذخیره‌ی دائمی») ---
        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.addWidget(QLabel(T("🌐 زبان برنامه:")))
        self.lang_combo = NoWheelComboBox()
        from i18n import LANGUAGES
        for code, label in LANGUAGES:
            self.lang_combo.addItem(label, code)
        idx = self.lang_combo.findData(self.store.language)
        self.lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.lang_combo.setToolTip(
            T("زبانِ رابط کاربری — فارسی یا English.\nبا ذخیره، برای اعمالِ کاملِ زبان و چیدمان (راست‌به‌چپ/چپ‌به‌راست)\nبرنامه پیشنهاد راه‌اندازیِ مجددِ خودکار می‌دهد؛ انتخاب در config.json ذخیره می‌شود.")
        )
        lang_row.addWidget(self.lang_combo, 1)
        lang_row.addStretch(1)
        lay.addLayout(lang_row)

        # --- حالت عملکرد (0.45.0 — بازبینی تنظیمات/بهینه‌سازی: ثابت‌کردنِ شمارِ کارگرها) ---
        perf_row = QHBoxLayout()
        perf_row.setSpacing(6)
        perf_row.addWidget(QLabel(T("🚀 حالت عملکرد:")))
        self.perf_combo = NoWheelComboBox()
        self.perf_combo.addItem(T("خودکار (تشخیص دیسک)"), "auto")
        self.perf_combo.addItem(T("حداکثر سرعت"), "high")
        self.perf_combo.addItem(T("محافظه‌کار (دیسک چرخان)"), "safe")
        idx = self.perf_combo.findData(self.store.perf_mode)
        self.perf_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.perf_combo.setToolTip(
            T("تعداد کارگرهای پیش‌ساخت و اسکن:\n«خودکار» نوع دیسکِ هر درایو را تشخیص می‌دهد (نتایج ذخیره و بین اجراها ثابت می‌ماند).\n«حداکثر سرعت» همیشه با تمام توان (مناسب SSD/NVMe) و «محافظه‌کار» همیشه ملایم\n(مناسب HDD/درایو خارجی کند) کار می‌کند — اگر یک‌بار تشخیص اشتباه شد، اینجا ثابتش کن.")
        )
        perf_row.addWidget(self.perf_combo, 1)
        perf_row.addStretch(1)
        lay.addLayout(perf_row)

        # --- زمان ویدیوها ---
        self.chk_durations = QCheckBox(
            T("⏱ هنگام اسکن، مدت‌زمان ویدیوها خوانده و ذخیره شود (برای مرتب‌سازی بر اساس زمان)")
        )
        self.chk_durations.setChecked(self.store.compute_durations)
        self.chk_durations.setToolTip(
            T("مدت MKV/MP4/AVI/WMV با خواندن مستقیم هدر فایل و فوری به‌دست می‌آید\n"
            "(بدون ffmpeg؛ هر فایل ~یک‌هزارم ثانیه) و دائماً کش می‌شود؛\n"
            "فقط پوشه‌های تازه یا تغییرکرده دوباره محاسبه می‌شوند.")
        )
        lay.addWidget(self.chk_durations)

        if self.durations is not None:
            self.btn_redur = QPushButton(T("♻ بازمحاسبه‌ی همه‌ی زمان‌ها (پاک‌کردن کش و اسکن مجدد)"))
            self.btn_redur.setObjectName("FlatBtn")
            self.btn_redur.clicked.connect(self._recalc_durations)
            lay.addWidget(self.btn_redur)

        # --- تامبنیل از داخل فایل‌های فشرده ---
        self.chk_archive = QCheckBox(
            T("🗜 تامبنیل از داخل فایل‌های فشرده (برای پوشه‌های بدون تصویر و ویدیو)")
        )
        self.chk_archive.setChecked(self.store.archive_thumbs)
        self.chk_archive.setToolTip(
            T("اولین تصویرِ داخل فایل فشرده تامبنیل عنوان می‌شود؛ اگر تصویر نبود ویدیو، وگرنه PDF.\n"
            "ZIP/CBZ/EPUB و TAR بدون نصبِ هیچ‌چیز کار می‌کنند.\n"
            "برای RAR و 7z از خودِ 7-Zip یا UnRARِ نصب‌شده‌ی سیستم استفاده می‌شود (بدون نیاز به\n"
            "اینترنت/pip؛ نصبِ 7-Zip یا WinRAR کافی است — پکیج‌های py7zr/rarfile هم اختیاری‌اند).\n"
            "نتیجه کش می‌شود؛ اگر آرشیو خراب/رمزدار بود، فقط یک‌بار امتحان و بعد کارت نوع‌فایل می‌ماند.")
        )
        lay.addWidget(self.chk_archive)

        # --- تامبنیل فقط پوشه‌ها ---
        self.chk_folder_only = QCheckBox(
            T("📁 تامبنیل واقعی فقط برای پوشه‌ها (فایل‌ها کارت نوع‌فایل می‌گیرند)")
        )
        self.chk_folder_only.setChecked(self.store.folder_thumbs_only)
        self.chk_folder_only.setToolTip(
            T("برای کتابخانه‌های خیلی بزرگ و حالت سقف‌دار کش: دیگر برای تک‌تک فایل‌ها\n"
            "(قسمت‌ها/تصاویر) تامبنیل واقعی ساخته نمی‌شود — فقط پوستر پوشه‌ها/عناوین.\n"
            "حجم کش و زمان ساخت به‌شدت کم می‌شود؛ در جزئیات هم فایل‌ها کارت نوع‌فایل\n"
            "شیک می‌گیرند (همان گرادیان MKV/PDF/ZIP…).")
        )
        lay.addWidget(self.chk_folder_only)

        # --- پیش‌ساخت تامبنیل‌ها بعد از اسکن ---
        self.chk_prebuild = QCheckBox(
            T("⚡ بعد از افزودن مسیر/اسکن، همه‌ی تامبنیل‌ها در پس‌زمینه پیش‌ساخته شوند")
        )
        self.chk_prebuild.setChecked(self.store.prebuild_thumbs)
        self.chk_prebuild.setToolTip(
            T("با فعال بودن، همین‌که مسیر تازه اضافه و اسکن تمام شود، برنامه در\n"
            "پس‌زمینه (با فشار ملایم روی سیستم) همه‌ی پوسترها و تامبنیل فایل‌ها\n"
            "را یک‌بار برای همیشه می‌سازد — باز کردن پوشه‌ها بعداً فوری است.\n"
            "پیشرفت آن در نوار وضعیت پایین پنجره دیده می‌شود؛ با خاموش/روشن‌کردنش\n"
            "هم همین حالا بدون اسکن مجدد شروع می‌شود.\n"
            "برای کتابخانه‌های خیلی بزرگ، دفعه‌ی اول بسته به حجم ممکن است مدتی طول بکشد.")
        )
        lay.addWidget(self.chk_prebuild)

        # درخواست کاربر: چک‌باکس «پیش‌ساخت سنگین‌ها» حذف شد — پیش‌ساختِ روشن
        # یعنی ساختِ همه‌ی تامبنیل‌ها (سبک+سنگین) در پس‌زمینه با اولویتِ UI.


        # --- پخش‌کننده برای رزومه/ردیابی ---
        lay.addSpacing(6)
        lay.addWidget(QLabel(T("🎬 پخش‌کننده برای پخش و ردیابی تماشا:"), objectName="SectionHeader"))
        player_row = QHBoxLayout()
        player_row.setSpacing(6)
        self.player_combo = NoWheelComboBox()
        self.player_combo.addItem(T("PotPlayer — ردیابی خودکار ترک/زیرنویس + رزومه"), "potplayer")
        self.player_combo.addItem(T("پخش‌کننده‌ی پیش‌فرض ویندوز — بدون ردیابی"), "default")
        cfg = self.store.player_cfg
        idx = self.player_combo.findData(cfg.get("mode", "auto"))
        self.player_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.player_edit = QLineEdit(cfg.get("path", ""))
        self.player_edit.setPlaceholderText(T("مثال: C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe"))
        b_pbrowse = QPushButton("…")
        b_pbrowse.setFixedWidth(34)
        b_pbrowse.clicked.connect(self._browse_player)
        self.player_edit.setVisible(self.player_combo.currentData() == "custom")
        b_pbrowse.setVisible(self.player_combo.currentData() == "custom")
        self._player_browse_btn = b_pbrowse
        self.player_combo.currentIndexChanged.connect(self._on_player_mode)
        player_row.addWidget(self.player_combo, 2)
        player_row.addWidget(self.player_edit, 3)
        player_row.addWidget(b_pbrowse)
        lay.addLayout(player_row)

        # بازخورد زنده: آیا انتخاب فعلی واقعاً پیدا می‌شود؟ (تا تنظیم «بی‌صدا نادیده» گرفته نشود)
        self.player_status = QLabel("")
        self.player_status.setObjectName("MutedLabel")
        self.player_status.setWordWrap(True)
        lay.addWidget(self.player_status)
        self.player_edit.textChanged.connect(self._update_player_status)
        self.player_combo.currentIndexChanged.connect(self._update_player_status)
        self._update_player_status()

        hint = QLabel(
            T("• PotPlayer → ردیابی خودکار ترک/زیرنویس + ذخیره‌ی موقعیت واقعی + ادامه از همان‌جا.\n"
            "• پخش‌کننده‌ی پیش‌فرض ویندوز → بدون ردیابی تماشا.")
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # --- کش ---
        if self.cache_dir is not None:
            lay.addSpacing(6)
            from scanner import human_size as _hs
            from thumbs import cache_size
            cache_row = QHBoxLayout()
            cache_row.setSpacing(6)
            cache_row.addWidget(QLabel(T("💾 سقف حجم کش تصاویر:")))
            self.cache_combo = NoWheelComboBox()
            cur_mb = self.store.cache_max_mb()
            presets = [(T("نامحدود"), 0), (T("۱۰۰ مگابایت"), 100), (T("۲۵۰ مگابایت"), 250),
                       (T("۵۰۰ مگابایت"), 500), (T("۱ گیگابایت"), 1024), (T("۲ گیگابایت"), 2048),
                       (T("۵ گیگابایت"), 5120), (T("۱۰ گیگابایت"), 10240)]
            idx = -1
            for i, (label, val) in enumerate(presets):
                self.cache_combo.addItem(label, val)
                if val == cur_mb:
                    idx = i
            if idx < 0:     # مقدار دستیِ قبلی در config — گزینه‌اش را اضافه کن
                self.cache_combo.addItem(_hs(cur_mb * 1024 * 1024), cur_mb)
                idx = self.cache_combo.count() - 1
            self.cache_combo.setCurrentIndex(idx)
            self.cache_combo.setToolTip(
                T("وقتی حجم پوشه‌ی کش از این سقف بگذرد، قدیمی‌ترین تامبنیل‌های بلااستفاده\n"
                "به‌صورت خودکار پاک می‌شوند (هنگام اجرای برنامه و پس از ذخیره‌ی تنظیمات).\n"
                "حجم فعلی کنار دکمه‌ی پاک‌کردن دیده می‌شود؛ «نامحدود» یعنی بدون پاک‌سازی خودکار.")
            )
            cache_row.addWidget(self.cache_combo, 1)
            cache_row.addStretch(1)
            lay.addLayout(cache_row)

            # درخواست کاربر: کش تامبنیل همیشه کنار برنامه است —
            # کامبوی محل و مسیر سراسری/سفارشی به تصمیمِ کاربر حذف شد.

            self.btn_clear_cache = QPushButton(T("🧹 پاک کردن کش تصاویر"))
            self.btn_clear_cache.setObjectName("FlatBtn")
            self._refresh_cache_label(cache_size(self.cache_dir))
            self.btn_clear_cache.clicked.connect(self._clear_cache)
            lay.addWidget(self.btn_clear_cache)

        # درخواست کاربر — «می‌خوام اکسپورت/ایمپورت بشه»: انتقالِ اطلاعات
        # بین نسخه‌ها/پوشه‌ها کاربرمحور است — یک‌کلیک خروجی، یک‌کلیک ورود.
        # (درخواست کاربر): خروجی به‌جای پوشه، *یک فایلِ تکی* با پسوندِ
        # اختصاصی ‎.mcbak‎ است — جابه‌جاکردن/نگه‌داریِ یک فایل راحت‌تر است.
        bk_row = QHBoxLayout()
        bk_row.setSpacing(6)
        self.btn_export = QPushButton(T("📤 خروجی‌گرفتن از اطلاعات (پشتیبان)"))
        self.btn_export.setObjectName("FlatBtn")
        self.btn_export.setToolTip(
            T("تنظیمات، فهرست پوشه‌ها، دیتابیس و — به انتخاب تو — کشِ تامبنیل‌ها "
            "داخل یک فایلِ تکی با پسوند ‎.mcbak‎ ذخیره می‌شود"))
        self.btn_export.clicked.connect(self._export_backup)
        self.btn_import = QPushButton(T("📥 واردکردن اطلاعات از پشتیبان"))
        self.btn_import.setObjectName("FlatBtn")
        self.btn_import.setToolTip(
            # دو حالت — ادغامِ غیرمخرب (حفظ فعلی‌ها) یا جایگزینی (با اسنپ‌شات)
            T("دو حالت دارد: «ادغام» مواردِ پشتیبان را بدون حذفِ داده‌ی فعلی اضافه می‌کند " 
            "(مواردِ بدون‌مسیر رد می‌شوند)؛ «جایگزینی» همان رفتارِ قبلی است ولی پیش از آن " 
            "اسنپ‌شاتِ امنیتیِ خودکار ساخته می‌شود"))
        self.btn_import.clicked.connect(self._import_backup)
        bk_row.addWidget(self.btn_export, 1)
        bk_row.addWidget(self.btn_import, 1)
        lay.addLayout(bk_row)
        self.chk_backup_cache = QCheckBox(
            T("کشِ تامبنیل‌ها هم در پشتیبان باشد (حجیم‌تر ولی بدون پیش‌ساختِ دوباره)"))
        self.chk_backup_cache.setChecked(True)
        lay.addWidget(self.chk_backup_cache)

        # درخواست کاربر: «پشتیبان‌گیری خودکار» — گزینه + مسیرِ دلخواه.
        # فایل فقط وقتی بازنویسی می‌شود که داده‌ی تازه‌ای اضافه و تامبنیل‌هایش
        # کامل شده باشد («فقط وقتی تغییری میکنه فایل تغییر میده») — بدون کش، سبک،
        # اتمیک و در پس‌زمینه.
        ab_row = QHBoxLayout()
        ab_row.setSpacing(6)
        self.chk_auto_backup = QCheckBox(
            T("🗄 پشتیبان‌گیری خودکار وقتی اطلاعاتِ تازه کامل شد"))
        _ab48 = self.store.auto_backup_cfg()
        self.chk_auto_backup.setChecked(bool(_ab48.get("enabled")))
        self.chk_auto_backup.setToolTip(
            T("وقتی مواردِ تازه اسکن و تامبنیل‌هایشان کامل شد — یا هنگامِ خروج —\n"
            "یک فایلِ ‎.mcbak‎ سبک (بدون کش) در مسیرِ مقصد به‌روزرسانی می‌شود.\n"
            "اگر هیچ تغییرِ واقعی‌ای رخ نداده باشد، به فایل دست نمی‌زند."))
        ab_row.addWidget(self.chk_auto_backup, 1)
        self.btn_ab_path = QPushButton(T("مسیر فایل…"))
        self.btn_ab_path.setObjectName("FlatBtn")
        self.btn_ab_path.clicked.connect(self._pick_auto_backup_path)
        ab_row.addWidget(self.btn_ab_path)
        lay.addLayout(ab_row)
        self.ab_path_label = QLabel()
        self.ab_path_label.setObjectName("MutedLabel")
        self.ab_path_label.setWordWrap(True)
        self._ab_path = str(_ab48.get("path") or "")
        self._refresh_ab_path_label()
        lay.addWidget(self.ab_path_label)
        # بازبینی کاربر — «پشتیبان مگه نباید همه‌چی ذخیره کنه؟ دو حالت بزار»:
        # انتخاب نوع — ساده (فقط اطلاعات، سبک) یا کامل (با کشِ تامبنیل‌ها).
        self.chk_ab_cache = QCheckBox(
            T("پشتیبانِ کامل — کشِ تامبنیل‌ها هم داخل فایل باشد (حجم خیلی بزرگ‌تر)"))
        self.chk_ab_cache.setChecked(bool(_ab48.get("include_cache")))
        self.chk_ab_cache.setToolTip(
            T("• پشتیبانِ ساده (پیش‌فرض): فقط اطلاعات کتابخانه — مسیرها، وضعیت تماشا،\n"
            "امتیازها، دسته‌ها و ترجیحات. کوچک و سریع — بعد از بازیابی، تامبنیل‌ها\n"
            "دوباره از خودِ فایل‌ها ساخته می‌شوند (چند دقیقه برای پیش‌ساخت صبر).\n"
            "• پشتیبانِ کامل: کشِ تامبنیل‌ها هم داخل فایل است — بازیابی فوریِ کامل\n"
            "ولی حجم فایل مثل پشتیبانِ دستیِ شما بزرگ است (ده‌ها مگابایت)."))
        lay.addWidget(self.chk_ab_cache)

        # 0.37.0+0.39.0: «پاکسازی کامل» — همه‌ی داده‌های کنار برنامه + بازمانده‌ی سراسری
        self.btn_factory_reset = QPushButton(T("🧨 پاکسازی کامل و بازنشانی به حالت اولیه"))
        self.btn_factory_reset.setObjectName("FlatBtn")
        self.btn_factory_reset.setToolTip(
            T("تنظیمات، فهرست پوشه‌ها، دیتابیسِ وضعیت تماشا/دسته‌ها و کلِ کشِ "
            "تامبنیل‌ها (کنار برنامه) پاک و بازمانده‌ی مسیر سراسری قدیمی هم "
            "جمع می‌شود؛ برنامه دوباره راه می‌افتد"))
        self.btn_factory_reset.clicked.connect(self._factory_reset)
        lay.addWidget(self.btn_factory_reset)

        lay.addStretch(1)
        from appinfo import APP_VERSION, SOURCE_URL
        ver = QLabel(T("نسخه‌ی {0} — FolderDex").format(APP_VERSION))
        ver.setObjectName("MutedLabel")
        ver.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(ver)
        # لایسنس و آدرسِ سورس — تا اینجا جای لایسنس در رابط خالی
        # بود؛ اگر REPO_URL/SOURCE_URL به مخزنِ بدونِ کد اشاره کند همین‌جا لو می‌رود.
        lic = QLabel(T("GPL-3.0-or-later · سورس: {0}").format(SOURCE_URL))
        lic.setObjectName("MutedLabel")
        lic.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(lic)
        # GPLv3 §5d: رابطِ تعاملی باید اعلانِ کپی‌رایت، بی‌ضمانت‌بودن و
        # محلِ متنِ لایسنس را نشان بدهد:
        legal = QLabel(T("© 2026 Mohammad Jafari · بدون ضمانت · متن کامل مجوز در فایل LICENSE کنار برنامه"))
        legal.setObjectName("MutedLabel")
        legal.setAlignment(Qt.AlignmentFlag.AlignLeft)
        legal.setWordWrap(True)
        lay.addWidget(legal)
        self.tabs.addTab(tab, T("⚙ عمومی"))

    # ------------------------------------------------------------ هندلرها
    def _append(self, category: str, path: str):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(_display(category)))
        self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, category)
        self.table.setItem(r, 1, QTableWidgetItem(path))

    def _selected_rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)

    def _add(self):
        from .widgets import pick_directory
        path = pick_directory(self, T("انتخاب پوشه"))
        if not path:
            return
        guess = Path(path).name
        cats = self.store.categories()
        cat = CategoryDialog.pick(self.store, guess if guess in cats else "", self)
        if cat:
            self._append(cat, path)
            # اگر برای این دسته هنوز الگوی وضعیتی انتخاب نشده، همین‌جا پیشنهاد بده
            # (فیلم ← دیده شده، کتاب/مانگا ← خوانده شده، یا بدون ترکینگ/سفارشی)
            if self.store.cat_track_profile_key(cat) is None:
                key = pick_track_profile(self.store, cat, self)
                if key:
                    self.store.set_cat_track_profile(cat, key)
                    self._refresh_cats()   # باکس الگوی ردیف همین دسته همخوان شود

    def _edit(self):
        rows = self._selected_rows()
        if not rows:
            return
        r = rows[-1]
        current = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
        cat = CategoryDialog.pick(self.store, current, self)
        if cat:
            self.table.item(r, 0).setText(_display(cat))
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, cat)

    def _delete(self):
        for r in self._selected_rows():
            self.table.removeRow(r)

    def _refresh_cache_label(self, size: int):
        from scanner import human_size
        self.btn_clear_cache.setText(
            T("🧹 پاک کردن کش تصاویر (حجم فعلی: {0})").format(human_size(size))
        )

    # ------------------------------------------------------------ کمکیِ عملیاتِ طولانی با پروگرس‌بارِ قفل‌کننده
    def _run_backup_op(self, title: str, intro: str, fn):
        """اجرای fn(progress_cb) در نخِ پس‌زمینه با پروگرس‌بارِ مودال (درخواست کاربر).

        0.41.0 — کاربر: «یک پروگرس‌بار بزار که بگه چقدر پیشرفت داشته و وقتی هست
        برنامه قفل بشه و بنویسه لطفاً صبر کنید تا تمام شود و زمانِ تقریبی هم
        بنویسه». پنجره ApplicationModal است (کلِ برنامه قفل می‌شود)، دکمه‌ی
        لغو/بستن ندارد، و هر لحظه درصد + حجم + زمانِ تقریبیِ باقی‌مانده را از
        نرخِ واقعی نشان می‌دهد. خروجی: ("ok", نتیجه) یا ("err", exception)."""
        import queue as _q
        import threading as _th
        import time as _t
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QProgressDialog
        from scanner import human_size as _hs, fa as _fa

        def _remain(sec) -> str:
            sec = int(max(0, sec))
            if sec <= 1:
                return T("در حال اتمام…")
            if sec < 90:
                return T("≈ {0} ثانیه").format(_fa(sec))
            return T("≈ {0} دقیقه").format(_fa((sec + 30) // 60))

        que: "_q.Queue" = _q.Queue()
        t0 = _t.monotonic()
        dlg = QProgressDialog(intro, "", 0, 1000, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)   # قفلِ کل برنامه
        dlg.setCancelButton(None)                                    # بستن/لغو ممنوع
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(470)
        self.setEnabled(False)
        state = {"result": None, "error": None, "done": False}
        last = {"done": 0, "total": 1, "_ftotal": 1}

        def _cb(done, total, fdone, ftotal):
            que.put((int(done), int(total), int(fdone), int(ftotal)))

        def _work():
            try:
                state["result"] = fn(_cb)
            except Exception as e:                       # noqa: BLE001 — نمایش به کاربر
                dbg_log(f"_run_backup_op._work failed: {type(e).__name__}")
                state["error"] = e
            finally:
                que.put(("__end__",))
        _th.Thread(target=_work, daemon=True, name="backup-op").start()

        timer = QTimer(dlg)
        timer.setInterval(70)

        def _tick():
            try:
                while True:
                    m = que.get_nowait()
                    if m[0] == "__end__":
                        state["done"] = True
                    else:
                        done, total, fdone, ftotal = m
                        last.update(done=done, total=max(total, 1), _ftotal=max(ftotal, 1))
                        dlg.setValue(min(1000, int(done * 1000 / max(total, 1))))
                        elapsed = max(_t.monotonic() - t0, 0.25)
                        rate = done / elapsed if done > 0 else 0.0
                        remain = (total - done) / rate if rate > 0 else 0
                        pct = done * 100.0 / max(total, 1)
                        dlg.setLabelText(
                            T("{0}\n\nپیشرفت: {1}٪ — {2} از {3} · {4} فایل\nزمانِ تقریبیِ باقی‌مانده: {5}").format(intro, _fa(f'{pct:.0f}'), _hs(done), _hs(total), _fa(fdone), _remain(remain)))
            except _q.Empty:
                pass
            if state["done"]:
                timer.stop()
                self.setEnabled(True)
                dlg.done(0)
        timer.timeout.connect(_tick)
        timer.start()
        dlg.exec()                                        # حلقه‌ی مودال تا پایانِ کار
        dlg.close()
        dlg.deleteLater()
        if state["error"] is not None:
            return "err", state["error"]
        return "ok", state["result"]

    # ------------------------------------------------------------ خروجی/ورود اطلاعات
    def _refresh_ab_path_label(self):
        if self._ab_path:
            self.ab_path_label.setText(T("فایلِ مقصد: {0}").format(self._ab_path))
        else:
            # بازبینی کاربر: مسیر ندهی هم کار می‌کند — پیش‌فرض backups/ کنار
            # دیتابیس؛ دیگر «بدون مسیر هیچ اتفاقی نمی‌افتد» نداریم.
            _def48 = self.store.config_path.parent / "backups" / "auto-backup.mcbak"
            self.ab_path_label.setText(
                T("فایلِ مقصد: {0} (پیش‌فرض)").format(str(_def48)))

    def _pick_auto_backup_path(self):
        from PySide6.QtWidgets import QFileDialog
        import backup as _bk
        _start = self._ab_path or str(Path.home() / "FolderDex-auto.mcbak")
        dest, _sel = QFileDialog.getSaveFileName(
            self, T("فایلِ پشتیبانِ خودکار کجا بازنویسی شود؟"), _start,
            T("فایل پشتیبان FolderDex (*{0})").format(_bk.BACKUP_EXT))
        if dest:
            self._ab_path = str(dest)
            self._refresh_ab_path_label()

    def _export_backup(self):
        """از همه‌ی اطلاعاتِ کنار برنامه یک فایلِ پشتیبانِ ‎.mcbak‎ می‌سازد
        (درخواست کاربر — 0.40.0: «یک فایل با یک پسوند خاص»؛ 0.41.0: پروگرس‌بار+قفل)."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        import backup as _bk
        dest, _sel = QFileDialog.getSaveFileName(
            self, T("فایلِ پشتیبان کجا ذخیره شود؟"),
            str(Path.home() / _bk.default_backup_name()),
            T("فایل پشتیبان FolderDex (*{0})").format(_bk.BACKUP_EXT))
        if not dest:
            return
        from scanner import human_size, fa
        inc = bool(self.chk_backup_cache.isChecked())
        _ef, _eb = _bk.estimate_export_size(inc)
        intro = (T("لطفاً صبر کنید تا خروجی‌گرفتن از اطلاعات تمام شود.\nبرنامه تا پایانِ کار قفل است.\nحجمِ تقریبی: {0} فایل · {1} — زمانِ دقیق هنگام کار اعلام می‌شود").format(fa(_ef), human_size(_eb)))
        status, res = self._run_backup_op(
            T("خروجی‌گرفتن از اطلاعات (پشتیبان)"), intro,
            lambda cb: _bk.export_backup(Path(dest), include_cache=inc, progress=cb))
        if status == "err":
            QMessageBox.warning(self, T("خروجی‌گرفتن"),
                                T("خطا هنگام خروجی‌گرفتن:\n{0}").format(res))
            return
        out, files, bytes_ = res
        QMessageBox.information(
            self, T("خروجی‌گرفتن"),
            T("پشتیبان ساخته شد ✔\nفایل: {0}\n{1} فایل داخلش · {2}\n\nاین یک فایل است — جابه‌جاکردنش راحت است؛ جای امنی نگه‌ش دار. در هر نسخه/پوشه‌ی دیگری با «واردکردن اطلاعات» کاملاً برمی‌گردد.").format(out.name, fa(files), human_size(bytes_)))

    def _ask_import_mode(self) -> str | None:
        """دیالوگ انتخابِ حالتِ ورود پشتیبان (0.47.0) → «merge» | «replace» | None.

        به متدی جدا منتقل شده تا تست‌ها بتوانند انتخاب را بدون اجرای حلقه‌ی
        مودالِ واقعی (box.exec) بدانند/موک کنند."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(T("ورود اطلاعات"))
        box.setText(T("این پشتیبان چطور اعمال شود؟"))
        box.setInformativeText(T(
            "🔀 «ادغام» مواردِ پشتیبان (وضعیت تماشا، دسته‌ها، الگوها) را *اضافه* می‌کند — "
            "هیچ‌کدام از اطلاعاتِ فعلی پاک نمی‌شود و مواردِ تکراری به نفعِ نسخه‌ی فعلی رد می‌شوند.\n"
            "♻️ «جایگزینی» همه‌ی اطلاعاتِ فعلی را حذف و پشتیبان را بارگذاری می‌کند — "
            "پیش از آن خودکار یک اسنپ‌شاتِ امنیتی از وضعیت فعلی در پوشه‌ی backups کنار برنامه ساخته می‌شود.\n"
            "کشِ تامبنیل فقط در «جایگزینی» و اگر داخل پشتیبان باشد جایگزین می‌شود.\n"
            "🔒 برای امنیت، «برنامه‌های بازکننده‌ی دسته» از هیچ پشتیبانی وارد نمی‌شوند — "
            "بعداً خودت دوباره تعریفشان کن."))
        btn_merge = box.addButton(
            T("🔀 ادغام — حفظ فعلی‌ها (پیشنهادی)"), QMessageBox.ButtonRole.AcceptRole)
        btn_replace = box.addButton(
            T("♻️ جایگزینی کامل"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(T("انصراف"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_merge)
        box.exec()
        _clicked = box.clickedButton()
        if _clicked is btn_merge:
            return "merge"
        if _clicked is btn_replace:
            return "replace"
        return None

    def _import_backup(self):
        """بازیابی — 0.47.0 (درخواست کاربر: «قابلیت حفظ فایل‌های الان بعد از واردکردن بکاپ»).

        کاربر خودش حالت را انتخاب می‌کند:
          🔀 ادغام — هیچ‌کدام از اطلاعاتِ فعلی پاک نمی‌شود؛ مواردِ پشتیبان *اضافه*
             می‌شود (تکراری‌ها به نفعِ نسخه‌ی فعلی رد) + گزارشِ وجود مسیرها
             (تشخیص پشتیبانِ ساخته‌شده روی سیستمِ دیگر — گزارش کاربر: «چون هیچ‌کدام
             در آن سیستم نبود هیچی نشان داده نشد ولی پوشه‌هایم پاک شد»).
          ♻️ جایگزینی کامل — رفتار قبلی (آماده‌سازی + بازنویسی در بوتِ بعدی) ولی
             پیش از آن خودکار یک اسنپ‌شاتِ امنیتی از وضعیت فعلی در پوشه‌ی backups
             ساخته می‌شود تا بازگشت همیشه ممکن بماند.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        import backup as _bk
        import sys as _sys
        from portable import app_dir as _appd
        dest, _sel = QFileDialog.getOpenFileName(
            self, T("فایلِ پشتیبان را انتخاب کن"),
            str(Path.home()),
            T("فایل پشتیبان FolderDex (*{0})").format(_bk.BACKUP_EXT))
        if not dest:
            return
        ok, msg = _bk.validate_backup(Path(dest))
        if not ok:
            QMessageBox.warning(self, T("ورود اطلاعات"), msg)
            return

        # ---- انتخابِ حالت — ادغام (امن/پیش‌فرض) یا جایگزینی (مخرب + اسنپ‌شات)
        _mode47 = self._ask_import_mode()

        from scanner import human_size, fa
        _f0, _b0 = _bk.estimate_backup_size(Path(dest))

        if _mode47 == "merge":
            intro = (T("لطفاً صبر کنید تا ادغام اطلاعات تمام شود.\nبرنامه تا پایانِ کار قفل است — برنامه را نبند.\nحجمِ پشتیبان: {0} فایل · {1} — زمانِ دقیق هنگام کار اعلام می‌شود").format(fa(_f0), human_size(_b0)))
            _roots48 = [str(x.get("path") or "") for x in self.store.roots()]   # برای نقشه‌ی جابه‌جایی
            status, res = self._run_backup_op(
                T("ادغام اطلاعات از پشتیبان"), intro,
                lambda cb: _bk.merge_backup(Path(dest), self.store, progress=cb,
                                            remap_roots=_roots48))
            if status == "err":
                QMessageBox.warning(self, T("ورود اطلاعات"), str(res))
                return
            r = res or {}
            _lines = [
                T("ادغام انجام شد ✔"),
                "",
                T("• {0} موردِ تازه اضافه شد · {1} مورد از قبل بود (نسخه‌ی فعلی حفظ شد)").format(
                    fa(r.get("items_added", 0)), fa(r.get("items_existing", 0))),
                T("• {0} دسته‌ی تازه · {1} الگوی تازه اضافه شد").format(
                    fa(r.get("cats_added", 0)), fa(r.get("profiles_added", 0))),
            ]
            _pairs48 = r.get("remapped_pairs") or []
            if _pairs48:
                _o48, _n48 = _pairs48[0]
                _lines += ["", T("• {0} مورد با مسیرِ تازه‌ای که خودکار پیدا شد ادغام شد:\n{1} ← {2}").format(
                    fa(r.get("remapped_items", 0)), _o48, _n48)]
            if r.get("skipped_missing"):
                _lines += ["", T("• {0} مورد اضافه نشد — مسیرشان روی این سیستم نیست و معادلِ درستی هم پیدا نشد. اگر درایو/مسیرشان را وصل یا درست کنی، با «ادغام»ِ دوباره وارد می‌شوند. هیچ داده‌ای هم حذف نشده است.").format(
                    fa(r.get("skipped_missing", 0)))]
            if not r.get("items_added") and not r.get("skipped_missing") and not _pairs48:
                _lines += ["", T("مورد تازه‌ای نبود — همه‌ی محتویاتِ پشتیبان همین‌حالا هم در کتابخانه‌ات هست.")]
            _lines += ["", T("کشِ تامبنیل دست‌نخورده ماند — تامبنیلِ مواردِ تازه هنگام نمایش/پیش‌ساخت ساخته می‌شود.")]
            QMessageBox.information(self, T("ورود اطلاعات"), "\n".join(_lines))
            _ans47 = QMessageBox.question(
                self, T("ورود اطلاعات"),
                T("برای نمایشِ کامل تغییرات برنامه باید تازه شود.\nالان بازنشانی و اسکن مجدد شود؟"))
            if _ans47 == QMessageBox.StandardButton.Yes:
                mw = self.parent()
                if mw is not None and hasattr(mw, "request_restart"):
                    mw.request_restart()
            return

        if _mode47 != "replace":
            return

        # ---- ♻️ جایگزینی کامل — پیش از هر چیز، اسنپ‌شات امنیتیِ خودکار (بدون کش)
        _fs47, _bs47 = _bk.estimate_export_size(False)
        _tot47 = max(_bs47 + _b0, 1)

        def _op47(cb, _dst=dest, _bs=_bs47, _b=max(_b0, 1), _t=_tot47):  # noqa: B008 — snap-shot عمدیِ هنگامِ تعریف
            def _cb1(d, t, fd, ft):                 # فاز ۱: اسنپ‌شات → [0، _bs]
                cb(min(int(d or 0), _bs), _t, fd, ft)
            _snap = _bk.export_backup(_bk.auto_snapshot_path(),
                                      include_cache=False, progress=_cb1)

            def _cb2(d, t, fd, ft):                 # فاز ۲: آماده‌سازی → [_bs، _t]
                cb(_bs + min(int(d or 0), _b), _t, fd, ft)
            _flg = _bk.stage_import(Path(_dst), progress=_cb2,
                                    extra={"auto_snapshot": Path(_snap[0]).name})
            return _snap, _flg

        intro = (T("ابتدا اسنپ‌شات امنیتی از وضعیت فعلی ساخته و سپس پشتیبان آماده‌سازی می‌شود.\nبرنامه تا پایانِ کار قفل است — برنامه را نبند.\nحجمِ پشتیبان: {0} فایل · {1} — زمانِ دقیق هنگام کار اعلام می‌شود").format(fa(_f0), human_size(_b0)))
        status, res = self._run_backup_op(
            T("واردکردن اطلاعات از پشتیبان (جایگزینی + اسنپ‌شات امنیتی)"), intro, _op47)
        if status == "err":
            QMessageBox.warning(self, T("ورود اطلاعات"), str(res))
            return
        _snap47, _flag47 = res
        QMessageBox.information(
            self, T("ورود اطلاعات"),
            T("✔ آماده‌سازی انجام شد.\nپیش از جایگزینی، اسنپ‌شاتِ امنیتیِ خودکار از وضعیت فعلی ساخته شد:\n{0}\n(در پوشه‌ی backups کنار برنامه — اگر پشیمان شدی همین فایل را با «واردکردن اطلاعات» برگردان)\n\nبرنامه حالا بسته و دوباره باز می‌شود.").format(Path(_snap47[0]).name))
        # گزارش کاربر: «برنامه‌ی قبلی باز ماند و نمونه‌ی جدید باز شد» —
        # ریلانچ از دلِ این دیالوگِ مودال (حلقه‌های رویدادِ تودرتو) ناقص می‌ماند؛
        # پس مثل «پاکسازی کامل» فقط درخواست ثبت می‌شود و پنجره‌ی اصلی از مسیرِ
        # تمیزِ closeEvent خودش بسته و دوباره باز می‌شود (+ واچ‌دگ خروجِ قطعی).
        mw = self.parent()
        if mw is not None and hasattr(mw, "request_restart"):
            mw.request_restart()
            return
        if getattr(_sys, "frozen", False):
            ok = _relaunch_app([_sys.executable])
        else:
            ok = _relaunch_app([_sys.executable, str(_appd() / "main.py")])
        if ok:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        else:
            QMessageBox.information(
                self, T("ورود اطلاعات"),
                T("آماده‌سازی انجام شد؛ برنامه را دستی ببند و دوباره اجرا کن تا "
                "اطلاعاتِ جدید بارگذاری شود."))



    def _clear_cache(self):
        from PySide6.QtWidgets import QMessageBox
        from scanner import human_size
        from thumbs import cache_size, clear_cache
        before = cache_size(self.cache_dir)
        ans = QMessageBox.question(
            self, T("پاک کردن کش"),
            T("کش تصاویر و تامبنیل‌ها ({0}) پاک شود؟\nدر اولین نمایش بعدی دوباره ساخته می‌شود.").format(human_size(before)),
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        freed = clear_cache(self.cache_dir)
        # با پاک‌شدنِ کش، مانیفستِ تامبنیل‌ها هم بی‌اعتبار می‌شود تا پیش‌ساز
        # در اجرای بعد همه‌ی عناوین را یک‌بار هوشمندانه بازبینی/بازسازی کند و
        # هیچ تامبنیلِ گمشده‌ای پشتِ «ردکردنِ هوشمند» پنهان نماند.
        try:
            self.store.clear_thumb_manifests()
        except Exception as _exc_dbg:
            dbg_log(f"_clear_cache failed: {type(_exc_dbg).__name__}")
            pass
        self._refresh_cache_label(0)
        QMessageBox.information(
            self, T("انجام شد ✓"),
            T("کش پاک شد ({0} آزاد شد).\nپوسترها با «اسکن مجدد» و تامبنیل‌ها هنگام باز کردن جزئیات دوباره ساخته می‌شوند.").format(human_size(freed)),
        )

    # ------------------------------------------------------------ پاکسازی کامل
    def _factory_reset(self):
        """دکمه‌ی «پاکسازی کامل» (درخواست کاربر): بازنشانیِ همه‌ی داده‌ها به روزِ اول.

        دیتابیس و کانفیگِ باز روی ویندوز قفل‌اند و در همین اجرا حذف نمی‌شوند؛
        پس فقط پرچمِ بازنشانی (فهرست مقصدها) کنار برنامه نوشته می‌شود، برنامه
        دوباره راه می‌افتد و حذفِ واقعی در بوتِ بعدی — پیش از بازشدن هر فایلِ
        دیتابیسی — انجام می‌گیرد (paths.factory_reset_if_pending)."""
        from PySide6.QtWidgets import QMessageBox, QApplication
        ans = QMessageBox.warning(
            self, T("پاکسازی کامل"),
            T("همه‌ی داده‌های این برنامه پاک می‌شود:\n"
            "• تنظیمات، دسته‌ها، زیردسته‌ها و فهرستِ پوشه‌ها\n"
            "• دیتابیسِ وضعیتِ تماشا، دسته‌ها و زمانِ ویدیوها\n"
            "• الگوهای وضعیت سفارشی و برنامه‌های بازکننده\n"
            "• کلِ کشِ تامبنیل‌ها (کنار برنامه)\n"
            "• همه‌ی پشتیبان‌های داخلِ پوشه‌ی backups کنار برنامه\n"
            "• لاگ‌های عیب‌یابی کنار برنامه — اگر باشند\n"
            "• فایل‌های نمونه/دموی نسخه‌های قدیمی — اگر باشند\n"
            "• بازمانده‌ی مسیرِ سراسریِ قدیمی — اگر هست\n\n"
            "برنامه خودش بسته و دوباره باز می‌شود و مثل روزِ اول خالی است.\n"
            "مطمئنی؟"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            import sys as _sys
            import paths as _p
            from portable import app_dir as _appd
            targets: list = []
            for cand in (self.cache_dir, _p.user_data_dir(),
                         _p.thumb_cache_dir(), _appd() / ".thumb_cache",
                         _appd() / ".covers",
                         _appd() / "config.json", _appd() / "data.json",
                         _appd() / "data.db", _appd() / "data.db-shm",
                         _appd() / "data.db-wal", _appd() / "data.db-journal"):
                try:
                    s = str(Path(cand).resolve())
                    if s not in targets:
                        targets.append(s)
                except Exception as _exc_dbg:
                    dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                    continue
            # پاکسازی کاملِ فایل‌های دمو/نمونه‌ی نسخه‌های قدیمی.
            # توجه: config.example.json عمداً در این فهرست نیست — نمونه‌ی
            # مستندشده‌ی برنامه است (README) و با پاکسازی نباید حذف شود.
            for extra in ("data.demo.db",):
                try:
                    p = _appd() / extra
                    if p.exists():
                        s = str(p.resolve())
                        if s not in targets:
                            targets.append(s)
                except Exception as _exc_dbg:
                    dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                    pass
            # پوشه‌ی backups هم پاک شود
            try:
                bk_dir = _appd() / "backups"
                if bk_dir.is_dir():
                    s = str(bk_dir.resolve())
                    if s not in targets:
                        targets.append(s)
            except Exception as _exc_dbg:
                dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                pass
            # لاگ‌های دیباگ
            try:
                for log_name in ("folderdex-debug.log", "folderdex-error.log",
                                 "folderdex-launcher.log", "folderdex-diag.log",
                                 "covershelf-debug.log", "covershelf-error.log",
                                 "covershelf-launcher.log", "covershelf-diag.log",
                                 "media-cataloger-debug.log",
                                 "media-cataloger-error.log"):
                    lp = _appd() / log_name
                    if lp.exists():
                        s = str(lp.resolve())
                        if s not in targets:
                            targets.append(s)
            except Exception as _exc_dbg:
                dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                pass
            # بازمانده‌ی پوشه‌ی سراسریِ قدیمی (0.34.0–0.38.0) هم جمع شود —
            # «هیچ‌چیز در مسیر سراسری نماند»
            try:
                _legacy = _p._shared_data_dir()
                if _legacy is not None and _legacy.is_dir():
                    s = str(_legacy.resolve())
                    if s not in targets:
                        targets.append(s)
            except Exception as _exc_dbg:
                dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                pass
            _p.write_factory_reset_flag(targets)
            if getattr(_sys, "frozen", False):
                ok = _relaunch_app([_sys.executable])
            else:
                ok = _relaunch_app([_sys.executable, str(_appd() / "main.py")])
            if ok:
                app = QApplication.instance()
                if app is not None:
                    app.quit()
            else:
                QMessageBox.information(
                    self, T("پاکسازی کامل"),
                    T("دستورِ پاکسازی ثبت شد و در اجرای بعدی انجام می‌شود.\n"
                    "لطفاً برنامه را ببند و خودت دوباره بازش کن."))
        except Exception as e:
            dbg_log(f"_factory_reset failed: {type(e).__name__}")
            QMessageBox.warning(self, T("خطا"),
                                T("ثبتِ پاکسازی انجام نشد ({0}).").format(type(e).__name__))

    def _on_player_mode(self, _i):
        custom = self.player_combo.currentData() == "custom"
        self.player_edit.setVisible(custom)
        self._player_browse_btn.setVisible(custom)

    def _browse_player(self):
        from .widgets import pick_open_file
        f, _ = pick_open_file(self, T("انتخاب پخش‌کننده"), "",
                              T("برنامه (*.exe);;همه فایل‌ها (*)"))
        if f:
            self.player_edit.setText(f)

    def _recalc_durations(self):
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, T("بازمحاسبه‌ی زمان‌ها"),
            T("کش زمان‌ها پاک می‌شود و در اسکن بعدی همه‌ی ویدیوها دوباره محاسبه می‌شوند.\n"
            "مطمئنی؟"),
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.durations.clear()
        self.store.clear_all_manifests()
        self.chk_durations.setChecked(True)
        QMessageBox.information(self, T("انجام شد ✓"),
                                T("کش زمان‌ها پاک شد. با «ذخیره و اسکن مجدد» همه از نو محاسبه می‌شود."))

    @staticmethod
    def _roots_signature(roots) -> set:
        """اثر انگشت لیست مسیرها (ترتیب مهم نیست) — برای تشخیص نیاز به اسکن مجدد."""
        def norm(p):
            return str(p).strip().replace("/", "\\").rstrip("\\").lower()
        return {(norm(r.get("path", "")), str(r.get("category", "")).strip())
                for r in roots if r.get("path")}

    def _update_player_status(self, *_):
        """نمایش زنده: انتخاب فعلی به کدام پلیر واقعی حل می‌شود."""
        import watchtime
        res = watchtime.resolve_player({
            "mode": self.player_combo.currentData(),
            "path": self.player_edit.text().strip(),
        })
        kind = res["kind"]
        labels = {
            "mpv": T("mpv (ردیابی زنده و دقیق)"),
            "potplayer": T("PotPlayer (ردیابی واقعی زنده — از پنجره‌ی خودش)"),
            "vlc": T("VLC (رزومه + ردیابی تخمینی)"),
            "elapsed": T("پخش‌کننده‌ی سفارشی (ردیابی تخمینی)"),
            "default": T("پخش‌کننده‌ی پیش‌فرض ویندوز (بدون ردیابی!)"),
        }
        note = res.get("note") or ""
        if kind == "default":
            self.player_status.setStyleSheet("color:#fbbf24;")
            msg = "⚠ " + (note + " · " if note else "") + labels[kind]
        else:
            self.player_status.setStyleSheet("color:#4ade80;")
            where = Path(res["path"]).name if res.get("path") else ""
            msg = "✓ " + (note + " · " if note else "") + labels[kind] + \
                  (f" — {where}" if where else "")
        self.player_status.setText(msg)

    def accept(self):
        roots = []
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 1).text().strip()
            cat = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) or self.table.item(r, 0).text()
            if path and cat:
                roots.append({"path": path, "category": cat})
        # اسکن مجدد فقط وقتی لازم است: مسیر/دسته‌ی مسیر عوض شده، فال‌بک عوض شده،
        # یا محاسبه‌ی زمان روشن شده. تغییر پلیر/پوسته/تنظیمات عمومی → بدون اسکن.
        self.paths_changed = (
            self._roots_signature(roots) != self._orig_roots_sig
            or self.chk_fallback.isChecked() != self._orig_fallback
            or (self.chk_durations.isChecked() and not self._orig_durations)
        )
        # گزارش کاربر: «ذخیره را زدم چند ثانیه هنگ کرد» — همه‌ی setterها
        # زیر یک چترِ ذخیره‌ی دسته‌ای — قبلاً ۸+ نوشتنِ اتمیکِ متوالی روی دیسک
        # (به‌خصوص با آنتی‌ویروسِ بلادرنگِ ویندوز) رابط را چند ثانیه فریز می‌کرد؛
        # حالا فقط یک save_config + یک save_data در پایان انجام می‌شود.
        with self.store.defer_save():
            self.store.set_roots(roots, self.chk_fallback.isChecked())
            self.store.set_compute_durations(self.chk_durations.isChecked())
            self.store.set_archive_thumbs(self.chk_archive.isChecked())
            self.store.set_folder_thumbs_only(self.chk_folder_only.isChecked())
            self.store.set_prebuild_thumbs(self.chk_prebuild.isChecked())
            # الگوی وضعیت به‌ازای هر دسته — باکس هر ردیف (فیلم/کتاب/بدون/سفارشی)
            for i in range(self.cat_list.count()):
                li = self.cat_list.item(i)
                combo = self._cat_row_combo(self.cat_list, li)
                if combo is not None:
                    self.store.set_cat_track_profile(
                        li.data(Qt.ItemDataRole.UserRole), combo.currentData())
            if self.cache_dir is not None:
                self.store.set_cache_max_mb(self.cache_combo.currentData() or 0)
                # کش همیشه کنار برنامه است — تنظیمِ محل کش حذف شد.
                self.store.save_config()
            self.store.set_player_cfg(self.player_combo.currentData(),
                                      self.player_edit.text().strip())
            self.store.set_theme(self.theme_combo.currentData())
            # زبان + حالت عملکرد — زبان بعداً در _after_settings_save با
            # پیشنهادِ راه‌اندازیِ مجدد اعمال می‌شود؛ حالت عملکرد بلافاصله.
            self.store.set_language(self.lang_combo.currentData())
            self.store.set_perf_mode(self.perf_combo.currentData())
            # پشتیبان‌گیری خودکار — گزینه + مسیرِ انتخاب‌شده
            self.store.set_auto_backup(
                self.chk_auto_backup.isChecked(), self._ab_path,
                include_cache=self.chk_ab_cache.isChecked())
        super().accept()


# ---------------------------------------------------------------- ویرایش عنوان

class ItemEditDialog(QDialog):
    """ویرایش نام نمایشی، دسته، توضیحات، پوستر و علاقه‌مندی یک عنوان."""

    # (round 19 §۵) تغییرِ کاور از داخلِ دیالوگ فوری در Store اعمال می‌شود؛
    # مالکِ UI (details/main) باید همان لحظه خبردار شود — وگرنه بعد از Cancel
    # صفحه/کارتِ کهنه با کاورِ قبلی می‌ماند (متای جدید، UI قدیمی).
    coverTouched = Signal(object)

    def __init__(self, item, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.item = item
        self.setWindowTitle(T("ویرایش: {0}").format(store.title(item)))
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)

        self.ed_title = QLineEdit(store.title(item))
        form.addRow(T("نام نمایشی:"), self.ed_title)

        self.cb_cat = CategoryPicker(store)
        self.cb_cat.set_value(store.category_of(item))
        form.addRow(T("دسته‌بندی:"), self.cb_cat)

        poster_row = QHBoxLayout()
        # کاور سفارشی (round 11): دیگر مسیرِ آزاد نیست — نمایشِ نامِ کاورِ جاری
        # + انتخاب/حذف از طریقِ ماژولِ مشترکِ cover_actions (نرمال‌سازی در .covers)
        stc = store.cover_status(item.path)
        self._cover_missing = stc["missing"]
        self.lbl_cover = QLineEdit()
        self.lbl_cover.setReadOnly(True)
        self.lbl_cover.setPlaceholderText(T("آیکون پوشه"))
        self.lbl_cover.setText(stc["name"] if stc["kind"] != "none" else "")
        b_pick = QPushButton(T("انتخاب…"))
        b_del = QPushButton(T("حذف"))
        b_pick.clicked.connect(self._pick_cover)
        b_del.clicked.connect(self._clear_cover)
        poster_row.addWidget(self.lbl_cover, 1)
        poster_row.addWidget(b_pick)
        poster_row.addWidget(b_del)
        pw = QWidget()
        pw.setLayout(poster_row)
        poster_row.setContentsMargins(0, 0, 0, 0)
        form.addRow(T("پوستر سفارشی:"), pw)
        # هشدارِ نارنجی وقتی فایلِ کاورِ ذخیره‌شده پیدا نمی‌شود (B10)
        self.lbl_cover_warn = QLabel(
            T("فایل کاور پیدا نشد — آیکون پوشه نمایش داده می‌شود."))
        self.lbl_cover_warn.setStyleSheet("color:#f59e0b;")
        self.lbl_cover_warn.setVisible(self._cover_missing)
        form.addRow(self.lbl_cover_warn)

        lay.addLayout(form)

        lay.addWidget(QLabel(T("توضیحات:")))
        self.ed_desc = QPlainTextEdit(store.desc(item.path))
        self.ed_desc.setPlaceholderText(T("خلاصه داستان، نکات، هر چیزی…"))
        self.ed_desc.setMinimumHeight(90)
        lay.addWidget(self.ed_desc)

        self.chk_fav = QCheckBox(T("⭐ افزودن به علاقه‌مندی‌ها"))
        self.chk_fav.setChecked(store.fav(item.path))
        lay.addWidget(self.chk_fav)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        box.button(QDialogButtonBox.StandardButton.Save).setText(T("ذخیره"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _pick_cover(self):
        """انتخاب عکس از کاربر → نرمال‌سازی در .covers → ذخیره‌ی فوری متا."""
        from ui.cover_actions import pick_cover
        pick_cover(self, self.store, self.item, self._cover_changed)

    def _clear_cover(self):
        """حذفِ کاور سفارشی و بازگشت به آیکونِ پوشه."""
        from ui.cover_actions import clear_cover
        clear_cover(self, self.store, self.item, self._cover_changed)

    def _cover_changed(self, changed: bool):
        """هم‌گام‌سازیِ سطرِ کاور و هشدار پس از عمل (از نخِ UI صدا زده می‌شود)."""
        if not changed:
            return
        stc = self.store.cover_status(self.item.path)
        self._cover_missing = stc["missing"]
        self.lbl_cover.setText(stc["name"] if stc["kind"] != "none" else "")
        self.lbl_cover_warn.setVisible(self._cover_missing)
        # (round 19 §۵) مستقل از Save/Cancel — مالکِ UI بلافاصله تازه می‌شود
        try:
            self.coverTouched.emit(self.item)
        except RuntimeError:
            pass

    def accept(self):
        # poster دیگر از این فرم ذخیره نمی‌شود — کاور سفارشی با دکمه‌های
        # «انتخاب…/حذف» فوری اعمال می‌شود و مقدارِ کهنه دست نمی‌خورد
        self.store.set_meta(
            self.item.path,
            title=self.ed_title.text().strip() or None,
            category=self.cb_cat.value().strip() or None,
            description=self.ed_desc.toPlainText().strip() or None,
            favorite=self.chk_fav.isChecked() or None,
        )
        super().accept()


# ---------------------------------------------------------------- موارد گمشده
class MissingItemsDialog(QDialog):
    """مدیریت خودکار-کمکیِ موارد گمشده (0.48.0 — درخواست کاربر).

    برای عنوان‌هایی که زیر ریشه‌های فعلی ثبت‌اند ولی فایلشان دیگر نیست، دو
    انتخابِ دقیقِ خودِ کاربر: «🗑 حذف» یا «📂 مسیر جدید». انتقال مسیر، کلِ
    اطلاعاتِ مرتبط (وضعیت تماشا، دسته، توضیح، امتیاز) را می‌برد — کلیدهای
    تماشا نسبی‌اند پس سالم می‌مانند."""

    @staticmethod
    def _name_of(p: str) -> str:
        return str(p).replace("\\", "/").rstrip("/").split("/")[-1] or str(p)

    def __init__(self, store: Store, missing: list, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(T("⚠ مدیریت موارد گمشده"))
        self.setMinimumWidth(640)
        self.setMinimumHeight(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self._remaining = list(missing)
        info = QLabel(T(
            "این {0} عنوان زیر مسیرهای فعلی‌ات ثبت‌شده‌اند ولی فایلشان دیگر آنجا نیست "
            "(حذف یا جابه‌جا شده‌اند؟). برای هرکدام انتخاب کن: «حذف از کتابخانه» یا "
            "«مسیر جدید…» تا همه‌ی اطلاعاتِشان (وضعیت تماشا، دسته، امتیاز) به مسیرِ "
            "تازه منتقل شود.").format(str(len(missing))))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("MutedLabel")
        lay.addWidget(self.count_lbl)

        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        self.rows = QVBoxLayout(host)
        self.rows.setSpacing(4)
        self._row_widgets = {}               # path → (ویدجت ردیف)
        for p in self._remaining:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            h.setSpacing(6)
            name = QLabel(self._name_of(p))
            name.setToolTip(str(p))
            h.addWidget(name, 1)
            btn_del = QPushButton(T("🗑 حذف"))
            btn_del.setObjectName("FlatBtn")
            btn_move = QPushButton(T("📂 مسیر جدید…"))
            btn_move.setObjectName("FlatBtn")
            h.addWidget(btn_del)
            h.addWidget(btn_move)
            btn_del.clicked.connect(lambda _c=False, _p=p, _r=row: self._remove_one(_p, _r))
            btn_move.clicked.connect(lambda _c=False, _p=p, _r=row: self._move_one(_p, _r))
            self._row_widgets[p] = row
            self.rows.addWidget(row)
        self.rows.addStretch(1)
        area.setWidget(host)
        lay.addWidget(area, 1)

        btns = QHBoxLayout()
        btn_all = QPushButton(T("🗑 حذف همه ({0})").format(str(len(missing))))
        btn_all.setObjectName("FlatBtn")
        btn_all.clicked.connect(self._remove_all)
        btn_close = QPushButton(T("بستن"))
        btn_close.setObjectName("PrimaryBtn")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_all)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        lay.addLayout(btns)
        self._sync_count()

    def _sync_count(self):
        self.count_lbl.setText(T("مانده: {0} مورد").format(str(len(self._remaining))))
        if not self._remaining:
            self.accept()

    def _remove_one(self, p: str, row: QWidget):
        try:
            self.store.remove_item(p)
        except Exception as _exc_dbg:
            dbg_log(f"_remove_one failed: {type(_exc_dbg).__name__}")
            pass
        if p in self._remaining:
            self._remaining.remove(p)
        row.setVisible(False)
        self._sync_count()

    def _remove_all(self):
        from PySide6.QtWidgets import QMessageBox
        for p in list(self._remaining):
            try:
                self.store.remove_item(p)
            except Exception as _exc_dbg:
                dbg_log(f"_remove_all failed: {type(_exc_dbg).__name__}")
                pass
        self._remaining = []
        QMessageBox.information(self, T("مدیریت موارد گمشده"),
                                T("همه‌ی مواردِ گمشده از کتابخانه حذف شدند ✔"))
        self.accept()

    def _move_one(self, p: str, row: QWidget):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        dest = QFileDialog.getExistingDirectory(
            self, T("مسیرِ تازه‌ی «{0}» کجاست؟").format(self._name_of(p)))
        if not dest:
            return
        try:
            ok = self.store.move_item(p, dest)
        except Exception as _exc_dbg:
            dbg_log(f"_move_one failed: {type(_exc_dbg).__name__}")
            ok = False
        if not ok:
            QMessageBox.warning(self, T("مدیریت موارد گمشده"),
                                T("انتقال انجام نشد — در مسیرِ مقصد از قبل اطلاعاتی هست."))
            return
        if p in self._remaining:
            self._remaining.remove(p)
        row.setVisible(False)
        self._sync_count()
