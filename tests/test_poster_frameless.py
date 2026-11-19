# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Frameless poster on the details page.

The poster slot stays 252×336 but no bar/border/background may
be drawn — just the image itself (rounded corners, floating on the page
color). This mode is only for the details page: bg="none". The card grid
keeps its default bg=None and its theme-colored letterbox stays untouched.
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
    """A solid horizontal 320×180 image — in a portrait frame it gets top/bottom bars."""
    img = QImage(320, 180, QImage.Format.Format_ARGB32)
    img.fill(QColor(180, 30, 40))
    return img


def test_none_bg_leaves_bars_transparent(qapp):
    """bg="none": bars/area outside the rounded corners are transparent — just the image."""
    out = poster_letterbox_image(_wide_img(), 100, 133, 8, bg="none")
    # frame 100×133, letterboxed image ≈ 100×56 centered (y≈38..94)
    top_bar = out.pixelColor(50, 2)
    assert top_bar.alpha() == 0                      # top bar: no background
    bottom_bar = out.pixelColor(50, 131)
    assert bottom_bar.alpha() == 0                   # bottom bar: no background
    mid = out.pixelColor(50, 66)                     # inside the image itself
    assert mid.alpha() == 255
    assert (mid.red(), mid.green(), mid.blue()) == (180, 30, 40)


def test_default_bg_still_theme_colored(qapp):
    """Default (bg=None) → same behavior as before: bars are filled with the
    poster theme color."""
    out = poster_letterbox_image(_wide_img(), 100, 133, 8)
    bar = out.pixelColor(50, 2)
    assert bar.alpha() == 255
    theme = poster_bg_color()
    assert (bar.red(), bar.green(), bar.blue()) \
        == (theme.red(), theme.green(), theme.blue())
