# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""رگرسیون‌های Round 29 — چهار نقطه‌ی ‎os.replace‎ لخت در filethumbs.py.

Round 28 تلاشِ مجددِ قفلِ گذرای ویندوز را فقط برای دو نویسنده‌ی اصلی (imgcache
و covers) آورد و چهار جایگزینیِ filethumbs را عمداً لخت گذاشت — استدلالِ آن‌روز
این بود که آن مسیرها «تحمل‌پذیرِ خطا»یند و فقط لاگ می‌اندازند. ممیزیِ بعدی
(درست) نکته را گرفت: آن مسیرها کرش نمی‌کنند، اما قفلِ گذرای آنتی‌ویروس روی
فایلِ تازه‌استخراج‌شده یعنی از دست رفتنِ تامبنیلِ همان فایلِ فشرده در اولین
تلاش — دقیقاً برای قابلیتی که برایش ساخته شده. حالا هر چهار نقطه از همان
هلپرِ کانونیِ imgcache.replace_with_retry استفاده می‌کنند:

  - دو تغییرنامِ بک‌اندِ CLIِ 7z (_CliArc.extract)
  - جابه‌جاییِ عضوِ استخراج‌شده در بک‌اندِ py7zr (openerِ داخل _arc_open)
  - نوشتنِ اتمیکِ کشِ «عضو انتخاب‌شده» (_arc_picks_save)

مثل round 28، ویندوزِ واقعی بازسازی نمی‌شود — با *تزریقِ خطا* همان رفتار تعیین
می‌شود و تست‌ها همه‌جا (لینوکس/ویندوز) یکسان اجرا می‌شوند. تستِ opener عمداً
از _arc_open کامل استفاده می‌کند تا هر بک‌اندی که روی آن میزبان انتخاب شود
(7zِ CLI در ویندوز، py7zر در لینوکس) پوشش داده شود.
"""

from __future__ import annotations

import json
import os

from PIL import Image

import filethumbs


def _inject_transient_replace(monkeypatch, failures: int) -> dict:
    """همان تزریقِ خطای round 28 (tests/test_round28_fixes.py): ‎os.replace‎
    واقعی را با نسخه‌ای قلابی عوض می‌کند که «failures» بارِ اول
    PermissionErrorِ وین‌خطای 32 می‌دهد و بعد عبور می‌دهد — شبیه‌سازیِ قفلِ
    گذرای آنتی‌ویروس/ایندکسر روی فایلِ تازه‌نوشته. چون patch روی ماژولِ os
    است، بک‌اندهای filethumbs هم از همان مسیرِ قلابی می‌گذرند."""

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


# ------------------------------------------------- کشِ «عضو انتخاب‌شده»
def test_arc_picks_save_survives_transient_lock(tmp_path, monkeypatch):
    """نوشتنِ اتمیکِ archive_picks.json باید قفلِ گذرا را جذب کند — در درختِ
    والد، همان یک PermissionError نوشتنِ کش را به‌کلی از بین می‌برد (رفتارِ
    قدیمی: exceptِ بی‌صدا) و انتخابِ بعدیِ کاربر دوباره از صفر شروع می‌شد."""
    ft = filethumbs.FileThumbs(tmp_path / "cache")
    ft._arc_picks = {"/lib/films.zip": {"member": "a.png", "kind": "image"}}

    _inject_transient_replace(monkeypatch, 2)

    ft._arc_picks_save()
    out = ft.dir / "archive_picks.json"
    assert out.exists(), "archive_picks.json must land despite transient locks"
    assert json.loads(out.read_text(encoding="utf-8")) == ft._arc_picks


# ------------------------------------------- openerِ عضوِ آرشیو (هر بک‌اند)
def test_archive_member_opener_survives_transient_lock(tmp_path, monkeypatch):
    """استخراجِ عضوِ آرشیو باید قفلِ گذرای ویندوز را جذب کند و فایلِ عضو را
    کامل روی دیسک بنشاند — در درختِ والد، همان یک PermissionError مسیرِ خطا را
    تا «تامبنیل این پاس ساخته نشد» می‌برد و کاربر جای‌نگه‌دار می‌دید."""
    src = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), (12, 34, 56)).save(src, "PNG")
    arc = tmp_path / "sample.7z"
    import py7zr
    with py7zr.SevenZipFile(arc, "w") as z:
        z.write(src, arcname="frame.png")

    opened = filethumbs._arc_open(arc)
    assert opened, "no .7z backend available (CLI 7z and py7zr both missing?)"
    handle, members, opener = opened
    try:
        names = [n for n, _size in members]
        assert "frame.png" in names, f"member list missing frame.png: {names}"
        dest = tmp_path / "out" / "frame.png"
        dest.parent.mkdir(parents=True, exist_ok=True)

        _inject_transient_replace(monkeypatch, 2)

        opener("frame.png", dest, timeout=60)
        assert dest.exists() and dest.stat().st_size > 0
        with Image.open(dest) as im:
            assert im.size == (64, 64)
    finally:
        try:
            handle.close()
        except Exception:
            pass
