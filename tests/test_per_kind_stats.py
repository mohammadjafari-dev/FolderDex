# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for the explicit per-kind sidebar stats.

Spec: per-kind sidebar stats — a video category shows the watched
percentage of its videos and a book category the reading percentage of its
books; a category can have none of that (games), one of them, or both, and
the two percentages are written separately.

1) A category with neither videos nor books (Games) has NO percentage lines —
   its marks live in the hidden "other" bucket.
2) A video category shows the watch percentage of its CURRENT videos only —
   a deleted episode's stale mark and a poster sidecar never count.
3) A book category shows a separate reading percentage; comics (cbz) count
   as books even though they are archive files.
4) A mixed category gets two independent denominators — a pdf mark never
   leaks into the video counter and a game mark never leaks into either.
5) Orphan marks (file deleted after being marked) never count — the "11
   items of 26" / >100% artifacts are impossible.
6) The "none" (no tracking) profile hides everything, even for video titles.
7) EN i18n keys exist for the new sidebar lines (test 8 exercises the real
   _update_stats wording offscreen; it runs only where PySide6 is installed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from i18n_en import EN  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


class _Item:
    """Minimal item stand-in (same shape as the sibling test modules)."""

    def __init__(self, path, video_count=0, category=""):
        self.path = str(path)
        self.video_count = video_count
        self.category = category


# ------------------------------------- 1) a category with nothing to track
def test_category_without_videos_or_books_has_no_percent_lines(store, tmp_path):
    d = tmp_path / "LOST REALMS"
    d.mkdir()
    (d / "game.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    (d / "save.exe").write_bytes(b"MZ" + b"\x00" * 32)
    it = _Item(d, video_count=0, category="Games")
    store.watch_set(it, "game.zip", Store.WATCH_DONE, 0.0)

    assert store.progress_kind(it) == "files"      # based on file kind
    prog = store.scope_progress_by_kind([it])
    assert prog["video"][2] == 0                   # no video line
    assert prog["book"][2] == 0                    # no book line
    assert prog["other"] == (1, 0, 2)              # mark stays hidden (zip+exe) — no fake percentage


# ------------------------------------- 2) video category: current files only
def test_video_category_counts_current_videos_only(store, tmp_path):
    d = tmp_path / "COSMIC DRIFT"
    d.mkdir()
    (d / "a.mp4").write_bytes(b"\x00" * 64)
    (d / "b.mp4").write_bytes(b"\x00" * 64)
    (d / "poster.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 32)
    it = _Item(d, video_count=3, category="Movies")   # stale denominator from the previous scan: 3
    store.watch_set(it, "a.mp4", Store.WATCH_DONE, 0.0)
    store.watch_set(it, "deleted.mp4", Store.WATCH_DONE, 0.0)   # deleted episode
    store.watch_set(it, "poster.jpg", Store.WATCH_DONE, 0.0)    # sidecar

    prog = store.scope_progress_by_kind([it])
    # the denominator comes from the current files (2), not the stale
    # video_count (3); the deleted episode and the poster sidecar don't
    # count -> 1 of 2 (50%)
    assert prog["video"] == (1, 0, 2)
    assert prog["book"] == (0, 0, 0)


def test_video_category_zero_percent_line_still_exists(store, tmp_path):
    # No episode had been watched and the whole line
    # disappeared; now 0% is shown explicitly too (total > 0 is enough)
    d = tmp_path / "Sample Clips"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"\x00" * 64)
    it = _Item(d, video_count=1, category="Movies")
    prog = store.scope_progress_by_kind([it])
    assert prog["video"] == (0, 0, 1)


# ------------------------------------- 3) book category: separate read percent
def test_book_category_shows_separate_read_percent(store, tmp_path):
    d = tmp_path / "Moth City"
    d.mkdir()
    (d / "chapter1.cbz").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    (d / "chapter2.cbz").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    (d / "novel.pdf").write_bytes(b"%PDF-1.4 " + b"\x00" * 32)
    it = _Item(d, video_count=0, category="Comics")
    store.watch_set(it, "chapter1.cbz", Store.WATCH_DONE, 0.0)
    store.watch_set(it, "novel.pdf", Store.WATCH_PART, 9.0)

    assert store.progress_kind(it) == "book"       # "Comics" -> guessed as books
    prog = store.scope_progress_by_kind([it])
    assert prog["book"] == (1, 1, 3)               # compressed comics count as "books" too
    assert prog["video"] == (0, 0, 0)              # never shows up in the video line


# ------------------------------------- 4) mixed category: two denominators
def test_mixed_category_has_two_independent_denominators(store, tmp_path):
    docs = tmp_path / "Learning"
    docs.mkdir()
    (docs / "guide.pdf").write_bytes(b"%PDF" + b"\x00" * 32)
    (docs / "wallpaper.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 32)
    vids = tmp_path / "Sample Clips"
    vids.mkdir()
    (vids / "clip.mp4").write_bytes(b"\x00" * 64)
    game = tmp_path / "LOST REALMS"
    game.mkdir()
    (game / "build.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    it_doc = _Item(docs, video_count=0, category="Mixed")
    it_vid = _Item(vids, video_count=1, category="Mixed")
    it_game = _Item(game, video_count=0, category="Mixed")
    store.watch_set(it_doc, "guide.pdf", Store.WATCH_DONE, 0.0)
    store.watch_set(it_doc, "wallpaper.jpg", Store.WATCH_PART, 0.0)
    store.watch_set(it_vid, "clip.mp4", Store.WATCH_PART, 4.0)
    store.watch_set(it_game, "build.zip", Store.WATCH_DONE, 0.0)

    prog = store.scope_progress_by_kind([it_doc, it_vid, it_game])
    assert prog["video"] == (0, 1, 1)    # pdf and game marks never reach the watch counter
    assert prog["book"] == (1, 0, 1)     # the book line is independent
    assert prog["image"] == (0, 1, 1)    # image is separate (shown only when it carries a mark)
    assert prog["other"] == (1, 0, 1)    # games appear in no line


# ------------------------------------- 5) orphan marks never count
def test_orphan_marks_never_count(store, tmp_path):
    d = tmp_path / "Books"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"%PDF" + b"\x00" * 32)
    it = _Item(d, video_count=0, category="Books")
    store.watch_set(it, "a.pdf", Store.WATCH_DONE, 0.0)
    store.watch_set(it, "deleted.pdf", Store.WATCH_DONE, 0.0)   # orphan

    prog = store.scope_progress_by_kind([it])
    assert prog["book"] == (1, 0, 1)     # no "2 of 1", no above-100%


# ------------------------------------- 6) «none» profile hides everything
def test_none_profile_hides_everything(store, tmp_path):
    d = tmp_path / "SAVES BACKUP"
    d.mkdir()
    (d / "save.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    it = _Item(d, video_count=0, category="Games")
    store.set_cat_track_profile("Games", "none")
    store.watch_set(it, "save.zip", Store.WATCH_DONE, 0.0)

    assert store.progress_kind(it) == "none"
    assert store.watch_stats_by_kind(it) == {
        "video": (0, 0, 0), "book": (0, 0, 0),
        "image": (0, 0, 0), "other": (0, 0, 0),
    }

    # even a video title contributes nothing under "no tracking"
    it2 = _Item(tmp_path / "NORTH SHORE", video_count=2, category="Games")
    store.watch_set(it2, "ep1.mp4", Store.WATCH_DONE, 0.0)
    assert store.progress_kind(it2) == "none"
    assert store.watch_stats_by_kind(it2)["video"] == (0, 0, 0)


# ------------------------------------- 7) explicit video-profile assignment
def test_explicit_video_profile_buckets_everything_as_video(store, tmp_path):
    # a category assigned to the "video" profile: all of
    # its consumable content lands in the video bucket (even a visual comic
    # inside) — that is the profile's meaning
    d = tmp_path / "Mixed Title"
    d.mkdir()
    (d / "ep1.mp4").write_bytes(b"\x00" * 64)
    it = _Item(d, video_count=0, category="Films")
    store.set_cat_track_profile("Films", "video")
    store.watch_set(it, "ep1.mp4", Store.WATCH_DONE, 0.0)

    assert store.progress_kind(it) == "video"
    prog = store.scope_progress_by_kind([it])
    assert prog["video"] == (1, 0, 1)


# ------------------------------------- 8) EN wording keys
def test_explicit_i18n_keys_exist():
    for k in ("👁 ویدیوها: {0} از {1} دیده‌شده ({2}٪)",
              " · ◐ {0} در حال تماشا",
              "📖 کتاب‌ها: {0} از {1} خوانده‌شده ({2}٪)",
              "🖼 تصاویر: {0} از {1} ({2}٪)",
              "🎲 امشب چی بخونم؟"):
        assert k in EN, f"missing EN key: {k}"


# ------------------------------------- 9) the real _update_stats wording (Qt)
@pytest.fixture()
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_update_stats_lines_by_scope(qapp, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")

    from i18n import set_lang
    from scanner import scan_roots
    from thumbs import ThumbnailCache
    from ui.library import LibraryPage

    set_lang("en")   # same procedure as the sibling tests: English UI strings

    # --- miniature library: Games (nothing) + Movies (video) + Books (books)
    games = tmp_path / "GamesRoot"
    games.mkdir()
    (games / "LOST REALMS").mkdir()
    (games / "LOST REALMS" / "game.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    movies = tmp_path / "MoviesRoot"
    movies.mkdir()
    (movies / "COSMIC DRIFT").mkdir()
    (movies / "COSMIC DRIFT" / "a.mp4").write_bytes(b"\x00" * 64)
    (movies / "COSMIC DRIFT" / "b.mp4").write_bytes(b"\x00" * 64)
    books = tmp_path / "BooksRoot"
    books.mkdir()
    (books / "Atlas").mkdir()
    (books / "Atlas" / "atlas.pdf").write_bytes(b"%PDF" + b"\x00" * 32)
    (books / "Atlas" / "chapter1.cbz").write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    store = Store(tmp_path / "config.json")
    store.set_roots([{"path": str(games), "category": "Games"},
                     {"path": str(movies), "category": "Movies"},
                     {"path": str(books), "category": "Books"}])
    items = scan_roots(store.roots(), status={})
    assert {i.category for i in items} == {"Games", "Movies", "Books"}

    page = LibraryPage(store, ThumbnailCache(tmp_path / "cache"))
    try:
        page.items = items

        # Games — no percentage lines
        page.filter_kind, page.filter_cat = "cat", "Games"
        page._update_stats()
        t = page.stats.text()
        assert "watched" not in t and "read" not in t and "Images" not in t, \
            f"Games must have no percent lines:\n{t}"

        # Movies — explicit video line, even at 0%
        page.filter_kind, page.filter_cat = "cat", "Movies"
        page._update_stats()
        t = page.stats.text()
        assert "👁 Videos: 0 of 2 watched (0%)" in t, f"Movies line:\n{t}"

        # Books — the book line only
        page.filter_kind, page.filter_cat = "cat", "Books"
        page._update_stats()
        t = page.stats.text()
        assert "📖 Books: 0 of 2 read (0%)" in t, f"Books line:\n{t}"
        assert "watched" not in t, f"Books must not show a video line:\n{t}"

        # All — both independent lines, no mixed line
        page.filter_kind, page.filter_cat = "all", ""
        page._update_stats()
        t = page.stats.text()
        assert "👁 Videos: 0 of 2 watched (0%)" in t, f"All video line:\n{t}"
        assert "📖 Books: 0 of 2 read (0%)" in t, f"All book line:\n{t}"
        assert "items completed" not in t, f"the mixed items line must be gone:\n{t}"

        # Persian — the same lines with the equivalent wording
        set_lang("fa")
        page.filter_kind, page.filter_cat = "cat", "Movies"
        page._update_stats()
        t = page.stats.text()
        assert "ویدیوها: ۰ از ۲ دیده\u200cشده (۰٪)" in t, f"Movies fa line:\n{t}"
        page.filter_kind, page.filter_cat = "cat", "Games"
        page._update_stats()
        t = page.stats.text()
        assert "دیده\u200cشده (" not in t and "خوانده\u200cشده (" not in t, \
            f"Games fa must have no percent lines:\n{t}"
    finally:
        set_lang("en")
        page.deleteLater()
        store.close()
