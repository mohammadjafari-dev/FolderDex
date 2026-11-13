# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""مسیرهای داده‌ی برنامه — نسخه‌ی 0.39.0: **همه‌چیز کنارِ برنامه؛ هیچ‌چیز سراسری نیست.**

درخواست کاربر در 0.39.0: «تمام تنظیمات برنامه کنار خود برنامه ذخیره بشن، نه در
مسیر سراسری — هیچ چیزی در مسیر سراسری ذخیره نشه». مدلِ 0.34.0–0.38.0 (نگه‌داشتنِ
داده‌ها در %LOCALAPPDATA%\\\\MediaCataloger برای پایداریِ بین‌نسخه‌ای) به تصمیمِ
کاربر برگردانده شد؛ انتقالِ داده بین نسخه‌ها از این‌پس کاربرمحور است: دکمه‌های
«📤 خروجی‌گرفتن / 📥 واردکردن اطلاعات» در تنظیمات (backup.py).

مهاجرتِ یک‌باره (migrate_shared_to_app): در اولین اجرای 0.39.0، اگر پوشه‌ی
سراسریِ قدیمی هنوز وجود داشته باشد، config/data.db از آن به کنار برنامه
*منتقل* می‌شود (0.34.0–0.38.0 منبعِ حقیقت همان بود) و کشِ سراسری — فقط اگر کشِ
کناربرنامه نباشد — منتقل می‌شود؛ پوشه‌ی سراسری وقتی خالی شد حذف می‌شود.

تست‌ها/عیب‌یابی: متغیرهای محیطی MC_DATA_DIR (ریشه‌ی داده) و MC_CACHE_DIR (کش).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from portable import app_dir
from watchtime import dbg_log

_DIR_NAME = "MediaCataloger"          # نامِ پوشه‌ی سراسریِ قدیمی (فقط خواندنی)
_DATA_SUBDIR = ".mc_data"             # پوشه‌ی ریزِ داده کنار برنامه (misc)


def user_data_dir() -> Path:
    """پوشه‌ی داده‌های ریز — **کنار برنامه** (0.39.0). یک‌بار ساخته و برگردانده می‌شود."""
    env = os.environ.get("MC_DATA_DIR", "").strip()
    p = Path(env) if env else app_dir() / _DATA_SUBDIR
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def default_config_path() -> Path:
    """مسیر config.json — **کنار خودِ برنامه** (همان فایلی که در zip همراه است)."""
    env = os.environ.get("MC_DATA_DIR", "").strip()
    return (Path(env) if env else app_dir()) / "config.json"


def thumb_cache_dir() -> Path:
    """پوشه‌ی کش تامبنیل‌ها — **کنار برنامه** (`.thumb_cache`).

    کش مدت‌زمان (durations.db) و watch_later هم داخل همین پوشه‌اند پس آن‌ها هم
    کنار برنامه‌اند. بازتعریفِ دستیِ تست/عیب‌یابی: MC_CACHE_DIR."""
    env = os.environ.get("MC_CACHE_DIR", "").strip()
    p = Path(env) if env else app_dir() / ".thumb_cache"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _move(old: Path, new: Path) -> bool:
    """انتقال old به new اگر old هست و new نیست. برمی‌گرداند: آیا انتقال انجام شد؟"""
    try:
        if not old.exists() or new.exists():
            return False
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(old, new)             # همان درایو: فوری
        except OSError:
            shutil.move(str(old), str(new))  # درایو متفاوت: نسخه + حذف
        return True
    except OSError:
        return False


def _replace_or_copy(src: Path, dst: Path) -> bool:
    """src را روی dst بازنویسی می‌کند (move در صورت امکان؛ وگرنه کپی+حذف)."""
    try:
        if not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)             # بازنویسی اتمیک روی همان درایو
        except OSError:
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
                src.unlink()
        return True
    except OSError:
        return False


# ============================================== مهاجرتِ یک‌باره از مسیرِ سراسری
_MIGRATE_FILES = ("config.json", "data.json", "data.db",
                  "data.db-shm", "data.db-wal", "data.db-journal")


def _shared_data_dir() -> Path | None:
    """مسیرِ پوشه‌ی سراسریِ قدیمی (0.34.0–0.38.0) — **فقط برای مهاجرت خوانده می‌شود**.

    هیچ‌وقت چیزی در آن ساخته/نوشته نمی‌شود (درخواست 0.39.0: «هیچ چیزی در مسیرِ
    سراسری ذخیره نشه»). نبودِ متغیرهای رایج → None."""
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA")
            if not base:
                up = os.environ.get("USERPROFILE")
                base = str(Path(up) / "AppData" / "Local") if up else ""
            return Path(base) / _DIR_NAME if base else None
        base = os.environ.get("XDG_DATA_HOME",
                              str(Path.home() / ".local" / "share"))
        return Path(base) / _DIR_NAME
    except Exception as _exc_dbg:
        dbg_log(f"_shared_data_dir failed: {type(_exc_dbg).__name__}")
        return None


def migrate_shared_to_app() -> list[str]:
    """انتقالِ یک‌باره‌ی داده‌های مسیرِ سراسریِ قدیمی به کنارِ برنامه (0.39.0).

    فایل‌های شناخته‌شده (config/data.db/…) همیشه منتقل و بازنویسی می‌شوند —
    در دورانِ 0.34.0–0.38.0 مسیرِ سراسری منبعِ حقیقت بود و کنارِ برنامه فقط قالبِ
    خالیِ zip است. کشِ سراسری فقط وقتی منتقل می‌شود که کشِ کناربرنامه نباشد
    (دو کشِ هم‌زمان را بی‌صدا یکی نمی‌کنیم). در پایان، اگر پوشه‌ی سراسری خالی
    شد حذف می‌شود؛ اگر چیزی در آن ماند دست‌نخورده می‌ماند و دکمه‌ی «پاکسازی
    کامل» آن را هم هدف می‌گیرد. با MC_DATA_DIR (تست/پرتِ دستی) کاری نمی‌کند.

    برمی‌گرداند فهرستِ نامِ مواردِ منتقل‌شده (برای لاگ — بدون مسیر)."""
    moved: list[str] = []
    if os.environ.get("MC_DATA_DIR", "").strip():
        return moved                            # تست‌ها/پرتِ دستی — بدون مهاجرت
    try:
        shared = _shared_data_dir()
    except Exception as _exc_dbg:
        dbg_log(f"migrate_shared_to_app failed: {type(_exc_dbg).__name__}")
        shared = None
    try:
        if not shared or not shared.is_dir():
            return moved
        app = app_dir()
        for name in _MIGRATE_FILES:
            try:
                if _replace_or_copy(shared / name, app / name):
                    moved.append(name)
            except OSError:
                pass
        try:                                   # کشِ سراسری — فقط در صورت نبودِ کشِ محلی
            sc = shared / "thumb_cache"
            dc = app / ".thumb_cache"
            if sc.is_dir() and not dc.exists():
                if _replace_or_copy(sc, dc):
                    moved.append("thumb_cache")
        except OSError:
            pass
        try:
            shared.rmdir()                     # فقط وقتی خالی است (هیچ‌چیز سراسری نماند)
            moved.append("(پوشه‌ی سراسری حذف شد)")
        except OSError:
            pass
    except Exception as _exc_dbg:
        dbg_log(f"migrate_shared_to_app failed: {type(_exc_dbg).__name__}")
        pass
    return moved


# ================================================================ بازنشانی کامل
# دکمه‌ی «پاکسازی کامل» در تنظیمات (درخواست کاربر): همه‌ی داده‌های برنامه —
# config/data.db/کشِ کنار برنامه و اگر بازمانده‌ای از مسیرِ سراسریِ قدیمی هست —
# پاک و برنامه به حالتِ روزِ اول برمی‌گردد. چون فایل‌های دیتابیس/config در حینِ
# اجرا باز و قفل‌اند (ویندوز اجازه‌ی حذف نمی‌دهد)، پاک‌سازی با پرچمِ کنارِ برنامه
# به اجرای *بعدی* موکول می‌شود: دکمه پرچم را می‌نویسد و برنامه دوباره راه می‌افتد؛
# در بوت، قبل از بازشدن هر دیتابیسی، مقصدها حذف می‌شوند.

_RESET_FLAG_NAME = "._mc_reset"


def factory_reset_flag_path() -> Path:
    """مسیر پرچمِ بازنشانی — کنار برنامه (تا پاکشدن خودِ داده‌ها به آن آسیب نرسد)."""
    return app_dir() / _RESET_FLAG_NAME


def write_factory_reset_flag(targets) -> Path:
    """نوشتن اتمیکِ پرچم با فهرستِ مقصدها (رشته‌ی مسیرهای مطلق)."""
    import json as _json
    p = factory_reset_flag_path()
    uniq = []
    for t in targets or []:
        try:
            s = str(Path(str(t)))
            if s not in uniq:
                uniq.append(s)
        except Exception as _exc_dbg:
            dbg_log(f"write_factory_reset_flag failed: {type(_exc_dbg).__name__}")
            continue
    tmp = p.with_name(_RESET_FLAG_NAME + ".tmp")
    tmp.write_text(_json.dumps({"targets": uniq}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def factory_reset_if_pending() -> list:
    """اگر پرچمِ بازنشانی باشد مقصدها را حذف می‌کند — فقط در آغازِ بوت صدا زده شود.

    برمی‌گرداند فهرستِ مقصدِ پاک‌شده (بدون مسیر در لاگ — فقط شمارش). اگر حذفِ
    یک مقصد شکست بخورد (قفلِ فایل و…) پرچم می‌ماند تا اجرای بعدی دوباره تلاش شود.
    محافظت: مقصدِ ریشه‌ی درایو/خانه/خودِ پوشه‌ی برنامه هرگز حذف نمی‌شود."""
    import json as _json
    p = factory_reset_flag_path()
    try:
        if not p.exists():
            return []
        data = _json.loads(p.read_text(encoding="utf-8"))
        targets = data.get("targets") if isinstance(data, dict) else None
        if not isinstance(targets, list):
            targets = []
    except Exception as _exc_dbg:
        dbg_log(f"factory_reset_if_pending failed: {type(_exc_dbg).__name__}")
        targets = []
    wiped: list = []
    ok = True
    try:
        _app_res = app_dir().resolve()
        _home_res = Path.home().resolve()
    except OSError:
        _app_res = _home_res = None
    for t in targets:
        try:
            tp = Path(str(t))
            try:
                tr = tp.resolve()
            except OSError:
                tr = tp
            # محافظتِ حریم: فقط مسیرهای واقعاً تخصیص‌یافته به برنامه
            if (_app_res is not None and tr == _app_res) or \
               (_home_res is not None and tr == _home_res) or \
               len(tr.parts) < 3:
                ok = False
                continue
            if tp.is_dir():
                shutil.rmtree(str(tp))
            elif tp.exists():
                tp.unlink()
            wiped.append(str(tp))
        except OSError:
            ok = False                    # قفل‌شده — در بوتِ بعد دوباره
    try:
        user_data_dir()                   # پوشه‌ی داده‌ی تازه و خالی حتماً ساخته شود
    except OSError:
        pass
    # کاورهای سفارشی هم داده‌ی برنامه‌اند — بازنشانیِ کامل یعنی حذفشان (round 11)
    try:
        from covers import COVERS_DIR_NAME
        cov = app_dir() / COVERS_DIR_NAME
        if cov.is_dir():
            shutil.rmtree(str(cov))
    except OSError:
        ok = False                        # قفل‌شده — در بوتِ بعد دوباره
    except Exception as _exc_dbg:
        dbg_log(f"factory_reset_if_pending failed: {type(_exc_dbg).__name__}")
    if ok:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return wiped
