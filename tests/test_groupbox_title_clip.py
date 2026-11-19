# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests — the clipped category-dialog title.

An earlier fix adjusted the dialog *width*, but the first word of the RTL
title was still eaten at narrow widths. Offscreen pixel reproduction pinpointed
the real root cause: the global stylesheet offset `QGroupBox::title
{ right: 12px }` makes Qt clip the FIRST glyph of the RTL title (and the first
letter "C" in the LTR English UI) at narrow widths — a Qt QSS
subcontrol-offset bug, not a dialog-size problem. The fix uses the box-model
`margin-right: 12px` instead, which shrinks the title rect instead of
shifting the drawn text.

Test 1 pins the rule pattern (no bare positional offsets on the title
subcontrol). Test 2 pins the rendered pixels themselves: the groupbox title
must carry the same ink width as a bold QLabel with the same text, in RTL
(Persian) and LTR (English) — the bug is ~10+ px short, the fix is equal.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pytest

TITLE_FA = "دسته یا زیردسته‌ی جدید"
TITLE_EN = "Category or subcategory"

# The app's bundled font — the same one main.py ("load_font") loads for the whole UI.
_FONT_FILE = Path(__file__).resolve().parent.parent / "assets" / "Vazirmatn-Regular.ttf"

# Registered family name (None = the font cannot be loaded in this environment) — qapp skips.
_FONT_FAMILY: str | None = None


def _load_bundled_font() -> str | None:
    """Register the bundled font in QFontDatabase; returns the registered family or None.

    On a local Windows machine the repo may be copied under a non-ASCII path
    (e.g. a folder named in Persian). On the offscreen platform all fonts are
    opened through FreeType with 8-bit fopen; Windows fopen cannot map the
    UTF-8 Persian path into the ANSI codepage → "FT_New_Face failed with
    index 0 : 1" (error 1 = cannot open resource, i.e. the file could not be
    opened — not that it is corrupt) and the font is not registered; after
    that QFont() falls back to a font without Persian glyphs and the
    "~22px ink" guard fails.
    The repo file itself is intact (magic 00010000); the problem is the path.
    Solution: if the path is not directly 8-bit clean — or direct
    registration fails — copy the font to a temporary ASCII path under %TEMP%
    and load it from there; if it still fails to register, return None so the
    pixel test is skipped (an environment-induced false red, not a real bug
    detection).
    """
    from PySide6.QtGui import QFontDatabase

    def _register(path: Path) -> str | None:
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid == -1:
            return None
        fams = QFontDatabase.applicationFontFamilies(fid)
        if "Vazirmatn" in fams:
            return "Vazirmatn"
        return fams[0] if fams else None

    if not _FONT_FILE.is_file():
        return None
    try:
        str(_FONT_FILE).encode("ascii")  # Windows' 8-bit fopen only guarantees ASCII
    except UnicodeEncodeError:
        pass
    else:
        fam = _register(_FONT_FILE)
        if fam:
            return fam
    # Non-ASCII path (or failed direct registration): load from a temporary ASCII path
    tmp_font = Path(tempfile.mkdtemp(prefix="folderdex-font-")) / _FONT_FILE.name
    shutil.copyfile(_FONT_FILE, tmp_font)
    return _register(tmp_font)


@pytest.fixture(scope="module")
def qapp():
    """Module-shared QApplication + loading of the bundled Persian font.

    The app itself loads Vazirmatn from assets, but this fixture used to
    create only a QApplication; on the Windows CI runner (offscreen) the
    system default font has no Persian glyphs, the Persian text renders
    almost inkless and the "reference label rendered empty" guard fails
    (assert 22 > 40). Loading the same repo font makes the pixel result
    identical on every runner.
    It also tolerates a non-ASCII font path (temporary copy under %TEMP%);
    if the font cannot be registered at all, the pixel test skips instead of
    going falsely red.
    """
    from PySide6.QtWidgets import QApplication
    global _FONT_FAMILY
    app = QApplication.instance() or QApplication([])
    _FONT_FAMILY = _load_bundled_font()
    return app


@pytest.fixture()
def rtl_dir():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    prev = QApplication.layoutDirection()
    QApplication.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    yield
    QApplication.setLayoutDirection(prev)


def _title_rule(css: str) -> str:
    """Extracts the actual QGroupBox::title rule from the built theme."""
    m = re.search(r"QGroupBox::title\s*\{[^}]*\}", css)
    assert m, "QGroupBox::title rule missing from the theme template"
    return m.group(0)


# ------------------------------------------- 1) pattern guard on the theme rule itself
def test_title_rule_has_no_positional_offset():
    """The category was still invisible when creating a new category.

    Root cause: the absolute right: 12px offset on the title subcontrol — Qt
    shifts the text in RTL but does not move the clip region with it; the
    first glyph of the RTL text falls outside on the right and is cleanly
    clipped.
    The healthy shape: margin-right (box model), which shrinks the text rect
    instead of shifting the drawing.
    """
    from qss import build_theme, DEFAULT_THEME
    rule = _title_rule(build_theme(DEFAULT_THEME))
    # no absolute right/left offset is allowed on the group title — the bug pattern
    for side in ("right", "left"):
        bad = re.search(rf"(?<![-\w]){side}\s*:\s*[-\d.]+px", rule)
        assert not bad, (
            f"positional '{side}' offset on QGroupBox::title clips the first "
            f"RTL/LTR glyph root cause: {rule!r}")
    # and the healthy replacement must be in place (the 12px edge gap)
    assert re.search(r"margin-(?:right|left)\s*:\s*\d+px", rule), \
        f"title inset lost — the 12px edge gap must use margin, not offset: {rule!r}"


# ------------------------------------------------ 2) the rendered pixels themselves
def _ink_width(img) -> int:
    """Ink width of white text on a dark background — columns containing bright pixels."""
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
    """The QGroupBox title must lay down ink as wide as the full text — not a clipped first glyph.

    Comparison reference: a same-font same-size QLabel — with the bug, the
    title ink is shorter by roughly the width of the first glyph; with the
    fix they are equal (±6px).
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

        # Set the font family explicitly on the box and the reference label so
        # the ink comparison does not depend on the runner's default font; the
        # 13px size and bold come from the QSS and are identical for both.
        # Full environment guard — both a missing file and an unregistered font
        # (non-ASCII path on Windows offscreen: "FT_New_Face failed with
        # index 0 : 1", or a corrupt file) skip explicitly — an
        # environment-induced false red, not a real bug detection.
        if _FONT_FAMILY is None:
            pytest.skip("bundled Vazirmatn not loadable here (missing file, or "
                        "FreeType cannot open this non-ASCII path) — no deterministic "
                        "Persian reference")
        from PySide6.QtGui import QFont
        _fnt = QFont(_FONT_FAMILY)

        box = QGroupBox(title)
        box.setFont(_fnt)
        box.setStyleSheet(box_css + " " + rule)
        box.resize(420, 60)

        lab = QLabel(title)
        lab.setFont(_fnt)
        lab.setStyleSheet(lab_css)
        lab.resize(420, 30)
        lab.adjustSize()

        box_w = _ink_width(box.grab().toImage())
        lab_w = _ink_width(lab.grab().toImage())

        assert lab_w > 40, "reference label rendered empty — test environment broken"
        assert box_w >= lab_w - 6, (
            f"groupbox title ink {box_w}px vs full text ink {lab_w}px — the first "
            f"character of {title!r} is clipped again (regression)")
    finally:
        QApplication.setLayoutDirection(prev)
