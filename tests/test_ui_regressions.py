# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for real-world usage bugs.

Each test pins one fixed bug, root-caused in code:
1. theme names still Persian in the English UI (4 missing i18n_en keys);
2. "Recent items" counted 15 but showed 9 — raw string path comparison +
   silently-orphaned recents; fixed by norm_path() + prune_recents();
3. the cover image vanished from the details file list and the card said
   1 file while details said 0 — cover no longer excluded, and
   gather_stats/get_file_tree share one visible-file rule;
4. a half-read book's ◐ badge said "5 seconds" instead of "Page 5";
5. the add-category dialog clipped its labels — minimum width now comes
   from the content's sizeHint (>= 420);
6. category colors are now user-settable (set_category_color).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import i18n as i18n_mod
from i18n import T
from scanner import gather_stats, get_file_tree
from store import Store, PALETTE

PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


@pytest.fixture()
def en_lang():
    prev = i18n_mod.lang()
    i18n_mod.set_lang("en")
    yield
    i18n_mod.set_lang(prev)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------- 1) theme names in the English UI
def test_every_theme_label_is_english_in_en_mode(en_lang):
    from qss import THEMES, theme_label
    for key in THEMES:
        shown = T(theme_label(key))
        assert not PERSIAN_RE.search(shown), (
            f"theme '{key}' still shows Persian in the English UI: {shown!r}")


def test_new_theme_labels_exact_english(en_lang):
    from qss import theme_label
    assert T(theme_label("forest")) == "Forest"
    assert T(theme_label("sunset")) == "Sunset"
    assert T(theme_label("ocean")) == "Ocean"
    assert T(theme_label("paper")) == "Paper"
    assert T(theme_label("dark")) == "Dark Blue"
    assert T(theme_label("dim")) == "Dark Gray"
    assert T(theme_label("amoled")) == "Pure Black (AMOLED)"
    assert T(theme_label("light")) == "Light"


def test_theme_labels_persian_in_fa_mode():
    from qss import theme_label
    prev = i18n_mod.lang()
    try:
        i18n_mod.set_lang("fa")
        assert T(theme_label("forest")) == "جنگل"
        assert T(theme_label("ocean")) == "اقیانوس"
    finally:
        i18n_mod.set_lang(prev)


# --------------------------------- 2) recent items — normalization + orphan pruning
def test_norm_path_collapses_form_variants():
    a = Store.norm_path("/lib/Movie A")
    assert a == Store.norm_path("/lib/Movie A/")        # trailing separator
    assert a == Store.norm_path("/lib/./Movie A")       # the "./" segment
    if sys.platform == "win32":
        assert Store.norm_path(r"C:\Lib\Movie A") == Store.norm_path(r"c:\lib\MOVIE a")


def test_add_recent_dedupes_equivalent_forms(store: Store):
    store.add_recent("/lib/Movie A")
    store.add_recent("/lib/Movie A/")                   # another shape of the same path
    store.add_recent("/lib/./Movie A")
    assert len(store.recents()) == 1


def test_prune_recents_removes_orphans_keeps_order(store: Store):
    live = [f"/lib/Item{i}" for i in range(9)]
    dead = [f"/gone/Item{i}" for i in range(6)]
    for p in (*live, *dead):                            # 15 items
        store.add_recent(p)
    assert len(store.recents()) == 15
    removed = store.prune_recents(live)
    assert removed == 6
    assert len(store.recents()) == 9
    # newest-first order preserved
    assert store.recents() == list(reversed(live))
    # real persistence — a reload from disk sees the same
    s2 = Store(Path(store.config_path))
    try:
        assert len(s2.recents()) == 9
    finally:
        s2.close()


def test_prune_recents_never_wipes_history_on_empty_scan(store: Store):
    for i in range(5):
        store.add_recent(f"/lib/Item{i}")
    assert store.prune_recents([]) == 0                 # empty scan = protected
    assert len(store.recents()) == 5


def test_recents_badge_count_matches_filter_math(store: Store):
    """The sidebar count = the number of recent items with a current title —
    keyed on the norm form. After orphans are pruned (what main does after
    every scan) the stored list and the counted number always match —
    "15 vs 9" can no longer happen."""
    live = [f"/lib/Item{i}" for i in range(9)]
    for p in (*live, "/gone/X"):
        store.add_recent(p)
    assert store.prune_recents(live) == 1                # orphan pruned
    paths = {store.norm_path(p) for p in live}
    matched = [p for p in store.recents() if store.norm_path(p) in paths]
    assert len(matched) == len(store.recents()) == 9


# ----------------------------------- 3) cover in the details list + consistent counting
def _tree_total(sections) -> int:
    return sum(len(s["files"]) for s in sections)


def test_folder_with_only_a_cover_counts_one_everywhere(tmp_path: Path):
    folder = tmp_path / "Interstellar"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    stats_files, _v, _sz, _tc = gather_stats(folder)
    tree = get_file_tree(folder, exclude=set())          # the details page's new behavior
    assert stats_files == 1
    assert _tree_total(tree) == 1
    assert tree[0]["files"][0].name == "poster.jpg"      # the cover must be shown


def test_hidden_and_system_files_never_counted(tmp_path: Path):
    folder = tmp_path / "Book Y"
    folder.mkdir()
    (folder / "cover.jpg").write_bytes(b"x")
    (folder / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    (folder / "thumbs.db").write_bytes(b"x")
    (folder / ".dotfile").write_bytes(b"x")
    assert gather_stats(folder)[0] == 1                  # only cover.jpg
    assert _tree_total(get_file_tree(folder)) == 1


def test_cover_plus_ten_files_equal_counts_card_vs_details(tmp_path: Path):
    folder = tmp_path / "Show Z"
    folder.mkdir()
    (folder / "poster.jpg").write_bytes(b"x")
    for i in range(10):
        (folder / f"ep{i:02d}.mp4").write_bytes(b"x")
    (folder / "desktop.ini").write_bytes(b"x")           # system file — excluded from both
    card_count = gather_stats(folder)[0]
    details_count = _tree_total(get_file_tree(folder))
    assert card_count == details_count == 11
    names = [f.name for s in get_file_tree(folder) for f in s["files"]]
    assert "poster.jpg" in names                         # the cover stays in the list


# ------------------------------------- 4) book ◐ badge: "Page N", not seconds
def test_book_badge_shows_pages_not_seconds(qapp, en_lang):
    from PySide6.QtGui import QPixmap
    from ui.details import ThumbCard
    card = ThumbCard(Path("/lib/One Piece v01.cbz"), QPixmap(), "1 KB")
    card.set_watch(Store.WATCH_PART, 5.0, page_based=True)
    txt = card._watch_badge.text()
    assert "Page" in txt and "5" in txt
    assert "ثانیه" not in txt and "second" not in txt.lower()
    card.deleteLater()


def test_book_badge_falls_back_to_bare_half_marker(qapp, en_lang):
    from PySide6.QtGui import QPixmap
    from ui.details import ThumbCard
    card = ThumbCard(Path("/lib/Manga v02.cbz"), QPixmap(), "1 KB")
    card.set_watch(Store.WATCH_PART, 0.0, page_based=True)
    assert card._watch_badge.text() == "◐"               # no number = bare ◐
    card.deleteLater()


def test_video_badge_keeps_resume_time(qapp, en_lang):
    from PySide6.QtGui import QPixmap
    from ui.details import ThumbCard
    card = ThumbCard(Path("/lib/movie.mp4"), QPixmap(), "1 KB")
    card.set_watch(Store.WATCH_PART, 5.0, page_based=False)
    txt = card._watch_badge.text()
    assert "Page" not in txt and "صفحه" not in txt       # video = time
    card.deleteLater()


def test_fa_mode_page_badge_is_persian(qapp):
    from PySide6.QtGui import QPixmap
    from ui.details import ThumbCard
    prev = i18n_mod.lang()
    try:
        i18n_mod.set_lang("fa")
        card = ThumbCard(Path("/lib/کتاب.pdf"), QPixmap(), "2 MB")
        card.set_watch(Store.WATCH_PART, 5.0, page_based=True)
        txt = card._watch_badge.text()
        assert "صفحه" in txt and "ثانیه" not in txt and "۵" in txt
        card.deleteLater()
    finally:
        i18n_mod.set_lang(prev)


# ---------------------------- 5) add-category dialog takes its width from the content
def test_category_dialog_min_width_follows_content(qapp, store: Store):
    from ui.dialogs import CategoryDialog
    dlg = CategoryDialog(store, create_only=True)
    try:
        assert dlg.minimumWidth() >= 420
        assert dlg.minimumWidth() >= dlg.sizeHint().width()
        dlg.deleteLater()
    finally:
        dlg.close()


# ------------------------------------- 6) custom category colors (new feature)
def test_set_category_color_roundtrip_persist_and_reset(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    try:
        s.ensure_category("فیلم")
        auto = s.color("فیلم")                           # palette color
        assert s.set_category_color("فیلم", "#3B82F6")   # user's choice
        assert s.color("فیلم") == "#3b82f6"              # normalized
        s2 = Store(tmp_path / "config.json")             # persistent save
        try:
            assert s2.color("فیلم") == "#3b82f6"
        finally:
            s2.close()
        # the subcategory is independent too
        s.ensure_category("فیلم/سریال‌های خارجی")
        assert s.color("فیلم/سریال‌های خارجی") == auto or \
            s.color("فیلم/سریال‌های خارجی").startswith("#")
        assert s.set_category_color("فیلم/سریال‌های خارجی", "#112233")
        assert s.color("فیلم/سریال‌های خارجی") == "#112233"
        assert s.color("فیلم") == "#3b82f6"              # parent untouched
        # back to automatic — the palette color of the moment (the palette
        # index can shift as the number of registered colors changes; what
        # matters is that it comes from the palette)
        assert s.set_category_color("فیلم", None)
        assert s.color("فیلم") in PALETTE
        # invalid input is rejected
        assert not s.set_category_color("فیلم", "#12345")
        assert not s.set_category_color("فیلم", "red")
        assert not s.set_category_color("", "#112233")
        assert not s.set_category_color(None, "#112233")
    finally:
        s.close()


def test_remove_category_also_drops_custom_color(store: Store):
    store.ensure_category("بازی")
    assert store.set_category_color("بازی", "#ff0000")
    store.remove_category("بازی")
    assert "بازی" not in (store.config.get("category_colors") or {})
