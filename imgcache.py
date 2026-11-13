# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""قالب فایل‌های کش تصویر — **WebP** (تنها قالبِ نوشتن).

تاریخچه: این ماژول قبلاً هم کش را به JPEG XL می‌نوشت. اندازه‌گیری روی تامبنیل‌های
واقعی (۱۶۰–۵۱۲px) نشان داد در این اندازه WebP هم کوچک‌تر است و هم سریع‌تر
(دیکود native در Qt) — و JXL فقط از PNG کوچک‌تر بود. کش‌های کهنه‌ی .png همچنان
خوانده می‌شوند؛ کشِ .jxl دیگر خوانده نمی‌شود (دیکودری برای آن همراه برنامه نیست)
و مثل فایلِ ناموجود رفتار می‌شود تا خودکار به WebP بازسازی شود.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

from watchtime import dbg_log

WEBP_QUALITY = 84      # روش ۴ — چند برابر سریع‌تر از JXL و کوچک‌تر در اندازه‌ی تامبنیل
WEBP_METHOD = 4


def apply_exif_transpose(im: Image.Image) -> Image.Image:
    """چرخشِ EXIF (تگِ orientation) را روی تصویر اعمال می‌کند — تک‌فریمی‌ها فقط.

    عکسِ گوشی با orientation≠1 (اکثرِ عکس‌های عمودیِ موبایل) بدونِ این کار
    خوابیده/وارونه ذخیره می‌شد — پوشِ round 20 §۲: import_cover، پوسترِ پوشه
    و تامبنیلِ فایل همه از همین هلپر استفاده می‌کنند.

    چرا فقط تک‌فریمی؟ ‎exif_transpose‎ روی تصویرِ چندفریمی در نسخه‌های
    مختلفِ Pillow فریم‌ها را می‌اندازد (آزمایش با Pillow 11.3: n_frames
    ‎2→1‎) — یعنی کاورِ متحرک بی‌صدا ساکن می‌شد. GIF/WebP/APNGِ متحرک عملاً
    orientation ندارد؛ محتاطانه دست نمی‌خوریم. هر خطایی = تصویرِ دست‌نخورده
    (بازیِ امن — نبودِ EXIF هرگز نباید import را بشکند).
    """
    try:
        if getattr(im, "is_animated", False) \
                or int(getattr(im, "n_frames", 1) or 1) > 1:
            return im
        if int((im.getexif() or {}).get(0x0112, 1) or 1) == 1:
            return im
        fixed = ImageOps.exif_transpose(im)
        return fixed if fixed is not None else im
    except Exception as _exc_dbg:
        dbg_log(f"apply_exif_transpose failed: {type(_exc_dbg).__name__}")
        return im


def _webp_ok() -> bool:
    """آیا Pillowِ حاضر WebP می‌نویسد؟ (در ویل‌های رسمی همیشه هست)"""
    try:
        import io
        Image.new("RGB", (2, 2)).save(io.BytesIO(), "WEBP")
        return True
    except Exception as _exc_dbg:
        dbg_log(f"_webp_ok failed: {type(_exc_dbg).__name__}")
        return False


WEBP_OK = _webp_ok()
CACHE_EXT = ".webp" if WEBP_OK else ".png"
# فقط قالب‌هایی که واقعاً می‌توانیم بخوانیم — .jxl عمداً نیست (بندِ بالای docstring):
# برگرداندنِ یک کشِ نادیدنی یعنی کارت برای همیشه روی placeholder حروفی می‌ماند.
_LEGACY_EXTS = (".webp", ".png")

_CACHE_FALLBACK_NAME = "FolderDex-cache"


def writable_cache_dir(cache_dir, fallback_name: str = _CACHE_FALLBACK_NAME
                       ) -> tuple[Path, bool]:
    """پوشه‌ی کشِ **قابل‌نوشتن** را تضمین می‌کند — فال‌بک به ‎%TEMP%‎.

    برنامه‌ی پرتابل ممکن است در پوشه‌ی فقط‌خواندنی استخراج شود (Program Files،
    درایوِ قفل‌شده، فلشِ write-protect). قبلاً `mkdir` مستقیم `PermissionError`
    می‌داد که از `main()` بیرون می‌زد و کاربر فقط «برنامه باز نمی‌شود» می‌دید.
    حالا: mkdir + آزمونِ نوشتنِ واقعی؛ اگر نشد، پوشه‌ی کش به ‎%TEMP%‎ می‌رود.
    خروجی: (مسیرِ مؤثرِ کش، آیا فال‌بکِ موقت روشن شد).
    """
    import tempfile

    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / f".wprobe-{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            raise PermissionError(f"not writable: {e}") from None
        return cache_dir, False
    except OSError as _exc_dbg:
        dbg_log(f"cache dir not writable ({type(_exc_dbg).__name__}) "
                f"— falling back to temp ({fallback_name})")
    fb = Path(tempfile.gettempdir()) / fallback_name
    try:
        fb.mkdir(parents=True, exist_ok=True)
        return fb, True
    except OSError as _exc_dbg:
        dbg_log(f"cache temp fallback failed: {type(_exc_dbg).__name__}")
        # حتی ‎%TEMP%‎ هم نوشتن نداشت — همان مسیرِ اصلی (خطاهای بعدی خودشان
        # در نقطه‌ی استفاده گرفته می‌شوند؛ مرگِ بی‌صدای استارت را نداریم)
        return cache_dir, False


def cache_file(directory, stem: str) -> Path:
    """مسیر فایل کش: نسخه‌ی جاری اگر هست همان، وگرنه نسخه‌ی کهنه‌ی **قابل‌خواندن**."""
    d = Path(directory)
    cur = d / (stem + CACHE_EXT)
    try:
        if cur.exists():
            return cur
        for ext in _LEGACY_EXTS:
            if ext == CACHE_EXT:
                continue
            q = d / (stem + ext)
            if q.exists():
                return q
    except OSError:
        pass
    return cur


def replace_with_retry(tmp: Path, out: Path, max_attempts: int = 8) -> None:
    """‎os.replace‎ با تلاشِ مجددِ کوتاه روی قفلِ گذرای ویندوز (round 28).

    ویندوز اجازه‌ی جایگزینیِ فایلِ *باز* را نمی‌دهد (sharing violation —
    winerror 32/33): آنتی‌ویروس/ایندکسر لحظه‌ای فایلِ تازه‌نوشته را اسکن
    می‌کند و چند کارگرِ هم‌زمان هم سرِ یک مقصد مسابقه می‌دهند (گزارشِ کاربرِ
    round 17: «بعضی وقت‌ها موقع تعویض کاور خطای ذخیره می‌دهد»). نسخه‌ی اول
    فقط در covers.py بود و جایگزینیِ imgcache همان‌جا لخت مانده بود — نتیجه‌اش در
    CI (ویندوز ۳٫۱۲، راند ۲۷) قرمزِ ناپایدار بود: در طوفانِ ۵۰ کارگرِ هم‌زمان
    چند جایگزینیِ پشت‌سرهم قفل می‌خورد و مسیرِ خطا تا QMessageBox می‌رفت.
    فقط PermissionError و فقط محدود (۸ بار، جمعاً ~۰٫۸ ثانیه — در نخِ کارگر
    است نه UI) تلاش مجدد می‌شود؛ بقیه‌ی خطاها (دیسکِ پر/دسترسیِ دائمی) بی‌معطلی
    بالا می‌آیند و فایلِ قبلی دست‌نخورده می‌ماند. روی لینوکس چنین قفلی وجود
    ندارد و حلقه در نخستین تلاش تمام می‌شود؛ گاردش در tests/test_round28_fixes.py
    با تزریقِ خطاست، نه با ریسِ واقعی ویندوز."""
    last: PermissionError | None = None
    for attempt in range(max_attempts):
        try:
            os.replace(tmp, out)
            return
        except PermissionError as e:
            last = e
            dbg_log(f"replace-retry {attempt + 1}/{max_attempts}: "
                    f"{type(e).__name__} errno={getattr(e, 'errno', None)} "
                    f"winerror={getattr(e, 'winerror', None)}")
            time.sleep(0.03 * (attempt + 1))
        except OSError as e:
            dbg_log(f"replace failed: {type(e).__name__} "
                    f"errno={getattr(e, 'errno', None)} "
                    f"winerror={getattr(e, 'winerror', None)} on {out.name}")
            raise
    raise last  # type: ignore[misc]


def save_cache_image(im: Image.Image, out: Path):
    """ذخیره‌ی اتمیک تصویر کش (WebP؛ فال‌بک PNG).

    نوشتن روی فایل موقتِ یکتا + جایگزینی اتمیک با تلاشِ مجددِ قفلِ گذرای
    ویندوز (round 28): دو کارگر موازی روی یک خروجی فایل خراب نمی‌سازند و
    قفلِ لحظه‌ایِ جایگزینی هم دیگر کش را بی‌صدا به PNG نمی‌برد. پروفایل رنگیِ
    منبع منتقل نمی‌شود. اگر WebP شکست خورد، PNG کنار همان مسیر نوشته و
    مسیرِ واقعی برگردانده می‌شود — فراخوان باید همیشه مقدار برگشتی را
    استفاده کند، نه `out` را.
    """
    out = Path(out)
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGB")
    try:
        im.info.pop("icc_profile", None)
    except Exception as _exc_dbg:
        dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
        pass
    tmp = out.with_name(f"{out.stem}.tmp{os.getpid()}{threading.get_ident()}{out.suffix}")
    try:
        if out.suffix.lower() != ".png":
            im.save(tmp, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
        else:
            im.save(tmp, "PNG", optimize=False, compress_level=1)
        replace_with_retry(tmp, out)
        return out
    except Exception as _exc_dbg:
        dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        alt = out.with_suffix(".png")
        atmp = alt.with_name(f"{alt.stem}.tmp{os.getpid()}{threading.get_ident()}.png")
        try:
            im.save(atmp, "PNG", optimize=False, compress_level=1)
            replace_with_retry(atmp, alt)
            return alt
        except Exception as _exc_dbg:
            dbg_log(f"save_cache_image failed: {type(_exc_dbg).__name__}")
            try:
                atmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise


def read_cache_pil(path: Path) -> Image.Image | None:
    """خواندن فایل کش به تصویر PIL — برای مسیرهای غیر Qt (HTML export و …)."""
    try:
        im = Image.open(path)
        im.load()
        return im
    except Exception as _exc_dbg:
        dbg_log(f"read_cache_pil failed: {type(_exc_dbg).__name__}")
        return None
