# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""round 33 regression tests — the «دسته is still invisible» follow-up.

Round 32 fixed the dialog *width* but the user still saw the word «دسته»
eaten on v0.55.0. Offscreen pixel reproduction pinpointed the real root
cause: the global stylesheet offset `QGroupBox::title { right: 12px }` makes
Qt clip the FIRST glyph of the RTL title (and the first letter «C» in the
LTR English UI) at narrow widths — a Qt QSS subcontrol-offset bug, not a
dialog-size problem. The fix uses the box-model `margin-right: 12px`
instead, which shrinks the title rect instead of shifting the drawn text.

Test 1 pins the rule pattern (no bare positional offsets on the title
subcontrol). Test 2 pins the rendered pixels themselves: the groupbox title
must carry the same ink width as a bold QLabel with the same text, in RTL
(Persian) and LTR (English) — the bug is ~10+ px short, the fix is equal.
"""

from __future__ import annotations

import re

import pytest

TITLE_FA = "دسته یا زیردسته‌ی جدید"
TITLE_EN = "Category or subcategory"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def rtl_dir():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    prev = QApplication.layoutDirection()
    QApplication.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    yield
    QApplication.setLayoutDirection(prev)


def _title_rule(css: str) -> str:
    """قانونِ واقعیِ QGroupBox::title را از تمِ ساخته‌شده برمی‌دارد."""
    m = re.search(r"QGroupBox::title\s*\{[^}]*\}", css)
    assert m, "QGroupBox::title rule missing from the theme template"
    return m.group(0)


# ------------------------------------------- ۱) گاردِ الگو روی خودِ قانونِ تم
def test_title_rule_has_no_positional_offset():
    """گزارشِ کاربر (0.55.0): «هنوز هم موقع ساخت دسته جدید دسته دیده نمی‌شود».

    علتِ ریشه‌ای: آفستِ مطلقِ right: 12px روی زیرکنترلِ عنوان — Qt در RTL
    متن را جابه‌جا می‌کشد ولی ناحیه‌ی clip را با آن جابه‌جا نمی‌کند؛ حرفِ
    اولِ متنِ RTL از سمتِ راست بیرون می‌ماند و تمیز بریده می‌شود.
    شکلِ سالم: margin-right (مدلِ جعبه) که مستطیلِ متن را تنگ می‌کند، نه
    اینکه ترسیم را شیفت دهد.
    """
    from qss import build_theme, DEFAULT_THEME
    rule = _title_rule(build_theme(DEFAULT_THEME))
    # هیچ آفستِ مطلقِ راست/چپ روی عنوانِ گروه مجاز نیست — الگویِ باگ
    for side in ("right", "left"):
        bad = re.search(rf"(?<![-\w]){side}\s*:\s*[-\d.]+px", rule)
        assert not bad, (
            f"positional '{side}' offset on QGroupBox::title clips the first "
            f"RTL/LTR glyph (round 33 root cause): {rule!r}")
    # و شکلِ سالمِ جایگزین باید سرِ جایش باشد (فاصله‌ی ۱۲px از لبه)
    assert re.search(r"margin-(?:right|left)\s*:\s*\d+px", rule), \
        f"title inset lost — the 12px edge gap must use margin, not offset: {rule!r}"


# ------------------------------------------------ ۲) خودِ پیکسل‌هایِ ترسیم‌شده
def _ink_width(img) -> int:
    """پهنایِ جوهرِ متنِ سفید روی زمینه‌ی تیره — ستون‌هایِ حاویِ پیکسلِ روشن."""
    cols = []
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            if c.red() + c.green() + c.blue() > 300:
                cols.append(x)
                break
    return (max(cols) - min(cols) + 1) if cols else 0


@pytest.mark.parametrize("title,lang_dir", [(TITLE_FA, "rtl"), (TITLE_EN, "ltr")])
def test_groupbox_title_is_not_clipped(qapp, title, lang_dir):
    """عنوانِ QGroupBox باید به پهنایِ متنِ کامل جوهر بگذارد — نه حرفِ اولِ بریده.

    مرجعِ مقایسه: QLabelِ هم‌فونتِ هم‌اندازه — با باگ، جوهرِ عنوان حدودِ
    پهنایِ «د»/«C» کوتاه‌تر است؛ با اصلاح، برابر (±۶px) می‌شود.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QGroupBox, QLabel

    prev = QApplication.layoutDirection()
    QApplication.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if lang_dir == "rtl"
        else Qt.LayoutDirection.LeftToRight)
    try:
        from qss import build_theme, DEFAULT_THEME
        rule = _title_rule(build_theme(DEFAULT_THEME))
        box_css = ("QGroupBox { border: none; padding: 8px; margin-top: 14px;"
                   "  font-weight: bold; font-size: 13px; background:#15161a;"
                   "  color:#ffffff; }")
        lab_css = "QLabel { font-weight: bold; font-size: 13px; background:#15161a; color:#ffffff; }"

        box = QGroupBox(title)
        box.setStyleSheet(box_css + " " + rule)
        box.resize(420, 60)

        lab = QLabel(title)
        lab.setStyleSheet(lab_css)
        lab.resize(420, 30)
        lab.adjustSize()

        box_w = _ink_width(box.grab().toImage())
        lab_w = _ink_width(lab.grab().toImage())

        assert lab_w > 40, "reference label rendered empty — test environment broken"
        assert box_w >= lab_w - 6, (
            f"groupbox title ink {box_w}px vs full text ink {lab_w}px — the first "
            f"character of {title!r} is clipped again (round 33 regression)")
    finally:
        QApplication.setLayoutDirection(prev)
