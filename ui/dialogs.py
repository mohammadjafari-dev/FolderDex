# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Dialogs: pick/create categories (with subcategory support), manage roots, edit a title."""
from __future__ import annotations

from i18n import T
from scanner import fa

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from store import Store
from watchtime import dbg_log

_NEW = "__new__"
_NO_PARENT = "__root__"


class NoWheelComboBox(QComboBox):
    """A combo box whose value does not change on mouse wheel:
    the value only changes when an item is clicked.

    Scrolling down to reach the settings at the bottom used to change other
    settings — they should only change when clicked. Qt's
    default changes a combo box's value when the cursor merely hovers over it
    (even without click/focus) — in the scrollable settings dialog that meant
    unintended theme/player/... switches while scrolling.

    Rule: pop-up closed -> the wheel event is ignored so it reaches the outer
    scroll; pop-up open -> the default behavior (moving through the open
    list) is kept."""

    def wheelEvent(self, event):            # noqa: N802 (Qt signature)
        try:
            v = self.view()
            if v is not None and v.isVisible():
                return super().wheelEvent(event)
        except Exception as _exc_dbg:
            dbg_log(f"wheelEvent failed: {type(_exc_dbg).__name__}")
            pass
        event.ignore()


def _relaunch_app(argv: list) -> bool:
    """Relaunches the app in an independent, fully hidden process.

    Used by "factory reset": the reset flag is written, and this fresh
    process wipes all data during its own boot. No extra console windows.
    Tests patch it so no real process is spawned. Returns: success?"""
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
    """Display form of a category path: Anime/Watched -> Anime › Watched.
    Common names are translated in English mode (the stored value
    stays fixed)."""
    from i18n import disp_cat
    return disp_cat(path).replace("/", " › ")


# ---------------------------------------------------------------- Category dialog

class CategoryDialog(QDialog):
    """Pick from existing categories, or create a new category/subcategory."""

    def __init__(self, store: Store, current: str = "", parent=None, create_only: bool = False):
        super().__init__(parent)
        self.store = store
        self.create_only = create_only
        self.setWindowTitle(T("ساخت دسته/زیردسته") if create_only else T("انتخاب دسته‌بندی"))
        # The word "category" used to be unreadable and outside the
        # frame — the minimum width is derived from the content's real
        # sizeHint, not the fixed 380, so no label is clipped with different
        # fonts/display scaling.
        self.setMinimumWidth(0)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # --- Pick from existing ---
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

        # --- Create new ---
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

        # Minimum width = the content's real size (safe floor of 420) — the
        # layout is fully built so sizeHint is valid; the dialog never gets
        # smaller than the label texts.
        self.setMinimumWidth(max(420, self.sizeHint().width()))

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
    """Category dropdown: pick from existing ones + the "create new" entry."""

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
                self.set_value(self._value)  # fall back to the previous selection
        else:
            self._value = self.currentData()


# ---------------------------------------------------------------- Track profiles

class TrackProfileEditDialog(QDialog):
    """Create/edit a custom track profile — a name plus labels for the three
    states.

    Example: "Workout" <- done / in progress / not done
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
    """Manage custom track profiles: list + add/edit/delete."""

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


# ---------------------------------------------------------------- Category opener apps

class _AppPickerDialog(QDialog):
    """App picker dialog — all installed programs with icons + search."""

    def __init__(self, parent, ext: str, programs: list):
        super().__init__(parent)
        self.ext = ext
        self.selected_app = None
        self.setWindowTitle(T("انتخاب برنامه برای فایل‌های .{0}").format(ext))
        self.setMinimumSize(500, 500)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        # Search
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(T("🔍 جستجو:")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(T("نام برنامه را تایپ کن…"))
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)
        lay.addLayout(search_row)

        # Program list with icons
        from PySide6.QtCore import QSize
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setSpacing(1)
        self.list.itemDoubleClicked.connect(self._accept_selection)
        lay.addWidget(self.list, 1)

        # QFileIconProvider — created once and shared by every lookup.
        # Important: QFileIconProvider lives in QtWidgets, not QtCore;
        # importing it from QtCore raised an ImportError right at dialog
        # construction — the whole "pick default app" dialog (with program
        # names+icons) never opened (the error vanished silently inside
        # clicked.connect).
        from PySide6.QtWidgets import QFileIconProvider
        self._icon_provider = QFileIconProvider()
        self._icon_cache = {}  # icon cache — avoids re-extraction

        # Populate the list
        self._all_programs = list(programs)
        self._populate()

        # Buttons
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
        """Extract an exe icon — cached, via QFileIconProvider, SHGetFileInfoW fallback."""
        import os, sys
        from PySide6.QtCore import QFileInfo
        from PySide6.QtGui import QIcon, QPixmap, QImage
        if not path or not os.path.isfile(path):
            return QIcon()
        # cache — return right away if already extracted
        if path in self._icon_cache:
            return self._icon_cache[path]
        icon = QIcon()
        # Method 1: QFileIconProvider
        try:
            icon = self._icon_provider.icon(QFileInfo(path))
            if not icon.isNull() and icon.availableSizes():
                self._icon_cache[path] = icon
                return icon
        except Exception as _exc_dbg:
            dbg_log(f"_get_icon failed: {type(_exc_dbg).__name__}")
            pass
        # Method 2: SHGetFileInfoW (Windows — more reliable for exe)
        if sys.platform == "win32":
            try:
                import ctypes

                # Win64 signatures : without argtypes/restype every
                # 64-bit handle (HDC/HBITMAP/HICON/HGDIOBJ) was truncated to
                # 32 bits by ctypes' c_int default — wrong-address crashes and
                # leaked GDI objects. All of them are pointer-sized.
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
                user32 = ctypes.windll.user32
                gdi32 = ctypes.windll.gdi32
                shell32.SHGetFileInfoW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_uint32,
                    ctypes.POINTER(SHFILEINFO), ctypes.c_uint32, ctypes.c_uint32]
                shell32.SHGetFileInfoW.restype = ctypes.c_void_p  # DWORD_PTR
                user32.GetDC.argtypes = [ctypes.c_void_p]
                user32.GetDC.restype = ctypes.c_void_p            # HDC
                user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                user32.ReleaseDC.restype = ctypes.c_int           # BOOL
                user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_void_p]
                user32.FillRect.restype = ctypes.c_int            # BOOL
                user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_void_p,
                                              ctypes.c_int, ctypes.c_int,
                                              ctypes.c_uint, ctypes.c_void_p,
                                              ctypes.c_uint]
                user32.DrawIconEx.restype = ctypes.c_int          # BOOL
                user32.DestroyIcon.argtypes = [ctypes.c_void_p]
                user32.DestroyIcon.restype = ctypes.c_int         # BOOL
                gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
                gdi32.CreateCompatibleDC.restype = ctypes.c_void_p    # HDC
                gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p,
                                                         ctypes.c_int, ctypes.c_int]
                gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p  # HBITMAP
                gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                gdi32.SelectObject.restype = ctypes.c_void_p      # HGDIOBJ
                gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
                gdi32.DeleteObject.restype = ctypes.c_int         # BOOL
                gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
                gdi32.DeleteDC.restype = ctypes.c_int             # BOOL
                gdi32.GetStockObject.argtypes = [ctypes.c_int]
                gdi32.GetStockObject.restype = ctypes.c_void_p    # HBRUSH
                gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_uint32, ctypes.c_uint32,
                                            ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_uint32]
                gdi32.GetDIBits.restype = ctypes.c_int            # BOOL (scanlines)

                ret = shell32.SHGetFileInfoW(
                    str(path), 0,
                    ctypes.byref(shinfo), ctypes.sizeof(shinfo),
                    SHGFI_ICON | SHGFI_SMALLICON)
                if ret and shinfo.hIcon:
                    # Convert the HICON to a QPixmap via a memory DC
                    sz = 32
                    hdc = user32.GetDC(0)
                    hdc_mem = gdi32.CreateCompatibleDC(hdc)
                    hbm = gdi32.CreateCompatibleBitmap(hdc, sz, sz)
                    try:
                        if hdc and hdc_mem and hbm:
                            gdi32.SelectObject(hdc_mem, hbm)
                            # Draw the icon onto the bitmap
                            user32.FillRect(
                                hdc_mem,
                                ctypes.byref((ctypes.c_long * 4)(0, 0, sz, sz)),
                                gdi32.GetStockObject(0))  # WHITE_BRUSH
                            user32.DrawIconEx(hdc_mem, 0, 0, shinfo.hIcon, sz,
                                              sz, 0, None, 0x0003)  # DI_NORMAL
                            # Read the pixels into a QImage — the header MUST
                            # be a full 40-byte BITMAPINFOHEADER :
                            # the old 24-byte (7-field) struct declared
                            # biSize=40, so GetDIBits read/wrote ~16 bytes
                            # past the buffer (heap corruption).
                            class BITMAPINFOHEADER(ctypes.Structure):
                                _fields_ = [
                                    ("biSize", ctypes.c_uint32),
                                    ("biWidth", ctypes.c_int32),
                                    ("biHeight", ctypes.c_int32),
                                    ("biPlanes", ctypes.c_ushort),
                                    ("biBitCount", ctypes.c_ushort),
                                    ("biCompression", ctypes.c_uint32),
                                    ("biSizeImage", ctypes.c_uint32),
                                    ("biXPelsPerMeter", ctypes.c_int32),
                                    ("biYPelsPerMeter", ctypes.c_int32),
                                    ("biClrUsed", ctypes.c_uint32),
                                    ("biClrImportant", ctypes.c_uint32),
                                ]
                            assert ctypes.sizeof(BITMAPINFOHEADER) == 40
                            class BITMAPINFO(ctypes.Structure):
                                _fields_ = [("bmiHeader", BITMAPINFOHEADER)]
                            bi = BITMAPINFO()
                            ctypes.memset(ctypes.byref(bi), 0, ctypes.sizeof(bi))
                            bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                            bi.bmiHeader.biWidth = sz
                            bi.bmiHeader.biHeight = -sz  # top-down
                            bi.bmiHeader.biPlanes = 1
                            bi.bmiHeader.biBitCount = 32
                            buf = (ctypes.c_ubyte * (sz * sz * 4))()
                            gdi32.GetDIBits(hdc_mem, hbm, 0, sz, buf,
                                            ctypes.byref(bi), 0)
                            img = QImage(bytes(buf), sz, sz,
                                         QImage.Format.Format_ARGB32)
                            pm = QPixmap.fromImage(img)
                            result = QIcon(pm)
                            self._icon_cache[path] = result
                            return result
                    finally:
                        # Every GDI/USER object is released on every path —
                        # an exception mid-conversion used to leak them all
                        if hbm:
                            gdi32.DeleteObject(hbm)
                        if hdc_mem:
                            gdi32.DeleteDC(hdc_mem)
                        if hdc:
                            user32.ReleaseDC(0, hdc)
                        if shinfo.hIcon:
                            user32.DestroyIcon(shinfo.hIcon)
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
    """Scan installed Windows programs — Start Menu + Program Files.
    Returns: [(name, exe_path), ...] sorted."""
    import sys, os
    if sys.platform != "win32":
        return []
    progs = {}  # name_lower → (name, path)

    # 1) Program Files — fastest and most reliable source
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

    # 2) Start Menu — shortcuts
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
                            # approximate path: the lnk itself, but the name is good
                            lnk_path = os.path.join(root, f)
                            # try to read the target
                            target = _read_lnk_target(lnk_path)
                            if target and target.lower().endswith(".exe") and os.path.isfile(target):
                                progs[key] = (name, target)
                            elif target and os.path.isfile(target):
                                progs[key] = (name, target)
        except OSError:
            pass

    return sorted(progs.values(), key=lambda x: x[0].lower())[:150]


def _read_lnk_target(lnk_path: str) -> str:
    """Read the target path of a Windows .lnk shortcut — no pywin32 needed."""
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
    """Assign default apps for opening each extension's files within a
    category.

    Two steps — first the category is picked, then the
    real extensions present in it are listed. Per extension the user can
    choose an app or leave the Windows default."""

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

        # ----- Top row: category picker -----
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

        # ----- Extensions table -----
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

        # ----- Buttons -----
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

        # Initial display
        if self.cat_combo.count() > 0:
            self._on_cat_changed(0)

    def _on_cat_changed(self, _idx):
        """Category changed -> collect its real extensions and rebuild the table."""
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

        # Current rules for this category
        openers = self.store.cat_openers(cat)
        ext_app = {}   # ext → app path
        for rule in openers:
            for e in rule.get("exts", []):
                ext_app[e.lower().lstrip(".")] = rule.get("app", "")

        for ext in sorted(exts):
            row = self.ext_table.rowCount()
            self.ext_table.insertRow(row)
            # Extension
            ext_item = QTableWidgetItem("." + ext)
            ext_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.ext_table.setItem(row, 0, ext_item)
            # App
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
            # Change button
            btn = QPushButton(T("تغییر…"))
            btn.setFixedWidth(90)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, _e=ext, _r=row: self._pick_app(_e, _r))
            self.ext_table.setCellWidget(row, 2, btn)

    def _collect_exts(self, cat: str) -> set:
        """Collect every extension present in the files of a category's items."""
        import os as _os
        from scanner import _walkable_dirs as _wdirs49, _winfix as _wf49
        exts = set()
        # Method 1: from the items list (when available — fast)
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
                    for root, dirs, names in _os.walk(_wf49(path)):
                        _wdirs49(root, dirs)      # junction/ignored/hidden guard
                        for n in names:
                            s = Path(n).suffix.lower().lstrip(".")
                            if s and not n.startswith("."):
                                exts.add(s)
                except OSError:
                    pass
        # Method 2: with no items, scan the roots directly
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
                        for dr, _ds, ns in _os.walk(_wf49(base)):
                            _wdirs49(dr, _ds)     # same guard here
                            for n in ns:
                                s = Path(n).suffix.lower().lstrip(".")
                                if s and not n.startswith("."):
                                    exts.add(s)
                except OSError:
                    pass
        return exts

    @staticmethod
    def _scan_installed_programs() -> list[tuple[str, str]]:
        """Scan all installed programs — Start Menu shortcuts +
        Program Files. Returns: [(name, exe_path), ...] sorted by name."""
        import sys, os
        if sys.platform != "win32":
            return []
        progs = {}  # name_lower → (name, path)
        # 1) Start Menu shortcuts (best source — every installed program)
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
                                # read the target from the shortcut
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
        # 2) Program Files — direct exes
        for pf in (os.environ.get("ProgramFiles", ""),
                    os.environ.get("ProgramFiles(x86)", ""),
                    os.environ.get("LocalAppData", "") + r"\Programs"):
            if not pf or not os.path.isdir(pf):
                continue
            try:
                for entry in sorted(os.scandir(pf), key=lambda e: e.name.lower()):
                    if entry.is_dir():
                        # find the main exe in the folder (usually named like the folder)
                        try:
                            for f2 in os.scandir(entry.path):
                                if f2.is_file() and f2.name.lower().endswith(".exe"):
                                    name = os.path.splitext(f2.name)[0]
                                    key = name.lower()
                                    if key not in progs:
                                        progs[key] = (name, f2.path)
                                    break  # only the first exe
                        except OSError:
                            pass
                    elif entry.is_file() and entry.name.lower().endswith(".exe"):
                        name = os.path.splitext(entry.name)[0]
                        key = name.lower()
                        if key not in progs:
                            progs[key] = (name, entry.path)
            except OSError:
                pass
        # 3) Uninstall registry
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
                                    # find an exe in the install location
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
        return result[:100]  # at most 100 programs

    def _pick_app(self, ext: str, row: int):
        """Pick an app — custom dialog listing installed programs with icons."""
        cat = self.cat_combo.currentData()
        if not cat:
            return
        # Scan the programs
        all_progs = self._scan_installed_programs()
        # App picker dialog
        dlg = _AppPickerDialog(self, ext, all_progs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        app = dlg.selected_app
        if not app:
            return
        # Update the rules
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
        # Update the table row
        from pathlib import Path as _P
        app_name = _P(app).stem
        app_item = self.ext_table.item(row, 1)
        if app_item:
            app_item.setText(f"{app_name}  ({app})")
            app_item.setToolTip(f"{app_name}  ({app})")

    def _set_all_to_one(self):
        """One app for every extension in this category."""
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
        # Update the table
        from pathlib import Path as _P
        app_name = _P(app).stem
        for row in range(self.ext_table.rowCount()):
            app_item = self.ext_table.item(row, 1)
            if app_item:
                app_item.setText(f"{app_name}  ({app})")
                app_item.setToolTip(f"{app_name}  ({app})")

    def _reset_all(self):
        """Return every extension in this category to the Windows default."""
        cat = self.cat_combo.currentData()
        if not cat:
            return
        if self.ext_table.rowCount() == 0:
            return
        self.store.set_cat_openers(cat, [])
        # Update the table
        for row in range(self.ext_table.rowCount()):
            app_item = self.ext_table.item(row, 1)
            if app_item:
                app_item.setText(T("پیش‌فرض ویندوز"))
                app_item.setToolTip(T("پیش‌فرض ویندوز"))


def pick_track_profile(store: Store, cat: str, parent=None) -> str | None:
    """Small dialog to pick a track profile while adding a path -> key or None (cancel)."""
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
        combo.addItem(T(title), key)   # built-ins translated on display; customs stay as typed
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


# ---------------------------------------------------------------- Settings (all in one place)

class SettingsDialog(QDialog):
    """Single settings window: roots, categories (with new-category creation), general."""

    def __init__(self, store: Store, parent=None, cache_dir=None, durations=None):
        super().__init__(parent)
        self.store = store
        self.cache_dir = cache_dir
        self.durations = durations
        self.setWindowTitle(T("تنظیمات"))
        self.setMinimumSize(700, 560)

        # Is a rescan needed? (only for changed paths/fallback/durations toggle)
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

    # ------------------------------------------------------------ Tab 1: roots
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

    # ------------------------------------------------------------ Tab 2: categories
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

        # The word "category" used to be unreadable and outside the
        # frame — four buttons in one row did not fit the 700px minimum
        # width and the last ones were clipped off; now two short rows — safe
        # at any width.
        btns = QHBoxLayout()
        b_addcat = QPushButton(T("➕ افزودن دسته/زیردسته‌ی جدید…"))
        b_addcat.setObjectName("PrimaryBtn")
        b_addcat.clicked.connect(self._add_category)
        self.btn_delcat = QPushButton(T("🗑 حذف دسته‌ی بلااستفاده"))
        self.btn_delcat.setObjectName("FlatBtn")
        self.btn_delcat.clicked.connect(self._del_category_confirm)
        btns.addWidget(b_addcat)
        btns.addWidget(self.btn_delcat)
        btns.addStretch(1)
        lay.addLayout(btns)

        btns2 = QHBoxLayout()
        b_profs = QPushButton(T("🧩 الگوهای وضعیت سفارشی…"))
        b_profs.clicked.connect(self._manage_track_profiles)
        b_openers = QPushButton(T("📂 برنامه‌های بازکننده‌ی دسته‌ها…"))
        b_openers.setObjectName("FlatBtn")
        b_openers.clicked.connect(self._manage_cat_openers)
        btns2.addWidget(b_profs)
        btns2.addWidget(b_openers)
        btns2.addStretch(1)
        lay.addLayout(btns2)

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
        self._refresh_cats()   # refresh profile boxes with the added/removed customs

    def _manage_cat_openers(self):
        # Hand over the item list for smart extension collection
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
        """The track profile box of a category row (row = a "name + box" widget)."""
        w = cat_list.itemWidget(li)
        if w is None:
            return None
        if isinstance(w, QComboBox):      # compatibility with rows lacking the composite widget
            return w
        return w.findChild(QComboBox)

    def _refresh_cats(self):
        # Keep the current boxes so unsaved picks survive category add/remove
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
            # Composite row "category name + profile box" — previously the
            # row held only the box and setItemWidget covered the item's own
            # text, hiding the category name
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2)
            hl.setSpacing(10)
            # A color swatch button per category: click opens a menu (pick
            # color/automatic/copy code).
            sw = QPushButton()
            sw.setFixedSize(24, 24)
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setToolTip(T("🎨 رنگِ دسته — انتخاب رنگ، بازگشت به خودکار، کپیِ کد"))
            sw.setStyleSheet(
                f"QPushButton {{ background: {self.store.color(c)};"
                f" border: 1px solid rgba(128,128,128,0.55);"
                f" border-radius: 7px; }}"
                f"QPushButton:hover {{ border: 1px solid rgba(200,200,200,0.9); }}")
            sw.clicked.connect(lambda _chk=False, cc=c, btn=sw: self._pick_cat_color(cc, btn))
            hl.addWidget(sw)
            name_lbl = QLabel(_display(c) + ("" if used else T("   (بلااستفاده)")))
            name_lbl.setStyleSheet(
                f"color: {self.store.color(c)}; font-weight: bold;")
            hl.addWidget(name_lbl, 1)
            combo = NoWheelComboBox()
            combo.setMinimumWidth(220)
            for key, title in self.store.profile_options():
                combo.addItem(T(title), key)   # profile titles translated at display time
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

    def _pick_cat_color(self, cat: str, swatch: QWidget | None = None):
        """Color menu for a category ("can't the category
        colors be changed?"): pick a custom color, return to automatic, copy
        the code. Colors are stored in category_colors; the sidebar/cards/
        details all read store.color(), so one refresh updates them after
        saving."""
        menu = QMenu(self)
        a_pick = menu.addAction(T("انتخاب رنگ…"))
        a_auto = menu.addAction(T("بازگشت به رنگِ خودکار"))
        a_copy = menu.addAction(T("کپی کد رنگ"))
        if swatch is not None:
            act = menu.exec(swatch.mapToGlobal(swatch.rect().bottomLeft()))
        else:
            act = menu.exec(self.mapToGlobal(self.rect().center()))
        menu.deleteLater()   # QObjects pile up in a long session — free it deterministically
        if act is None:
            return
        if act is a_pick:
            col = QColorDialog.getColor(QColor(self.store.color(cat)), self,
                                        T("🎨 رنگِ دسته"))
            if col.isValid():
                if self.store.set_category_color(cat, col.name()):
                    self.cats_changed = True
                    self._refresh_cats()
        elif act is a_auto:
            if self.store.set_category_color(cat, None):
                self.cats_changed = True
                self._refresh_cats()
        elif act is a_copy:
            QGuiApplication.clipboard().setText(self.store.color(cat))

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
        """Actual deletion (unused only) — no dialog, for tests/reuse."""
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

    # ------------------------------------------------------------ Tab 3: general
    def _build_general_tab(self):
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        inner = QWidget()
        tab.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)

        # --- Theme ---
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        theme_row.addWidget(QLabel(T("🎨 پوسته‌ی برنامه:")))
        from qss import THEMES, theme_label
        self.theme_combo = NoWheelComboBox()
        for key in THEMES:
            self.theme_combo.addItem(T(theme_label(key)), key)   # label translated, key fixed
        idx = self.theme_combo.findData(self.store.theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        theme_row.addWidget(self.theme_combo, 1)
        theme_row.addStretch(1)
        lay.addLayout(theme_row)

        # --- Language (bilingual: English default + persistent) ---
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

        # --- Performance mode (settings review: pin the worker count) ---
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

        # --- Video durations ---
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

        # --- Thumbnails from inside archives ---
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

        # --- Folder-only thumbnails ---
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

        # --- Thumbnail prebuild after scan ---
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

        # The "prebuild heavy files" checkbox was removed —
        # enabling prebuild means building all thumbnails (light+heavy) in
        # the background, deferring to the UI.


        # --- Player for resume/tracking ---
        lay.addSpacing(6)
        lay.addWidget(QLabel(T("🎬 پخش‌کننده برای پخش و ردیابی تماشا:"), objectName="SectionHeader"))
        player_row = QHBoxLayout()
        player_row.setSpacing(6)
        self.player_combo = NoWheelComboBox()
        # the combo had only two items ("potplayer"/"default") while
        # resolve_player supports auto/mpv/vlc/potplayer/custom/default — any
        # other stored mode fell back to index 0 and pressing Save silently
        # rewrote mpv/vlc/auto → "potplayer" (tracking stopped working).
        self.player_combo.addItem(T("خودکار — بهترین پخش‌کننده‌ی موجود"), "auto")
        self.player_combo.addItem(T("mpv — ردیابی دقیق زنده (IPC)"), "mpv")
        self.player_combo.addItem(T("VLC — رزومه + ردیابی تخمینی"), "vlc")
        self.player_combo.addItem(T("PotPlayer — ردیابی خودکار ترک/زیرنویس + رزومه"), "potplayer")
        self.player_combo.addItem(T("پخش‌کننده‌ی دلخواه (فایل exe)…"), "custom")
        self.player_combo.addItem(T("پخش‌کننده‌ی پیش‌فرض ویندوز — بدون ردیابی"), "default")
        cfg = self.store.player_cfg
        mode = str(cfg.get("mode", "auto") or "auto")
        idx = self.player_combo.findData(mode)
        if idx < 0:
            # An unknown stored mode is kept visible as "custom" with its path
            # (player_edit below already carries the stored path) — never
            # silently rewritten just by opening this dialog.
            idx = self.player_combo.findData("custom")
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

        # Live feedback: does the current pick actually resolve? (so no setting is silently ignored)
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

        # --- Cache ---
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
            if idx < 0:     # custom value stored in config — add its option
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

            # The thumbnail cache always lives beside the app —
            # the location combo and global/custom path option were removed
            # removed.

            self.btn_clear_cache = QPushButton(T("🧹 پاک کردن کش تصاویر"))
            self.btn_clear_cache.setObjectName("FlatBtn")
            self._refresh_cache_label(cache_size(self.cache_dir))
            self.btn_clear_cache.clicked.connect(self._clear_cache)
            lay.addWidget(self.btn_clear_cache)

        # Export/import: moving data between
        # versions/folders is user-driven — one click out, one click in.
        # The export is a *single file* with the dedicated .mcbak extension
        # rather than a folder — easier to move and keep safe.
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
            # two modes — non-destructive merge (keep current) or replace (snapshot)
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

        # "Auto backup" — a switch + a path of choice.
        # The file is rewritten only when new data has been added and its
        # thumbnails finished ("only touch the file when something changed")
        # — cache-free, light, atomic, in the background.
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
        # A kind selector — simple (data only, light) or full (with
        # the thumbnail cache).
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

        # Factory reset — all app-side data + the legacy global leftover
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
        # License and source URL — the license slot used to be empty in the
        # UI; if REPO_URL/SOURCE_URL pointed at a code-less repo it would
        # leak right here.
        lic = QLabel(T("GPL-3.0-or-later · سورس: {0}").format(SOURCE_URL))
        lic.setObjectName("MutedLabel")
        lic.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(lic)
        # GPLv3 §5d: an interactive interface must show the copyright notice,
        # the no-warranty statement and where to find the license text:
        legal = QLabel(T("© 2026 Mohammad Jafari · بدون ضمانت · متن کامل مجوز در فایل LICENSE کنار برنامه"))
        legal.setObjectName("MutedLabel")
        legal.setAlignment(Qt.AlignmentFlag.AlignLeft)
        legal.setWordWrap(True)
        lay.addWidget(legal)
        self.tabs.addTab(tab, T("⚙ عمومی"))

    # ------------------------------------------------------------ Handlers
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
            # If this category has no track profile yet, offer one right
            # here (video -> watched, book/manga -> read, or no tracking/
            # custom)
            if self.store.cat_track_profile_key(cat) is None:
                key = pick_track_profile(self.store, cat, self)
                if key:
                    self.store.set_cat_track_profile(cat, key)
                    self._refresh_cats()   # keep this row's profile box in sync

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

    # ------------------------------------------------------------ Blocking progress-bar helper
    def _run_backup_op(self, title: str, intro: str, fn):
        """Runs fn(progress_cb) on a background thread behind a modal
        progress bar.

        "add a progress bar showing how far along it is;
        while it runs the app should lock with a 'please wait until
        finished' message and an ETA". The window is ApplicationModal (the
        whole app locks), has no cancel/close button, and continuously shows
        percent + size + the estimated remaining time from the real rate.
        Returns ("ok", result) or ("err", exception)."""
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

        class _LockedProgress(QProgressDialog):
            """A progress bar that really cannot be closed.

            The old dialog had no cancel *button* but Esc still triggered
            QProgressDialog.cancel() — the modal closed while the background
            thread was still mutating the live store (a lost-update race).
            Esc is swallowed; the only closer is the worker's own done()."""

            def keyPressEvent(self, ev):
                if ev.key() == Qt.Key.Key_Escape:
                    ev.accept()
                    return
                super().keyPressEvent(ev)

        dlg = _LockedProgress(intro, "", 0, 1000, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)   # lock the whole app
        dlg.setCancelButton(None)                                    # no close/cancel
        dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
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
            except Exception as e:                       # noqa: BLE001 - surfaced to the user
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
        dlg.exec()                                        # modal loop until the work is done
        dlg.close()
        dlg.deleteLater()
        if state["error"] is not None:
            return "err", state["error"]
        return "ok", state["result"]

    # ------------------------------------------------------------ Export/import
    def _refresh_ab_path_label(self):
        if self._ab_path:
            self.ab_path_label.setText(T("فایلِ مقصد: {0}").format(self._ab_path))
        else:
            # An empty path is valid — it resolves to backups/
            # beside the database.
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
        """Packs all app-side data into one .mcbak backup file
        ("a single file with a dedicated extension";
        progress bar + lock)."""
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
        """Import-mode chooser dialog -> "merge" | "replace" | None.

        Split into its own method so tests can read/mock the choice without
        running the real modal loop (box.exec)."""
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
        """Restore ("keep my current files after
        importing a backup").

        The user picks the mode:
          🔀 merge — nothing currently stored is wiped; backup entries are
             *added* (duplicates defer to the current version) + a report on
             which paths exist (detects a backup made on another machine —
             "since none of them existed on that system nothing
             was shown but my folders were wiped").
          ♻️ full replace — the previous behavior (stage + rewrite on the
             next boot), but an automatic safety snapshot of the current
             state is written to the backups folder first, so going back
             stays possible.
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

        # ---- Pick the mode — merge (safe/default) or replace (destructive + snapshot)
        _mode47 = self._ask_import_mode()

        from scanner import human_size, fa
        _f0, _b0 = _bk.estimate_backup_size(Path(dest))

        if _mode47 == "merge":
            intro = (T("لطفاً صبر کنید تا ادغام اطلاعات تمام شود.\nبرنامه تا پایانِ کار قفل است — برنامه را نبند.\nحجمِ پشتیبان: {0} فایل · {1} — زمانِ دقیق هنگام کار اعلام می‌شود").format(fa(_f0), human_size(_b0)))
            _roots48 = [str(x.get("path") or "") for x in self.store.roots()]   # remap table
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

        # ---- ♻️ full replace — first of all, an automatic safety snapshot (no cache)
        _fs47, _bs47 = _bk.estimate_export_size(False)
        _tot47 = max(_bs47 + _b0, 1)

        def _op47(cb, _dst=dest, _bs=_bs47, _b=max(_b0, 1), _t=_tot47):  # noqa: B008 - deliberate snapshot at definition time
            def _cb1(d, t, fd, ft):                 # phase 1: snapshot -> [0, _bs]
                cb(min(int(d or 0), _bs), _t, fd, ft)
            _snap = _bk.export_backup(_bk.auto_snapshot_path(),
                                      include_cache=False, progress=_cb1)

            def _cb2(d, t, fd, ft):                 # phase 2: staging -> [_bs, _t]
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
        # The old window used to stay open and a second instance
        # appeared — relaunching from inside this modal dialog (nested
        # event loops) stays incomplete, so like the factory reset only the
        # request is recorded; the main window closes and reopens through
        # its own clean closeEvent path (+ a watchdog for a definitive
        # exit).
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
        # With the cache gone, the thumbnail manifests are invalidated too,
        # so the next run's prebuild re-checks/rebuilds every title once and
        # no missing thumbnail hides behind the "skip smartly" logic.
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

    # ------------------------------------------------------------ Factory reset
    def _factory_reset(self):
        """The "factory reset" button: reset all data to day
        one.

        The open database and config are locked on Windows and cannot be
        deleted in this session; so only the reset flag (the list of targets)
        is written beside the app, the app relaunches, and the actual
        deletion happens on the next boot — before any database file is
        opened (paths.factory_reset_if_pending)."""
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
            # Also wipe demo/sample files left by older versions.
            # Note: config.example.json is deliberately not on this list —
            # it is the documented sample shipped with the app (README) and
            # must survive a reset.
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
            # Wipe the backups folder too
            try:
                bk_dir = _appd() / "backups"
                if bk_dir.is_dir():
                    s = str(bk_dir.resolve())
                    if s not in targets:
                        targets.append(s)
            except Exception as _exc_dbg:
                dbg_log(f"_factory_reset failed: {type(_exc_dbg).__name__}")
                pass
            # Debug logs
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
            # Collect the old global-folder leftover too —
            # "nothing left in a global path"
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
            # the relaunch used to run from inside this modal dialog
            # (_relaunch_app + app.quit) — the fresh process started before the
            # old one released the data.db lock and the wipe failed. This is
            # exactly the bug _import_backup already fixed with
            # request_restart(): the relaunch happens in closeEvent, after the
            # threads stop and the databases close.
            win = self.window()
            req = getattr(win, "request_restart", None)
            if callable(req):
                self.accept()
                req()
                return
            import sys as _sys2
            if getattr(_sys2, "frozen", False):
                ok = _relaunch_app([_sys2.executable])
            else:
                ok = _relaunch_app([_sys2.executable, str(_appd() / "main.py")])
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
        # the switch-on case was covered by "checked and not
        # _orig_durations", but when durations were ALREADY on, accept() saw no
        # change and no rescan ever ran — the wipe looked like a no-op. The
        # explicit flag makes accept() schedule the rescan.
        self._durations_recalc = True
        QMessageBox.information(self, T("انجام شد ✓"),
                                T("کش زمان‌ها پاک شد. با «ذخیره و اسکن مجدد» همه از نو محاسبه می‌شود."))

    @staticmethod
    def _roots_signature(roots) -> set:
        """Fingerprint of the roots list (order-insensitive) — decides if a rescan is needed."""
        def norm(p):
            return str(p).strip().replace("/", "\\").rstrip("\\").lower()
        return {(norm(r.get("path", "")), str(r.get("category", "")).strip())
                for r in roots if r.get("path")}

    def _update_player_status(self, *_):
        """Live display: which real player the current choice resolves to."""
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
        # Rescan only when needed: the paths/their categories changed, the
        # fallback changed, or duration computation was switched on. Player/
        # theme/general changes -> no rescan.
        self.paths_changed = (
            self._roots_signature(roots) != self._orig_roots_sig
            or self.chk_fallback.isChecked() != self._orig_fallback
            or (self.chk_durations.isChecked() and not self._orig_durations)
            or getattr(self, "_durations_recalc", False)   # the recalc-durations button requests a rescan
)
        # Hitting save used to freeze the app for seconds — all
        # setters run under one batched-save umbrella; 8+ sequential atomic
        # disk writes (especially with Windows real-time antivirus) used to
        # freeze the UI for seconds — now a single save_config + save_data
        # runs at the end.
        with self.store.defer_save():
            self.store.set_roots(roots, self.chk_fallback.isChecked())
            self.store.set_compute_durations(self.chk_durations.isChecked())
            self.store.set_archive_thumbs(self.chk_archive.isChecked())
            self.store.set_folder_thumbs_only(self.chk_folder_only.isChecked())
            self.store.set_prebuild_thumbs(self.chk_prebuild.isChecked())
            # Track profile per category — each row's box (video/book/none/custom)
            for i in range(self.cat_list.count()):
                li = self.cat_list.item(i)
                combo = self._cat_row_combo(self.cat_list, li)
                if combo is not None:
                    self.store.set_cat_track_profile(
                        li.data(Qt.ItemDataRole.UserRole), combo.currentData())
            if self.cache_dir is not None:
                self.store.set_cache_max_mb(self.cache_combo.currentData() or 0)
                # The cache always lives beside the app — the cache location setting is gone.
                self.store.save_config()
            self.store.set_player_cfg(self.player_combo.currentData(),
                                      self.player_edit.text().strip())
            self.store.set_theme(self.theme_combo.currentData())
            # Language + performance mode — the language is applied later in
            # _after_settings_save with a relaunch prompt; performance mode
            # immediately.
            self.store.set_language(self.lang_combo.currentData())
            self.store.set_perf_mode(self.perf_combo.currentData())
            # Auto backup — switch + chosen path
            self.store.set_auto_backup(
                self.chk_auto_backup.isChecked(), self._ab_path,
                include_cache=self.chk_ab_cache.isChecked())
        super().accept()


# ---------------------------------------------------------------- Title editor

class ItemEditDialog(QDialog):
    """Edit a title's display name, category, description, poster and favorite flag."""

    # A cover change from inside this dialog hits the Store immediately; the
    # UI owner (details/main) must be notified at that very moment —
    # otherwise after Cancel the stale page/card keeps the old cover (new
    # meta, old UI).
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
        # Custom cover: no longer a free-form path — shows the current
        # cover's name + pick/remove through the shared cover_actions module
        # (normalized into .covers)
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
        # Orange warning when the stored cover file cannot be found
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
        """Pick an image -> normalize into .covers -> save the meta immediately."""
        from ui.cover_actions import pick_cover
        pick_cover(self, self.store, self.item, self._cover_changed)

    def _clear_cover(self):
        """Remove the custom cover and fall back to the folder icon."""
        from ui.cover_actions import clear_cover
        clear_cover(self, self.store, self.item, self._cover_changed)

    def _cover_changed(self, changed: bool):
        """Re-sync the cover row and warning after the operation (called on the UI thread)."""
        if not changed:
            return
        stc = self.store.cover_status(self.item.path)
        self._cover_missing = stc["missing"]
        self.lbl_cover.setText(stc["name"] if stc["kind"] != "none" else "")
        self.lbl_cover_warn.setVisible(self._cover_missing)
        # Independent of Save/Cancel — the UI owner refreshes immediately
        try:
            self.coverTouched.emit(self.item)
        except RuntimeError:
            pass

    def accept(self):
        # poster is no longer saved from this form — the custom cover is
        # applied instantly via the "Pick.../Remove" buttons and the old
        # value is left untouched
        self.store.set_meta(
            self.item.path,
            title=self.ed_title.text().strip() or None,
            category=self.cb_cat.value().strip() or None,
            description=self.ed_desc.toPlainText().strip() or None,
            favorite=self.chk_fav.isChecked() or None,
)
        super().accept()


# ---------------------------------------------------------------- Missing items
class MissingItemsDialog(QDialog):
    """Assisted management of missing items.

    For titles recorded under the current roots whose files are gone, the
    user gets two explicit choices: "🗑 delete" or "📂 new path". Moving
    keeps all related info (watch state, category, description, rating) —
    watch keys are relative, so they survive."""

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
            "تازه منتقل شود.").format(fa(len(missing))))
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
        self._row_widgets = {}               # path -> row widget
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
        btn_all = QPushButton(T("🗑 حذف همه ({0})").format(fa(len(missing))))
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
        self.count_lbl.setText(T("مانده: {0} مورد").format(fa(len(self._remaining))))
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
        # a loose single FILE title can only be re-pointed to the
        # file itself — the old directory picker made "new path" impossible
        # for loose files (a folder pick could never be the file's new key).
        was_file = bool(Path(p).suffix) and not str(p).endswith(("/", "\\"))
        dest = ""
        if was_file:
            dest, _sel = QFileDialog.getOpenFileName(
                self,
                T("فایلِ تازه‌ی «{0}» کجاست؟").format(self._name_of(p)),
                str(Path(p).parent) if Path(p).parent.exists() else "")
        else:
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
