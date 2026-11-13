# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""صفحه‌ی جزئیات یک عنوان: هدر Hero بلارشده، پوستر بزرگ، مشخصات، توضیحات
قابل ویرایش، ردیابی تماشا (✓ دیده‌شده / ◐ نیمه‌تمام با رزومه)، و فایل‌ها
در دو حالت: درختِ تودرتوی قابل‌بازوبسته یا بندانگشتی بزرگ."""
from __future__ import annotations

from i18n import T

import os


def _widget_dead(w) -> bool:
    """آیا ویجت Qt قبلاً حذف شده؟ (بدون sip — دسترسی به شیءِ مرده RuntimeError می‌دهد).

    فقط برای ویجت‌های واقعی (ThumbCard). RuntimeError یعنی C++ object رفته؛
    AttributeError (شیء غیرویجت، مثل QTreeWidgetItem) یعنی مفهومی نیست → زنده."""
    try:
        w.isVisible()
        return False
    except RuntimeError:
        return True
    except AttributeError:
        return False


def _dir_fingerprint(path: Path) -> tuple:
    """اثر انگشتِ سبکِ پوشه برای کش ساختار فایل‌ها (نسخه‌ی 0.24.0).

    فقط نام‌ها و mtime پوشه‌ها — هیچ stat دست‌به‌فایل‌ای در کار نیست، پس برای
    پوشه‌ی ۲هزارفایلی چند میلی‌ثانیه طول می‌کشد (گزارش کاربر: «خارج شی، دوباره
    وارد شی، باز از اول صبر»). افزودن/حذف/تغییرنامِ هر فایل یا پوشه، mtimeِ
    پوشه‌ی مادرش را عوض می‌کند و کش را باطل؛ ویرایشِ محتوای درونِ فایل (که
    فهرست را نمی‌چرخاند) بی‌اثر می‌ماند — همان چیزی که می‌خواهیم.
    """
    p = Path(path)
    try:
        if p.is_file():
            st = p.lstat()
            return ("file", p.name, st.st_size, int(st.st_mtime_ns))
        if not p.exists():
            return ("missing", 0)
    except OSError:
        return ("missing", 0)
    n_files = 0
    n_dirs = 0
    mt = 0
    try:
        for root, dirs, files_ in os.walk(p):
            n_files += len(files_)
            n_dirs += 1
            try:
                m = os.lstat(root).st_mtime_ns
                if m > mt:
                    mt = m
            except OSError:
                pass
            files_.sort()
            dirs.sort()
    except OSError:
        return ("dir", p.name, -1)
    return ("dir", p.name, n_files, n_dirs, int(mt))
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QPoint, QRunnable, QThreadPool, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QBrush, QMovie, QImageReader
from PySide6.QtWidgets import (
    QButtonGroup, QApplication, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QStackedWidget, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QHeaderView, QSizePolicy,
)

from scanner import (
    LOOSE_DOC_EXTS, get_file_hierarchy, get_file_tree, fa, human_size,
    is_reserved_cover, item_watchables,
    natural_key, file_type_group, TYPE_ORDER, VIDEO_EXTS,
)

# رتبه‌ی دسته‌ها برای مرتب‌سازی «نوع فایل» (0.23.0) — یک نگاشت‌سازی یک‌باره
_TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}
from store import Store
from thumbs import ThumbnailCache
from filethumbs import (
    FileThumbs, ThumbJob, ThumbSignals, group_of, load_cache_pixmap,
    poster_png_fallback,
)


class _HeroSignals(QObject):
    """نتیجه‌ی ساخت پوستر هدر + بکدراپ بلارشده در پس‌زمینه (به نخ UI)."""
    ready = Signal(str, str, str)    # کلید عنوان، مسیر png پوستر، مسیر png بکدراپ


class _HeroJob(QRunnable):
    """پوستر هدر و بکدراپ را در نخ پس‌زمینه می‌سازد.

    قبلاً همان زنجیره‌ی فال‌بک پوستر (آیکون ← ویدیو(shell/ffmpeg) ← PDF(rندر) ←
    فشرده) *همگام* روی نخ UI صدا زده می‌شد و باز کردن عناوینی با زنجیره‌ی سنگین
    (مثلاً PDF بزرگ یا ویدیوی روی درایو کند) کل برنامه را چند لحظه قفل می‌کرد.
    """

    def __init__(self, page, src, item):
        super().__init__()
        self._page = page
        self._src = src
        self._item = item

    def run(self):
        p, item = self._page, self._item
        png = hero = None
        try:
            # پوسترِ جزئیات ۲۵۲×۳۳۶ نقطه‌ی منطقی است؛ برای وضوح در اسکیل ۱۵۰–۲۰۰٪
            # حداقل ۳۸۴px کش می‌شود (max با کشِ مقیاس‌آگاه).
            px = max(getattr(p, "cache_px", 256), 384)
            png = poster_png_fallback(
                p.cache.get, p.fthumbs, self._src,
                getattr(item, "preview_video", None),
                getattr(item, "preview_pdf", None),
                getattr(item, "preview_archive", None), px)
            hero = p.fthumbs.hero_backdrop(png) if png else None
        except Exception as _exc_dbg:
            dbg_log(f"run failed: {type(_exc_dbg).__name__}")
            png = hero = None
        try:
            p._hero_signals.ready.emit(
                str(item.path), str(png or ""), str(hero or ""))
        except RuntimeError:
            pass          # بستن برنامه — منبع سیگنال حذف شده
from media_info import (
    DurationJob, DurationSignals, DurationStore, format_duration, format_duration_short,
)
from .dialogs import ItemEditDialog, NoWheelComboBox
from .widgets import (
    FlowLayout, clear_layout, open_path,
    placeholder_poster, poster_pixmap, rounded_pixmap,
)
from watchtime import dbg_log


DONE_COLOR = QColor("#4ade80")   # سبز — دیده‌شده
PART_COLOR = QColor("#fbbf24")   # کهربایی — نیمه‌تمام


def rel_of(item, f: Path) -> str:
    """مسیر نسبی یک فایل نسبت به پوشه‌ی عنوان (با / برای پایداری بین سیستم‌عامل‌ها)."""
    try:
        if Path(item.path).is_file():
            return Path(f).name
        return str(Path(f).relative_to(item.path)).replace("\\", "/")
    except (ValueError, OSError):
        return Path(f).name


class _Hero(QFrame):
    """هدر بالای صفحه با پس‌زمینه‌ی بلارشده‌ی پوستر."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        self.setMinimumHeight(158)
        self._bg: QPixmap | None = None

    def set_background(self, pm: QPixmap | None):
        self._bg = pm
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111927"))
        p.drawRoundedRect(r, 14, 14)
        if self._bg and not self._bg.isNull():
            scaled = self._bg.scaled(
                r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - r.width()) // 2
            y = (scaled.height() - r.height()) // 2
            p.setClipRect(r.adjusted(0, 0, -1, -1))
            path_rect = r.adjusted(0, 0, 0, 0)
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(path_rect, 14, 14)
            p.setClipPath(path)
            p.drawPixmap(-x, -y, scaled)
            p.fillRect(r, QColor(12, 17, 26, 175))
        p.end()
        super().paintEvent(event)


class _FlowContainer(QWidget):
    """نگه‌دارنده‌ی FlowLayout که ارتفاعش را با توجه به عرض تنظیم می‌کند
    (تا داخل اسکرول عمودی درست نمایش داده شود)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing=10)
        self.flow.setContentsMargins(4, 8, 4, 8)
        self._uniform: tuple | None = None   # (عرض کارت، ارتفاع کارت) اگر هم‌اندازه

    def set_uniform_card(self, cw: int, ch: int):
        """اعلام کارت‌های هم‌اندازه — مسیر محاسبه‌ی تحلیلیِ ارتفاع فعال می‌شود."""
        self._uniform = (cw, ch)

    def refresh_height(self, real: bool = False):
        """تازه‌سازی ارتفاع پس از افزودن کارت‌ها.

        باگ گزارش‌شده: کارت‌ها دسته‌ای (با QTimer) اضافه می‌شوند ولی resizeEvent
        فقط هنگام تغییر عرض فایر می‌شد؛ پس ارتفاع container روی مقدار خالیِ اولیه
        می‌ماند و فقط چند ردیف اول (≈۳۶ کارت) دیده می‌شد. این متد بعد از هر نوبت
        درج صدا زده می‌شود تا *همه* فایل‌ها واقعاً قابل‌مشاهده باشند.

        نسخه‌ی 0.21.0 (گزارش کاربر: پوشه‌ی +۲۰۰۰ فایل گیر می‌کرد): در حالت کارت‌های
        هم‌اندازه (گرید جزئیات — همه‌ی ThumbCardها ۱۵۲×۱۸۶ ثابت‌اند) ارتفاع با
        فرمول O(1) حساب می‌شود، نه heightForWidth که کل آیتم‌ها را پیمایش می‌کرد؛
        قبلاً هر نوبت‌ی ساخت، یک پیمایش کامل O(n) روی چیدمان می‌زد و حاصل‌ضربش
        برای پوشه‌های بزرگ ده‌ها ثانیه فریز بود. فرمول دقیقاً همان نتیجه‌ی
        FlowLayout._do_layout را برای آیتم‌های هم‌اندازه می‌دهد. با real=True یک
        پیمایش واقعیِ اضمحلالی انجام می‌شود (پایان ساخت دسته‌ای — اطمینان نهایی).
        """
        w = self.width()
        if w <= 0:
            return
        if self._uniform and not real:
            cw, ch = self._uniform
            n = self.flow.count()
            if n <= 0:
                h = 16
            else:
                avail = max(1, w - 8)                 # مارجین چپ/راست ۴+۴
                sp = 10                                # spacing چیدمان
                cols = max(1, int((avail + sp) // (cw + sp)))
                rows = (n + cols - 1) // cols
                h = rows * (ch + sp) - sp + 16         # مارجین بالا/پایین ۸+۸
            if h != self.height():
                self.setFixedHeight(h)
            return
        h = self.flow.heightForWidth(w)
        if h > 0 and h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        self.refresh_height()
        super().resizeEvent(event)


class _VSection(QWidget):
    """سکشنِ مجازیِ گرید — کارت‌ها فقط برای ردیف‌های نزدیکِ دید ساخته می‌شوند.

    نسخه‌ی 0.24.0 (گزارش کاربر: «ورود به پوشه‌ی پرفایل صبر می‌خواهد؛ رفت و برگشت
    باز از اول»): گلوگاهِ واقعی ورود، ساختِ هزاران ویجت ThumbCard بود — نه
    چیدمان و نه تامبنیل. چون کارت‌ها سایزِ ثابت دارند و چیدمان یکنواخت است،
    مختصاتِ هر فایل تحلیلیِ کامل است: سکشن ارتفاعش را همان لحظه می‌داند و
    فقط پنجره‌ی «دید ± دو صفحه» را واقعاً ویجت می‌سازد؛ با دور شدن از پنجره،
    کارت‌های آن سمت هرس و آزاد می‌شوند تا حافظه هم کران‌دار بماند. همه‌ی
    فایل‌ها همچنان قابلِ رسیدن‌اند (اسکرول آن‌ها را لحظه‌ای می‌سازد) — فقط
    هزینه‌ی ساخت به «آنچه روی صفحه است» محدود می‌شود. بازسازیِ کارتِ هرسی‌شده
    بی‌درنگ است چون تامبنیل از کش دیسکی (peek) می‌آید.
    """

    CARD_W, CARD_H, SP = 152, 186, 10   # هم‌خوان با ThumbCard سایزثابت و spacing قدیمی
    M_H, M_V = 4, 8                     # مارجین چپ/راست و بالا/پایین — عینِ FlowLayout قبلی

    def __init__(self, files, make_card, parent=None, notify=None, width_hint=0):
        super().__init__(parent)
        self.files = list(files)
        self._make_card = make_card      # فراخوانِ ساخت کارت (ThumbCard سالمِ پیوسته)
        self._notify = notify            # فراخوانِ «هندسه عوض شد — sync کن» (DetailsPage)
        self._width_hint = int(width_hint or 0)   # حدسِ اولیه‌ی عرض — تا چیدمان واقعی برسد
        self._cards: dict = {}           # index -> ThumbCard
        self._cols = 0
        self._eff_w = 0                  # عرضِ مؤثر (viewport) برای لنگرِ RTL
        self._last_w = 0                 # آخرین عرضی که کارت‌ها با آن چیده شدند
        self._win = None                 # (ردیف شروع، ردیف پایان)‌ی جاریِ واقعی
        # عرضِ سکشن نباید حداقلِ قابل‌اتکا به layout تحمیل کند — ارتفاع ثابت،
        # عرض پیگیرِ viewport (با سیاستِ Ignored حداقل‌عرضِ آن قفل نمی‌شود).
        _sp = self.sizePolicy()
        _sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.setSizePolicy(_sp)
        self._rebuild_layout()

    def resizeEvent(self, ev):
        """بازچینیِ درجای کارت‌ها با تغییر عرض — بدون هیچ اسکرول افقی.

        0.33.0: فقط وقتی «تعداد ستون‌ها» عوض می‌شد بازچینی انجام می‌گرفت.
        0.35.0 (گزارش کاربر: «برای بعضی از پوشه‌ها کارت‌ها از برنامه بیرون می‌زنن
        تا فول‌اسکرین کنم»): ریشه این بود که با ظاهر‌شدنِ *اسکرول‌بار عمودی*
        (پوشه‌های پرفایل) عرضِ نمای ~۱۲px کوچک می‌شود؛ تعداد ستون‌ها ثابت
        می‌ماند ولی xِ ستونِ آخر (که در حالت راست‌به‌چپ با self.width() حساب
        می‌شود) با عرضِ پهنِ قبلی ذخیره مانده بود و کارت‌های ستون آخر بیرونِ
        کادر می‌افتادند. حالا بازچینی با *هر* تغییرِ عرض انجام می‌شود.
        """
        old = self._cols
        self._rebuild_layout()
        w_changed = self.width() != self._last_w
        if self._cols and (self._cols != old or w_changed):
            for i, card in list(self._cards.items()):
                try:
                    card.move(*self._cell_rect(i))
                except RuntimeError:
                    pass
        self._last_w = self.width()
        super().resizeEvent(ev)

    # ---------------------------------------------------------------- هندسه‌ی تحلیلی
    def _row_count(self) -> int:
        return max(1, (len(self.files) + max(1, self._cols) - 1) // max(1, self._cols))

    def _effective_width(self) -> int:
        """عرضِ قابل‌اعتماد برای چیدمان — عرضِ واقعیِ viewport اسکرول (0.36.0).

        باگِ گزارش‌شده: «در یک پوشه‌ی خاص روی ۷ آیکون در سطر قفل شده؛ با کوچک‌کردن
        پنجره کم نمی‌شود و ۲ آیکون هر سطر بیرون می‌ماند» — علت: دکمه‌ی عنوانِ سکشن
        با متنِ بلند، حداقل‌عرضِ کلِ کانتینر را بالا نگه می‌داشت (minimumSizeHint
        دکمه ≈ متنِ آن) و اسکرول‌نما نمی‌توانست زیارتِ عملاً باریک‌تر کند؛ نتیجه:
        self.width() هر سکشن بزرگ‌ماندنِ ماست و چیدمان با عرضِ بی‌ربطِ پهن
        محاسبه می‌شد. حالا مرجعِ چیدمان و موقعیتِ افقی، عرضِ واقعیِ viewportِ
        نزدیک‌ترین QScrollArea‌ی والد است — همان چیزی که کاربر می‌بیند.
        """
        w = self.width()
        try:
            p = self.parentWidget()
            while p is not None:
                if isinstance(p, QScrollArea) and p.viewport() is not None:
                    vw = p.viewport().width()
                    if vw > 50 and (w < 50 or vw < w):
                        w = vw
                    break
                p = p.parentWidget()
        except Exception as _exc_dbg:
            dbg_log(f"_effective_width failed: {type(_exc_dbg).__name__}")
            pass
        return w

    def _rebuild_layout(self):
        """ستون‌ها بر اساس عرض فعلی — دقیقاً معادلِ فرمول FlowLayout برای کارتِ هم‌اندازه."""
        w = self._effective_width()
        if w < 120:
            w = max(120, self._width_hint)   # قبل از نشستِ چیدمان از حدس استفاده کن
        self._eff_w = w                      # مرجعِ لنگرِ RTL در _cell_rect
        avail = max(1, w - 2 * self.M_H)
        cols = max(1, int((avail + self.SP) // (self.CARD_W + self.SP)))
        if cols != self._cols:
            self._cols = cols
            self._win = None               # با عرضِ جدید، پنجره از نو محاسبه می‌شود
        rows = self._row_count()
        h = rows * (self.CARD_H + self.SP) - self.SP + 2 * self.M_V
        if h != self.height():
            self.setFixedHeight(h)

    def _cell_rect(self, idx: int):
        """مختصاتِ مطلق داخل سکشن — با پشتیبانیِ چیدمانِ راست‌به‌چپ (مثل FlowLayout)."""
        r, c = divmod(idx, max(1, self._cols))
        x_ltr = self.M_H + c * (self.CARD_W + self.SP)
        if QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            w = float(self._eff_w or self.width())   # لنگر = عرضِ واقعیِ دید
            x = w - self.M_H - self.CARD_W - c * (self.CARD_W + self.SP)
        else:
            x = x_ltr
        y = self.M_V + r * (self.CARD_H + self.SP)
        return x, y

    # ---------------------------------------------------------------- واقعی‌سازی پنجره
    def sync_window(self, y0: float, y1: float):
        """ردیف‌های متقاطع با [y0,y1] (مختصات محلیِ سکشن) را بساز؛ بیرونش را هرس کن."""
        self._rebuild_layout()
        rows = self._row_count()
        rh = self.CARD_H + self.SP
        r0 = max(0, int((y0 - self.M_V) // rh))
        r1 = min(rows - 1, int((y1 - self.M_V) // rh) + 1)
        win = (r0, r1)
        if win == self._win:
            return
        self._win = win
        i0 = r0 * self._cols
        i1 = min(len(self.files), (r1 + 1) * self._cols) if r1 >= r0 else i0
        keep = set(range(i0, i1))
        # هرس کارت‌های خارج از پنجره — آزادسازی (گزارش کاربر: حافظه و کندی پس‌مانده)
        for i in list(self._cards):
            if i not in keep:
                wdg = self._cards.pop(i)
                wdg.hide()
                wdg.deleteLater()
        # ساخت کارت‌های جدیدِ پنجره
        for i in range(i0, i1):
            if i in self._cards:
                continue
            try:
                card = self._make_card(self.files[i])
            except Exception as _exc_dbg:
                dbg_log(f"sync_window failed: {type(_exc_dbg).__name__}")
                continue                    # فایل بین اسکن و نمایش پاک شده — ردش کن
            try:
                card.setParent(self)
                x, y = self._cell_rect(i)
                card.move(x, y)
                card.show()
                self._cards[i] = card
            except RuntimeError:
                pass

    def card_count(self) -> int:
        return len(self._cards)

    # resizeEvent در بالای کلاس تعریف شده (0.33.0: بازچینی درجا به‌جای پاک‌سازی — این
    # نسخه‌ی قدیمی «پاک‌سازی کامل» حذف شد تا فلیکر/تاخیر دوباره پیش نیاید و کارت‌ها
    # هرگز از صفحه بیرون نزنند).


class ThumbCard(QFrame):

    watchMenuRequested = Signal(object, object)   # (ThumbCard, global pos)
    playRequested = Signal(object)                # دابل‌کلیک → همان مسیر ـ_play (پخش‌کننده‌ی تنظیمات)

    def __init__(self, path: Path, thumb: QPixmap, size_text: str, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.rel = ""
        self.setObjectName("ThumbCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(152, 186)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(4)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(132, 132)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setPixmap(thumb)
        lay.addWidget(self._thumb_label)

        name = QLabel(self.path.name)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        name.setFixedHeight(30)
        name.setStyleSheet("font-size:11px;")
        name.setToolTip(self.path.name)
        self._name_label = name
        lay.addWidget(name)

        self._size_text = size_text
        self._size_label = QLabel(size_text)
        self._size_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._size_label.setObjectName("MutedLabel")
        self._size_label.setStyleSheet("font-size:10.5px;")
        lay.addWidget(self._size_label)

        # بج وضعیت تماشا (✓ / ◐) — گوشه‌ی بالای کارت
        self._watch_badge = QLabel(self)
        self._watch_badge.hide()

        self.setToolTip(f"{self.path.name}\n{size_text}\n{self.path}")

    def set_thumb(self, pm: QPixmap):
        self._thumb_label.setPixmap(pm)

    def set_duration(self, text: str):
        if not text:
            return
        base = self._size_text
        self._size_label.setText(f"{base} · {text}" if base else text)

    def set_watch(self, mode: int, pos: float = 0.0):
        if mode == Store.WATCH_DONE:
            self._watch_badge.setText("✓")
            self._watch_badge.setStyleSheet(
                "background:#16a34a; color:#fff; border-radius:9px;"
                "padding:1px 6px; font-size:12px; font-weight:bold;")
            self._watch_badge.adjustSize()
            self._watch_badge.move(8, 8)
            self._watch_badge.show()
            self._name_label.setStyleSheet("font-size:11px; color:#4ade80;")
        elif mode == Store.WATCH_PART:
            # ویدیو: ◐ + موقعیت رزومه — سند/فشرده (pos=0): فقط ◐ (نیمه‌خوانده)
            self._watch_badge.setText(
                "◐ " + format_duration_short(pos) if pos else "◐")
            self._watch_badge.setStyleSheet(
                "background:#b45309; color:#fff; border-radius:9px;"
                "padding:1px 6px; font-size:10.5px; font-weight:bold;")
            self._watch_badge.adjustSize()
            self._watch_badge.move(8, 8)
            self._watch_badge.show()
            self._name_label.setStyleSheet("font-size:11px; color:#fbbf24;")
        else:
            self._watch_badge.hide()
            self._name_label.setStyleSheet("font-size:11px;")

    def mouseDoubleClickEvent(self, event):
        self.playRequested.emit(self.path)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.watchMenuRequested.emit(self, event.globalPosition().toPoint())
        super().mousePressEvent(event)


class DetailsPage(QWidget):
    backRequested = Signal()
    changed = Signal()
    # (C1) کاور تعویض/حذف شد — با خودِ item تا کتابخانه فقط *همان کارت* را
    # بی‌اعتبار کند، نه کلِ شبکه را؛ receiver: main._on_cover_changed
    coverChanged = Signal(object)
    playRequested = Signal(object, object)      # (item, مسیر فایل) — پخش ردیابی‌شده از پنجره‌ی اصلی

    def __init__(self, store: Store, cache: ThumbnailCache, parent=None,
                 durations: DurationStore | None = None, cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # اندازه‌ی مؤثرِ کش (از مقیاس نمایش ویندوز) — پوسترِ جزئیات حداقل ۳۸۴ می‌گیرد
        self.cache_px = int(cache_px)
        self.fthumbs = FileThumbs(cache.cache_dir)
        self.item = None
        self._sections = None
        self._vsections = []               # سکشن‌های مجازیِ گرید (0.24.0)
        self._tree_cache: dict = {}        # کش ساختار فایل‌ها: (فینگرپرینت، سکشن‌ها) (0.24.0)
        self._hier = None

        # بارگذاری ناهمزمان تامبنیل‌ها
        self._thumb_signals = ThumbSignals()
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        self._thumb_pool = QThreadPool(self)
        # سقف نخ‌ها بالاتر رفت (۳ تا ۶). دو دلیل: ۱) کشِ جدید WebP است که
        # کدکردنش چند برابر سریع‌تر از JPEG XL است و هر نخ خیلی کمتر اشباع می‌شود؛
        # ۲) گزارش کاربر: «ساخت تامبنیل خیلی کنده». هنوز حداقل ۲ هسته برای
        # UI/دیسک/پخش آزاد می‌ماند تا رابط لگ نزند.
        self._thumb_pool.setMaxThreadCount(
            max(3, min(6, max(2, (os.cpu_count() or 4) - 2))))
        self._thumb_pending: dict[tuple, list] = {}   # (path,px) -> [owners]
        self._thumb_queued: set[tuple] = set()

        # استخر تک‌نخه‌ی مخصوص پوستر هدر — تا پشت صف صدها تامبنیل فایل نماند
        self._hero_signals = _HeroSignals()
        self._hero_signals.ready.connect(self._on_hero_ready)
        self._hero_pool = QThreadPool(self)
        self._hero_pool.setMaxThreadCount(1)
        self._hero_key = ""

        # سیاست دید برای تامبنیل فایل‌ها (گزارش کاربر): کارها «می‌خواسته» می‌شوند
        # ولی فقط وقتی استارت می‌خورند که کارتشان در دید باشد؛ در حالت نامحدود کش
        # بقیه هم آرام‌آرام در پس‌زمینه و با اولویت پایین‌تر ساخته می‌شوند
        self._thumb_wanted: set[tuple] = set()   # (path,px) — در انتظارِ شروع
        # سطل‌های رتبه‌دارِ تغذیه‌ی پس‌زمینه — درج هنگام صف (به جای مرتب‌سازی کل
        # فهرست در هر تیکِ ۸۰ms که با هزاران فایل O(n log n) در هر تیک و مجموعاً
        # ده‌ها ثانیه فریز می‌ساخت — گزارش کاربر: «پوشه‌ی ۲۱۳۰فایلی هنگ کرد»).
        # ورودیِ کهنه (که از wanted بیرون رفته) موقع مصرف با یک نگاه رد می‌شود.
        self._bg_buckets: dict[int, deque] = {r: deque() for r in range(4)}
        self._thumb_prio: dict[tuple, int] = {}  # (path,px) → اولویت کار استارت‌شده
        self._thumb_bg: set[tuple] = set()       # کارهای پس‌زمینه‌ی در‌حال‌اجرا (اولویت ۰)
        self._limited = False                    # سقف حجم کش فعال است؟
        self._folder_only = False                # «تامبنیل فقط پوشه‌ها»
        self._thumb_sched = QTimer(self)
        self._thumb_sched.setSingleShot(True)
        self._thumb_sched.timeout.connect(self._start_visible_thumb_jobs)

        # مدت‌زمان ویدیوها (ناهمزمان + کش دائمی — نمونه‌ی مشترک با اسکنر)
        self.dstore = durations if durations is not None else DurationStore(cache.cache_dir)
        self._dur_signals = DurationSignals()
        # استخر مستقل برای پروب زمان‌ها: صف تامبنیل‌ها حق ندارد زمان‌ها را عقب بیندازد
        # (در غیر این صورت پخش، موقتاً مقدار اشتباه/نامعلوم استفاده می‌کرد)
        self._dur_pool = QThreadPool(self)
        self._dur_pool.setMaxThreadCount(2)
        self._dur_signals.ready.connect(self._on_dur_ready)
        self._dur_paths: list = []
        self._dur_known: dict[str, float] = {}
        self._dur_waits: dict[str, list] = {}
        self._dur_queued: set[str] = set()

        # ردیابی تماشا — نظارت بر پروسه‌ها در پنجره‌ی اصلی (PlayTracker) انجام می‌شود
        # تا با بستن این صفحه/برنامه چیزی گم نشود؛ این صفحه فقط UI را تازه می‌کند.
        self._watch_rows: dict[str, list] = {}     # rel -> [ownerها (درخت/کارت)]
        self._track_on = True                      # دسته‌ی جاری وضعیت دیده/خوانده می‌خواهد؟
        self._profile = self.store.track_profile("")  # الگوی وضعیت دسته‌ی جاری (auto پیش‌فرض)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.btn_back = QPushButton(T("→ بازگشت به کتابخانه"))
        self.btn_back.setObjectName("FlatBtn")
        self.btn_back.setFixedWidth(190)
        self.btn_back.clicked.connect(self.backRequested)
        top.addWidget(self.btn_back)
        top.addStretch(1)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(20)

        # ---------------- ستون پوستر ----------------
        poster_col = QVBoxLayout()
        poster_col.setSpacing(10)
        self.poster = QLabel()
        self.poster.setFixedSize(252, 336)
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # بدونِ کادر/نوار (round 13 §۴ — تصمیمِ کاربر): پوستر شناور روی رنگِ
        # صفحه؛ نوارهای letterboxِ ساکن هم شفاف‌اند تا رنگِ تم از پشت دیده شود.
        self.poster.setStyleSheet("background: transparent; border: none;")
        # کاورِ متحرک: لیبلِ فرزندِ دقیقاً هم‌نسبت با تصویر، داخلِ قابِ ۲۵۲×۳۳۶.
        # QLabel با QMovie اندازه‌ی طبیعیِ فریم را می‌کشد و در قابِ ثابت بریده
        # می‌شد (گزارشِ کاربر: گیفِ افقی نصفه دیده می‌شد)؛ setScaledSize روی
        # همین مسیر قابل‌اعتماد نیست. با setScaledContents هر فریم به اندازه‌ی
        # همین لیبلِ هم‌نسبت کشیده می‌شود (آزمایشِ offscreen تأیید کرد). قبل
        # از btn_cover ساخته می‌شود تا دکمه‌ی گوشه همیشه روی فیلم بماند.
        self.poster_movie = QLabel(self.poster)
        self.poster_movie.setStyleSheet("background: transparent; border: none;")
        self.poster_movie.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_movie.setScaledContents(True)   # هر فریم به اندازه‌ی همین لیبل
        self.poster_movie.hide()
        # کاور سفارشی (round 11): راست‌کلیک روی پوستر + دکمه‌ی کوچکِ گوشه برای
        # کشفِ قابلیت بدون راست‌کلیک — همان سه عمل در هر دو مسیر
        self.poster.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.poster.customContextMenuRequested.connect(self._poster_menu)
        self.btn_cover = QPushButton("🖼", self.poster)
        self.btn_cover.setObjectName("FlatBtn")
        self.btn_cover.setFixedSize(38, 32)
        self.btn_cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cover.setToolTip(T("انتخاب کاور سفارشی برای این عنوان"))
        self.btn_cover.move(252 - 38 - 8, 336 - 32 - 8)
        self.btn_cover.clicked.connect(self._poster_menu)
        poster_col.addWidget(self.poster)
        self._movie: QMovie | None = None      # پخشِ کاورِ متحرک (فقط در جزئیات)
        self._poster_media_src = None          # منبعِ کاورِ متحرکِ فعلی — برای پخشِ مجدد با showEvent (D12)

        self.btn_play = QPushButton(T("▶ پخش هوشمند"))
        self.btn_play.setObjectName("PrimaryBtn")
        self.btn_play.setToolTip(
            T("اولین قسمت نیمه‌تمام/ندیده پخش می‌شود و از همان‌جایی که مانده بودی ادامه می‌یابد.\n"
            "ردیابی دقیق با mpv یا PotPlayer — از «⚙ تنظیمات» انتخاب‌شده است."))
        self.btn_open_folder = QPushButton(T("📂 باز کردن پوشه"))
        self.btn_edit = QPushButton(T("✏ ویرایش اطلاعات"))
        self.btn_fav = QPushButton()
        self.btn_copy = QPushButton(T("📋 کپی مسیر"))
        self.btn_play.clicked.connect(self.play_smart)
        self.btn_open_folder.clicked.connect(self._open_folder)
        self.btn_edit.clicked.connect(self._edit_dialog)
        self.btn_fav.clicked.connect(self._toggle_fav)
        self.btn_copy.clicked.connect(self._copy_path)
        for b in (self.btn_play, self.btn_open_folder, self.btn_edit, self.btn_fav, self.btn_copy):
            poster_col.addWidget(b)
        # باکس تغییر دسته و دکمه‌ی «همه دیده‌شده» به ستون پوستر منتقل شدند
        self.cat_combo = NoWheelComboBox()
        self.cat_combo.setSizeAdjustPolicy(
            NoWheelComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cat_combo.setMinimumContentsLength(8)
        self.cat_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cat_combo.setToolTip(
            T("دسته‌ی این عنوان — کلیک برای تعویض بین دسته‌ی اصلی و زیردسته‌ها"))
        self.cat_combo.activated.connect(self._on_hero_cat)
        poster_col.addWidget(self.cat_combo)
        self.btn_watch_all = QPushButton()
        self.btn_watch_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_watch_all.clicked.connect(self._toggle_watch_all)
        poster_col.addWidget(self.btn_watch_all)
        # درخواست کاربر: امتیاز شخصی ⭐ ۱–۵ برای عنوان — ماندگار در دیتابیس،
        # روی کارت نشان داده می‌شود و مرتب‌سازی/پیداکردنش در دسترس است.
        self.rate_combo = NoWheelComboBox()
        self.rate_combo.addItem(T("⭐ امتیاز: —"), 0)
        for _n48 in range(1, 6):
            self.rate_combo.addItem("⭐ " + "★" * _n48, _n48)
        self.rate_combo.currentIndexChanged.connect(self._on_rate_change)
        poster_col.addWidget(self.rate_combo)
        poster_col.addStretch(1)
        body.addLayout(poster_col)

        # ---------------- ستون محتوا ----------------
        info = QVBoxLayout()
        info.setSpacing(10)

        # هدر Hero: عنوان + چیپ‌ها روی پس‌زمینه‌ی بلارشده
        self.hero = _Hero()
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("HeroTitle")
        self.title.setWordWrap(True)
        hl.addWidget(self.title)
        # بازبینی کاربر — «نصفش میره رو اسم دسته و ظاهر برنامه خراب میشه»:
        # چیپ‌ها در چیدمان شناور به سطر بعد می‌شکنند؛ دیگر روی اسم دسته نمی‌افتند.
        chips = FlowLayout(spacing=8)
        # باکس دسته به ستون پوستر منتقل شد — اینجا فقط نام دسته
        self.cat_name_lbl = QLabel()
        self.cat_name_lbl.setStyleSheet(
            "color:#e7edf6; background:#0c111a88; border:1px solid #ffffff30;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
        )
        chips.addWidget(self.cat_name_lbl)
        self.chips_extra = []
        for _ in range(4):
            c = QLabel()
            c.setStyleSheet(
                "color:#e7edf6; background:#0c111a88; border:1px solid #ffffff30;"
                "border-radius:9px; padding:2px 10px; font-size:11.5px;"
            )
            self.chips_extra.append(c)
            chips.addWidget(c)
        self.chip_dur = QLabel()
        self.chip_dur.setStyleSheet(
            "color:#ffd9a0; background:#0c111a88; border:1px solid #fbbf2466;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
        )
        self.chip_dur.hide()
        chips.addWidget(self.chip_dur)
        self.chip_watch = QLabel()
        self.chip_watch.setStyleSheet(
            "color:#b7f7cd; background:#0c111a88; border:1px solid #4ade8055;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
        )
        self.chip_watch.hide()
        chips.addWidget(self.chip_watch)
        hl.addLayout(chips)

        # نوار پیشرفت تماشا (نازک، زیر چیپ‌ها)
        self.watch_bar = QProgressBar()
        self.watch_bar.setTextVisible(False)
        self.watch_bar.setFixedHeight(6)
        self.watch_bar.hide()
        hl.addWidget(self.watch_bar)
        info.addWidget(self.hero)

        # توضیحات
        desc_head = QHBoxLayout()
        dl = QLabel(T("📝 توضیحات"))
        dl.setObjectName("SectionHeader")
        self.btn_desc_edit = QPushButton(T("✏ ویرایش"))
        self.btn_desc_edit.setObjectName("MiniBtn")
        self.btn_desc_edit.clicked.connect(self._edit_desc)
        desc_head.addWidget(dl)
        desc_head.addStretch(1)
        desc_head.addWidget(self.btn_desc_edit)
        info.addLayout(desc_head)

        self.desc_label = QLabel()
        self.desc_label.setObjectName("DescBox")
        self.desc_label.setWordWrap(True)
        info.addWidget(self.desc_label)
        self.desc_editor = QPlainTextEdit()
        self.desc_editor.setMaximumHeight(100)
        self.desc_editor.hide()
        info.addWidget(self.desc_editor)
        self.desc_btns = QWidget()
        db = QHBoxLayout(self.desc_btns)
        db.setContentsMargins(0, 0, 0, 0)
        b_save = QPushButton(T("ذخیره"))
        b_save.setObjectName("PrimaryBtn")
        b_cancel = QPushButton(T("انصراف"))
        b_save.clicked.connect(self._save_desc)
        b_cancel.clicked.connect(self._end_desc_edit)
        db.addStretch(1)
        db.addWidget(b_cancel)
        db.addWidget(b_save)
        self.desc_btns.hide()
        info.addWidget(self.desc_btns)

        # ---------------- فایل‌ها ----------------
        files_head = QHBoxLayout()
        fl = QLabel(T("📂 فایل‌ها"))
        fl.setObjectName("SectionHeader")
        self.files_count = QLabel("")
        self.files_count.setObjectName("MutedLabel")
        files_head.addWidget(fl)
        files_head.addWidget(self.files_count)
        files_head.addStretch(1)
        # ---------------- مرتب‌سازی فایل‌ها (داخل هر بخش/پوشه — درخت به‌هم نمی‌ریزد) ----------------
        self.sort_combo = NoWheelComboBox()
        self.sort_combo.setMinimumWidth(120)
        for lbl, key in ((T("نام"), "name"), (T("نوع فایل"), "type"), (T("حجم"), "size"),
                         (T("تاریخ تغییر"), "modified"), (T("مدت‌زمان"), "duration")):   # جاافتاده
            self.sort_combo.addItem(lbl, key)
        self.sort_combo.setToolTip(
            T("مرتب‌سازی فایل‌های این عنوان — داخل هر بخش/پوشه جدا اعمال می‌شود\n"
            "(ساختار پوشه‌های تودرتو دست‌نخورده می‌ماند؛ فقط ترتیب فایل‌ها عوض می‌شود)"))
        self._file_sort = store.config.get("file_sort", "name")
        if self.sort_combo.findData(self._file_sort) < 0:
            self._file_sort = "name"
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(self._file_sort))
        self._file_sort_desc = bool(store.config.get("file_sort_desc", False))
        self.sort_dir_btn = QPushButton()
        self.sort_dir_btn.setObjectName("FlatBtn")
        self.sort_dir_btn.setFixedWidth(78)
        self.sort_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sync_sort_dir_btn()
        self.sort_combo.currentIndexChanged.connect(self._on_file_sort)
        self.sort_dir_btn.clicked.connect(self._on_file_sort_dir)
        files_head.addWidget(QLabel("↕"))
        files_head.addWidget(self.sort_combo)
        files_head.addWidget(self.sort_dir_btn)
        self.btn_mode_tree = QPushButton(T("☰  درختی"))
        self.btn_mode_grid = QPushButton(T("▦  بندانگشتی"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for b in (self.btn_mode_tree, self.btn_mode_grid):
            b.setCheckable(True)
            b.setObjectName("ModeBtn")
            self.mode_group.addButton(b)
            files_head.addWidget(b)
        self.btn_mode_tree.clicked.connect(lambda: self._set_mode("tree"))
        self.btn_mode_grid.clicked.connect(lambda: self._set_mode("grid"))
        info.addLayout(files_head)

        self.stack = QStackedWidget()

        # حالت درخت
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["نام", "حجم", T("مدت")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setObjectName("FileTree")
        self.tree.setIndentation(20)
        self.tree.setAnimated(True)
        self.tree.expandToDepth(0)
        self.tree.itemDoubleClicked.connect(self._tree_dblclick)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_ctx_menu)
        self.stack.addWidget(self.tree)

        # حالت بندانگشتی
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        # گزارش کاربر: «آیکون‌ها از صفحه می‌زنن بیرون و اسکرول افقی اضافه
        # میشه» — اسکرول افقی گرید همیشه خاموش — چیدمان موظف است با هر عرضی
        # بازچینی شود (resizeEvent در _VSection کارت‌ها را دوباره می‌چیند).
        self.grid_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_container = QWidget()
        self.grid_container.setObjectName("ScrollContent")
        # کانتینر نباید حداقل‌عرضی را به اسکرول‌نما تحمیل کند (رفع قفلِ ستون‌ها)
        self.grid_container.setMinimumWidth(0)
        self.grid_layout = QVBoxLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 8, 4)
        self.grid_layout.setSpacing(6)
        self.grid_scroll.setWidget(self.grid_container)
        self.stack.addWidget(self.grid_scroll)

        # اسکرول ⇒ باززمان‌بندی سیاست دید تامبنیل‌ها (گرید و درخت هر دو)
        self.grid_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_thumb_jobs())
        self.tree.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_thumb_jobs())
        # اسکرول/تغییر اندازه ⇒ واقعی‌سازی پنجره‌ی دیدِ سکشن‌های مجازی
        self.grid_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._grid_sync_visible())
        self.grid_scroll.viewport().installEventFilter(self)

        info.addWidget(self.stack, 1)
        body.addLayout(info, 1)
        root.addLayout(body, 1)

        self._mode = store.config.get("file_view", "grid")
        if self._mode not in ("tree", "grid"):
            self._mode = "grid"

    def eventFilter(self, obj, event):
        # تغییر اندازه‌ی viewport گرید → واقعی‌سازی مجدد پنجره‌ی دید
        try:
            if (obj is self.grid_scroll.viewport()
                    and event.type() == QEvent.Type.Resize):
                QTimer.singleShot(0, self._grid_sync_visible)
        except Exception as _exc_dbg:
            dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
            pass
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ نمایش
    def set_item(self, item):
        self.item = item
        st = self.store
        self._clear_thumb_pending()
        self._thumb_pool.clear()            # کارهای صف‌شده‌ی عنوان قبلی لغو شوند —
        self._thumb_queued.clear()          # وگرنه فایل‌های اولِ عنوان تازه پشت صفِ
        self._triage_y = None               # کهنه می‌ماندند (گزارش کاربر)
        self.fthumbs.archive_enabled = st.archive_thumbs   # تنظیم تازه‌ی کاربر
        # سیاست کش: سقف‌دار = فقط تامبنیلِ در دید؛ «فقط پوشه‌ها» = بدون تامبنیل فایل
        self._limited = bool(st.cache_max_mb())
        self._folder_only = bool(st.folder_thumbs_only)

        # پوستر — فوری جای‌نگه‌دار حروفی نشان بده؛ خود زنجیره‌ی فال‌بک (آیکون/تصویر
        # ← ویدیو ← PDF ← فشرده) در نخ پس‌زمینه ساخته می‌شود تا UI قفل نکند (_HeroJob)
        src = st.poster_of(item)
        src_static = st.poster_of(item, static=True)
        cat = st.category_of(item)
        self._hero_key = str(item.path)
        self._stop_poster_movie()
        self.poster.setPixmap(placeholder_poster(
            st.title(item).strip()[:1] or T("؟"), st.color(cat), 252, 336, 16))
        self.hero.set_background(None)
        # کاورِ متحرک؟ همان‌جا شروع می‌شود؛ hero/backdrop از فریمِ ساکن ساخته می‌شود
        self._set_poster_media(src, src_static)
        self._hero_pool.start(_HeroJob(self, src_static or src, item))
        self.title.setText(st.title(item))

        self._fill_hero_cat_combo()             # بجِ رنگیِ دسته = خودِ باکس دسته
        texts = []
        if item.video_count:
            texts.append(T("🎬 {0} قسمت").format(fa(item.video_count)))
        texts.append(f"💾 {item.size_text}")
        texts.append(T("📦 {0} فایل").format(fa(item.file_count)))
        if item.modified:
            texts.append("🕓 " + datetime.fromtimestamp(item.modified).strftime("%Y/%m/%d"))
        for lbl, txt in zip(self.chips_extra, texts + [""] * (4 - len(texts))):
            lbl.setText(txt)
            lbl.setVisible(bool(txt))

        self.btn_open_folder.setText(T("📂 باز کردن پوشه") if not item.path.is_file() else T("📂 باز کردن پوشه‌ی محتوی"))
        self._refresh_fav_btn()
        self._refresh_rate_combo()              # همگام با امتیازِ عنوانِ جدید
        self._refresh_desc()

        # داده‌ی فایل‌ها
        exclude = {p for p in (item.icon_path, st.poster_of(item))
                   if p and is_reserved_cover(p)}
        loose = getattr(item, "loose_group", False)
        # گروه اسنادِ ریشه فقط فشرده/PDF نمایش می‌دهد (ویدیوها کارت جدای خودشان را دارند)
        only = LOOSE_DOC_EXTS if getattr(item, "loose_docs_only", False) else None
        # کش ساختار فایل‌ها با اثر انگشتِ سبک — برگشت از کتابخانه به همان
        # عنوانِ بدون‌تغییر دیگر get_file_tree را از نو نمی‌زند (گزارش کاربر:
        # «وقتی خارج شی دوباره وارد شی باز از اول باید صبر کنی»).
        ck = (str(item.path), frozenset(exclude), bool(loose), bool(only))
        fp = _dir_fingerprint(item.path)
        cached = self._tree_cache.get(ck)
        if cached is not None and cached[0] == fp:
            self._sections = cached[1]
        else:
            self._sections = get_file_tree(item.path, exclude=exclude, loose=loose,
                                           only_exts=only)
            self._tree_cache[ck] = (fp, self._sections)
            while len(self._tree_cache) > 12:     # کران حافظه — قدیمی‌ترین عنوان برود
                self._tree_cache.pop(next(iter(self._tree_cache)))
        # سلسله‌مراتب درختی فقط برای حالت «درخت» لازم است — در گرید (پیش‌فرض) ساخت
        # همگامش برای پوشه‌های +۲۰۰۰ فایل فقط تاخیر اضافه بود؛ حالا تنبلی ساخته
        # می‌شود (گزارش کاربر: «ورود به پوشه‌ی شلوغ گیر می‌کند»).
        self._hier_params = (item.path, frozenset(exclude), loose, only)
        self._hier = None
        # الگوی وضعیت این دسته (فیلم/کتاب/سفارشی/بدون ترکینگ) — برچسب‌های
        # «دیده شده»/«خوانده شده»/«انجام شد» از روی همین الگو ساخته می‌شوند
        self._profile = st.track_profile(st.category_of(item))
        self._track_on = bool(self._profile.get("enabled", True))
        self._resort_files()          # معیار مرتب‌سازی کاربر (نام/حجم/تاریخ/مدت)
        total = sum(len(s["files"]) for s in self._sections)
        self.files_count.setText(T("({0} فایل)").format(fa(total)))

        # مدت‌زمان ویدیوها: استفاده از کش + صف محاسبه‌ی بقیه
        self._dur_paths = [
            f for s in self._sections for f in s["files"]
            if f.suffix.lower() in VIDEO_EXTS
        ]
        self._dur_known = {}
        self._dur_waits = {}
        self._dur_queued = set()
        for p in self._dur_paths:
            v = self.dstore.get(p)
            if v is not None:
                self._dur_known[str(p)] = v
        self._update_dur_chip()

        self._watch_rows = {}
        self._refresh_watch_ui()
        self._refresh_watch_all_btn()         # دکمه‌ی «همه دیده‌شده» همگام شود
        self._apply_mode(build=True)
        # گزارش کاربر: «باز می‌کنم اسکرول وسطه/آخره به‌جای اول» — با باز
        # شدن هر پوشه اسکرولِ هر دو حالت به ابتدا برمی‌گردد — هم فوری و هم
        # یک‌بار دیگر بعد از نشستِ چیدمان (چیدمان دیرهنگام ممکن است مقدار را
        # به جایی معتبرش برگرداند که همان وسط/ته است).
        self._reset_scroll_top()
        QTimer.singleShot(0, self._reset_scroll_top)

    def _reset_scroll_top(self):
        """اسکرولِ عمودی و افقیِ گرید و درخت به ابتدا (0.33.0)."""
        for sb in (self.grid_scroll.verticalScrollBar(),
                   self.grid_scroll.horizontalScrollBar(),
                   self.tree.verticalScrollBar(),
                   self.tree.horizontalScrollBar()):
            try:
                sb.setValue(0)
            except Exception as _exc_dbg:
                dbg_log(f"_reset_scroll_top failed: {type(_exc_dbg).__name__}")
                pass

    # ---------------- حفظِ جای کاربر هنگامِ تعویضِ کاور (round 19 §۱۷) ----------------
    def _view_state_snapshot(self) -> dict:
        """اسکرولِ گرید/درخت + گره‌های بازِ درخت — برای بازگردانی بعد از rebuild."""
        snap: dict = {}
        for key, sb in (("gv", self.grid_scroll.verticalScrollBar()),
                        ("gh", self.grid_scroll.horizontalScrollBar()),
                        ("tv", self.tree.verticalScrollBar()),
                        ("th", self.tree.horizontalScrollBar())):
            try:
                snap[key] = int(sb.value())
            except Exception:
                snap[key] = 0
        try:
            snap["expanded"] = self._tree_expanded_paths()
        except Exception:
            snap["expanded"] = set()
        return snap

    def _tree_expanded_paths(self) -> set:
        out: set = set()

        def walk(it, prefix):
            for i in range(it.childCount()):
                ch = it.child(i)
                try:
                    path = prefix + "/" + str(ch.text(0))
                    if ch.isExpanded():
                        out.add(path)
                except RuntimeError:
                    continue          # شیءِ C++ رفته
                walk(ch, path)

        walk(self.tree.invisibleRootItem(), "")
        return out

    def _restore_view_state(self, snap: dict) -> None:
        """بازگردانیِ جای کاربر — با singleShot(0) تا بعد از singleShot(0)ِ
        خودِ set_item (که به ابتدا برمی‌گرداند) اجرا شود."""
        if not snap:
            return

        def _do():
            try:
                self.grid_scroll.verticalScrollBar().setValue(snap.get("gv", 0))
                self.grid_scroll.horizontalScrollBar().setValue(snap.get("gh", 0))
                self.tree.verticalScrollBar().setValue(snap.get("tv", 0))
                self.tree.horizontalScrollBar().setValue(snap.get("th", 0))
            except Exception as _exc_dbg:
                dbg_log(f"_restore_view_state scroll failed: {type(_exc_dbg).__name__}")
            exp = snap.get("expanded") or set()
            if exp:
                def walk(it, prefix):
                    for i in range(it.childCount()):
                        ch = it.child(i)
                        try:
                            path = prefix + "/" + str(ch.text(0))
                            ch.setExpanded(path in exp)
                        except RuntimeError:
                            continue
                        walk(ch, path)
                walk(self.tree.invisibleRootItem(), "")

        QTimer.singleShot(0, _do)

    # ------------------------------------------------------------ حالت نمایش
    def _apply_mode(self, build=False):
        if self._mode == "tree":
            self.btn_mode_tree.setChecked(True)
            self.stack.setCurrentWidget(self.tree)
            if build:
                self._build_tree()
        else:
            self.btn_mode_grid.setChecked(True)
            self.stack.setCurrentWidget(self.grid_scroll)
            if build:
                self._build_grid()

    def _set_mode(self, mode):
        self._mode = mode
        self.store.config["file_view"] = mode
        self.store.save_config()
        self._apply_mode(build=True)

    # ------------------------------------------------------------ مرتب‌سازی فایل‌ها
    def _sync_sort_dir_btn(self):
        self.sort_dir_btn.setText(T("↓ نزولی") if self._file_sort_desc else T("↑ صعودی"))
        self.sort_dir_btn.setToolTip(T("کلیک برای تغییر جهت مرتب‌سازی"))

    def _save_file_sort(self):
        self.store.config["file_sort"] = self._file_sort
        self.store.config["file_sort_desc"] = bool(self._file_sort_desc)
        self.store.save_config()

    def _on_file_sort(self, _i):
        self._file_sort = self.sort_combo.currentData()
        # جهت طبیعی هر معیار: نام و نوع‌فایل صعودی پیش‌فرض‌اند (گروه‌بندی خواناتر)
        self._file_sort_desc = self._file_sort not in ("name", "type")
        self._sync_sort_dir_btn()
        self._save_file_sort()
        self._resort_and_rebuild()

    def _on_file_sort_dir(self):
        self._file_sort_desc = not self._file_sort_desc
        self._sync_sort_dir_btn()
        self._save_file_sort()
        self._resort_and_rebuild()

    def _resort_and_rebuild(self):
        self._resort_files()
        self._apply_mode(build=True)

    def _dur_for(self, f: Path) -> float:
        d = self._dur_known.get(str(f))
        if d:
            return d
        if f.suffix.lower() in VIDEO_EXTS:
            try:
                v = self.dstore.get(f)
            except Exception as _exc_dbg:
                dbg_log(f"_dur_for failed: {type(_exc_dbg).__name__}")
                v = None
            if v and v > 0:
                return float(v)
        return 0.0

    def _file_sort_key(self, f: Path):
        name_key = natural_key(f.name.lower())
        mode = self._file_sort
        if mode == "type":
            # درخواست کاربر: مرتب‌سازی بر اساس «نوع فایل» — اول گروه
            # (ویدیو ← تصویر ← فشرده ← سند ← صوتی ← دیگر)، داخل گروه نام طبیعی.
            # کارِ مرتب‌سازی فقط روی «پسوند» است؛ نام فایل به هر زبانی می‌تواند
            # باشد و هیچ محدودیتی ایجاد نمی‌کند (نکته‌ی کاربر).
            return (_TYPE_RANK.get(file_type_group(f), 9), name_key)
        if mode == "size":
            try:
                return (f.lstat().st_size, name_key)
            except OSError:
                return (0, name_key)
        if mode == "modified":
            try:
                return (f.lstat().st_mtime, name_key)
            except OSError:
                return (0.0, name_key)
        return name_key

    def _sorted(self, files: list) -> list:
        """مرتب‌سازی با معیار/جهت جاری — مدت‌نامعلوم در حالت «مدت‌زمان» همیشه تهِ فهرست."""
        rev = self._file_sort_desc
        if self._file_sort == "duration":
            keyed = [(f, self._dur_for(f)) for f in files]
            known = [(f, d) for f, d in keyed if d > 0]
            unknown = [f for f, d in keyed if d <= 0]
            known.sort(key=lambda t: (t[1], natural_key(t[0].name.lower())), reverse=rev)
            unknown.sort(key=lambda f: natural_key(f.name.lower()))
            return [f for f, _ in known] + unknown
        return sorted(files, key=self._file_sort_key, reverse=rev)

    def _resort_files(self):
        if self._sections:
            for s in self._sections:
                s["files"] = self._sorted(s["files"])

    # ------------------------------------------------------------ تامبنیل ناهمزمان
    def _thumb_px_or_queue(self, f: Path, px: int, owner) -> QPixmap | None:
        """تامبنیل کش‌شده را می‌دهد؛ اگر نبود placeholder می‌ماند و تولید واقعی صف می‌شود.

        قبلاً در نبود کش، «کارت نوع‌فایل» همین‌جا همگام روی نخ UI ساخته می‌شد (~۳ م.ث.
        برای هر فایل — با ۷۰۰ فایل یعنی ~۲ ثانیه گیر رابط). حالا فوراً placeholder
        برمی‌گردد و کارت نوع/تامبنیل واقعی هر دو در نخ پس‌زمینه می‌رسند.
        کارها دیگر فوری استارت نمی‌شوند: «می‌خواسته» ثبت می‌شوند و سیاست دید
        (_start_visible_thumb_jobs) استارتشان می‌کند — تا پایینِ لیست هم بی‌نیاز از
        اسکرولِ بالا→پایین تامبنیل بگیرد."""
        cached = self.fthumbs.peek(f, px)
        if cached:
            pm = load_cache_pixmap(cached)
            if pm is not None and not pm.isNull():
                return pm
        if self._folder_only:
            # «تامبنیل فقط پوشه‌ها»: فایل‌ها فقط کارت نوع‌فایل می‌گیرند (سبک و کش‌شده)
            cp = self.fthumbs.card(f, px)
            if cp:
                pm = load_cache_pixmap(cp)
                if pm is not None and not pm.isNull():
                    return pm
            return None
        key = (str(f), px)
        self._thumb_pending.setdefault(key, []).append(owner)
        if key not in self._thumb_queued:
            self._thumb_wanted.add(key)
            self._bg_buckets[self._bg_rank(key)].append(key)   # سطل سبک‌به‌سنگین
            self._schedule_thumb_jobs()
        return None

    # ------------------------------------------------------------ سیاست دید تامبنیل
    def _schedule_thumb_jobs(self):
        if not self._thumb_sched.isActive():
            self._thumb_sched.start(90)

    def _owner_visible(self, owner) -> bool:
        """آیا صاحب این درخواست (کارت گرید یا ردیف درخت) در دید کاربر است؟"""
        try:
            if isinstance(owner, ThumbCard):
                h = max(1, self.grid_scroll.viewport().height())
                # باگ اصلاح‌شده: y() قبلاً نسبت به *کانتینر سکشن* گرفته می‌شد نه
                # viewport — برای هر سکشن بعد از اولی (و حتی آفست هدر اولی) مقایسه
                # با اسکرول غلط بود و سیاست دید خطا می‌کرد. mapTo مستقیم به
                # مختصات viewport تبدیل می‌کند، مستقل از تودرتویی بودن کانتینرها.
                y = owner.mapTo(self.grid_scroll.viewport(), QPoint(0, 0)).y()
                wh = owner.height()
                return y + wh >= -h and y <= 2 * h            # دید + یک‌صفحه حاشیه
            if isinstance(owner, QTreeWidgetItem):
                r = self.tree.visualItemRect(owner)
                vh = max(1, self.tree.viewport().height())
                return r.bottom() >= -vh and r.top() <= 2 * vh
        except Exception as _exc_dbg:
            dbg_log(f"_owner_visible failed: {type(_exc_dbg).__name__}")
            pass
        return True   # نامشخص → محتاطانه بساز

    # ------------------------------------------------------------ دیدِ هندسی گرید
    def _grid_metrics(self) -> tuple[int, int, int]:
        """(تعداد ستون، ارتفاع ردیف، ارتفاع هدر سکشن) — کارت‌ها سایزِ ثابت ۱۵۲×۱۸۶
        در FlowLayout با spacing=10 دارند، پس موقعیت هر فایل *قبل از ساخته‌شدن
        کارتش* قابلِ محاسبه است (کلید حل مشکل «پایین لیست تامبنیل نمی‌گیرد»)."""
        try:
            vpw = self.grid_scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"_grid_metrics failed: {type(_exc_dbg).__name__}")
            vpw = 800
        cont_w = max(120, vpw - 10)        # margin چپ/راست grid_layout = 2+8
        avail = cont_w - 8                 # margin چپ/راست FlowLayout = 4+4
        cols = max(1, int((avail + 10) // (152 + 10)))
        hdr = getattr(self, "_grid_hdr_h", 34) or 34
        return cols, 186 + 10, hdr

    def _visible_grid_files(self) -> list:
        """فایل‌هایی که کارتشان (حتی اگر هنوز ساخته نشده باشد!) ناحیه‌ی دیدِ
        اسکرول را قطع می‌کند — محاسبه‌ی هندسی با یک‌ردیف حاشیه از هر طرف."""
        if self._mode != "grid" or not self._sections or self._folder_only:
            return []
        try:
            sb = self.grid_scroll.verticalScrollBar()
            h = max(1, self.grid_scroll.viewport().height())
            y0 = sb.value()
        except Exception as _exc_dbg:
            dbg_log(f"_visible_grid_files failed: {type(_exc_dbg).__name__}")
            return []
        lo, hi = y0 - h, y0 + 2 * h         # دید + یک‌صفحه بالا/پایین (مثل کارت‌ها)
        cols, row_h, hdr = self._grid_metrics()
        out = []
        y = 2                                # margin بالای grid_layout
        for section in self._sections:
            n = len(section["files"])
            y += hdr + 6                     # دکمه‌ی سکشن + spacing چیدمان
            rows = max(1, (n + cols - 1) // cols)
            cont_h = rows * row_h - 10 + 16  # -spacing آخر + margin بالا/پایین جریان
            c0, c1 = y, y + cont_h
            if c1 >= lo and c0 <= hi and n:
                r0 = max(0, int((lo - c0 - 8) // row_h))
                r1 = min(rows - 1, int((hi - c0 - 8) // row_h) + 1)
                for r in range(r0, r1 + 1):
                    out.extend(section["files"][r * cols:(r + 1) * cols])
            y = c1 + 6
        return out

    def _thumb_start(self, key: tuple, prio: int):
        """استارت یک کار تامبنیل با ثبت اولویت — تنه به تنه با _on_thumb_ready."""
        self._thumb_queued.add(key)
        self._thumb_prio[key] = prio
        if prio == 0:
            self._thumb_bg.add(key)
        self._thumb_pool.start(
            ThumbJob(self.fthumbs, key[0], key[1], self._thumb_signals), prio)

    def _retriage_thumb_queue(self):
        """لغو کارهای صف‌شده‌ی اجرانشده و بازگرداندنشان به «می‌خواسته».

        QThreadPool نمی‌تواند کارِ صف‌شده را بازاولویت کند؛ فقط clear می‌کند. پس
        وقتی کاربر پرش بزرگی در اسکرول می‌کند، کارهای کهنه (دور از دید) لغو و
        اولویت‌بندی کامل از روی دیدِ تازه بازسازی می‌شود — کلید حل «بازم از قبل
        ترش درست می‌کنه» (گزارش کاربر). کارهای در حال اجرا لمس نمی‌شوند؛ نتیجه‌شان
        بی‌ضرر دور ریخته می‌شود.
        """
        self._thumb_pool.clear()
        back = list(self._thumb_queued)
        self._thumb_queued.clear()
        for k in back:
            self._thumb_bg.discard(k)
            self._thumb_prio.pop(k, None)
            self._thumb_wanted.add(k)
            self._bg_buckets[self._bg_rank(k)].append(k)

    def _start_visible_thumb_jobs(self):
        # ری‌تریج با پرش بزرگِ اسکرول (بیشتر از ~۲ صفحه): صف کهنه بازسازی می‌شود
        try:
            if self._mode == "grid":
                _sb = self.grid_scroll.verticalScrollBar()
                _h = max(1, self.grid_scroll.viewport().height())
            else:
                _sb = self.tree.verticalScrollBar()
                _h = max(1, self.tree.viewport().height())
            _y = _sb.value()
            _last = getattr(self, "_triage_y", None)
            if _last is None:
                self._triage_y = _y
            elif abs(_y - _last) > 2 * _h and self._thumb_queued:
                self._triage_y = _y
                self._retriage_thumb_queue()
        except Exception as _exc_dbg:
            dbg_log(f"_start_visible_thumb_jobs failed: {type(_exc_dbg).__name__}")
            pass
        # فاز ۰ (هندسی): فایل‌های ناحیه‌ی دید — *حتی اگر کارتشان هنوز ساخته نشده*.
        # علت باگ گزارش‌شده‌ی «رفتم ته لیست ولی از بالا به پایین ساخت»: تا قبل از این
        # فقط کارت‌های موجود می‌توانستند اولویت بگیرند و کارت‌های تهِ لیست هنگام
        # پرش سریع هنوز وجود نداشتند؛ حالا محاسبه‌ی هندسیِ پیکسل‌ثابتِ گرید مستقیم
        # فایل‌های دیده‌شده را پیدا و با بالاترین اولویت (۱۰) استارت می‌کند. وقتی
        # کارت بعداً جریانی ساخته شود، peek() به کش تازه می‌خورد و فوری نمایش می‌دهد.
        if not self._folder_only:
            for f in self._visible_grid_files():
                key = (str(f), 132)
                self._thumb_wanted.discard(key)
                if key not in self._thumb_queued:
                    self._thumb_start(key, 10)
        # فاز ۱: کارت‌های موجودِ در دید — *فقط حالت درختی*؛ در گرید، فاز ۰ (هندسیِ
        # پیکسل‌ثابت) همان ناحیه را با حاشیه‌ی یکسان مستقیم و ارزان پوشش می‌دهد.
        # قبلاً در اینجا برای هر کلیدِ فهرستِ می‌خواسته (پوشه‌ی +۲۰۰۰ فایل یعنی
        # هزاران ورودی) در هر تیکِ ۹۰ms یک mapTo هندسی زده می‌شد: هزینه‌ی O(n) در
        # هر تیک = ده‌ها ثانیه چرخه‌ی بیهوده و فریزِ ورود به پوشه‌های خیلی پرفایل
        # (گزارش کاربر: «مجبور شدم برنامه را ببندم و دوباره باز کنم»).
        if self._mode != "grid":
            for key in list(self._thumb_wanted):
                owners = self._thumb_pending.get(key) or []
                if any(self._owner_visible(o) for o in owners):
                    self._thumb_wanted.discard(key)
                    if key not in self._thumb_queued:
                        self._thumb_start(key, 10)
        # فاز ۲ (فقط نامحدود): بقیه هم کم‌کم در پس‌زمینه — ولی با سقف هم‌پوشانی، تا
        # نخ‌ها همیشه برای کارهای مرئی (فاز ۰/۱) آزاد بمانند؛ قبلاً سیل کارهای
        # پس‌زمینه صف را اشباع می‌کرد و کاربر پایینِ لیست چشم‌انتظار می‌ماند.
        if not self._limited:
            self._pump_bg_thumbs()
        # فاز ۳: مدتِ ویدیوهای «در دید» — فقط همین‌ها، نه هزاران فایلِ پوشه
        # (رفع گزارش کاربر: گیر و کندی هنگام ورود به پوشه‌ی +۲۰۰۰ فایل)
        self._dur_queue_visible()

    # رتبه‌ی سبک‌به‌سنگین برای تغذیه‌ی پس‌زمینه — با کش به‌ازای *پسوند* (نه فایل!)
    # چون group_of یک Path کامل می‌سازد (~۱۴ میکروثانیه) و مرتب‌سازیِ هر تیکِ ۸۰ms
    # روی هزاران کلید یعنی میلیون‌ها Path و ده‌ها ثانیه فریز (گزارش کاربر:
    # «پوشه‌ی ۲۱۳۰ فایلی برنامه را هنگ کرد»). پسوندها معدود‌اند، پس کش کافی است.
    _BG_ORDER = {"image": 0, "doc": 1, "code": 1, "other": 1, "exe": 1,
                 "audio": 1, "archive": 2, "video": 3}

    def _bg_rank(self, key) -> int:
        cache = getattr(self, "_ext_rank_cache", None)
        if cache is None:
            cache = self._ext_rank_cache = {}
        name = key[0]
        i = name.rfind(".")
        ext = name[i:].lower() if i >= 0 else ""
        r = cache.get(ext)
        if r is None:
            try:
                r = self._BG_ORDER.get(group_of("x" + ext), 1)
            except Exception as _exc_dbg:
                dbg_log(f"_bg_rank failed: {type(_exc_dbg).__name__}")
                r = 1
            cache[ext] = r
        return r

    def _pump_bg_thumbs(self):
        """تغذیه‌ی تدریجی کارهای پس‌زمینه (اولویت ۰) تا سقف نصفِ نخ‌ها.

        نسخه‌ی 0.26.0 (گزارش کاربر: «چندین فایل ویدیو در زیر یک ثانیه تامبنیل بساز»):
        پمپ پس‌زمینه دیگر ویدیو/فشرده را eager نمی‌سازد — آن‌ها فقط از مسیرِ
        «سیاست دید» وقتی کارتشان واقعاً نزدیکِ viewport است ساخته می‌شوند (دقیقاً
        رفتار اکسپلورر: دیده‌شده‌ها فوری، بقیه هرگز یا وقتی دیده شوند). پمپ فقط
        سطل‌های سبک (تصویر/سند/متفرقه) را جلو می‌برد که کسری از ثانیه‌اند.
        """
        cap = max(2, self._thumb_pool.maxThreadCount() // 2)
        n = 0
        for _round in range(2):
            # مصرف از سطل‌های سبک فقط (r=0 تصویر، r=1 سند/متفرقه)؛ هر درج/رد O(1)
            # است (به‌جای مرتب‌سازی کاملِ فهرست در هر تیک — آن با هزاران فایل میلیون‌ها
            # کلید‌سازی می‌ساخت و برنامه هنگ می‌کرد). ورودی کهنه رد می‌شود.
            for r in range(2):
                bucket = self._bg_buckets[r]
                while bucket and n < 24 and len(self._thumb_bg) < cap:
                    key = bucket.popleft()
                    if key not in self._thumb_wanted:
                        continue            # کهنه — قبلاً مصرف/لغو شده
                    self._thumb_wanted.discard(key)
                    if key in self._thumb_queued:
                        continue            # فاز دیدِ کاربر زودتر شروعش کرده
                    self._thumb_start(key, 0)
                    n += 1
                if n >= 24 or len(self._thumb_bg) >= cap:
                    break
            if n or not self._thumb_wanted:
                break
            # خودترمیم: کلیدهای سبکِ باقی‌مانده را بازسطل‌بندی کن (سنگین‌ها دست‌نخورده
            # در wanted می‌مانند تا سیاست دید هر وقت نزدیک شد بسازدشان).
            for key in list(self._thumb_wanted):
                if self._bg_rank(key) < 2:
                    self._bg_buckets[self._bg_rank(key)].append(key)
        light_left = bool(self._bg_buckets[0] or self._bg_buckets[1])
        if light_left and not self._limited:
            QTimer.singleShot(80, self._pump_bg_thumbs)

    def _on_hero_ready(self, key: str, png: str, hero: str):
        """پوستر هدر + بکدراپ از نخ پس‌زمینه رسید — فقط اگر همان عنوان جاری باشد."""
        if key != self._hero_key or self.item is None:
            return                     # کاربر در این فاصله عنوان دیگری را باز کرده
        st, item = self.store, self.item
        cat = st.category_of(item)
        pm = load_cache_pixmap(png) if png else None
        if pm is None or pm.isNull():
            if getattr(self, "_movie", None) is None:
                pm = placeholder_poster(
                    st.title(item).strip()[:1] or T("؟"), st.color(cat), 252, 336, 16)
            else:
                pm = None   # انیمیشن در حالِ پخش — لایه‌ی شفاف/فریمِ ساکنِ زیرِ آن
                            # دست نمی‌خورد و بنفشِ placeholder برنمی‌گردد (round 17 §۲)
        else:
            pm = poster_pixmap(pm, 252, 336, 16, bg="none")   # بدونِ قاب، شکلِ خودِ تصویر (round 13 §۴)
            # فریمِ ساکنِ واقعی — ساکن: مستقیم؛ متحرک: زیرِ فیلم (نوارهای خالی
            # تم‌رنگ می‌مانند، نه بنفشِ placeholder؛ گزارش کاربر round 17 §۲).
            # لیبلِ فیلم فرزندِ پوستر است و رویِ این پیکسمپ کشیده می‌شود —
            # پس «لمس نکنِ» قدیمی لازم نبود؛ به‌روزرسانیِ آن بی‌خطر است.
        if pm is not None:
            self.poster.setPixmap(pm)
        hero_pm = load_cache_pixmap(hero) if hero else None
        self.hero.set_background(hero_pm)

    # ------------------------------------------------------------ کاور سفارشی
    def hideEvent(self, event):
        """D12: برگشت به کتابخانه صفحه را فقط *پنهان* می‌کند — QMovie تا
        stop() نشود فریم دیکد می‌کند (CPU/باتریِ بی‌دلیل). با پنهان‌شدن
        پخش می‌ایستد و با بازگشت، showEvent همان منبع را دوباره راه می‌اندازد."""
        super().hideEvent(event)
        try:
            self._stop_poster_movie()
        except Exception as _exc_dbg:
            dbg_log(f"DetailsPage.hideEvent failed: {type(_exc_dbg).__name__}")

    def showEvent(self, event):
        """D12: بازگشت به جزئیات — کاورِ متحرکِ همان عنوان دوباره پخش می‌شود."""
        super().showEvent(event)
        try:
            if self.isVisible() and self.item is not None \
                    and getattr(self, "_poster_media_src", None) \
                    and getattr(self, "_movie", None) is None:
                # (round 19 §۴) گاردِ تک‌منبعی: اگر set_item پیش از نمایان‌شدن
                # فیلم را شروع کرده (سناریوی ورودِ اولیه — show_details اول
                # set_item می‌کند بعد setCurrentWidget)، نمونهٔ دوم ساخته/start
                # نمی‌شود؛ نمونهٔ قدیمی بدونِ stop رها نمی‌شود (CPU/handle فایل).
                self._set_poster_media(*self._poster_media_src)
        except Exception as _exc_dbg:
            dbg_log(f"DetailsPage.showEvent failed: {type(_exc_dbg).__name__}")

    def _stop_poster_movie(self):
        """توقفِ پخشِ کاورِ متحرک قبلی (اگر بود) — پیش از هر set_item/حذف کاور.

        (round 19 §۴) شیءِ متوقف‌شده با deleteLater آزاد می‌شود — قبلاً فقط
        مرجعِ پایتون رها می‌شد و نمونه‌های جایگزین‌شده به‌عنوان فرزندِ صفحه
        تا نابودیِ خودِ صفحه زنده می‌ماندند (تجمعِ QObject در نشستِ بلند).
        stop پیش از deleteLater الزامی است و ترتیب حفظ شده."""
        mv = getattr(self, "_movie", None)
        if mv is not None:
            self._movie = None
            try:
                mv.stop()
            except RuntimeError:
                pass                       # شیءِ C++ رفته
            try:
                mv.deleteLater()
            except RuntimeError:
                pass
        self._hide_poster_movie()          # پوسترِ ساکن دیگر فیلم نمی‌گیرد — setMovie(None) لازم نیست

    def _poster_slot_rect(self, sw: int, sh: int) -> tuple[int, int, int, int]:
        """مستطیلِ متناسب با نسبتِ تصویر داخلِ قابِ ۲۵۲×۳۳۶ (x, y, w, h)."""
        if sw <= 0 or sh <= 0:
            return 0, 0, 252, 336
        k = min(252 / sw, 336 / sh)
        w, h = max(1, round(sw * k)), max(1, round(sh * k))
        return (252 - w) // 2, (336 - h) // 2, w, h

    def _install_movie_underlay(self) -> None:
        """فریمِ ساکنِ کاور زیرِ انیمیشن نصب می‌شود (round 17 §۲).

        گیفِ مربعی/افقی فقط بخشِ میانیِ قابِ ۲۵۲×۳۳۶ را می‌پوشاند؛ قبلاً پشتِ
        فیلم همان placeholderِ حروفی می‌ماند که زمینه‌اش رنگِ دسته است
        (دسته‌ی پیش‌فرض بنفش — ‎#a78bfa) و نوارِ خالیِ اطرافِ فیلم بنفش
        می‌ماند — ناهماهنگ با تمِ کاغذ (گزارش کاربر). حالا فریمِ ساکنِ خودِ
        کاور (letterboxِ شفاف) زیرِ فیلم می‌نشیند و رنگِ تم از پشتِ نوارها
        دیده می‌شود؛ لیبلِ فیلم *فرزندِ* پوستر است و رویِ این لایه کشیده
        می‌شود، پس نصبِ آن بی‌خطر است. اگر فریمِ ساکن خوانده نشد، یک لایه‌ی
        شفاف می‌نشیند تا بنفشِ placeholder هرگز پشتِ فیلم نیاید."""
        src = (getattr(self, "_poster_media_src", None) or (None, None))[1]
        try:
            pm = load_cache_pixmap(src) if src else None
            if pm is not None and not pm.isNull():
                self.poster.setPixmap(poster_pixmap(pm, 252, 336, 16, bg="none"))
                return
            clear = QPixmap(252, 336)
            clear.fill(Qt.GlobalColor.transparent)
            self.poster.setPixmap(clear)
        except Exception as _exc_dbg:
            dbg_log(f"_install_movie_underlay failed: {type(_exc_dbg).__name__}")

    def _set_poster_media(self, src, src_static) -> None:
        """کاور متحرک → QMovie روی لیبلِ فرزندِ هم‌نسبت؛ ساکن → همان مسیرِ hero.

        خودِ self.poster با QMovie فریم را در قابِ ثابت بریده می‌کشد — لیبلِ
        فرزندِ هم‌نسبت (poster_movie) راه‌حلِ کاورِ افقی/مربعی است. بدونِ
        raise_() تا دکمه‌ی گوشه‌ی کاور همیشه روی فیلم بماند (کلیک‌پذیر)."""
        try:
            # (round 19 §۴) lifecycle تک‌منبعی: هر set/replace اول نمونهٔ قبلی
            # را کامل stop و جدا می‌کند — قبلاً نمونهٔ دوم روی نمونهٔ اول
            # نوشته می‌شد و اولی (فرزندِ صفحه، هنوز running) بدونِ stop
            # می‌ماند: CPU/RAM بی‌دلیل + هندلِ بازِ فایل در ویندوز.
            self._stop_poster_movie()
            if src is None or src_static is None or Path(src) == Path(src_static):
                self._poster_media_src = None
                self._hide_poster_movie()      # ساکن: پوستر از _on_hero_ready می‌آید
                return
            self._poster_media_src = (src, src_static)
            mv = QMovie(str(src), parent=self)
            mv.setCacheMode(QMovie.CacheMode.CacheAll)
            sz = QImageReader(str(src)).size()   # ابعادِ فریمِ اول (QMovie.size تا شروع پر نیست)
            sw, sh = (sz.width(), sz.height()) if sz.isValid() else (252, 336)
            self.poster_movie.setGeometry(*self._poster_slot_rect(sw, sh))
            self._install_movie_underlay()   # فریمِ ساکن زیرِ فیلم — نه placeholderِ بنفش (round 17 §۲)
            self.poster_movie.setMovie(mv)
            self.poster_movie.show()
            mv.start()
            self._movie = mv
        except Exception as _exc_dbg:
            dbg_log(f"_set_poster_media failed: {type(_exc_dbg).__name__}")
            self._hide_poster_movie()
            self._movie = None

    def _hide_poster_movie(self) -> None:
        """لیبلِ فیلم را پاک/پنهان کن (بدونِ لمسِ پوسترِ ساکن)."""
        try:
            self.poster_movie.setMovie(None)
            self.poster_movie.hide()
        except Exception as _exc_dbg:
            dbg_log(f"_hide_poster_movie failed: {type(_exc_dbg).__name__}")

    def _poster_menu(self, pos=None):
        """منوی کاورِ سفارشی روی پوستر (راست‌کلیک یا دکمه‌ی گوشه)."""
        item = self.item
        if item is None:
            return
        menu = QMenu(self)
        a_pick = menu.addAction(T("🖼 انتخاب کاور…"))
        has_cover = bool(self.store.custom_poster(item.path)[0])
        a_clear = menu.addAction(T("↺ بازگشت به آیکون پوشه")) if has_cover else None
        menu.addSeparator()
        a_open = menu.addAction(T("📂 باز کردن پوشه‌ی کاورها"))
        if pos is None:
            pos = self.poster.rect().center()
        act = menu.exec(self.poster.mapToGlobal(pos))
        if act is a_pick:
            from ui.cover_actions import pick_cover
            pick_cover(self, self.store, item, self._after_cover_change)
        elif a_clear is not None and act is a_clear:
            from ui.cover_actions import clear_cover
            clear_cover(self, self.store, item, self._after_cover_clear)
        elif act is a_open:
            from ui.cover_actions import open_covers_folder
            open_covers_folder(self)

    def _after_cover_change(self, changed: bool):
        """پس از تغییر کاور: پوستر + hero از نو ساخته می‌شوند (همان مسیرِ reload).

        (C1) قبلاً فقط همین صفحه تازه می‌شد و هیچ خبری به کتابخانه نمی‌رفت —
        کارت تا refresh/rescan کاورِ قدیمی نشان می‌داد. حالا coverChanged
        emit می‌شود تا main کشِ پوسترِ *همان* عنوان را بی‌اعتبار کند.
        (round 19 §۱۷) جای کاربر (اسکرول/گره‌های بازِ درخت) هم حفظ می‌شود —
        قبلاً set_item کامل همه‌چیز را به ابتدا برمی‌گرداند."""
        if not changed:
            return
        self._stop_poster_movie()
        self._tree_cache.clear()      # exclude-set شاملِ مسیرِ پوستر است — همه کهنه‌اند
        if self.item is not None:
            snap = self._view_state_snapshot()
            self.set_item(self.item)
            self._restore_view_state(snap)
            self.coverChanged.emit(self.item)   # کارتِ کتابخانه هم تازه شود (C1)
        self._set_status_hint(T("کاور به‌روز شد ✓"))

    def _after_cover_clear(self, changed: bool):
        """پس از حذفِ کاور سفارشی: بازگشت به آیکونِ پوشه با بازسازیِ پوستر.

        (C1) همان مسیرِ تعویض — کارتِ کتابخانه باید به آیکونِ پوشه برگردد.
        (round 19 §۱۷) حفظِ جای کاربر — همان مکانیزمِ تعویض."""
        if not changed:
            return
        self._stop_poster_movie()
        self._tree_cache.clear()
        if self.item is not None:
            snap = self._view_state_snapshot()
            self.set_item(self.item)
            self._restore_view_state(snap)
            self.coverChanged.emit(self.item)   # کارتِ کتابخانه هم تازه شود (C1)
        self._set_status_hint(T("کاور حذف شد"))

    def _on_thumb_ready(self, path_str: str, png: str, px: int):
        key = (path_str, px)
        self._thumb_queued.discard(key)
        prio = self._thumb_prio.pop(key, None)
        if prio == 0:
            self._thumb_bg.discard(key)
        owners = self._thumb_pending.pop(key, [])
        pm = load_cache_pixmap(png) if png else None
        if pm is None or pm.isNull():
            if png:
                return
            # شکست تولید (ویدیوی خراب، آرشیو رمزدار/ناشناخته…): کارت نوع‌فایل
            # جایگزین می‌شود تا کارت برای همیشه placeholder نماند. (ThumbJob از
            # این نسخه در شکست هم سیگنال می‌دهد — وگرنه کلید در صف گیر می‌کرد.)
            try:
                cp = self.fthumbs.card(path_str, px)
            except Exception as _exc_dbg:
                dbg_log(f"_on_thumb_ready failed: {type(_exc_dbg).__name__}")
                cp = None
            pm = load_cache_pixmap(cp) if cp else None
            if pm is None or pm.isNull():
                return
        for owner in owners:
            try:
                if isinstance(owner, ThumbCard):
                    owner.set_thumb(rounded_pixmap(pm, 132, 132, 10))
                elif isinstance(owner, QTreeWidgetItem):
                    owner.setIcon(0, QIcon(pm.scaled(
                        26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)))
            except RuntimeError:
                pass   # ویجت حذف شده
        if not self._limited:
            self._pump_bg_thumbs()       # جای خالی پس‌زمینه پر شود

    def _clear_thumb_pending(self):
        self._thumb_pending.clear()
        self._thumb_wanted.clear()
        self._thumb_prio.clear()
        self._thumb_bg.clear()
        for b in self._bg_buckets.values():
            b.clear()

    def cancel_pending(self):
        """لغو فوری همه‌ی کار پس‌زمینه‌ی صفحه — هنگام خروج از پوشه صدا زده می‌شود.

        گزارش کاربر (0.22.0): «حتی اگر خارج شی از آن پوشه برنامه کند می‌ماند».
        علت: صف هزاران تامبنیل + زنجیره‌ی تغذیه‌ی پس‌زمینه + پروبهای ffprobe
        عنوانِ رهاشده همچنان تا ته کشیده می‌شدند و داخل کتابخانه هم CPU/دیسک
        می‌خوردند. این متد بدون disconnect (صفحه برای عنوان بعدی زنده می‌ماند)
        صف‌ها را خالی و QRunnableهای صف‌شده را حذف می‌کند؛ کارهای در‌حال‌اجرا
        هم نتیجه‌شان با گارد RuntimeError و چکِ wanted/queued رد می‌شود.
        """
        try:
            self._clear_thumb_pending()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        for pool in (self._thumb_pool, self._hero_pool, self._dur_pool):
            try:
                pool.clear()            # کارهای صف‌شده لغو می‌شوند
            except Exception as _exc_dbg:
                dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
                pass
        try:
            self._thumb_queued.clear()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._dur_queued.clear()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        self._triage_y = None           # موقعیت‌نگاشت دید برای عنوان قبلی

    def shutdown(self):
        """بستن تمیز صفحه — قطع سیگنال‌ها و لغو صف نخ‌ها.

        رفع ارور «RuntimeError: Signal source has been deleted» هنگام خروج
        (گزارش کاربر): کار در حال اجرای QRunnable بعد از حذف شدن QObject
        سیگنال‌ها emit می‌کرد. disconnect + clear + گارد RuntimeError داخل
        خودِ کارها (لایه‌ی دوم دفاع) آن را بی‌صدا می‌کند.
        """
        for sig in (self._thumb_signals, self._hero_signals, self._dur_signals):
            try:
                sig.ready.disconnect()
            except (TypeError, RuntimeError):
                pass
        try:
            self._thumb_sched.stop()
        except Exception as _exc_dbg:
            dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
            pass
        for pool in (self._thumb_pool, self._hero_pool, self._dur_pool):
            try:
                pool.clear()
            except Exception as _exc_dbg:
                dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
                pass

    # ------------------------------------------------------------ مدت‌زمان ویدیوها
    def _dur_apply_or_wait(self, f: Path, owner):
        """مثل _dur_lookup ولی بدون صفِ فوری — محاسبه بعداً برای فایل‌های در دید."""
        key = str(f)
        v = self._dur_known.get(key)
        if v is not None:
            if v > 0:
                self._apply_dur(owner, format_duration(v))
            return
        if not self.store.compute_durations:
            return   # محاسبه‌ی مدت از تنظیمات خاموش است — فقط کش استفاده شود
        self._dur_waits.setdefault(key, []).append(owner)

    def _dur_queue_visible(self):
        """محاسبه‌ی مدت را فقط برای ویدیوهای ناحیه‌ی دیدِ اسکرول صف می‌کند."""
        if not self.store.compute_durations or not self._dur_waits:
            return
        try:
            if self._mode == "grid":
                files = self._visible_grid_files()
            else:
                files = None            # درخت: همان رفتار قبلی (همه‌ی ردیف‌ها صف می‌شوند)
        except Exception as _exc_dbg:
            dbg_log(f"_dur_queue_visible failed: {type(_exc_dbg).__name__}")
            return
        if files is None:
            return
        for f in files:
            try:
                if f.suffix.lower() not in VIDEO_EXTS:
                    continue
            except Exception as _exc_dbg:
                dbg_log(f"_dur_queue_visible failed: {type(_exc_dbg).__name__}")
                continue
            key = str(f)
            if key in self._dur_waits and key not in self._dur_queued \
                    and key not in self._dur_known:
                self._dur_queued.add(key)
                self._dur_pool.start(DurationJob(self.dstore, key, self._dur_signals))

    def _dur_lookup(self, f: Path, owner):
        """مدت کش‌شده را اعمال می‌کند یا محاسبه را صف می‌کند."""
        key = str(f)
        v = self._dur_known.get(key)
        if v is not None:
            if v > 0:
                self._apply_dur(owner, format_duration(v))
            return
        if not self.store.compute_durations:
            return   # محاسبه‌ی مدت از تنظیمات خاموش است — فقط کش استفاده شود
        self._dur_waits.setdefault(key, []).append(owner)
        if key not in self._dur_queued:
            self._dur_queued.add(key)
            self._dur_pool.start(DurationJob(self.dstore, key, self._dur_signals))
        if isinstance(owner, QTreeWidgetItem):
            owner.setText(2, "…")

    def _apply_dur(self, owner, text: str):
        try:
            if isinstance(owner, ThumbCard):
                owner.set_duration(text)
            elif isinstance(owner, QTreeWidgetItem):
                owner.setText(2, text)
        except RuntimeError:
            pass

    def _on_dur_ready(self, path_str: str, val: float):
        self._dur_queued.discard(path_str)
        self._dur_known[path_str] = val
        owners = self._dur_waits.pop(path_str, [])
        text = format_duration(val) if val > 0 else ""
        for owner in owners:
            self._apply_dur(owner, text)
        self._update_dur_chip()

    def _update_dur_chip(self):
        need = len(self._dur_paths)
        probing = self.store.compute_durations
        # در حالت خاموش، هیچ انتظار محاسبه‌ای نیست → همه «تمام‌شده» فرض می‌شوند
        done = (sum(1 for p in self._dur_paths if str(p) in self._dur_known)
                if probing else need)
        total = sum(
            self._dur_known.get(str(p), 0)
            for p in self._dur_paths
            if self._dur_known.get(str(p), -1) > 0
        )
        # اگر محاسبه چیزی نداد، از مقدار ذخیره‌شده در اسکن استفاده کن
        meta_total = 0
        if total <= 0 and self.item is not None:
            meta_total = self.store.total_duration(self.item)
        if not need and not meta_total:
            self.chip_dur.hide()
            return
        if done < need:
            text = (f"⏱ {format_duration(total)}…" if total > 0 else T("⏱ محاسبه…"))
            text += f" {fa(done)}/{fa(need)}"
            self.chip_dur.setText(text)
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها (در حال محاسبه)"))
            self.chip_dur.show()
        elif total > 0:
            self.chip_dur.setText(T("⏱ مجموع: {0}").format(format_duration(total)))
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها"))
            self.chip_dur.show()
        elif meta_total > 0:
            self.chip_dur.setText(T("⏱ مجموع: {0}").format(format_duration(meta_total)))
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها"))
            self.chip_dur.show()
        else:
            self.chip_dur.hide()

    # ------------------------------------------------------------ وضعیت تماشا/خواندن (UI)
    def _status_labels(self, f: Path | None = None) -> tuple[str, str, str, bool]:
        """(برچسب تمام، برچسب نیمه، نام فعالیت، رزومه‌ی موقعیت؟) از الگوی دسته‌ی جاری.

        الگوی auto برچسب را بر اساس نوع فایل می‌سازد (ویدیو→تماشا، سند→خواندن)؛
        الگوی پین‌شده (فیلم/کتاب/سفارشی) برچسب خودش را تحمیل می‌کند.
        0.50.0: همه‌ی برچسب‌ها از T() عبور می‌کنند تا ترجمه‌ی کامل باشد.
        """
        p = getattr(self, "_profile", None) or self.store.track_profile("")
        is_vid = bool(f) and Path(f).suffix.lower() in VIDEO_EXTS
        if p.get("id") == "auto":
            b = p["vid"] if is_vid else p["doc"]
            return T(b["done"]), T(b["part"]), T(b["act"]), is_vid
        # برچسب‌ها از T() عبور می‌کنند (برای الگوهای داخلی ترجمه، برای سفارشی فال‌بک)
        return (T(p.get("done") or "تمام شد"), T(p.get("part") or "نیمه"),
                T(p.get("act") or "پیشرفت"), bool(p.get("id") == "video" and is_vid))

    def _pool_labels(self, has_vids: bool) -> tuple[str, str, str]:
        """برچسب‌های چیپ بر اساس محتوای عنوان و الگو (بدون فایل مشخص).
        0.50.0: همه‌ی برچسب‌ها از T() عبور می‌کنند."""
        p = getattr(self, "_profile", None) or self.store.track_profile("")
        if p.get("id") == "auto":
            b = p["vid"] if has_vids else p["doc"]
            return T(b["done"]), T(b["part"]), T(b["act"])
        return (T(p.get("done") or "تمام شد"), T(p.get("part") or "نیمه"),
                T(p.get("act") or "پیشرفت"))

    def _refresh_watch_ui(self):
        """چیپ + نوار پیشرفت در هدر — برچسب از الگوی وضعیت دسته (تماشا/خواندن/سفارشی)."""
        if self.item is None or not self._track_on:
            self.chip_watch.hide()
            self.watch_bar.hide()
            return
        # مخرج پیشرفت: ویدیوها اگر هستند، وگرنه همه‌ی فایل‌های عنوان (مانگا/کتاب/فشرده)
        allf = [f for s in (self._sections or []) for f in s["files"]]
        vids = [f for f in allf if f.suffix.lower() in VIDEO_EXTS]
        pool = vids if vids else allf
        total = len(pool)
        done = part = 0
        for f in pool:
            m, _p = self.store.watch_get(self.item, rel_of(self.item, f))
            if m == Store.WATCH_DONE:
                done += 1
            elif m == Store.WATCH_PART:
                part += 1
        if not total or (not done and not part):
            self.chip_watch.hide()
            self.watch_bar.hide()
            return
        done_lbl, part_lbl, act_lbl = self._pool_labels(bool(vids))
        txt = f"👁 {fa(done)}/{fa(total)} {done_lbl}"
        if part:
            txt += f" · ◐ {fa(part)} {part_lbl}"
        self.chip_watch.setText(txt)
        self.chip_watch.setToolTip(
            T("پیشرفت {0} — با راست‌کلیک روی هر فایل علامت بزن").format(act_lbl))
        self.chip_watch.show()
        pct = int(round(100 * (done + 0.5 * part) / max(1, total)))
        self.watch_bar.setRange(0, 100)
        self.watch_bar.setValue(pct)
        self.watch_bar.setToolTip(T("{0}٪ {1}").format(fa(pct), done_lbl))
        self.watch_bar.show()

    def _apply_watch(self, owner, rel: str, mode: int, pos: float):
        """افکت بصری وضعیت روی آیتم درخت یا کارت."""
        try:
            if isinstance(owner, ThumbCard):
                owner.set_watch(mode, pos)
            elif isinstance(owner, QTreeWidgetItem):
                name_txt = owner.data(0, Qt.ItemDataRole.UserRole + 1) or owner.text(0)
                if mode == Store.WATCH_DONE:
                    owner.setForeground(0, QBrush(DONE_COLOR))
                    if "✓" not in owner.text(0):
                        owner.setText(0, "✓ " + str(name_txt))
                elif mode == Store.WATCH_PART:
                    owner.setForeground(0, QBrush(PART_COLOR))
                    if "◐" not in owner.text(0):
                        owner.setText(0, "◐ " + str(name_txt))
                else:
                    owner.setForeground(0, QBrush())
                    owner.setText(0, str(name_txt))
                data = owner.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == "file":
                    fpath = Path(str(data[1]))
                    done_lbl, part_lbl, _act_lbl, resume_ok = self._status_labels(fpath)
                    if mode == Store.WATCH_PART and pos and resume_ok:
                        tip = (T("\n◐ تا {0} {1} — رزومه: {2}").format(format_duration(pos), part_lbl, format_duration_short(pos)))
                    elif mode == Store.WATCH_PART and pos and not resume_ok:
                        # شماره صفحه برای فایل‌های غیرویدیویی (کتاب/مانگا)
                        tip = T("\n◐ {0} — تا صفحه‌ی {1}").format(part_lbl, fa(int(pos)))
                    elif mode == Store.WATCH_PART:
                        tip = f"\n◐ {part_lbl}"
                    elif mode == Store.WATCH_DONE:
                        tip = f"\n✓ {done_lbl}"
                    else:
                        tip = ""
                    owner.setToolTip(0, str(data[1]) + tip)
        except RuntimeError:
            pass

    def _refresh_watch_rows(self, rel: str):
        if self.item is None:
            return
        mode, pos = self.store.watch_get(self.item, rel)
        for owner in self._watch_rows.get(rel, []):
            self._apply_watch(owner, rel, mode, pos)

    def _after_watch_change(self, rels):
        for rel in rels:
            self._refresh_watch_rows(rel)
        self._refresh_watch_ui()
        self.changed.emit()

    # ------------------------------------------------------------ منوی راست‌کلیک تماشا
    def _watch_menu(self, f: Path, gpos, owners_rels: list[str] | None = None):
        rel = rel_of(self.item, f)
        mode, pos = self.store.watch_get(self.item, rel)
        done_lbl, part_lbl, _act_lbl, resume_ok = self._status_labels(f)
        menu = QMenu(self)
        a_play = menu.addAction(T("▶ پخش") + (T(" (ادامه از ") + format_duration_short(pos) + ")"
                                           if mode == Store.WATCH_PART and pos else ""))
        menu.addSeparator()
        a_done = menu.addAction(T("✓ علامت «{0}»").format(done_lbl) if mode != Store.WATCH_DONE
                                else T("✓ {0} — برداشتن علامت").format(done_lbl))
        if resume_ok:
            a_part = menu.addAction(T("◐ {0} — ثبت موقعیت…").format(part_lbl))
        else:
            a_part = menu.addAction(T("◐ علامت «{0}»").format(part_lbl) if mode != Store.WATCH_PART
                                    else T("◐ {0} — برداشتن علامت").format(part_lbl))
        if mode or pos:
            a_clear = menu.addAction(T("↺ پاک کردن وضعیت"))
        else:
            a_clear = None
        act = menu.exec(gpos)
        menu.deleteLater()   # (round 19 §19) تجمعِ QObject در نشستِ بلند — آزادسازیِ قطعی
        if act is a_play:
            self._play(f)
        elif act is a_done:
            to_clear = mode == Store.WATCH_DONE
            if owners_rels:
                for r in owners_rels:
                    self.store.watch_set(self.item, r, 0 if to_clear else Store.WATCH_DONE, 0)
                self._after_watch_change(owners_rels)
            else:
                self.store.watch_set(self.item, rel, 0 if to_clear else Store.WATCH_DONE, 0)
                self._after_watch_change([rel])
        elif act is a_part:
            if not resume_ok:
                self.store.watch_set(self.item, rel,
                                     0 if mode == Store.WATCH_PART else Store.WATCH_PART, 0)
                self._after_watch_change([rel])
                return
            val, ok = QInputDialog.getInt(
                self, T("موقعیت تماشا"),
                T("تا کجای «{0}» دیده‌ای؟ (به دقیقه)").format(f.name),
                value=int(pos // 60) if pos else 0, minValue=0, maxValue=10000)
            if ok:
                self.store.watch_set(self.item, rel, Store.WATCH_PART, val * 60.0)
                self._after_watch_change([rel])
        elif a_clear is not None and act is a_clear:
            self.store.watch_set(self.item, rel, 0, 0)
            self._after_watch_change([rel])

    def _doc_status_actions(self, f: Path):
        """ساخت منوی فایل غیرویدیو → (menu, actions) — جدا از exec تا انتخاب‌ها
        (متن/ترتیب گزینه‌ها) بدون باز کردن واقعی منو هم قابل بررسی باشند.
        برچسب‌ها از الگوی وضعیت دسته می‌آیند (خوانده/انجام/…)."""
        rel = rel_of(self.item, f)
        mode, _pos = self.store.watch_get(self.item, rel)
        done_lbl, part_lbl, _act_lbl, _resume = self._status_labels(f)
        menu = QMenu(self)
        acts = {
            "open": menu.addAction(T("▶ باز کردن")),
            "folder": menu.addAction(T("📂 نمایش در پوشه")),
        }
        menu.addSeparator()
        acts["done"] = menu.addAction(
            T("✓ علامت «{0}»").format(done_lbl) if mode != Store.WATCH_DONE
            else T("✓ {0} — برداشتن علامت").format(done_lbl))
        acts["part"] = menu.addAction(
            T("◐ {0} — تا صفحه‌ی {1}").format(part_lbl, fa(int(_pos)))
            if mode == Store.WATCH_PART and _pos else
            (T("◐ {0} — برداشتن علامت").format(part_lbl) if mode == Store.WATCH_PART
             else T("◐ {0}…").format(part_lbl)))
        return menu, acts

    def _doc_status_choose(self, f: Path, acts: dict, act):
        """اعمال انتخاب منوی وضعیت (خوانده‌شده/نیمه‌خوانده/بازکردن) روی فایل.
        0.50.0: نیمه‌خوانده → دیالوگ ثبت شماره صفحه."""
        if act is None:
            return
        rel = rel_of(self.item, f)
        mode, _pos = self.store.watch_get(self.item, rel)
        if act is acts["open"]:
            self._play(f)  # از بازکننده‌ی دسته استفاده کن
            return
        elif act is acts["folder"]:
            open_path(f.parent)
        elif act is acts["done"]:
            self.store.watch_set(self.item, rel,
                                 0 if mode == Store.WATCH_DONE else Store.WATCH_DONE, 0)
            self._after_watch_change([rel])
        elif act is acts["part"]:
            if mode == Store.WATCH_PART:
                # برداشتن علامت
                self.store.watch_set(self.item, rel, 0, 0)
                self._after_watch_change([rel])
            else:
                # ثبت شماره صفحه برای نیمه‌خوانده
                from PySide6.QtWidgets import QInputDialog
                page, ok = QInputDialog.getInt(
                    self, T("ثبت پیشرفت مطالعه"),
                    T("تا چه صفحه‌ای خوانده‌اید؟ (۰ = فقط علامت نیمه‌تمام)"),
                    value=int(_pos) if _pos else 0, minValue=0, maxValue=99999)
                if ok:
                    self.store.watch_set(self.item, rel, Store.WATCH_PART, float(page))
                    self._after_watch_change([rel])

    def _doc_status_menu(self, f: Path, gpos):
        """منوی راست‌کلیک فایل‌های غیرویدیویی (PDF/فشرده/تصویر…): باز کردن +
        علامت «✓ خوانده‌شده / ◐ نیمه‌خوانده» — درخواست کاربر برای مانگا و کتاب."""
        menu, acts = self._doc_status_actions(f)
        self._doc_status_choose(f, acts, menu.exec(gpos))
        menu.deleteLater()   # (round 19 §19)

    def _dir_watch_menu(self, dir_path: Path, gpos):
        """منوی پوشه: علامت‌گذاری گروهی — ویدیوها اگر هستند، وگرنه همه‌ی فایل‌ها.
        برچسب از الگوی وضعیت دسته می‌آید («✓ همه دیده/خوانده شد» یا برچسب سفارشی)."""
        base = Path(dir_path)
        videos, others = [], []
        if base.is_dir():
            for p in base.rglob("*"):
                if not p.is_file() or p.name.startswith("."):
                    continue
                (videos if p.suffix.lower() in VIDEO_EXTS else others).append(p)
        targets = videos if videos else others
        done_lbl, _part_lbl, _act_lbl = self._pool_labels(bool(videos))
        menu = QMenu(self)
        a_open = menu.addAction(T("📂 باز کردن پوشه"))
        a_done = menu.addAction(T("✓ همه‌ی این بخش {0}").format(done_lbl))
        a_clear = menu.addAction(T("↺ پاک کردن وضعیت این بخش"))
        act = menu.exec(gpos)
        menu.deleteLater()   # (round 19 §19) تجمعِ QObject در نشستِ بلند — آزادسازیِ قطعی
        if act is a_open:
            open_path(dir_path)
            return
        if act not in (a_done, a_clear):
            return
        rels = [rel_of(self.item, v) for v in targets]
        for r in rels:
            self.store.watch_set(self.item, r, Store.WATCH_DONE if act is a_done else 0, 0)
        self._after_watch_change(rels)

    # ------------------------------------------------------------ پخش ردیابی‌شده
    @staticmethod
    def _opener_is_safe(opener: str) -> bool:
        """اعتبارسنجیِ مسیرِ بازکننده (امنیت انتشار عمومی).

        مسیرِ ذخیره‌شده در config.json باید یک *فایلِ موجودِ مطلق* با پسوندِ
        اجراییِ ویندوز (.exe/.bat/.cmd/.com) باشد؛ هرگز shell=True نیست و
        نام‌های خامِ قابل‌حل از PATH (مثل cmd یا powershell) قبول نمی‌شوند تا
        یک config.json دست‌کاری‌شده/دریافتی نتواند به اجرای مفسرها بینجامد.
        بازکننده‌های واقعی همیشه از دیالوگِ انتخابِ فایل با مسیرِ مطلق می‌آیند
        پس این بررسی هیچ تنظیمِ سالمی را نمی‌شکند."""
        try:
            from pathlib import Path as _P
            p = _P(str(opener or "").strip())
            if not p.is_absolute() or not p.is_file():
                return False
            if p.suffix.lower() not in (".exe", ".bat", ".cmd", ".com"):
                return False
            return True
        except Exception as _exc_dbg:
            dbg_log(f"_opener_is_safe failed: {type(_exc_dbg).__name__}")
            return False

    def _play(self, f: Path):
        """پخش فایل — نظارت و ثبت نتیجه در پنجره‌ی اصلی (PlayTracker) انجام می‌شود.
        0.49.0: برای فایل‌های غیرویدیویی از برنامه‌ی بازکننده‌ی دسته استفاده می‌کند."""
        if f.suffix.lower() not in VIDEO_EXTS:
            # اگر برای این دسته و پسوند برنامه‌ی بازکننده مشخص شده، با آن باز کن
            if self.item is not None:
                cat = self.store.category_of(self.item)
                opener = self.store.cat_opener_for(cat, f.suffix)
                if opener:
                    if not self._opener_is_safe(opener):
                        # امنیت: openerِ نامعتبر — به‌جای اجرا، لاگ کن و با
                        # بازکننده‌ی پیش‌فرضِ ویندوز ادامه بده (fallback ساکت نباشد)
                        try:
                            from watchtime import _dbg, safe_ref
                            _dbg(f"cat opener rejected by validation: "
                                 f"{safe_ref(opener)} — falling back to shell open")
                        except Exception as _exc_dbg:
                            dbg_log(f"_play failed: {type(_exc_dbg).__name__}")
                            pass
                    else:
                        import subprocess as _sp49
                        try:
                            _sp49.Popen([opener, str(f)])
                            return
                        except Exception as e:
                            try:
                                from watchtime import _dbg, safe_ref
                                _dbg(f"cat opener launch failed: {safe_ref(opener)}: "
                                     f"{type(e).__name__}")
                            except Exception as _exc_dbg:
                                dbg_log(f"_play failed: {type(_exc_dbg).__name__}")
                                pass
            open_path(f)
            return
        self.playRequested.emit(self.item, f)

    def refresh_watch(self, rel: str):
        """به‌روزرسانی UI پس از تغییر وضعیت تماشای یک فایل (از ردیاب مرکزی)."""
        self._refresh_watch_rows(rel)
        self._refresh_watch_ui()

    def _set_status_hint(self, msg: str):
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage(msg, 6000)

    # ------------------------------------------------------------ پخش هوشمند
    def _best_play_target(self) -> Path | None:
        """هدف پخش: نیمه‌تمام با بیشترین موقعیت، نکرده اولینِ ندیده، نه اولین ویدیو.
        0.49.0: برای پوشه‌های بدون ویدیو (کتاب/مانگا/تصویر) اولین فایل ندیده یا اولین
        فایل قابل‌مصرف باز می‌شود."""
        videos = [f for s in (self._sections or []) for f in s["files"]
                  if f.suffix.lower() in VIDEO_EXTS]
        # اگر ویدیو نیست، برای فایل‌های قابل‌مصرف (کتاب/مانگا/فشرده/تصویر) امتحان کن
        if not videos:
            from scanner import item_watchables
            watchable = item_watchables(self.item)
            if not watchable:
                p = Path(self.item.path)
                return p if p.is_file() else None
            # اول نیمه‌تمام‌ها (با بیشترین موقعیت)
            partials = []
            for f in watchable:
                mode, pos = self.store.watch_get(self.item, rel_of(self.item, f))
                if mode == Store.WATCH_PART:
                    partials.append((pos, f))
            if partials:
                return max(partials, key=lambda t: t[0])[1]
            # اولین ندیده
            for f in watchable:
                mode, _ = self.store.watch_get(self.item, rel_of(self.item, f))
                if mode != Store.WATCH_DONE:
                    return f
            # اولین فایل
            return watchable[0]
        partials = []
        for f in videos:
            mode, pos = self.store.watch_get(self.item, rel_of(self.item, f))
            if mode == Store.WATCH_PART:
                partials.append((pos, f))
        if partials:
            return max(partials, key=lambda t: t[0])[1]
        for f in videos:
            mode, _ = self.store.watch_get(self.item, rel_of(self.item, f))
            if mode != Store.WATCH_DONE:
                return f
        return videos[0]

    def play_smart(self):
        if self.item is None:
            return
        target = self._best_play_target()
        if target is not None:
            self._play(target)

    # ------------------------------------------------------------ درخت
    def _build_tree(self):
        self.tree.clear()
        if self._hier is None and self.item is not None:
            # ساخت تنبلی سلسله‌مراتب — فقط وقتی واقعاً حالت درختی لازم شد
            try:
                _p, _ex, _lo, _only = getattr(self, "_hier_params", None) or (
                    self.item.path, frozenset(), getattr(self.item, "loose_group", False), None)
                self._hier = get_file_hierarchy(_p, exclude=set(_ex), loose=_lo,
                                                only_exts=_only)
            except Exception as _exc_dbg:
                dbg_log(f"_build_tree failed: {type(_exc_dbg).__name__}")
                self._hier = None
        style = QApplication.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_fallback = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        def add_children(parent_item, node):
            for d in node["dirs"]:
                it = QTreeWidgetItem([d["path"].name, ""])
                it.setIcon(0, dir_icon)
                it.setData(0, Qt.ItemDataRole.UserRole, ("dir", str(d["path"])))
                parent_item.addChild(it)
                add_children(it, d)
            for f in self._sorted(node["files"]):   # همان معیار گرید — درخت هم مرتب
                try:
                    size = human_size(f.lstat().st_size)
                except OSError:
                    size = ""
                it = QTreeWidgetItem([f.name, size, ""])
                it.setData(0, Qt.ItemDataRole.UserRole + 1, f.name)  # نام خام برای بازنشانی وضعیت
                pm = self._thumb_px_or_queue(f, 26, it)
                it.setIcon(0, QIcon(pm.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                           if pm else file_fallback)
                it.setData(0, Qt.ItemDataRole.UserRole, ("file", str(f)))
                it.setToolTip(0, str(f))
                if f.suffix.lower() in VIDEO_EXTS:
                    self._dur_lookup(f, it)
                if self._track_on:
                    # وضعیت برای همه‌ی نوع فایل — ویدیو «دیده»، PDF/فشرده «خوانده»
                    rel = rel_of(self.item, f)
                    self._watch_rows.setdefault(rel, []).append(it)
                    mode, pos = self.store.watch_get(self.item, rel)
                    if mode:
                        self._apply_watch(it, rel, mode, pos)
                parent_item.addChild(it)

        if self._hier:
            root_item = self.tree.invisibleRootItem()
            if self.item.path.is_file():
                node = {"path": self.item.path.parent, "dirs": [], "files": [self.item.path]}
            else:
                node = self._hier
            add_children(root_item, node)
            self.tree.expandToDepth(0)

    def _tree_dblclick(self, it: QTreeWidgetItem, _col):
        kind, p = it.data(0, Qt.ItemDataRole.UserRole) or (None, None)
        if kind == "dir":
            it.setExpanded(not it.isExpanded())
        elif kind == "file":
            self._play(Path(p))

    def _tree_ctx_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        kind, p = it.data(0, Qt.ItemDataRole.UserRole) or (None, None)
        gpos = self.tree.viewport().mapToGlobal(pos)
        if kind == "file":
            f = Path(p)
            ext = f.suffix.lower()
            if not self._track_on:
                self._generic_file_menu(f, gpos)
            elif ext in VIDEO_EXTS:
                self._watch_menu(f, gpos)
            else:
                self._doc_status_menu(f, gpos)
        elif kind == "dir":
            if self._track_on:
                self._dir_watch_menu(Path(p), gpos)
            else:
                menu = QMenu(self)
                a_open = menu.addAction(T("📂 باز کردن پوشه"))
                _act = menu.exec(gpos)
                menu.deleteLater()   # (round 19 §19)
                if _act is a_open:
                    open_path(p)

    # ------------------------------------------------------------ بندانگشتی
    def _build_grid(self):
        clear_layout(self.grid_layout)
        self._grid_gen = getattr(self, "_grid_gen", 0) + 1
        if not self._sections:
            empty = QLabel(T("فایلی در این پوشه نیست."))
            empty.setObjectName("MutedLabel")
            empty.setStyleSheet("padding:20px;")
            self.grid_layout.addWidget(empty)
            return
        # گزارش کاربر: «باید صبر کنیم همه ساخته بشن و بعد یکدفعه
        # ظاهر می‌شن؛ با برگشت هم باز از اول صبر» — دیگر ساختِ دسته‌ایِ کارت‌ها در
        # کار نیست! هر سکشن یک کانتینرِ «مجازی» است که ارتفاعش را از همان لحظه
        # تحلیلی می‌داند؛ فقط ردیف‌های نزدیکِ دید (± دو صفحه) واقعاً کارت می‌شوند و
        # با دورشدن هرس/آزاد می‌شوند. ورود به پوشه‌ی ۲هزارفایلی یعنی چند هدر +
        # چند ده کارت — کسری از ثانیه.
        self._vsections = []
        for section in self._sections:
            btn, container = self._add_grid_section(section["title"], section["files"])
            # ارتفاع واقعی هدر سکشن برای محاسبه‌ی هندسیِ دید (fallback داخل _grid_metrics)
            _h = btn.sizeHint().height()
            if _h > 4:
                self._grid_hdr_h = _h
            self._vsections.append(container)
        self.grid_layout.addStretch(1)
        # اولین واقعی‌سازی: بلافاصله (بدون انتظارِ رویداد نقاشی) و یک‌بار هم بعد از
        # نشستِ چیدمان — هر دو ارزان‌اند چون فقط پنجره‌ی دید واقعی می‌شود.
        self._grid_sync_visible()
        QTimer.singleShot(0, self._grid_sync_visible)

    def _grid_sync_visible(self):
        """واقعی‌سازی پنجره‌ی دید برای همه‌ی سکشن‌های مجازی (0.24.0).

        با اسکرول، تغییر اندازه و بعد از ساخت/بازبستن سکشن صدا زده می‌شود؛ برای
        هر سکشن، متقاطعِ «دید ± دو صفحه» را به مختصاتِ محلی ترجمه و به خودِ
        سکشن می‌سپارد (_VSection.sync_window هرس/ساخت را انجام می‌دهد).
        """
        sects = getattr(self, "_vsections", None) or []
        if getattr(self, "_mode", "grid") != "grid" or not sects:
            return
        try:
            vh = max(1, self.grid_scroll.viewport().height())
            y0 = self.grid_scroll.verticalScrollBar().value()
        except Exception as _exc_dbg:
            dbg_log(f"_grid_sync_visible failed: {type(_exc_dbg).__name__}")
            return
        lo, hi = y0 - vh, y0 + 2 * vh          # دید + دو صفحه پایین + یک صفحه بالا
        for sec in sects:
            try:
                if not sec.isVisible():
                    continue                       # سکشنِ بسته‌شده — چیزی نساز
                top = sec.mapTo(self.grid_container, QPoint(0, 0)).y()
            except RuntimeError:
                continue                           # ویجت حذف شده
            if top + sec.height() < lo or top > hi:
                sec.sync_window(-(10 ** 9), -(10 ** 9) + 1)   # سکشن کاملاً دور → هرس
                continue
            sec.sync_window(lo - top, hi - top)

    def _make_grid_card(self, f: Path) -> ThumbCard:
        try:
            size_text = human_size(f.lstat().st_size)
        except OSError:
            size_text = ""
        card = ThumbCard(f, placeholder_poster(f.suffix.lstrip(".")[:1] or T("؟"),
                                               "#33507c", 132, 132, 10), size_text)
        card.rel = rel_of(self.item, f)

        def _menu(c, gp):
            ext = c.path.suffix.lower()
            if not self._track_on:
                self._generic_file_menu(c.path, gp)
            elif ext in VIDEO_EXTS:
                self._watch_menu(c.path, gp)
            else:
                self._doc_status_menu(c.path, gp)

        card.watchMenuRequested.connect(_menu)
        card.playRequested.connect(self._play)
        pm = self._thumb_px_or_queue(f, 132, card)
        if pm is not None:
            card.set_thumb(rounded_pixmap(pm, 132, 132, 10))
        if f.suffix.lower() in VIDEO_EXTS:
            # مقدار کش‌شده اعمال می‌شود؛ اما محاسبه‌ی مدت فقط برای فایل‌های
            # «در دید» صف می‌شود — قبلاً ساختِ هر کارتِ ویدیو بلافاصله یک ffprobe
            # به صف می‌افکند و برای پوشه‌ی چندهزارفایلی یعنی هزاران subprocess
            # (گزارش کاربر: «ورود به پوشه‌ی شلوغ برنامه را کند/هنگ می‌کرد»).
            self._dur_apply_or_wait(f, card)
        if self._track_on:
            row = self._watch_rows.setdefault(card.rel, [])
            # نسخه‌های هرس‌شده‌ی این کارت (مجازی‌سازی) حذف شده‌اند — کنارشان
            row[:] = [w for w in row if not _widget_dead(w)]
            row.append(card)
            mode, pos = self.store.watch_get(self.item, card.rel)
            if mode:
                self._apply_watch(card, card.rel, mode, pos)
        return card

    def _add_grid_section(self, title: str, files: list):
        btn = QPushButton(T("▼  {0}   ·   {1} فایل").format(title, fa(len(files))))
        btn.setObjectName("SectionToggle")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # ریشه‌ی «پوشه‌ی خاصِ قفل‌شده روی ۷ آیکون در سطر»: متنِ بلندِ عنوان،
        # حداقل‌عرضِ دکمه را بزرگ می‌کرد و از راه layout حداقل‌عرضِ کلِ کانتینر را
        # سفت می‌کرد تا هیچ‌وقت باریک‌تر نشود — سیاستِ Ignored یعنی عرضِ دکمه
        # دیگر حداقلِ قفل‌کننده نیست (متن هر چقدر بلند باشد).
        btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        btn.setMinimumWidth(0)
        self.grid_layout.addWidget(btn)

        # سکشنِ مجازی — دیگر هیچ batch ساخت کارتی در کار نیست؛ سکشن
        # ارتفاعش را تحلیلی می‌داند و فقط پنجره‌ی نزدیکِ دید را ویجت می‌سازد
        try:
            hint_w = max(120, self.grid_scroll.viewport().width() - 10)
        except Exception as _exc_dbg:
            dbg_log(f"_add_grid_section failed: {type(_exc_dbg).__name__}")
            hint_w = 0
        container = _VSection(files, self._make_grid_card,
                              notify=self._grid_sync_visible, width_hint=hint_w)
        self.grid_layout.addWidget(container)

        btn.clicked.connect(lambda _c=False, b=btn, w=container, t=title, count=len(files):  # noqa: B008 — snap-shot عمدی
                            self._toggle_section(b, w, t, count))
        return btn, container

    def _toggle_section(self, btn, container, title, count):
        vis = container.isVisible()
        container.setVisible(not vis)
        btn.setText(T("{0}  {1}   ·   {2} فایل").format('▼' if not vis else '▶', title, fa(count)))
        QTimer.singleShot(0, self._grid_sync_visible)   # سکشنِ بازشده پنجره‌اش ساخته شود

    def _generic_file_menu(self, f: Path, gpos):
        menu = QMenu(self)
        a_open = menu.addAction(T("▶ باز کردن"))
        a_folder = menu.addAction(T("📂 نمایش در پوشه"))
        act = menu.exec(gpos)
        menu.deleteLater()   # (round 19 §19) تجمعِ QObject در نشستِ بلند — آزادسازیِ قطعی
        if act is a_open:
            open_path(f)
        elif act is a_folder:
            open_path(f.parent)

    # ------------------------------------------------------------ اکشن‌ها
    def _open_folder(self):
        if self.item:
            p = self.item.path if self.item.path.is_dir() else self.item.path.parent
            open_path(p)

    # ------------------------------------------------------------ باکس دسته در هدر
    def _fill_hero_cat_combo(self):
        """بجِ دسته‌ی هدر = خودِ باکس دسته (0.48.0 — بازبینی کاربر «با اسم دسته یکیش
        کن»). متنش نامِ دسته‌ی جاری است و رنگش از رنگِ دسته می‌آید؛ با تعویض
        دسته هر دو عوض می‌شوند. اگر زیردسته‌ای برای تعویض نباشد، همان نام را
        تک‌گزینه‌ای نشان می‌دهد تا چیدمانِ هدر ثابت بماند."""
        st = self.store
        cur = st.category_of(self.item) if self.item else ""
        view = st.cat_switch_view(self.item) if self.item else None
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        if view:
            labels = view.get("labels") or {}
            for full in view["options"]:
                self.cat_combo.addItem(labels.get(full, full), full)
            idx = self.cat_combo.findData(view["current"])
            self.cat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        elif cur:
            self.cat_combo.addItem(cur, cur)       # فقط نمایش — چیزی برای تعویض نیست
        self.cat_combo.blockSignals(False)
        if cur:
            from i18n import disp_cat as _dcat45
            self.cat_combo.setToolTip(
                T("دسته: {0} — کلیک برای تعویض").format(_dcat45(cur).replace("/", " › "))
                if view else T("دسته: {0}").format(_dcat45(cur).replace("/", " › ")))
            self.cat_combo.show()
            # نام دسته در هدر (فقط نمایش)
            self.cat_name_lbl.setText(_dcat45(cur).split("/")[-1])
            self.cat_name_lbl.show()
        else:
            self.cat_combo.hide()
            self.cat_name_lbl.hide()

    def _on_hero_cat(self, i: int):
        if not self.item:
            return
        data = self.cat_combo.itemData(i)
        if not data or data == self.store.category_of(self.item):
            return
        self.store.set_category(self.item.path, data)
        # (round 19 §۱۸) خودِ صفحه هم همگام شود — الگوی ترکینگ/برچسب‌های ردیف‌ها/
        # دکمهٔ «همه دیده‌شده» از الگوی دستهٔ جدید می‌آیند؛ قبلاً فقط Library
        # خبردار می‌شد و هدر تا بازگشتِ بعدی کهنه می‌ماند.
        try:
            self._profile = self.store.track_profile(data)
            self._track_on = bool(self._profile.get("enabled", True))
            self._fill_hero_cat_combo()
            self._refresh_watch_ui()
            self._refresh_watch_all_btn()
        except Exception as _exc_dbg:
            dbg_log(f"_on_hero_cat refresh failed: {type(_exc_dbg).__name__}")
        self.changed.emit()                      # کتابخانه بازسازی می‌شود
        from i18n import disp_cat as _dcat45
        self._set_status_hint(T("📁 دسته → {0}").format(_dcat45(data).replace('/', ' › ')))

    # ------------------------------------------------ دکمه‌ی «کلِ عنوان دیده‌شده»
    def _refresh_watch_all_btn(self):
        """همگام‌سازی دکمه‌ی «✓ همه دیده‌شده ↔ برداشتن» با وضعیتِ فعلیِ عنوان."""
        btn = getattr(self, "btn_watch_all", None)
        if btn is None:
            return
        if self.item is None or not getattr(self, "_track_on", True):
            btn.hide()
            return
        try:
            total = self.store.watchable_total(self.item)
        except Exception as _exc_dbg:
            dbg_log(f"_refresh_watch_all_btn failed: {type(_exc_dbg).__name__}")
            total = len(item_watchables(self.item))
        if not total:
            btn.hide()
            return
        done, _part = self.store.watch_progress(self.item)
        all_done = done >= total > 0
        done_lbl, _pl, _al = self._pool_labels(bool(self.item.video_count))
        if all_done:
            btn.setText(T("↺ برداشتن علامت «همه {0}»").format(done_lbl))
            btn.setToolTip(
                T("پاک‌کردن علامتِ تماشا از هر {0} فایلِ قابل‌مصرفِ این عنوان").format(fa(total)))
        else:
            btn.setText(T("✓ همه {0}").format(done_lbl))
            btn.setToolTip(
                T("علامت‌گذاری یک‌جای هر {0} فایلِ قابل‌مصرف به‌عنوان «{1}» — "
                  "برای مواردی که از قبل دیده‌ای؛ دیگر لازم نیست دونه‌دونه بزنی")
                .format(fa(total), done_lbl))
        btn.show()

    def _toggle_watch_all(self):
        """کلیک روی دکمه: کلِ عنوان دیده‌شده ↔ برداشتنِ علامت — یک‌جا و سریع
        (0.48.0 — درخواست کاربر: «فیلم‌هایی که از قبل دیده سریع‌تر بزنه تماشا شده»)."""
        if self.item is None:
            return
        files = item_watchables(self.item)
        if not files:
            self._set_status_hint(T("فایلِ قابل‌مصرفی (ویدیو/کتاب/فشرده/…) در این عنوان نیست"))
            return
        done, _part = self.store.watch_progress(self.item)
        make = not (len(files) > 0 and done >= len(files))
        rels = []
        for f in files:
            r = rel_of(self.item, f)
            self.store.watch_set(self.item, r, Store.WATCH_DONE if make else 0, 0)
            rels.append(r)
        self._after_watch_change(rels)           # ردیف‌ها + چیپ پیشرفت + سیگنال کتابخانه
        self._refresh_watch_all_btn()
        done_lbl, _pl, _al = self._pool_labels(bool(self.item.video_count))
        self._set_status_hint(
            T("✓ هر {0} فایلِ «{1}» {2} علامت خورد").format(
                fa(len(files)), self.store.title(self.item), done_lbl)
            if make else
            T("○ علامتِ تماشای «{0}» پاک شد").format(self.store.title(self.item)))

    def _refresh_fav_btn(self):
        fav = self.store.fav(self.item.path)
        self.btn_fav.setText(T("★ حذف از علاقه‌مندی‌ها") if fav else T("☆ افزودن به علاقه‌مندی‌ها"))

    def _refresh_desc(self):
        desc = self.store.desc(self.item.path)
        if desc:
            self.desc_label.setText(desc)
            self.desc_label.setObjectName("DescBox")
            self.desc_label.setStyleSheet("")
        else:
            self.desc_label.setText(T("توضیحاتی ثبت نشده — از «ویرایش اطلاعات» یا دکمه‌ی ✏ اضافه کن."))
            self.desc_label.setObjectName("DescEmpty")
            self.desc_label.setStyleSheet(
                "background:rgba(128,128,128,0.07); border:1px dashed rgba(128,128,128,0.35);"
                "border-radius:12px; padding:10px 12px;"
            )

    def _toggle_fav(self):
        self.store.set_meta(self.item.path, favorite=not self.store.fav(self.item.path))
        self._refresh_fav_btn()
        self.changed.emit()

    # امتیاز شخصی ⭐
    def _refresh_rate_combo(self):
        if getattr(self, "rate_combo", None) is None or self.item is None:
            return
        _v48 = self.store.rating(self.item.path)
        _i48 = self.rate_combo.findData(_v48)
        self.rate_combo.blockSignals(True)
        self.rate_combo.setCurrentIndex(_i48 if _i48 >= 0 else 0)
        self.rate_combo.blockSignals(False)

    def _on_rate_change(self, _i):
        if self.item is None:
            return
        _v48 = int(self.rate_combo.currentData() or 0)
        self.store.set_meta(self.item.path, rating=_v48 if _v48 else None)  # None ← پاک‌کردن کلید
        self.changed.emit()                     # کارت‌ها نشانِ ستاره را تازه کنند

    def _copy_path(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(str(self.item.path))

    def _edit_dialog(self):
        dlg = ItemEditDialog(self.item, self.store, self)
        # (round 19 §۵) کاور از دیالوگِ ویرایش فوری اعمال می‌شود؛ Cancel هم باید
        # صفحه را تازه کند — بدونِ این، متا جدید است ولی کاور/هدرِ کهنه می‌ماند.
        try:
            dlg.coverTouched.connect(self._on_edit_cover_touch)
        except Exception as _exc_dbg:
            dbg_log(f"_edit_dialog coverTouched failed: {type(_exc_dbg).__name__}")
        if dlg.exec():
            self.set_item(self.item)
            self.changed.emit()

    def _on_edit_cover_touch(self, item):
        """کاور از دیالوگِ ویرایش تغییر کرد (Save/Cancel بی‌تفاوت) — همان مسیرِ C1."""
        if item is None or self.item is None \
                or str(item.path) != str(self.item.path):
            return
        self._stop_poster_movie()
        self._tree_cache.clear()
        snap = self._view_state_snapshot()
        self.set_item(self.item)
        self._restore_view_state(snap)
        self.coverChanged.emit(self.item)      # کارتِ کتابخانه هم تازه شود (C1)

    def _edit_desc(self):
        self.desc_editor.setPlainText(self.store.desc(self.item.path))
        self.desc_label.hide()
        self.btn_desc_edit.hide()
        self.desc_editor.show()
        self.desc_btns.show()
        self.desc_editor.setFocus()

    def _save_desc(self):
        self.store.set_meta(self.item.path, description=self.desc_editor.toPlainText().strip() or None)
        self._end_desc_edit()
        self.changed.emit()

    def _end_desc_edit(self):
        self.desc_editor.hide()
        self.desc_btns.hide()
        self.desc_label.show()
        self.btn_desc_edit.show()
        self._refresh_desc()
