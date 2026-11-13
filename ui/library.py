# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""صفحه‌ی اصلی کتابخانه: سایدبار (جستجو، مرتب‌سازی، درخت دسته‌ها) + شبکه‌ی کارت‌ها.

زیردسته‌بندی: دسته‌ها می‌توانند با «/» تودرتو باشند (مثل «انیمه/تماشا‌شده»).
انتخاب یک والد، همه‌ی زیردسته‌هایش را هم شامل می‌شود.
"""
from __future__ import annotations

from i18n import T

import os
import time
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import (
    QMutex, QMutexLocker, QThread, Qt, QTimer, QWaitCondition, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from .dialogs import NoWheelComboBox

from media_info import format_duration, format_duration_short
from scanner import fa, fa_norm, natural_key, TYPE_ORDER

# رتبه‌ی نوع غالب محتوا برای گزینه‌ی مرتب‌سازی «نوع محتوا» (نسخه‌ی 0.23.0)
_TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}
from store import Store
from thumbs import ThumbnailCache
from qss import THEMES, theme_label
from filethumbs import (
    FileThumbs, load_cache_pixmap, load_cache_qimage, poster_png_fallback,
)
from .widgets import (
    CARD_H, CARD_W, CardWidget, FlowLayout, open_path,
    placeholder_poster, poster_letterbox_image, poster_needs_letterbox,
    poster_pixmap, POSTER_H, POSTER_W,
)
from watchtime import dbg_log

SORTS = ["نام", "نوع محتوا", "مدت زمان", "حجم", "تعداد قسمت‌ها", "تعداد فایل‌ها", "تاریخ تغییر", "امتیاز"]   # امتیاز شخصی
ALL_KEY, FAV_KEY = "__all__", "__fav__"

POSTER_RADIUS = 12


def _poster_dbg(msg: str):
    """ثبت خطاهای ساخت پوستر در لاگ دیباگ — برای ردیابی موارد «تامبنیل نیومد»."""
    try:
        from watchtime import _dbg
        _dbg(msg)
    except Exception as _exc_dbg:
        dbg_log(f"_poster_dbg failed: {type(_exc_dbg).__name__}")
        pass


def safe_ref(p) -> str:
    """ارجاع بی‌نام برای لاگ (0.30.0) — نمایه‌ی watchtime."""
    try:
        from watchtime import safe_ref as _sr
        return _sr(p)
    except Exception as _exc_dbg:
        dbg_log(f"safe_ref failed: {type(_exc_dbg).__name__}")
        return ".#?"


def _job_rank(job) -> int:
    """سبک‌به‌سنگین برای صف اولیه‌ی پوستر: آیکون/تصویر ← ویدیو ← PDF ← فشرده.
    (راهنما + گزارش کاربر: پوشه‌های فقط‌فشرده همیشه ته صف می‌ماندند.)"""
    _p, s, v, d, a = (list(job) + [""] * 5)[:5]
    if s:
        return 0
    if v:
        return 2
    if d:
        return 3
    if a:
        return 4
    return 1


def _rounded_image(img: QImage, w: int, h: int, radius: int) -> QImage:
    """کراپ وسط + گوشه‌گرد روی *QImage* — برخلاف QPixmap، در نخ جدا از UI مجاز است."""
    scaled = img.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(path)
    p.drawImage(-x, -y, scaled)
    p.end()
    return out


class PosterWorker(QThread):
    """بارگذار ناهمزمان پوسترهای شبکه‌ی کتابخانه.

    کار سنگین (یافتن/تولید PNG کش، دیکود تصویر از روی دیسک، کراپ و گردکردن
    گوشه‌ها) همگی در این نخ انجام می‌شود — نخ UI هرگز منتظر دیسک نمی‌ماند،
    پس ویندوز هم پنجره‌ی «سفید/روح» برای برنامه نمی‌سازد. QImage (برخلاف
    QPixmap) برای ساخت پردازش خارج از نخ UI امن است؛ تبدیل به QPixmap و
    نمایش، پس از رسیدن سیگنال در نخ اصلی انجام می‌شود.

    برای عناوین بدون تصویر هم زنجیره‌ی فال‌بک ادامه پیدا می‌کند: تامبنیل واقعی
    یک ویدیو از داخل عنوان (shell ویندوز یا ffmpeg) — درخواست کاربر.
    """

    # نکته: mtime_ns از ظرفیت int32 سیگنال Qt بیرون می‌زند — object نگه‌اش می‌دارد
    loaded = Signal(int, str, str, object, object)   # نسل، مسیر عنوان، مسیر png، mtime_ns، QImage

    # چند کارگر هم‌زمان داخل این نخ — قبلاً پوسترها تک‌نخی و صفی (دونه‌دونه)
    # ساخته می‌شدند و کتابخانه‌ی چندصدعنوانه دیر پر می‌شد (گزارش کاربر). ترتیب صف
    # همان ترتیب نمایش است، پس کارت‌های قابل‌مشاهده همچنان اول ساخته می‌شوند.
    # تعداد کارگرها با CPU مقیاس می‌شود (۴ تا ۱۰) — ساخت پوستر بیشتر I/O و
    # subprocess است تا محاسبه‌ی خام، پس روی پردازنده‌های چندهسته‌ای بیشتر هم
    # می‌تواند.
    WORKERS = max(4, min(10, (os.cpu_count() or 4)))
    # خط کند: ساخت پوستر از داخل آرشیوِ «سرد» (بدون کش عضو) چندثانیه طول می‌کشد؛
    # قبلاً چندتا از این‌ها هر ۸–۱۰ نخِ استخر را اشغال می‌کردند و پوسترهای ساده‌ی
    # در دید کاربر پشت آن‌ها معطل می‌ماندند (گزارش کاربر: «پوشه‌ی فقط‌فشرده تامبنیل
    # نمی‌گیرد»). حالا کارهای سردِ فشرده به خط کوچک جدا می‌روند تا همیشه نخ‌های
    # آزاد برای کارهای سریع بماند.
    SLOW_MAX = max(1, min(3, WORKERS // 3))

    def __init__(self, store: Store, cache: ThumbnailCache, fthumbs, parent=None,
                 cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # اندازه‌ی مؤثرِ کش (از مقیاس نمایش) — فراخوانی‌های زنجیره‌ی پوستر با همین px
        self.cache_px = int(cache_px)
        self.fthumbs = fthumbs          # FileThumbs — برای تامبنیل ویدیوی فال‌بک
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._jobs: deque = deque()
        self._gen = 0
        self._stop = False

    def enqueue(self, jobs, gen: int):
        """افزودن کارهای تازه + اعلام نسل جاری (کارهای نسل‌های قدیمی رد می‌شوند)."""
        with QMutexLocker(self._mutex):
            self._gen = gen
            self._jobs.extend(jobs)
            self._cond.wakeAll()

    def prioritize(self, paths, gen: int):
        """کارت‌های در دید کاربر به ابتدای صف منتقل می‌شوند (ترتیب نسبی حفظ می‌شود).

        کلید حل مشکل «پایینِ لیست تامبنیل نمی‌گیرد تا از بالا اسکرول کنی»: صف
        به‌جای ترتیب ثابتِ بالا→پایین، با هر اسکرول بازچیده می‌شود."""
        paths = set(paths or ())
        with QMutexLocker(self._mutex):
            if not self._jobs or gen != self._gen:
                return
            pri = [j for j in self._jobs if j and j[0] in paths]
            if not pri:
                return
            rest = [j for j in self._jobs if not (j and j[0] in paths)]
            self._jobs = deque(pri + rest)
            self._cond.wakeAll()

    def stop(self):
        with QMutexLocker(self._mutex):
            self._stop = True
            self._jobs.clear()
            self._cond.wakeAll()

    def _is_slow_job(self, job) -> bool:
        """آیا این کار ساخت پوستر از «آرشیوِ سرد» است؟ (نه کش تصویر دارد نه کش عضو.)"""
        try:
            j = tuple(job) + ("",) * (5 - len(job))
            arc = j[4]
            if not arc:
                return False
            from pathlib import Path as _P
            ft = self.fthumbs
            p = _P(arc)
            if ft._cache_path(p, "archive", self.cache_px).exists():
                return False
            bad = ft._cache_path(p, "archive", self.cache_px).with_suffix(".bad")
            if bad.exists():
                return False            # مارکر منفی ⇒ پروب فوری شکست می‌خورد (سریع)
            return ft._arc_pick_get(p) is None
        except Exception as _exc_dbg:
            dbg_log(f"_is_slow_job failed: {type(_exc_dbg).__name__}")
            return False

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        pool = ThreadPoolExecutor(max_workers=self.WORKERS,
                                  thread_name_prefix="poster")
        slow_pool = ThreadPoolExecutor(max_workers=self.SLOW_MAX,
                                       thread_name_prefix="poster-arc")
        futs: set = set()
        parked: list = []              # کارهای آرشیوی سرد در انتظار نوبتِ خط کند
        try:
            while True:
                # جایگاه‌های آزاد را فوری پر کن — قبلاً کل دسته (WORKERS کار) تک‌جا
                # صبر می‌شد و یک پوسترِ آرشیوی کند (7z/rar چندثانیه‌ای) جای خروجی
                # ۹ کارگر دیگر را می‌گرفت و کل لیست عقب می‌ماند (گزارش کاربر).
                self._mutex.lock()
                gen = self._gen
                slow_busy = sum(1 for f in futs if getattr(f, "_mc_slow", False))
                # کارهای پارک‌شده‌ی خط کند هر وقت جا باز شد میروند؛ از نسل کهنه حذف
                while parked and slow_busy < self.SLOW_MAX and not self._stop:
                    pg, pjob = parked.pop(0)
                    if pg != gen:
                        continue                        # بازسازی کهنه — دور ریخته می‌شود
                    fu = slow_pool.submit(self._build_one, pg, pjob)
                    fu._mc_slow = True
                    futs.add(fu)
                    slow_busy += 1
                fast_busy = len(futs) - slow_busy
                while self._jobs and fast_busy < self.WORKERS and not self._stop:
                    job = self._jobs.popleft()
                    slow = self._is_slow_job(job)
                    if slow:
                        # خط کند فقط تا سقف خودش — بقیه‌ی آرشیوهای سرد پارک می‌شوند
                        # تا نوبتِ کارهای تندِ در دید کاربر را نگیرند (گزارش کاربر).
                        if slow_busy >= self.SLOW_MAX:
                            parked.append((gen, job))
                            continue
                        slow_busy += 1
                    else:
                        fast_busy += 1
                    fu = (slow_pool if slow else pool).submit(
                        self._build_one, gen, job)
                    fu._mc_slow = slow
                    futs.add(fu)
                stop = self._stop
                self._mutex.unlock()
                if stop:
                    return
                if not futs:
                    self._mutex.lock()
                    while not self._jobs and not parked and not self._stop:
                        self._cond.wait(self._mutex)
                    self._mutex.unlock()
                    continue
                done, _pending = wait(futs, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        f.result()
                    except Exception as _exc_dbg:
                        dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                        pass
                futs -= done
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            slow_pool.shutdown(wait=False, cancel_futures=True)

    def _emit_fail(self, gen: int, item_path: str, why: str):
        """اطلاع رسمی شکست (نه سکوت!) تا صفحه بتواند با backoff بازتلاش کند.

        قبلاً شکستِ هر مرحله (مثلاً قفل موقت آنتی‌ویروس روی فایل یا کارگر 7z که
        گیر کرد) فقط «ساکت» می‌شد و کارت برای همیشه روی جای‌نگه‌دار حروفی می‌ماند،
        درحالی‌که صفحه‌ی جزئیات همان پوشه را باز می‌کرد و پوستر را می‌ساخت
        (گزارش کاربر 0.21.0)."""
        if gen != self._gen:
            return
        _poster_dbg(f"poster fail [{why}]: {safe_ref(item_path)}")
        try:
            self.loaded.emit(gen, item_path, "", 0, None)
        except RuntimeError:
            pass                # برنامه در حال بستن — منبع سیگنال رفته

    def _build_one(self, gen: int, job):
        """ساخت یک پوستر (در نخ‌های استخر داخلی) — گارد خطا کامل تا یک فایل بد، صف را نکشد."""
        if gen != self._gen:          # صف از یک بازسازیِ قدیمی‌تر است — رد شود
            return
        job = tuple(job) + ("",) * (6 - len(job))   # تحمل tupleهای قدیمی‌تر
        item_path, src, vid, pdf, arc, force_flag = job
        force = str(force_flag) == "1"      # بازتلاش زمان‌بندی‌شده: کش منفی رد شود
        try:
            # این درخواستِ UI است — فازهای پیش‌ساخت رعایتش می‌کنند
            FileThumbs.ui_request_tick()
            # زنجیره‌ی کامل با عبور از مرحله‌ی شکست‌خورده: آیکون ← ویدیو ← PDF ← فشرده
            # (اگر ویدیو خراب باشد و تامبنیل ندهد، خودکار PDF و بعد فشرده امتحان می‌شود)
            png = poster_png_fallback(self.cache.get, self.fthumbs,
                                      src, vid, pdf, arc, self.cache_px, force=force)
            if not png:
                self._emit_fail(gen, item_path, "chain empty")
                return
            try:
                mt = Path(png).stat().st_mtime_ns
            except OSError:
                mt = 0
            qimg = load_cache_qimage(png)       # کش WebP — دیکودر native Qt
            if qimg is None or qimg.isNull():
                self._emit_fail(gen, item_path, f"decode {png}")
                return
            # آیکون مربعی (نسبت ناسازگار) → letterbox با زمینه‌ی بلارشده،
            # نسبت نزدیک → همان کراپ پوششیِ سنتی
            if poster_needs_letterbox(qimg.width(), qimg.height(), POSTER_W, POSTER_H):
                img = poster_letterbox_image(qimg, POSTER_W, POSTER_H, POSTER_RADIUS)
            else:
                img = _rounded_image(qimg, POSTER_W, POSTER_H, POSTER_RADIUS)
            if gen == self._gen:
                try:
                    self.loaded.emit(gen, item_path, str(png), mt, img)
                except RuntimeError:
                    pass        # برنامه در حال بستن
        except RuntimeError:
            pass                # سیگنال مرده هنگام بستن برنامه
        except Exception as e:
            dbg_log(f"_build_one failed: {type(e).__name__}")
            try:
                self._emit_fail(gen, item_path, f"error {e!r:.120}")
            except Exception as _exc_dbg:
                dbg_log(f"_build_one failed: {type(_exc_dbg).__name__}")
                pass


class LibraryPage(QWidget):
    openDetails = Signal(object)
    rescanClicked = Signal()
    # خروجی HTML حذف شد (تصمیمِ کاربر) — دکمه و سیگنالِ آن هم رفت
    pathsClicked = Signal()
    editRequested = Signal(object)
    favChanged = Signal(object)
    manageCatsClicked = Signal()
    themeChanged = Signal(str)
    randomRequested = Signal(object)
    smartPlayRequested = Signal(object)
    subcatChanged = Signal(object, str)     # (item, دسته‌ی جدید) — چک‌باکس سریع کارت
    itemsChanged = Signal()                  # تغییر وضعیت تماشا از منوی راست‌کلیک

    def __init__(self, store: Store, cache: ThumbnailCache, parent=None,
                 cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # اندازه‌ی مؤثرِ کش (از مقیاس نمایش ویندوز) — همه‌ی زنجیره‌های پوستر با آن کار می‌کنند
        self.cache_px = int(cache_px)
        self.items = []
        self.filter_kind = "all"   # all | fav | cat
        self.filter_cat = ""
        # کش LRU پیکس‌مپ پوسترها (کلید: مسیر png کش + mtime) — سوییچ دسته‌ها
        # بدون خواندن دوباره‌ی دیسک است و سقف‌دار است تا RAM بی‌حد نرود.
        self._pm_cache: OrderedDict = OrderedDict()
        # مسیر عنوان → کلید آماده‌ی کش (تا بازسازی کارت‌ها به کارگر نیاز نداشته باشد)
        self._item_png: dict[str, tuple] = {}
        # (C1) نتیجه‌های درِپروازِ کارگر برای عنوانی که کاورش عوض شده —
        # در _on_poster_loaded رد می‌شوند تا پوسترِ قدیمی برگردد
        self._stale_sources: set[str] = set()
        self._cards_gen = 0                    # نسل بازسازی کارت‌ها (باطل‌سازی کارهای کهنه)
        import weakref
        self._weakref = weakref
        self._waiting: dict[str, list] = {}    # مسیر عنوان → [weakref کارت‌های در انتظار پوستر]
        self._poster_attempts: dict[str, int] = {}   # شمار تلاش‌های ناموفق هر عنوان (این نسل)
        # تامبنیل فایل‌ها/ویدیوها — برای فال‌بک پوستر عناوین بدون تصویر
        from filethumbs import FileThumbs
        self._fthumbs = FileThumbs(cache.cache_dir)
        # نخ بارگذار پوستر (عمر برنامه؛ در closeEvent متوقف می‌شود)
        self._poster_thread = PosterWorker(store, cache, self._fthumbs, self,
                                           cache_px=self.cache_px)
        self._poster_thread.loaded.connect(self._on_poster_loaded)
        self._poster_thread.start()
        # متا‌کش دسته‌ها: ساخت نقشه‌ی خانواده‌ها *یک‌بار* در هر بازسازی، نه به‌ازای هر کارت
        # (رفع O(N²) — علت اصلی گیر چندثانیه‌ای هنگام سوییچ دسته در کتابخانه‌ی بزرگ)
        self._cats_list: list[str] = []
        self._fam_map: dict[str, list[str]] = {}    # ریشه → [ریشه + فرزندان مستقیم]
        self._expanded: set | None = None           # وضعیت بازشدن درخت (None = پیش‌فرض باز)
        # جستجویِ debounceشده: با تایپ هر حرف، رندر کل شبکه را ۱۸۰ میلی‌ثانیه به تعویق می‌اندازیم
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.rebuild_cards)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------- سایدبار ----------------
        side = QFrame()
        side.setObjectName("SideBar")
        side.setFixedWidth(260)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 14, 14, 10)
        sl.setSpacing(8)

        # لوگو + نام برنامه
        brand = QHBoxLayout()
        brand.setSpacing(9)
        logo = QLabel()
        from portable import resource_path as _rpath45    # BUILD/exe: منابع از _MEIPASS
        logo_pm = QPixmap(str(_rpath45("assets/icon.png")))
        if not logo_pm.isNull():
            logo.setPixmap(logo_pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        t1 = QLabel(T("FolderDex"))
        t1.setObjectName("AppTitle")
        from appinfo import APP_NAME, APP_NAME_EN, APP_VERSION
        from i18n import is_rtl as _rtl45
        t2 = QLabel(T("{0} · نسخه‌ی {1}").format(
            APP_NAME if _rtl45() else APP_NAME_EN, fa(APP_VERSION)))
        t2.setObjectName("BrandSub")
        brand_text.addWidget(t1)
        brand_text.addWidget(t2)
        brand.addWidget(logo)
        brand.addLayout(brand_text, 1)
        sl.addLayout(brand)

        self.search = QLineEdit()
        self.search.setPlaceholderText(T("🔍 جستجو در نام، مسیر، توضیحات…"))
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(T("با ی/ک عربی هم پیدا می‌کند (یعنی «سريال» = «سریال»)"))
        self.search.textChanged.connect(self._on_search)
        sl.addWidget(self.search)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(6)
        self.sort_combo = NoWheelComboBox()
        for _s45 in SORTS:                     # مقدار فارسیِ config ثابت، نمایش دوزبانه
            self.sort_combo.addItem(T(_s45), _s45)
        self.btn_sort_dir = QPushButton("↑")
        self.btn_sort_dir.setObjectName("ModeBtn")
        self.btn_sort_dir.setFixedWidth(38)
        self.btn_sort_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        sort_row.addWidget(QLabel(T("مرتب‌سازی:")))
        sort_row.addWidget(self.sort_combo, 1)
        sort_row.addWidget(self.btn_sort_dir)
        sl.addLayout(sort_row)

        # بازیابی ترجیح ذخیره‌شده
        sort_cfg = (store.config.get("sort") or {})
        mode = sort_cfg.get("mode", "نام")
        if mode in SORTS:
            _di45 = self.sort_combo.findData(mode)
            if _di45 >= 0:
                self.sort_combo.setCurrentIndex(_di45)
        self.sort_asc = bool(sort_cfg.get("asc", mode == "نام"))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_mode)
        self.btn_sort_dir.clicked.connect(self._toggle_sort_dir)
        self._refresh_sort_dir_btn()

        # درخواست کاربر: فیلتر وضعیت تماشا — هوشمند و مستقل از نوع محتوا
        # (کتاب/مانگا/فشرده هم پوشش داده می‌شود). برچسبِ نمایشی خنثی شده چون
        # «دیده‌شده» برای کتاب معنا ندارد؛ مقدارِ ذخیره‌شده‌ی قدیمی حفظ می‌شود.
        wf_row = QHBoxLayout()
        wf_row.setSpacing(6)
        self.watch_filter = NoWheelComboBox()
        for _wlbl, _wval in ((T("همه"), "همه"),
                             (T("دیده‌نشده"), "ندیده"),
                             (T("نیمه‌تمام"), "نیمه‌تمام"),
                             (T("کامل دیده‌شده"), "دیده‌شده")):
            self.watch_filter.addItem(_wlbl, _wval)
        _wfc48 = str(store.config.get("watch_filter") or "همه")
        _wi48 = self.watch_filter.findData(_wfc48)
        self.watch_filter.setCurrentIndex(_wi48 if _wi48 >= 0 else 0)
        self.watch_filter.currentIndexChanged.connect(self._on_watch_filter)
        wf_row.addWidget(QLabel(T("نمایش:")))
        wf_row.addWidget(self.watch_filter, 1)
        sl.addLayout(wf_row)

        # انتخاب پوسته (تم)
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        self.theme_combo = NoWheelComboBox()
        for key in THEMES:
            self.theme_combo.addItem(T(theme_label(key)), key)   # جاافتاده — سایدبار
        idx = self.theme_combo.findData(store.theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_pick)
        theme_row.addWidget(QLabel(T("پوسته:")))
        theme_row.addWidget(self.theme_combo, 1)
        sl.addLayout(theme_row)

        sl.addWidget(QLabel(T("دسته‌بندی"), objectName="SideLabel"))
        self.cats = QTreeWidget()
        self.cats.setObjectName("CatTree")
        self.cats.setHeaderHidden(True)
        self.cats.setIndentation(18)
        self.cats.setAnimated(True)
        self.cats.currentItemChanged.connect(self._on_cat_item)
        self.cats.itemCollapsed.connect(self._on_tree_fold)
        self.cats.itemExpanded.connect(self._on_tree_fold)
        sl.addWidget(self.cats, 1)

        self.stats = QLabel("")
        self.stats.setObjectName("StatsLabel")
        self.stats.setWordWrap(True)
        sl.addWidget(self.stats)

        self.btn_rescan = QPushButton(T("🔄 اسکن مجدد"))
        self.btn_paths = QPushButton(T("⚙ تنظیمات"))
        self.btn_paths.setObjectName("PrimaryBtn")
        self.btn_paths.setToolTip(T("مسیرها، دسته‌ها، پوسته، پخش‌کننده و تنظیمات کش"))
        self.btn_rescan.clicked.connect(self.rescanClicked)
        self.btn_paths.clicked.connect(self.pathsClicked)
        sl.addWidget(self.btn_paths)
        sl.addWidget(self.btn_rescan)

        # ---------------- محتوا ----------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(20, 12, 20, 6)
        self.page_title = QLabel(T("همه"))          # برچسب اولیه هم دوزبانه
        self.page_title.setObjectName("PageTitle")
        self.page_sub = QLabel("")
        self.page_sub.setObjectName("PageSub")
        head.addWidget(self.page_title)
        head.addSpacing(10)
        head.addWidget(self.page_sub, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.btn_random = QPushButton(T("🎲 امشب چی ببینم؟"))
        self.btn_random.setObjectName("FlatBtn")
        self.btn_random.setToolTip(
            T("یک عنوان از فیلتر فعلی انتخاب می‌شود؛\n"
            "نیمه‌تمام‌ها شانس بیشتری دارند و کاملاً دیده‌شده‌ها کمتر."))
        self.btn_random.clicked.connect(self._random_dialog)
        head.addWidget(self.btn_random)
        rl.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container.setObjectName("ScrollContent")
        self.flow = FlowLayout(self.container, spacing=14)
        self.flow.setContentsMargins(18, 8, 18, 16)
        self.scroll.setWidget(self.container)
        rl.addWidget(self.scroll, 1)

        # سیاست دید برای پوسترها (گزارش کاربر: «باید از بالا کم کم بیای پایین تا
        # تامبنیل‌ها ساخته شوند») — در حالت سقف‌دارِ کش فقط کارت‌های در دید job
        # می‌گیرند و در حالت نامحدود صف با اسکرول بازاولویت می‌شود:
        # مسیر عنوان → (job tuple، weakref کارت) برای کارت‌های هنوز صف‌نشده
        self._poster_pending: dict[str, tuple] = {}
        # مسیرهایی که کارشان (از هر مسیر: جریانی یا هندسی) صف شده — تکراری نسازیم
        self._poster_started: set = set()
        # آخرین فهرست مرتب/فیلترشده‌ی نمایش‌داده‌شده — برای دیدِ هندسیِ کارت‌های
        # «هنوز ساخته‌نشده» (پرش مستقیم به تهِ لیست ← گزارش کاربر)
        self._shown_items: list = []
        self._vis_timer = QTimer(self)
        self._vis_timer.setSingleShot(True)
        self._vis_timer.timeout.connect(self._apply_poster_visibility)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_poster_visibility())
        # تشخیص اسکرول فعال — برای تأخیر ساخت کارت‌ها حین اسکرول
        self._scroll_paused = False
        self._scroll_last_val = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(200)
        self._scroll_timer.timeout.connect(self._on_scroll_idle)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_move)

        root.addWidget(side)
        root.addWidget(right, 1)

    # ------------------------------------------------------------ داده
    def set_items(self, items):
        """آیتم‌های تازه (بعد از اسکن/تغییر مسیرها) — یک‌پاس: سایدبار بازسازی می‌شود
        و خودش در پایان بازسازی کارت‌ها و آمار را صدا می‌زند (بدون دوبار کار)."""
        self.items = list(items)
        self.rebuild_sidebar()

    def _counts(self):
        counts, favs = {}, 0
        for it in self.items:
            c = self.store.category_of(it)
            counts[c] = counts.get(c, 0) + 1
            favs += self.store.fav(it.path)
        return counts, favs

    def _total_for(self, prefix: str, counts: dict) -> int:
        return sum(n for c, n in counts.items() if c == prefix or c.startswith(prefix + "/"))

    def _on_tree_fold(self, _item):
        """کاربر گرهای را جمع/باز کرد — مجموعه‌ی بازشده‌ها را صریح به‌روز کن."""
        cur: set = set()
        self._collect_expanded(cur)
        self._expanded = cur

    def _collect_expanded(self, into: set):
        """جمع‌آوری دسته‌های بازشده‌ی فعلی درخت (قبل از بازسازی آن)."""
        it = self._tree_iter()
        while (item := it.value()) is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "cat" and item.isExpanded():
                into.add(data[1])
            it += 1

    def rebuild_sidebar(self):
        self._refresh_cat_meta()              # متا‌کش خانواده‌ها — یک‌بار برای همه‌ی کارت‌ها
        counts, favs = self._counts()
        sel_kind, sel_cat = self.filter_kind, self.filter_cat
        self.cats.blockSignals(True)
        # وضعیت بازشدن درخت را نگه دار تا کاربر با هر بازسازی از نو جمعشان نبیند
        # (و expandAll سنگین فقط در ساختنِ اول اجرا شود، نه هر بار)
        if self._expanded is None:
            expanded_prev = None
        else:
            expanded_prev = set()
            self._collect_expanded(expanded_prev)
        self.cats.clear()

        def make(text, kind, cat="", color=None, bold=False):
            li = QTreeWidgetItem([text])
            li.setData(0, Qt.ItemDataRole.UserRole, (kind, cat))
            if color:
                li.setForeground(0, QBrush(QColor(color)))
            if bold:
                # کپی از فونت واقعی درخت (نه QFont پیش‌فرض با pointSize=‎-۱)
                f = QFont(self.cats.font())
                # فونت‌های QSS-px پوینت‌سایزِ -۱ دارند: اگر همان‌طور نسبت داده شود Qt
                # هنگام رسم هشدار «setPointSize: Point size <= 0» می‌دهد — نرمال به پوینت
                if f.pointSizeF() <= 0:
                    f.setPointSizeF(10.0 if f.pixelSize() <= 0 else max(
                        8.0, round(f.pixelSize() * 0.75, 1)))
                f.setBold(True)
                li.setFont(0, f)
            return li

        self.cats.addTopLevelItem(make(T("🏠  همه ({0})").format(fa(len(self.items))), "all", bold=True))
        self.cats.addTopLevelItem(make(T("⭐  علاقه‌مندی‌ها ({0})").format(fa(favs)), "fav", bold=True))
        # درخواست کاربر: «🕘 موارد اخیر» — آخرین عنوان‌های پخش/بازشده
        # (بازبینی نام: پیشنهادِ خود کاربر — «موارد اخیر یا یک همچین چیزی»)
        # round 32: مقایسه با کلیدِ یکسان‌شده‌ی مسیر (normcase/normpath) —
        # قبلاً رشته‌ی خام بود و شکلِ متفاوتِ همان مسیر از عددِ کنارِ
        # «موارد اخیر» می‌افتاد (گزارش کاربر: «۱۵ ولی ۹ می‌نویسد»).
        _paths48 = {self.store.norm_path(i.path) for i in self.items}
        _rec48 = [p for p in self.store.recents()
                  if self.store.norm_path(p) in _paths48]
        self.cats.addTopLevelItem(make(T("🕘  موارد اخیر ({0})").format(fa(len(_rec48))), "recent", bold=True))

        nodes: dict[str, QTreeWidgetItem] = {}
        cats = sorted(self.store.categories(), key=natural_key)
        for cat in cats:
            parts = [p for p in cat.split("/") if p.strip()]
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                if sub in nodes:
                    continue
                total = self._total_for(sub, counts)
                from i18n import disp_cat as _dcat45   # نامِ نمایشیِ سگمنت
                item = make(f"{_dcat45(parts[i-1])}  ({fa(total)})",
                            "cat", sub, self.store.color(sub),
                            bold=(i == 1))
                if i == 1:
                    self.cats.addTopLevelItem(item)
                else:
                    parent = nodes["/".join(parts[:i - 1])]
                    parent.addChild(item)
                nodes[sub] = item

        # بازشدن درخت: در ساختنِ اول همه باز؛ بازسازی‌های بعدی حفظ وضعیت کاربر
        if expanded_prev is None:
            self.cats.expandAll()
            self._expanded = None           # یعنی «همه باز» (پیش‌فرض اولیه)
        else:
            for sub, node in nodes.items():
                node.setExpanded(sub in expanded_prev)
            self._expanded = expanded_prev

        # بازگرداندن انتخاب قبلی
        target = None
        it = self._tree_iter()
        while (item := it.value()) is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data == (sel_kind, sel_cat):
                target = item
                break
            it += 1
        if target is None and self.cats.topLevelItemCount():
            target = self.cats.topLevelItem(0)
        if target:
            self.cats.setCurrentItem(target)
        self.cats.blockSignals(False)
        self._on_cat_item(self.cats.currentItem(), None)

    def _tree_iter(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        return QTreeWidgetItemIterator(self.cats)

    def _on_cat_item(self, item, _prev):
        if item:
            self.filter_kind, self.filter_cat = item.data(0, Qt.ItemDataRole.UserRole)
        titles = {"all": T("🏠 همه"), "fav": T("⭐ علاقه‌مندی‌ها"),
                  "recent": T("🕘 موارد اخیر")}
        from i18n import disp_cat as _dcat45
        self.page_title.setText(
            titles.get(self.filter_kind, _dcat45(self.filter_cat).replace("/", " › "))
        )
        self.rebuild_cards()
        self._update_stats()          # آمار سایدبار متناسب با دسته‌ی انتخاب‌شده

    # ------------------------------------------------------------ کش پوستر (LRU)
    PM_LIMIT = 900

    def _pm_get(self, key):
        pm = self._pm_cache.get(key)
        if pm is not None:
            self._pm_cache.move_to_end(key)      # LRU: استفاده‌ی اخیر → عقب صف
        return pm

    def _pm_put(self, key, pm: QPixmap):
        self._pm_cache[key] = pm
        self._pm_cache.move_to_end(key)
        while len(self._pm_cache) > self.PM_LIMIT:
            try:
                self._pm_cache.popitem(last=False)     # بیرون‌اندازی قدیمی‌ترین
            except KeyError:
                break

    def clear_pixmap_cache(self):
        """پاک‌سازی کش پوسترها (مثلاً بعد از ویرایش پوستر سفارشی یک عنوان).

        باگ گزارش‌شده (0.21.0): پیاده‌سازی قبلی فقط کش‌ها را پاک و gen را بالا
        می‌برد — کارت‌هایی که همان لحظه در انتظار نتیجه‌ی کارگر بودند (مثل
        پوشه‌ی فقط‌فشرده در حال ساخت) برای همیشه روی جای‌نگه‌دار می‌ماندند چون
        نتیجه‌ی در پرواز با gen جدید رد می‌شد و کارت هم دیگر در صف انتظار نبود.
        حالا بازسازی کاملِ امن انجام می‌شود و موقعیت اسکرول هم حفظ می‌شود."""
        self._pm_cache.clear()
        self._item_png.clear()
        self._stale_sources.clear()   # نسلِ تازه منبعِ تازه می‌خواند — نگهبانِ C1 دیگر لازم نیست
        try:
            y = self.scroll.verticalScrollBar().value()
        except Exception as _exc_dbg:
            dbg_log(f"clear_pixmap_cache failed: {type(_exc_dbg).__name__}")
            y = 0
        self.rebuild_cards()
        if y:
            def _restore(v=y):
                try:
                    self.scroll.verticalScrollBar().setValue(v)
                except Exception as _exc_dbg:
                    dbg_log(f"clear_pixmap_cache._restore failed: {type(_exc_dbg).__name__}")
                    pass
            for d in (0, 150, 450, 1000):     # چیدمان دسته‌ای است — چند نوبت برگردان
                QTimer.singleShot(d, _restore)

    def invalidate_item_poster(self, path) -> None:
        """بی‌اعتبارکردنِ پوسترِ *یک* عنوان — پس از تعویض/حذفِ کاور از جزئیات (C1).

        برخلاف clear_pixmap_cache (کلِ شبکه + اسکرول‌پرش)، فقط همین عنوان از
        کش و صف‌ها بیرون می‌آید: بازسازیِ بعدی کارت job تازه با منبعِ تازه
        می‌سازد و بقیه‌ی کارت‌ها دست‌نخورده می‌مانند. نتیجه‌ی درِپروازِ کارگر
        برای این عنوان هم باطل می‌شود (_stale_sources) تا پوسترِ قدیمی برگردد."""
        p = str(path)
        self._item_png.pop(p, None)
        self._poster_started.discard(p)     # تا job تازه با منبعِ تازه صف شود
        self._poster_attempts.pop(p, None)
        self._poster_pending.pop(p, None)
        self._stale_sources.add(p)

    def shutdown(self):
        """توقف تمیز نخ بارگذار پوستر هنگام بستن برنامه."""
        try:
            self._poster_thread.stop()
            self._poster_thread.wait(800)
        except Exception as _exc_dbg:
            dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ پوستر placeholder / شناخته‌شده
    def _ph_pixmap(self, item) -> QPixmap:
        """پوستر جایگزین (حرف اول + رنگ دسته) — فوری و بدون خواندن دیسک."""
        cat = self.store.category_of(item)
        letter = self.store.title(item).strip()[:1] or T("؟")
        key = ("ph", letter, self.store.color(cat))
        pm = self._pm_get(key)
        if pm is None:
            pm = placeholder_poster(letter, self.store.color(cat),
                                    POSTER_W, POSTER_H, POSTER_RADIUS)
            self._pm_put(key, pm)
        return pm

    def _known_pm(self, item):
        """اگر پوستر واقعی این عنوان قبلاً آماده شده، همان — وگرنه None (کارگر می‌سازد)."""
        key = self._item_png.get(str(item.path))
        if key:
            pm = self._pm_get(key)
            if pm is not None:
                return pm
        return None

    def _poster_pixmap(self, item) -> QPixmap:
        """خواندن همگام پوستر — فقط برای مصارف تکی (دیالوگ «امشب چی ببینم؟»).

        شبکه‌ی کتابخانه از این مسیر استفاده نمی‌کند؛ پوستر کارت‌ها ناهمزمان است.
        """
        FileThumbs.ui_request_tick()          # درخواستِ UI (رعایتِ پیش‌ساخت)
        pm = self._known_pm(item)
        if pm is not None:
            return pm
        src = self.store.poster_of(item, static=True)  # شبکه‌ی کارت‌ها فریم ساکن می‌خواند (C7)
        png = poster_png_fallback(
            self.cache.get, self._fthumbs, src,
            getattr(item, "preview_video", None),
            getattr(item, "preview_pdf", None),
            getattr(item, "preview_archive", None), self.cache_px)
        if png:
            try:
                mt = Path(png).stat().st_mtime_ns
            except OSError:
                mt = 0
            key = (str(png), mt)
            pm = self._pm_get(key)
            if pm is None:
                raw = load_cache_pixmap(png)
                if raw is not None and not raw.isNull():
                    pm = poster_pixmap(raw, POSTER_W, POSTER_H, POSTER_RADIUS)
                    self._pm_put(key, pm)
            if pm is not None and not pm.isNull():
                self._item_png[str(item.path)] = key
                return pm
        return self._ph_pixmap(item)

    # ------------------------------------------------------------ متا‌کش دسته‌ها (رفع O(N²))
    def _refresh_cat_meta(self):
        """نقشه‌ی خانواده‌ی دسته‌ها یک‌بار برای همه‌ی کارت‌ها ساخته می‌شود.

        قبلاً هر کارت Store.categories() (که O(N) روی متای همه‌ی عناوین است) و
        child_categories (فیلتر دوباره‌ی لیست) را صدا می‌زد — یعنی O(N²) در هر
        بازسازی؛ علت اصلی گیر چندثانیه‌ای سوییچ دسته در کتابخانه‌ی بزرگ همین بود.
        """
        cats = self.store.categories()
        self._cats_list = cats
        children: dict[str, list[str]] = {}
        for c in cats:
            p = Store.parent_of(c)
            if p is not None:
                children.setdefault(p, []).append(c)
        fam: dict[str, list[str]] = {}
        for base, kids in children.items():
            fam[base] = [base] + kids
        self._fam_map = fam

    def _subcat_view(self, item) -> dict | None:
        """باکس تغییر سریع زیردسته — O(1) از روی متا‌کش."""
        cat = self.store.category_of(item)
        base = Store.parent_of(cat) or cat
        options = self._fam_map.get(base)
        if not options or len(options) < 2:
            return None
        labels = {f: (f if f == base else "↳ " + f[len(base) + 1:]) for f in options}
        return {
            "kind": "combo",
            "options": options,
            "labels": labels,
            "current": cat,
            "tip": T("تغییر سریع دسته‌ی این عنوان — دسته‌ی اصلی و همه‌ی زیردسته‌ها"),
        }

    def refresh_durations(self):
        """بعد از محاسبه‌ی پس‌زمینه‌ی زمان‌ها، متنِ اطلاعاتِ کارت‌های موجود تازه می‌شود
        (0.31.0) — بدون بازسازیِ کارت/پوستر و بدون پرشِ اسکرول."""
        for card in self.findChildren(CardWidget):
            try:
                card.apply_view(self._view(card.item))
            except Exception as _exc_dbg:
                dbg_log(f"refresh_durations failed: {type(_exc_dbg).__name__}")
                pass

    def _view(self, item) -> dict:
        cat = self.store.category_of(item)
        info = item.info_short
        dur = self.store.total_duration(item)
        if dur:
            info += f" · ⏱ {format_duration_short(dur)}"
        done, part = self.store.watch_progress(item)
        tot48 = self.store.watchable_total(item)     # هوشمند — همهٔ دسته‌ها
        if tot48 and (done or part):
            info += f" · ✓{fa(done)}/{fa(tot48)}"
            if part:
                info += f" ◐{fa(part)}"
        _r48 = self.store.rating(item.path)          # امتیاز شخصی روی کارت
        if _r48:
            # بازبینی کاربر: «به فیل چند ستاره دادیم دیده نمیشه وقتی جزئیات زیادن»
            # ستاره اولِ خط می‌آید تا با هر طولِ جزئیات هرگز بریده نشود.
            info = f"★{fa(_r48)} · " + info
        return {
            "title": self.store.title(item),
            "category": cat,
            "color": self.store.color(cat),
            "info_short": info,
            "desc": self.store.desc(item.path),
            "fav": self.store.fav(item.path),
            # پوستر واقعی اگر در کش باشد، وگرنه جایگزین فوری — نسخه‌ی نهایی از کارگر می‌رسد
            "poster": self._known_pm(item) or self._ph_pixmap(item),
            "subcat": self._subcat_view(item),
        }

    def _on_theme_pick(self, _i):
        self.themeChanged.emit(self.theme_combo.currentData())

    def _on_watch_filter(self, _i):
        # فیلتر وضعیت تماشا — بلافاصله ذخیره (ترجیح کاربر ماندگار است)
        self.store.config["watch_filter"] = str(self.watch_filter.currentData() or "همه")
        try:
            self.store.save_config(_bump=False)
        except TypeError:
            self.store.save_config()
        self.rebuild_cards()
        self._update_stats()

    def _on_sort_mode(self, _i):
        # پیش‌فرض جهت: نام و نوع‌محتوا → صعودی، بقیه → نزولی
        self.sort_asc = (self.sort_combo.currentData() or self.sort_combo.currentText()) in ("نام", "نوع محتوا")
        self._refresh_sort_dir_btn()
        self._save_sort()
        self.rebuild_cards()

    def _toggle_sort_dir(self):
        self.sort_asc = not self.sort_asc
        self._refresh_sort_dir_btn()
        self._save_sort()
        self.rebuild_cards()

    def _refresh_sort_dir_btn(self):
        self.btn_sort_dir.setText("↑" if self.sort_asc else "↓")
        self.btn_sort_dir.setToolTip(T("صعودی") if self.sort_asc else T("نزولی"))

    def _save_sort(self):
        self.store.config["sort"] = {
            "mode": self.sort_combo.currentData() or self.sort_combo.currentText(),
            "asc": self.sort_asc,
        }
        self.store.save_config()

    def _filtered(self):
        # fa_norm: «ي/ك» عربی، نیم‌فاصله و کشیده مهم نیستند — جستجوی فارسی واقعی
        q = fa_norm(self.search.text().strip())
        # نقشه‌ی «موارد اخیر» (برای فیلتر + مرتب‌سازی بازگشتی) و فیلتر وضعیت تماشا
        # round 32: کلیدِ نقشه مسیرِ یکسان‌شده است — همان کلید در فیلتر و مرتب‌سازی
        _rmap48 = ({self.store.norm_path(p): i
                    for i, p in enumerate(self.store.recents())}
                   if self.filter_kind == "recent" else {})
        _wf48 = str(self.watch_filter.currentData() or "همه")
        out = []
        for it in self.items:
            cat = self.store.category_of(it)
            if self.filter_kind == "fav" and not self.store.fav(it.path):
                continue
            if self.filter_kind == "cat" and not Store.matches(cat, self.filter_cat):
                continue
            if self.filter_kind == "recent" and self.store.norm_path(it.path) not in _rmap48:
                continue
            if _wf48 != "همه":                     # فیلتر وضعیت تماشا
                _d48, _p48 = self.store.watch_progress(it)   # (دیده‌شده، نیمه‌تمام)
                _tot48 = self.store.watchable_total(it)       # هوشمند — همهٔ دسته‌ها
                _alldone48 = _tot48 > 0 and _d48 >= _tot48
                _seen48 = _d48 > 0 or _p48 > 0
                if _wf48 == "دیده‌شده" and not _alldone48:
                    continue
                if _wf48 == "نیمه‌تمام" and (_alldone48 or not _seen48):
                    continue
                if _wf48 == "ندیده" and _seen48:
                    continue
            if q:
                hay = fa_norm(
                    self.store.title(it) + " " + str(it.path) + " " + self.store.desc(it.path)
                )
                if q not in hay:
                    continue
            out.append(it)

        if self.filter_kind == "recent":           # تازه‌ترین اول، بدون مرتب‌سازی دستی
            out.sort(key=lambda i: _rmap48.get(self.store.norm_path(i.path), 999))
            return out

        mode = self.sort_combo.currentData() or self.sort_combo.currentText()
        if mode == "نام":
            out.sort(key=lambda i: natural_key(self.store.title(i)),
                     reverse=not self.sort_asc)
        elif mode == "نوع محتوا":
            # درخواست کاربر: مرتب‌سازی بر اساس «نوع غالب محتوا» —
            # ویدئومحور ← تصویرمحور ← فشرده‌محور ← سندمحور ← صوتی ← سایر؛ داخل
            # هر گروه مرتب‌سازی نامی که با دکمه‌ی جهت برعکس می‌شود. نوع غالب
            # هنگام اسکن از شمارش واقعی فایل‌ها حساب می‌شود (نه از روی اسم).
            grouped: dict = {}
            for i in out:
                rank = _TYPE_RANK.get(getattr(i, "dom_type", "") or "other", 99)
                grouped.setdefault(rank, []).append(i)
            res = []
            for rank in sorted(grouped):
                res += sorted(grouped[rank],
                              key=lambda i: natural_key(self.store.title(i)),
                              reverse=not self.sort_asc)
            out = res
        elif mode == "مدت زمان":
            # مدت‌های نامشخص (۰) همیشه ته لیست می‌مانند، در هر دو جهت.
            known = [i for i in out if self.store.total_duration(i) > 0]
            unknown = [i for i in out if self.store.total_duration(i) <= 0]
            known.sort(key=lambda i: self.store.total_duration(i),
                       reverse=not self.sort_asc)
            unknown.sort(key=lambda i: natural_key(self.store.title(i)))
            out = known + unknown
        elif mode == "امتیاز":                       # امتیاز شخصی (بدون‌امتیاز ته لیست)
            rated = [i for i in out if self.store.rating(i.path) > 0]
            unrated = [i for i in out if self.store.rating(i.path) <= 0]
            rated.sort(key=lambda i: self.store.rating(i.path),
                       reverse=not self.sort_asc)
            unrated.sort(key=lambda i: natural_key(self.store.title(i)))
            out = rated + unrated
        else:
            out.sort(key=self._sort_key(mode), reverse=not self.sort_asc)
        return out

    def _sort_key(self, mode: str):
        if mode == "حجم":
            return lambda i: i.size_bytes
        if mode == "تعداد قسمت‌ها":
            return lambda i: i.video_count
        if mode == "تعداد فایل‌ها":
            return lambda i: i.file_count
        if mode == "تاریخ تغییر":
            return lambda i: i.modified
        return lambda i: 0

    # ------------------------------------------------------------ ساخت کارت‌ها (اتمی و بدون سفیدی)
    BUILD_BUDGET_MS = 0.028      # هر نوبت ساخت ~۲۸ میلی‌ثانیه — UI زنده می‌ماند، هاپ‌ها کم

    def _on_search(self, _text):
        # debounce: بازسازی شبکه پس از مکث کوتاه در تایپ (نه با هر حرف)
        self._search_timer.start()

    def _swap_container(self, container: QWidget, flow: FlowLayout):
        """جایگزینی اتمی محتوای اسکرول — صفحه‌ی کهنه اول پنهان و بعد حذف می‌شود.

        این کلید حذف «صفحه‌های سفید» است: اگر ویجت نمایان را بدون پنهان‌کردن از
        والد جدا کنی (کاری که الگوی کلاسیک clear_layout می‌کرد)، Qt همان لحظه آن را
        به یک پنجره‌ی مستقلِ سفیدِ قابل‌مشاهده (با نام برنامه) تبدیل می‌کند — و با
        ۱۰۰۰ کارت، ۱۰۰۰ پنجره‌ی زودگذر!"""
        sb = self.scroll.verticalScrollBar()
        pos = sb.value()
        old = self.scroll.takeWidget()        # مالکیت به ما برمی‌گردد تا خودمان امن حذفش کنیم
        self.scroll.setWidget(container)
        self.container, self.flow = container, flow
        if old is not None and old is not container:
            old.hide()                        # ← حیاتی: اول پنهان، بعد حذف
            old.deleteLater()
        # حفظ موقعیت اسکرول (مثلاً برگشت از جزئیات) — بعد از نشستن چیدمان
        QTimer.singleShot(0, lambda: sb.setValue(min(pos, sb.maximum())))
        # بعد از جایگزینی ظرف، سیاست «اول دید کاربر» روی کارت‌های تازه اجرا شود
        QTimer.singleShot(20, self._schedule_poster_visibility)

    def rebuild_cards(self):
        self._cards_gen += 1
        gen = self._cards_gen
        # تغییر تنظیم «تامبنیل فشرده» همین‌جا به زنجیره‌ی فال‌بک اعمال می‌شود (بدون اسکن)
        self._fthumbs.archive_enabled = self.store.archive_thumbs
        self._waiting.clear()
        self._poster_thread.enqueue([], gen)        # هر بازسازی، کارهای کهنه را باطل می‌کند
        shown = self._filtered()
        self._shown_items = list(shown)     # برای دیدِ هندسیِ کارت‌های ناساخته
        self._poster_started = set()        # نسل تازه — تکراری‌سنجی همین نسل
        self._poster_attempts = {}          # نسل تازه — شمار بازتلاش‌ها صفر
        # نسل تازه منبعِ پوستر را دوباره از store می‌خواند — نگهبانِ C1 برطرف
        self._stale_sources.clear()
        self.page_sub.setText(T("{0} عنوان").format(fa(len(shown))))

        # ظرف تازه *خاموش* ساخته و پر می‌شود؛ صفحه‌ی فعلی تا جایگزینی (اتمی) می‌ماند —
        # یعنی نه فلیکر سفید خالی داریم، نه کلون پنجره‌ایِ کارت‌های کهنه.
        container = QWidget()
        container.setObjectName("ScrollContent")
        flow = FlowLayout(container, spacing=14)
        flow.setContentsMargins(18, 8, 18, 16)

        if not shown:
            msg = QLabel(
                T("🗂\n\nهنوز عنوانی نیست!\nبا دکمه‌ی «⚙ تنظیمات» پوشه‌هایت را اضافه کن.")
                if not self.items else T("چیزی مطابق فیلتر/جستجو پیدا نشد.")
            )
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setObjectName("MutedLabel")
            msg.setStyleSheet("font-size:15px; padding:60px;")
            msg.setMinimumWidth(CARD_W * 3)
            flow.addWidget(msg)
            self._swap_container(container, flow)
            return

        # رزرو ارتفاع نهاییِ شبکه از قبل (تخمینی، بدترین‌حالت با ردیفِ زیردسته):
        # بدون این، هر batch از کارت‌های تازه که اضافه می‌شود ارتفاعِ ScrollArea را
        # کمی بزرگ‌تر می‌کند؛ اگر کاربر همان لحظه در حال اسکرول باشد، محدوده‌ی
        # اسکرول‌بار مدام رشد می‌کند و طوری به‌نظر می‌رسد که «پوستر‌ها از اول دارند
        # ساخته می‌شوند و موقعیت اسکرول ریست می‌شود». با رزروِ ارتفاعِ نهایی همین
        # اول کار، ScrollArea از ابتدا اندازه‌ی واقعی/نهایی را می‌بیند.
        try:
            vpw = self.scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"rebuild_cards failed: {type(_exc_dbg).__name__}")
            vpw = CARD_W
        avail = max(CARD_W, vpw - 36)               # margin چپ/راست جریان ۱۸+۱۸
        cols = max(1, int((avail + 14) // (CARD_W + 14)))
        row_h = CARD_H + 26 + 14                    # بدترین حالت (ردیف با زیردسته) + spacing
        rows = (len(shown) + cols - 1) // cols
        flow.reserve_height(rows * row_h + 8 + 16)  # + contentsMargins بالا/پایینِ FlowLayout

        queue = deque(shown)
        swapped = {"done": False}

        def step():
            if gen != self._cards_gen:              # بازسازی تازه‌تری رخ داده — این صف متروکه
                if not swapped["done"]:
                    container.deleteLater()         # هرگز نمایان نشد — حذف امن
                return
            t0 = time.monotonic()
            jobs = []
            waiters = []
            # غیرفعال‌سازی موقت آپدیت‌های بصری حین افزودن دسته‌ای → یک‌بار
            # layout recalculation به‌جای یکی‌یکی (رفع لگ/هنگ اسکرول)
            if swapped["done"]:
                container.setUpdatesEnabled(False)
            while queue and time.monotonic() - t0 < self.BUILD_BUDGET_MS:
                item = queue.popleft()
                card = CardWidget(item, self._view(item))
                card.clicked.connect(self.openDetails)
                card.menuRequested.connect(self._card_menu)
                card.subcatChanged.connect(self.subcatChanged)
                flow.addWidget(card)
                src = self.store.poster_of(item, static=True)  # شبکه‌ی کارت‌ها فریم ساکن می‌خواند (C7)
                # هر سه کاندید به کارگر داده می‌شود تا با شکست هر مرحله (مثل ویدیوی
                # خراب) زنجیره خودکار سراغ PDF و بعد محتوای فایل فشرده برود.
                vid = getattr(item, "preview_video", None)   # حتی با آیکون — زنجیره‌ی
                pdf = getattr(item, "preview_pdf", None)     # فال‌بک باید با خرابیِ
                arc = getattr(item, "preview_archive", None) # آیکون هم جلو برود
                if (src or vid or pdf or arc) and self._known_pm(item) is None:
                    pth = str(item.path)
                    waiters.append((pth, self._weakref.ref(card)))
                    # شاید دیدِ هندسی (پرش به ته لیست) همین عنوان را همین حالا صف
                    # کرده باشد — کار تکراری نسازیم
                    if pth not in self._poster_started:
                        self._poster_started.add(pth)
                        jobs.append((pth, str(src) if src else "",
                                     str(vid) if vid else "", str(pdf) if pdf else "",
                                     str(arc) if arc else ""))
            if swapped["done"]:
                container.setUpdatesEnabled(True)
            unlimited = not self.store.cache_max_mb()
            if unlimited:
                # حالت نامحدود: همه در پس‌زمینه ساخته می‌شوند (اولویت با دیدِ کاربر
                # از طریق prioritize موقع اسکرول می‌آید)
                for pth, ref in waiters:
                    self._waiting.setdefault(pth, []).append(ref)
                if jobs:
                    # سبک‌به‌سنگین: آیکون/تصویر ← ویدیو ← PDF ← فشرده — پوشه‌های
                    # فقط‌فشرده (سنگین‌ترین) دیگر جلوی بقیه را نمی‌گیرند (گزارش
                    # کاربر: «پوستر پوشه‌های فشرده کند/نمی‌آید»). مرتب‌سازی پایدار
                    # است؛ ترتیب دیداریِ داخل هر رده حفظ می‌شود.
                    jobs.sort(key=_job_rank)
                    self._poster_thread.enqueue(jobs, gen)
            else:
                # حالت سقف‌دار: فقط کارت‌های در دید job می‌گیرند؛ بقیه با رسیدن
                # اسکرول بهشان (در _apply_poster_visibility)
                jobd = {j[0]: j for j in jobs}
                for pth, ref in waiters:
                    if pth in jobd:
                        self._poster_pending[pth] = (jobd[pth], ref)
                    elif pth in self._poster_started:
                        # دیدِ هندسی قبلاً صفش کرده — فقط آپدیت کارت ثبت می‌شود
                        self._waiting.setdefault(pth, []).append(ref)
            self._schedule_poster_visibility()
            if not swapped["done"]:
                swapped["done"] = True
                self._swap_container(container, flow)   # اولین دسته آماده → جایگزینی اتمی
            if queue:
                # تأخیر بیشتر حین اسکرول فعال — اولویت با روانیِ اسکرول است
                delay = 80 if self._scroll_paused else 0
                QTimer.singleShot(delay, step)          # نوبت بعدی در تیک بعدی حلقه‌ی رویداد
            else:
                flow.reserve_height(0)              # ساخت کامل شد؛ ارتفاعِ واقعی جایگزین شود

        step()

    # ------------------------------------------------------------ ردیابی اسکرول
    def _on_scroll_move(self, *_):
        """0.50.0: شروع/ادامه اسکرول → علامت‌گذاری فعال بودن + تأخیر ساخت کارت‌ها."""
        self._scroll_paused = True
        self._scroll_timer.start()

    def _on_scroll_idle(self):
        """0.50.0: ۲۰۰ms بدون اسکرول → ادامه ساخت کارت‌ها."""
        self._scroll_paused = False

    # ------------------------------------------------------------ سیاست دید پوسترها
    def _schedule_poster_visibility(self):
        if not self._vis_timer.isActive():
            self._vis_timer.start(140)

    def _visible_paths(self) -> set:
        """مسیر عناوینی که کارتشان در دید کاربر است (+ یک‌صفحه حاشیه بالا/پایین)."""
        out = set()
        try:
            sb = self.scroll.verticalScrollBar()
            y0 = sb.value()
            h = max(1, self.scroll.viewport().height())
        except Exception as _exc_dbg:
            dbg_log(f"_visible_paths failed: {type(_exc_dbg).__name__}")
            return out

        def _check(path, ref):
            w = ref()
            if w is None:
                return
            try:
                y = w.y()
                wh = w.height()
                if y + wh >= y0 - h and y <= y0 + 2 * h:
                    out.add(path)
            except Exception as _exc_dbg:
                dbg_log(f"_visible_paths._check failed: {type(_exc_dbg).__name__}")
                pass

        for path, (_j, ref) in self._poster_pending.items():
            _check(path, ref)
        for path, refs in self._waiting.items():
            for ref in refs:
                _check(path, ref)
        return out

    def _vis_shown_items(self) -> list:
        """عناوین ناحیه‌ی دیدِ اسکرول — محاسبه‌ی هندسی روی فهرست مرتب/فیلترشده،
        *بدون‌نیاز به ساخته‌شدن کارت* (کارت‌ها سایز ثابت دارند: CARD_W×CARD_H در
        FlowLayout با spacing=14). کلید حل «بالا→پایین می‌سازد وقتی می‌پرم ته
        لیست»: فایلِ کارتِ ناساخته هم کشف و صف می‌شود."""
        items = self._shown_items or []
        if not items:
            return []
        try:
            sb = self.scroll.verticalScrollBar()
            h = max(1, self.scroll.viewport().height())
            y0 = sb.value()
            vpw = self.scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"_vis_shown_items failed: {type(_exc_dbg).__name__}")
            return []
        lo, hi = y0 - h, y0 + 2 * h
        avail = max(CARD_W, vpw - 36)                # margin چپ/راست جریان ۱۸+۱۸
        cols = max(1, int((avail + 14) // (CARD_W + 14)))
        row_h = CARD_H + 26 + 14                     # بدترین حالت (ردیف با زیردسته)
        rows_total = (len(items) + cols - 1) // cols
        # دو ردیف حاشیه از هر طرف برای کارت‌های کوتاه‌ترِ بدون زیردسته
        r0 = max(0, int((lo - 8) // row_h) - 1)
        r1 = min(rows_total - 1, int((hi - 8) // row_h) + 2)
        out = []
        for r in range(r0, r1 + 1):
            out.extend(items[r * cols:(r + 1) * cols])
        return out

    def _apply_poster_visibility(self):
        """سیاست دید را اجرا می‌کند — بعد از ساخت کارت‌ها و روی هر اسکرول صدا زده می‌شود."""
        gen = self._cards_gen
        vis = self._visible_paths()
        unlimited = not self.store.cache_max_mb()
        # حالت سقف‌دار: کارت‌هایی که وارد دید شدند حالا job می‌گیرند
        if self._poster_pending and vis:
            moved = []
            for path, (j, ref) in list(self._poster_pending.items()):
                if path in vis:
                    del self._poster_pending[path]
                    self._waiting.setdefault(path, []).append(ref)
                    moved.append(j)
            if moved:
                self._poster_thread.enqueue(moved, gen)
                self._poster_thread.prioritize(vis, gen)
        # حالت نامحدود: صف پس‌زمینه را به نفع کارت‌های دیده‌شده بازچین کن
        if unlimited and vis:
            self._poster_thread.prioritize(vis, gen)
        # دیدِ هندسی: عناوینِ کارتِ «ناساخته»‌ی ناحیه‌ی دید هم فوری صف می‌شوند.
        # این دقیقاً سناریوی گزارش کاربر بود: «یک پوشه باز کردم و رفتم آخر،
        # تامبنیل‌ها ساخته نشد تا ۱-۲ دقیقه صبر کردم» — کارتی که هنوز جریانی
        # ساخته نشده بود نمی‌توانست اولویت بگیرد؛ حالا از روی موقعیت اسکرول
        # محاسبه و صف می‌شود. نتیجه تا رسیدن کارت در کش است و _known_pm آن را
        # هنگام ساخت کارت مستقیم برمی‌دارد.
        geo_jobs = []
        for it in self._vis_shown_items():
            pth = str(it.path)
            if pth in self._poster_started or self._known_pm(it) is not None:
                continue
            src = self.store.poster_of(it, static=True)  # شبکه‌ی کارت‌ها فریم ساکن می‌خواند (C7)
            vid = getattr(it, "preview_video", None)
            pdf = getattr(it, "preview_pdf", None)
            arc = getattr(it, "preview_archive", None)
            if not (src or vid or pdf or arc):
                continue
            self._poster_started.add(pth)
            self._poster_pending.pop(pth, None)
            geo_jobs.append((pth, str(src) if src else "",
                             str(vid) if vid else "", str(pdf) if pdf else "",
                             str(arc) if arc else ""))
        if geo_jobs:
            self._poster_thread.enqueue(geo_jobs, gen)
            self._poster_thread.prioritize({j[0] for j in geo_jobs}, gen)

    def _on_poster_loaded(self, gen: int, item_path: str, png: str, mt: int, img: QImage):
        """رسیدن پوستر آماده از نخ کارگر (در نخ اصلی — اتصال Queued خودکار Qt)."""
        if gen != self._cards_gen:                  # بازسازی کهنه — نتیجه بی‌مصرف
            return
        if item_path in self._stale_sources:
            # (C1) کاورِ این عنوان از جزئیات عوض/حذف شده — نتیجه‌ی منبعِ
            # قدیمیِ درِپرواز نباید کش/کارت را با پوسترِ قدیمی پر کند؛
            # بازسازیِ بعدی (gen تازه) job تازه با منبعِ تازه می‌سازد
            self._stale_sources.discard(item_path)
            return
        if not png or img is None:                  # شکستِ اعلام‌شده از کارگر
            self._handle_poster_fail(gen, item_path)
            return
        self._poster_attempts.pop(item_path, None)
        self._poster_pending.pop(item_path, None)
        key = (png, mt)
        pm = self._pm_get(key)
        if pm is None:
            pm = QPixmap.fromImage(img)
            if pm.isNull():
                return
            self._pm_put(key, pm)
        self._item_png[item_path] = key
        refs = self._waiting.pop(item_path, [])
        for ref in refs:
            card = ref()
            if card is not None:
                card.set_poster(pm)

    # ------------------------------------------------- بازتلاش پوسترِ شکست‌خورده
    RETRY_DELAYS = (4000, 15000)                      # backoff: ۴ثانیه، ۱۵ثانیه
    MAX_POSTER_TRIES = 1 + len(RETRY_DELAYS)

    def _handle_poster_fail(self, gen: int, path: str):
        """شکست ساخت پوستر — بازتلاش خودکار با تأخیر (نه جای‌نگه‌دار ابدی).

        گزارش کاربر: «پوشه‌ی فقط‌فشرده پوستر نمی‌گیرد ولی در جزئیات می‌آید». خیلی
        از شکست‌ها گذرا هستند (قفل آنتی‌ویروس/OneDrive روی فایل، راه‌افتادن اولِ
        7za، دیسک مشغول)؛ قبلاً هیچ خروجی از کارگر نمی‌رسید و کارت تا ابد بی‌پوستر
        می‌ماند. حالا تا ۳ بار (فوری + ۴ث + ۱۵ث) دوباره می‌سازیم و بعد تسلیم."""
        att = self._poster_attempts.get(path, 0)
        if att >= len(self.RETRY_DELAYS):
            refs = self._waiting.pop(path, [])
            self._poster_pending.pop(path, None)
            _poster_dbg(f"poster gave up after {att + 1} tries: {safe_ref(path)}")
            return
        # فقط اگر کارتی واقعاً در انتظار است (یا حالت نامحدود) بازتلاش ارزش دارد
        refs = self._waiting.get(path) or []
        alive = any(ref() is not None for ref in refs)
        limited = bool(self.store.cache_max_mb())
        if not alive and limited:
            self._poster_pending.pop(path, None)
            return
        self._poster_attempts[path] = att + 1
        delay = self.RETRY_DELAYS[att]
        QTimer.singleShot(delay, lambda p=path, g=gen: self._retry_poster(p, g))

    def _retry_poster(self, path: str, gen: int):
        """یک بازتلاش برای پوسترِ قبلاً شکست‌خورده (اگر هنوز لازم است)."""
        if gen != self._cards_gen:
            return
        if path in self._item_png:                # در این فاصله از راه دیگر ساخته شد
            return
        item = next((it for it in self.items if str(it.path) == path), None)
        if item is None:
            return
        src = self.store.poster_of(item, static=True)  # شبکه‌ی کارت‌ها فریم ساکن می‌خواند (C7)
        vid = getattr(item, "preview_video", None)
        pdf = getattr(item, "preview_pdf", None)
        arc = getattr(item, "preview_archive", None)
        if not (src or vid or pdf or arc):
            return
        self._poster_started.add(path)
        self._poster_pending.pop(path, None)
        # «1» یعنی بازتلاش: این دور کش منفیِ مراحل نادیده گرفته می‌شود تا یک خرابیِ
        # گذرا (قفل آنتی‌ویروس/راه‌افتادن اولِ 7za) تامبنیل را برای همیشه نکشد.
        job = (path, str(src) if src else "", str(vid) if vid else "",
               str(pdf) if pdf else "", str(arc) if arc else "", "1")
        self._poster_thread.enqueue([job], gen)
        self._poster_thread.prioritize({path}, gen)

    def update_item_card(self, item) -> bool:
        """به‌روزرسانی درجای کارت یک عنوان (بدون بازسازی شبکه و پرش اسکرول).

        برای ⭐/تغییر دسته/ویرایش متن — فقط همان یک کارت تازه می‌شود.
        False یعنی کارت در حوصله‌ی فعلی نیست یا ساختارش باید عوض شود → بازسازی لازم.
        """
        target = None
        for i in range(self.flow.count()):
            w = self.flow.itemAt(i).widget()
            if isinstance(w, CardWidget) and w.item.path == item.path:
                target = w
                break
        if target is None:
            return False
        view = self._view(item)
        if bool(target.view.get("subcat")) != bool(view.get("subcat")):
            return False                            # ظهور/حذف سطر زیردسته → بازسازی
        target.apply_view(view)
        return True

    def _scope_items(self):
        """عنوان‌های داخل حوصله‌ی آمار فعلی: همه / علاقه‌مندی‌ها / یک دسته (با زیردسته‌ها)."""
        if self.filter_kind == "fav":
            return [i for i in self.items if self.store.fav(i.path)], T("⭐ علاقه‌مندی‌ها")
        if self.filter_kind == "cat":
            flt = self.filter_cat
            scope = [i for i in self.items
                     if Store.matches(self.store.category_of(i), flt)]
            from i18n import disp_cat as _dcat45
            return scope, "📂 " + _dcat45(flt).replace("/", " › ")
        return list(self.items), T("📊 آمار کلی کتابخانه")

    def _update_stats(self):
        """آمار سایدبار — هوشمند بر اساس دسته‌ی انتخاب‌شده.

        روی «همه» → آمار کل کتابخانه؛ روی یک دسته یا علاقه‌مندی → فقط همان حوصله
        (تعداد عنوان/قسمت، حجم، زمان، و پیشرفت تماشا).
        """
        from scanner import human_size
        scope, label = self._scope_items()
        total_size = sum(i.size_bytes for i in scope)
        total_eps = sum(i.video_count for i in scope)
        total_dur = sum(self.store.total_duration(i) for i in scope)
        done_eps = part_eps = 0
        for i in scope:
            d, p = self.store.watch_progress(i)
            done_eps += d
            part_eps += p
        lines = [label,
                 T("🎬 {0} عنوان · {1} قسمت").format(fa(len(scope)), fa(total_eps)),
                 T("💾 حجم: {0}").format(human_size(total_size))]
        if total_dur:
            lines.append(T("⏱ زمان: {0}").format(format_duration(total_dur)))
        if done_eps or part_eps:
            watched = T("👁 {0} قسمت دیده‌شده").format(fa(done_eps))
            if total_eps:
                pct = int(round(100 * done_eps / total_eps))
                watched += T(" ({0}٪)").format(fa(pct))
            if part_eps:
                watched += T(" · ◐ {0} نیمه‌تمام").format(fa(part_eps))
            lines.append(watched)
        self.stats.setText("\n".join(lines))

    # ------------------------------------------------------------ امشب چی ببینم؟
    def _random_dialog(self):
        candidates = self._filtered()
        if not candidates:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "🎲", T("با فیلتر فعلی چیزی برای انتخاب نیست!"))
            return
        dlg = _LuckDialog(self, candidates, self)
        dlg.exec()

    # ------------------------------------------------------------ منوی کارت
    def _card_menu(self, item, gpos):
        fav = self.store.fav(item.path)
        menu = QMenu(self)
        a_open = menu.addAction(T("📄 صفحه‌ی جزئیات"))
        a_folder = menu.addAction(T("📂 باز کردن پوشه"))
        menu.addSeparator()
        a_edit = menu.addAction(T("✏ ویرایش اطلاعات"))
        # کاور سفارشی — از همین منو هم قابل تعویض/حذف است (round 11)
        a_cover = menu.addAction(T("🖼 انتخاب کاور…"))
        has_cover = bool(self.store.custom_poster(item.path)[0])
        a_clear = menu.addAction(T("↺ بازگشت به آیکون پوشه")) if has_cover else None
        a_fav = menu.addAction(T("☆ حذف از علاقه‌مندی") if fav else T("⭐ افزودن به علاقه‌مندی"))
        menu.addSeparator()
        # یک دکمه toggle — وضعیت فعلی رو بررسی می‌کنه
        done, part = self.store.watch_progress(item)
        total = self.store.watchable_total(item)
        all_done = total > 0 and done >= total
        if all_done:
            a_watch = menu.addAction(T("↺ پاک کردن وضعیت تماشا"))
        else:
            a_watch = menu.addAction(T("✓ همه دیده‌شده"))
        menu.addSeparator()
        a_copy = menu.addAction(T("📋 کپی مسیر"))
        act = menu.exec(gpos)
        menu.deleteLater()   # (round 19 §19) تجمعِ QObject در نشستِ بلند — آزادسازیِ قطعی
        if act is a_open:
            self.openDetails.emit(item)
        elif act is a_folder:
            open_path(item.path)
        elif act is a_edit:
            self.editRequested.emit(item)
        elif act is a_cover:
            from ui.cover_actions import pick_cover
            pick_cover(self, self.store, item, self._after_cover_change)
        elif a_clear is not None and act is a_clear:
            from ui.cover_actions import clear_cover
            clear_cover(self, self.store, item, self._after_cover_clear)
        elif act is a_fav:
            self.favChanged.emit(item)
        elif act is a_watch:
            self._card_watch_all(item, not all_done)
        elif act is a_copy:
            QGuiApplication.clipboard().setText(str(item.path))

    def _after_cover_change(self, changed: bool):
        """پس از تغییر کاور: کش پیکسل‌ها و کارت‌ها بازسازی می‌شوند (بدون اسکرول‌پرش)."""
        if not changed:
            return
        self.clear_pixmap_cache()        # همین حالا وجود دارد (ui/library.py)
        self._set_status_hint(T("کاور به‌روز شد ✓"))

    def _after_cover_clear(self, changed: bool):
        """پس از حذفِ کاور سفارشی: بازگشت به آیکونِ پوشه با بازسازیِ کارت‌ها."""
        if not changed:
            return
        self.clear_pixmap_cache()
        self._set_status_hint(T("کاور حذف شد"))

    def _set_status_hint(self, msg: str):
        """پیامِ کوتاه در نوار وضعیت — همان الگوی ui/details.py (بدون مسیر)."""
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage(msg, 6000)

    def _card_watch_all(self, item, mark: bool):
        """علامت‌گذاری یا پاک‌کردن همه‌ی فایل‌های قابل‌مصرف یک عنوان (0.49.0).
        0.50.0: رفع باگ import + ذخیره دسته‌ای + آپدیت درجا (بدون rebuild)."""
        try:
            from scanner import item_watchables
            from ui.details import rel_of
            files = item_watchables(item)
        except Exception as _exc_dbg:
            dbg_log(f"_card_watch_all failed: {type(_exc_dbg).__name__}")
            files = []
        if not files:
            return
        with self.store.defer_save():
            for f in files:
                r = rel_of(item, f)
                self.store.watch_set(item, r,
                                     Store.WATCH_DONE if mark else 0, 0)
        # آپدیت درجای کارت — بدون بازسازی شبکه و بدون پرش اسکرول
        if not self.update_item_card(item):
            self.itemsChanged.emit()

    def set_busy(self, busy: bool):
        for b in (self.btn_rescan, self.btn_paths, self.btn_random):
            b.setEnabled(not busy)


# ------------------------------------------------------------------ امشب چی ببینم؟

class _LuckDialog(QDialog):
    """انتخاب تصادفی وزن‌دار: نیمه‌تمام‌ها شانس بیشتر، کاملاً دیده‌شده‌ها کمتر."""

    def __init__(self, page: LibraryPage, candidates: list, parent=None):
        super().__init__(parent)
        self.page = page
        self.store = page.store
        self.candidates = list(candidates)
        self._current = None
        self._seen_ids = set()
        self.setWindowTitle(T("امشب چی ببینم؟ 🎲"))
        self.setMinimumWidth(560)

        lay = QHBoxLayout(self)
        lay.setSpacing(18)
        lay.setContentsMargins(18, 16, 18, 16)

        self.poster = QLabel()
        self.poster.setFixedSize(POSTER_W, POSTER_H)
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("H1")
        self.title.setWordWrap(True)
        right.addWidget(self.title)

        self.meta = QLabel()
        self.meta.setObjectName("MutedLabel")
        self.meta.setWordWrap(True)
        right.addWidget(self.meta)

        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setMaximumHeight(90)
        right.addWidget(self.desc, 1)

        btns = QVBoxLayout()
        btns.setSpacing(8)
        self.btn_play = QPushButton(T("▶ همین را ببین"))
        self.btn_play.setObjectName("PrimaryBtn")
        self.btn_details = QPushButton(T("📄 جزئیات"))
        self.btn_reroll = QPushButton(T("🔄 نه، یکی دیگه"))
        self.btn_close = QPushButton(T("بستن"))
        self.btn_play.clicked.connect(self._play)
        self.btn_details.clicked.connect(self._details)
        self.btn_reroll.clicked.connect(self.pick)
        self.btn_close.clicked.connect(self.reject)
        for b in (self.btn_play, self.btn_details, self.btn_reroll):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        right.addLayout(btns)
        lay.addLayout(right, 1)

        self.pick()

    # وزن هر نامزد: نیمه‌تمام ⬆ / کاملاً دیده‌شده ⬇ / بقیه متعادل
    def _weight(self, item) -> float:
        total = self.store.watchable_total(item)     # هوشمند — همهٔ دسته‌ها
        if not total:
            return 1.0
        done, part = self.store.watch_progress(item)
        if part:
            return 5.0                      # نصفه مانده — اولویت اصلی
        if done >= total and total > 0:
            return 0.25                     # کامل دیده شده — تقریباً هیچ
        if done > 0:
            return 2.5                      # شروع شده
        return 2.0                          # ندیده

    def pick(self):
        import random
        pool = [(i, self._weight(i)) for i in self.candidates
                if str(i.path) not in self._seen_ids]
        if not pool:  # همه نشان داده شدند — از نو
            self._seen_ids.clear()
            pool = [(i, self._weight(i)) for i in self.candidates]
        items, weights = zip(*pool)
        item = random.choices(items, weights=weights, k=1)[0]
        self._seen_ids.add(str(item.path))
        self._show(item)

    def _show(self, item):
        self._current = item
        st = self.store
        self.title.setText("🎬 " + st.title(item))
        cat = st.category_of(item)
        from i18n import disp_cat as _dcat45
        parts = [f"📂 {_dcat45(cat)}", f"💾 {item.size_text}"]
        if item.video_count:
            parts.append(T("🎬 {0} قسمت").format(fa(item.video_count)))
        dur = st.total_duration(item)
        if dur:
            parts.append(f"⏱ {format_duration(dur)}")
        done, part = st.watch_progress(item)
        if done or part:
            parts.append(T("👁 {0}/{1} دیده شده").format(fa(done), fa(item.video_count))
                         + (T(" · ◐ {0} نیمه‌تمام").format(fa(part)) if part else ""))
        self.meta.setText("   ·   ".join(parts))
        d = st.desc(item.path)
        self.desc.setText(d if d else T("توضیحاتی ثبت نشده."))
        self.poster.setPixmap(self.page._poster_pixmap(item))

    def _play(self):
        it = self._current
        self.accept()
        self.page.smartPlayRequested.emit(it)

    def _details(self):
        it = self._current
        self.accept()
        self.page.randomRequested.emit(it)
