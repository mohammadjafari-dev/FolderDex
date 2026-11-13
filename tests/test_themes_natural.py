# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""تم‌های طبیعیِ round 13 §۸ — forest / sunset / ocean / paper.

خواسته‌ی کاربر: تم‌های خودِ برنامه زیباتر و طبیعی‌تر شوند و **هیچ تصویر/بلوری**
به‌عنوان پس‌زمینه نیاید — زیبایی از خودِ پالت می‌آید. این تست‌ها قراردادِ پالت‌ها
را قفل می‌کنند:
- کلیدهای هر تم عیناً همان کلیدهای «dark» (بدونِ کلیدِ اضافه/کم)؛
- همه‌ی رنگ‌ها ‎#rrggbb‎ معتبر؛
- کنتراست با luminance استاندارد: text/bg ≥ 7، muted/bg ≥ 4.5، accent/bg ≥ 3؛
- build_theme هیچ @کلید@ جای‌گذاری‌نشده‌ای باقی نمی‌گذارد؛
- منوی تم از «for key in THEMES» ساخته می‌شود (ui/dialogs.py) پس خودکار ۸ تاست؛
  ذخیره/بازیابی در config.json با store.set_theme/store.theme؛
- قانونِ ثابت: Hero و اسپلش در همه‌ی تم‌ها تیره می‌مانند (رنگ‌های ثابت در _TEMPLATE).
"""

from __future__ import annotations

import re

from qss import THEMES, build_theme, theme_bg, theme_label, theme_names


def _lum(hex6: str) -> float:
    """luminance نسبیِ WCAG 2.x برای ‎#rrggbb‎."""
    h = hex6.lstrip("#")

    def ch(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_all_themes_have_exactly_dark_keyset():
    """هر تم باید دقیقاً همان کلیدهایِ «dark» را داشته باشد — نه کم، نه زیاد."""
    assert len(THEMES) == 8, f"expected 8 themes, got {sorted(THEMES)}"
    dark_keys = set(THEMES["dark"])
    for name, t in THEMES.items():
        assert set(t) == dark_keys, \
            f"theme {name!r} key mismatch: {set(t) ^ dark_keys}"


def test_all_theme_colors_are_valid_hex():
    for name, t in THEMES.items():
        for k, v in t.items():
            if k == "label":
                assert isinstance(v, str) and v.strip()
                continue
            assert HEX_RE.match(str(v)), f"{name}.{k} = {v!r} is not #rrggbb"


def test_contrast_thresholds_all_themes():
    """جدولِ بریف §۸: text/bg ≥ 7، muted/bg ≥ 4.5، accent/bg ≥ 3.

    اعدادِ محاسبه‌شده‌ی ۴ پالتِ جدید (round-trip با جدولِ بریف):
    forest 15.88/8.06/5.47، sunset 15.93/7.48/5.22،
    ocean 15.87/7.59/5.18، paper 13.49/5.42/4.19.
    """
    for name, t in THEMES.items():
        tr = _contrast(t["text"], t["bg"])
        mr = _contrast(t["muted"], t["bg"])
        ar = _contrast(t["accent"], t["bg"])
        assert tr >= 7.0, f"{name}: text/bg = {tr:.2f} < 7"
        assert mr >= 4.5, f"{name}: muted/bg = {mr:.2f} < 4.5"
        assert ar >= 3.0, f"{name}: accent/bg = {ar:.2f} < 3"


def test_build_theme_has_no_leftover_placeholders():
    """build_theme هیچ @کلید@ جای‌گذاری‌نشده‌ای در خروجی نمی‌گذارد."""
    leftover = re.compile(r"@[a-z0-9_]+@")
    for name in theme_names():
        css = build_theme(name)
        hits = leftover.findall(css)
        assert not hits, f"build_theme({name!r}) left placeholders: {hits}"
        # رنگ‌هایِ تم واقعاً در خروجی نشسته‌اند
        assert THEMES[name]["bg"] in css


def test_hero_and_splash_stay_dark_in_every_theme():
    """قانونِ ثابت: هدرِ Hero و صفحه‌ی اسپلش در همه‌ی تم‌ها تیره می‌مانند.

    رنگ‌های #Hero/#Splash در _TEMPLATE ثابت‌اند — نه از متغیرهای تم."""
    for name in theme_names():
        css = build_theme(name)
        assert "#Hero { background: #111927;" in css, name
        assert "#Splash { background: #0f1524;" in css, name


def test_theme_names_and_labels():
    """theme_names() هر ۸ تم را می‌دهد و theme_label فارسی و غیرخالی است."""
    assert set(theme_names()) == \
        {"dark", "dim", "amoled", "light", "forest", "sunset", "ocean", "paper"}
    assert theme_label("forest") == "جنگل"
    assert theme_label("sunset") == "غروب"
    assert theme_label("ocean") == "اقیانوس"
    assert theme_label("paper") == "کاغذ"
    for name in theme_names():
        assert theme_label(name).strip()


def test_theme_bg_follows_palette():
    """theme_bg() پس‌زمینه‌ی letterboxِ پوسترها را هم‌رنگِ همان تم می‌کند."""
    for name, t in THEMES.items():
        assert theme_bg(name) == t["bg"]
    assert theme_bg("not-a-theme") == THEMES["dark"]["bg"]


def test_theme_config_roundtrip(tmp_path):
    """تمِ جدید در config.json ذخیره و بازیابی می‌شود (store.set_theme/theme)."""
    from store import Store
    s = Store(tmp_path / "config.json")
    try:
        for name in ("forest", "sunset", "ocean", "paper"):
            s.set_theme(name)
            assert s.theme == name
            # بازیابی از دیسک — Store تازه همان تم را می‌خواند
            s2 = Store(s.config_path)
            try:
                assert s2.theme == name
            finally:
                s2.close()
    finally:
        s.close()


def test_new_theme_names_are_valid_qss_keys_not_needed():
    """کلیدِ تم فقط یک نامِ دیکشنری است — مطمئن می‌شویم نام‌ها رزِرو و ثابت‌اند."""
    for name in theme_names():
        assert re.match(r"^[a-z][a-z0-9_]*$", name), name
