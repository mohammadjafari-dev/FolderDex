# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پوسترِ بدونِ کادر در صفحه‌ی جزئیات (round 13 §۴).

خواسته‌ی کاربر: جای پوستر همان ۲۵۲×۳۳۶ بماند ولی هیچ نوار/کادر/زمینه‌ای رسم
نشود — فقط خودِ تصویر (گوشه‌گرد، شناور روی رنگِ صفحه). این حالت فقط برای
صفحه‌ی جزئیات است: bg="none". شبکه‌ی کارت‌ها پیش‌فرضِ bg=None را می‌گیرد و
letterboxِ هم‌رنگِ تمش دست‌نخورده می‌ماند.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtGui")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage                    # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

from ui.widgets import poster_bg_color, poster_letterbox_image  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wide_img() -> QImage:
    """تصویرِ افقیِ یک‌رنگِ ۳۲۰×۱۸۰ — در قابِ عمودی نوارِ بالایی می‌گیرد."""
    img = QImage(320, 180, QImage.Format.Format_ARGB32)
    img.fill(QColor(180, 30, 40))
    return img


def test_none_bg_leaves_bars_transparent(qapp):
    """bg="none": نوارها/بیرونِ گوشه‌ها شفاف‌اند — فقط خودِ تصویر."""
    out = poster_letterbox_image(_wide_img(), 100, 133, 8, bg="none")
    # قاب ۱۰۰×۱۳۳، تصویرِ لِتِرباکس‌شده ≈ ۱۰۰×۵۶ وسط‌چین (y≈۳۸..۹۴)
    top_bar = out.pixelColor(50, 2)
    assert top_bar.alpha() == 0                      # نوارِ بالا: هیچ زمینه‌ای
    bottom_bar = out.pixelColor(50, 131)
    assert bottom_bar.alpha() == 0                   # نوارِ پایین: هیچ زمینه‌ای
    mid = out.pixelColor(50, 66)                     # داخلِ خودِ تصویر
    assert mid.alpha() == 255
    assert (mid.red(), mid.green(), mid.blue()) == (180, 30, 40)


def test_default_bg_still_theme_colored(qapp):
    """پیش‌فرض (bg=None) → همان رفتارِ قبلی: نوارها با رنگِ تمِ پوستر پر می‌شوند."""
    out = poster_letterbox_image(_wide_img(), 100, 133, 8)
    bar = out.pixelColor(50, 2)
    assert bar.alpha() == 255
    theme = poster_bg_color()
    assert (bar.red(), bar.green(), bar.blue()) \
        == (theme.red(), theme.green(), theme.blue())
