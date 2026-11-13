# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""مدیریت تنظیمات و داده‌های کاربر.

- config.json: مسیرها، دسته‌ها، رنگ دسته‌ها، گزینه‌ها (کوچک — خواندنی/ویرایش‌پذیر)
- data.db (SQLite): اطلاعات هر عنوان (نام نمایشی، دسته، توضیحات، پوستر سفارشی،
  علاقه‌مندی، زمان‌ها، مانیفست فایل‌ها، وضعیت تماشا) — کلید = مسیر پوشه

الگوهای وضعیت (نسخه‌ی 0.15.0): هر دسته می‌تواند الگوی علامت‌گذاری خودش را داشته باشد —
«فیلم و سریال» (دیده شده/نیمه‌تمام + ترک خودکار)، «کتاب و مانگا» (خوانده شده/نیمه‌خوانده)،
«بدون ترکینگ»، یا هر الگوی سفارشی که کاربر می‌سازد (مثل «تمرین ورزشی»: انجام شد /
در حال انجام / انجام نشده). تنظیم در config: cat_track_profile + track_profiles.

چرا SQLite: با هزاران عنوان، بازنویسیِ کاملِ یک JSON بزرگ برای هر تغییر کوچک
(مثل ذخیره‌ی زنده‌ی موقعیت هر ۱۰ ثانیه) کُند و گیرکردنی می‌شود؛ SQLite هر بار
فقط همان یک ردیف را می‌نویسد (تراکنش WAL، بدون وابستگی خارجی — sqlite3 خود پایتون).

مهاجرت خودکار: اگر data.json قدیمی وجود داشته باشد، در اولین اجرا به data.db
منتقل و فایل JSON به .bak تغییر نام می‌یابد. رابطه‌ی عمومی Store دست‌نخورده است.

اگر از --config یک فایل دیگر استفاده شود (مثل دمو)، دیتابیس همان ساخته می‌شود
(config.demo.json -> data.demo.db، با مهاجرت از data.demo.json در صورت وجود).
"""
from __future__ import annotations

import i18n as _i18n
from i18n import T

import copy
import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import portable
from watchtime import dbg_log

# گزارش کاربر: اسم‌های مخدوش دکمه/هدر را می‌شکنند — کنترل‌کاراکترها و
# دنباله‌های ANSI از متنِ نمایشی حذف می‌شوند — یک اسمِ فایل با \x00 یا newline
# نباید لیبل را چندخطی کند یا رنگ/فرمت ترمینال‌گونه تزریق کند.
_CTRL_ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


def ui_clean(text) -> str:
    """متنِ امن برای نمایش در یک خطِ UI (بدون null/ANSI/شکستِ خط، فاصله‌ی یکدست)."""
    s = _CTRL_ANSI_RE.sub("", str(text or ""))
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def _meta_key(path) -> str:
    """کلیدِ متا: فرمِ کانونیِ Path — «رشته‌ی خام» و «Path» به یک کلید می‌رسند.

    روی ویندوز str(Path("C:/a/b")) به «C:\\a\\b" عادی می‌شود؛ کلیدی که با
    رشته‌ی خامِ «C:/a/b» (مثل apiهای کاور و تست‌ها) نوشته شده بود پیدا نمی‌شد
    و کاورِ سفارشی/امتیازِ همان عنوان «گم» می‌شد — ۲ تستِ قرمزِ ویندوزِ
    round 12 دقیقاً همین بود (روی لینوکس هر دو فرم یکی‌اند و باگ دیده نمی‌شد).
    Path های واقعی عیناً همان str قبلی را می‌دهند — رفتارِ موجود دست‌نخورده.
    """
    try:
        return str(Path(path))
    except (TypeError, ValueError):
        return str(path)

PALETTE = [
    "#a78bfa",  # بنفش
    "#38bdf8",  # آبی آسمانی
    "#4ade80",  # سبز
    "#fbbf24",  # کهربایی
    "#fb7185",  # صورتی
    "#2dd4bf",  # فیروزه‌ای
    "#e879f9",  # سرخابی
    "#fb923c",  # نارنجی
    "#a3e635",  # لیمویی
    "#22d3ee",  # سیان
]

DEFAULT_CONFIG = {
    "roots": [],
    "categories": [],
    "category_colors": {},
    "include_image_fallback": True,
    "theme": "dark",
    "compute_durations": True,
    "cat_status_off": [],          # قدیمی (0.14.0) — در الگوها به none ترجمه می‌شود
    "cat_track_profile": {},       # دسته → کلید الگو (با ارث از والد)
    "track_profiles": {},          # الگوهای سفارشی کاربر {id: {title, done, part, none}}
    "folder_thumbs_only": False,   # True → برای فایل‌ها تامبنیل واقعی ساخته نشود
    "prebuild_thumbs": True,    # درخواست کاربر: پیش‌ساخت پیش‌فرض روشن — در پس‌زمینه و با رعایتِ اولویتِ UI
    # (درخواست کاربر): برنامه دوزبانه شد — پیش‌فرض **انگلیسی** (مخاطب گیت‌هاب)؛
    # انتخاب کاربر همین‌جا ذخیره می‌شود و در اجرای بعدی برقرار است.
    "language": "en",              # "en" | "fa"
    # درخواست کاربر — بازبینی تنظیمات/بهینه‌سازی: حالت عملکردِ پیش‌ساخت.
    # auto = تشخیص نوع دیسک | high = همیشه تمام‌توان (SSD) | safe = همیشه محافظه‌کار (HDD)
    "perf_mode": "auto",
    # درخواست کاربر: گزینه‌ی «پیش‌ساخت سنگین‌ها» حذف شد — پیش‌ساختِ روشن
    # یعنی ساختِ *همه‌ی* تامبنیل‌ها (سبک+سنگین) در پس‌زمینه.
    # (درخواست کاربر): «همه‌چیز کنار برنامه — هیچ‌چیز در مسیر سراسری» →
    # محلِ کش دیگر تنظیمی ندارد (کلیدهای cache_loc_* حذف شدند؛ بقایای آن‌ها در
    # فایل‌های قدیمی بی‌اثر است). کش همیشه کنار برنامه است (paths.thumb_cache_dir).
    # سقفِ پیش‌فرضِ حجمِ کش: قبلاً 0 (بی‌سقف) بود — با تغییرِ مقیاسِ نمایش کلیدِ
    # کش عوض می‌شود و نسلِ قبل هرگز پاک نمی‌شد (D10)؛ حالا ۲ گیگ LRU پیش‌فرض است.
    "cache_max_mb": 2048,
}

# ---------------------------------------------------------------- الگوهای وضعیت داخلی
# سه حالت پایه: شروع‌نشده (none) / نیمه (part) / تمام (done)
# auto = برچسب بر اساس نوع فایل انتخاب می‌شود (رفتار پیش‌فرض قبلی)
BUILTIN_TRACK_PROFILES = {
    "auto": {
        "title": "خودکار — بر اساس نوع فایل",
        "vid": {"done": "دیده شده", "part": "نیمه‌تمام", "act": "تماشا"},
        "doc": {"done": "خوانده شده", "part": "نیمه‌خوانده", "act": "خواندن"},
    },
    "video": {
        "title": "فیلم و سریال — تماشا",
        "done": "دیده شده", "part": "نیمه‌تمام", "act": "تماشا",
    },
    "book": {
        "title": "کتاب و مانگا — خواندن",
        "done": "خوانده شده", "part": "نیمه‌خوانده", "act": "خواندن",
    },
    "none": {"title": "بدون ترکینگ", "done": "", "part": "", "act": ""},
}

_BOOK_HINTS = ("کتاب", "مانگا", "کمیک", "رمان", "daisy", "manga", "comic",
               "book", "novel", "مانهوا", "مانوا", "وب‌تون", "وب تون")
DEFAULT_DATA = {"items": {}}


def _quarantine_db_files(db_path: Path) -> list[str]:
    """فایلِ خراب + سایدکارهایش را با پسوند زمانی کنار می‌گذارد (فهرستِ نام‌ها).

    0.41.0 (گزارش کاربر: «برنامه با database disk image is malformed بالا نیامد»):
    به‌جای کراش در بوت، فایلِ آسیب‌دیده قرنطینه می‌شود تا برنامه با دیتابیسِ
    تازه بالا بیاید؛ نامِ فایل‌های قرنطینه برای نمایشِ یک‌باره به کاربر برمی‌گردد
    (فقط نام، بدون مسیر)."""
    import datetime as _dt
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    moved: list[str] = []
    db_path = Path(db_path)
    for cand in (db_path,) + tuple(db_path.with_name(db_path.name + sfx)
                                   for sfx in ("-wal", "-shm", "-journal")):
        try:
            if cand.exists():
                dst = cand.with_name(cand.name + ".corrupt-" + stamp)
                try:
                    cand.rename(dst)
                except OSError:
                    import shutil as _sh
                    _sh.copy2(str(cand), str(dst))
                    cand.unlink()
                moved.append(dst.name)
        except OSError as e:
            # انتشار عمومی: اگر قرنطینه‌ی فایلِ خراب ممکن نشد، تلاشِ بعدیِ
            # بازکردنِ دیتابیس هم شکست می‌خورد — دلیلش در لاگ بماند
            try:
                from watchtime import _dbg
                _dbg(f"db quarantine failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_quarantine_db_files failed: {type(_exc_dbg).__name__}")
                pass
    return moved


def _open_db_fresh(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "  path TEXT PRIMARY KEY,"
            "  meta TEXT NOT NULL"
            ")"
        )
        # آزمونِ سلامت همین‌جا — خرابیِ پنهان تا وسطِ کار مخفی نمی‌ماند
        res = con.execute("PRAGMA quick_check").fetchone()
        if not res or str(res[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"quick_check failed: {res[0] if res else '?'}")
        return con
    except sqlite3.DatabaseError:
        # انتشار عمومی: اگر PRAGMA/quick_check روی فایلِ خراب شکست بخورد، اتصال
        # باید *قبل از* تلاشِ قرنطینه بسته شود — روی ویندوز هندلِ بازِ فایل،
        # rename/unlinkِ قرنطینه را با WinError 32 قفل می‌کرد و بوتِ برنامه روی
        # دیتابیسِ آسیب‌دیده کرش می‌کرد (لینوکس به هندلِ باز حساس نیست).
        # رفتارِ مسیرِ سالم هیچ تغییری نمی‌کند.
        con.close()
        raise


def _open_db(db_path: Path) -> tuple:
    """اتصال مشترک SQLite + ساخت جدول در صورت نبود → (کانکشن، فهرست قرنطینه).

    0.41.0 (باگِ کاربر): اگر فایل base خراب باشد، به‌جای کراش، همراهِ سایدکارهایش
    قرنطینه می‌شود و دیتابیسِ تازه‌ای ساخته می‌شود — برنامه همیشه بالا می‌آید."""
    try:
        return _open_db_fresh(db_path), []
    except sqlite3.DatabaseError:
        moved = _quarantine_db_files(db_path)
        return _open_db_fresh(db_path), moved


class Store:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        name = self.config_path.name
        base = name.replace("config", "data", 1) if name.startswith("config") else "data.json"
        base = Path(base).stem                      # data / data.demo
        self.data_path = self.config_path.with_name(base + ".json")   # مسیر قدیمی (مهاجرت)
        self.db_path = self.config_path.with_name(base + ".db")       # SQLite جدید

        self._lock = threading.RLock()
        # گزارش کاربر: «ذخیره را زدم چند ثانیه گیر کرد و هنگ کرد» — با یک
        # بارِ «ذخیره» در تنظیمات، ۸+ setter هرکدام یک نوشتنِ اتمیکِ config (+فلاش
        # SQLite) روی دیسک می‌زدند — روی ویندوز با آنتی‌ویروسِ بلادرنگ این یعنی
        # چند ثانیه فریزِ رابط. defer_save همه را جمع می‌کند و یک‌بار فلاش می‌کند.
        self._defer = 0
        self._cfg_dirty = False
        self._data_deferred = False
        # خودترمیمیِ دیتابیسِ خراب — فهرست فایل‌های قرنطینه (نام، بدون مسیر)
        self._con, self.db_quarantined = _open_db(self.db_path)
        self._dirty: set[str] = set()               # مسیرهای تغییرکرده در انتظار فلاش
        # (C8) پرچمِ شکستِ آخرین فلاشِ دیتابیس — نوارِ وضعیتِ main آن را می‌بیند
        # تا «ذخیره‌ی اطلاعات ممکن نشد» اعلام شود (الگویِ read_only_fallback)
        self.save_failed = False
        # شمارنده‌ی تغییر داده/تنظیم — «پشتیبان خودکار فقط هنگام تغییر واقعی»
        # (باید پیش از نخستین save_config احتمالیِ __init__ مقداردهی شود)
        self.data_rev = 0
        self.cfg_rev = 0

        # deepcopy حیاتی: لیست categories و دیکت colors/items در کپی کم‌عمق
        # بین همه‌ی نمونه‌های یک پروسه مشترک می‌شدند (نشتی دسته‌ها بین استورها)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                _raw_cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(_raw_cfg, dict):
                    # مهاجرت زبان — قاعده در «یک» جا است: i18n.lang_from_config (D14)
                    _raw_cfg["language"] = _i18n.lang_from_config(_raw_cfg)
                    self.config.update(_raw_cfg)
            except Exception as e:
                # انتشار عمومی: configِ ناخوانا بی‌صدا نماند — پیش‌فرض‌ها می‌آیند
                # ولی دلیلش در لاگ هست (بدون مسیر — همان قراردادِ _redact)
                try:
                    from watchtime import _dbg
                    _dbg(f"config load failed - defaults kept: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"__init__ failed: {type(_exc_dbg).__name__}")
                    pass
        else:
            self.save_config()

        # بررسی فلگ پاکسازی کامل — حتی اگر فایل‌ها قفل بوده و پاک نشده‌اند،
        # داده‌ها از درون پاک شوند تا دسته‌ها/زیردسته‌ها/وضعیت تماشا باقی نمانند.
        self._check_factory_reset_flag()

        self.data = copy.deepcopy(DEFAULT_DATA)
        self.data.setdefault("items", {})
        self._load_items()
        self._migrate_legacy_json()
        # (round 19 §۱۳) کلیدهای خامِ نسخه‌های قدیمی/backup واردشده یک‌بار به
        # فرمِ کانونیِ _meta_key منتقل می‌شوند — وگرنه متای همان عنوان نیمه‌ای
        # نمرئی/تکراری می‌شود (APIهای فعلی همه کانونی می‌نویسند، خواندنِ قدیمی نه).
        self._canonicalize_item_keys()

    def _canonicalize_item_keys(self):
        """کلیدهای خامِ دیتابیس/JSON قدیمی → فرمِ کانونیِ _meta_key (یک‌بار، امن).

        قاعده‌ی برخورد (ممیزیِ round 19 §۱۳ — «کورکورانه rename/merge نکن»):
        فقط کلیدهایی جابه‌جا می‌شوند که فرمِ کانونی‌شان *با خودشان* فرق دارد؛
        اگر کلیدِ کانونی از قبل باشد، دیکشنری‌ها *بدونِ بازنویسیِ مقدارهای موجود*
        ادغام می‌شوند (setdefault — داده‌ی فعلی همیشه برنده است) و شمارش در لاگ
        بی‌نام می‌رود. پیش از هر تغییر، یک نسخه‌ی پشتیبانِ یک‌باره از data.db
        کنار گذاشته می‌شود (‎.pre-canonical.bak‎ — فقط بارِ اول)."""
        try:
            self._canonicalize_item_keys_impl()
        except Exception as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys failed: {type(_exc_dbg).__name__}")

    # ------------------------------------------------------------ لایه‌ی SQLite
    def _load_items(self):
        """خواندن همه‌ی ردیف‌ها به حافظه (همان مدل قبلی؛ حجمش هم‌اندازه‌ی JSON قدیم است)."""
        try:
            with self._lock:
                cur = self._con.execute("SELECT path, meta FROM items")
                for p, m in cur.fetchall():
                    try:
                        self.data["items"][p] = json.loads(m)
                    except ValueError:
                        continue
        except sqlite3.Error as e:
            # انتشار عمومی: خواندنِ آیتم‌ها شکست خورده (کتابخانه خالی دیده
            # می‌شود) — بی‌صدا نماند
            try:
                from watchtime import _dbg
                _dbg(f"items load from SQLite failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_load_items failed: {type(_exc_dbg).__name__}")
                pass

    def _migrate_legacy_json(self):
        """ورود data.json قدیمی به SQLite (یک‌بار) + تغییر نام فایل به .bak."""
        if not self.data_path.exists():
            return
        if self.data["items"]:      # دیتابیس از قبل پر است — JSON قدیمی را فقط کنار بگذار
            try:
                self.data_path.rename(self.data_path.with_suffix(".json.bak"))
            except OSError as e:
                try:
                    from watchtime import _dbg
                    _dbg(f"legacy data.json rename failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                    pass
            return
        try:
            legacy = json.loads(self.data_path.read_text(encoding="utf-8"))
        except Exception as _exc_dbg:
            dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
            legacy = {}
        items = legacy.get("items") or {}
        if items:
            self.data["items"] = items
            with self._lock:
                try:
                    self._con.executemany(
                        "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)",
                        [(p, json.dumps(m, ensure_ascii=False)) for p, m in items.items()],
                    )
                    self._con.commit()
                except sqlite3.Error as e:
                    # انتشار عمومی: مهاجرتِ data.json قدیمی به SQLite شکست خورد —
                    # آیتم‌ها فقط در حافظه‌اند و با بستنِ برنامه می‌پرند؛ بی‌صدا نماند
                    try:
                        from watchtime import _dbg
                        _dbg(f"legacy data.json import into SQLite failed: "
                             f"{type(e).__name__}")
                    except Exception as _exc_dbg:
                        dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                        pass
        try:
            self.data_path.rename(self.data_path.with_suffix(".json.bak"))
        except OSError as e:
            try:
                from watchtime import _dbg
                _dbg(f"legacy data.json rename failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                pass

    def _check_factory_reset_flag(self):
        """0.50.0: بررسی فلگ پاکسازی کامل — حتی اگر فایل‌ها قفل بوده و پاک نشده‌اند،
        داده‌ها از درون پاک شوند تا دسته‌ها/زیردسته‌ها/وضعیت تماشا باقی نمانند.
        کش تامبنیل‌ها هم پاک می‌شود."""
        try:
            import paths as _p
            flag = _p.factory_reset_flag_path()
            if not flag.exists():
                return
            # فلگ وجود دارد → پاکسازی داخلی کامل
            # ۱) پاک‌کردن تمام ردیف‌های items از SQLite
            try:
                with self._lock:
                    self._con.execute("DELETE FROM items")
                    self._con.commit()
            except Exception as e:
                # انتشار عمومی: پاک‌سازیِ دیتابیس در بازنشانیِ کامل شکست خورد —
                # بی‌صدا نماند (فایل‌های قفل‌شده ممکن است باقی بمانند)
                try:
                    from watchtime import _dbg
                    _dbg(f"factory reset: items wipe failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                    pass
            # ۲) پاک‌کردن config به پیش‌فرض
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.data = copy.deepcopy(DEFAULT_DATA)
            self.data.setdefault("items", {})
            # ۳) نوشتن config خالی
            try:
                self.save_config()
            except Exception as e:
                try:
                    from watchtime import _dbg
                    _dbg(f"factory reset: config rewrite failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                    pass
            # ۴) 0.50.0: پاک‌سازی کش تامبنیل‌ها از روی دیسک
            try:
                import shutil
                cache_dir = _p.thumb_cache_dir()
                if cache_dir and cache_dir.exists():
                    for item in cache_dir.iterdir():
                        try:
                            if item.is_dir():
                                shutil.rmtree(str(item))
                            else:
                                item.unlink()
                        except OSError:
                            pass
            except Exception as _exc_dbg:
                dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                pass
            # ۵) پاک‌سازی کش داخل حافظه (durations و thumb_cache) + کاورهای سفارشی
            try:
                app_dir = self.config_path.parent
                for sub in (".thumb_cache", ".covers"):
                    d = app_dir / sub
                    if d.exists():
                        import shutil as _sh
                        _sh.rmtree(str(d), ignore_errors=True)
            except Exception as _exc_dbg:
                dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                pass
            # ۶) حذف فلگ (موفقیت‌آمیز بود)
            try:
                flag.unlink(missing_ok=True)
            except OSError:
                pass
        except Exception as _exc_dbg:
            dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
            pass

    def _canonicalize_item_keys_impl(self):
        items = self.data.get("items", {})
        renames: list[tuple[str, str]] = []
        for p in list(items):
            if not isinstance(items.get(p), dict):
                continue
            canon = _meta_key(p)
            if canon != p:
                renames.append((p, canon))
        if not renames:
            return
        # پشتیبانِ یک‌باره‌ی DB پیش از اولین مهاجرت — بازگشتِ دستی ممکن بماند
        try:
            if self.db_path.is_file():
                bak = self.db_path.with_name(self.db_path.name + ".pre-canonical.bak")
                if not bak.exists():
                    shutil.copy2(str(self.db_path), str(bak))
        except OSError as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys backup failed: {type(_exc_dbg).__name__}")
        merged = 0
        with self._lock:
            for old, canon in renames:
                old_meta = items.pop(old, None) or {}
                if canon in items and isinstance(items[canon], dict):
                    cur = items[canon]
                    for k, v in old_meta.items():
                        cur.setdefault(k, v)     # مقدارِ موجودِ هر دو نسخه می‌ماند
                    merged += 1
                else:
                    items[canon] = old_meta
            # بازنویسیِ پایدار در SQLite: کلیدِ خام حذف، کانونی درج
            try:
                self._con.executemany("DELETE FROM items WHERE path = ?",
                                      [(old,) for old, _c in renames])
                self._con.executemany(
                    "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)",
                    [(c, json.dumps(items[c], ensure_ascii=False))
                     for _o, c in renames if c in items])
                self._con.commit()
            except sqlite3.Error as e:
                # انتشار عمومی: مهاجرتِ کلید در SQLite نشست — حافظه کانونی است
                # و با نخستین touch هر مسیر درست می‌شود؛ بی‌صدا نماند
                self.save_failed = True
                dbg_log(f"_canonicalize_item_keys db rewrite failed: {type(e).__name__}")
        try:
            from watchtime import _dbg
            _dbg(f"meta key canonicalization: {len(renames)} renamed, "
                 f"{merged} merged")
        except Exception as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys failed: {type(_exc_dbg).__name__}")

    def _touch(self, path: str):
        self._dirty.add(path)

    def close(self):
        """فلاش نهایی + بستن اتصال (هنگام خروج برنامه)."""
        try:
            self.save_data()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass
        try:
            with self._lock:
                self._con.close()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ ذخیره
    def save_config(self, _bump: bool = True):
        """نوشتن اتمیک config.json — اول فایل موقت، بعد جایگزینی اتمیک.

        با نوشتن مستقیم، کرش/فورس‌کیل وسط ذخیره، فایل را نصفه (یا صفر بایت)
        رها می‌کرد و تنظیمات کاربر می‌پرید. os.replace روی ویندوز/یونیکس اتمیک است.
        0.48.0: _bump شمارنده‌ی تغییر (برای پشتیبانِ خودکارِ هوشمند) را جلو می‌برد؛
        «موارد اخیر» با _bump=False خودش را بی‌اثر نگه می‌دارد."""
        if self._defer:                             # ذخیره‌ی دسته‌ای — در پایان فلاش
            self._cfg_dirty = True
            if _bump:
                self.cfg_rev += 1
            return
        # بهینه‌سازی — گزارشِ «یک‌بار سریع یک‌بار کند»: نتیجه‌ی تشخیص نوعِ
        # دیسک هر درایو (SSD/HDD) این‌جا همراهِ تنظیمات ذخیره می‌شود تا شمارِ
        # کارگرهای پیش‌ساخت بین اجراها ثابت بماند — نه این‌که هر بوت یک‌بار پرس‌وجوی
        # سطح‌پایین جواب متفاوتی بدهد. کلید فقط وقتی داده هست نوشته می‌شود تا
        # config.json پیش‌فرض دست‌نخورده و مینیمال بماند.
        try:
            import hwinfo as _hw
            _kinds = _hw.persisted_kinds()
            if _kinds:
                self.config["disk_kinds"] = _kinds
        except Exception as _exc_dbg:
            dbg_log(f"save_config failed: {type(_exc_dbg).__name__}")
            pass
        data = json.dumps(self.config, ensure_ascii=False, indent=2)
        tmp = self.config_path.with_name(self.config_path.name + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.config_path)
        except OSError:
            try:
                self.config_path.write_text(data, encoding="utf-8")
            except OSError as e2:
                # انتشار عمومی: شکستِ ذخیره‌ی تنظیمات بی‌صدا نماند — کاربر با
                # «تنظیماتم ذخیره نشد» مواجه می‌شود و لاگ هم دلیل را دارد
                try:
                    from watchtime import _dbg
                    _dbg(f"config save failed (direct write also failed): "
                         f"{type(e2).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"save_config failed: {type(_exc_dbg).__name__}")
                    pass
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if _bump:
            self.cfg_rev += 1                    # پشتیبانِ خودکارِ هوشمند

    def save_data(self, _bump: bool = True):
        """فلاش تغییرها به SQLite — فقط همین ردیف‌ها، نه بازنویسی کل دیتابیس.

        0.48.0: فلاشِ موفق شمارنده‌ی تغییر داده را جلو می‌برد تا «پشتیبان خودکار
        فقط هنگام تغییر واقعی» بداند چه‌وقتی ارزش بازنویسی دارد."""
        if self._defer:                             # ذخیره‌ی دسته‌ای — در پایان فلاش
            self._data_deferred = True
            if _bump:
                self.data_rev += 1
            return
        with self._lock:
            # (C8) تب‌دیلِ dirty و خواندنِ items داخلِ قفل — قبلاً بیرونِ قفل
            # بود و با setterِ نخِ دیگر (تماشای زنده/کاور) مسابقه می‌داد
            if not self._dirty:
                return
            dirty, self._dirty = self._dirty, set()
            items = self.data.get("items", {})
            upserts, deletes = [], []
            for p in dirty:
                meta = items.get(p)
                if meta:
                    upserts.append((p, json.dumps(meta, ensure_ascii=False)))
                else:
                    deletes.append((p,))
            try:
                if upserts:
                    self._con.executemany(
                        "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)", upserts)
                if deletes:
                    self._con.executemany("DELETE FROM items WHERE path = ?", deletes)
                self._con.commit()
            except sqlite3.Error as e:
                # شکست تراکنش → مسیرها را دوباره کثیف علامت بزن تا دفعه‌ی بعد فلاش شوند
                self._dirty.update(dirty)
                # (C8) خطای SQLite دیگر بی‌صدا بلعیده نمی‌شود — لاگ + پرچم؛
                # اگر خطا دائمی باشد (دیسک پر/فایل فقط‌خواندنی/قفل) کاربر
                # دیگر فکر نمی‌کند ذخیره شده و تماشا/امتیاز/کاور نمی‌پرد
                self.save_failed = True
                dbg_log(f"save_data failed: {type(e).__name__}")
                return
            self.save_failed = False
            if _bump:
                self.data_rev += 1               # فلاش موفق = داده واقعاً عوض شد

    # ------------------------------------------------------------ ذخیره‌ی دسته‌ای
    @contextmanager
    def defer_save(self):
        """ذخیره‌ها را جمع و در پایان دقیقاً یک‌بار فلاش می‌کند (رفع فریزِ «ذخیره»).

        گزارش کاربر: «گزینه‌ی ذخیره را زدم برنامه چند ثانیه گیر کرد». علت: تک‌تکِ
        setterها بلافاصله save_config می‌زدند (۸+ نوشتنِ اتمیکِ متوالی روی دیسک).
        داخل این کانتکست setterها فقط روی حافظه می‌نشینند و خروجیِ بلوک یک
        save_config + یک save_data انجام می‌دهد. تو در تو امن است.
        """
        self._defer += 1
        try:
            yield
        finally:
            self._defer -= 1
            if self._defer <= 0:
                self._defer = 0
                try:
                    if self._cfg_dirty:
                        self._cfg_dirty = False
                        self.save_config()
                finally:
                    if self._data_deferred:
                        self._data_deferred = False
                        self.save_data()

    # ------------------------------------------------------------ مسیرها
    def roots(self) -> list[dict]:
        return self.config.get("roots", [])

    def set_roots(self, roots: list[dict], include_image_fallback: bool | None = None):
        self.config["roots"] = roots
        if include_image_fallback is not None:
            self.config["include_image_fallback"] = include_image_fallback
        for r in roots:
            c = str(r.get("category", "")).strip()
            if c:
                self.ensure_category(c)
        self.save_config()

    @property
    def include_image_fallback(self) -> bool:
        return bool(self.config.get("include_image_fallback", True))

    @property
    def archive_thumbs(self) -> bool:
        """تامبنیل از محتوای فایل‌های فشرده برای پوشه‌های بدون تصویر/ویدیو."""
        return bool(self.config.get("archive_thumbs", True))

    def set_archive_thumbs(self, flag: bool):
        self.config["archive_thumbs"] = bool(flag)
        # بلافاصله ذخیره — قبلاً فقط حافظه عوض می‌شد و اگر برنامه بدون
        # ثبتِ تنظیماتِ دیگری بسته می‌شد، انتخابِ کاربر گم می‌شد (نکته‌ی راهنما).
        self.save_config()

    @property
    def folder_thumbs_only(self) -> bool:
        """فقط پوستر پوشه‌ها ساخته شود؛ فایل‌ها کارت نوع‌فایل می‌گیرند (صرفه‌جویی
        حجم و زمان — پیشنهاد کاربر برای حالت سقف‌دار کش)."""
        return bool(self.config.get("folder_thumbs_only", False))

    def set_folder_thumbs_only(self, flag: bool):
        self.config["folder_thumbs_only"] = bool(flag)
        self.save_config()   # ماندگاری بی‌درنگ انتخاب کاربر

    @property
    def prebuild_thumbs(self) -> bool:
        """«پیش‌ساخت تامبنیل‌ها بعد از اسکن» — گزینه‌ی تنظیمات (0.31.0: پیش‌فرض روشن،
        چون دیگر برنامه را قفل نمی‌کند؛ در پس‌زمینه و با رعایت اولویت UI)."""
        return bool(self.config.get("prebuild_thumbs", True))

    def set_prebuild_thumbs(self, flag: bool):
        self.config["prebuild_thumbs"] = bool(flag)
        self.save_config()

    def cache_max_mb(self) -> int:
        """سقف حجم کش تصاویر به مگابایت — 0 یعنی نامحدود (پیش‌فرض: 2048)."""
        try:
            v = int(self.config.get("cache_max_mb", 2048) or 0)
        except (TypeError, ValueError):
            v = 2048
        return max(0, v)

    def set_cache_max_mb(self, mb: int):
        try:
            self.config["cache_max_mb"] = max(0, int(mb))
        except (TypeError, ValueError):
            self.config["cache_max_mb"] = 0

    # ------------------------------------------------------------ دسته‌ها
    SEP = "/"

    @staticmethod
    def parent_of(cat: str) -> str | None:
        """والد یک دسته: «انیمه/تماشا‌شده» → «انیمه»"""
        return cat.rsplit("/", 1)[0] if "/" in cat else None

    @staticmethod
    def matches(cat: str, flt: str) -> bool:
        """آیا cat با فیلتر flt منطبق است؟ (والد شامل همه‌ی زیردسته‌هاست)"""
        return cat == flt or cat.startswith(flt + "/")

    def categories(self) -> list[str]:
        # (round 19 §۲) پیمایشِ دیکشنریِ مشترک زیرِ قفل — وگرنه با setterِ نخِ
        # دیگر RuntimeErrorِ «dictionary changed size during iteration» ممکن است.
        with self._lock:
            out: list[str] = list(self.config.get("categories", []))
            for r in self.roots():
                c = str(r.get("category", "")).strip()
                if c and c not in out:
                    out.append(c)
            for m in self.data.get("items", {}).values():
                c = (m.get("category") or "").strip()
                if c and c not in out:
                    out.append(c)
            return out

    def ensure_category(self, name: str):
        """ثبت دسته (و همه‌ی والدهایش): «انیمه/تماشا‌شده» → انیمه + انیمه/تماشا‌شده"""
        parts = [p.strip() for p in (name or "").split("/") if p.strip()]
        if not parts:
            return
        cats = self.config.setdefault("categories", [])
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in cats:
                cats.append(sub)
            self.color(sub)          # رنگ ثابت برایش رزرو کن
        self.save_config()

    def color(self, name: str) -> str:
        colors = self.config.setdefault("category_colors", {})
        if name not in colors:
            colors[name] = PALETTE[len(colors) % len(PALETTE)]
        return colors[name]

    # ---- باکس تغییر سریع دسته روی کارت‌ها (ریشه + همه‌ی فرزندها در یک باکس) ----
    def child_categories(self, cat: str) -> list[str]:
        """فرزندان مستقیم یک دسته (دقیقاً یک سطح پایین‌تر)، به ترتیب ثبت در تنظیمات."""
        prefix = cat + "/"
        out = [c for c in self.categories()
               if c.startswith(prefix) and "/" not in c[len(prefix):]]
        order = {name: i for i, name in enumerate(self.config.get("categories", []))}
        out.sort(key=lambda c: order.get(c, 1_000_000))
        return out

    def sibling_subcategories(self, cat: str) -> list[str]:
        """زیردسته‌های هم‌رده با cat (فرزندان همان والد)."""
        parent = self.parent_of(cat)
        return self.child_categories(parent) if parent else []

    def cat_family(self, item) -> tuple[str, list[str]] | None:
        """خانواده‌ی دسته‌ی یک عنوان: (ریشه، [ریشه + فرزندان]) — برای باکس سریع کارت.

        اگر عنوان در زیردسته باشد ریشه = والد؛ اگر در خود ریشه باشد و فرزند داشته
        باشد ریشه = همان. اگر هیچ زیردسته‌ای وجود نداشته باشد → None (باکس نمی‌خواهد).
        گزینه‌ی ریشه هم داخل لیست است تا بتوان به خودِ دسته‌ی اصلی هم برگشت.
        """
        cat = self.category_of(item)
        base = self.parent_of(cat) or cat
        children = self.child_categories(base)
        if not children:
            return None
        return base, [base] + children

    def cat_switch_view(self, item) -> dict | None:
        """نمای باکس تغییر سریع دسته برای کارت یک عنوان.

        گزینه‌ها = دسته‌ی اصلی + تمام زیردسته‌هایش؛ انتخاب هرکدام دسته را عوض می‌کند.
        """
        fam = self.cat_family(item)
        if not fam:
            return None
        base, options = fam
        cat = self.category_of(item)
        labels = {}
        for full in options:
            labels[full] = full if full == base else "↳ " + full[len(base) + 1:]
        return {
            "kind": "combo",
            "options": options,
            "labels": labels,
            "current": cat,
            "tip": T("تغییر سریع دسته‌ی این عنوان — دسته‌ی اصلی و همه‌ی زیردسته‌ها"),
        }

    def remove_category(self, cat: str):
        """حذف یک دسته‌ی بدون استفاده از فهرست (به متا/مسیرها دست نمی‌زند)."""
        cats = self.config.setdefault("categories", [])
        if cat in cats:
            cats.remove(cat)
        self.config.get("category_colors", {}).pop(cat, None)
        self.save_config()

    def category_in_use(self, cat: str) -> bool:
        """آیا مسیر یا عنوانی مستقیماً از این دسته استفاده می‌کند؟"""
        with self._lock:                      # (round 19 §۲) پیمایش زیرِ قفل
            for r in self.roots():
                if str(r.get("category", "")) == cat:
                    return True
            for m in self.data.get("items", {}).values():
                if (m.get("category") or "") == cat:
                    return True
        return False

    def set_category(self, path, cat: str):
        """تغییر دسته‌ی یک عنوان (با ثبت خودکار دسته/والدها)."""
        self.set_meta(path, category=cat)

    # ------------------------------------------------------------ متادیتا
    def meta(self, path) -> dict:
        return self.data.get("items", {}).get(_meta_key(path), {})

    def set_meta(self, path, **kw):
        # (round 19 §۲) snapshot→mutate→touch→save همه زیرِ همان RLock —
        # قبلاً دیکشنریِ مشترک بیرونِ قفل عوض می‌شد و با setterِ نخِ دیگر
        # (تماشای زنده/پیش‌ساخت) last-writer-wins می‌شد و فیلدِ دیگری می‌پرید.
        with self._lock:
            path = _meta_key(path)
            items = self.data.setdefault("items", {})
            meta = dict(items.get(path, {}))
            for k, v in kw.items():
                if v in (None, ""):
                    meta.pop(k, None)
                else:
                    meta[k] = v
            if "category" in kw and kw.get("category"):
                self.ensure_category(kw["category"])
            if meta:
                items[path] = meta
            else:
                items.pop(path, None)
            self._touch(path)
            self.save_data()

    # ---- مقادیر مؤثر (با احتساب ویرایش کاربر) ----
    def title(self, item) -> str:
        # عنوانِ مخدوش (null/ANSI/newline در اسم فایل یا متای دستی) تمیز
        # نمایش داده می‌شود؛ مقدارِ ذخیره‌شده دست‌نخورده می‌ماند.
        return ui_clean(self.meta(item.path).get("title") or item.name)

    def category_of(self, item) -> str:
        return self.meta(item.path).get("category") or item.category

    def poster_of(self, item, static: bool = False):
        """پوستر مؤثر عنوان: کاور سفارشی (اگر هست) وگرنه آیکونِ پوشه.

        static=True فریمِ ساکنِ کاورِ متحرک را می‌خواهد — برای شبکه‌ی کارت‌ها
        و hero؛ پخشِ انیمیشن فقط در صفحه‌ی جزئیات انجام می‌شود."""
        poster, static_poster = self.custom_poster(item.path)
        if static and static_poster:
            return static_poster
        if poster:
            return poster
        return item.icon_path

    # ---- کاور سفارشی (round 11) ----
    def _resolve_cover_value(self, v) -> Path | None:
        """مقدارِ متای poster/poster_static → مسیرِ مطلقِ موجود یا None.

        مقدارهای تازه نسبی‌اند (‎.covers/<name>‎) و با app_dir() حل می‌شوند تا
        جابه‌جاییِ کلِ پوشه‌ی پرتابل کاورها را نمی‌شکند؛ مقدارهای قدیمیِ فرمِ
        ویرایش مسیرِ مطلق بودند — همان‌طور پشتیبانی می‌شوند. فایلِ گمشده لاگ
        می‌شود (B10) و None برمی‌گردد؛ متا عمداً پاک نمی‌شود چون شاید درایوِ
        منبع موقتاً سوار نباشد.
        """
        if not v:
            return None
        v = str(v).replace("\\", "/")
        if v.startswith(".covers/"):
            p = portable.app_dir() / v
        else:
            p = Path(v)
        try:
            if p.is_file():
                return p
        except OSError as _exc_dbg:
            dbg_log(f"_resolve_cover_value failed: {type(_exc_dbg).__name__}")
            return None
        with self._lock:
            logged = getattr(self, "_cover_missing_logged", None)
            if logged is None:
                logged = self._cover_missing_logged = set()
            if str(p) not in logged:
                if len(logged) > 1024:
                    logged.clear()
                logged.add(str(p))
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"cover file missing: {safe_ref(p)}")
                except Exception as _exc_dbg:
                    dbg_log(f"_resolve_cover_value failed: {type(_exc_dbg).__name__}")
        return None

    def custom_poster(self, path) -> tuple[Path | None, Path | None]:
        """(کاور، فریمِ ساکنِ همان کاور) — مسیرها مطلق‌اند یا None."""
        m = self.meta(path)
        poster = self._resolve_cover_value(m.get("poster"))
        if poster is None:
            return None, None
        static = self._resolve_cover_value(m.get("poster_static"))
        return poster, static

    def cover_refs(self) -> set[str]:
        """همهٔ مقادیرِ poster/poster_static در دیتابیس — برای جمع‌آوریِ کاورهای یتیم.

        منبع باید خودِ دیتابیس باشد، نه آیتم‌های اسکنِ فعلی؛ وگرنه کاورِ عنوان‌های
        یک ریشهٔ موقتاً آفلاین (درایوِ جدا‌شده/شبکه‌ی قطع) «یتیم» به‌نظر می‌رسید و
        پاک می‌شد — از‌دست‌رفتنِ داده (round 13 §۲). با برگشتنِ ریشه، کاور سرِ
        جایش است چون متا هرگز دست‌نخورده باقی مانده.
        """
        with self._lock:
            out: set[str] = set()
            for meta in self.data.get("items", {}).values():
                for k in ("poster", "poster_static"):
                    v = meta.get(k)
                    if v:
                        out.add(str(v).replace("\\", "/"))
            return out

    def cover_status(self, path) -> dict:
        """وضعیتِ کاور برای UI (فرمِ ویرایش): kind: custom|legacy|none + missing."""
        m = self.meta(path)
        v = m.get("poster")
        if not v:
            return {"kind": "none", "name": "", "missing": False}
        kind = "custom" if str(v).replace("\\", "/").startswith(".covers/") \
            else "legacy"
        p = self._resolve_cover_value(v)
        return {"kind": kind, "name": Path(str(v)).name,
                "missing": p is None}

    def set_custom_poster(self, item_path, info: dict) -> None:
        """ذخیره‌ی متای poster/poster_static (نسبی به پوشه‌ی برنامه).

        فایلِ قدیمیِ بدونِ ارجاع همان‌جا حذف می‌شود (‎.covers/‎ پف نمی‌کند) —
        ولی (C3) نامِ فایل‌ها هشِ محتواست و دو عنوان می‌توانند یک فایل را
        شریک باشند؛ حذفِ بی‌چکِ فایلِ قدیمی، کاورِ عنوانِ دیگر را هم می‌پراند.
        حالا بعد از به‌روزرسانیِ متا فقط فایل‌هایی حذف می‌شوند که هیچ ارجاعی
        در دیتابیس نمانده (cover_refs)؛ بقیه را collect_garbage جمع می‌کند.
        (round 19 §۲) کلِ عملیات زیرِ همان قفلِ Store — با GCِ کاور که unlink
        را زیرِ همان قفل انجام می‌دهد، «حذفِ فایل تازه‌ارجاع‌شده» ناممکن می‌شود."""
        with self._lock:
            m = self.meta(item_path)
            old = (m.get("poster"), m.get("poster_static"))
            new = (info.get("poster") or None, info.get("poster_static") or None)
            self.set_meta(item_path, poster=new[0], poster_static=new[1])
            refs = self.cover_refs()            # بعدِ set_meta — شاملِ ارجاعِ بقیه‌ی عنوان‌ها
            stale = [v for v in old
                     if v and str(v).replace("\\", "/") not in refs]
            if stale:
                try:
                    from covers import delete_cover_files
                    delete_cover_files(stale[0],
                                       stale[1] if len(stale) > 1 else None)
                except Exception as _exc_dbg:
                    dbg_log(f"set_custom_poster cleanup failed: {type(_exc_dbg).__name__}")

    def clear_custom_poster(self, item_path) -> None:
        """حذفِ کلیدها + فایل‌های بدونِ ارجاع — idempotent (B10: فایلِ گمشده خطا نمی‌دهد).

        (C3) اول متا پاک می‌شود، بعد فقط فایل‌هایی حذف می‌شوند که دیگر هیچ
        عنوانی به آن‌ها ارجاع نمی‌دهد — کاورِ مشترک با عنوانِ دیگر می‌ماند."""
        with self._lock:
            m = self.meta(item_path)
            old = (m.get("poster"), m.get("poster_static"))
            self.set_meta(item_path, poster=None, poster_static=None)
            refs = self.cover_refs()
            stale = [v for v in old
                     if v and str(v).replace("\\", "/") not in refs]
            if stale:
                try:
                    from covers import delete_cover_files
                    delete_cover_files(stale[0],
                                       stale[1] if len(stale) > 1 else None)
                except Exception as _exc_dbg:
                    dbg_log(f"clear_custom_poster failed: {type(_exc_dbg).__name__}")

    def desc(self, path) -> str:
        return self.meta(path).get("description", "")

    def fav(self, path) -> bool:
        return bool(self.meta(path).get("favorite", False))

    # ------------------------------------------------------
    def rating(self, path) -> int:
        """امتیاز شخصی ۰–۵ (درخواست کاربر — ستاره در جزئیات/مرتب‌سازی)."""
        try:
            v = int(self.meta(path).get("rating") or 0)
        except (TypeError, ValueError):
            v = 0
        return min(max(v, 0), 5)

    # «🕘 موارد اخیر» — آخرین عنوان‌های پخش/بازشده (درخواست کاربر: نامِ پیشنهادی خودش —
    # «بازشده‌های اخیر»). حداکثر RECENT_MAX مورد، تازه‌ترین اول.
    RECENT_MAX = 15

    def recents(self) -> list:
        r = self.config.get("recents")
        return [str(p) for p in r] if isinstance(r, list) else []

    def add_recent(self, path):
        path = str(path)
        if not path:
            return
        r = [p for p in self.recents() if p != path]
        r.insert(0, path)
        self.config["recents"] = r[: self.RECENT_MAX]
        # عمداً _bump=False: بازدید محض «تغییر داده» محسوب نمی‌شود تا پشتیبانِ
        # خودکار بی‌دلیل بازنویسی نشود (قاعده‌ی کاربر: فقط وقتی تغییر واقعی).
        self.save_config(_bump=False)

    # ---- حذف/انتقال مورد (مدیر موارد گمشده — درخواست کاربر: «حذف» / «مسیر جدید»)
    def remove_item(self, path) -> bool:
        path = _meta_key(path)
        with self._lock:
            if path not in self.data.get("items", {}):
                return False
            self.data["items"].pop(path, None)
            self._touch(path)                   # در save_data ← DELETE
            self.save_data()
        return True

    def move_item(self, old, new) -> bool:
        """کل اطلاعاتِ یک عنوان (تماشا/دسته/توضیح/امتیاز) را به مسیرِ تازه منتقل می‌کند.

        کلیدهای تماشا نسبی‌اند (نسبت به پوشه‌ی عنوان) پس سالم می‌مانند. اگر مقصد
        از قبل اطلاعات داشته باشد جابه‌جایی انجام نمی‌شود (هیچ‌داده‌ای له نمی‌شود)."""
        old, new = _meta_key(old), _meta_key(new)
        if old == new:
            return True
        with self._lock:
            items = self.data.setdefault("items", {})
            meta = items.pop(old, None)
            if meta is None or new in items:
                if meta is not None:
                    items[old] = meta           # برگردان — مقصد اشغال بود
                return False
            items[new] = meta
            self._touch(old)                    # حذف کلید قدیمی
            self._touch(new)                    # درج کلید جدید
            self.save_data()
        return True

    # ---- پشتیبان‌گیری خودکارِ هوشمند (درخواست کاربر) ----
    def auto_backup_cfg(self) -> dict:
        cfg = self.config.get("auto_backup")
        return dict(cfg) if isinstance(cfg, dict) else {}

    def set_auto_backup(self, enabled: bool, path: str | None = None,
                        include_cache: bool | None = None):
        """گزینه + مسیر + نوعِ پشتیبانِ خودکار؛ None یعنی مقدارِ قبلی حفظ می‌شود.

        0.48.0 (بازبینی کاربر — «فایلش ۲۲۲ کیلوبایته، پشتیبانِ دستی‌ام ۴۰ مگ بود»):
        دو نوع — «ساده» فقط اطلاعات (مسیرها، وضعیت تماشا، امتیاز، دسته‌ها؛ سبک
        و سریع) و «کامل» شامل کشِ تامبنیل‌ها (بازیابی آماده، حجم بالا).
        include_cache فقط وقتی صریحاً داده شود عوض می‌شود؛ پیش‌فرش «ساده» است
        تا بازنویسی‌های روزمره ارزان بمانند."""
        cur = self.auto_backup_cfg()
        cur["enabled"] = bool(enabled)
        if path is not None:
            cur["path"] = str(path)
        if include_cache is not None:
            cur["include_cache"] = bool(include_cache)
        if cur["enabled"] and not str(cur.get("path") or "").strip():
            cur["path"] = str(self.config_path.parent / "backups" / "auto-backup.mcbak")
        self.config["auto_backup"] = cur

    # ---- برنامه‌ی بازکننده‌ی اختصاصی هر دسته (درخواست کاربر) ----
    # «در بخش گیم فایل‌های فشرده با WinRAR، در مانگا با کامیک‌ریدر باز شوند».
    # config["cat_openers"] = {"<دسته>": [{"exts": ["zip","rar"], "app": "C:/..."}]}
    # مثل الگوی وضعیت از والد ارث می‌برد؛ زمان‌اجرا ساخته می‌شود (پیش‌فرض کانفیگ ثابت).
    def cat_openers(self, cat: str) -> list[dict]:
        """قواعد بازکننده‌ی مؤثر برای دسته (با ارث از نزدیک‌ترین والدِ تنظیم‌شده)."""
        m = self.config.get("cat_openers") or {}
        c = str(cat or "").strip()
        while c:
            rules = m.get(c)
            if isinstance(rules, list) and rules:
                out = []
                for r in rules:
                    try:
                        exts = [str(e).lower().lstrip(".") for e in (r.get("exts") or [])]
                        app = str(r.get("app") or "").strip()
                    except (AttributeError, TypeError):
                        continue
                    if exts and app:
                        out.append({"exts": exts, "app": app})
                if out:
                    return out
            c = self.parent_of(c)
        return []

    def set_cat_openers(self, cat: str, rules: list):
        """بازنویسی قواعد بازکننده‌ی یک دسته؛ لیست خالی = حذف (ارث از والد)."""
        cat = str(cat or "").strip()
        if not cat:
            return
        m = dict(self.config.get("cat_openers") or {})
        cur = m.get(cat) or []
        if list(rules or []) == list(cur):
            return
        if rules:
            m[cat] = rules
        else:
            m.pop(cat, None)
        if m:
            self.config["cat_openers"] = m
        else:
            self.config.pop("cat_openers", None)
        self.save_config()

    def cat_opener_for(self, cat: str, suffix: str) -> str:
        """مسیر برنامه‌ی بازکننده‌ی این پسوند در این دسته (یا "" = پیش‌فرض سیستم)."""
        sfx = str(suffix or "").lower().lstrip(".")
        if not sfx:
            return ""
        for r in self.cat_openers(cat):
            if sfx in r["exts"]:
                return r["app"]
        return ""

    def cat_openers_map(self) -> dict:
        """نسخه‌ی کپی از همه‌ی قواعد (تب دسته‌ها/تست‌ها)."""
        m = self.config.get("cat_openers") or {}
        return {c: [dict(r) for r in rs if isinstance(r, dict)]
                for c, rs in m.items() if isinstance(rs, list)}

    # ------------------------------------------------------------ وضعیت تماشا
    # watch: {"<مسیر نسبی فایل>": {"m": 1|2, "p": ثانیه}}
    #   m=1 دیده‌شده ✓ | m=2 نیمه‌تمام ◐ (با p = موقعیت رزومه)
    WATCH_DONE, WATCH_PART = 1, 2

    def watch_map(self, item) -> dict:
        m = self.meta(item.path).get("watch")
        return m if isinstance(m, dict) else {}

    # ---- الگوی وضعیت هر دسته (فیلم/کتاب/بدون/سفارشی — با ارث از والد) ----
    @staticmethod
    def builtin_profile(key: str) -> dict | None:
        return BUILTIN_TRACK_PROFILES.get(key)

    def custom_profiles(self) -> dict:
        m = self.config.get("track_profiles")
        return m if isinstance(m, dict) else {}

    def save_custom_profile(self, title: str, done: str, part: str,
                            none: str = "", pid: str | None = None) -> str:
        """افزودن/ویرایش یک الگوی سفارشی → کلید الگو برمی‌گردد."""
        import hashlib as _hl
        profs = dict(self.config.setdefault("track_profiles", {}))
        pid = pid or ("u_" + _hl.sha1(
            f"{title}|{done}|{part}".encode("utf-8")).hexdigest()[:8])
        if pid in BUILTIN_TRACK_PROFILES:   # کلید نمی‌تواند با الگوی داخلی برخورد کند
            pid = "u_" + pid
        profs[pid] = {
            "title": str(title).strip() or T("الگو"),
            "done": str(done).strip() or T("تمام شد"),
            "part": str(part).strip() or T("نیمه"),
            "none": str(none).strip(),
        }
        self.config["track_profiles"] = profs
        self.save_config()
        return pid

    def delete_custom_profile(self, pid: str):
        profs = dict(self.config.setdefault("track_profiles", {}))
        profs.pop(pid, None)
        self.config["track_profiles"] = profs
        # دسته‌هایی که آن را داشتند → بدون تخصیص (خودکار)
        m = {c: k for c, k in (self.config.get("cat_track_profile") or {}).items()
             if k != pid}
        self.config["cat_track_profile"] = m
        self.save_config()

    def profile_options(self) -> list[tuple[str, str]]:
        """گزینه‌های باکس الگو: (کلید، نام نمایشی) — داخلی‌ها + سفارشی‌ها."""
        out = [(k, v["title"]) for k, v in BUILTIN_TRACK_PROFILES.items()]
        for pid, p in self.custom_profiles().items():
            out.append((pid, p.get("title") or pid))
        return out

    def cat_track_profile_key(self, cat: str) -> str | None:
        """کلید صریحِ تخصیص‌داده‌شده به دسته یا یکی از والدهایش (None = خودکار)."""
        m = self.config.get("cat_track_profile") or {}
        c = str(cat or "").strip()
        while c:
            if c in m:
                return m[c]
            c = self.parent_of(c)
        return None

    def set_cat_track_profile(self, cat: str, key: str | None):
        """تخصیص/حذف الگو به دسته (key=None یا »auto« یعنی رفع تخصیص)."""
        m = dict(self.config.get("cat_track_profile") or {})
        c = str(cat or "").strip()
        if not c:
            return
        if key and key != "auto" and (
                key in BUILTIN_TRACK_PROFILES or key in self.custom_profiles()):
            m[c] = key
        else:
            m.pop(c, None)
        self.config["cat_track_profile"] = m
        self.save_config()

    def _legacy_off_key(self, cat: str) -> str | None:
        """سازگاری با 0.14.0: فهرست cat_status_off قدیمی → الگوی none."""
        cat = str(cat or "").strip()
        for o in (self.config.get("cat_status_off") or []):
            o = str(o).strip()
            if o and (cat == o or cat.startswith(o + "/")):
                return "none"
        return None

    def track_profile(self, cat: str) -> dict:
        """الگوی مؤثر برای یک دسته — همیشه دیکشنری کامل با برچسب‌ها برمی‌گردد.

        خروجی: {"id": کلید, "title": نام, و برای auto: vid/doc دیکت برچسب،
        و برای بقیه: done/part/act، شاخص‌های custom و enabled}
        """
        key = (self.cat_track_profile_key(cat)
               or self._legacy_off_key(cat) or "auto")
        custom = self.custom_profiles().get(key)
        base = dict(BUILTIN_TRACK_PROFILES.get(key) or {})
        if custom and not base:
            base = {"title": custom.get("title", key),
                    "done": custom.get("done", T("تمام شد")),
                    "part": custom.get("part", T("نیمه")),
                    "act": custom.get("title", T("پیشرفت"))}
        if key not in BUILTIN_TRACK_PROFILES and not custom:
            key, base = "auto", dict(BUILTIN_TRACK_PROFILES["auto"])  # خراب → خودکار
        return {
            "id": key,
            "title": base.get("title", key),
            "done": base.get("done", ""),
            "part": base.get("part", ""),
            "act": base.get("act", ""),
            "vid": base.get("vid", BUILTIN_TRACK_PROFILES["auto"]["vid"]),
            "doc": base.get("doc", BUILTIN_TRACK_PROFILES["auto"]["doc"]),
            "enabled": key != "none",
            "custom": custom is not None,
        }

    def cat_track_status(self, cat: str) -> bool:
        """آیا عناوین این دسته علامت وضعیت (✓/◐) می‌گیرند؟ (الگو ≠ بدون ترکینگ)"""
        return bool(self.track_profile(cat)["enabled"])

    def cat_status_off_list(self) -> list[str]:
        """فهرست دسته‌های تخصیص‌داده‌شده به »none« (به‌علاوه‌ی قدیمی‌های cat_status_off)."""
        out = [str(c).strip() for c in (self.config.get("cat_status_off") or [])
               if str(c).strip()]
        for c, k in (self.config.get("cat_track_profile") or {}).items():
            if k == "none" and c not in out:
                out.append(c)
        return out

    def set_cat_track_statuses(self, off: list):
        """سازگاری با 0.14.0 — لیست خاموش‌ها به الگوی »none« تبدیل می‌شود (بقیه آزاد)."""
        m = dict(self.config.get("cat_track_profile") or {})
        off_set = {str(c).strip() for c in (off or []) if str(c).strip()}
        for c in off_set:
            m[c] = "none"
        for c in [c for c, k in m.items() if k == "none" and c not in off_set]:
            m.pop(c, None)
        self.config["cat_track_profile"] = m
        self.config["cat_status_off"] = []
        self.save_config()

    def guess_profile_key(self, cat: str) -> str:
        """پیش‌فرض هوشمند هنگام افزودن مسیر: نام کتابی/مانگایی → book وگرنه video."""
        low = str(cat or "").casefold()
        return "book" if any(h in low for h in _BOOK_HINTS) else "video"

    def watch_get(self, item, rel: str) -> tuple[int, float]:
        e = self.watch_map(item).get(rel) or {}
        try:
            return int(e.get("m") or 0), float(e.get("p") or 0.0)
        except (TypeError, ValueError):
            return 0, 0.0

    def watch_set(self, item, rel: str, mode: int, pos: float = 0.0):
        # (round 19 §۲) کلید کانونی + کلِ عملیات زیرِ قفل — قبلاً کلیدِ خامِ
        # «C:/x/y» می‌نوشت ولی watch_get با _meta_key «C:\x\y» می‌خواند و تماشا
        # برای آیتم‌های با کلیدِ غیرکانونی گم می‌شد.
        path = _meta_key(item.path)
        with self._lock:
            items = self.data.setdefault("items", {})
            meta = dict(items.get(path, {}))
            watch = dict(meta.get("watch") or {})
            if mode:
                watch[rel] = {"m": int(mode), "p": round(float(pos), 1)}
            else:
                watch.pop(rel, None)
            if watch:
                meta["watch"] = watch
            else:
                meta.pop("watch", None)
            if meta:
                items[path] = meta
            else:
                items.pop(path, None)
            self._touch(path)
            self.save_data()

    def watch_progress(self, item) -> tuple[int, int]:
        """برمی‌گرداند: (تعداد دیده‌شده، تعداد نیمه‌تمام)"""
        done = part = 0
        for e in self.watch_map(item).values():
            try:
                m = int(e.get("m") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if m == self.WATCH_DONE:
                done += 1
            elif m == self.WATCH_PART:
                part += 1
        return done, part

    def has_partial(self, item) -> bool:
        return self.watch_progress(item)[1] > 0

    def watchable_total(self, item) -> int:
        """مجموع فایل‌های قابل‌مصرفِ عنوان — مبنای «کامل دیده‌شده» (0.48.0).

        درخواست کاربر: «بخش دیده‌شده بر اساس دسته درست کن که شامل همه دسته‌ها
        بشه». برای عنوانِ ویدیویی همان تعداد ویدیوهاست؛ برای بقیه (کتاب، مانگای
        فشرده، تصویر، صوت، سند، برنامه) شمارِ فایل‌های قابل‌مصرف روی دیسک —
        زیرنویس و سایدکارها هیچ‌وقت جزو کل نیستند. نتیجه روی خود آیتم کش
        می‌شود (کلید = زمانِ تغییر پوشه که اسکن ثبت کرده) تا فیلترِ هر ضربه‌ی
        کلیدِ جستجو دیسک را دوباره نخورد.
        """
        v = getattr(item, "video_count", 0) or 0
        if v > 0:
            return v
        key = getattr(item, "modified", 0.0) or 0.0
        cached = getattr(item, "_wc48", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            from scanner import item_watchables
            n = len(item_watchables(item))
        except Exception as _exc_dbg:
            dbg_log(f"watchable_total failed: {type(_exc_dbg).__name__}")
            n = 0
        try:
            item._wc48 = (key, n)
        except Exception as _exc_dbg:
            dbg_log(f"watchable_total failed: {type(_exc_dbg).__name__}")
            pass
        return n

    # ---- پوسته و مدت‌زمان ----
    @property
    def theme(self) -> str:
        return (self.config.get("theme") or "dark").strip() or "dark"

    def set_theme(self, name: str):
        self.config["theme"] = name
        self.save_config()

    # ---- زبان ----
    @property
    def language(self) -> str:
        v = (self.config.get("language") or "en").strip().lower()
        return "fa" if v == "fa" else "en"

    def set_language(self, code: str):
        self.config["language"] = "fa" if str(code).strip().lower() == "fa" else "en"
        self.save_config()

    # ---- حالت عملکرد ----
    @property
    def perf_mode(self) -> str:
        v = (self.config.get("perf_mode") or "auto").strip().lower()
        return v if v in ("auto", "high", "safe") else "auto"

    def set_perf_mode(self, mode: str):
        v = str(mode).strip().lower()
        self.config["perf_mode"] = v if v in ("auto", "high", "safe") else "auto"
        self.save_config()

    # ---- نوع دیسکِ ذخیره‌شده (ثباتِ شمار کارگرها بین اجراها) ----
    @property
    def disk_kinds(self) -> dict:
        d = self.config.get("disk_kinds")
        return d if isinstance(d, dict) else {}

    def set_disk_kinds(self, kinds: dict):
        if isinstance(kinds, dict) and kinds:
            self.config["disk_kinds"] = kinds
            self.save_config()

    @property
    def compute_durations(self) -> bool:
        return bool(self.config.get("compute_durations", True))

    def set_compute_durations(self, flag: bool):
        self.config["compute_durations"] = bool(flag)
        self.save_config()

    # ---- پخش‌کننده (برای رزومه) ----
    # config["player"]: {"mode": "auto|mpv|vlc|default|custom", "path": "..."}
    @property
    def player_cfg(self) -> dict:
        p = self.config.get("player")
        return p if isinstance(p, dict) else {"mode": "auto", "path": ""}

    def set_player_cfg(self, mode: str, path: str = ""):
        self.config["player"] = {"mode": mode, "path": path}
        self.save_config()

    def total_duration(self, item) -> int:
        """مجموع مدت‌زمان محاسبه‌شده‌ی یک عنوان (ثانیه). ۰ = نامشخص."""
        try:
            return int(self.meta(item.path).get("total_dur") or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------ مانیفست زمان‌ها
    # مانیفست = نقشه‌ی {مسیر نسبی ویدیو: [size, mtime_ns]} — اگر پوشه تغییر نکرده
    # باشد، محاسبه‌ی زمان‌ها کلاً رد می‌شود (سریع).
    def manifest_of(self, item) -> dict | None:
        m = self.meta(item.path).get("vids")
        return m if isinstance(m, dict) else None

    def set_total_durations(self, durations: dict, manifests: dict | None = None):
        """ثبت یکجای مجموع مدت‌زمان همه‌ی عناوین پس از اسکن (یک بار ذخیره‌ی فایل).

        (round 19 §۲) زیرِ قفل + کلیدِ کانونی — کارگرِ زمان‌ها نخِ پس‌زمینه‌ست
        و UI هم‌زمان می‌تواند watch/meta بنویسد؛ قبلاً دیکشنریِ مشترک بیرونِ
        قفل عوض می‌شد."""
        with self._lock:
            items = self.data.setdefault("items", {})
            for path, dur in durations.items():
                # (round 20) کلیدِ خام پیش از کانونی‌سازی نگه داشته می‌شود —
                # قبلاً fallbackِ «کلیدِ خام» بعد از بازنویسیِ path با همان
                # کلیدِ کانونی خوانده می‌شد و هرگز اجرا نمی‌شد؛ فراخوانِ آینده‌ای
                # که مانیفست‌ها را با کلیدِ خام بسازد بی‌صدا از دست می‌رفت
                raw = path
                path = _meta_key(path)
                meta = dict(items.get(path, {}))
                if dur:
                    meta["total_dur"] = int(dur)
                else:
                    meta.pop("total_dur", None)
                if manifests is not None:
                    man = manifests.get(str(path))
                    if not man:
                        man = manifests.get(str(raw))   # سازگاری با کلیدِ خامِ فراخوانِ قدیمی
                    if man:
                        meta["vids"] = man
                    else:
                        meta.pop("vids", None)
                if meta:
                    items[path] = meta
                else:
                    items.pop(path, None)
                self._touch(path)
            self.save_data()

    def clear_all_manifests(self):
        """حذف مانیفست و مجموع زمان همه‌ی عناوین (بازمحاسبه‌ی کامل در اسکن بعدی).

        (round 19 §۹) باگِ «or» — اگر vids موجود بود، popِ سمتِ راستِ or
        اصلاً اجرا نمی‌شد و total_dur کهنه می‌ماند؛ حالا هر دو مستقل pop
        می‌شوند و پرچمِ تغییر از نتیجه‌ی هر دو ساخته می‌شود."""
        with self._lock:
            items = self.data.get("items", {})
            for p, meta in list(items.items()):      # list: چون ممکن است حذف هم بکنیم
                vids = meta.pop("vids", None)
                total = meta.pop("total_dur", None)
                if vids is not None or total is not None:
                    if not meta:
                        items.pop(p, None)
                    self._touch(p)
            self.save_data()

    # ------------------------------------------------------------ مانیفست تامبنیل‌ها
    # مانیفستِ تامبنیل = رکورد {"m": {مسیر نسبی: [size, mtime_sec]}, "r": بازتلاش,
    # "e": era} — الگوی همان مانیفستِ زمان‌ها ولی برای پیش‌ساخت تامبنیل‌ها
    # (درخواست کاربر: «برای فایل‌هایی که تامبنیل دارن سریع ردشون کنه و فقط در
    # صورت تغییر دوباره بررسی کنه»). پیش‌ساز معنی فیلدها را می‌داند؛ اینجا فقط
    # ذخیره/بازخوانیِ یکجا انجام می‌شود (مهم: چون داخل data.db است، در فایلِ
    # پشتیبان .mcbak هم می‌آید و بعد از «واردکردن اطلاعات» ردکردن کار می‌کند).
    def thumb_state_of(self, item) -> dict | None:
        """رکورد مانیفست تامبنیل یک عنوان (یا None اگر هنوز ثبت نشده)."""
        m = self.meta(item.path).get("thumbs")
        return m if isinstance(m, dict) and isinstance(m.get("m"), dict) else None

    def set_thumb_states(self, states: dict):
        """ثبت یکجای مانیفست تامبنیل عناوین پس از پیش‌ساختِ کامل (یک بار ذخیره).

        (round 19 §۲) پیش‌ساز از نخِ پس‌زمینه صدا می‌زند — زیرِ قفل + کلیدِ کانونی."""
        with self._lock:
            items = self.data.setdefault("items", {})
            for path, rec in states.items():
                path = _meta_key(path)
                meta = dict(items.get(path, {}))
                if rec and isinstance(rec.get("m"), dict):
                    meta["thumbs"] = {"m": rec["m"],
                                      "r": int(rec.get("r") or 0),
                                      "e": str(rec.get("e") or "")}
                else:
                    meta.pop("thumbs", None)
                if meta:
                    items[path] = meta
                else:
                    items.pop(path, None)
                self._touch(path)
            self.save_data()

    def clear_thumb_manifests(self):
        """حذف مانیفست تامبنیل همه‌ی عناوین — بعد از پاک‌کردن/کوتاه‌کردن کش.

        وقتی کش تامبنیل‌ها پاک یا با سقفِ حجمی (LRU) کوتاه می‌شود، مانیفست‌ها
        دیگر «همه‌چیز ساخته شده» را تضمین نمی‌کنند؛ پاکشان می‌کنیم تا پیش‌ساز
        در اجرای بعد همه‌ی عناوین را یک‌بار هوشمندانه بازبینی کند."""
        with self._lock:                      # (round 19 §۲) پیمایش/تغییر زیرِ قفل
            items = self.data.get("items", {})
            changed = False
            for p, meta in list(items.items()):      # list: چون ممکن است حذف هم بکنیم
                if isinstance(meta, dict) and meta.pop("thumbs", None) is not None:
                    if not meta:
                        items.pop(p, None)
                    self._touch(p)
                    changed = True
            if changed:
                self.save_data()


