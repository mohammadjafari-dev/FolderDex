# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""موتور دوزبانه‌ی برنامه (نسخه‌ی 0.45.0 — درخواست کاربر «برنامه دو زبانه کن»).

طراحی (درخواست کاربر: پیش‌فرض **انگلیسی** + ذخیره‌ی دائمی انتخاب کاربر):
  · کلیدِ ترجمه خودِ رشته‌ی فارسیِ داخل کد است — پس صدها محلِ فراخوانی دست‌نخورده
    می‌مانند و فقط با T(…) پوشیده شده‌اند. اگر کلیدی در واژه‌نامه نباشد، همان
    متنِ ورودی برمی‌گردد (فال‌بک امن — هیچ‌وقت کرش/رشته‌ی خالی نداریم).
  · قالب‌های دارای {0}… از f-stringهای قبلی ساخته شده‌اند و با .format پر می‌شوند؛
    ترجمه‌ی انگلیسی حق دارد ترتیب جای‌نشان‌ها را عوض کند.
  · جهت چیدمان (RTL/LTR) و ارقام (۰۱۲۳/0123) از همین‌جا خوانده می‌شود.

زبان از config.json خوانده می‌شود (main.py بعد از ساخت Store صدا می‌زند:
set_lang(store.language())). برای تست‌ها/اجرای دستی، متغیر محیطی MC_LANG=fa|en
پیش‌فرضِ اولیه را تعیین می‌کند (تنظیمات ذخیره‌شده بر آن غالب است).
"""
from __future__ import annotations

import os

_LANG = os.environ.get("MC_LANG", "en").strip().lower()
if _LANG not in ("fa", "en"):
    _LANG = "en"

LANGUAGES = (("en", "English"), ("fa", "فارسی"))


def lang() -> str:
    """کد زبانِ فعلی: «fa» یا «en»."""
    return _LANG


def set_lang(code: str) -> None:
    """تنظیم زبانِ درحال‌اجرا. ناشناخته → en (پیش‌فرضِ امنِ درخواستی کاربر)."""
    global _LANG
    _LANG = "fa" if str(code).strip().lower() == "fa" else "en"


def lang_from_config(raw) -> str:
    """زبانِ مؤثر از یک configِ خام — قاعده‌ی مهاجرتِ 0.45.0 در «یک» جا.

    قبلاً همین قاعده در store.py و main.py (و مسیرِ ‎--config‎) کپی شده بود؛
    با هر تغییرِ کوچک یک‌جا از قلم می‌افتاد (D14). قاعده:
    · کلیدِ language اگر "fa" باشد → fa؛ هر چیزِ دیگر → en.
    · configِ قدیمیِ قبل از 0.45.0 کلیدِ زبان ندارد — اگر نشانه‌ی استفاده‌ی
      قبلی (مسیر/دسته) هست، کاربر فارسی‌زبان بوده: fa می‌ماند (پیش‌فرضِ en
      فقط برای نصب‌های تازه است).
    """
    if not isinstance(raw, dict):
        return "en"
    lang = raw.get("language")
    if lang is None and (raw.get("roots") or raw.get("categories")):
        lang = "fa"
    return "fa" if str(lang).strip().lower() == "fa" else "en"


def is_rtl() -> bool:
    """چیدمان راست‌به‌چپ فقط برای فارسی."""
    return _LANG == "fa"


def disp_cat(cat: str) -> str:
    """نامِ دسته برای *نمایش* — 0.46.0 (درخواست کاربر: «وقتی انگلیسیه همچی انگلیسی»).

    نامِ دسته داده‌ی کاربر است و در config/دیتابیس همیشه فارسیِ اصلی می‌ماند؛
    این تابع فقط *متنِ نمایشی* را سگمنت‌به‌سگمنت («انیمه/تماشا‌شده» → هر بخش)
    از واژه‌نامه می‌گذراند: نام‌های رایج فارسی انگلیسی می‌شوند و نام‌های
    سفارشیِ کاربر که در واژه‌نامه نیستند دست‌نخورده (فال‌بک امن) می‌مانند."""
    s = str(cat or "")
    if _LANG == "fa" or not s:
        return s
    return "/".join(T(seg.strip()) if seg.strip() else seg for seg in s.split("/"))


def T(text: str) -> str:
    """ترجمه‌ی لحظه‌ایِ یک رشته‌ی فارسی (یا قالبِ آن) به زبانِ فعلی.

    در حالت فارسی همان ورودی برمی‌گردد (هزینه‌ی رشته‌ای صفر). در حالت انگلیسی
    از واژه‌نامه‌ی i18n_en.EN خوانده می‌شود؛ نبودِ کلید → همان ورودی (فال‌بک امن).
    واژه‌نامه تنبل لود می‌شود تا ایمپورتِ i18n همیشه سبک بماند."""
    if _LANG == "fa" or not text:
        return text
    try:
        return _EN.get(text, text)
    except NameError:
        try:
            from i18n_en import EN as _loaded
        except Exception:
            _loaded = {}
        globals()["_EN"] = _loaded
        return _loaded.get(text, text)


def digits(s) -> str:
    """ارقام متناسب با زبان: فارسی → ۰۱۲۳…، انگلیسی → اعداد لاتین.

    جایگزینِ هوشمندِ ترجمه‌ی دستیِ ارقام در fa()/format_duration — با این کار
    همه‌ی شمارنده‌ها بدون دست‌خوردنِ صدها محلِ فراخوانی دوزبانه می‌شوند."""
    s = str(s)
    if _LANG == "fa":
        return s.translate(_FA_TABLE)
    return s


_FA_TABLE = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


# -------------------------------------------------- دکمه‌های استاندارد دیالوگ‌ها
# درخواست کاربر — «برای بخش فارسی هم همه‌چیز فارسی باشه حتی yes,no»:
# متنِ دکمه‌های بله/خیر/باشه/… در QMessageBox و QInputDialog را Qt از ترجمه‌ی
# داخلیِ خودش می‌خوانَد که در نسخه‌ی توزیع‌شده وجود ندارد → انگلیسی می‌ماند. به‌جای
# دست‌زدن به صدها فراخوان، نگهبانِ رویدادِ سراسری متنِ دکمه‌های استاندارد را
# هنگام آماده‌سازی هر دیالوگ داخلی محلی می‌کند — حتی باکس‌های استاتیکِ
# QMessageBox.question و OK/Cancelِ داخل QInputDialog. در حالت انگلیسی هیچ
# کاری نمی‌کند (پیش‌فرضِ Qt همان انگلیسی مطلوب است). دیالوگ‌های فایلِ بومیِ
# سیستم‌عامل (انتخاب پوشه/فایل) از زبانِ خودِ ویندوز پیروی می‌کنند.
_STD_BUTTON_TEXTS = {
    "&yes": "بله", "yes": "بله", "yes to all": "بله برای همه",
    "&yes to all": "بله برای همه", "yestoall": "بله برای همه",
    "&no": "خیر", "no": "خیر", "no to all": "خیر برای همه",
    "&no to all": "خیر برای همه", "notoall": "خیر برای همه",
    "ok": "باشه", "&ok": "باشه",
    "cancel": "انصراف", "&cancel": "انصراف",
    "save": "ذخیره", "&save": "ذخیره", "save all": "ذخیره‌ی همه",
    "don't save": "ذخیره نکن", "don'tsave": "ذخیره نکن", "discard": "ذخیره نکن",
    "close": "بستن", "&close": "بستن",
    "retry": "تلاش دوباره", "&retry": "تلاش دوباره",
    "abort": "لغو", "&abort": "لغو",
    "ignore": "نادیده گرفتن", "&ignore": "نادیده گرفتن",
    "apply": "اعمال", "&apply": "اعمال",
    "reset": "بازنشانی", "&reset": "بازنشانی",
    "restore defaults": "بازگردانی پیش‌فرض‌ها",
    "help": "راهنما", "&help": "راهنما",
    "open": "باز کردن", "&open": "باز کردن",
}


def localize_std_buttons(widget) -> int:
    """متنِ دکمه‌های استانداردِ انگلیسیِ یک پنجره را به فارسی بازنویسی می‌کند.

    هم برای استفاده‌ی مستقیم روی یک QMessageBox/QInputDialog تازه‌ساخته‌شده،
    هم از داخل نگهبانِ رویداد. فقط در حالت فارسی اثر دارد. برمی‌گرداند: تعداد
    دکمه‌های بازنویسی‌شده (برای تست/تشخیص)."""
    if _LANG != "fa":
        return 0
    try:
        from PySide6.QtWidgets import QAbstractButton
    except Exception:
        return 0
    changed = 0
    try:
        for b in widget.findChildren(QAbstractButton):
            try:
                txt = (b.text() or "").strip()
            except Exception:
                continue
            key = txt.replace(" ", " ").lower()
            local = _STD_BUTTON_TEXTS.get(key)
            if local is None and key.startswith("&"):
                local = _STD_BUTTON_TEXTS.get(key.lstrip("&"))
            if local:
                b.setText(T(local))
                changed += 1
    except Exception:
        # hot path: runs for every dialog polish/show — silence keeps this
        # free of log spam and it must never break showing a dialog.
        pass
    return changed


class _StdButtonGuard:
    """نگهبانِ سراسری: هر دیالوگ استاندارد که polish/show شود، دکمه‌هایش فارسی."""

    def __init__(self, app):
        self._app = app
        self._filter = None

    def install(self):
        try:
            from PySide6.QtCore import QObject, QEvent

            class _Filter(QObject):
                def eventFilter(self, obj, ev):
                    try:
                        if ev.type() in (QEvent.Type.Polish, QEvent.Type.Show):
                            from PySide6.QtWidgets import QMessageBox, QInputDialog, QErrorMessage
                            if isinstance(obj, (QMessageBox, QInputDialog, QErrorMessage)):
                                localize_std_buttons(obj)
                    except Exception:
                        # global event filter: fires for EVERY Qt event — it
                        # must never raise or log-spam; return False always.
                        pass
                    return False

            self._filter = _Filter(self._app)
            self._app.installEventFilter(self._filter)
        except Exception:
            self._filter = None


def install_std_button_localizer(app) -> None:
    """نصب نگهبانِ فارسی‌سازیِ دکمه‌های استاندارد روی QApplication (یک‌بار)."""
    try:
        if getattr(app, "_mc_std_guard48", None) is None:
            g = _StdButtonGuard(app)
            g.install()
            app._mc_std_guard48 = g
    except Exception as _exc_dbg:
        try:
            from watchtime import dbg_log   # lazy import — avoids a module cycle
            dbg_log(f"install_std_button_localizer failed: {type(_exc_dbg).__name__}")
        except Exception:
            pass  # one-shot startup nicety — must never break app boot
