# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""ویجت‌های مشترک: چیدمان شناور، کارت، پوستر گردشده، ردیف فایل."""
from __future__ import annotations

from i18n import T

import os
import sys
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal, QUrl
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QImage, QLinearGradient, QPainter,
    QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLayout, QPushButton, QSizePolicy,
    QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from scanner import file_icon, human_size, natural_key
from watchtime import dbg_log


def open_path(path):
    """باز کردن فایل/پوشه با برنامه‌ی پیش‌فرض سیستم."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ------------------------------------------------------------------ انتخابگر پوشه‌ی داخلی
# دیالوگ Qt/نیتیو ویندوز با چند درایو کُند باز می‌شد و لحظه‌ای به‌صورت پنجره‌ی
# سفیدِ بی‌عنوان (با نام برنامه روی تایتل‌بار) دیده می‌شد. این انتخابگر کاملاً
# داخل برنامه است: با تم خودمان، باز شدن فوری (لود تنبل — درایوهای شبکه هم
# فقط موقع بازکردن‌شان خوانده می‌شوند).

def _list_drives() -> list[str]:
    """درایوهای موجود بدون خواندنشان (تا درایو شبکه/کند هنگی ایجاد نکند)."""
    if sys.platform == "win32":
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            out = [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]
            if out:
                return out
        except Exception as _exc_dbg:
            dbg_log(f"_list_drives failed: {type(_exc_dbg).__name__}")
            pass
        out = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
        if out:
            return out
    return ["/"]


def _subdirs(path: str) -> list[str]:
    """نام زیرپوشه‌ها (نامرئی‌ها و سیستمی کنار گذاشته می‌شوند)."""
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                name = e.name
                if not name or name.startswith("$"):
                    continue
                if sys.platform == "win32":
                    try:
                        if e.stat(follow_symlinks=False).st_file_attributes & 0x2:
                            continue      # FILE_ATTRIBUTE_HIDDEN
                    except (OSError, AttributeError):
                        pass
                elif name.startswith("."):
                    continue
                out.append(name)
    except OSError:
        return []
    out.sort(key=natural_key)
    return out


class _DirItem(QTreeWidgetItem):
    def __init__(self, path: str, label: str):
        super().__init__([label])
        self.dir_path = path
        self.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
        # فرزند ساختگی → قابلیت بازشدن تنبل؛ در اولین expand جایگزین می‌شود
        QTreeWidgetItem(self, ["…"])


class DirPickerDialog(QDialog):
    """انتخاب پوشه با درخت تنبل — سریع، استایل‌دار، و کاملاً درونِ برنامه."""

    def __init__(self, parent=None, title=None, start: str = ""):
        super().__init__(parent)
        # T() در آرگومانِ پیش‌فرض ممنوع: هنگامِ import منجمد می‌شود و کاربرِ
        # فارسی عنوانِ انگلیسی می‌بیند (زبانِ واقعی بعد از خواندنِ config ست
        # می‌شود) — ارزیابی در لحظه‌ی ساخت، نه در لحظه‌ی import.
        self.setWindowTitle(T("انتخاب پوشه") if title is None else title)
        from i18n import is_rtl as _rtl45     # جهت چیدمان بر اساس زبان
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if _rtl45()
            else Qt.LayoutDirection.LeftToRight)
        self.resize(620, 540)
        self.setMinimumSize(520, 440)
        self._sel = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(18)
        self.tree.itemExpanded.connect(self._populate)
        self.tree.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.tree, 1)

        self.path_lbl = QLabel(T("پوشه‌ای انتخاب کن…"))
        self.path_lbl.setObjectName("MutedLabel")
        self.path_lbl.setWordWrap(True)
        lay.addWidget(self.path_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.btn_new = QPushButton(T("➕ پوشه‌ی جدید"))
        self.btn_new.setObjectName("FlatBtn")
        self.btn_new.clicked.connect(self._new_folder)
        btns.addButton(self.btn_new, QDialogButtonBox.ButtonRole.ActionRole)
        ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(T("انتخاب همین پوشه ✓"))
        ok.setDefault(True)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(T("انصراف"))
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._ok = ok
        self._ok.setEnabled(False)

        style = QApplication.style()
        self._dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        for drive in _list_drives():
            it = _DirItem(drive, "💽 " + drive.rstrip("\\/") or drive)
            it.setIcon(0, self._dir_icon)
            self.tree.addTopLevelItem(it)

        # باز کردن مسیر شروع (اگر معتبر باشد)
        if start and Path(start).is_dir():
            self._expand_to(str(Path(start)))

    # ------------------------------------------------------------ درخت تنبل
    def _populate(self, item: _DirItem):
        """پر کردن فرزندان واقعی به‌جای جایگذار — فقط در اولین بازکردن."""
        if not isinstance(item, _DirItem):
            return
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChild(0)
            for name in _subdirs(item.dir_path):
                sub = _DirItem(str(Path(item.dir_path) / name), "📁 " + name)
                sub.setIcon(0, self._dir_icon)
                item.addChild(sub)

    def _expand_to(self, start: str):
        try:
            p = Path(start).resolve()
            parts = [x for x in p.parts if x != p.anchor]
        except OSError:
            return
        if not p.anchor:
            return
        node = None
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if Path(top.dir_path) == Path(p.anchor):
                node = top
                break
        if node is None:
            return
        cur = Path(p.anchor)
        for part in parts:
            cur = cur / part
            self._populate(node)
            node.setExpanded(True)
            nxt = None
            for j in range(node.childCount()):
                ch = node.child(j)
                if Path(ch.dir_path) == cur:
                    nxt = ch
                    break
            if nxt is None:
                return
            node = nxt
        self._populate(node)
        node.setExpanded(True)
        self.tree.setCurrentItem(node)
        self.tree.scrollToItem(node)

    # ------------------------------------------------------------ انتخاب
    def _on_select(self):
        items = self.tree.selectedItems()
        if items and isinstance(items[0], _DirItem):
            self._sel = items[0].dir_path
            self.path_lbl.setText("📂 " + self._sel)
            self._ok.setEnabled(True)
        else:
            self._sel = ""
            self._ok.setEnabled(False)

    def selected_path(self) -> str:
        return self._sel

    def _accept(self):
        if self._sel:
            self.accept()

    def _new_folder(self):
        items = self.tree.selectedItems()
        if not items or not isinstance(items[0], _DirItem):
            self.path_lbl.setText(T("اول پوشه‌ای را برای ساخت زیرپوشه انتخاب کن."))
            return
        parent_item = items[0]
        name, ok = QInputDialog.getText(self, T("پوشه‌ی جدید"), T("نام پوشه:"))
        name = name.strip()
        if not ok or not name:
            return
        try:
            newp = Path(parent_item.dir_path) / name
            newp.mkdir(parents=False, exist_ok=False)
        except OSError as e:
            self.path_lbl.setText(T("⚠ ساخت پوشه ممکن نشد: {0}").format(e))
            return
        self._populate(parent_item)
        parent_item.setExpanded(True)
        # اگر از قبل populate شده بود، دستی اضافه می‌کنیم (ترتیب حفظ شود)
        if parent_item.childCount() and all(
                not (isinstance(c, _DirItem) and Path(c.dir_path) == newp)
                for c in (parent_item.child(i) for i in range(parent_item.childCount()))):
            sub = _DirItem(str(newp), "📁 " + name)
            sub.setIcon(0, self._dir_icon)
            parent_item.addChild(sub)
            parent_item.sortChildren(0, Qt.SortOrder.AscendingOrder)
        for j in range(parent_item.childCount()):
            ch = parent_item.child(j)
            if isinstance(ch, _DirItem) and Path(ch.dir_path) == newp:
                self.tree.setCurrentItem(ch)
                break


# ------------------------------------------------------------------ دیالوگ‌های فایل Qt
QT_DLG = QFileDialog.Option.DontUseNativeDialog


def pick_directory(parent, title, start=""):
    """انتخاب پوشه.

    روی **ویندوز** از دیالوگ بومی خود اکسپلورر استفاده می‌کنیم (IFileOpenDialog) —
    درخواست کاربر: آشنا، سریع، با دسترسی به Quick Access و Network. انتخابگر
    داخلی‌مان (DirPickerDialog) برای پلتفرم‌های دیگر باقی می‌ماند.
    """
    if sys.platform == "win32":
        return QFileDialog.getExistingDirectory(parent, title, start) or ""
    dlg = DirPickerDialog(parent, title=title, start=start)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.selected_path()
    return ""


def pick_open_file(parent, title, start="", flt=None):
    """انتخاب فایل. روی ویندوز بومیِ اکسپلورر (درخواست کاربر)، روی بقیه Qt غیربومی.

    flt=None یعنی «همه فایل‌ها» در لحظه‌ی فراخوان ترجمه می‌شود — مقدارِ
    پیش‌فرضِ ترجمه‌شده هنگامِ import منجمد می‌شد (باگِ دوزبانه‌ی D4).
    """
    flt = T("همه فایل‌ها (*)") if flt is None else flt
    if sys.platform == "win32":
        return QFileDialog.getOpenFileName(parent, title, start, flt)
    return QFileDialog.getOpenFileName(parent, title, start, flt, options=QT_DLG)


# ------------------------------------------------------------------ تصاویر

def rounded_pixmap(pix: QPixmap, w: int, h: int, radius: int = 12) -> QPixmap:
    """کراپ وسط + گوشه‌های گرد."""
    scaled = pix.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(path)
    p.drawPixmap(-x, -y, scaled)
    p.end()
    return out


def poster_needs_letterbox(sw: int, sh: int, w: int, h: int) -> bool:
    """نسبتِ تصویر با قاب ناسازگار است؟ (0.31.0)

    تصاویر نزدیک به نسبتِ قاب (±۱۴٪) با کراپِ پوششیِ سنتی بهتر دیده می‌شوند؛
    فقط نسبت‌های متفاوت — مثل آیکونِ مربعیِ ویندوز در قابِ پرتره‌ی ۳:۴ —
    letterbox می‌خواهند (گزارش کاربر: «آیکون‌های مربعی کامل نشون داده نمی‌شن»).
    """
    if sw <= 0 or sh <= 0 or w <= 0 or h <= 0:
        return False
    r = (sw / sh) / (w / h)
    return not (0.88 <= r <= 1.14)


# رنگِ پس‌زمینه‌ی پوسترهای letterbox — با تمِ فعال همگام است (0.32.0؛ پیش‌فرض «تاریک آبی»)
_POSTER_BG = QColor(12, 17, 26)


def set_poster_bg(color) -> None:
    """رنگِ پس‌زمینه‌ی پوسترها را با تمِ برنامه یکی می‌کند (0.32.0).

    apply_theme/راه‌اندازی صدا می‌زند؛ کارگرهای پوستر در نخ‌های خودشان فقط می‌خوانند.
    """
    global _POSTER_BG
    try:
        c = color if isinstance(color, QColor) else QColor(str(color))
        if c.isValid():
            _POSTER_BG = c
    except Exception as _exc_dbg:
        dbg_log(f"set_poster_bg failed: {type(_exc_dbg).__name__}")
        pass


def poster_bg_color() -> QColor:
    """نسخه‌ی کپی از رنگِ جاری زمینه‌ی پوستر (امن برای نخ‌های کارگر)."""
    return QColor(_POSTER_BG)


def poster_letterbox_image(img: "QImage", w: int, h: int, radius: int = 12,
                           bg: str | None = None) -> "QImage":
    """جا دادنِ *کاملِ* تصویر در قاب روی زمینه‌ی یکدست به رنگِ تمِ برنامه (0.32.0,
    بازخوردِ کاربر: «پس‌زمینه همون رنگ تم باشه نه بلور تصویر — ظاهر تمیزتر»).

    کلِ تصویر با حفظِ نسبت در قاب می‌نشیند و فضای خالی با رنگِ پس‌زمینه‌ی خودِ تم
    (dark/dim/amoled/light — همان @bg@ صفحه) پر می‌شود؛ بخش‌های شفافِ آیکون‌ها
    هم روی همان رنگِ یکدست می‌نشینند. روی QImage کار می‌کند تا در نخِ کارگرِ
    پوستر (خارج از نخ UI) مجاز باشد.

    bg (round 13 §۴): None → رنگِ تم؛ "none" → بدونِ هیچ زمینه‌ای — فقط خودِ
    تصویر شناور روی صفحه (خواسته‌ی کاربر برای صفحه‌ی جزئیات؛ بیرونِ گوشه‌ها
    همچنان شفاف می‌ماند). شبکه‌ی کارت‌ها همان letterboxِ تم‌رنگ را نگه می‌دارد
    (پیش‌فرض bg=None) تا کارت‌ها ناهم‌اندازه و ریش‌ریش نشوند.
    """
    fg = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(path)
    if bg != "none":
        p.fillRect(0, 0, w, h, poster_bg_color())       # زمینه‌ی یکدستِ هم‌رنگ تم
    p.drawImage((w - fg.width()) // 2, (h - fg.height()) // 2, fg)
    p.end()
    return out


def poster_pixmap(pix: QPixmap, w: int, h: int, radius: int = 12,
                  bg: str | None = None) -> QPixmap:
    """پوسترِ تطبیقی (0.31.0): نسبتِ نزدیک → کراپِ پوششی؛ ناسازگار → letterboxِ بلار.

    جایگزینِ drop-in برای rounded_pixmap در تمام جاهایی که «پوسترِ عنوان» ساخته
    می‌شود — آیکون‌های مربعی دیگر کراپ نمی‌شوند و پوسترهای عادی هم همان رفتارِ
    قبلی را دارند. bg فقط پاس داده می‌شود (round 13 §۴) — None برای همه‌ی
    تماس‌های موجود یعنی همان letterboxِ هم‌رنگِ تم.
    """
    if pix.isNull() or w < 2 or h < 2 \
            or not poster_needs_letterbox(pix.width(), pix.height(), w, h):
        return rounded_pixmap(pix, w, h, radius)
    return QPixmap.fromImage(
        poster_letterbox_image(pix.toImage(), w, h, radius, bg))


_PH_CACHE: "OrderedDict" = OrderedDict()


def placeholder_poster(letter: str, color: str, w: int, h: int, radius: int = 12) -> QPixmap:
    """پوستر جایگزین حرفی — با کش به‌اشتراک‌گذاشته.

    قبلاً برای *هر* کارتِ در حال ساختِ جریانی یک QPixmap تازه با گرادیان و متن
    نقاشی می‌شد (~۱-۲ میلی‌ثانیه) — با چندصد کارت یعنی چند صد میلی‌ثانیه در نخ UI.
    QPixmap ایمن برای به‌اشتراک‌گذاری بین QLabelهاست (implicit-sharing، فقط‌خواندنی)،
    پس یک نسخه برای همه‌ی کارت‌های هم‌حرف/هم‌رنگ کافی است.
    """
    key = (letter, color, w, h, radius)
    pm = _PH_CACHE.get(key)
    if pm is not None:
        _PH_CACHE.move_to_end(key)
        return pm
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c1, c2 = QColor(color), QColor(color).darker(230)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0, c1)
    grad.setColorAt(1, c2)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.fillPath(path, grad)
    p.setPen(QColor(color).lighter(150))
    p.drawPath(path)
    f = QFont("Vazirmatn")
    f.setFamilies(["Vazirmatn", "Segoe UI", "Tahoma"])
    f.setPointSize(max(8, int(h * 0.22)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(out.rect(), Qt.AlignmentFlag.AlignCenter, (letter or T("؟"))[:1])
    p.end()
    _PH_CACHE[key] = out
    while len(_PH_CACHE) > 64:               # سقف کش — حروف فارسی/لاتین محدودند
        try:
            _PH_CACHE.popitem(last=False)
        except KeyError:
            break
    return out


def badge_style(color: str) -> str:
    return (
        f"color:{color}; background:{color}1e; border:1px solid {color}55;"
        "border-radius:9px; padding:2px 10px; font-size:11px; font-weight:bold;"
    )


def badge_combo_style(color: str) -> str:
    """کامبوی دسته به‌شکلِ همان بج رنگی — 0.48.0 (درخواست کاربر: «با اسم دسته
    یکیش کن، خود اسم دسته باشه ولی بشه عوض کرد و رنگش هم رنگ دسته بشه»)."""
    return (
        f"QComboBox {{ color:#fff; background:{color}e6; border:1px solid {color}aa;"
        "border-radius:9px; padding:2px 10px; font-size:11px; font-weight:bold; }"
        "QComboBox::drop-down { border:none; width:18px; }"
        "QComboBox QAbstractItemView { background:#131b29; color:#e7edf6; "
        f"border:1px solid {color}66; }}"
    )


# ---------------------------------------------------------------- چیدمان شناور

class FlowLayout(QLayout):
    """چیدمان شبکه‌ای که با عرض پنجره جا‌به‌جا می‌شود (راست‌چین سازگار)."""

    def __init__(self, parent=None, spacing=14):
        super().__init__(parent)
        self._items = []
        self._reserved_h = 0   # ارتفاعِ نهاییِ رزرو‌شده در حین ساختِ تدریجیِ کارت‌ها
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()   # بدون این، افزودن دسته‌ای (گرِید جزئیات) هندسه را تازه نمی‌کرد

    def reserve_height(self, h: int):
        """ارتفاع نهاییِ (تخمینیِ) شبکه را از قبل رزرو کن تا وقتی کارت‌ها به‌صورت
        تدریجی (batch به batch) اضافه می‌شوند، ارتفاعِ محتوا ناگهان کوچک نباشد و
        بعد رشد نکند — علتِ باگِ «اسکرول در حین ساخت، بزرگ می‌شود/ریست می‌شود».
        بعد از پایانِ ساختِ کامل، با reserve_height(0) رزرو برداشته می‌شود تا
        ارتفاعِ واقعی (که معمولاً کمی کوچک‌تر است) جایگزین شود."""
        h = max(0, int(h))
        if h != self._reserved_h:
            self._reserved_h = h
            self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize(60, 60)
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        avail_w = rect.width() - m.left() - m.right()
        spacing = self.spacing()
        rtl = QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x = y = line_h = 0
        for item in self._items:
            sz = item.sizeHint()
            w, h = sz.width(), sz.height()
            if x > 0 and x + w > avail_w:
                x = 0
                y += line_h + spacing
                line_h = 0
            if not test_only:
                px = rect.x() + m.left() + (avail_w - x - w if rtl else x)
                item.setGeometry(QRect(px, rect.y() + m.top() + y, w, h))
            x += w + spacing
            line_h = max(line_h, h)
        computed = y + line_h + m.top() + m.bottom()
        return max(computed, self._reserved_h)


# ---------------------------------------------------------------------- کارت

CARD_W, CARD_H = 206, 348          # ۱۲+ برای سطرِ دومِ اطلاعات (بازبینی کاربر)
POSTER_W, POSTER_H = 186, 248


class CardWidget(QFrame):
    clicked = Signal(object)
    menuRequested = Signal(object, QPoint)
    subcatChanged = Signal(object, str)     # (item, دسته‌ی جدید) — سوییچ سریع زیردسته

    def __init__(self, item, view: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.view = view
        self.setObjectName("Card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        sub = view.get("subcat")
        self.setFixedSize(CARD_W, CARD_H + (26 if sub else 0))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 9)
        lay.setSpacing(6)

        self.poster_lbl = QLabel()
        self.poster_lbl.setFixedSize(POSTER_W, POSTER_H)
        self.poster_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_lbl.setPixmap(view["poster"])
        lay.addWidget(self.poster_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

        self.title_lbl = QLabel(view["title"])
        self.title_lbl.setObjectName("CardTitle")
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.title_lbl.setFixedHeight(38)
        self.title_lbl.setToolTip(view["title"])
        lay.addWidget(self.title_lbl)

        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        self.info_lbl = QLabel(view["info_short"])
        self.info_lbl.setObjectName("MutedLabel")
        # بازبینی کاربر: «درست کن که در چند خط باشن که کامل دیده بشن» —
        # خطِ اطلاعات تا دو سطر می‌شکند؛ ستاره/زمان/حجم/شمارِ دیده‌شده دیگر
        # روی کارت‌های پُرجزئیات بریده نمی‌شود.
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setMaximumHeight(30)
        self.info_lbl.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.info_lbl.setToolTip(view["info_short"])
        row.addStretch(1)
        row.addWidget(self.info_lbl)
        row.addStretch(1)
        lay.addLayout(row)

        # سوییچ سریع بین زیردسته‌های هم‌رده (چک‌باکس/دراپ‌داون کوچک)
        self.subcat_combo = None
        if sub:
            self.subcat_combo, sub_row = self._build_subcat_row(sub)
            lay.addLayout(sub_row)

        # بج دسته روی پوستر (مثل کارت‌های نتفلیکس)
        self.chip = QLabel(view["category"].split("/")[-1], self)
        self.chip.setStyleSheet(
            f"color:#fff; background:{view['color']}e6; border-radius:9px;"
            "padding:2px 10px; font-size:11px; font-weight:bold;"
            "border:1px solid rgba(255,255,255,.28);"
        )
        self.chip.adjustSize()
        chip_x = max(10 + POSTER_W - self.chip.width() - 8, 14)
        self.chip.move(chip_x, 16)

        # ستاره‌ی علاقه‌مندی — همیشه ساخته می‌شود تا لازم نباشد برای ⭐ کارت بازسازی شود
        self.star = QLabel("★", self)
        self.star.setStyleSheet(
            "color:#fbbf24; font-size:16px; background:#0c111acc;"
            "border-radius:10px; padding:1px 5px;"
        )
        self.star.adjustSize()
        self.star.move(14, 14)
        self.star.setVisible(bool(view.get("fav")))

        self._apply_tooltip()

    # ------------------------------------------------------------ به‌روزرسانی درجا
    def set_poster(self, pm: QPixmap):
        """تعویض پوستر (وقتی بارگذار ناهمزمان تصویر واقعی را آورد)."""
        self.poster_lbl.setPixmap(pm)

    def apply_view(self, view: dict):
        """به‌روزرسانی همان کارت بدون بازسازی (⭐/دسته/اطلاعات) — بدون پرش اسکرول."""
        self.view = view
        self.title_lbl.setText(view["title"])
        self.title_lbl.setToolTip(view["title"])
        self.info_lbl.setText(view["info_short"])
        self.chip.setText(view["category"].split("/")[-1])
        self.chip.setStyleSheet(
            f"color:#fff; background:{view['color']}e6; border-radius:9px;"
            "padding:2px 10px; font-size:11px; font-weight:bold;"
            "border:1px solid rgba(255,255,255,.28);"
        )
        self.chip.adjustSize()
        self.chip.move(max(10 + POSTER_W - self.chip.width() - 8, 14), 16)
        self.star.setVisible(bool(view.get("fav")))
        sub = view.get("subcat")
        if sub and self.subcat_combo is not None:
            self.subcat_combo.blockSignals(True)
            idx = self.subcat_combo.findData(sub["current"])
            self.subcat_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.subcat_combo.blockSignals(False)
        self._apply_tooltip()

    def _apply_tooltip(self):
        view, item = self.view, self.item
        from i18n import disp_cat as _dcat45
        tip = T("{0}\nدسته: {1}\n{2}\nمسیر: {3}").format(view['title'], _dcat45(view['category']), item.info_text, item.path)
        if view["desc"]:
            tip += f"\n\n{view['desc']}"
        self.setToolTip(tip)

    def _build_subcat_row(self, sub: dict):
        """ردیف پایین کارت: باکس کوچک با دسته‌ی اصلی + همه‌ی زیردسته‌ها (تغییر سریع)."""
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(4)
        row.addStretch(1)
        combo = QComboBox()
        combo.setObjectName("CardSwitch")
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setToolTip(sub.get("tip", T("تغییر سریع دسته")))
        combo.setFixedWidth(168)
        labels = sub.get("labels") or {}
        for full in sub["options"]:
            combo.addItem(labels.get(full, full), full)
        idx = combo.findData(sub["current"])
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.activated.connect(
            lambda i: self.subcatChanged.emit(self.item, combo.itemData(i)))
        row.addWidget(combo)
        row.addStretch(1)
        return combo, row

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item)
        elif event.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit(self.item, event.globalPosition().toPoint())
        super().mousePressEvent(event)


# ------------------------------------------------------------------ ردیف فایل

class FileRow(QFrame):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setObjectName("FileRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)

        icon = QLabel(file_icon(self.path))
        icon.setFixedWidth(20)
        icon.setStyleSheet("font-size:14px;")

        name = QLabel(self.path.name)
        name.setObjectName("FileName")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(str(self.path))

        size = QLabel(human_size(self._size()))
        size.setObjectName("MutedLabel")
        size.setFixedWidth(90)

        btn = QPushButton(T("▶ باز کردن"))
        btn.setObjectName("MiniBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: open_path(self.path))

        lay.addWidget(icon)
        lay.addWidget(name, 1)
        lay.addWidget(size)
        lay.addWidget(btn)

    def _size(self) -> int:
        try:
            return self.path.lstat().st_size
        except OSError:
            return 0

    def mouseDoubleClickEvent(self, event):
        open_path(self.path)
        super().mouseDoubleClickEvent(event)


def clear_layout(layout):
    """خالی‌کردن چیدمان — با ترتیب امن تا هیچ پنجره‌ی «روح» سفیدی ساخته نشود.

    نکته‌ی حیاتی Qt: اگر ویجتِ *نمایان* را setParent(None) کنی، آن ویجت تبدیل به
    یک *پنجره‌ی مستقلِ قابل‌مشاهده* می‌شود و برای لحظه‌ای (تا deleteLater) به‌صورت
    پنجره‌ی سفید با نام برنامه روی صفحه می‌ایستد. با چندصد کارت، همین می‌شود
    «صدها پنجره‌ی سفید که دونه‌دونه باز و بسته می‌شوند»!
    راه‌حل: اول پنهان، بعد جدایی از والد، بعد حذف.
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()                 # ← اول پنهان: هرگز پنجره‌ی مستقل نمایان نمی‌شود
            w.setParent(None)
            w.deleteLater()
