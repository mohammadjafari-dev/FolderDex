# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests — four English-UI fixes.

1) The «Games» root-group card kept its letter placeholder although the
   root had a loose image: the scanner
   parked that image in preview_video — a slot the video-thumb chain
   rejects (not a video extension). Now the first loose image becomes the
   card icon when no explicit icon exists.
2) Sidebar stats on two parallel bases (Store.scope_progress): videos on
   the «episodes» basis (video_count), non-video titles on the
   watchable_total basis («items») — so the All page matches category
   pages. (Superseded by the per-kind buckets of
   scope_progress_by_kind; the test below pins the bucketed API.)
3) prebuild skip guard (cover_ready): an «unchanged» title is only
   skipped when its cover really exists on disk — an icon-less title
   whose poster was never built (e.g. soft video failures without
   ffmpeg) must be re-checked every run, not stuck as a letter
   placeholder forever with zero posters built.
4) The forced retry counter is separate from the normal probe counter in
   filethumbs — the first (unforced) poster attempt used to consume the
   >= 1 budget so the forced retry in the same run never really probed.
5) English singular for value 1: «Duration: 1 minutes» → «1 minute»
   (i18n.fix_en_singular + format_duration).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from i18n import fix_en_singular, set_lang  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


@pytest.fixture()
def en_lang():
    set_lang("en")
    yield
    set_lang("fa")


class _Item:
    """Minimal item stand-in (same shape as the sibling test modules)."""

    def __init__(self, path, video_count=0, loose_group=False, category=""):
        self.path = str(path)
        self.video_count = video_count
        self.loose_group = loose_group
        self.category = category


# --------------------------------------------------- 1) Games root cover
def test_loose_image_root_group_becomes_icon(tmp_path: Path):
    from scanner import scan_roots
    root = tmp_path / "بازی"
    root.mkdir()
    wall = root / "Wallpaper - Lost Realms.jpg"
    wall.write_bytes(b"\xff\xd8" + b"\x00" * 64)     # a realistic-looking JPEG stub
    items = scan_roots([{"path": str(root), "category": "بازی"}], status={})
    groups = [i for i in items if getattr(i, "loose_group", False)]
    assert groups, "گروهِ فایل‌های مستقیمِ ریشه باید ساخته شود"
    g = groups[0]
    assert g.icon_path == wall          # the root's loose image = the card cover
    assert g.preview_video is None      # no longer parked in the video slot
    assert g.video_count == 0


def test_loose_image_root_group_with_video_keeps_video_preview(tmp_path: Path):
    from scanner import scan_roots
    root = tmp_path / "میکس"
    root.mkdir()
    wall = root / "Wallpaper.jpg"
    wall.write_bytes(b"\xff\xd8" + b"\x00" * 64)
    vid = root / "teaser.mp4"
    vid.write_bytes(b"\x00" * 128)
    items = scan_roots([{"path": str(root), "category": "فیلم"}], status={})
    g = next(i for i in items if getattr(i, "loose_group", False))
    assert g.preview_video == vid       # the video wins the preview slot
    assert g.icon_path == wall          # and the image still covers the card


# --------------------------------------------------- 2) two parallel bases
def test_scope_progress_by_kind(store: Store, tmp_path: Path):
    # miniature library: two video titles + a book
    # + a game; replaces the dual-basis scope_progress with video/book/
    # image/other buckets
    book_dir = tmp_path / "The Cartographer's Daughter"
    book_dir.mkdir()
    (book_dir / "book.pdf").write_bytes(b"%PDF-1.4 " + b"\x00" * 32)
    (book_dir / "poster.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 32)
    game_dir = tmp_path / "LOST REALMS"
    game_dir.mkdir()
    (game_dir / "game.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    (game_dir / "Wallpaper.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 32)

    series = _Item(tmp_path / "HOLLOW CREEK", video_count=2, category="Series")
    store.watch_set(series, "Season 1/S01E01.mp4", Store.WATCH_DONE, 0.0)
    store.watch_set(series, "Season 1/S01E02.mp4", Store.WATCH_DONE, 0.0)
    store.watch_set(series, "poster.jpg", Store.WATCH_DONE, 0.0)   # sidecar
    film = _Item(tmp_path / "COSMIC DRIFT", video_count=1, category="Movies")
    store.watch_set(film, "teaser.mp4", Store.WATCH_PART, 5.0)
    book = _Item(book_dir, video_count=0, category="Books")
    store.watch_set(book, "book.pdf", Store.WATCH_DONE, 0.0)
    store.watch_set(book, "poster.jpg", Store.WATCH_DONE, 0.0)
    game = _Item(game_dir, video_count=0, category="Games")
    store.watch_set(game, "game.zip", Store.WATCH_DONE, 0.0)

    prog = store.scope_progress_by_kind([series, film, book, game])
    assert prog["video"] == (2, 1, 3)    # episodes: only video records of video titles
    assert prog["book"] == (2, 0, 2)     # books: the pdf + its poster — current files of the book title
    assert prog["image"] == (0, 0, 1)    # the game's wallpaper has a denominator but no mark -> no line
    # game: its mark stays in the "other" bucket — no percentage line in the UI
    assert prog["other"] == (1, 0, 1)


# --------------------------------------------------- 3) prebuild skip guard
def test_cover_ready_guard(store: Store, tmp_path: Path):
    from prebuild import cover_ready
    made_png = tmp_path / "cache" / "thumb.webp"
    made_png.parent.mkdir()
    made_png.write_bytes(b"RIFF" + b"\x00" * 16)
    cache = {str(tmp_path / "v.mp4"): made_png}
    cache_get = lambda p: cache.get(str(p))          # like ThumbnailCache.get
    fake_st = type("S", (), {"custom_poster": lambda self, p: (None, None)})()

    it_icon = _Item(tmp_path / "A")
    it_icon.icon_path = tmp_path / "a.jpg"
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8")
    assert cover_ready(it_icon, fake_st, cache_get) is True

    it_cached = _Item(tmp_path / "B", video_count=1)
    it_cached.preview_video = tmp_path / "v.mp4"
    assert cover_ready(it_cached, fake_st, cache_get) is True   # existing thumbnail

    it_bare = _Item(tmp_path / "C", video_count=1)
    it_bare.preview_video = tmp_path / "missing.mp4"
    assert cover_ready(it_bare, fake_st, cache_get) is False    # never built

    # a custom poster counts as a cover too
    st2 = type("S", (), {"custom_poster": lambda self, p: (made_png, None)})()
    assert cover_ready(_Item(tmp_path / "D"), st2, cache_get) is True


def test_probe_force_retry_uses_separate_counter(tmp_path: Path):
    from filethumbs import FileThumbs
    FileThumbs._probe_counts.clear()
    FileThumbs._force_counts.clear()
    ft = FileThumbs(tmp_path / "cache")
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00" * 64)
    calls = []

    def failing_probe(p, px):
        calls.append(p)
        return None                                   # always fails

    r1 = ft._probe_cached(vid, 256, "video", failing_probe)                 # normal
    r2 = ft._probe_cached(vid, 256, "video", failing_probe, force=True)     # forced
    # old bug: the first normal attempt filled the >= 1 counter, so the
    # forced retry of the same run returned None without a real probe
    # (calls == 1)
    assert len(calls) == 2
    assert r1 is None and r2 is None
    ft._probe_counts.clear()
    ft._force_counts.clear()


# --------------------------------------------------- 5) English singular
def test_fix_en_singular_whitelist(en_lang):
    s = "Duration: 1 minutes · 👁 1 episodes watched · 27 titles · 10 episodes"
    assert fix_en_singular(s) == (
        "Duration: 1 minute · 👁 1 episode watched · 27 titles · 10 episodes")
    assert fix_en_singular("21 minutes") == "21 minutes"
    assert fix_en_singular("1 series") == "1 series"          # not in the whitelist
    assert fix_en_singular("") == ""


def test_format_duration_en_singular(en_lang):
    from media_info import format_duration
    assert format_duration(60) == "1 minute"
    assert format_duration(90) == "1 minute"
    assert format_duration(30) == "30 seconds"
    assert format_duration(3600) == "1 hour"


def test_fix_en_singular_is_noop_in_fa():
    set_lang("fa")
    assert fix_en_singular("1 minutes") == "1 minutes"        # Persian stays untouched
    set_lang("en")
