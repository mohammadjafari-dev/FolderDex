# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""رگرسیون‌های Round 28 — قفلِ گذرای ویندوز روی جایگزینیِ اتمیکِ فایل‌ها.

ویندوز اجازه‌ی ‎os.replace‎ روی فایلِ *باز* را نمی‌دهد (sharing violation —
winerror 32/33): آنتی‌ویروس/ایندکسر لحظه‌ای فایلِ تازه‌نوشته را اسکن می‌کند و
چند کارگرِ هم‌زمان هم سرِ یک مقصد مسابقه می‌دهند. تا این راند فقط covers.py
تلاشِ مجدد داشت (round 17 §۱) و جایگزینیِ imgcache.py لخت بود؛ نتیجه در CI
(windows-latest, 3.12 — راند ۲۷) یک قرمزِ ناپایدار بود: در طوفانِ کارگرهای
هم‌زمان، جایگزینیِ قفل‌خورده مسیرِ خطا را تا QMessageBox برد و زیرپروسه‌ی
استرسِ C2 در پایان با SystemExit قرمز شد (هر ۵ جابِ راندهای پیش سبز بودند؛
کامیتِ R27 فقط نسخه/مستندات عوض می‌کرد).

این تست‌ها ریسِ واقعی ویندوز را بازسازی نمی‌کنند — بازتولیدِ آن اینجا ممکن
نیست و زمان‌بندیش غیرقطعی است — بلکه با *تزریقِ خطا* همان رفتار را تعیین‌تکلیف
می‌کنند و همه‌جا (لینوکس/ویندوز) یکسان اجرا می‌شوند:
  - قفلِ گذرا (چند PermissionError بعد از موفقیت) باید جذب شود.
  - قفلِ پایدار باید بعد از سقفِ تلاش‌ها بالا بیاید، نه بی‌صدا قورت داده شود.
"""

from __future__ import annotations

import os

import pytest
from PIL import Image

import covers
from imgcache import read_cache_pil, replace_with_retry, save_cache_image


def _tiny() -> Image.Image:
    return Image.new("RGB", (64, 64), (12, 34, 56))


def _inject_transient_replace(monkeypatch, failures: int) -> dict:
    """‎os.replace‎ واقعی را با نسخه‌ای قلابی می‌کند که «failures» بارِ اول
    PermissionErrorِ ویندوزی (winerror 32) می‌دهد و بعد عبور می‌دهد —
    شبیه‌سازیِ قفلِ گذرای آنتی‌ویروس/ایندکسر یا مسابقه‌ی دو کارگر هم‌زمان.
    چون patch روی ماژولِ os است، هر دو نویسنده‌ی اتمیک (imgcache و covers)
    از همان مسیرِ قلابی می‌گذرند — همان چیزی که در ویندوز واقعی می‌شود."""

    real_replace = os.replace
    state = {"left": failures}

    def flaky(src, dst, *a, **kw):
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(
                32, "The process cannot access the file because it is being "
                    "used by another process")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", flaky)
    return state


# ------------------------------------------------------ helper خودِ هلپر
def test_replace_with_retry_absorbs_transient_locks(tmp_path, monkeypatch):
    """۷ قفلِ پشت‌سرهم باید جذب شود — بودجه‌ی قدیمیِ ۵تاییِ round 17 کافی نبود
    و در طوفانِ کارگرهای هم‌زمان تمام می‌شد (مکانیزمِ قرمزِ CI در راند ۲۷)."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"old")

    _inject_transient_replace(monkeypatch, 7)

    replace_with_retry(src, dst)
    assert dst.read_bytes() == b"payload"


def test_replace_with_retry_raises_after_persistent_lock(tmp_path, monkeypatch):
    """قفلِ *پایدار* هرگز قورت داده نمی‌شود: بعد از سقفِ تلاش‌ها بالا می‌آید
    تا دیسکِ پر/دسترسیِ دائمی مثل قبل فوراً دیده شود — سقفِ کوتاه هم یعنی
    نخِ کارگر معطلِ حلقه نمی‌ماند."""
    src = tmp_path / "src2.bin"
    src.write_bytes(b"payload")
    dst = tmp_path / "dst2.bin"

    def always_locked(src, dst, *a, **kw):
        raise PermissionError(32, "locked forever")

    monkeypatch.setattr(os, "replace", always_locked)

    with pytest.raises(PermissionError):
        replace_with_retry(src, dst, max_attempts=3)
    assert not dst.exists()          # جایگزینیِ ناتمام فایلِ نیمه‌کاره نمی‌گذارد


# ------------------------------------------------------ کشِ تامبنیل
def test_save_cache_image_keeps_webp_under_transient_lock(tmp_path, monkeypatch):
    """دو قفلِ گذرا دیگر کش را بی‌صدا به PNG نمی‌برد — فایلِ .webp باید سرِ
    جای خودش ساخته شود و فال‌بکِ PNG اصلاً رخ ندهد. (قبل از round 28 همین
    سناریو بازمی‌گشت «atomic.png» و تستِ موازیِ روی یک فایلِ مشترک در
    بدشانسیِ ویندوزی قرمز می‌شد.)"""
    out = tmp_path / "atomic.webp"

    _inject_transient_replace(monkeypatch, 2)

    ret = save_cache_image(_tiny(), out)
    assert ret == out and out.is_file()
    assert read_cache_pil(out) is not None
    assert not out.with_suffix(".png").exists()


# ------------------------------------------------------ کاور سفارشی
def test_covers_atomic_save_survives_lock_burst_beyond_old_budget(tmp_path,
                                                                  monkeypatch):
    """۶ قفلِ پشت‌سرهم در _atomic_save کاور باید جذب شود — بودجه‌ی قدیمیِ
    ۵تایی (جمعاً ~۳۰۰ms) در برابر موجِ اسکنِ آنتی‌ویروس روی ۵۰ فایلِ هم‌زمان
    تمام می‌شد، CoverWriteError بالا می‌آمد و در زیرپروسه‌ی C2 به QMessageBox
    و SystemExit می‌رسید. حالا ۸ تلاش (~۰٫۸s) موجِ کوتاه را رد می‌کند و
    خطای پایدار همان‌جا غیرصفر بالا می‌آید."""
    out = tmp_path / "cover.webp"

    _inject_transient_replace(monkeypatch, 6)

    covers._atomic_save(_tiny(), out, quality=covers.STATIC_QUALITY)
    assert out.is_file()
    assert read_cache_pil(out) is not None
