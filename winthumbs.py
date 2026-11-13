# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""گرفتن بندانگشتی واقعیِ همان که اکسپلورر ویندوز نشان می‌دهد (Shell Thumbnail).

فقط روی ویندوز کار می‌کند؛ در هر خطایی None برمی‌گرداند تا برنامه به حالت
جایگزین (ffmpeg / کارت نوع‌فایل) برگردد.

نسخه‌ی 0.25.0 (گزارش کاربر: «برای ویدیو بلافاصله تامبنیل بساز مثل خودِ اکسپلورر»):
- حالت cached_only اضافه شد (SIIGBF_CACHEDONLY): فقط کشِ آماده‌ی ویندوز خوانده
  می‌شود و هیچ رندری انجام نمی‌شود — همان سرعتی که کاربر در اکسپلورر دیده
  (~چند میلی‌ثانیه). فراخواننده اول این را می‌زند، بعد اگر نبود رندر کامل.
- رندرِ کامل حالا SIIGBF_THUMBNAILONLY هم دارد: اگر هندلر تامبنیلی نبود، به‌جای
  آیکنِ نوع‌فایل (که هیچ فایده‌ای برای ما ندارد و حتی بدتری است که کش شود!)
  شکست می‌خورد تا فال‌بک ffmpeg امتحان شود.
- GdiplusStartup دیگر برای هر فراخوان تکرار نمی‌شود (یک‌بار در پروسه) و
  شمارنده‌های آماری (cache/render/miss) برای گزارش به کاربر نگه داشته می‌شوند —
  کاربر دوست دارد بداند دقیقاً چند تا کدام مسیر طول کشید.
"""
from __future__ import annotations

import sys
import threading
from watchtime import dbg_log

# شمارنده‌های آماری — صفِ پیش‌ساخت در خلاصه‌ی پایانی گزارششان می‌کند (0.25.0)
_stats = {"cache_hit": 0, "render_hit": 0, "miss": 0, "error": 0}


def shell_thumbnail_stats() -> dict:
    return dict(_stats)


def reset_thumbnail_stats():
    for k in _stats:
        _stats[k] = 0


# توکن GDI+ یک‌بار در کل پروسه ساخته می‌شود؛ ساخت/تخریبِ مکررش (~چندده‌میلی‌ثانیه
# برای هر فایل) در پیش‌ساخت چندهزارفایلی خودش تبدیل به گلوگاه می‌شد (0.25.0)
_gdip_token = None
_gdip_lock = threading.Lock()


def _gdip_start(gdiplus, GdiplusStartupInput):
    global _gdip_token
    if _gdip_token:
        return _gdip_token
    with _gdip_lock:
        if _gdip_token:
            return _gdip_token
        import ctypes
        token = ctypes.c_ulong()
        if gdiplus.GdiplusStartup(ctypes.byref(token),
                                  ctypes.byref(GdiplusStartupInput(1, None, 0, 0)),
                                  None) == 0:
            _gdip_token = token.value
        return _gdip_token


def get_shell_thumbnail(path, size: int = 256, cached_only: bool = False) -> bytes | None:
    """PNG به‌صورت بایت، یا None.

    cached_only=True → فقط کشِ موجود ویندوز (SIIGBF_CACHEDONLY) بدون هیچ رندری —
    سریع‌ترین مسیر ممکن (همان چیزی که اکسپلورر را «آنی» می‌کند). miss طبیعی است.
    """
    if sys.platform != "win32":
        return None
    try:
        out = _get_thumbnail(str(path), int(size), bool(cached_only))
        if out:
            _stats["cache_hit" if cached_only else "render_hit"] += 1
        else:
            _stats["miss"] += 1
        return out
    except Exception as _exc_dbg:
        dbg_log(f"get_shell_thumbnail failed: {type(_exc_dbg).__name__}")
        _stats["error"] += 1
        return None


def _get_thumbnail(path: str, size: int, cached_only: bool) -> bytes | None:
    import ctypes
    from ctypes import POINTER, Structure, byref, c_ubyte, c_uint, c_ulong, c_ushort, c_void_p, cast

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    gdiplus = ctypes.windll.gdiplus
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    LONG = ctypes.c_long

    class SIZE(Structure):
        _fields_ = [("cx", LONG), ("cy", LONG)]

    class GUID(Structure):
        _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                    ("Data3", c_ushort), ("Data4", c_ubyte * 8)]

    def guid(text: str) -> GUID:
        h = text.replace("-", "")
        return GUID(int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                    (c_ubyte * 8)(*[int(h[i:i + 2], 16) for i in range(16, 32, 2)]))

    IID_SHELL_ITEM = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
    IID_SHELL_IMG_FACTORY = guid("bcc18b79-ba16-442f-80c4-8a59c30c463b")
    CLSID_PNG = guid("557cf406-1a04-11d3-9a73-0000f81ef32e")

    def release(obj):
        if obj:
            Rel = ctypes.WINFUNCTYPE(c_ulong, c_void_p)(
                cast(obj, POINTER(POINTER(c_void_p))).contents[2])
            Rel(obj)

    # پرچم‌های SIIGBF (0.25.0): CACHEDONLY → فقط خواندنِ کش بدون رندر؛ THUMBNAILONLY
    # → بدون آیکنِ نوع‌فایل؛ BIGGERSIZEOK → اجازه‌ی بزرگ‌نمایی تامبنیل کوچک‌تر
    SIIGBF_BIGGERSIZEOK = 0x01
    SIIGBF_THUMBNAILONLY = 0x08
    SIIGBF_CACHEDONLY = 0x10
    flags = SIIGBF_BIGGERSIZEOK | SIIGBF_THUMBNAILONLY
    if cached_only:
        flags |= SIIGBF_CACHEDONLY

    psi = c_void_p()
    pif = c_void_p()
    hbm = ctypes.c_void_p()
    hr = ole32.CoInitialize(None)
    try:
        hr = shell32.SHCreateItemFromParsingName(path, None, byref(IID_SHELL_ITEM), byref(psi))
        if hr != 0 or not psi:
            return None
        vtbl = cast(psi, POINTER(POINTER(c_void_p))).contents
        QI = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))(vtbl[0])
        if QI(psi, byref(IID_SHELL_IMG_FACTORY), byref(pif)) != 0 or not pif:
            return None
        vtbl2 = cast(pif, POINTER(POINTER(c_void_p))).contents
        GetImage = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, SIZE, c_uint, POINTER(c_void_p))(vtbl2[3])
        if GetImage(pif, SIZE(size, size), flags, byref(hbm)) != 0 or not hbm:
            return None
        try:
            return _hbitmap_to_png(gdiplus, ole32, kernel32, hbm, GUID, CLSID_PNG,
                                   POINTER, byref, c_void_p, cast, ctypes, release)
        finally:
            gdi32.DeleteObject(hbm)
    finally:
        release(pif)
        release(psi)
        ole32.CoUninitialize()


def _hbitmap_to_png(gdiplus, ole32, kernel32, hbm, GUID, CLSID_PNG,
                    POINTER, byref, c_void_p, cast, ctypes, release) -> bytes | None:
    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", c_void_p),
                    ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]

    # استارتاپ GDI+ یک‌بار در پروسه (قبلاً برای هر فایل ساخته/تخریب می‌شد)
    if not _gdip_start(gdiplus, GdiplusStartupInput):
        return None
    bmp = c_void_p()
    stream = c_void_p()
    try:
        if gdiplus.GdipCreateBitmapFromHBITMAP(hbm, None, byref(bmp)) != 0:
            return None
        if ole32.CreateStreamOnHGlobal(None, True, byref(stream)) != 0:
            return None
        if gdiplus.GdipSaveImageToStream(bmp, stream, byref(CLSID_PNG), None) != 0:
            return None
        hglob = c_void_p()
        if ole32.GetHGlobalFromStream(stream, byref(hglob)) != 0:
            return None
        size = kernel32.GlobalSize(hglob)
        ptr = kernel32.GlobalLock(hglob)
        try:
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(hglob)
    finally:
        if bmp:
            gdiplus.GdipDisposeImage(bmp)
        release(stream)
        # GdiplusShutdown عمداً صدا زده نمی‌شود — توکن در کل عمر پروسه زنده است
