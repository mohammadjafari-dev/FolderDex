# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Fetch the real thumbnail the same way Windows Explorer shows it
(Shell Thumbnail).

Windows-only; returns None on any error so the app falls back to the
alternate path (ffmpeg / file-type card).

Modes:
- cached_only mode added (SIIGBF_CACHEDONLY): only Windows' ready cache is
  read and no rendering happens — the same instant speed the user sees in
  Explorer (~a few ms). The caller tries this first, then a full render if
  there is no hit.
- The full render now also passes SIIGBF_THUMBNAILONLY: if no thumbnail
  handler exists it fails instead of returning the file-type icon (which is
  useless to us and even worse to cache!), so the ffmpeg fallback can kick in.
- GdiplusStartup is no longer repeated per call (once per process), and
  statistics counters (cache/render/miss) feed the prewarm summary —
  exactly how many thumbnails came from which path.
"""
from __future__ import annotations

import sys
import threading
from watchtime import dbg_log

# Statistics counters — the pre-build queue reports them in its final summary
_stats = {"cache_hit": 0, "render_hit": 0, "miss": 0, "error": 0}


def shell_thumbnail_stats() -> dict:
    return dict(_stats)


def reset_thumbnail_stats():
    for k in _stats:
        _stats[k] = 0


# The GDI+ token is created once per process; recreating/destroying it
# repeatedly (~tens of ms each) became its own bottleneck in a
# multi-thousand-file pre-build
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
        # Win64 safety: GdiplusStartup's first parameter is ULONG_PTR* — a
        # pointer-sized slot. A c_ulong (4 bytes on Win64) let the API write
        # 8 bytes into a 4-byte buffer (stack corruption). c_size_t matches
        # the pointer width on both 32- and 64-bit Windows.
        gdiplus.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                                           ctypes.c_void_p, ctypes.c_void_p]
        gdiplus.GdiplusStartup.restype = ctypes.c_int   # GpStatus (Status enum)
        token = ctypes.c_size_t()
        if gdiplus.GdiplusStartup(ctypes.byref(token),
                                  ctypes.byref(GdiplusStartupInput(1, None, 0, 0)),
                                  None) == 0:
            _gdip_token = token.value
        return _gdip_token


def get_shell_thumbnail(path, size: int = 256, cached_only: bool = False) -> bytes | None:
    """PNG as bytes, or None.

    cached_only=True → only Windows' existing cache (SIIGBF_CACHEDONLY) with
    no rendering at all — the fastest possible path (what makes Explorer feel
    instant). A miss here is normal.
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

    # SIIGBF flags: CACHEDONLY → read the cache only, no render;
    # THUMBNAILONLY → never a file-type icon; BIGGERSIZEOK → allow upscaling
    # a smaller thumbnail
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
    co_initialized = hr in (0, 1)          # S_OK / S_FALSE — a *successful* init
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
        # Balance CoUninitialize ONLY against a successful CoInitialize:
        # RPC_E_CHANGED_MODE means this call initialized nothing and the
        # thread already ran under another COM model — a paired
        # CoUninitialize would then tear down THAT state (MSDN: every
        # successful CoInitialize/CoInitializeEx must be balanced by
        # CoUninitialize; failed calls must not be).
        if co_initialized:
            ole32.CoUninitialize()


def _hbitmap_to_png(gdiplus, ole32, kernel32, hbm, GUID, CLSID_PNG,
                    POINTER, byref, c_void_p, cast, ctypes, release) -> bytes | None:
    # Win64 signatures: without restype, ctypes assumed c_int —
    # GlobalLock's 64-bit pointer was truncated to 32 bits and string_at()
    # then read from a wrong (or zero) address. Explicit pointer-sized types
    # + a NULL/zero check make this path safe on x64.
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_int     # BOOL
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t    # SIZE_T

    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", c_void_p),
                    ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]

    # GDI+ startup happens once per process (previously it was created and
    # torn down for every file)
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
        if not ptr or not size:
            return None                              # lock failed — never string_at(NULL)
        try:
            return ctypes.string_at(ptr, size)
        finally:
            kernel32.GlobalUnlock(hglob)
    finally:
        if bmp:
            gdiplus.GdipDisposeImage(bmp)
        release(stream)
        # GdiplusShutdown is deliberately not called — the token lives for the
        # whole process lifetime
