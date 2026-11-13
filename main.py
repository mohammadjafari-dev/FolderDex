# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""FolderDex — نقطه‌ی ورود برنامه.

اجرا:   python main.py
"""
from __future__ import annotations

from i18n import T

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QStackedWidget, QWidget,
)

import watchtime
from appinfo import app_title
from media_info import DurationStore, fast_duration, format_duration, probe_many
from nav import NavHistory                  # تاریخچه‌ی ناوبری (دکمه‌های کناری موس)
from prebuild import Prebuilder
from scanner import fa, fa_mmss, item_videos, item_watchables, scan_roots
from store import Store
from thumbs import ThumbnailCache, cache_px_for_app
# خروجی HTML به درخواست کاربر کامل حذف شد (تصمیمِ طراحی — «الکی وقت روش نزاریم»)
from qss import build_theme, theme_bg, theme_label
from ui.details import DetailsPage, rel_of
from ui.dialogs import ItemEditDialog, SettingsDialog
from ui.library import LibraryPage
from ui.splash import SplashScreen
from ui.widgets import open_path

from scanner import VIDEO_EXTS

from portable import app_dir as _portable_app_dir
# BUILD/exe: در حالت PyInstaller داده‌ها کنار exe و منابع داخل بسته‌اند؛
# app_dir() در هر دو حالت درست است (سورس: همین پوشه؛ exe: پوشه‌ی اجرایی).
APP_DIR = _portable_app_dir()

# درخواست کاربر: «همه‌چیز کنار برنامه؛ هیچ‌چیز در مسیر سراسری ذخیره
# نشه» — مدلِ 0.34.0–0.38.0 (پوشه‌ی سراسریِ ویندوز) به تصمیمِ کاربر برگردانده شد.
# در اولین اجرای 0.39.0 داده‌های مسیرِ سراسریِ قدیمی — اگر هنوز آنجا باشند —
# یک‌بار به کنار برنامه *منتقل* می‌شوند (نه کپی) و پوشه‌ی خالیِ سراسری هم حذف
# می‌شود. انتقالِ داده بین نسخه‌ها از این‌پس با دکمه‌های «خروجی/ورود اطلاعات»
# در تنظیمات انجام می‌شود (backup.py — درخواستِ خودِ کاربر).
import paths as _paths                                  # noqa: E402
from watchtime import dbg_log
_moved = _paths.migrate_shared_to_app()
CONFIG_PATH = _paths.default_config_path()
CACHE_DIR = _paths.thumb_cache_dir()
if _moved:
    try:
        watchtime._dbg("shared→app migration: " + ", ".join(_moved))
    except Exception as _exc_dbg:
        dbg_log(f"main.<module> failed: {type(_exc_dbg).__name__}")
        pass


class ScanWorker(QThread):
    progressed = Signal(str)
    counts = Signal(str, int, int)   # (فاز، انجام‌شده، کل) — 0.23.0: نوار پیشرفتِ واقعی اسکن
    finished_items = Signal(object)

    def __init__(self, store: Store, cache: ThumbnailCache,
                 dstore: DurationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        self.dstore = dstore   # همان نمونه‌ی مشترک صفحه‌ی جزئیات — بدون پاک‌شدن کش

    def run(self):
        try:
            items = scan_roots(
                self.store.roots(),
                include_image_fallback=self.store.include_image_fallback,
                progress=self.progressed.emit,
                counts=lambda d, t: self.counts.emit("scan", d, t),
            )
            # درخواست کاربر: «برنامه همیشه قابل استفاده باشه» — محاسبه‌ی
            # زمان ویدیوها دیگر داخل اسکن نیست — پنجره بلافاصله بعد از اسکن با
            # پوسترها باز می‌شود و زمان‌ها با DurationWorker در پس‌زمینه حساب
            # می‌شوند (نوار وضعیت: «در حال محاسبه‌ی زمان…»).
            durations = None
        except Exception as e:  # هر خطا = اسکن ناموفق، نه گیر کردن اسپلش
            dbg_log(f"run failed: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            self.progressed.emit(T("⚠ خطای اسکن: {0}").format(e))
            items, durations = [], None
        self.finished_items.emit((items, durations))


class DurationWorker(QThread):
    """محاسبه‌ی زمانِ ویدیوها در پس‌زمینه — بدون معطل‌کردنِ باز شدن برنامه (0.31.0).

    قبلاً این کار داخل نخِ اسکن و *قبل* از نمایش پنجره انجام می‌شد («چند دقیقه
    صبر کن تا برنامه بیاد بالا»). حالا: اسکن فقط عنوان‌ها را می‌آورد، پنجره و
    پوسترها بلافاصله دیده می‌شوند، و این کارگر زمان‌ها را با مانیفست (پوشه‌های
    بدون‌تغییر رد می‌شوند) در پس‌زمینه حساب می‌کند؛ پیشرفتش با متنِ
    «⏱ در حال محاسبه‌ی زمان…» در نوار وضعیت دیده می‌شود و کارت‌ها با اتمامش
    به‌روز می‌شوند.
    """

    progressed = Signal(str)
    counts = Signal(str, int, int)          # ("durations", انجام‌شده، کل)
    totals_ready = Signal(object)           # (totals, manifests) یا None

    def __init__(self, store: Store, dstore: DurationStore, items: list, parent=None):
        super().__init__(parent)
        self.store = store
        self.dstore = dstore
        self.items = list(items)
        self._abort = False

    def stop(self):
        self._abort = True

    def _item_manifest(self, videos) -> dict:
        """اثر انگشت ویدیوهای یک عنوان: {مسیر نسبی: [size, mtime_ns]} — پایدار بین اجراها.

        (round 19 §۱۴) قبلاً ثانیهٔ کامل ذخیره می‌شد؛ جایگزینیِ فایل با فایلِ
        هم‌اندازه در *همان ثانیه* «بدون تغییر» دیده می‌شد. حالا نانوثانیه
        ذخیره/مقایسه می‌شود — مانیفست‌های قدیمیِ ثانیه‌ای یک‌بار نامطابق می‌شوند
        و فقط همان یک‌بار از کشِ durations دوباره پر می‌شوند (بدونِ پروبِ جدید)."""
        man: dict[str, list] = {}
        for vf in videos:
            try:
                st = vf.stat()
                rel = str(vf.relative_to(self._manifest_base)).replace("\\", "/") \
                    if self._manifest_base else vf.name
                man[rel] = [st.st_size, int(st.st_mtime_ns)]
            except OSError:
                continue
        return man

    def _video_durations(self, items):
        """مجموع زمان هر عنوان با مانیفست: اگر پوشه تغییر نکرده، هیچ محاسبه‌ای نمی‌شود."""
        totals: dict[str, int] = {}
        manifests: dict[str, dict] = {}
        to_probe: dict[str, list] = {}       # فقط عناوینِ تغییرکرده/تازه
        skipped = 0
        for item in items:
            if self._abort:
                return None
            key = str(item.path)
            self._manifest_base = item.path if not item.path.is_file() else item.path.parent
            videos = item_videos(item)
            man = self._item_manifest(videos)
            old_man = self.store.manifest_of(item)
            old_total = self.store.total_duration(item)
            if old_man is not None and old_man == man and (
                    (videos and old_total > 0) or not videos):
                totals[key] = old_total
                manifests[key] = man
                skipped += 1
                continue
            totals[key] = 0
            manifests[key] = man
            to_probe[key] = [vf for vf in videos if self.dstore.get(vf) is None]
        if skipped:
            self.progressed.emit(T("⏱ {0} عنوان بدون تغییر — محاسبه‌ی مجدد ندارند").format(fa(skipped)))

        # فایل‌های نیازمند پروب (فقط ناموجود در کش) — موازی
        flat: list = []
        for key, vids in to_probe.items():
            flat.extend(vids)
        if flat:
            self.progressed.emit(T("⏱ خواندن زمان {0} ویدیوی جدید…").format(fa(len(flat))))
            last = [0.0]

            def _prog(d, t):
                # throttle: حداکثر ~۷ بار در ثانیه پیام به UI (مهم برای هزاران فایل)
                now = time.monotonic()
                if d == t or now - last[0] >= 0.15:
                    last[0] = now
                    # درخواست کاربر: متنِ صریحِ «در حال محاسبه‌ی زمان»
                    self.progressed.emit(T("⏱ در حال محاسبه‌ی زمان… {0} از {1}").format(fa(d), fa(t)))
                    self.counts.emit("durations", d, t)   # نوار پیشرفت واقعی (0.23.0)

            # هدر MKV/MP4/AVI/WMV مستقیم و خیلی سریع خوانده می‌شود (بدون subprocess)؛
            # ffprobe فقط برای فرمت‌های باقی‌مانده استفاده می‌شود.
            # ماندیتِ سرعتِ کاربر: ورکرها دیگر ثابت نیستند؛ روی HDD
            # محافظه‌کار و روی SSD/NVMe با تمام توان (همان منطقِ hwinfo).
            try:
                import hwinfo as _hw39
                _w39 = _hw39.video_workers(
                    _hw39.any_rotational([str(x) for x in flat[:150]]))
            except Exception as _exc_dbg:
                dbg_log(f"_video_durations failed: {type(_exc_dbg).__name__}")
                _w39 = 16
            probe_many(flat, self.dstore, workers=_w39, progress=_prog)
        # جمع‌بندی نهایی از کش (سریع — بدون پروب)
        for item in items:
            key = str(item.path)
            if totals.get(key):
                continue
            total = 0
            for vf in item_videos(item):
                v = self.dstore.get(vf)
                if v and v > 0:
                    total += v
            totals[key] = int(total)
        if self._abort:
            return None
        self.dstore.save()
        return totals, manifests

    def run(self):
        res = None
        try:
            if self.store.compute_durations and self.items and not self._abort:
                res = self._video_durations(self.items)
        except Exception as e:  # خطای محاسبه‌ی زمان نباید برنامه را بکشد
            try:
                from watchtime import _dbg
                _dbg(f"duration-worker error: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                pass
            res = None
        self.totals_ready.emit(res)


class _ProgressChip(QWidget):
    """ترکیب «نوار باریک + برچسبِ جدا» برای نوار وضعیت (نسخه‌ی 0.23.0).

    گزارش کاربر: متنِ داخل خودِ نوارِ ۱۲پیکسلی نصفه برش می‌خورد («پروگرس‌بار
    کامل دیده نمی‌شود»). حالا نوار فقط درصدِ بصری است و متنِ کاملِ فارسی —
    شمارش + «گذشته … · مانده …» — در یک QLabel جدا با ارتفاعِ طبیعی دیده
    می‌شود. جهت داخلی چپ‌به‌راست نگه می‌ماند تا چیدمان بار/متن همیشه ثابت باشد.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(8)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(170)
        self.bar.setMinimumHeight(10)
        self.bar.setMaximumHeight(10)
        self.lbl = QLabel()
        from i18n import is_rtl as _rtl45     # برچسبِ پیشرفت بر اساس زبان
        self.lbl.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if _rtl45()
            else Qt.LayoutDirection.LeftToRight)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl)

    def reset(self, t_max: int, text: str):
        self.bar.setRange(0, max(1, t_max))
        self.bar.setValue(0)
        self.lbl.setText(text)

    def update(self, done: int, total: int, text: str):
        self.bar.setMaximum(max(1, int(total)))
        self.bar.setValue(max(0, min(int(done), int(total))))
        self.lbl.setText(text)


def _arm_exit_watchdog(seconds: float = 4.0):
    """واچ‌دگ خروجِ قطعی (0.42.0) — جدا از closeEvent تا در تست‌ها امن غیرفعال شود.

    اگر رهاکردنِ پروسه‌ی قدیمی (ماندنِ نخ‌های زمینه / حلقه‌های رویدادِ تودرتو)
    مثل گذشته ناقص بماند، این تایمر پروسه را به‌زور می‌بندد تا هرگز «دو نمونه‌ی
    هم‌زمانِ باز» دیده نشود (گزارش کاربر بعد از ورودِ فایل پشتیبان)."""
    try:
        import os as _os
        _wt = threading.Timer(seconds, lambda: _os._exit(0))
        _wt.daemon = True
        _wt.start()
    except Exception as _exc_dbg:
        dbg_log(f"_arm_exit_watchdog failed: {type(_exc_dbg).__name__}")
        pass


class MainWindow(QMainWindow):
    # پاک‌شدنِ کش به‌خاطرِ سقفِ حجم (LRU) روی نخ پس‌زمینه اتفاق می‌افتد؛
    # با سیگنال به نخ UI می‌آید تا مانیفستِ تامبنیل‌ها هم بی‌اعتبار شود.
    cache_pruned = Signal(int)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        # درخواست کاربر: کش همیشه کنار برنامه است — چیزی در مسیرِ سراسری
        # ذخیره نمی‌شود و دیگر گزینه‌ای هم لازم نیست.
        self.cache_dir = CACHE_DIR
        # اندازه‌ی کش آگاه از مقیاس نمایش ویندوز (۱۰۰٪←۲۵۶، ۱۵۰٪←۳۸۴، ۲۰۰٪←۵۱۲)
        # تا پوسترها در اسکیل‌های بالا نرم/کشیده دیده نشوند — همان قالبِ کلیدِ کش
        # هم این اندازه را دارد، پس تغییرِ اسکیل فقط بازسازیِ طبیعیِ کش می‌آورد.
        cache_px = cache_px_for_app(QApplication.instance())
        self.cache_px = cache_px
        self.cache = ThumbnailCache(self.cache_dir, size=(cache_px, cache_px))
        self.worker: ScanWorker | None = None
        self.items = []

        self.setWindowTitle(app_title())            # عنوان بر اساس زبان
        self.setMinimumSize(1000, 680)
        self.resize(1240, 800)
        icon_file = APP_DIR / "assets" / "icon.png"
        if icon_file.exists():
            self.setWindowIcon(QIcon(str(icon_file)))

        self.dstore = DurationStore(self.cache_dir)   # نمونه‌ی مشترک — اسکنر و جزئیات
        # پیش‌ساز تامبنیل در پس‌زمینه (گزینه‌ی تنظیمات — درخواست کاربر)
        self.prebuilder = Prebuilder(self.store, self.cache)
        self.duration_worker: DurationWorker | None = None   # زمان‌ها در پس‌زمینه
        # گزارش کاربر: «بعد از ورود پشتیبان برنامه‌ی قبلی باز می‌ماند» —
        # ریلانچ فقط با این پرچم و از مسیرِ تمیزِ closeEvent انجام می‌شود —
        # نه از دلِ دیالوگِ مودال (حلقه‌های رویدادِ تودرتو بستن را ناقص می‌کردند).
        self._restart_after_import = False
        self.cache_pruned.connect(self._on_cache_pruned)

        self.stack = QStackedWidget()
        self.library = LibraryPage(store, self.cache, cache_px=self.cache_px)
        self.details = DetailsPage(store, self.cache, durations=self.dstore,
                                   cache_px=self.cache_px)
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.details)
        self.setCentralWidget(self.stack)

        self.status_lbl = QLabel(T("آماده ✨"))
        # پیامِ یک‌باره‌ی فال‌بکِ فقط‌خواندنی (D2): کش در ‎%TEMP%‎ است و دائمی نیست
        if getattr(self.cache, "read_only_fallback", False) or \
                getattr(self.dstore, "read_only_fallback", False):
            self.status_lbl.setText(
                T("پوشه‌ی برنامه فقط‌خواندنی است — کش موقتاً در پوشه‌ی Temp ویندوز ساخته شد"))
        self.statusBar().addWidget(self.status_lbl, 1)
        # هر دو نوار به ترکیب «برچسب جدا + نوار باریک» تبدیل شدند —
        # متن فارسی دیگر در نوارِ کوتاه برش نمی‌خورد (گزارش کاربر)؛ اسکن هم
        # از حالت نامعین به شمارش واقعی رسید و هر دو «گذشته/مانده» نشان می‌دهند.
        self.scan_chip = _ProgressChip()
        self.scan_chip.hide()
        self.statusBar().addPermanentWidget(self.scan_chip)
        self.pb_chip = _ProgressChip()
        self.pb_chip.hide()
        self.statusBar().addPermanentWidget(self.pb_chip)
        self._scan_track = {"phase": "", "t0": 0.0}   # ریست تایمر ETA در هر فاز
        self._pb_track = {"phase": "", "t0": 0.0}
        self._scan_t0 = 0.0
        self.splash: SplashScreen | None = None

        # رله‌ی امنِ پیشرفت پیش‌ساز تامبنیل از نخ پس‌زمینه به نوار وضعیت —
        # گزارش کاربر: «گزینه‌ی پیش‌ساخت کار نمی‌کند» یعنی اصلاً معلوم نبود چه
        # زمانی در حال اجراست؛ حالا درصد/شمارش کار زنده دیده می‌شود.
        class _PbRelay(QObject):
            text = Signal(str)                     # متن آزاد → نوار وضعیت
            counts = Signal(str, int, int)         # (فاز، ساخته‌شده، کل) → نوار پیشرفت
        self._pb_relay = _PbRelay(self)
        self._pb_relay.text.connect(self.status_lbl.setText)
        self._pb_relay.counts.connect(self._on_prebuild_counts)
        self.prebuilder.on_progress = self._pb_relay.text.emit
        self.prebuilder.on_counts = self._pb_relay.counts.emit

        # سیگنال‌ها
        self.library.openDetails.connect(self.show_details)
        self.library.rescanClicked.connect(self.start_scan)
        self.library.pathsClicked.connect(self.manage_paths)
        self.library.editRequested.connect(self.edit_item)
        self.library.favChanged.connect(self.toggle_fav)
        self.library.themeChanged.connect(self.apply_theme)
        self.library.randomRequested.connect(self.random_pick)
        self.library.smartPlayRequested.connect(self.smart_play)
        self.details.backRequested.connect(self._back_to_library)
        self.details.changed.connect(self.refresh_library)
        # (C1) کاور از جزئیات عوض/حذف شد → کشِ پوسترِ همان کارت بی‌اعتبار شود
        self.details.coverChanged.connect(self._on_cover_changed)
        self.library.subcatChanged.connect(self.change_subcat)
        self.library.itemsChanged.connect(self.refresh_library)
        self.details.playRequested.connect(self.play_media)
        self._lib_dirty = False

        # ردیاب مرکزی پخش‌ها — مستقل از صفحات؛ با بستن صفحه/برنامه چیزی گم نمی‌شود
        self.tracker = watchtime.PlayTracker(self.store, rel_of)
        self.tracker.on_update = self._on_watch_tracked
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(700)   # تیک سریع‌تر — ثبت/تعویض قسمت بی‌درنگ احساس شود
        self._track_timer.timeout.connect(self._track_poll)
        self._last_lib_ping = 0.0

        # محدودیت حجم کش (اگر کاربر سقف گذاشته) — بعد از بالا آمدن رابط، در نخ جدا
        QTimer.singleShot(2500, self._enforce_cache_limit)

        # (C8) پایشِ پرچمِ شکستِ ذخیره‌ی دیتابیس — شکستِ SQLite دیگر بی‌صدا
        # نماند؛ کاربر در نوار وضعیت «ذخیره‌ی اطلاعات ممکن نشد» را ببیند
        self._save_warn_shown = False
        self._save_watch = QTimer(self)
        self._save_watch.setInterval(10000)
        self._save_watch.timeout.connect(self._check_save_failed)
        self._save_watch.start()

        # درخواست کاربر: ناوبری با دکمه‌های کناری موس (◀ Back / ▶ Forward) —
        # تاریخچه‌ی مرورگریِ «کتابخانه(دسته) ↔ جزئیات(عنوان)»؛ اتصالِ اضافی به
        # انتخابِ سایدبار *بعد* از هندلرِ داخلی کتابخانه اجرا می‌شود (ترتیب اتصال).
        self._nav = NavHistory()
        self._nav_replay = False
        self._nav.record(("lib", "all", "", ""))
        self.library.cats.currentItemChanged.connect(self._nav_on_side)
        _app47 = QApplication.instance()
        if _app47 is not None:
            _app47.installEventFilter(self)

        # درخواست کاربر: میان‌برهای صفحه‌کلید — مکمل دکمه‌های کناری موس.
        # در فیلدهای متنی خودکار خاموش‌اند (_sc_focus_guard) تا تایپ خراب نشود.
        try:
            from PySide6.QtGui import QKeySequence as _KS48, QShortcut as _SC48
            _SC48(_KS48("Alt+Left"), self, activated=lambda: self._sc_nav(True))
            _SC48(_KS48("Alt+Right"), self, activated=lambda: self._sc_nav(False))
            _SC48(_KS48(Qt.Key.Key_Space), self, activated=self._sc_space)
            _SC48(_KS48("W"), self, activated=self._sc_watched)
            _SC48(_KS48("F"), self, activated=self._sc_fav)
        except Exception as _exc_dbg:
            dbg_log(f"__init__ failed: {type(_exc_dbg).__name__}")
            pass

        # درخواست کاربر: وضعیتِ «پشتیبان‌گیری خودکارِ هوشمند» — فقط هنگام
        # تغییرِ واقعی بازنویسی می‌شود (شمارنده‌های store.data_rev/cfg_rev).
        self._ab_last_rev = -1
        self._ab_last_cfg_rev = -1
        self._ab_running = False
        self._ab_last_t = 0.0
        self._ab_pending = False          # فعال‌سازی در انتظارِ پایانِ پیش‌ساخت
        self._pb_active = False           # پیش‌ساز تامبنیل در حال کار است؟

    # ------------------------------------------------------ ناوبری موس
    def _nav_push(self, state):
        if getattr(self, "_nav_replay", False):
            return
        try:
            self._nav.record(state)
        except Exception as _exc_dbg:
            dbg_log(f"_nav_push failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_on_side(self, *_a):
        """انتخابِ دسته در سایدبار — فقط وقتی کتابخانه نمایان است و در بازپخش نیستیم."""
        if getattr(self, "_nav_replay", False):
            return
        try:
            if self.stack.currentWidget() is not self.library:
                return
            self._nav.record(("lib", self.library.filter_kind,
                              self.library.filter_cat, ""))
        except Exception as _exc_dbg:
            dbg_log(f"_nav_on_side failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_go(self, back: bool):
        try:
            state = self._nav.back() if back else self._nav.forward()
        except Exception as _exc_dbg:
            dbg_log(f"_nav_go failed: {type(_exc_dbg).__name__}")
            state = None
        if state:
            self._nav_apply(state)

    def _nav_apply(self, state):
        """اعمالِ یک وضعیتِ تاریخچه — بدون ثبتِ دوباره (پرچمِ بازپخش)."""
        page, kind, cat, item_path = state
        self._nav_replay = True
        try:
            if page == "det" and item_path:
                target = None
                for it in (self.items or []):
                    if str(getattr(it, "path", "")) == item_path:
                        target = it
                        break
                if target is not None:
                    # set_item خودش صف‌های عنوانِ قبلی را جمع می‌کند
                    if self.stack.currentWidget() is not self.details \
                            or self.details.item is not target:
                        self.show_details(target)      # ضدثبت با پرچمِ بازپخش
                    return
                # عنوان دیگر در اسکنِ فعلی نیست (حذف/جابه‌جا شده) → کتابخانه
                page, kind, cat = "lib", "all", ""
            if self.stack.currentWidget() is not self.library:
                self._back_to_library()                  # رفرشِ عَلیل هم درونش است
            self._select_side_filter(kind, cat)
        finally:
            self._nav_replay = False

    def _select_side_filter(self, kind: str, cat: str):
        """انتخابِ گرهِ سایدبار مطابق (kind, cat)؛ در نبودِ گره → «🏠 همه»."""
        try:
            tree = self.library.cats
            node = None
            it = self.library._tree_iter()
            while (li := it.value()) is not None:
                if li.data(0, Qt.ItemDataRole.UserRole) == (kind, cat):
                    node = li
                    break
                it += 1
            if node is None and tree.topLevelItemCount():
                node = tree.topLevelItem(0)
            if node is not None:
                tree.setCurrentItem(node)
        except Exception as _exc_dbg:
            dbg_log(f"_select_side_filter failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_busy_context(self) -> bool:
        """دیالوگِ مودال باز است؟ (پروگرس‌بار بکاپ/تنظیمات/پرسش‌ها) → دکمه‌ها کار نکنند."""
        try:
            app = QApplication.instance()
            return bool(app is not None and app.activeModalWidget() is not None)
        except Exception as _exc_dbg:
            dbg_log(f"_nav_busy_context failed: {type(_exc_dbg).__name__}")
            return False

    def eventFilter(self, obj, event):
        """مصرفِ سراسری دکمه‌های کناری موس (XButton1/XButton2) — 0.47.0.

        روی Press عمل می‌شود؛ Release هم مصرف می‌شود تا ویجتِ زیر نشان دوباره
        رویداد را تفسیر نکند. وقتی دیالوگِ مودال باز است هیچ دخالتی نمی‌شود."""
        et = event.type()
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            xb = getattr(Qt.MouseButton, "BackButton", None)
            xf = getattr(Qt.MouseButton, "ForwardButton", None)
            try:
                b = event.button()
            except Exception as _exc_dbg:
                dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
                b = None
            if b is not None and xb is not None and b in (xb, xf):
                if et is QEvent.Type.MouseButtonRelease:
                    return True
                if not self._nav_busy_context():
                    if b is xb and self._nav.can_back():
                        self._nav_go(True)
                    elif b is xf and self._nav.can_forward():
                        self._nav_go(False)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------ میان‌برها
    def _sc_focus_guard(self) -> bool:
        """وقتی کاربر در حال تایپ است میان‌برها دخالت نکنند."""
        try:
            from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit
            w = QApplication.focusWidget()
            return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))
        except Exception as _exc_dbg:
            dbg_log(f"_sc_focus_guard failed: {type(_exc_dbg).__name__}")
            return False

    def _sc_nav(self, back: bool):
        if self._nav_busy_context():
            return
        if back and self._nav.can_back():
            self._nav_go(True)
        elif not back and self._nav.can_forward():
            self._nav_go(False)

    def _sc_space(self):
        """Space روی صفحه‌ی جزئیات = پخش هوشمند (مثل دکمه‌ی ▶)."""
        if self._sc_focus_guard():
            return
        if self.stack.currentWidget() is self.details:
            self.details.play_smart()

    def _sc_watched(self):
        """W روی صفحه‌ی جزئیات = همه‌ی قسمت‌ها دیده‌شده ↔ پاک‌کردنِ وضعیت."""
        if self._sc_focus_guard() or self.stack.currentWidget() is not self.details:
            return
        item = self.details.item
        if item is None:
            return
        try:
            # هوشمند: ویدیوها اگر هستند — وگرنه فایل‌های قابل‌مصرفِ دیگر
            # (کتاب/مانگای فشرده/تصویر/…)؛ زیرنویس هرگز جزو کل نیست.
            vids = item_watchables(item)
        except Exception as _exc_dbg:
            dbg_log(f"_sc_watched failed: {type(_exc_dbg).__name__}")
            vids = []
        if not vids:
            return
        done, _part = self.store.watch_progress(item)   # خروجی: (دیده‌شده، نیمه‌تمام)
        total = len(vids)                               # مجموع واقعی = قابل‌مصرف‌های روی دیسک
        make = not (total > 0 and done >= total)
        for f in vids:
            self.store.watch_set(item, rel_of(item, f),
                                 Store.WATCH_DONE if make else 0)
        self.refresh_library()
        try:
            for f in vids:
                self.details.refresh_watch(rel_of(item, f))
        except Exception as _exc_dbg:
            dbg_log(f"_sc_watched failed: {type(_exc_dbg).__name__}")
            pass
        self.statusBar().showMessage(
            T("✓ همه‌ی قسمت‌های «{0}» دیده‌شده ثبت شد").format(self.store.title(item))
            if make else
            T("○ وضعیت تماشای «{0}» پاک شد").format(self.store.title(item)), 7000)

    def _sc_fav(self):
        """F روی صفحه‌ی جزئیات = علاقه‌مندی ↔ حذف از علاقه‌مندی."""
        if self._sc_focus_guard() or self.stack.currentWidget() is not self.details:
            return
        if self.details.item is not None:
            self.details._toggle_fav()

    # ------------------------------------- پشتیبان‌گیری خودکارِ هوشمند
    def _maybe_auto_backup(self, trigger: str = "", force: bool = False):
        """فایلِ بکاپ فقط وقتی بازنویسی می‌شود که داده/تنظیم واقعاً عوض شده باشد
        («فقط وقتی تغییری میکنه فایل تغییر میده» — درخواست کاربر). سبک (بدون
        کش)، اتمیک، در نخِ پس‌زمینه؛ هرگز رابط را قفل نمی‌کند.
        force=True (فعال‌سازی گزینه / اتمامِ پیش‌ساختِ معلق‌شده): چکِ بازبینی و
        سقفِ دقیقه‌ای دور زده می‌شود تا فایلِ تازه بلافاصله ساخته شود — حتی اگر
        از زمانِ فعال‌کردن هنوز هیچ داده‌ای عوض نشده باشد (بازبینیِ کاربر)."""
        try:
            cfg = self.store.auto_backup_cfg()
            path = str(cfg.get("path") or "")
            if not cfg.get("enabled") or not path or self._ab_running:
                return
            rev = (self.store.data_rev, self.store.cfg_rev)
            if (not force and trigger != "exit"
                    and rev == (self._ab_last_rev, self._ab_last_cfg_rev)):
                return
            now = time.monotonic()
            if not force and trigger != "exit" and now - self._ab_last_t < 60:
                return                                   # حداکثر دقیقه‌ای یک‌بار
            self._ab_last_t = now
            self._ab_last_rev, self._ab_last_cfg_rev = rev
            self._ab_running = True

            def _job48(_p=path, _inc=bool(cfg.get("include_cache"))):  # noqa: B008 — snap-shot عمدیِ هنگامِ تعریف
                try:
                    import backup as _bk48
                    # بازبینی کاربر: انتخابِ خودِ کاربر — ساده (بدون کش) یا
                    # کامل (با تامبنیل‌ها)؛ پیش‌فرض مرسوم = ساده/سبک.
                    _bk48.export_backup(_p, include_cache=_inc, atomic=True)
                    try:
                        self._pb_relay.text.emit(T("🗄 پشتیبانِ خودکار به‌روزرسانی شد ✔"))
                    except Exception as _exc_dbg:
                        dbg_log(f"_maybe_auto_backup._job48 failed: {type(_exc_dbg).__name__}")
                        pass
                except Exception as e:
                    try:
                        watchtime._dbg(f"auto-backup error: {type(e).__name__}")
                    except Exception as _exc_dbg:
                        dbg_log(f"_maybe_auto_backup._job48 failed: {type(_exc_dbg).__name__}")
                        pass
                finally:
                    self._ab_running = False

            threading.Thread(target=_job48, daemon=True).start()
        except Exception as _exc_dbg:
            dbg_log(f"_maybe_auto_backup failed: {type(_exc_dbg).__name__}")
            self._ab_running = False

    def _queue_auto_backup(self, force: bool = False):
        """فعال‌سازی/تغییر مسیر پشتیبانِ خودکار از تنظیمات (0.48.0 — بازبینی کاربر:
        «وقتی پشتیبان گیری میزنیم اگه در حال ساخت تامبنیل نبود اطلاعات اضافه
        میکنه اگه بود صبر میکنه تموم بشه بعد اضافه میکنه»). پیش‌ساز جاری است؟
        پرچمِ معلق می‌ماند تا done بیاید؛ وگرنه فایل همین لحظه نوشته می‌شود."""
        try:
            self._ab_pending = bool(self._ab_pending or force)
            if self._pb_active or not self._ab_pending:
                return
            self._ab_pending = False
            self._maybe_auto_backup("enable", force=True)
        except Exception as _exc_dbg:
            dbg_log(f"_queue_auto_backup failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------- موارد گمشده زیر ریشه‌ها
    def _check_missing_items(self):
        """عنوان‌هایی که زیر ریشه‌های فعلی‌اند ولی فایلشان دیگر نیست — دیالوگِ
        «حذف / مسیر جدید» (درخواست کاربر). فقط وقتی پنجره دیده می‌شود (در تست‌های
        نامرئی هرگز پاپ‌آپ نیاید) و حداکثر یک‌بار در هر اجرا."""
        try:
            if not self.isVisible():
                return
            if getattr(self, "_miss_shown_run", False):
                return
            import os as _os48
            roots = [str(r.get("path") or "") for r in self.store.roots()]
            roots = [r for r in roots if r]
            if not roots:
                return

            def _under48(p, r):
                p_ = _os48.path.normcase(str(p).replace("/", "\\"))
                r_ = _os48.path.normcase(str(r).replace("/", "\\")).rstrip("\\")
                return p_ == r_ or p_.startswith(r_ + "\\")

            miss = []
            for p in list((self.store.data.get("items") or {}).keys()):
                try:
                    if any(_under48(p, r) for r in roots) and not _os48.path.exists(p):
                        miss.append(p)
                except OSError:
                    continue
            if not miss:
                return
            self._miss_shown_run = True
            QTimer.singleShot(1500, lambda: self._open_missing_dialog(miss))
        except Exception as _exc_dbg:
            dbg_log(f"_check_missing_items failed: {type(_exc_dbg).__name__}")
            pass

    def _open_missing_dialog(self, miss: list):
        try:
            from ui.dialogs import MissingItemsDialog
            before = len((self.store.data.get("items") or {}))
            MissingItemsDialog(self.store, miss, self).exec()
            after = len((self.store.data.get("items") or {}))
            if after != before:
                self.statusBar().showMessage(T("تغییراتِ موارد گمشده اعمال شد ✔"), 6000)
                if self.worker is None or not self.worker.isRunning():
                    self.start_scan()                # کتابخانه با حذف/انتقال‌ها همگام شود
        except Exception as _exc_dbg:
            dbg_log(f"_open_missing_dialog failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ پخش ردیابی‌شده
    def play_media(self, item, f):
        """پخش یک ویدیو با پخش‌کننده‌ی تنظیم‌شده + ثبت خودکار نتیجه‌ی تماشا.

        جریان: تعیین پلیر ← موقعیت ذخیره‌شده ← لانچ ← ثبت در PlayTracker.
        نتیجه‌های بعدی (لایو/نهایی) از مسیر _on_watch_tracked می‌آیند.
        """
        f = Path(f)
        if f.suffix.lower() not in VIDEO_EXTS:
            open_path(f)
            return
        if self.tracker.has_media(f):
            self.statusBar().showMessage(
                T("▶ «{0}» همین‌الان هم در حال پخش است").format(f.name), 6000)
            return
        self.store.add_recent(str(item.path))            # «🕘 موارد اخیر»
        player = watchtime.resolve_player(self.store.player_cfg)
        rel = rel_of(item, f)
        mode0, pos = self.store.watch_get(item, rel)
        if mode0 == Store.WATCH_DONE:
            pos = 0.0                    # دیده‌شده → از ابتدا
        # مدت‌زمان باید «همین فایل» باشد — هرگز مجموع زمان کل سریال!
        # (باگ قدیمی: اگر مدت فایل نامعلوم بود به total_duration عنوان برمی‌گشتیم و
        # برای مثال قسمتی که ۱۰۰٪ دیده شده بود «۲۰ دقیقه دیده شده» ثبت می‌شد.)
        dur = self.details._dur_known.get(str(f)) or 0
        if dur <= 0:
            v_dstore = self.dstore.get(f)         # None=محاسبه‌نشده · -1=نامشخص
            if v_dstore and v_dstore > 0:
                dur = v_dstore
        if dur <= 0:
            # پروب سریع هدر همین فایل (چند میلی‌ثانیه برای MKV/MP4/AVI/WMV)
            v_probe = fast_duration(f)
            if v_probe and v_probe > 0:
                dur = v_probe
                try:
                    self.dstore.set(f, v_probe)
                    self.dstore.save()
                except Exception as _exc_dbg:
                    dbg_log(f"play_media failed: {type(_exc_dbg).__name__}")
                    pass
        if dur <= 0:
            dur = 0.0                              # نامشخص — قواعد درصد بر مبنای پلیر می‌آیند
        kind = player["kind"]
        note = (player.get("note") or "")
        prefix = (note + " · ") if note else ""
        name = Path(player["path"]).name if player.get("path") else ""

        wp = None
        track_label = ""
        if kind == "mpv" and player.get("path"):
            wp = watchtime.launch_mpv(player["path"], f, pos, self.cache.cache_dir)
            track_label = T("ردیابی زنده و دقیق فعال است — موقعیت هر چند ثانیه ذخیره می‌شود")
        elif kind == "potplayer" and player.get("path"):
            wp = watchtime.launch_potplayer(player["path"], f, pos)
            track_label = (T("ردیابی واقعی فعال است — موقعیت از خود پنجره‌ی PotPlayer "
                           "خوانده می‌شود (سیک هم لحاظ است)"))
        elif kind == "vlc" and player.get("path"):
            wp = watchtime.launch_vlc(player["path"], f, pos)
            track_label = T("ردیابی تخمینی فعال است (زمان پخش مداوم) — برای دقت کامل mpv/PotPlayer")
        elif kind == "elapsed" and player.get("path"):
            wp = watchtime.launch_elapsed(player["path"], f, pos)
            track_label = T("ردیابی تخمینی فعال است (زمان پخش مداوم)")

        if wp is not None:
            self.tracker.add(wp, item, rel, dur, kind)
            self._track_timer.start()
            resume_txt = (T(" · ادامه از {0}").format(format_duration(pos)) if pos > 5 else "")
            self.statusBar().showMessage(
                prefix + T("▶ پخش «{0}» با {1}{2} — {3}").format(f.name, name, resume_txt, track_label), 8000)
            return
        # ---- بدون ردیابی: پخش‌کننده‌ی پیش‌فرض سیستم ----
        self.statusBar().showMessage(
            prefix + T("▶ پخش با پخش‌کننده‌ی پیش‌فرض (بدون ردیابی) — برای ردیابی خودکار، "
            "mpv یا PotPlayer را در «⚙ تنظیمات» انتخاب کن"), 8000)
        open_path(f)

    def _track_poll(self):
        if not self.tracker.poll():
            self._track_timer.stop()

    def _on_watch_tracked(self, entry, kind: str, result):
        """بازخورد ردیاب مرکزی: به‌روزرسانی UI جزئیات (اگر همان عنوان است) + کتابخانه."""
        if self.details.item is not None and self.details.item.path == entry.item.path:
            self.details.refresh_watch(entry.rel)
        if kind == "final":
            mode, pos = result
            if mode == Store.WATCH_DONE:
                msg = T("✓ «{0}» دیده‌شده ثبت شد").format(entry.proc.media.name)
            else:
                msg = (T("◐ «{0}» تا {1} ذخیره شد — پخش بعدی از همین‌جا ادامه می‌یابد").format(entry.proc.media.name, format_duration(pos)))
            self.statusBar().showMessage(msg, 8000)
            self.refresh_library()            # اگر جزئیات باز است، تعویق می‌افتد (بدون گیر)
        elif kind == "switch":
            self.statusBar().showMessage(
                T("⏭ پلیر رفت سراغ «{0}» — ردیابی روی قسمت جدید ادامه دارد").format(Path(result).name), 8000)
            if self.details.item is not None and self.details.item.path == entry.item.path:
                self.details.refresh_watch(entry.rel)
        else:
            # به‌روزرسانی کارت‌های کتابخانه حین پخش زنده — حداکثر هر ~۳۰ ثانیه
            now = time.monotonic()
            if now - self._last_lib_ping >= 30:
                self._last_lib_ping = now
                self.refresh_library()

    # ------------------------------------------------------------ پوسته
    def apply_theme(self, name: str):
        self.store.set_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_theme(name))
        try:
            from ui.widgets import set_poster_bg     # زمینه‌ی پوستر هم‌رنگ تم
            set_poster_bg(theme_bg(name))
        except Exception as _exc_dbg:
            dbg_log(f"apply_theme failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # (round 17 §۲) رنگِ letterboxِ کارت‌ها موقعِ *ساختِ* پیکسمپ در
            # _pm_cache پخته می‌شود؛ بدونِ پاک‌کردنِ کش، بعد از تعویضِ تم
            # کارت‌ها نوارِ رنگِ تمِ قدیمی را نگه می‌دارند (تحلیلِ هوشِ ۱).
            # بازسازی موقعیتِ اسکرول را حفظ می‌کند (ui/library.py).
            self.library.clear_pixmap_cache()
        except Exception as _exc_dbg:
            dbg_log(f"apply_theme cache-clear failed: {type(_exc_dbg).__name__}")
            pass
        combo = self.library.theme_combo          # همگام‌سازی کمبوی سایدبار
        idx = combo.findData(name)
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self.status_lbl.setText(T("🎨 پوسته‌ی «{0}» اعمال شد").format(T(theme_label(name))))

    # ------------------------------------------------------------ ناوبری
    def show_details(self, item):
        self.details.set_item(item)
        self.stack.setCurrentWidget(self.details)
        self.store.add_recent(str(item.path))             # «🕘 موارد اخیر»
        self._nav_push(("det", "", "", str(item.path)))   # تاریخچه‌ی ناوبری

    def random_pick(self, item):
        """دکمه‌ی «امشب چی ببینم؟» — جزئیات همان عنوان."""
        self.show_details(item)

    def smart_play(self, item):
        """پخش مستقیم از صفحه‌ی کتابخانه (بهترین هدف: نیمه‌تمام → ندیده → اولین)."""
        self.show_details(item)
        QApplication.processEvents()
        self.details.play_smart()

    # ------------------------------------------------------------ ناوبری/به‌روزرسانی
    def _back_to_library(self):
        # کارِ پس‌زمینه‌ی صفحه‌ی رها شده لغو شود — گزارش کاربر: «حتی اگر خارج
        # شی از آن پوشه برنامه کند می‌ماند». صف تامبنیل/مدتِ پوشه‌ی قبلی بدون
        # این لغو، داخل کتابخانه هم به خوردن CPU/دیسک ادامه می‌داد.
        try:
            self.details.cancel_pending()
        except Exception as _exc_dbg:
            dbg_log(f"_back_to_library failed: {type(_exc_dbg).__name__}")
            pass
        self.stack.setCurrentWidget(self.library)
        if self._lib_dirty:               # تغییرات حین جزئیات حالا اعمال می‌شود
            self.refresh_library()
        # ثبت در تاریخچه‌ی ناوبری (با همان فیلتر دسته‌ای که کاربر داشت)
        self._nav_push(("lib", self.library.filter_kind, self.library.filter_cat, ""))

    def refresh_library(self):
        """بازسازی کتابخانه — «عَلیل» نسبت به صفحه‌ی نمایان.

        اگر صفحه‌ی جزئیات باز است (کاربر کتابخانه را نمی‌بیند)، بازسازی به
        بازگشت موکول می‌شود؛ بدین‌ترتیب ذخیره‌ی زنده/نهاییِ تماشا یا به‌روزرسانی
        توضیحات هیچ گیری روی UI نمی‌گذارد (درخواست «بعد از بستن پلیر برنامه
        چندثانیه گیر می‌کند»).
        """
        if self.stack.currentWidget() is self.details:
            self._lib_dirty = True
            return
        self._lib_dirty = False
        self.library.set_items(self.items)

    def _on_cover_changed(self, item):
        """(C1) کاور از صفحه‌ی جزئیات تعویض/حذف شد.

        خودِ جزئیات تازه شده؛ این‌جا فقط کشِ پوسترِ *همان* عنوان بی‌اعتبار
        می‌شود (قبلاً هیچ خبری به کتابخانه نمی‌رسید و کارت کاورِ قدیمی نشان
        می‌داد) و بازسازی به برگشت موکول می‌شود — همان سیاستِ refresh_library."""
        try:
            self.library.invalidate_item_poster(item.path)
        except Exception as _exc_dbg:
            dbg_log(f"_on_cover_changed failed: {type(_exc_dbg).__name__}")
            pass
        if self.stack.currentWidget() is self.details:
            self._lib_dirty = True             # در _back_to_library بازسازی می‌شود
        else:
            self.refresh_library()

    def _check_save_failed(self):
        """(C8) شکستِ ذخیره‌ی دیتابیس بی‌صدا نماند — نوارِ وضعیت اعلام می‌کند.

        پرچم را store.save_data روشن/خاموش می‌کند؛ این‌جا فقط پیامِ یک‌باره در
        هر دوره‌ی شکست نشان داده می‌شود (الگویِ read_only_fallback)."""
        failed = bool(getattr(self.store, "save_failed", False))
        if failed and not self._save_warn_shown:
            self._save_warn_shown = True
            self.statusBar().showMessage(
                T("⚠ ذخیره‌ی اطلاعات ممکن نشد — تغییرات فعلاً فقط در حافظه‌اند"), 8000)
        elif not failed:
            self._save_warn_shown = False

    # ------------------------------------------------------------ محدودیت حجم کش
    def _on_cache_pruned(self, removed: int):
        """0.42.0: بعد از کوتاه‌شدنِ کش با سقفِ حجم — مانیفستِ تامبنیل‌ها بی‌اعتبار
        می‌شود تا پیش‌ساز در اجرای بعد فایل‌های پاک‌شده را دوباره بسازد و هیچ
        تامبنیلِ گمشده‌ای پشتِ «ردکردنِ هوشمند» پنهان نماند."""
        if not removed:
            return
        try:
            self.store.clear_thumb_manifests()
            watchtime._dbg("cache-limit: thumb manifests invalidated")
        except Exception as e:
            watchtime._dbg(f"cache-limit manifest clear error: {type(e).__name__}")

    def _enforce_cache_limit(self):
        """سقف حجم کش + پاک‌سازیِ نسلِ مقیاسِ قبلی — هر دو در نخ جدا."""
        limit = self.store.cache_max_mb()
        # D10: تغییرِ مقیاسِ نمایش کلیدِ کش را عوض می‌کند و نسلِ قبلی بی‌استفاده
        # می‌ماند — یک‌بار در هر بوت (با نشانگرِ مقیاسِ بوتِ قبل) قدیمی‌ها پاک می‌شوند
        if self.cache_px:
            self._prune_scale_generation_async()
        if not limit:
            return
        from thumbs import enforce_cache_size_limit

        def job():
            try:
                freed, removed = enforce_cache_size_limit(self.cache.cache_dir, limit)
                if removed:
                    watchtime._dbg(
                        f"cache-limit: {removed} files removed, {freed} bytes freed (cap {limit}MB)")
                    try:
                        self.cache_pruned.emit(removed)      # 0.42.0 → نخ UI
                    except Exception as _exc_dbg:
                        dbg_log(f"_enforce_cache_limit.job failed: {type(_exc_dbg).__name__}")
                        pass
            except Exception as e:
                watchtime._dbg(f"cache-limit error: {type(e).__name__}")

        threading.Thread(target=job, daemon=True).start()

    def _prune_scale_generation_async(self) -> None:
        """prune_scale_generation در نخ پس‌زمینه — فقط وقتی مقیاس عوض شده حذف می‌کند."""
        def job():
            try:
                from thumbs import prune_scale_generation
                freed, removed = prune_scale_generation(self.cache.cache_dir,
                                                        self.cache_px)
                if removed:
                    watchtime._dbg(f"cache scale-gen prune: {removed} files, "
                                   f"{freed} bytes (px was different)")
                    try:
                        self.cache_pruned.emit(removed)
                    except Exception as _exc_dbg:
                        dbg_log(f"_prune_scale_generation_async.job failed: {type(_exc_dbg).__name__}")
                        pass
            except Exception as _exc_dbg:
                dbg_log(f"_prune_scale_generation_async.job failed: {type(_exc_dbg).__name__}")

        threading.Thread(target=job, daemon=True, name="cache-scale-prune").start()

    # ------------------------------------------------------------ ریلانچ تمیز
    def request_restart(self):
        """ریلانچِ «تمیز» — دقیقاً همان رفتارِ خروجِ عادی/پاکسازیِ کامل.

        گزارش کاربر: «فایل بکاپ وارد کردم … برنامه‌ی قبلی باز ماند و یک برنامه‌ی
        جدید باز شد». علت: ریلانچ از دلِ دیالوگِ مودال (حلقه‌های رویدادِ تودرتوی
        پروگرس‌بار/تنظیمات) انجام می‌شد و app.quit همه‌چیز را تمیز نمی‌بست.
        حالا فقط پرچم گذاشته می‌شود، همه‌ی پنجره‌ها بسته می‌شوند و ریلانچِ واقعی
        در closeEvent — *بعد* از توقفِ نخ‌ها و بستنِ دیتابیس‌ها — انجام می‌گیرد.
        """
        self._restart_after_import = True
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                for w in app.topLevelWidgets():
                    if w is not self and w.isVisible():
                        try:
                            w.close()
                        except Exception as _exc_dbg:
                            dbg_log(f"request_restart failed: {type(_exc_dbg).__name__}")
                            pass
        except Exception as _exc_dbg:
            dbg_log(f"request_restart failed: {type(_exc_dbg).__name__}")
            pass
        self.close()                # → closeEvent: پاکسازی + ریلانچ + واچ‌دگ

    # ------------------------------------------------------------ تنظیمات
    def manage_paths(self):
        prev_theme = self.store.theme
        prev_ab48 = self.store.auto_backup_cfg()    # تشخیص فعال‌سازی/تغییر مسیر
        dlg = SettingsDialog(self.store, self, self.cache.cache_dir, durations=self.dstore)
        if dlg.exec():
            _ab48 = self.store.auto_backup_cfg()
            if (_ab48.get("enabled")
                    and (not prev_ab48.get("enabled")
                         or str(_ab48.get("path") or "") != str(prev_ab48.get("path") or ""))):
                # بازبینی کاربر: فعال شد → فایل همین حالا و با همهٔ داده‌های
                # موجود ساخته شود؛ اگر پیش‌ساخت جاری است صبر تا تمام شود.
                self._queue_auto_backup(force=True)
            watchtime.invalidate_player_cache()   # ممکن است پلیر عوض شده باشد
            self.library.clear_pixmap_cache()     # و کش تصاویر شاید پاک شده باشد
            self._enforce_cache_limit()           # سقف تازه‌ی کش همین حالا اعمال شود
            if self.store.theme != prev_theme:
                self.apply_theme(self.store.theme)

            # گزارش کاربر: «ذخیره را زدم چند ثانیه گیر کرد و هنگ کرد» —
            # کارِ سنگینِ بعد از ذخیره (اسکن/بازسازی کتابخانه/پیش‌ساخت) یک نوبتِ
            # رویداد بعد اجرا می‌شود تا پنجره‌ی تنظیمات اول جمع و رنگِ صفحه تازه
            # شود — دیگر هیچ مکثِ چندثانیه‌ای روی رشته‌ی UI دیده نمی‌شود.
            def _after_settings_save():
                # درخواست کاربر: تغییرِ زبان ذخیره می‌شود و برای اعمالِ کامل
                # (متن‌ها + چیدمان راست‌به‌چپ/چپ‌به‌راست) پیشنهادِ راه‌انداریِ
                # مجددِ تمیز داده می‌شود — همان مکانیزمِ امنِ 0.42.0.
                try:
                    import i18n as _i45
                    import hwinfo as _hw45
                    _hw45.set_perf_mode(self.store.perf_mode)
                    if self.store.language != _i45.lang():
                        from PySide6.QtWidgets import QMessageBox as _MB45
                        _a45 = _MB45.question(
                            self, T("تغییر زبان"),
                            T("برای اعمالِ کاملِ زبان جدید برنامه باید دوباره راه‌اندازی شود.")
                            + "\n\n" + T("الان راه‌اندازی مجدد شود؟"))
                        if _a45 == _MB45.StandardButton.Yes:
                            self.request_restart()
                            return
                except Exception as _exc_dbg:
                    dbg_log(f"manage_paths._after_settings_save failed: {type(_exc_dbg).__name__}")
                    pass
                if dlg.paths_changed:
                    # اسکن فقط اگر هم‌اکنون اسکنی در جریان نیست (مسیرِ شروعِ اولِ
                    # برنامه خودش بلافاصله پس از manage_paths اسکن می‌زند — دوبله نه)
                    if self.worker is None or not self.worker.isRunning():
                        self.start_scan()         # مسیرها/فال‌بک/زمان‌ها عوض شده → لازم
                else:
                    # تغییرات عمومی (پلیر/پوسته/دسته‌ها) بدون اسکن اعمال می‌شوند
                    if dlg.cats_changed:
                        self.refresh_library()
                    # روشن‌کردن «پیش‌ساخت تامبنیل» همین حالا اثر کند — کاربر انتظار
                    # دارد بدون اسکن مجدد هم ساخت شروع شود (گزارش: «گزینه کار نمی‌کند»؛
                    # قبل از این فقط بعد از یک اسکنِ تازه شروع می‌شد).
                    if self.store.prebuild_thumbs and self.items \
                            and not self.prebuilder.running:
                        self.status_lbl.setText(T("⚙ تنظیمات ذخیره شد ✓ · ⚡ پیش‌ساخت تامبنیل شروع شد…"))
                        self.prebuilder.start(self.items)
                    else:
                        self.status_lbl.setText(T("⚙ تنظیمات ذخیره شد — بدون اسکن مجدد ✓"))

            from PySide6.QtCore import QTimer as _QT43
            _QT43.singleShot(0, _after_settings_save)

    # ------------------------------------------------------------ اسکن
    def start_scan(self, first: bool = False):
        if not self.store.roots():
            QMessageBox.information(
                self, T("شروع کار 🎬"),
                T("هنوز مسیری اضافه نشده.\nحالا پنجره‌ی مسیرها باز می‌شود — پوشه‌هایت را اضافه کن."),
            )
            self.manage_paths()
            if not self.store.roots():
                return
        # در اجرای اول، تا پایان اسکن فقط اسپلش دیده شود (بدون پنجره‌ی ناتمام)
        if first and not self.isVisible():
            self.splash = SplashScreen()
            self.splash.setStatus(T("در حال اسکن کتابخانه…"))
            self.splash.show()
        try:
            self.prebuilder.stop()          # پیش‌ساز قبلیِ اسکن کهنه متوقف شود
        except Exception as _exc_dbg:
            dbg_log(f"start_scan failed: {type(_exc_dbg).__name__}")
            pass
        try:
            if self.duration_worker is not None:   # کارگر زمانِ اسکن کهنه لغو شود
                self.duration_worker.stop()
                self.duration_worker = None
        except Exception as _exc_dbg:
            dbg_log(f"start_scan failed: {type(_exc_dbg).__name__}")
            pass
        self.pb_chip.hide()                 # نوار پیش‌سازِ رهای‌شده پنهان
        self.library.set_busy(True)
        self._scan_t0 = time.monotonic()
        self._scan_track.update(phase="", t0=0.0)
        self.scan_chip.reset(100, T("🔍 در حال شمارش عنوان‌ها…"))
        self.scan_chip.show()
        self.status_lbl.setText(T("در حال اسکن… ⏳"))
        self.worker = ScanWorker(self.store, self.cache, self.dstore, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.counts.connect(self._on_scan_counts)
        self.worker.finished_items.connect(self.on_scan_finished)
        self.worker.start()

    def _on_progress(self, msg: str):
        self.status_lbl.setText(msg)
        if self.splash:
            self.splash.setStatus(msg)

    # ------------------------------------------------------------ متن زمان/تخمین
    @staticmethod
    def _eta_parts(track: dict, phase: str, done: int, total: int):
        """(گذشته، تخمینِ مانده) — تایمر با تغییر فاز خودکار ریست می‌شود.

        درخواست کاربر (0.23.0): روی نوار پیشرفت بنویسد «چقدر طول کشیده و چقدر
        مانده»؛ مانده از نرخ میانگینِ همان فاز (elapsed/done) تخمین زده می‌شود.
        """
        now = time.monotonic()
        if track.get("phase") != phase or not track.get("t0"):
            track["phase"] = phase
            track["t0"] = now
        elapsed = now - track["t0"]
        remaining = None
        if done > 0 and total > done:
            remaining = elapsed / done * (total - done)
        return elapsed, remaining

    def _prog_text(self, label: str, track: dict, phase: str,
                   done: int, total: int) -> str:
        el, rem = self._eta_parts(track, phase, done, total)
        text = T("{0} {1} از {2} · گذشته {3}").format(label, fa(done), fa(total), fa_mmss(el))
        if rem is not None:
            text += T(" · مانده ~{0}").format(fa_mmss(rem))
        return text

    def _on_scan_counts(self, phase: str, done: int, total: int):
        """نوارِ شمارشِ واقعی اسکن (0.23.0) — «اسکن n از m عنوان»/«زمان ویدیوها»."""
        if total <= 0:
            return
        # درخواست کاربر: متنِ صریح «در حال محاسبه‌ی زمان» روی نوار وضعیت
        label = T("🔍 اسکن") if phase == "scan" else T("⏱ در حال محاسبه‌ی زمان")
        text = self._prog_text(label, self._scan_track, phase, done, total)
        self.scan_chip.update(done, total, text)
        if self.scan_chip.isHidden():
            self.scan_chip.show()

    def _on_prebuild_counts(self, phase: str, done: int, total: int):
        """نوار پیشرفت عددی پیش‌ساز — «n از m · گذشته · مانده» در نوار وضعیت.

        فاز «done» نوار را مخفی می‌کند (خلاصه‌ی نهایی با زمانِ کل روی خودِ
        نوار وضعیت می‌ماند)؛ بقیه‌ی فازها عدد دقیقِ لحظه‌ای را نشان می‌دهند.
        """
        ui = self.pb_chip
        self._pb_active = not (phase == "done" or total <= 0)   # وضعیت پیش‌ساز
        if phase == "done" or total <= 0:
            ui.hide()

            # تامبنیل‌ها کامل شد → پشتیبانِ خودکار؛ اگر فعال‌سازیِ تازه
            # معلقِ پایانِ پیش‌ساخت بود، همین‌جا اجباری نوشته می‌شود.
            def _pb_backup48():
                if self._ab_pending:
                    self._ab_pending = False
                    self._maybe_auto_backup("prebuild", force=True)
                else:
                    self._maybe_auto_backup("prebuild")

            QTimer.singleShot(1200, _pb_backup48)
            return
        label = {"check": T("🔎 بررسی"),                              # فاز اثر انگشت
                 "posters": T("⚡ پوستر"),
                 "archives": T("📦 آرشیو"),
                 "videos": T("🎬 ویدیو"),
                 "heavy": T("⚡ سنگین‌ها")}.get(phase, T("⚡ تامبنیل"))   # فازهای مجزا
        text = self._prog_text(label, self._pb_track, phase, done, total)
        ui.update(done, total, text)
        if ui.isHidden():
            ui.show()

    def on_scan_finished(self, payload):
        items, durations = payload
        self.items = list(items)
        self.worker = None
        if durations is not None:
            totals, manifests = durations
            self.store.set_total_durations(totals, manifests)
        # round 32 — مواردِ اخیرِ یتیم (مسیرِ جابه‌جا/حذف‌شده) بعد از هر اسکنِ
        # کامل پاک می‌شوند تا عددِ کنارِ «🕘 موارد اخیر» با واقعیت یکی باشد
        # (گزارش کاربر: «۱۵ ولی ۹ می‌نویسد»). قبل از set_items تا سایدبارِ
        # تازه عددِ پاک‌شده را نشان دهد. اگر کتابخانه خالی است (درایوِ خاموش/
        # اسکنِ ناکام) هیچ‌چیز پاک نمی‌شود — خرابیِ موقت تاریخچه را نابود نمی‌کند.
        if self.items:
            try:
                self.store.prune_recents([str(i.path) for i in self.items])
            except Exception as _exc_dbg:
                dbg_log(f"prune_recents failed: {type(_exc_dbg).__name__}")
        self.library.set_items(self.items)
        self.library.set_busy(False)
        self.scan_chip.hide()
        if self.splash:
            self.splash.close()
            self.splash = None
            self.show()
            self.raise_()
            self.activateWindow()
        # درخواست کاربر: «اول پوسترها/برنامه، بعد زمان‌ها در پس‌زمینه» —
        # پنجره *قبل* از شروع محاسبه‌ی زمان نمایش داده شد؛ حالا زمان‌ها با کارگر
        # مستقل (با ردکردن پوشه‌های بدون‌تغییر) حساب می‌شوند و نوار وضعیت
        # «در حال محاسبه‌ی زمان…» را نشان می‌دهد.
        self._start_duration_worker()
        dur_note = (T(" · ⏱ محاسبه‌ی زمان خاموش است")
                    if not self.store.compute_durations
                    else (T(" · ⏱ زمان‌ها در پس‌زمینه…") if self.items else ""))
        # زمانِ دقیقِ اسکن روی نوار وضعیت می‌ماند تا کاربر بداند چقدر طول کشید
        scan_el = time.monotonic() - self._scan_t0 if self._scan_t0 else 0.0
        scan_el_txt = f" · ⏱ {fa_mmss(scan_el)}" if scan_el >= 0.2 else ""
        self.status_lbl.setText(
            T("✅ {0} عنوان یافت شد{1}{2}").format(fa(len(self.items)), scan_el_txt, dur_note))
        # گزینه‌ی کاربر: «بعد از افزودن مسیر/اسکن، همه‌ی تامبنیل‌ها پیش‌ساخته شوند» —
        # نخِ مستقل و کم‌فشار؛ کارِ تکراری نمی‌کند (همان کش دیسکی مشترک).
        if self.store.prebuild_thumbs and self.items:
            self.prebuilder.start(self.items)
        # مواردِ زیر ریشه‌ها که فایلشان دیگر نیست → دیالوگِ «حذف/مسیر جدید»
        self._check_missing_items()
        # کاورهای یتیمِ ‎.covers/‎ (عنوانِ حذف‌شده / کاورِ تعویض‌شده‌ی باقی‌مانده
        # از نسخه‌های قبل) بعد از هر اسکنِ کامل جمع می‌شوند — نه در هر تغییرِ
        # کاور، چون جمع‌آوریِ صحیح به یک گذرِ کاملِ متا نیاز دارد.
        self._cover_gc_async()

    def _cover_gc_async(self) -> None:
        """collect_garbage در نخ پس‌زمینه — فایل‌های بدونِ ارجاعِ متا حذف می‌شوند.

        ارجاع‌ها از خودِ دیتابیس می‌آیند (store.cover_refs) نه از آیتم‌های
        اسکنِ فعلی — ریشه‌ی موقتاً آفلاین (درایوِ جدا/شبکه‌ی قطع) در اسکن نیست
        و کاورِ سفارشیِ عنوان‌هایش «یتیم» به‌نظر می‌رسید و پاک می‌شد
        (از‌دست‌رفتنِ داده — round 13 §۲).
        """
        try:
            refs: set[str] = self.store.cover_refs()
        except Exception as _exc_dbg:
            dbg_log(f"_cover_gc_async refs failed: {type(_exc_dbg).__name__}")
            return

        def job():
            try:
                from covers import collect_garbage
                # (round 19 §۳) store پاس داده می‌شود تا قبل از هر unlink ارجاع‌ها
                # زیرِ قفلِ Store دوباره خوانده شود — snapshotِ بالاتر ممکن است
                # تا پایانِ نخ قدیمی شده باشد و کاورِ تازه‌ارجاع‌شده پاک نشود
                removed = collect_garbage(refs, store=self.store)
                if removed:
                    watchtime._dbg(
                        f"cover-gc: {removed} orphan cover file(s) removed")
            except Exception as _exc_dbg:
                dbg_log(f"_cover_gc_async.job failed: {type(_exc_dbg).__name__}")

        threading.Thread(target=job, daemon=True, name="cover-gc").start()

    def _start_duration_worker(self):
        """کارگر پس‌زمینه‌ی زمان‌ها (0.31.0) — قدیمیِ در حال اجرا لغو می‌شود."""
        try:
            if self.duration_worker is not None:
                self.duration_worker.stop()
                self.duration_worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass
        self.duration_worker = None
        try:
            self.prebuilder.set_durations_busy(False)   # همیشه در نقطه‌ی تصمیم
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass
        if not self.store.compute_durations or not self.items:
            return
        self.duration_worker = DurationWorker(self.store, self.dstore, self.items, self)
        self.duration_worker.progressed.connect(self._on_progress)
        self.duration_worker.counts.connect(self._on_scan_counts)
        self.duration_worker.totals_ready.connect(self._on_durations_ready)
        self.duration_worker.start()
        # فازهای سنگینِ پیش‌ساخت تا پایانِ موجِ زمان‌ها صبر می‌کنند (رفع
        # رگرسیونِ «۳:۵۵ → ۱۵:۴۳» روی درایو اکسترنال)
        try:
            self.prebuilder.set_durations_busy(True)
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass

    def _on_durations_ready(self, payload):
        self.duration_worker = None
        try:
            self.prebuilder.set_durations_busy(False)   # سنگین‌ها آزادند
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            pass
        if not payload:
            return
        totals, manifests = payload
        try:
            self.store.set_total_durations(totals, manifests)
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            return
        try:
            self.library.refresh_durations()     # متن کارت‌ها درجا تازه می‌شود
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            pass
        self.scan_chip.hide()
        # اگر پیش‌ساخت در حال اجراست، متنِ خودش را می‌نویسد — خفه‌اش نکنیم
        if not self.prebuilder.running:
            self.status_lbl.setText(
                T("✅ {0} عنوان · ⏱ محاسبه‌ی زمان تمام شد").format(fa(len(self.items))))

    # ------------------------------------------------------------ ویرایش / علاقه‌مندی
    def edit_item(self, item):
        dlg = ItemEditDialog(item, self.store, self)
        # (round 19 §۵) تغییرِ کاور داخلِ دیالوگ فوری اعمال می‌شود؛ اگر کاربر
        # بعدش Cancel بزند، کتابخانه/جزئیات نباید کاورِ قدیمی نشان دهند —
        # همان مسیرِ C1 مستقل از accept/reject خبر می‌شود.
        try:
            dlg.coverTouched.connect(self._on_cover_changed)
        except Exception as _exc_dbg:
            dbg_log(f"edit_item coverTouched failed: {type(_exc_dbg).__name__}")
        if dlg.exec():
            self.library.clear_pixmap_cache()   # پوستر سفارشی ممکن است عوض شده باشد
            self.refresh_library()
            if self.stack.currentWidget() is self.details:
                self.details.set_item(item)

    def toggle_fav(self, item):
        self.store.set_meta(item.path, favorite=not self.store.fav(item.path))
        fav = self.store.fav(item.path)
        # به‌روزرسانی درجای همان کارت — بدون بازسازی شبکه و پرش اسکرول
        if self.stack.currentWidget() is self.library and self.library.update_item_card(item):
            self.library.rebuild_sidebar()
            self.library._update_stats()
        else:
            self.refresh_library()
        self.status_lbl.setText(
            (T("⭐ «{}» به علاقه‌مندی‌ها رفت") if fav else T("☆ «{}» از علاقه‌مندی حذف شد"))
            .format(self.store.title(item)))

    def change_subcat(self, item, cat: str):
        """باکس سریع کارت/هدر: جابه‌جایی بین دسته‌ی اصلی و زیردسته‌ها بدون دیالوگ."""
        self.store.set_category(item.path, cat)
        # اول تلاش برای به‌روزرسانی درجای کارت (بدون پرش/بازسازی — سریع)
        if self.stack.currentWidget() is self.library and self.library.update_item_card(item):
            self.library.rebuild_sidebar()
            self.library._update_stats()
        else:
            sb = self.library.scroll.verticalScrollBar()
            pos = sb.value()
            self.refresh_library()
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(0, lambda: sb.setValue(pos))   # بعد از چیدمان دوباره
        if self.stack.currentWidget() is self.details and self.details.item is item:
            self.details.set_item(item)
        self.status_lbl.setText(
            f"📁 «{self.store.title(item)}» → {cat.replace('/', ' › ')}")

    def closeEvent(self, event):
        """بستن برنامه: موقعیتِ پخش‌های هنوز-باز ذخیره، اسکن متوقف امن، کش فلاش."""
        try:
            _app47x = QApplication.instance()
            if _app47x is not None:
                _app47x.removeEventFilter(self)   # فراخوانِ فیلتر به شیِ مرده نرود
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._track_timer.stop()
            self.tracker.shutdown()
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # آخرین شانسِ پشتیبانِ خودکار — فقط اگر تغییرِ واقعی رخ داده باشد
            self._maybe_auto_backup("exit")
            _tab48 = time.monotonic()
            while self._ab_running and time.monotonic() - _tab48 < 10:
                # D11: انتظارِ خالصِ sleep پنجره را تا ۱۰ ثانیه فریز می‌کرد
                # (ویندوز از ثانیه‌ی ۵ «Not responding» می‌نوشت و کاربر End task
                # می‌زد) — چرخه‌ی رویداد در انتظار زنده می‌ماند.
                try:
                    _app48 = QApplication.instance()
                    if _app48 is not None:
                        _app48.processEvents()
                except Exception as _exc_dbg:
                    dbg_log(f"closeEvent pump failed: {type(_exc_dbg).__name__}")
                    pass
                time.sleep(0.05)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            if self.duration_worker is not None and self.duration_worker.isRunning():
                self.duration_worker.stop()      # کارگر زمان تمیز بیرون می‌آید
                self.duration_worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # اگر اسکن در حال اجراست، منتظر پایانِ امن نمی‌مانیم (ممکن است طول بکشد)
            # — خاتمه‌ی اجباری فقط در لحظه‌ی خروج برنامه و بدون اثر ناخوشایند دائمی.
            if self.worker is not None and self.worker.isRunning():
                self.worker.terminate()
                self.worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.prebuilder.stop()       # نخ پیش‌ساز تامبنیل در اولین نقطه‌ی امن می‌ایستد
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.library.shutdown()      # توقف تمیز نخ بارگذار پوستر
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # قطع سیگنال‌ها + لغو صف‌های تامبنیل/هدر/زمان — رفع ارور خروج
            # «RuntimeError: Signal source has been deleted» (گزارش کاربر)
            self.details.shutdown()
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.dstore.close()          # فلاش کثیفی‌های SQLite + بستن اتصال
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.store.close()           # فلاش نهایی دیتابیس داده
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        super().closeEvent(event)
        # گزارش کاربر: «مثل پاکسازی کامل درست ببنده» — ریلانچِ ورودِ
        # پشتیبان فقط از این مسیرِ تمیز — یعنی *بعد* از توقفِ همه‌ی نخ‌ها و
        # بستنِ دیتابیس‌ها — انجام می‌شود؛ پروسه‌ی تازه داده‌های سالم می‌بیند.
        if event.isAccepted() and getattr(self, "_restart_after_import", False):
            try:
                import sys as _sys
                from portable import app_dir as _appd
                from ui.dialogs import _relaunch_app as _rl
                if getattr(_sys, "frozen", False):
                    _rl([_sys.executable])
                else:
                    _rl([_sys.executable, str(_appd() / "main.py")])
            except Exception as _exc_dbg:
                dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
                pass
            # واچ‌دگِ خروجِ قطعی: اگر ماندنِ نخ‌های زمینه یا حلقه‌های تودرتو
            # مثل گذشته خروج را ناقص کند، پروسه‌ی قدیمی ظرف چند ثانیه به‌زور
            # می‌میرد تا هرگز «دو نمونه‌ی هم‌زمانِ باز» دیده نشود.
            _arm_exit_watchdog()


def load_font(app: QApplication):
    font_file = APP_DIR / "assets" / "Vazirmatn-Regular.ttf"
    if not font_file.exists():                    # حالت exe — داخل بسته‌ی _MEIPASS
        from portable import resource_path as _rpf45
        font_file = _rpf45("assets/Vazirmatn-Regular.ttf")
    if font_file.exists():
        QFontDatabase.addApplicationFont(str(font_file))
    font = QFont()
    font.setFamilies(["Vazirmatn", "Segoe UI", "Tahoma"])
    font.setPointSize(10)
    app.setFont(font)


def _write_log(line: str):
    """یک خط در لاگ برنامه (کنار main.py)."""
    try:
        with open(APP_DIR / "folderdex-error.log", "a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(f"\n[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
    except Exception as _exc_dbg:
        dbg_log(f"_write_log failed: {type(_exc_dbg).__name__}")
        pass


def _disable_window_ghosting():
    """ویندوز: خاموش‌کردن «پنجره‌های روح/سفید» (Window Ghosting).

    وقتی یک برنامه برای چند ثانیه به حلقه‌ی پیام نرسد، ویندوز به‌جای پنجره‌اش
    یک پنجره‌ی سفیدِ «Not Responding» با همان عنوان می‌سازد — دقیقاً همان
    «صفحه‌های سفید با عنوان برنامه» که گزارش شده بود. با این فراخوانی
    اگر جایی برنامه لحظه‌ای سنگین شود، ویندوز دیگر پنجره‌ی روح نمی‌سازد.
    (راه‌حل ریشه‌ای هم اعمال شده: کارهای سنگین از نخ UI خارج شده‌اند.)
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.DisableProcessWindowsGhosting()
    except Exception as _exc_dbg:
        dbg_log(f"_disable_window_ghosting failed: {type(_exc_dbg).__name__}")
        pass


def _writable_error_log_path() -> Path:
    """مسیرِ لاگِ خطا — اگر پوشه‌ی برنامه فقط‌خواندنی باشد به ‎%TEMP%‎ می‌رود.

    قبلاً خودِ لاگ‌نویسی روی پوشه‌ی غیرقابل‌نوشتن خطا می‌داد و excepthook
    به sys.__excepthook__ می‌خورد — در حالت ‎--windowed‎ کاربر هیچ چیزی
    نمی‌دید (ریشه‌ی «برنامه باز نمی‌شود»).
    """
    probe = APP_DIR / f".wprobe-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return APP_DIR / "folderdex-error.log"
    except OSError as _exc_dbg:
        dbg_log(f"error log in APP_DIR not writable: {type(_exc_dbg).__name__}")
    import tempfile
    try:
        return Path(tempfile.gettempdir()) / "folderdex-error.log"
    except Exception as _exc_dbg:
        dbg_log(f"error log temp failed: {type(_exc_dbg).__name__}")
        return APP_DIR / "folderdex-error.log"


def _install_excepthook():
    """ثبت خطاهای پیش‌بینی‌نشده در فایل لاگ (مهم برای حالت بدون کنسول pythonw)."""
    import traceback

    def hook(exc_type, exc, tb):
        try:
            with open(_writable_error_log_path(), "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception as _exc_dbg:
            dbg_log(f"_install_excepthook.hook failed: {type(_exc_dbg).__name__}")
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


class _PhantomWindowGuard(QObject):
    """نگهبان پنجره‌های ناشناخته — شکار «صفحه‌های سفید» مرموز گزارش‌شده.

    هر ویجت top-level که باز شود و جزو پنجره‌های شناخته‌شده‌ی برنامه نباشد
    (پنجره‌ی اصلی، دیالوگ‌ها، منو/تول‌تیپ/پاپ‌آپ)، در لاگ برنامه ثبت می‌شود
    (کلاس، اندازه، عنوان، زمان) و عنوان/آیکون مرتب می‌گیرد؛ هم تشخیص قطعی
    می‌دهد هم بدترین حالت را آراسته نگه می‌دارد.
    """

    _OK_TYPES = (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                 Qt.WindowType.SplashScreen, Qt.WindowType.Tool)

    def __init__(self):
        super().__init__()
        self.seen: list[str] = []           # برای تست/عیب‌یابی درون‌برنامه‌ای

    def eventFilter(self, obj, event):
        try:
            if (event.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and obj.isWindow()):
                if isinstance(obj, (QMainWindow, QDialog, QMenu)):
                    return False
                # فقط «نوعِ» پنجره را بسنج (ماسکِ بیت‌های WindowType، نه بیت‌به‌بیت)
                if (obj.windowFlags() & Qt.WindowType.WindowType_Mask) in self._OK_TYPES:
                    return False
                desc = (f"phantom-window: {type(obj).__name__} "
                        f"title={obj.windowTitle()!r} "
                        f"size={obj.width()}x{obj.height()}")
                self.seen.append(desc)
                _write_log(desc)
                if not obj.windowTitle():
                    obj.setWindowTitle(T("FolderDex"))
                ic = QApplication.instance().windowIcon()
                if not ic.isNull():
                    obj.setWindowIcon(ic)
        except Exception as _exc_dbg:
            dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
            pass
        return False


def _hide_all_consoles():
    """ویندوز: هیچ پنجره‌ی کنسول/فلش سفیدی از هیچ subprocessای ظاهر نشود.

    همه‌ی فراخوانی‌های مستقیم ما از قبل CREATE_NO_WINDOW دارند؛ این پچ سراسری
    تضمین می‌کند هر subprocess آینده (یا کتابخانه‌ی جانبی) هم بی‌صدا بماند.
    پخش‌کننده‌های GUI (mpv/PotPlayer/…) پنجره‌ی خودشان را طبعاً نشان می‌دهند.
    """
    if sys.platform != "win32":
        return
    import subprocess as _sp
    _orig_popen = _sp.Popen

    def _guarded(*args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
        return _orig_popen(*args, **kwargs)

    _sp.Popen = _guarded


def _install_qt_msg_filter():
    """فیلتر هشدارهای تزئینیِ داخلِ Qt — گزارش کاربر: «هنگام بستن/اجرا ارور نشون می‌ده».

    دو هشدار شناخته‌شده و بی‌خطر که منشاشان داخل خود Qt است (نه کد ما — همه‌ی
    نقاط کدمان قبلاً نرمال شده‌اند): «QFont::setPointSize: Point size <= 0»
    (وقتی یک فونت px-محورِ QSS جایی پوینت‌محور بازنویسی می‌شود) و «libpng iCCP»
    (پروفایل رنگیِ بدنساخته‌ی بعضی PNGهای خودِ کتابخانه‌ی کاربر که مستقیم — مثلاً
    در پیش‌نمایش انتخاب پوستر — توسط Qt خوانده می‌شود). این‌ها دیگر در کنسول
    چاپ نمی‌شوند ولی در فایل دیباگ ثبت می‌مانند تا ردیابی از بین نرود.
    """
    from PySide6.QtCore import qInstallMessageHandler

    def _handler(mode, ctx, msg):
        try:
            if ("setPointSize: Point size <= 0" in msg
                    or ("libpng warning" in msg and "iCCP" in msg)):
                try:
                    watchtime._dbg(f"qt-note suppressed: {msg}")
                except Exception as _exc_dbg:
                    dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
                    pass
                return
        except Exception as _exc_dbg:
            dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
            pass
        try:
            sys.stderr.write(msg + "\n")
        except Exception as _exc_dbg:
            dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
            pass

    qInstallMessageHandler(_handler)


def main() -> int:
    _install_excepthook()
    _hide_all_consoles()
    _disable_window_ghosting()
    watchtime.set_debug_log(APP_DIR / "folderdex-debug.log")
    _install_qt_msg_filter()
    # درخواست کاربر — دکمه‌ی «پاکسازی کامل»: اگر در اجرای قبلی بازنشانی
    # خواسته شده، این‌جا — پیش از بازشدن هر دیتابیس/کانفیگی — انجام می‌شود.
    try:
        _fr37 = _paths.factory_reset_if_pending()
        if _fr37:
            watchtime._dbg(T("factory reset: پاک‌سازی کامل انجام شد ({0} مقصد)").format(len(_fr37)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    # درخواست کاربر — «اکسپورت/ایمپورت»: اگر در اجرای قبلی بازیابیِ
    # پشتیبان خواسته شده، فایل‌های آماده‌سازی این‌جا — پیش از بازشدنِ هر
    # دیتابیسی — روی جای اصلی بازنویسی می‌شوند.
    try:
        import backup as _bk39
        _im39 = _bk39.apply_pending_import()
        if _im39:
            watchtime._dbg(T("import backup: بازیابی انجام شد ({0} مورد)").format(len(_im39)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    config_path = CONFIG_PATH
    argv = sys.argv[1:]
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            config_path = Path(argv[i + 1]).resolve()

    # درخواست کاربر — دوزبانه: زبانِ رابط **پیش از ساختِ هر ویجتی** مشخص
    # می‌شود تا چیدمان (راست‌به‌چپ/چپ‌به‌راست) و متن‌ها از اول درست باشند.
    # قاعده‌ی مهاجرت در «یک» جا است: i18n.lang_from_config (D14).
    import i18n as _i18n
    try:
        import json as _json45
        _raw45 = _json45.loads(Path(config_path).read_text(encoding="utf-8"))
        _i18n.set_lang(_i18n.lang_from_config(_raw45))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        _i18n.set_lang("en")

    app = QApplication(sys.argv)
    app.setApplicationName("FolderDex")
    # عنوان/تسک‌بارِ هر پنجره‌ی بی‌عنوانِ احتمالی به‌جای «Python» نام برنامه شود
    app.setApplicationDisplayName(app_title())
    # آیکون برنامه برای کل اپ (نه فقط پنجره‌ی اصلی) تا هیچ پنجره‌ی گذری آیکون «پایتون» نگیرد
    from portable import resource_path as _rpath45
    _icon = _rpath45("assets/icon.png")
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if _i18n.is_rtl()
        else Qt.LayoutDirection.LeftToRight)
    load_font(app)

    # نگهبان پنجره‌های ناشناخته (ریشه‌یابی «صفحه‌های سفید» گزارش‌شده)
    guard = _PhantomWindowGuard()
    app.installEventFilter(guard)
    app._phantom_guard = guard          # جلوگیری از جمع‌شدن توسط GC

    # درخواست کاربر — «حتی yes , no فارسی باشه»: فارسی‌سازیِ دکمه‌های
    # استاندارد Qt (QMessageBox/QInputDialog) — همه‌ی باکس‌ها، حتی استاتیک‌ها.
    _i18n.install_std_button_localizer(app)

    # امنیت (انتشار عمومی): اگر config.jsonِ واردشده به‌خاطرِ شکستِ پاک‌سازیِ
    # امن (fail-closed) اعمال نشد، به کاربر صریح بگو — سکوتِ این‌جا یعنی
    # «واردشدنِ ناقصِ نامحسوس». زبان هم اکنون قطعی است (store.language).
    try:
        _skipped_imports = _bk39.skipped_imports()
        if _skipped_imports:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                _i18n.T("ورود اطلاعات"),
                _i18n.T("این بخش از پشتیبان وارد نشد:\n{0}\n\nفایلِ config.jsonِ داخلِ پشتیبان سالم/امن نبود؛ تنظیماتِ فعلیِ شما دست‌نخورده ماند.").format(", ".join(_skipped_imports)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass

    store = Store(config_path)
    # زبانِ قطعی از Store (با مهاجرتِ configهای قدیمی) + جهتِ نهایی — حالا که
    # هنوز هیچ ویجتی ساخته نشده، اصلاحِ جهت این‌جا بدون‌هزینه و کامل است.
    _i18n.set_lang(store.language)
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if _i18n.is_rtl()
        else Qt.LayoutDirection.LeftToRight)
    # حالت عملکردِ کاربر + نتایجِ ذخیره‌شده‌ی تشخیص نوع دیسک (ثباتِ شمارِ
    # کارگرها بین اجراها — پاسخ به گزارشِ «یک‌بار ۴ دقیقه، یک‌بار ۶ دقیقه»)
    try:
        import hwinfo as _hw45
        _hw45.set_perf_mode(store.perf_mode)
        _hw45.load_kinds(store.disk_kinds)
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    # گزارش کاربر: «بعد از ورودِ پشتیبان، malformed — برنامه بالا نیامد» —
    # اگر دیتابیسِ خراب در _open_db قرنطینه و تازه‌سازی شده باشد، یک‌بار به
    # کاربر می‌گوییم چه شد (فقط نامِ فایل — بدون مسیر) و چگونه بازیابی کند.
    try:
        _q41 = getattr(store, "db_quarantined", None)
        if _q41:
            QMessageBox.information(
                None, T("بازیابی پس از خرابی دیتابیس"),
                T("دیتابیسِ قبلی آسیب دیده بود (خطای malformed که دیدی) و خودکار قرنطینه شد:\n{0}\n\nبرنامه با دیتابیسِ تازه بالا آمد؛ تنظیمات و فهرستِ پوشه‌ها از config.json حفظ شده‌اند. وضعیتِ تماشا و دسته‌ها را می‌توانی از تنظیمات با «📥 واردکردن اطلاعات» و فایلِ پشتیبانِ ‎.mcbak‎ برگردانی — نسخه‌ی جدید پیش از اعمال، سلامتِ دیتابیس را آزمون می‌کند (quick_check) و اگر فایل سالم نباشد هیچ تغییری نمی‌دهد.").format(T('، ').join(_q41)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    app.setStyleSheet(build_theme(store.theme))
    try:
        from ui.widgets import set_poster_bg       # همگام با تمِ شروع
        set_poster_bg(theme_bg(store.theme))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    try:
        w = MainWindow(store)
    except Exception as e:
        # D2: مرگِ بی‌صدای استارت — بدترین حالتِ رایج، پوشه‌ی برنامه‌ی
        # فقط‌خواندنی است (Program Files / فلشِ قفل‌شده). کاربر باید بداند
        # «چرا»؛ لاگ هم اگر پوشه فقط‌خواندنی باشد به ‎%TEMP%‎ می‌رود.
        dbg_log(f"MainWindow init failed: {type(e).__name__}")
        try:
            QMessageBox.critical(
                None, T("FolderDex"),
                T("برنامه نتوانست شروع کند — احتمالاً پوشه‌ی برنامه قابلِ نوشتن نیست.\n\nبرنامه را به یک پوشه‌ی قابلِ نوشتن (مثلاً Desktop یا Documents) منتقل و دوباره اجرا کنید.\nجزئیات در folderdex-error.log (اگر پوشه فقط‌خواندنی باشد در پوشه‌ی Temp ویندوز)."))
        except Exception as _exc_dbg:
            dbg_log(f"fatal-error dialog failed: {type(_exc_dbg).__name__}")
        return 1
    if store.roots():
        # اسکن در پس‌زمینه + اسپلش؛ پنجره بعد از لود نمایش داده می‌شود
        w.start_scan(first=True)
    else:
        w.show()
        w.start_scan()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
