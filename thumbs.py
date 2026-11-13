# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""کش بندانگشتی: فایل‌های .ico و عکس‌ها یک‌بار به WebP کوچک تبدیل می‌شوند
و دفعات بعد مستقیم از کش خوانده می‌شوند (اسکن‌های بعدی خیلی سریع می‌شود)."""
from __future__ import annotations

import hashlib
import time
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image

from imgcache import apply_exif_transpose, cache_file, save_cache_image, writable_cache_dir
from watchtime import dbg_log

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow قدیمی
    RESAMPLE = Image.LANCZOS


def clear_cache(cache_dir) -> int:
    """پاک کردن کل کش (پوسترها، تامبنیل فایل‌ها، مدت‌زمان‌ها). برمی‌گرداند: بایت آزادشده."""
    cache_dir = Path(cache_dir)
    freed = 0
    if not cache_dir.exists():
        return 0
    for p in sorted(cache_dir.rglob("*"), reverse=True):
        try:
            if p.is_file():
                freed += p.stat().st_size
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        except OSError:
            pass
    cache_dir.mkdir(parents=True, exist_ok=True)
    return freed


def enforce_cache_size_limit(cache_dir, max_mb) -> tuple[int, int]:
    """محدودیت حجم کش با الگوی LRU: اگر حجم از سقف گذشت، قدیمی‌ترین فایل‌ها (بر اساس
    mtime) پاک می‌شوند تا زیر سقف برگردد. max_mb ≤ 0 یعنی نامحدود (کاری نمی‌کند).

    برمی‌گرداند: (بایت آزادشده، تعداد فایل حذف‌شده). داده‌های کاربر مستثنا هستند:
    durations.db (کش مدت‌زمان) و watch_later (وضعیت ادامه‌ی تماشای mpv) هرگز پاک نمی‌شوند.
    """
    cache_dir = Path(cache_dir)
    if not max_mb or max_mb <= 0 or not cache_dir.exists():
        return (0, 0)
    cap = int(float(max_mb) * 1024 * 1024)   # float: مقدار اعشاری نباید کل کش را بپاکد!
    skip_names = {"durations.db", "durations.db-journal", "durations.db-wal", "durations.db-shm"}
    files = []
    total = 0
    for p in cache_dir.rglob("*"):
        try:
            if not p.is_file():
                continue
            rel = p.relative_to(cache_dir)
            if rel.parts[0] == "watch_later" or p.name in skip_names:
                continue
            st = p.stat()
            total += st.st_size
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    if total <= cap:
        return (0, 0)
    files.sort(key=lambda t: t[0])          # قدیمی‌ترین اول
    # درخواست کاربر: در حالت سقف‌دار اول تامبنیل *فایل‌ها* (زیرپوشه‌ی files/) پاک
    # شود و *پوستر پوشه‌ها* آخر — پوسترها به‌مراتب پرکاربردترند
    def _is_file_thumb(t):
        try:
            return t[2].relative_to(cache_dir).parts[0] == "files"
        except Exception as _exc_dbg:
            dbg_log(f"enforce_cache_size_limit._is_file_thumb failed: {type(_exc_dbg).__name__}")
            return False
    ordered = [t for t in files if _is_file_thumb(t)] + \
              [t for t in files if not _is_file_thumb(t)]
    freed = removed = 0
    for _mt, sz, p in ordered:
        if total <= cap:
            break
        try:
            p.unlink()
            total -= sz
            freed += sz
            removed += 1
        except OSError:
            continue
    return (freed, removed)


def prune_scale_generation(cache_dir, active_px: int,
                           max_age_days: float = 7.0) -> tuple[int, int]:
    """پاک‌سازیِ نسلِ کشِ مقیاسِ نمایشِ قبلی (D10).

    کلیدِ کش شامل cache_px است اما px در *نامِ فایل* نیست — پس نگاشتِ فایل
    به مقیاس ممکن نیست و فقط mtime معیار است. برای اینکه بی‌خطر بماند، فقط
    وقتی اجرا می‌شود که مقیاسِ این بوت با بوتِ قبل فرق کند (نشانگرِ ‎.scale_gen
    داخلِ پوشه‌ی کش): فایل‌های تصویریِ قدیمی‌تر از max_age روز حذف می‌شوند.
    بوت‌های بعدی با همان مقیاس چیزی پاک نمی‌کنند — کشِ فعلیِ قدیمی هم
    دست‌نخورده می‌ماند (بازسازیِ بی‌دلیلِ همه‌ی پوسترها نداریم).
    بازمی‌گرداند: (بایت آزادشده، تعداد فایل حذف‌شده).
    """
    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as _exc_dbg:
        dbg_log(f"prune_scale_generation mkdir failed: {type(_exc_dbg).__name__}")
        return (0, 0)
    marker = cache_dir / ".scale_gen"
    prev: int | None
    try:
        prev = int(marker.read_text(encoding="ascii", errors="ignore").strip() or 0)
    except (OSError, ValueError):
        prev = None
    try:
        marker.write_text(str(int(active_px)), encoding="ascii")
    except OSError as _exc_dbg:
        dbg_log(f"prune_scale_generation marker failed: {type(_exc_dbg).__name__}")
    if prev is None or prev == int(active_px):
        return (0, 0)                 # بوتِ اول یا همان مقیاسِ قبلی — کاری نکن
    cutoff = time.time() - max_age_days * 86400
    freed = removed = 0
    for p in cache_dir.rglob("*"):
        try:
            if not p.is_file() or p.suffix.lower() not in (".webp", ".png"):
                continue
            if p.name.startswith(".tmp-"):
                continue
            st = p.stat()
            if st.st_mtime >= cutoff:
                continue
            p.unlink()
            removed += 1
            freed += st.st_size
        except OSError as _exc_dbg:
            dbg_log(f"prune_scale_generation unlink failed: {type(_exc_dbg).__name__}")
            continue
    return (freed, removed)


def cache_px_for_app(app) -> int:
    """اندازه‌ی کش بر اساس مقیاس نمایش ویندوز — ۱۰۰٪←۲۵۶، ۱۵۰٪←۳۸۴، ۲۰۰٪←۵۱۲.

    کارت ۱۸۶×۲۴۸ نقطه‌ی منطقی است؛ در اسکیل ۲۰۰٪ یعنی ۳۷۲×۴۹۶ پیکسل واقعی و کشِ
    ۲۵۶ کشیده می‌شد (افتِ محسوس). بالاتر از ۵۱۲ نمی‌رویم تا حجم کش بی‌دلیل رشد نکند.
    """
    try:
        scr = app.primaryScreen() if app is not None else None
        dpr = float(scr.devicePixelRatio()) if scr is not None else 1.0
    except Exception as _exc_dbg:
        dbg_log(f"cache_px_for_app failed: {type(_exc_dbg).__name__}")
        dpr = 1.0
    if dpr <= 1.1:
        return 256
    if dpr <= 1.6:
        return 384
    return 512


def cache_size(cache_dir) -> int:
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    total = 0
    for p in cache_dir.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


class ThumbnailCache:
    # سقف کش حافظه‌ای مسیرها (LRU) — کتابخانه‌های خیلی بزرگ RAM را نمی‌بلعند
    MEM_LIMIT = 2000

    def __init__(self, cache_dir, size=(256, 256)):
        # پوشه‌ی برنامه فقط‌خواندنی؟ (Program Files / فلشِ قفل‌شده) — قبلاً
        # PermissionError از main() بیرون می‌زد و کاربر فقط «باز نمی‌شود» می‌دید؛
        # حالا کش به ‎%TEMP%‎ می‌رود و پرچمِ فال‌بک به نوار وضعیت می‌رسد.
        d, ro = writable_cache_dir(cache_dir)
        self.cache_dir = d
        self.read_only_fallback = ro
        self.size = size
        self._memory = OrderedDict()
        self._mlock = threading.Lock()   # کش از دو نخ (اسکن + بارگذار پوستر) خوانده/نوشته می‌شود

    def _cache_path(self, src: Path) -> Path:
        st = src.stat()
        # normcase+abspath به‌جای resolve: resolve() در ویندوز برای لینک‌خوانی به
        # دیسک می‌زند و در کشِ هر فایل (به‌خصوص درایو شبکه/HDD) زمان‌بر بود.
        # نوشتن جدید WebP است؛ کش‌های کهنه‌ی .png همچنان خوانده می‌شوند تا
        # پوسترهای ازپیش‌ساخته از نو ساخته نشوند.
        p = os.path.normcase(os.path.abspath(str(src)))
        # پسوند نسل |v5: چرخشِ EXIF در رندر (round 20 §۲) — کش‌های کهنهٔ
        # نسل‌های قبل (از جمله تامبنیل‌های خوابیدهٔ عکس‌های چرخیده) خودکار
        # بازسازی می‌شوند (بدون کدِ مهاجرت؛ یک بازسازی‌ی یک‌باره).
        raw = f"{p}|{st.st_mtime_ns}|{st.st_size}|{self.size}|v5"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return cache_file(self.cache_dir, key)

    def get(self, src) -> Path | None:
        if not src:
            return None
        src = Path(src)
        key = str(src)
        with self._mlock:
            hit = self._memory.get(key)
            if hit is not None:
                if hit.exists():
                    self._memory.move_to_end(key)          # LRU: تازه‌ترین عقب
                    return hit
                self._memory.pop(key, None)                # فایل کش پاک شده — از نو ساخته شود
        try:
            out = self._cache_path(src)
        except OSError:
            return None
        if not out.exists():
            try:
                with Image.open(src) as im:
                    ico = getattr(im, "ico", None)
                    if ico:  # بزرگ‌ترین سایز داخل فایل ico را بردار
                        best = max(ico.sizes(), key=lambda s: s[0] * s[1])
                        im.size = best
                        im.load()
                    else:
                        # JPEGهای بزرگ: رمزگشایی مستقیم در سایز کوچک‌تر (draft) —
                        # پوسترهای 4K از دیکد کامل + کوچک‌کردن خیلی کندتر بودند
                        try:
                            if (im.format or "") == "JPEG":
                                im.draft("RGB", (self.size[0] * 2, self.size[1] * 2))
                        except Exception as _exc_dbg:
                            dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                            pass
                    im = apply_exif_transpose(im)   # (round 20 §۲) عکسِ چرخیدهٔ EXIF
                    im = im.convert("RGBA")
                    # تصاویر خیلی بزرگ (PNG/JPEG چندهزارپیکسلی): یک ریزش سریعِ
                    # فاکتورصحیح قبل از ریسایز دقیق — چند برابر تندتر، بصری همان
                    _m0 = max(im.size)
                    _t0 = max(self.size)
                    if _m0 > _t0 * 4:
                        try:
                            im = im.reduce(max(1, _m0 // (_t0 * 4)))
                        except Exception as _exc_dbg:
                            dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                            pass
                    im.thumbnail(self.size, RESAMPLE)
                    # WebP با فال‌بک PNG — اتمیک و بدون پروفایل رنگیِ ممکن‌العیبِ
                    # منبع (علت هشدارهای libpng در Qt)
                    out = save_cache_image(im, out)
            except Exception as _exc_dbg:
                dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                return None
        with self._mlock:
            while len(self._memory) >= self.MEM_LIMIT:
                try:
                    self._memory.popitem(last=False)  # حذف قدیمی‌ترین (FIFO)
                except KeyError:
                    break
            self._memory[key] = out
        return out
