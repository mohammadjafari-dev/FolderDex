# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پیش‌ساخت تامبنیل‌ها در پس‌زمینه — گزینه‌ی تنظیمات «پیش‌ساخت بعد از اسکن».

کاربر (درخواست 0.20.0): «اگر فعال باشه وقتی مسیر جدیدی اضافه میشه همون موقع تمام
تامبنیل‌هاش ساخته بشن». پس از پایان اسکن، نخ‌های کم‌فشارِ موازی:
  فاز ۱) پوستر همه‌ی عناوین با همان زنجیره‌ی کامل فال‌بک (آیکون ← تصویر ← ویدیو
         ← PDF ← محتوای فشرده) — اثر بصری سریع در صفحه‌ی کتابخانه؛
  فاز ۲) تامبنیل واقعیِ تک‌تک فایل‌های هر عنوان (سایز گرید جزئیات ۱۳۲px) با همان
         قوانین صفحه‌ی جزئیات (پوستر/آیکون عنوان از فهرست فایل‌ها حذف می‌شود).

نسخه‌ی 0.22.0 (گزارش کاربر: «۱۷۴ پوستر و ۴۲۴۶ تامبنیل ۱۰–۱۵ دقیقه طول کشید»):
- هر دو فاز *موازی* اجرا می‌شوند (قبلاً تک‌نخی بود و GIL+subprocessها همه‌چیز را
  سریالی می‌کردند) — با انکد WebPِ تازه، زمانِ ساخت چند برابر کم می‌شود؛
- پیش‌ساز قبل از شروع فاز ۲، فایل‌ها را *فهرست* می‌کند تا «چند از چند» دقیق و
  واقعی به نوار پیشرفت گزارش شود (درخواست کاربر: پروگرس‌بار با عدد).

نسخه‌ی 0.24.0 (گزارش کاربر: «۱۰ دقیقه و ۳۲ ثانیه طول کشید که خیلی طولانیه»):
- صفِ فایل‌ها به دو صفِ **سبک/سنگین** تقسیم شد: تصویر و سند (سریع، درون‌پردازه‌ای)
  با استخرِ بزرگ‌تر و *اول*؛ آرشیو و ویدیو (هر کدام یک subprocess چندثانیه‌ای)
  با استخرِ جداگانه و *بعد* — دیگر چند آرشیو کل صف را گروگان نمی‌گیرند و
  تامبنیل‌های سبک خیلی زودتر در کش فرونشانده می‌شوند (اثر بصری سریع‌تر)؛
- فهرست‌سازیِ فاز ۲الف که سریال بود (۱۷۴ بار get_file_tree پشت سر هم) حالا با
  یک استخرِ کوچک موازی می‌شود؛
- تعداد نخ‌ها کمی بالاتر رفت — کار غالباً منتظرِ subprocess/disk است نه CPU.
هر دو فاز از کشِ دیسکی مشترک استفاده می‌کنند، پس کارِ تکراری صفر است: چیزی که
قبلاً (مثلاً هنگام مرور) ساخته شده دوباره ساخته نمی‌شود. فشار ملایم: بعد از هر
چند فایل چند میلی‌ثانیه مکث تا هارد/CPU برای رابط کاربری آزاد بماند.

نسخه‌ی 0.42.0 (گزارش کاربر: «فایل پشتیبان کامله ولی برنامه ۴ دقیقه دوباره تامبنیل‌ها
رو بررسی می‌کنه — فایل‌هایی که تامبنیل دارن باید سریع رد بشن و فقط در صورت تغییر
دوباره بررسی بشن»): **مانیفستِ تامبنیل** — همان الگوی مانیفستِ زمان‌ها ولی برای
پیش‌ساخت. فاز صفرِ جدید برای هر عنوان یک «اثر انگشت» از همه‌ی فایل‌ها (نامِ
نسبی + حجم + mtime، با os.scandirِ ارزانِ ویندوز) می‌سازد و با مانیفستِ ذخیره‌شده
در دیتابیس مقایسه می‌کند:
- برابر → عنوانِ بدون‌تغییر: نه پوستر، نه فایل، نه تلاشِ مجددِ آرشیو — رد کامل؛
- متفاوت/تازه → فقط همان عنوان بازرسی می‌شود (مثلاً وقتی به پوشه تصویری اضافه
  شده). فهرستِ فایل‌ها از همین پیمایشِ فاز صفر می‌آید؛ get_file_tree دیگر
  دوباره راه نمی‌افتد.
مانیفست داخل data.db است، پس در فایلِ پشتیبان ‎.mcbak هم می‌آید و بازیابی‌اش
«گرم» بالا می‌آید. پاک‌کردن/کوتاه‌کردنِ کش مانیفست‌ها را هم پاک می‌کند تا هیچ
وقت تامبنیلِ گمشده پنهان نماند. اگر آرشیویی شکستِ «نرم» بخورد، آن عنوان حداکثر
۲ بار در اجراهای بعدی دوباره تلاش می‌شود و بعد مثل بقیه رد می‌شود (بازتلاشِ
کران‌دار — نه هدررفتِ چنددقیقه‌ایِ هر بار).
دوره‌ی _MANIFEST_ERA: اگر منطقِ تامبنیل عوض شود، یک شماره بالا می‌رود تا
مانیفست‌های قدیمی خودکار بی‌اعتبار و همه یک‌بار بازبینی شوند.
"""
from __future__ import annotations

from i18n import T

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hwinfo
from filethumbs import FileThumbs, group_of, poster_png_fallback
from scanner import LOOSE_DOC_EXTS, fa, fa_mmss, thumb_manifest
from watchtime import dbg_log

# تقسیم صف به سبک/سنگین — سبک‌ها بدون subprocess و سریع‌اند؛ سنگین‌ها
# (آرشیو/ویدیو) هرکدام یک پروسه‌ی جدا چندثانیه‌ای دارند و نباید صف سبک‌ها را
# بگیرند (راهنما: «two queues — fast first, slow afterwards»).
_HEAVY_GROUPS = {"archive", "video"}

# دوره‌ی مانیفست تامبنیل — با هر تغییرِ منطقِ تامبنیل یکی بالا ببر تا
# مانیفست‌های نسخه‌های قبل خودکار بی‌اعتبار و همه‌ی عناوین یک‌بار بازبینی شوند.
_MANIFEST_ERA = "t2"     # (round 19 §۱۴) ns-mtime manifests — old "t1" records are invalidated once
# حداکثر بازتلاشِ اجراهای بعدی برای عنوانی که شکستِ نرمِ آرشیو داشته (0.42.0):
_RETRY_BUDGET = 2


class Prebuilder:
    """مدیر نخ پیش‌ساخت — start/stop امن؛ اسکن جدید، نخ قدیمی را لغو می‌کند."""

    # کارهای موازی خفیف — تامبنیل بیشتر I/O و subprocess است تا محاسبه‌ی خام CPU.
    # سبک‌ها (تصویر/سند) نخ بیشتری می‌گیرند؛ سنگین‌ها استخر جدا.
    # سنگین دو بخش شد — آرشیو (subprocess 7z سنگین → میانه‌رو) و ویدیو که
    # با زنجیره‌ی ویندوزمحور (کش/رندر Shell) غالباً I/O است و نخ بیشتری می‌خواهد.
    # گزارش کاربر: هر فایلِ سنگین ~۳ ثانیه! — تامبنیلِ ویدیو/تصویر بیشترِ
    # زمان‌ش را در subprocess/دیکد می‌گذراند نه CPUِ پایتون — پس نخ‌های بیشتر از
    # تعداد هسته ضرر نیست. روی ۸ هسته: ۱۶ نخِ ویدیو/فایل، ۸ نخِ آرشیو.
    FILE_WORKERS = max(8, min(16, (os.cpu_count() or 4) * 2))
    HEAVY_WORKERS = max(3, min(6, (os.cpu_count() or 4) // 2 + 1))  # سازگاریِ عقب‌رو
    # گزارش کاربر: «cache-limit... cache-size error» + تمایل «همه‌چیز کش
    # باشد ولی سریع» — دیواره‌ی کار واقعی در I/O و subprocess است نه پایتون —
    # برد core-increase دادن: آرشیو ×۲ هسته (۶ تا ۱۲ نخ) و ویدیو ×۳ هسته
    # (۱۲ تا ۲۴ نخ) تا فاز سنگینِ prebuild تقریباً ۲ برابر زودتر تمام شود.
    # (درخواست کاربر: «موازی‌سازی بر اساس مشخصات سخت‌افزاری»): تعداد نخ‌ها
    # دیگر فقط تابع هسته نیست — hwinfo حجم رم را هم می‌خواند (رم‌کم → نخ‌های
    # کم‌تر تا سیستم خفه نشود)، سقف‌ها کمی بالاتر رفت (آرشیو ۱۲→۱۴، ویدیو ۲۴→۲۸)
    # چون گلوگاهِ واقعی I/O درایو outside است نه CPU، و برای عیب‌یابی
    # MC_VIDEO_WORKERS / MC_ARCHIVE_WORKERS بازتعریف می‌کنند.
    ARCHIVE_WORKERS = hwinfo.archive_workers()
    VIDEO_WORKERS = hwinfo.video_workers()
    # مأموریت سرعتِ کاربر — لاگ: «کل ≈۱۵ دقیقه» — این دو فاز تا 0.39.0 روی
    # سقف‌های ثابتِ ۴ و ۳ نخ قفل بودند و تشخیصِ نوعِ دیسک (0.38.0) هم فقط *بعد*
    # از آن‌ها محاسبه می‌شد — پس فاز پوستر (که برای پوشه‌های بدون‌آیکون همان
    # استخراجِ ffmpeg اجرا می‌شود) با ۱۰ هسته فقط ۳ نخ داشت! حالا هر دو
    # در _run و بر اساس hwinfo + نوعِ دیسک تصمیم می‌شوند؛ این مقدارهای کلاس
    # فقط فالبکِ پیش از محاسبه‌اند. MC_POSTER_WORKERS / MC_LIST_WORKERS
    # برای عیب‌یابی بازتعریف می‌کنند (hwinfo.poster_workers / list_workers).
    LIST_WORKERS = hwinfo.list_workers()
    POSTER_WORKERS = hwinfo.poster_workers()

    def __init__(self, store, cache, progress=None, counts=None):
        self.store = store
        self.cache = cache
        # اعلان پیشرفت به UI — callbackهایی که خودشان باید thread-safe به نخ اصلی
        # برسند (در main.py از طریق سیگنال Qt Queued انجام می‌شود).
        self.on_progress = progress     # متن توصیفی → نوار وضعیت
        self.on_counts = counts         # (phase, done, total) → نوار پیشرفت عددی
        self._lock = threading.Lock()
        self._gen = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # رفع رگرسیون گزارش‌شده‌ی «پیش‌ساخت ۱۵ دقیقه!»: وقتی DurationWorker
        # در حال خواندن هدر هزاران ویدیو از همان درایو است، فازهای سنگینِ پیش‌ساخت
        # صبر می‌کنند — هم‌پوشانیِ دو موجِ دیسک‌خوان روی درایو اکسترنال، دیواره‌ی
        # I/O را چند برابر شلوغ می‌کرد (۳:۵۵ → ۱۵:۴۳).
        self._durations_busy = False
        # True اگر فایل‌های سنگین روی دیسک چرخان تشخیص داده شوند — ورکرهای
        # سنگین در _run محافظه‌کار می‌شوند و در خلاصه/لاگ دیده می‌شود.
        self._heavy_rotational = False
        # نخ‌های واقعیِ فاز پوستر/فهرست — در _run بر اساس hwinfo و نوعِ
        # دیسک پر می‌شوند (پیش‌فرض = مقدار کلاس).
        self._poster_workers = self.POSTER_WORKERS
        self._list_workers = self.LIST_WORKERS

    def set_durations_busy(self, flag: bool):
        """از سمت main.py: محاسبه‌ی زمان‌ها در جریان است/تمام شد (0.33.0)."""
        self._durations_busy = bool(flag)

    def _wait_durations(self, gen: int, ev: threading.Event):
        """تا پایانِ موجِ «در حال محاسبه‌ی زمان» صبر کن (نقطه‌ی تصمیمِ فاز سنگین)."""
        while self._alive(gen, ev) and getattr(self, "_durations_busy", False):
            time.sleep(0.40)

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def start(self, items):
        """شروع پیش‌ساخت برای فهرست عناوین (نخ قبلی در صورت وجود لغو می‌شود)."""
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._stop.set()                    # نخ قبلی در اولین فرصت می‌ایستد
            ev = threading.Event()
            self._stop = ev
            t = threading.Thread(
                target=self._run, args=(gen, list(items or []), ev),
                daemon=True, name="media-thumb-prebuild",
            )
            self._thread = t
            t.start()

    def stop(self):
        """لغو نرم — نخ در اولین نقطه‌ی امن خارج می‌شود."""
        with self._lock:
            self._gen += 1
            self._stop.set()

    def _alive(self, gen: int, ev: threading.Event) -> bool:
        return gen == self._gen and not ev.is_set()

    def _progress(self, msg: str):
        cb = self.on_progress
        if cb is None:
            return
        try:
            cb(msg)
        except Exception as _exc_dbg:
            dbg_log(f"_progress failed: {type(_exc_dbg).__name__}")
            pass

    def _counts(self, phase: str, done: int, total: int):
        cb = self.on_counts
        if cb is None:
            return
        try:
            cb(phase, done, total)
        except Exception as _exc_dbg:
            dbg_log(f"_counts failed: {type(_exc_dbg).__name__}")
            pass

    # ---------------------------------------------------------------- اجرای اصلی
    def _run(self, gen: int, items: list, ev: threading.Event):
        try:
            from watchtime import _dbg
        except Exception as _exc_dbg:  # pragma: no cover
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            def _dbg(_m):
                pass
        ft = FileThumbs(self.cache.cache_dir)
        try:
            ft.archive_enabled = bool(self.store.archive_thumbs)
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        # شمارنده‌های مسیرِ ویدیو برای «این اجرا» صفر می‌شوند تا خلاصه‌ی
        # پایانی عددهای واقعیِ همین پیش‌ساخت را بگوید (کاربر عدد دقیق می‌خواهد)
        try:
            import winthumbs as _wst
            _wst.reset_thumbnail_stats()
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        FileThumbs.ffmpeg_video_uses = 0
        FileThumbs._archive_soft_fails = 0     # پنجره‌ی تازه برای گزارش شکست نرم
        FileThumbs._archive_soft_files = set()  # مسیرهای شکست نرمِ این اجرا
        FileThumbs._probe_counts = {}          # پنجره‌ی تازه برای گزارش reprobe
        with FileThumbs._probe_inflight_lock:  # رویدادهای یتيم پاک شوند
            FileThumbs._probe_inflight = {}
        st = self.store
        # مأموریت سرعت: تشخیصِ چرخان‌بودنِ دیسک از *ابتدای* اجرا انجام
        # می‌شود (نمونه: اولین مسیرِ موجودِ هر عنوان — ویدیو/آرشیو/آیکون/خودِ
        # پوشه) تا استخرهای پوستر و فهرست هم دیسک‌آگاه شوند، نه فقط فازهای
        # سنگین؛ بلوکِ دقیق‌ترِ 0.38.0 بعداً دوباره از روی نمونه‌ی فایل‌های سنگین
        # سنجیده و در صورت نیاز فازهای سنگین را هم محافظه‌کار می‌کند.
        self._poster_workers = self.POSTER_WORKERS
        self._list_workers = self.LIST_WORKERS
        try:
            _step0 = max(1, len(items) // 120 + 1)
            _smp0 = []
            for _it0 in items[::_step0][:120]:
                for _p0 in (getattr(_it0, "preview_video", None),
                            getattr(_it0, "preview_archive", None),
                            getattr(_it0, "icon_path", None),
                            getattr(_it0, "path", None)):
                    if _p0:
                        _smp0.append(str(_p0))
                        break
            _rot0 = hwinfo.any_rotational(_smp0) if _smp0 else False
            if _rot0:
                self._heavy_rotational = True
                self._poster_workers = hwinfo.poster_workers(True)
                self._list_workers = hwinfo.list_workers(True)
            elif items:
                self._poster_workers = hwinfo.poster_workers(False)
                self._list_workers = hwinfo.list_workers(False)
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        state = {"n": 0, "t": 0.0}
        count_lock = threading.Lock()
        # گزارش کاربر: «۵۱:۲۵ برای فایل‌های سبک؟!» — ثانیه‌های هر گروه
        # (تصویر/سند/…) جدا شمرده می‌شود تا خلاصه و لاگ دیباگ دقیقاً بگویند زمان
        # کجا رفته — حدس‌زدن تمام شد. مقدارِ این دیکت یعنی «ثانیه‌ی نخِ کارگر»؛
        # تقسیم بر تعداد نخ‌ها ≈ سهمِ واقعیِ آن گروه در زمانِ ساعتِ‌دیواری.
        gsecs: dict = {}

        def _tick(kind: str, total: int, text_tmpl: str):
            with count_lock:
                state["n"] += 1
                n = state["n"]
            now = time.monotonic()
            if n % 5 == 0 or now - state["t"] > 1.2:
                state["t"] = now
                self._counts(kind, n, total)
                self._progress(text_tmpl.format(fa(n), fa(total)))
            if n % 12 == 0:
                time.sleep(0.004)              # ریتم ملایم — منابع برای UI آزاد

        n_posters = n_files = 0
        t0_run = time.monotonic()          # زمان کل روی خلاصه‌ی پایان می‌رود
        _t_lap = t0_run                    # زمانِ هر فاز جدا → تفکیک در خلاصه
        sec_p = sec_l = sec_a = sec_v = 0.0

        def _lap_s() -> float:
            nonlocal _t_lap
            now = time.monotonic()
            s = now - _t_lap
            _t_lap = now
            return s
        try:
            # ── فاز ۰ (0.42.0): اثر انگشتِ محتوا — ردکردنِ هوشمندِ عناوینِ بدون‌تغییر ──
            # درخواست کاربر: «وقتی فایل پشتیبان کامله و هیچ فایلی عوض نشده، نباید
            # دوباره چند دقیقه بررسی بشه». برای هر عنوان اثر انگشتِ همه‌ی فایل‌ها
            # (نامِ نسبی+حجم+mtime) با مانیفستِ ذخیره‌شده در دیتابیس مقایسه
            # می‌شود؛ برابر = ردِ کامل (پوستر/فایل/تلاش‌مجددِ آرشیو هیچ‌کدام).
            # فهرستِ فایل‌ها هم از همین پیمایش نگه داشته می‌شود تا فاز قدیمیِ
            # «فهرست‌سازی» (get_file_tree مجدد) اصلاً اجرا نشود.
            mans: dict = {}            # i → مانیفستِ فعلی
            listed: dict = {}          # i → فهرستِ تختِ فایل‌ها (از پیمایش فاز صفر)
            retry_old: dict = {}       # i → بودجه‌ی بازتلاشِ قبلی (شکستِ نرم)
            to_do: list = []           # اندیس عناوینی که باید بازرسی شوند
            n_skip = 0
            era = _MANIFEST_ERA
            # (round 19 §۱۵) nسلِ کش هم در era می‌آید — بعد از تغییرِ مقیاسِ نمایش
            # (۱۰۰٪→۱۵۰٪) اندازهٔ مؤثرِ کش (256/384/512) عوض می‌شود؛ بدونِ این،
            # پیش‌ساز عناوینِ unchanged را با مانیفستِ نسلِ قدیمی رد می‌کرد و
            # تامبنیلِ نسلِ تازه هیچ‌وقت پیش‌ساخت نمی‌شد.
            try:
                _px = int(getattr(self.cache, "size", (256, 256))[0])
            except Exception:
                _px = 256
            era = f"{era}-{_px}"
            total0 = len(items)
            state["n"] = 0; state["t"] = 0.0
            self._counts("check", 0, total0)
            self._progress(T("🔎 بررسی هوشمندِ تغییرها (۰/{0})…").format(fa(total0)))

            def _probe0(arg):
                i, it = arg
                try:
                    man, files = thumb_manifest(
                        Path(it.path), loose=getattr(it, "loose_group", False))
                except Exception as _exc_dbg:
                    dbg_log(f"_run._probe0 failed: {type(_exc_dbg).__name__}")
                    man, files = None, []   # محتاطانه: پردازش می‌شود ولی مانیفست نه
                return i, man, files

            with ThreadPoolExecutor(self._list_workers) as pool0:
                for i, man, files in pool0.map(_probe0, enumerate(items)):
                    if not self._alive(gen, ev):
                        return
                    mans[i] = man
                    listed[i] = files or []
                    old_r = 0
                    rec = None
                    try:
                        rec = st.thumb_state_of(items[i])
                    except Exception as _exc_dbg:
                        dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                        rec = None
                    if isinstance(rec, dict):
                        try:
                            old_r = int(rec.get("r") or 0)
                        except Exception as _exc_dbg:
                            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                            old_r = 0
                    if rec is not None and rec.get("e") == era \
                            and rec.get("m") == man and old_r <= 0:
                        n_skip += 1                     # بدون تغییر → رد کامل
                    else:
                        to_do.append(i)                 # تازه / تغییرکرده / بازتلاش
                        retry_old[i] = old_r
                    _tick("check", total0, T("🔎 بررسی هوشمندِ تغییرها… {0}/{1}"))
            if not self._alive(gen, ev):
                return
            self._counts("check", total0, total0)
            sec0 = _lap_s()                             # سهمِ فاز اثر انگشت
            if n_skip:
                _msg0 = (T("⏭ {0} عنوان بدون تغییر — تامبنیل‌هایشان سالم است و رد شدند").format(fa(n_skip)))
                if to_do:
                    _msg0 += T(" · 🔎 {0} عنوان بازرسی می‌شود").format(fa(len(to_do)))
                self._progress(_msg0)
            _dbg(f"prebuild manifest: {n_skip} unchanged skipped, {len(to_do)} "
                 f"to check (retry>0: {sum(1 for r in retry_old.values() if r > 0)}) "
                 f"in {sec0:.1f}s")

            # فایل‌های انتخابیِ هر عنوان برای ساخت (همان قواعد قدیمی: بدون
            # آیکون/پوسترِ عنوان، فیلترِ اسنادِ loose، فایل‌های مستقیم در loose)
            sel_files: dict = {}
            for i in to_do:
                it = items[i]
                files_i = listed.get(i) or []
                if getattr(it, "loose_docs_only", False):
                    files_i = [f for f in files_i
                               if f.suffix.lower() in LOOSE_DOC_EXTS]
                _ex = {str(p) for p in (getattr(it, "icon_path", None),
                                        st.poster_of(it, static=True)) if p}   # فریم ساکن (C7)
                if _ex:
                    files_i = [f for f in files_i if str(f) not in _ex]
                sel_files[i] = files_i

            # ── فاز ۱: پوستر عناوین (موازی خفیف) — فقط عناوینِ تغییرکرده/تازه ──
            total_p = len(to_do)
            state["n"] = 0; state["t"] = 0.0
            self._counts("posters", 0, total_p)
            if to_do:
                self._progress(T("⚡ پیش‌ساخت تامبنیل شروع شد — پوستر پوشه‌ها (۰/{0})").format(fa(total_p)))

            def _poster(it):
                if not self._alive(gen, ev):
                    return
                try:
                    # px مؤثرِ کش (اندازه‌ی آگاه از مقیاس نمایش) — پیش‌ساخت همان
                    # اندازه‌ای را می‌سازد که نمایش بعداً می‌خواند
                    poster_png_fallback(
                        self.cache.get, ft,
                        st.poster_of(it, static=True),   # فریم ساکن (C7)
                        getattr(it, "preview_video", None),
                        getattr(it, "preview_pdf", None),
                        getattr(it, "preview_archive", None),
                        getattr(self.cache, "size", (256, 256))[0])
                except Exception as e:
                    # کراشِ تک‌فایل هرگز مسیر نمی‌نویسد — فقط نوعِ استثنا
                    _dbg(T("⚠ خطا در پیش‌ساخت پوستر: {0}").format(type(e).__name__))
                _tick("posters", total_p,
                      T("⚡ پیش‌ساخت پوستر پوشه‌ها… {0}/{1}"))

            if to_do:
                with ThreadPoolExecutor(self._poster_workers) as pool:
                    list(pool.map(_poster, (items[i] for i in to_do)))
            if not self._alive(gen, ev):
                return
            n_posters = state["n"]
            self._counts("posters", n_posters, total_p)
            sec_p = _lap_s()                         # سهمِ فاز پوستر
            _dbg(f"prebuild: posters phase done ({n_posters}) in {sec_p:.1f}s")

            # «فاز فهرست‌سازی» جدا دیگر لازم نیست — فهرستِ فایل‌ها از
            # پیمایشِ فاز صفر (اثر انگشت) آمده و فقط باید به صف‌ها تقسیم شود.
            plan_light: list = []          # تصویر/سند/متفرقه — سریع (0.24.0)
            plan_archive: list = []        # آرشیوها — پروسه‌ی 7z (0.25.0: فاز مستقل)
            plan_video: list = []          # ویدیوها — Shell/winthumb → ffmpeg (0.25.0)
            arc_item_of: dict = {}         # مسیر آرشیو → اندیس عنوان (شکستِ نرم)
            for i in to_do:
                for f in (sel_files.get(i) or []):
                    # صف سبک/سنگین؛ 0.25.0: سنگین خودش دو بخش شد
                    g = group_of(f)
                    if g == "archive":
                        plan_archive.append(f)
                        arc_item_of[str(f)] = i
                    elif g == "video":
                        plan_video.append(f)
                    else:
                        plan_light.append(f)
            del listed
            total_l = len(plan_light)
            total_a = len(plan_archive)
            total_v = len(plan_video)

            # گزارش کاربر + راهنما: اگر فایل‌های سنگین روی دیسکِ چرخان
            # (HDD) باشند، ده‌ها خواننده‌ی موازی هدِ دیسک را به‌سا می‌کنند و
            # همه‌چیز کندتر می‌شود → ورکرهای سنگین خودکار محافظه‌کار می‌شوند.
            # تشخیص از روی خودِ همین ماشین است (IOCTL/sysfs — چیزی hardcode
            # نیست؛ روی SSD/NVMe یا ناشناخته همان سقف‌های سریعِ قبلی می‌ماند).
            try:
                _smp = (plan_archive + plan_video)[:: max(1, len(plan_archive + plan_video) // 120 + 1)][:120]
                _rot = hwinfo.any_rotational(_smp)
                self._heavy_rotational = bool(_rot)
                if _rot:
                    self.ARCHIVE_WORKERS = hwinfo.archive_workers(True)
                    self.VIDEO_WORKERS = hwinfo.video_workers(True)
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                self._heavy_rotational = False

            # ── فاز ۲ب: ساخت موازی تامبنیل فایل‌های «سبک» ───────────────────
            state["n"] = 0; state["t"] = 0.0
            self._counts("files", 0, total_l)
            self._progress(T("⚡ پیش‌ساخت تامبنیل فایل‌ها (۰/{0})…").format(fa(total_l)))

            def _file(f, kind="files", total=0, tmpl="", on_none=None):
                if not self._alive(gen, ev):
                    return
                # درخواست کاربر + راهنما: «اولویت با بخشیه که کاربر داره
                # میبینه؛ برنامه همیشه قابل استفاده باشد» — اگر UI همین حالا در
                # حال درخواستِ تامبنیل است (مرور گرید جزئیات/کارت‌ها)، فازهای
                # پیش‌ساخت مکث می‌کنند تا دیسک/CPU برای همان صفحه‌ی جلوی چشم
                # کاربر آزاد بماند. ~۲.۵ ثانیه بعد از آرام‌شدن UI کار از سر گرفته
                # می‌شود — برای کاربر «صبرِ چنددقیقه‌ای» دیگر معنا ندارد.
                while self._alive(gen, ev) and FileThumbs.ui_active():
                    time.sleep(0.30)
                _ta = time.monotonic()     # سهمِ این فایل در زمانِ گروهش
                try:
                    _got = ft.get(f, 132)  # سایز گرید جزئیات — همان کش مشترک
                    # شکستِ آرشیو به عنوان صاحبش نسبت داده می‌شود (بازتلاشِ
                    # کران‌دار). ft.get موقعِ شکست «کارت نوع‌فایل» برمی‌گرداند نه
                    # None — پس رجیستریِ مسیرهای شکست‌نرمِ filethumbs ملاک است.
                    if on_none is not None and (
                            _got is None
                            or str(f) in FileThumbs._archive_soft_files):
                        try:
                            on_none(f)
                        except Exception as _exc_dbg:
                            dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                            pass
                except Exception as _exc_dbg:
                    dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                    pass
                try:
                    dt = time.monotonic() - _ta
                    with count_lock:
                        gsecs[group_of(f)] = gsecs.get(group_of(f), 0.0) + dt
                except Exception as _exc_dbg:
                    dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                    pass
                _tick(kind, total, tmpl)

            if total_l:
                with ThreadPoolExecutor(self.FILE_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "files", total_l,
                        T("⚡ پیش‌ساخت تامبنیل فایل‌ها… {0}/{1}")), plan_light,
                        chunksize=8)
            if not self._alive(gen, ev):
                return
            n_files = state["n"]
            sec_l = _lap_s()                         # فهرست + فایل‌های سبک

            # ── فاز ۲ج: آرشیوها — subprocessهای لیست/استخراج (0.25.0 فاز مستقل) ──
            # گزارش کاربر: «۲۳۴۳۰ تامبنیل ۱۳:۵۲» — سنگین‌ها به‌صورت پیش‌فرض
            # اصلاً پیش‌ساخته نمی‌شوند — روی کتابخانه‌ی ویدیوییِ بزرگ ساختِ eagerِ
            # همه‌ی ویدیو/فشرده‌ها با هیچ بهینه‌سازیِ تک‌فایلی به سیعادِ اکسپلورر
            # نمی‌رسد؛ مثل اکسپلورر: حالا placeholder/کش و وقتی فایل واقعاً دیده
            # شده تامبنیل واقعی (visible-first در صفحه‌ی جزئیات). گزینه‌ی صریح
            # «پیش‌ساخت سنگین‌ها هم» در تنظیمات رفتارِ قبلی را برمی‌گرداند.
            # (درخواست کاربر: «گزینه‌ی سنگین باید حذف بشه»): هدفِ پیش‌ساخت
            # دقیقاً این است که تامبنیلِ همه‌ی فایل‌ها ساخته شده باشد تا برنامه
            # همیشه سریع بماند — پس با پیش‌ساختِ روشن، فازهای آرشیو/ویدیو همیشه
            # اجرا می‌شوند (البته به مدلِ 0.31.0: در پس‌زمینه و با مکث هنگام مرور UI).
            # مجموعه‌ی عناوینی که آرشیوی‌شان این اجرا شکست خورد (بازتلاشِ کران‌دار)
            soft_items: set = set()

            def _arc_none(f):
                try:
                    i0 = arc_item_of.get(str(f))
                    if i0 is not None:
                        with count_lock:
                            soft_items.add(i0)
                except Exception as _exc_dbg:
                    dbg_log(f"_run._arc_none failed: {type(_exc_dbg).__name__}")
                    pass

            heavy_on = True
            if total_a and heavy_on:
                self._wait_durations(gen, ev)     # اول زمان‌ها، بعد سنگین‌ها
                state["n"] = 0; state["t"] = 0.0
                self._counts("archives", 0, total_a)
                self._progress(
                    T("📦 پیش‌ساخت تامبنیل آرشیوها (۰/{0})…").format(fa(total_a)))
                with ThreadPoolExecutor(self.ARCHIVE_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "archives", total_a,
                        T("📦 پیش‌ساخت تامبنیل آرشیوها… {0}/{1}"),
                        on_none=_arc_none), plan_archive,
                        chunksize=2)
                if not self._alive(gen, ev):
                    return
                n_files += state["n"]
                sec_a = _lap_s()                     # سهمِ فاز آرشیوها

            # ── فاز ۲د: ویدیوها — مسیرِ سریعِ ویندوز اول (0.25.0) / تنبل (0.26.0) ──
            if total_v and heavy_on:
                self._wait_durations(gen, ev)
                state["n"] = 0; state["t"] = 0.0
                self._counts("videos", 0, total_v)
                self._progress(
                    T("🎬 پیش‌ساخت تامبنیل ویدیوها (۰/{0})…").format(fa(total_v)))
                with ThreadPoolExecutor(self.VIDEO_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "videos", total_v,
                        T("🎬 پیش‌ساخت تامبنیل ویدیوها… {0}/{1}")), plan_video,
                        chunksize=4)
                if not self._alive(gen, ev):
                    return
                n_files += state["n"]
                sec_v = _lap_s()                     # سهمِ فاز ویدیوها
            if (total_a or total_v) and not heavy_on:
                self._progress(
                    T("🏎 {0} فایل سنگین (ویدیو/فشرده) — موقعِ دیده‌شدن ساخته می‌شوند (پیش‌ساخت تنبل)").format(fa(total_a + total_v)))

            total_f = total_l + total_a + total_v
            self._counts("done", n_files, total_f)
            # درخواست کاربر: زمانِ دقیقِ کل در خلاصه‌ی پایان — هم کاربر می‌فهمد
            # چقدر طول کشیده، هم خودِ کاربر هنگام تست‌کردن عدد دقیق را می‌بیند.
            el_run = time.monotonic() - t0_run
            _dbg(f"prebuild done: {n_posters} posters + {n_files} file thumbs "
                 f"({n_skip} unchanged skipped) "
                 f"in {el_run:.1f}s (heavy={'on' if heavy_on else 'lazy'}, "
                 f"workers: poster={self._poster_workers} list={self._list_workers} "
                 f"video={self.VIDEO_WORKERS} archive={self.ARCHIVE_WORKERS}"
                 f"{' · دیسک چرخان→محافظه‌کار' if self._heavy_rotational else ''}"
                 f" · عملکرد={hwinfo.perf_mode()}, "                    # شفافیتِ حالت
                 f"hw: {hwinfo.hw_summary()})")
            # تفکیک مسیرِ ویدیوها در خلاصه — کاربر دقیقاً می‌بیند «سرعتِ
            # اکسپلورر» چقدر به کار آمد (کش) و ffmpeg فقط برای چند فایل افتاد.
            # در حالت تنبل، خلاصه صریح می‌گوید سنگین‌ها موقعِ دیدنشان ساخته
            # می‌شوند (نه اینکه عددهای صفرِ کش/رندر سؤال ایجاد کنند).
            vinfo = ""
            if total_v and heavy_on:
                try:
                    import winthumbs as _wst
                    ss = _wst.shell_thumbnail_stats()
                    vinfo = (T(" · ویدیوها: کش‌ویندوز {0} · رندر {1} · ffmpeg {2}").format(fa(ss['cache_hit']), fa(ss['render_hit']), fa(FileThumbs.ffmpeg_video_uses)))
                except Exception as _exc_dbg:
                    dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                    vinfo = ""
            elif (total_a or total_v) and not heavy_on:
                vinfo = (T(" · 🏎 {0} موردِ سنگین (موقعِ دیدنشان ساخته می‌شوند)").format(fa(total_a + total_v)))
            # گزارش کاربر: «ffmpeg 31320 بار» — اگر فایلی در همین اجرا بیش از
            # یک‌بار پروب شده باشد، تکرارها این‌جا ظاهر می‌شوند (عادی = هیچ) تا
            # ریشه‌ی دوباره‌کاریِ کش در گزارش بعدیِ کاربر دیده شود.
            try:
                _rt = FileThumbs.reprobe_total()
                if _rt > 0:
                    vinfo += f" · ⚠ reprobe {fa(_rt)}"
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
            # گزارش کاربر: «یک آرشیو تامبنیل نگرفت» — شکست‌های نرمِ آرشیو
            # صریح می‌شوند — این موارد کشِ منفی نمی‌گیرند. 0.42.0: بازتلاش کران‌دار
            # است (حداکثر ۲ اجرای بعدی، مدیده با مانیفست)؛ نه هر بار تا ابد.
            try:
                if FileThumbs._archive_soft_fails > 0:
                    vinfo += (T(" · ⚠ {0} آرشیو تلاش‌ناموفق (حداکثر {1} اجرای بعدی بازتلاش)").format(fa(FileThumbs._archive_soft_fails), fa(_RETRY_BUDGET)))
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
            # ثبتِ مانیفستِ تامبنیل — فقط وقتی که هیچ لغوی در کار نبوده.
            # مانیفستِ «نیمه‌کاره» خطرناک است (ردکردنِ اشتباه در اجرای بعد)؛ پس
            # یا کامل ثبت می‌شود یا همان مانیفستِ قبلی می‌ماند. شکستِ نرمِ
            # آرشیو → بودجه‌ی بازتلاش: تازه‌کارها ۲ بار و مصرف‌شده‌ها کم‌کم صفر
            # می‌شوند تا تلاشِ بی‌ثمرِ هر-بوت (گزارشِ «۴ دقیقه کارِ بیهوده»)
            # برای همیشه تمام شود؛ اگر موفق شدند بودجه صفر می‌شود.
            try:
                _states = {}
                for i in to_do:
                    _man = mans.get(i)
                    if not _man:
                        continue
                    _or = int(retry_old.get(i, 0) or 0)
                    if i in soft_items:
                        _nr = max(0, _or - 1) if _or > 0 else _RETRY_BUDGET
                    else:
                        _nr = 0
                    _states[str(items[i].path)] = {"m": _man, "r": _nr, "e": era}
                if _states:
                    st.set_thumb_states(_states)
                    _dbg(f"prebuild manifest: saved {len(_states)} thumb manifests "
                         f"(soft-retry: {sum(1 for v in _states.values() if v['r'] > 0)})")
            except Exception as e:
                _dbg(f"thumb manifest save failed: {type(e).__name__}")
            # گزارش کاربر: «۵۱:۲۵ — کجا رفت؟!» — خلاصه‌ی پایان زمان را
            # فازبه‌فاز تفکیک می‌کند — کاربر دقیقاً می‌بیند سهمِ هر بخش چقدر بود؛
            # سهمِ گروه‌های سبک (تصویر/سند/…) هم با جزئیات در لاگ دیباگ می‌نشیند.
            segs = T("پوستر {0} · سبک {1}").format(fa_mmss(sec_p), fa_mmss(sec_l))
            if sec_a > 0:
                segs += f" · 📦 {fa_mmss(sec_a)}"
            if sec_v > 0:
                segs += f" · 🎬 {fa_mmss(sec_v)}"
            # خلاصه صریح می‌گوید چند عنوان بدون تغییر بود و رد شد —
            # دقیقاً همان عددی که کاربر برای «کارِ بیهوده نکردن» می‌خواهد ببیند.
            _skip_txt = (T("⏭ {0} عنوان بدون‌تغییر · ").format(fa(n_skip)) if n_skip else "")
            self._progress(
                T("✅ پیش‌ساخت تامبنیل کامل شد — {0}{1} پوستر · {2} تامبنیل فایل · ⏱ {3} ({4}){5}").format(_skip_txt, fa(n_posters), fa(n_files), fa_mmss(el_run), segs, vinfo))
            try:
                if gsecs:
                    _top = sorted(gsecs.items(), key=lambda kv: -kv[1])[:5]
                    _dbg("prebuild group secs: " + ", ".join(
                        f"{k}={v:.1f}s" for k, v in _top))
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
        except Exception as e:
            _dbg(f"prebuild aborted: {e}")
