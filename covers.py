# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""کاورِ سفارشیِ پوشه — کپیِ نرمال‌شده داخل پوشه‌ی برنامه.

قواعد ثابت (نباید نقض شوند):
- هرگز داخل پوشه‌ی کاربر نوشته نمی‌شود (نه desktop.ini، نه icon.ico) — این
  برنامه وعده داده «هیچ‌وقت به فایل‌هایت دست نمی‌زند».
- عکسِ کاربر در APP_DIR/.covers/ کپی و به WebP نرمال می‌شود، پس اگر او فایلِ
  اصلی را جابه‌جا/پاک کند کاور نمی‌پرد.
- کاورِ متحرک: حداکثر ۵۱۲px، ۶۰ فریم، ۵ ثانیه، ۳ مگابایت؛ برای شبکه‌ی کارت‌ها
  همیشه فریمِ ۰ به‌صورت ساکن ذخیره می‌شود (پخشِ متحرک فقط در صفحه‌ی جزئیات).

نامِ فایل‌ها هشِ محتواست: importِ دوباره‌ی همان عکس فایلِ تازه نمی‌سازد و دو
کاورِ متفاوت هرگز سرِ یک نام جنگیده نمی‌شوند؛ حذف هم idempotent است.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from PIL import Image

from i18n import T
# (round 28) هلپرِ تلاشِ مجددِ جایگزینی یک‌جا در imgcache زندگی می‌کند و
# هر دو نویسنده‌ی اتمیک (کاور و کشِ تامبنیل) از همان نسخه استفاده می‌کنند
# تا رفتارشان هنگامِ قفلِ گذرای ویندوز یکی بماند.
from imgcache import apply_exif_transpose, replace_with_retry
from portable import app_dir
from watchtime import dbg_log

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow قدیمی
    RESAMPLE = Image.LANCZOS

COVERS_DIR_NAME = ".covers"
STATIC_MAX_PX = 1024        # منبعِ کاور ساکن (کیفیت بالا برای hero/بزرگ‌نمایی)
ANIM_MAX_PX = 512
ANIM_MAX_FRAMES = 60
ANIM_MAX_MS = 5000
ANIM_MAX_BYTES = 3 * 1024 * 1024
SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
# سقف‌های ورودیِ ایمپورتِ کاور — PNG غول‌آسا/TIFF چندصد مگابایتی نباید
# چند گیگ RAM بگیرد (خواندنِ کاملِ فایل + دیکدِ ابعادِ کامل؛ گزارشِ «برنامه
# بسته شد» روی ویندوز). عکسِ بزرگ‌تر از سقف‌ها رد می‌شود، نه crash.
MAX_SOURCE_BYTES = 32 * 1024 * 1024     # سقفِ حجمِ فایلِ منبع: ۳۲ مگابایت
# GIF واقعی (خروجیِ رکوردها/دانلود) اغلب از ۳۲ مگابایت بزرگ‌ترند. GIF
# فریم‌به‌فریم دیکد می‌شود و سقفِ ۶۰ فریم/۵ ثانیه داریم، پس RAM با سقفِ
# بالاتر هم مهار است — فقط GIF سقفِ جداگانه دارد (C4-c).
MAX_GIF_SOURCE_BYTES = 128 * 1024 * 1024  # سقفِ حجمِ فایلِ GIF: ۱۲۸ مگابایت
MAX_SOURCE_PIXELS = 40_000_000          # سقفِ ابعادِ منبع: ۴۰ مگاپیکسل
TMP_MAX_AGE_S = 3600                    # ‎.tmp-*‎ قدیمی‌تر از یک ساعت = پسماندِ قطعی (C6)
# پیامِ خطای ذخیره‌سازی — در covers و در UI (کارگرِ ایمپورت) یکسان است
WRITE_ERR_TEXT = "ذخیره‌ی کاور ممکن نشد (فضای دیسک یا دسترسیِ پوشه‌ی برنامه)."
READ_ERR_TEXT = "این فایل تصویرِ معتبری نیست."
TOO_BIG_ERR_TEXT = "تصویر برای کاور بیش از حد بزرگ است."
# سقفِ حجمی — عددِ سقف در پیام می‌آید تا کاربر بداند چقدر باید کوچک کند (C4-c)
TOO_BIG_BYTES_ERR_TEXT = ("تصویر برای کاور بیش از حد بزرگ است."
                         " (سقف: {0} مگابایت).")
DEGRADED_MSG_TEXT = ("انیمیشنِ این عکس برای کاور سنگین بود — فریمِ اول "
                     "به‌صورت ساکن ذخیره شد.")

# کیفیتِ کاور ساکن ۹۲ است چون این فایل «منبع» همه‌ی تامبنیل‌های مشتق است
STATIC_QUALITY = 92
ANIM_QUALITY = 80           # نردبانِ کاهش (C4-b): ۸۰ → ۶۰ → ۴۰ + فریم/ابعاد کمتر


class CoverError(Exception):
    """خطای قابل‌نمایش به کاربر (متنِ فارسی آماده برای UI)."""


class CoverReadError(CoverError):
    """فایلِ *منبع* مشکل دارد (خراب / نیست / بیش از حد بزرگ) — نه ذخیره‌سازی.

    از CoverError مشتق است تا همه‌ی `except CoverError`های موجود دست‌نخورده
    بمانند؛ فقط پیامِ دیگر دیگر «عکسِ سالم = خراب» نشان داده نمی‌شود.
    """


class CoverWriteError(CoverError):
    """ذخیره‌سازی در ‎.covers/‎ ممکن نشد (دسترسیِ پوشه / دیسکِ پر)."""


def covers_dir(app_dir_path=None) -> Path:
    """پوشه‌ی کاورها: ‎<app_dir>/.covers‎ — در هر دو شاخه با یک معنا.

    (باگِ قبلی: با پاس‌دادنِ app_dir_path خودِ همان مسیر برگردانده می‌شد نه
    زیرپوشه‌ی ‎.covers/‎ آن — یعنی «covers_dir(fake_app)» پوشه‌ی برنامه‌ی
    قلابی را می‌داد و تست‌هایی که با همین تابع می‌شمردند هیچ‌چیز را ثابت
    نمی‌کردند. ساخته نمی‌شود — فقط مسیر.)
    """
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    return base / COVERS_DIR_NAME


def is_animated(path) -> bool:
    """آیا فایلِ تصویر چندفریمی است؟ (GIF/WebP متحرک و …)"""
    try:
        with Image.open(path) as im:
            return bool(getattr(im, "is_animated", False)) \
                or int(getattr(im, "n_frames", 1)) > 1
    except Exception as _exc_dbg:
        dbg_log(f"is_animated failed: {type(_exc_dbg).__name__}")
        return False


def _err_note(e: BaseException) -> str:
    """یادداشتِ خطا برای لاگ — نوع + errno/winerror + نامِ فایلِ مقصد (بدون مسیر).

    (round 17 §۱) قبلاً فقط نوعِ exception در لاگ می‌آمد؛ با «ذخیره‌ی کاور
    ممکن نشد» هیچ‌کس نمی‌توانست بفهمد علت دیسک بوده، دسترسی، یا قفلِ گذرای
    ویندوز. حالا مرحله‌ی شکست + کدِ خطا در لاگِ بی‌نام می‌آید (dbg_log مسیر
    مطلق را سانسور می‌کند؛ نامِ فایلِ مقصد فقط هشِ محتواست)."""
    return (f"{type(e).__name__} errno={getattr(e, 'errno', None)} "
            f"winerror={getattr(e, 'winerror', None)}")


def _atomic_save(im: Image.Image, out: Path, *, save_all=False,
                 append_images=None, duration=None, loop=None, quality=None,
                 method=None):
    """نوشتنِ اتمیک WebP — فایلِ موقتِ یکتا + os.replace.

    method=None یعنی پیش‌فرض (ساکن ۶ / متحرک ۴)؛ نردبانِ C4-b در تلاش‌های
    بعدی method سبک‌تر (۲) می‌دهد تا زمانِ کل از چند ثانیه بیشتر نشود.
    (round 17 §۱) مرحله‌ی انکد از مرحله‌ی جایگزینی جدا شد — هر کدام لاگِ
    خودش را با errno/winerror می‌نویسد تا علتِ واقعی از لاگ دربیاید."""
    tmp = out.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{out.name}")
    kw: dict = {"quality": quality if quality is not None else STATIC_QUALITY,
                "method": 6}
    if save_all:
        kw.update(save_all=True, append_images=append_images or [],
                  duration=duration or 100, loop=0 if loop is None else loop,
                  method=4)
    if method is not None:
        kw["method"] = method
    try:
        im.save(tmp, "WEBP", **kw)
    except OSError as e:
        # خطای انکد/نوشتنِ موقت (دیسکِ پر همین‌جا هم ممکن است)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        dbg_log(f"_atomic_save encode failed: {_err_note(e)} file={out.name}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        replace_with_retry(tmp, out)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        dbg_log(f"_atomic_save replace failed: {_err_note(e)} file={out.name}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None


def _webp_decodes(path: Path) -> bool:
    """آیا این WebP *واقعاً* دیکد می‌شود؟ (round 19 §۸)

    سرآیندِ معتبر کافی نیست: payload می‌تواند وسطِ فایل خراب باشد و Pillow
    موقعِ دیکد «could not create decoder object» بدهد — آن فایل قبلاً از
    idempotent قبول می‌شد و هرگز تعمیر نمی‌شد. حالا فریمِ اول (و در فایلِ
    متحرک، فریمِ دومِ در صورت وجود) دیکد می‌شود؛ هزینه‌اش برای کاورِ ≤چندمگ
    میلی‌ثانیه‌ای است و فقط در مسیرِ idempotent می‌آید."""
    try:
        with Image.open(path) as im:
            im.load()
            if int(getattr(im, "n_frames", 1) or 1) > 1:
                try:
                    im.seek(1)
                    im.load()
                except EOFError:
                    pass                      # تک‌فریمِ مؤثر — کافی است
        return True
    except Exception as _exc_dbg:
        dbg_log(f"_webp_decodes failed: {type(_exc_dbg).__name__}")
        return False


def _looks_like_webp(path: Path) -> bool:
    """آیا مقصدِ موجود یک WebPِ سالم است؟ (idempotent بودنِ import — round 17 §۱)

    نامِ فایلِ مقصد = هشِ محتوای *منبع* است؛ پس همان عیسی که دوباره انتخاب
    می‌شود، نامِ مقصدِ همان است. قبلاً importِ دوباره بدونِ چک دوباره
    انکد و os.replace می‌زد — وقتی کاورِ متحرک همان لحظه در صفحه‌ی جزئیات
    پخش می‌شود، QMovie فایلِ ‎.anim.webp را باز نگه می‌دارد و ویندوز
    جایگزینی را با PermissionError می‌بندد → پیامِ گمراه‌کننده‌ی «فضای دیسک
    یا دسترسی». حالا مقصدِ سالم = بدونِ نوشتن (docstringِ خودِ این ماژول
    از روزِ اول همین را قول می‌داد). فایلِ ناقص/خالی (پسماندِ کرش) بازنویسی
    می‌شود.
    (round 19 §۸) «سالم» = سرآیندِ معتبر *و* دیکدِ واقعی — payloadِ خرابِ
    با سرآیندِ سالم تعمیر می‌شود، نه قبولِ دوباره."""
    try:
        if path.stat().st_size < 32:
            return False
        with open(path, "rb") as f:
            head = f.read(12)
        if not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
            return False
        return _webp_decodes(path)
    except OSError:
        return False


def _validate_source(src: Path) -> Image.Image:
    """بازکردنِ معتبرِ فایلِ منبع — پسوند و محتوا و سقفِ ابعاد کنترل می‌شوند.

    خطایِ منبع = CoverReadError (عکسِ خراب/بزرگ)؛ خطایِ نوشتنِ بعدی در
    import_cover با CoverWriteError جدا می‌شود — پیامِ گمراه‌کننده‌ی «عکسِ
    سالم را خراب می‌گوید» از این‌جا ریشه‌کن می‌شود.
    """
    if src.suffix.lower() not in SUPPORTED_EXT:
        raise CoverReadError(T(READ_ERR_TEXT))
    try:
        im = Image.open(src)
        if im.width * im.height > MAX_SOURCE_PIXELS:
            im.close()
            raise CoverReadError(T(TOO_BIG_ERR_TEXT))
        im.verify()                     # محتوا — نه فقط پسوند
        im.close()
    except CoverReadError:
        raise
    except Exception as _exc_dbg:
        dbg_log(f"_validate_source failed: {type(_exc_dbg).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    try:
        im = Image.open(src)
        if im.width * im.height > MAX_SOURCE_PIXELS:
            im.close()
            raise CoverReadError(T(TOO_BIG_ERR_TEXT))
        if (im.format or "").upper() == "JPEG":
            # دیکدِ سریعِ در مقیاسِ کاور — JPEG ۴۰ مگاپیکسلی تمام‌اندازه به RAM
            # نمی‌آید؛ draft فقط header-level است و کیفیتِ خروجیِ ۱۰۲۴px نمی‌کاهد
            try:
                im.draft("RGB", (STATIC_MAX_PX, STATIC_MAX_PX))
            except Exception as _exc_dbg:
                dbg_log(f"_validate_source draft failed: {type(_exc_dbg).__name__}")
        im.load()
        # (round 20 §۲) چرخشِ EXIF — عکسِ گوشی با orientation≠1 قبلاً خوابیده
        # ذخیره می‌شد (JPEG افقیِ ۴۰۰×۲۰۰ با چرخشِ ۹۰° باید ۲۰۰×۴۰۰ دیده شود).
        # هلپر برای تصویرِ متحرک دست‌نخورده برمی‌گرداند (exif_transpose فریم‌ها
        # را می‌اندازد) و روی هر خطایی بازیِ امن است.
        im = apply_exif_transpose(im)
    except CoverReadError:
        raise
    except Exception as _exc_dbg:
        dbg_log(f"_validate_source load failed: {type(_exc_dbg).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    return im


# خطاهای دیکدِ Pillow در وسطِ فایلِ چندفریمی (GIF ناقص/خراب — C4-a):
# EOFError/Truncated/OSError و IndexError/خطای unpackِ فریم‌های معیوب
_DECODE_ERRS = (OSError, EOFError, SyntaxError, ValueError, IndexError)


def _frame_iter(im: Image.Image):
    """فریم‌های یک تصویر را به‌صورت RGBA می‌دهد — با توقفِ نرم روی خطای دیکد.

    (C4-a) GIF ناقص در فریم‌های بعدی OSError/EOFError می‌دهد؛ قبلاً این خطا
    خام از import_cover بیرون می‌پرید و UI پیامِ غلطِ «ذخیره ممکن نشد» نشان
    می‌داد. حالا تکرارِ فریم‌ها در اولین خطای دیکد تمام می‌شود و فریم‌های
    سالمِ خوانده‌شده برمی‌گردند (تصمیمِ محصولی: کاورِ جزئی قابل‌استفاده است؛
    اگر هیچ فریمی خوانده نشود import_cover خطای خواندن می‌دهد)."""
    n = int(getattr(im, "n_frames", 1) or 1)
    for i in range(n):
        try:
            im.seek(i)
        except EOFError:
            break
        except _DECODE_ERRS as _exc_dbg:
            dbg_log(f"_frame_iter seek failed at frame {i}: {type(_exc_dbg).__name__}")
            break
        try:
            fr = im.convert("RGBA")
        except _DECODE_ERRS as _exc_dbg:
            dbg_log(f"_frame_iter convert failed at frame {i}: {type(_exc_dbg).__name__}")
            break
        yield fr, int(im.info.get("duration", 100) or 100)


# بودجه‌ی زمانیِ کلِ نردبانِ انکدِ متحرک — بعد از آن فریمِ اول ساکن ذخیره می‌شود
# (C4-b: قبلاً GIFِ نویزیِ ۶۰ فریمی ~۱۳ ثانیه بعد فقط «سنگین» می‌گفت؛ کاربر
# هیچ راهی نداشت. حالا زمانِ کل مهار است و همیشه یک کاورِ قابل‌استفاده می‌ماند)
ANIM_ENCODE_BUDGET_S = 8.0


def _encode_animated(frames: list[Image.Image], durations: list[int],
                     out: Path, *, t0: float | None = None) -> int | None:
    """انکدِ کاورِ متحرک با نردبانِ نرم‌شدن → بایتِ نهایی یا None (تسلیم).

    ترتیبِ تلاش‌ها: کیفیت ۸۰→۶۰→۴۰ (تلاش‌های بعدی method سبک‌تر) →
    یکی‌درمیان‌کردنِ فریم‌ها (مدت‌ها ×۲ تا ریتم حفظ شود) → کوچک‌کردن به
    ۳۸۴ و بعد ۲۵۶ پیکسل. اگر هیچ پیکربندی زیرِ سقفِ بایت نرفت یا بودجه‌ی
    زمانی تمام شد None برمی‌گردد — import_cover در آن حالت فریمِ اول را
    به‌عنوان کاور ساکن ذخیره می‌کند (degraded) به‌جای خطای بی‌راه‌حل."""
    start = time.monotonic() if t0 is None else t0
    half = frames[::2]
    half_durs = [d * 2 for d in durations[::2]]
    attempts: list[tuple[list, list, int, int]] = [
        (frames, durations, ANIM_QUALITY, 4),
        (frames, durations, 60, 4),
        (frames, durations, 40, 2),
        (half, half_durs, 40, 2),
    ]
    for px in (384, 256):
        small = [f.copy() for f in half]
        for f in small:
            f.thumbnail((px, px), RESAMPLE)
        attempts.append((small, half_durs, 40, 2))
    for frs, durs, q, method in attempts:
        if time.monotonic() - start > ANIM_ENCODE_BUDGET_S:
            dbg_log("_encode_animated: time budget exhausted -> degraded")
            return None
        _atomic_save(frs[0], out, save_all=True, append_images=frs[1:],
                     duration=durs, loop=0, quality=q, method=method)
        try:
            size = out.stat().st_size
        except OSError:
            size = ANIM_MAX_BYTES + 1
        if size <= ANIM_MAX_BYTES:
            return size
    dbg_log("_encode_animated: every rung over budget -> degraded")
    return None


def _digest_of(path: Path) -> str:
    """هشِ SHA-1 بلوکی (۱MB) — فایلِ منبع یک‌جا در RAM خوانده نمی‌شود."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def import_cover(src, app_dir_path=None) -> dict:
    """نرمال‌سازی + کپیِ کاور در ‎APP_DIR/.covers/‎ → اطلاعاتِ آن.

    خروجی: {"poster": "<rel>", "poster_static": "<rel>|None",
            "animated": bool, "bytes": int}
    مسیرها نسبی به پوشه‌ی برنامه‌اند (با ‎/‎) — اگر کلِ پوشه‌ی پرتابل جابه‌جا
    شود کاورها همچنان پیدا می‌شوند.

    GIFهای سنگین‌تر از آن‌که زیرِ سقفِ ۳MB جا شوند به‌جای خطا به کاورِ ساکن
    (فریمِ اول) تنزل می‌یابند — کلیدِ "degraded": True در دیکشنریِ برگشتی
    به UI می‌گوید پیامِ «انیمیشن حذف شد» نشان دهد (C4-b).

    سقف‌های ورودی (MAX_SOURCE_BYTES / MAX_GIF_SOURCE_BYTES /
    MAX_SOURCE_PIXELS) با CoverReadError
    رد می‌شوند؛ خطای نوشتن (دسترسی/دیسک‌پر) با CoverWriteError جدا است.
    """
    src = Path(src)
    if not src.is_file():
        raise CoverReadError(T(READ_ERR_TEXT))
    try:
        # GIF سقفِ حجمیِ جداگانه دارد — فریم‌به‌فریم دیکد می‌شود و سقفِ
        # فریم/مدت داریم؛ ۳۲MB برای GIFهای واقعی کم بود (C4-c)
        cap = (MAX_GIF_SOURCE_BYTES if src.suffix.lower() == ".gif"
               else MAX_SOURCE_BYTES)
        if src.stat().st_size > cap:
            raise CoverReadError(T(TOO_BIG_BYTES_ERR_TEXT).format(
                max(1, round(cap / (1024 * 1024)))))
    except OSError as e:
        dbg_log(f"import_cover stat failed: {type(e).__name__}")
        raise CoverReadError(T(READ_ERR_TEXT)) from None
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    dest = base / COVERS_DIR_NAME
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        dbg_log(f"import_cover mkdir failed: {type(e).__name__}")
        raise CoverWriteError(T(WRITE_ERR_TEXT)) from None

    im = _validate_source(src)
    digest = _digest_of(src)
    animated = bool(getattr(im, "is_animated", False)) \
        or int(getattr(im, "n_frames", 1)) > 1

    static_path = dest / f"{digest}.webp"
    static_rel = f"{COVERS_DIR_NAME}/{static_path.name}"

    if not animated:
        if not _looks_like_webp(static_path):      # idempotent (round 17 §۱)
            st = im.convert("RGBA")
            st.thumbnail((STATIC_MAX_PX, STATIC_MAX_PX), RESAMPLE)
            try:
                _atomic_save(st, static_path, quality=STATIC_QUALITY)
            except CoverError:
                raise
            except OSError as e:
                # خطای نوشتن (دیسکِ پر / دسترسی) — به‌عنوان «عکسِ خراب» نشان داده نمی‌شود
                dbg_log(f"import_cover save failed: {_err_note(e)}")
                raise CoverWriteError(T(WRITE_ERR_TEXT)) from None
        try:
            size = static_path.stat().st_size
        except OSError:
            size = 0
        return {"poster": static_rel, "poster_static": None,
                "animated": False, "bytes": size}

    # ---- کاور متحرک: فریم‌ها تا سقف‌ها کوچک و کوتاه می‌شوند ----
    frames: list[Image.Image] = []
    durations: list[int] = []
    total_ms = 0
    for fr, dur in _frame_iter(im):
        if len(frames) >= ANIM_MAX_FRAMES or total_ms >= ANIM_MAX_MS:
            break
        fr.thumbnail((ANIM_MAX_PX, ANIM_MAX_PX), RESAMPLE)
        frames.append(fr)
        durations.append(max(10, min(int(dur), 500)))
        total_ms += durations[-1]
    if not frames:
        # (C4-a) هیچ فریمِ سالمی دیکد نشد — خطای خواندنِ روشن، نه خطای خام
        # (که UI آن را «ذخیره ممکن نشد» نشان می‌داد) و نه بی‌صدا
        raise CoverReadError(T(READ_ERR_TEXT))
    # فریمِ ۰ ساکن هم ذخیره می‌شود (شبکه‌ی کارت‌ها و hero از همین می‌خوانند)
    # made_static = فقط فایلی که *این تلاش* ساخته — staticِ از-قبل-موجودِ
    # همان GIF (شاید متای قبلی/عنوانِ دیگر به آن ارجاع دارد) در صورتِ شکستِ
    # مرحله‌ی بعدی نباید پاک شود (ممیزیِ round 17 §۱)
    made_static = False
    if not _looks_like_webp(static_path):
        st0 = frames[0].copy()
        st0.thumbnail((STATIC_MAX_PX, STATIC_MAX_PX), RESAMPLE)
        try:
            _atomic_save(st0, static_path, quality=STATIC_QUALITY)
            made_static = True
        except CoverError:
            raise
        except OSError as e:
            dbg_log(f"import_cover save failed: {_err_note(e)}")
            raise CoverWriteError(T(WRITE_ERR_TEXT)) from None

    anim_path = dest / f"{digest}.anim.webp"
    # idempotent (round 17 §۱): مقصدِ متحرکِ سالم = بدونِ انکدِ دوباره —
    # دقیقاً سناریویِ «همان GIF که در جزئیات در حالِ پخش است دوباره انتخاب
    # شد»: نوشتنِ دوباره روی فایلِ بازِ QMovie روی ویندوز ممکن نیست.
    if _looks_like_webp(anim_path):
        try:
            size = anim_path.stat().st_size
        except OSError:
            size = ANIM_MAX_BYTES + 1
        if size <= ANIM_MAX_BYTES:
            anim_rel = f"{COVERS_DIR_NAME}/{anim_path.name}"
            return {"poster": anim_rel, "poster_static": static_rel,
                    "animated": True, "bytes": size}
    t0 = time.monotonic()
    try:
        size = _encode_animated(frames, durations, anim_path, t0=t0)
    except CoverError:
        # کاورِ متحرک رد شد — فقط فایلِ ساکنِ *تازه‌ساخت* یتیم نمی‌ماند
        if made_static:
            try:
                static_path.unlink(missing_ok=True)
            except OSError as _exc_dbg:
                dbg_log(f"import_cover cleanup failed: {type(_exc_dbg).__name__}")
        raise
    if size is None:
        # (C4-b) هیچ پیکربندیِ متحرکی زیرِ سقف نرفت — به‌جای خطای بی‌راه‌حل،
        # فریمِ اول (که همین حالا ساکن ذخیره شده) کاورِ نهایی می‌شود و
        # کلیدِ degraded به UI می‌گوید پیامِ «انیمیشن حذف شد» نشان دهد
        try:
            anim_path.unlink(missing_ok=True)
        except OSError as _exc_dbg:
            dbg_log(f"import_cover cleanup failed: {type(_exc_dbg).__name__}")
        dbg_log("import_cover: animated cover degraded to static frame")
        try:
            st_size = static_path.stat().st_size
        except OSError:
            st_size = 0
        return {"poster": static_rel, "poster_static": None,
                "animated": False, "bytes": st_size, "degraded": True}
    anim_rel = f"{COVERS_DIR_NAME}/{anim_path.name}"
    return {"poster": anim_rel, "poster_static": static_rel,
            "animated": True, "bytes": size}


def collect_garbage(referenced: set[str], app_dir_path=None, store=None) -> int:
    """فایل‌های ‎.covers/‎ که هیچ متایی به آن‌ها ارجاع نمی‌دهد را پاک کن.

    کاورِ عوض‌شده و کاورِ عنوانِ حذف‌شده قبلاً تا ابد می‌ماندند (پشتیبان هم
    پف می‌کرد). idempotent است — بعد از هر اسکنِ کامل با مجموعه‌ی مقادیرِ
    poster/poster_static همه‌ی آیتم‌ها صدا زده می‌شود.
    بازمی‌گرداند: تعدادِ فایلِ حذف‌شده.
    (round 19 §۳) اگر store داده شود، پیش از هر unlink مجموعه‌ی ارجاع‌ها
    *زیرِ قفلِ Store* دوباره خوانده می‌شود — GC با snapshotِ کهنه (از لحظه‌ی
    شروعِ نخ) دیگر هرگز کاوری را که همین حالا به متا وصل شده پاک
    نمی‌کند.
    (round 20 §۳) دو گاردِ ضدِ پاک‌شدنِ جمعی:
    ۱) دیتابیسِ قرنطینه‌شده — Store تازه و خالی است و بدونِ گارد، *همه‌ی*
       کاورهای سالمِ قابل‌بازیابی پاک می‌شدند؛ روی DB قرنطینه‌شده GC اصلاً
       اجرا نمی‌شود.
    ۲) DBِ بدونِ آیتم اما ‎.covers/‎ پُر — همان سناریو یک اجرا بعدتر (پس از
       بازشدنِ دوباره‌ی برنامه، وقتی پرچمِ قرنطینه ریست شده) و همچنین
       کتابخانه‌ی واقعاً خالی. در این حالت هم حذفِ جمعی جایز نیست؛ یتیم‌های
       احتمالی بی‌زیان می‌مانند (بهایِ امنیتِ داده در برابرِ چند صد کیلوبایت
       فایلِ مانده). اگر آیتم‌ها موجودند ولی به هیچ کاوری ارجاع نمی‌دهند،
       GC مثل قبل پاک می‌کند — آن یتیم‌ها واقعاً یتیم‌اند.
    چکِ ارجاع + unlink حالا هر دو زیرِ همان قفلِ Store است (اتمیکِ کامل؛
    قبلاً unlink بیرونِ قفل بود) و اگر refs خوانده نشود فایل حذف *نمی‌شود*
    (fail-safe: فایلِ مانده بی‌زیان است؛ کاورِ پاک‌شده نه)."""
    d = covers_dir(app_dir_path)
    if not d.is_dir():
        return 0
    # گاردِ ۱ — قرنطینه: تا وقتی پرچم است، هیچ حذفی (حتی فایلِ موقت) نه
    if store is not None and bool(getattr(store, "db_quarantined", False)):
        dbg_log("collect_garbage skipped: db is quarantined — covers protected")
        return 0
    refs = {str(v).replace("\\", "/") for v in referenced if v}
    n = 0
    now = time.time()
    try:
        files = [p for p in d.iterdir() if p.is_file()]
    except OSError as _exc_dbg:
        dbg_log(f"collect_garbage iterdir failed: {type(_exc_dbg).__name__}")
        return 0
    for p in files:
        if not p.name.startswith(".tmp-"):
            continue
        # (C6) فایلِ موقتِ نوشتنِ اتمیک — اگر برنامه وسطِ _atomic_save
        # بسته شود (قطع برق/kill) برای همیشه می‌ماند و پشتیبان هم پف
        # می‌کند؛ قدیمی‌تر از یک ساعت = پسماندِ قطعی، پاک می‌شود
        try:
            if now - p.stat().st_mtime < TMP_MAX_AGE_S:
                continue
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage stat failed: {type(_exc_dbg).__name__}")
            continue
        try:
            p.unlink()
            n += 1
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage tmp unlink failed: {type(_exc_dbg).__name__}")
    # گاردِ ۲ — DBِ بدونِ آیتم اما ‎.covers/‎ پُر: حذفِ جمعی ممنوع
    if store is not None:
        try:
            with store._lock:
                fresh0 = store.cover_refs()
                n_items = len(store.data.get("items", {}))
        except Exception as _exc_dbg:
            dbg_log(f"collect_garbage fresh refs failed: {type(_exc_dbg).__name__}")
            fresh0 = None
        if fresh0 is not None and not fresh0 and n_items == \
                0 and any(not p.name.startswith(".tmp-") for p in files):
            dbg_log("collect_garbage skipped: store has no items but .covers "
                    "is not empty (fresh/rebuilt db?) — covers protected")
            return n
    for p in files:
        if p.name.startswith(".tmp-"):
            continue
        if f"{COVERS_DIR_NAME}/{p.name}" in refs:
            continue
        # نگهبانِ مسابقه‌ی زمانی (round 13 §۲): فایلِ *تازه* رها می‌شود —
        # کاربر ممکن است همین حالا کاوری را وارد کرده باشد و متایش هنوز
        # در دیتابیس نوشته نشده باشد؛ پاک‌کردنش یعنی کاورِ تازه‌ساخت گم شود.
        try:
            if now - p.stat().st_mtime < 60:   # تازه‌تر از ۶۰ ثانیه → شاید در حالِ import است
                continue
        except OSError as _exc_dbg:
            dbg_log(f"collect_garbage stat failed: {type(_exc_dbg).__name__}")
            continue
        if store is None:
            try:
                p.unlink()
                n += 1
            except OSError as _exc_dbg:
                dbg_log(f"collect_garbage unlink failed: {type(_exc_dbg).__name__}")
            continue
        # (round 20) چکِ تازه + unlink اتمیک زیرِ قفل — mutatorهای کاور
        # (set_custom_poster/clear_custom_poster) همان قفل را می‌گیرند؛
        # بینِ «به متا وصل شد» و «پاک شد» دیگر هیچ پنجره‌ای نیست
        try:
            with store._lock:
                fresh = store.cover_refs()
                if f"{COVERS_DIR_NAME}/{p.name}" in fresh:
                    continue
                try:
                    p.unlink()
                    n += 1
                except OSError as _exc_dbg:
                    dbg_log(f"collect_garbage unlink failed: {type(_exc_dbg).__name__}")
        except Exception as _exc_dbg:
            # fail-safe: refs خوانده نشد → حذف نکن
            dbg_log(f"collect_garbage fresh refs failed: {type(_exc_dbg).__name__}")
    return n


def delete_cover_files(meta_poster, meta_static, app_dir_path=None) -> None:
    """حذفِ فایل‌های کاورِ ذخیره‌شده — idempotent و فقط داخلِ ‎.covers/‎.

    مقادیرِ متا نسبی‌اند (‎.covers/<name>‎)؛ هر مسیرِ دیگری (مثل مسیرِ مطلقِ
    قدیمیِ فرمِ ویرایش) عمداً دست‌نخورده می‌ماند — برنامه به فایل‌های کاربر
    هیچ‌وقت دست نمی‌زند.
    """
    base = Path(app_dir_path) if app_dir_path is not None else app_dir()
    dest = base / COVERS_DIR_NAME
    for v in (meta_poster, meta_static):
        if not v:
            continue
        v = str(v).replace("\\", "/")
        if not v.startswith(COVERS_DIR_NAME + "/"):
            continue
        try:
            p = (base / v).resolve()
            if dest.resolve() in p.parents and p.is_file():
                p.unlink()
        except OSError as _exc_dbg:
            dbg_log(f"delete_cover_files failed: {type(_exc_dbg).__name__}")
            continue
