# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for imgcache.py — the WebP-only thumbnail cache.

The cache format is a *product decision* (see requirements.txt): WebP writes
faster and decodes natively in Qt at thumbnail sizes, while JPEG XL needed a
native plugin + six DLLs and only ever beat PNG. These tests pin that decision
so nobody "optimizes" it back silently.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PIL import Image

import imgcache  # noqa: E402
from imgcache import cache_file, read_cache_pil, save_cache_image  # noqa: E402


def _rgba(w: int = 64, h: int = 64) -> Image.Image:
    return Image.new("RGBA", (w, h), (10, 20, 30, 255))


# ------------------------------------------------------------ فرمتِ کش
def test_cache_ext_is_webp():
    assert imgcache.WEBP_OK is True
    assert imgcache.CACHE_EXT == ".webp"


def test_no_jxl_imports():
    # no JPEG XL plugin module may be imported by the app chain (the codec is
    # gone on purpose — see the note in requirements.txt)
    assert not [m for m in sys.modules if "jxl" in m.lower()]


# ------------------------------------------------------------ نوشتن/خواندن
def test_save_and_read_roundtrip(tmp_path: Path):
    out = cache_file(tmp_path, "roundtrip")
    ret = save_cache_image(_rgba(), out)
    assert ret == out and out.suffix == ".webp" and out.is_file()
    im = read_cache_pil(out)
    assert im is not None and im.size == (64, 64)


def test_legacy_png_is_still_read(tmp_path: Path):
    stem = "legacy"
    (tmp_path / f"{stem}.png").write_bytes(b"not-a-real-png-but-present")
    # مسیرِ کهنه‌ی .png برمی‌گردد (خواندنِ آن بعداً None می‌دهد و بازسازی می‌شود — B1)
    assert cache_file(tmp_path, stem) == tmp_path / f"{stem}.png"


def test_stale_jxl_is_ignored(tmp_path: Path):
    stem = "stale"
    (tmp_path / f"{stem}.jxl").write_bytes(b"orphaned-jxl-payload")
    # کشِ .jxl مثل فایلِ ناموجود رفتار می‌شود تا خودکار به WebP بازسازی شود
    assert cache_file(tmp_path, stem) == tmp_path / f"{stem}.webp"


# ------------------------------------------------------------ اتمیک بودنِ نوشتن
def test_atomic_write_survives_parallel_saves(tmp_path: Path):
    out = cache_file(tmp_path, "atomic")
    errs: list[Exception] = []

    def _worker():
        try:
            for _ in range(8):
                save_cache_image(_rgba(), out)
        except Exception as exc:  # noqa: BLE001 — خودِ آزمون است
            errs.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errs == []
    im = read_cache_pil(out)
    assert im is not None and im.size == (64, 64)


def test_png_fallback_path_when_forced(tmp_path: Path):
    """save روی مسیرِ .png هم باید کار کند (قالب از پسوند پیروی می‌کند)."""
    out = tmp_path / "aspng.png"
    ret = save_cache_image(_rgba(), out)
    assert ret == out and out.is_file()
    assert read_cache_pil(out) is not None


# ------------------------------------------------------------ تغییرِ نام‌گذاری
def test_module_has_no_jxl_symbols():
    for name in dir(imgcache):
        assert "jxl" not in name.lower()


# ------------------------------------------------------------ D9 — دیکدِ دوباره در مسیرِ داغ
def test_corrupt_cache_is_rebuilt(tmp_path: Path):
    """فایلِ کشِ خراب (امضای ناشناس) decodable نیست → poster_png_fallback بازسازی می‌کند."""
    from filethumbs import _cache_decodable
    bad = tmp_path / "corrupt.webp"
    bad.write_bytes(b"JXLJXL-not-a-real-cache-file\x00\x01")
    assert _cache_decodable(bad) is False


def test_valid_webp_and_png_magic_pass(tmp_path: Path):
    """مسیرِ سریعِ D9: WebP/PNG سالم بدون دیکدِ کامل قبول می‌شوند."""
    from filethumbs import _cache_decodable
    webp = tmp_path / "ok.webp"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert _cache_decodable(webp) is True
    png = tmp_path / "ok.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"rest")
    assert _cache_decodable(png) is True


def test_riff_non_webp_rejected(tmp_path: Path):
    """RIFFِ غیرWebP (مثلاً WAV) قبول نمی‌شود — کشِ ما فقط تصویر است."""
    from filethumbs import _cache_decodable
    wav = tmp_path / "sound.webp"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert _cache_decodable(wav) is False


def test_real_webp_cache_file_decodes(tmp_path: Path):
    """فایلِ کشِ واقعیِ برنامه (save_cache_image) از مسیرِ سریع می‌گذرد."""
    from filethumbs import _cache_decodable
    out = cache_file(tmp_path, "real")
    save_cache_image(_rgba(), out)
    assert out.is_file()
    assert _cache_decodable(out) is True


# ------------------------------------------------------------ D10 — سقفِ پیش‌فرضِ کش
def test_cache_max_mb_default_is_capped(tmp_path: Path):
    """پیش‌فرض دیگر «بی‌سقف» نیست — ۲ گیگ LRU (تغییرِ مقیاس کش را چند برابر می‌کرد)."""
    from store import Store
    s = Store(tmp_path / "config.json")
    assert s.cache_max_mb() == 2048
    s.close()


def test_explicit_unlimited_is_preserved(tmp_path: Path):
    """کاربری که صریحاً 0 گذاشته (نامحدود) حفظ می‌شود."""
    from store import Store
    cfg = tmp_path / "config.json"
    cfg.write_text('{"cache_max_mb": 0}', encoding="utf-8")
    s = Store(cfg)
    assert s.cache_max_mb() == 0
    s.close()


def test_prune_scale_generation_only_on_change(tmp_path: Path):
    """نسلِ قدیمی فقط وقتی مقیاس عوض شده پاک می‌شود؛ بوتِ همان مقیاس چیزی نمی‌کشد."""
    from thumbs import prune_scale_generation
    d = tmp_path / ".thumb_cache"
    d.mkdir()
    old = d / "old.webp"
    new = d / "new.webp"
    old.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    new.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    import os as _os
    old_date = 0            # خیلی قدیمی (۱۹۷۰)
    _os.utime(old, (old_date, old_date))
    future = 2_000_000_000  # خیلی جدید
    _os.utime(new, (future, future))
    # بوتِ اول — فقط نشانگر می‌نویسد، پاک نمی‌کند
    assert prune_scale_generation(d, 256) == (0, 0)
    assert old.is_file() and new.is_file()
    # بوت با همان مقیاس — پاک نمی‌کند
    assert prune_scale_generation(d, 256) == (0, 0)
    assert old.is_file()
    # تغییرِ مقیاس → فایلِ قدیمیِ (>۷ روز) می‌رود، جدید می‌ماند
    removed = prune_scale_generation(d, 512)
    assert removed[1] == 1
    assert not old.exists() and new.is_file()
    # بوتِ بعدی با همان مقیاس جدید — دیگر پاک‌سازیِ بیش ندارد
    (d / "again.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    _os.utime(d / "again.webp", (old_date, old_date))
    assert prune_scale_generation(d, 512) == (0, 0)
    assert (d / "again.webp").exists()
