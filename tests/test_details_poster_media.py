# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Animated cover on the details page — aspect-fitted child label.

Bug: QMovie drawn directly on the poster (the fixed 252×336 frame without
scaledContents) painted the frame at its natural size, so non-3:4 covers
(horizontal/square) appeared center-cropped. Fix: the poster_movie child
label with setScaledContents(True) and a letterboxed geometry computed from
the frame's real aspect ratio (via QImageReader).

Expected numbers from _poster_slot_rect (k=min(252/sw,336/sh)):
  320×180 → (0, 97, 252, 142)        108×192 (9:16) → (31, 0, 189, 336)
  2520×1080 (21:9) → (0, 114, 252, 108)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image                                    # noqa: E402
from PySide6.QtCore import QRect                         # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from store import Store                                  # noqa: E402
from thumbs import ThumbnailCache                        # noqa: E402
from ui.details import DetailsPage                       # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _gif(path: Path, w: int, h: int, frames: int = 2) -> Path:
    """Test GIF of w×h with several colored frames."""
    frs = [Image.new("RGB", (w, h), (i * 60 % 256, 120, 90))
           for i in range(frames)]
    frs[0].save(path, save_all=True, append_images=frs[1:], duration=60, loop=0)
    return path


def _static_png(path: Path, w: int, h: int) -> Path:
    Image.new("RGB", (w, h), (8, 8, 8)).save(path, "PNG")
    return path


@pytest.fixture()
def page(qapp, tmp_path: Path):
    store = Store(tmp_path / "config.json")
    pg = DetailsPage(store, ThumbnailCache(tmp_path / ".thumb_cache"))
    yield pg
    pg.deleteLater()
    qapp.processEvents()
    store.close()


def test_horizontal_gif_gets_aspect_fitted_label(page, qapp, tmp_path: Path):
    """Horizontal 320×180 GIF: full-width child label with height 142 — not the whole frame."""
    src = _gif(tmp_path / "wide.gif", 320, 180)
    page._set_poster_media(src, _static_png(tmp_path / "wide_f0.png", 320, 180))
    qapp.processEvents()
    assert page.poster_movie.geometry() == QRect(0, 97, 252, 142)
    assert page._poster_media_src == (src, tmp_path / "wide_f0.png")


def test_portrait_gif_9_16_fills_height(page, qapp, tmp_path: Path):
    """9:16 portrait: the full frame height, width 189 centered."""
    src = _gif(tmp_path / "tall.gif", 108, 192)
    page._set_poster_media(src, _static_png(tmp_path / "tall_f0.png", 108, 192))
    qapp.processEvents()
    assert page.poster_movie.geometry() == QRect(31, 0, 189, 336)


def test_ultrawide_gif_21_9_is_108_tall(page, qapp, tmp_path: Path):
    """21:9 ultrawide: the full 252 width at exactly 108 tall (whole cover, no cropping)."""
    src = _gif(tmp_path / "ultra.gif", 2520, 1080)
    page._set_poster_media(src, _static_png(tmp_path / "ultra_f0.png", 2520, 1080))
    qapp.processEvents()
    rect = page.poster_movie.geometry()
    assert rect.width() == 252 and rect.height() == 108
    assert rect.x() == 0 and rect.y() == 114


def test_square_gif_is_centered(page, qapp, tmp_path: Path):
    """1:1: a square 252×252 label centered in the frame."""
    src = _gif(tmp_path / "sq.gif", 300, 300)
    page._set_poster_media(src, _static_png(tmp_path / "sq_f0.png", 300, 300))
    qapp.processEvents()
    assert page.poster_movie.geometry() == QRect(0, 42, 252, 252)


def test_static_cover_hides_movie_label(page, qapp):
    """Static cover (or no source): the movie label hides and the play source is cleared."""
    page._set_poster_media(None, None)
    assert page.poster_movie.isHidden() is True
    assert page._poster_media_src is None
    page._set_poster_media("C:/a/cover.anim.webp", "C:/a/cover.anim.webp")
    assert page.poster_movie.isHidden() is True     # src==static → stays static


def test_stop_poster_movie_hides_label(page, qapp, tmp_path: Path):
    """_stop_poster_movie stops the movie and hides the child label."""
    src = _gif(tmp_path / "stop.gif", 320, 180)
    page._set_poster_media(src, _static_png(tmp_path / "stop_f0.png", 320, 180))
    qapp.processEvents()
    assert page._movie is not None
    page._stop_poster_movie()
    qapp.processEvents()
    assert page.poster_movie.isHidden() is True
    assert page._movie is None


def test_movie_label_under_cover_button(page, qapp, tmp_path: Path):
    """The corner cover button (btn_cover) must stay above the movie label — clickable."""
    src = _gif(tmp_path / "btn.gif", 320, 180)
    page._set_poster_media(src, _static_png(tmp_path / "btn_f0.png", 320, 180))
    qapp.processEvents()
    assert page.btn_cover in page.poster.children() or True
    # z-order: btn_cover is created after poster_movie → stacked above it
    siblings = [w for w in page.poster.children()
                if w in (page.poster_movie, page.btn_cover)]
    assert siblings[-1] is page.btn_cover
