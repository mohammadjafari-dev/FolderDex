# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""موازی‌سازی خودکار بر اساس مشخصات سخت‌افزاری (نسخه‌ی 0.34.0).

درخواست کاربر: «نکته مهم: برنامه باید موازی‌سازی داشته باشه بر اساس مشخصات
سخت‌افزاری که داره». تا این نسخه فقط تعداد هسته‌ی CPU لحاظ می‌شد؛ حالا حجم رم
هم در انتخاب تعداد نخ‌ها می‌آید (روی دستگاه‌های رم‌کم، انباشتِ فرآیندهای
هم‌زمانِ ffmpeg/7z بدل به گرفتگی کلی سیستم می‌شد) و دو متغیر محیطی
MC_VIDEO_WORKERS و MC_ARCHIVE_WORKERS چیدمانِ دستی را برای عیب‌یابی باز
می‌گذارند.

نسخه‌ی 0.38.0 (گزارش کاربر + راهنما: «خواندنِ موازیِ ~۲۸‌تایی روی دیسک چرخان هد
را به‌سا می‌کند و کندترش می‌کند»): نوعِ دیسکِ میزبانِ فایل‌ها هم خودکار
تشخیص داده می‌شود — روی هاردِ چرخان (HDD) ورکرهای سنگین محافظه‌کار می‌شوند
(ویدیو حدود ۸، آرشیو حدود ۴) و روی SSD/NVMe همان سقف‌های سریعِ قبلی می‌مانند.
تشخیص روی ویندوز با IOCTL_STORAGE_QUERY_PROPERTY (IncursSeekPenalty) بدون
نیاز به ادمین و روی لینوکس با /sys/block/<disk>/queue/rotational انجام
می‌شود؛ نامشخص = رفتارِ سریعِ قبلی (هیچ‌کدام hardcode نیست — هر ماشینی خودش
سنجیده می‌شود).
"""
from __future__ import annotations

from i18n import T

import os
from watchtime import dbg_log

_RAM_GB: float | None = None


def cpu_cores() -> int:
    return max(2, os.cpu_count() or 4)


def ram_gb() -> float:
    """حجم رم نصب‌شده به گیگابایت — فقط stdlib بدون پکیج (ctypes/…proc)."""
    global _RAM_GB
    if _RAM_GB is not None:
        return _RAM_GB
    ram = 8.0                                   # پیش‌فرض میانه‌رو در نبود تشخیص
    try:
        if os.name == "nt":
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTAT()
            st.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                ram = st.ullTotalPhys / (1024 ** 3)
        else:
            with open("/proc/meminfo", encoding="ascii",
                      errors="replace") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        ram = int(line.split()[1]) / (1024 ** 2)
                        break
    except Exception as _exc_dbg:
        dbg_log(f"ram_gb failed: {type(_exc_dbg).__name__}")
        pass
    _RAM_GB = max(1.0, ram)
    return _RAM_GB


def _env_int(name: str) -> int | None:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- نوع دیسک
_SEEK_CACHE: dict = {}

# ----------------------------------------------------------------- حالت عملکرد
# درخواست کاربر در بازبینی تنظیمات: کنترل دستی روی «حافظه‌کاری/تهاجمی‌بودنِ»
# شمار کارگرها — تا اگر تشخیص خودکار دیسک روی سیستمی اشتباه/ناپایدار بود (همان
# معمای «یک اجرا سریع، یک اجرا کند»)، کاربر بتواند ثابتش کند.
_PERF_MODE = "auto"          # auto | high | safe
_PERF_OK = ("auto", "high", "safe")


def set_perf_mode(mode: str) -> None:
    """بازتعریفِ کاربر: high → همیشه پرسرعت، safe → همیشه محافظه‌کار، auto → تشخیص."""
    global _PERF_MODE
    _PERF_MODE = mode if mode in _PERF_OK else "auto"


def perf_mode() -> str:
    return _PERF_MODE


def _resolve_rotational(detected):
    """اعمال حالت عملکرد روی نتیجه‌ی تشخیص خودکار (None می‌ماند None)."""
    if _PERF_MODE == "high":
        return False
    if _PERF_MODE == "safe":
        return True
    return detected


def load_kinds(d) -> None:
    """تزریق نتایجِ ذخیره‌شده‌ی تشخیص دیسک از config در بوت (0.45.0).

    هدف: ثبات بین اجراها — پرس‌وجوی سطح‌پایینِ درایو (IOCTL) گاه بسته به حافظه‌ی
    کش‌ِ درایور/وضعیت USB جواب متفاوت می‌دهد و همین باعث دو‌ضرب شدنِ شمارِ
    کارگرها بین دو اجرا می‌شود؛ اولین نتیجه‌ی موفق پایه‌ی همیشگی است."""
    try:
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(k, str) and isinstance(v, bool) and k not in _SEEK_CACHE:
                    _SEEK_CACHE[k] = v
    except Exception as _exc_dbg:
        dbg_log(f"load_kinds failed: {type(_exc_dbg).__name__}")
        pass


def persisted_kinds() -> dict:
    """نتایجِ در-دسترس برای ذخیره در config (فقط پاسخ‌های قطعی True/False)."""
    return {k: v for k, v in _SEEK_CACHE.items() if isinstance(v, bool)}


def _win_seek_penalty(drv: str) -> "bool | None":
    """پرس‌وجوی IncursSeekPenalty از ویندوز — True = چرخان (HDD)， False = SSD.

    IOCTL_STORAGE_QUERY_PROPERTY با StorageDeviceSeekPenaltyProperty؛ هندل با
    دسترسیِ صفر باز می‌شود پس ادمین لازم نیست. هر خطا/درایوِ گزارش‌نده → None.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW(
        "\\\\.\\" + drv, 0, 7, None, 3, 0, None)   # access=0، share=R/W/DEL، OPEN_EXISTING
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        query = (ctypes.c_ulong * 3)(7, 0, 0)      # PropertyId=7 (SeekPenalty)، StandardQuery
        out = ctypes.create_string_buffer(16)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(
            h, 0x2D1400, query, ctypes.sizeof(query), out, 16,
            ctypes.byref(ret), None)               # IOCTL_STORAGE_QUERY_PROPERTY
        if not ok or ret.value < 9:
            return None
        return bool(out.raw[8])                    # DEVICE_SEEK_PENALTY_DESCRIPTOR.IncursSeekPenalty
    except Exception as _exc_dbg:
        dbg_log(f"_win_seek_penalty failed: {type(_exc_dbg).__name__}")
        return None
    finally:
        k32.CloseHandle(h)


def _posix_seek_penalty(path: str) -> "bool | None":
    """روی لینوکس/مک: /sys/block/<disk>/queue/rotational از روی دیوایسِ میزبان."""
    try:
        import re
        import sys
        p = str(path)
        while p and not os.path.exists(p):
            nxt = os.path.dirname(p)
            if nxt == p:
                return None
            p = nxt
        if not p:
            return None
        dev = os.stat(p).st_dev
        if sys.platform == "darwin":                # مک: دیسک‌های مدرن SSDاند؛ نامشخص
            return None
        lnk = f"/dev/block/{os.major(dev)}:{os.minor(dev)}"
        name = os.path.basename(os.path.realpath(lnk)) or None
        if not name:
            return None
        base = name
        if not os.path.exists(f"/sys/block/{base}"):
            m = re.match(r"^(nvme\d+n\d+|mmcblk\d+|[a-z]+?)", name)
            # پارتیشن‌ها: sda1→sda، nvme0n1p2→nvme0n1، mmcblk0p1→mmcblk0
            base = re.sub(r"(p\d+|\d+)$", "", name)
            if not os.path.exists(f"/sys/block/{base}") and m:
                base = m.group(1)
        try:
            with open(f"/sys/block/{base}/queue/rotational",
                      encoding="ascii", errors="replace") as fh:
                return fh.read().strip() == "1"
        except OSError:
            return None
    except Exception as _exc_dbg:
        dbg_log(f"_posix_seek_penalty failed: {type(_exc_dbg).__name__}")
        return None


def seek_penalty(path) -> "bool | None":
    """True = دیسکِ چرخان، False = SSD/NVMe، None = نامشخص — کشِ در-حافظه‌ای بر
    اساس درایو/دیوایس تا هزاران مسیر فقط یک‌بار سنجیده شوند."""
    try:
        if os.name == "nt":
            drv = os.path.splitdrive(os.path.abspath(str(path)))[0].rstrip("\\/")
            if len(drv) != 2 or not drv.endswith(":"):
                return None                          # مسیر UNC/عجیب → نامشخص
            key = "w:" + drv.lower()
            if key not in _SEEK_CACHE:
                _SEEK_CACHE[key] = _win_seek_penalty(drv)
        else:
            import sys
            p = str(path)
            while p and not os.path.exists(p):
                nxt = os.path.dirname(p)
                if nxt == p:
                    return None
                p = nxt
            if not p:
                return None
            dev = os.stat(p).st_dev
            if sys.platform == "darwin":
                return None
            key = "p:%d:%d" % (os.major(dev), os.minor(dev))
            if key not in _SEEK_CACHE:
                _SEEK_CACHE[key] = _posix_seek_penalty(p)
        return _SEEK_CACHE.get(key)
    except Exception as _exc_dbg:
        dbg_log(f"seek_penalty failed: {type(_exc_dbg).__name__}")
        return None


def any_rotational(paths) -> bool:
    """True اگر حداقل یکی از مسیرها روی دیسکِ چرخان باشد؛ ناشناخته‌ها رأی ندارند."""
    for p in paths:
        if seek_penalty(p) is True:
            return True
    return False


def video_workers(rotational: "bool | None" = None) -> int:
    """تعداد نخ‌های فاز تامبنیل ویدیو.

    هر فرآیند ffmpeg با «-threads 1» فقط یک هسته می‌گیرد و زمان واقعی‌اش صرفِ
    انتظارِ I/O درایو (مخصوصاً خارجی) است — پس روی SSD دو-سه برابرِ هسته بهترین
    نقطه است (سقف ۲۸: بیشتر از آن روی USB سودی نیست و فقط حافظه می‌خورد).
    0.38.0 (گزارش کاربر + راهنما): روی دیسکِ چرخان، ده‌ها خواننده‌ی موازی هد را
    به‌سا می‌کند → حالتِ محافظه‌کار (۴ تا ۸). رم < ۶ گیگ: سقف پایین‌تر هم
    می‌آید تا سیستم بی‌رم خفه نشود.
    """
    rotational = _resolve_rotational(rotational)  # حالت عملکرد کاربر
    if rotational:
        n = max(4, min(8, cpu_cores()))
        if ram_gb() < 6:
            n = min(n, 6)
    else:
        n = max(12, min(28, cpu_cores() * 3))
        if ram_gb() < 6:
            n = min(n, 16)
    return _env_int("MC_VIDEO_WORKERS") or n


def archive_workers(rotational: "bool | None" = None) -> int:
    """تعداد نخ‌های فاز آرشیو (فرآیند 7z سبکِ پردازه‌ای و سنگینِ دیسکی است).

    0.38.0: روی دیسکِ چرخان محافظه‌کار (۲ تا ۴) — استخراجِ موازیِ زیاد روی HDD
    فقط هد را جابه‌جا می‌کند؛ روی SSD/NVMe همان محدوده‌ی قبلی (۶ تا ۱۴)."""
    rotational = _resolve_rotational(rotational)  # حالت عملکرد کاربر
    if rotational:
        n = max(2, min(4, cpu_cores() // 2))
        if ram_gb() < 6:
            n = min(n, 3)
    else:
        n = max(6, min(14, cpu_cores() * 2))
        if ram_gb() < 6:
            n = min(n, 8)
    return _env_int("MC_ARCHIVE_WORKERS") or n


def poster_workers(rotational: "bool | None" = None) -> int:
    """تعداد نخ‌های فاز پوستر پوشه‌ها (نسخه‌ی 0.40.0 — مأموریت سرعتِ کاربر).

    پوسترِ هر پوشه با همان زنجیره‌ی کامل ساخته می‌شود (آیکون ← تصویر ← ویدیو
    ← PDF ← فشرده): برای پوشه‌های دارای آیکون کار ارزان است ولی برای بقیه
    همان استخراجِ فریمِ ffmpeg اجرا می‌شود — کارِ I/O/subprocess محور، درست مثل
    فاز ویدیوها. تا 0.39.0 این فاز روی سقفِ ثابتِ ۳ نخ قفل بود (با ۱۰ هسته!) —
    لاگ کاربر: فاز پوستر بخشِ بزرگی از زمانِ کل بود. حالا:
    SSD/NVMe → تا ۲۰ نخ؛ دیسکِ چرخان → محافظه‌کار (۳ تا ۶).
    رم < ۶ گیگ سقف را پایین می‌آورد؛ MC_POSTER_WORKERS بازتعریفِ دستی."""
    rotational = _resolve_rotational(rotational)  # حالت عملکرد کاربر
    if rotational:
        n = max(3, min(6, cpu_cores() // 2 + 1))
        if ram_gb() < 6:
            n = min(n, 4)
    else:
        n = max(10, min(20, cpu_cores() * 2))
        if ram_gb() < 6:
            n = min(n, 12)
    return _env_int("MC_POSTER_WORKERS") or n


def list_workers(rotational: "bool | None" = None) -> int:
    """تعداد نخ‌های فهرست‌سازیِ داخل پوشه‌ها (نسخه‌ی 0.40.0 — مأموریت سرعت).

    فهرست‌سازی (خواندنِ متادیتای درایو) سبک‌تر از رندر است ولی روی درایو
    خارجی/شبکه هم زمان‌بر می‌شود؛ تا 0.39.0 روی ۴ نخِ ثابت بود. SSD/NVMe → تا
    ۱۲ نخ؛ چرخان → ۴ (رفتار قبلی). MC_LIST_WORKERS بازتعریفِ دستی."""
    rotational = _resolve_rotational(rotational)  # حالت عملکرد کاربر
    if rotational:
        n = 4
    else:
        n = max(6, min(12, cpu_cores()))
        if ram_gb() < 6:
            n = min(n, 8)
    return _env_int("MC_LIST_WORKERS") or n


def hw_summary() -> str:
    """یک‌خط خلاصه برای لاگ/وضعیت: «۱۲ هسته · ۱۶ گیگ رم»."""
    return T("{0} هسته · {1:.0f} گیگ رم").format(cpu_cores(), ram_gb())
