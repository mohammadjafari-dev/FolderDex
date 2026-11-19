# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for scanner.py — pure helpers + small filesystem probes.

No GUI, no Windows: everything runs against tmp_path.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from scanner import (  # noqa: E402
    MediaItem,
    fa_norm,
    fa_mmss,
    file_icon,
    file_type_group,
    find_any_image,
    find_desktop_ini_icon,
    find_poster_image,
    gather_stats,
    human_size,
    is_reserved_cover,
    natural_key,
)


# ------------------------------------------------------------ natural_key
def test_natural_key_orders_episodes_numerically():
    assert sorted(["E2", "E10", "E1"], key=natural_key) == ["E1", "E2", "E10"]
    assert sorted(["Season 2", "Season 10"], key=natural_key) == \
        ["Season 2", "Season 10"]


def test_natural_key_is_case_insensitive():
    assert natural_key("ABC") == natural_key("abc")


# ------------------------------------------------------------ human_size
def test_human_size_bytes():
    out = human_size(500)
    assert out.startswith("500")


def test_human_size_kb_mb_gb():
    assert human_size(10 * 1024)          # KB tier: no crash, non-empty
    assert human_size(5 * 1024 * 1024)    # MB tier
    assert human_size(3 * 1024 ** 3)      # GB tier


# ------------------------------------------------------------ fa helpers
def test_fa_mmss_formats_minutes_seconds():
    assert fa_mmss(139) in ("02:19", "۰۲:۱۹")


def test_fa_mmss_hours():
    assert fa_mmss(3603) in ("1:00:03", "۱:۰۰:۰۳")


def test_fa_mmss_invalid_is_zero():
    assert fa_mmss(-5) in ("00:00", "۰۰:۰۰")
    assert fa_mmss("x") in ("00:00", "۰۰:۰۰")


def test_fa_norm_unifies_arabic_and_persian_letters():
    assert fa_norm("سريال") == fa_norm("سریال")
    assert fa_norm("كتاب") == fa_norm("کتاب")


def test_fa_norm_removes_kashida_and_zwnj():
    assert fa_norm("می‌شود") == fa_norm("می شود")
    assert fa_norm("عـلـی") == fa_norm("علی")


# ------------------------------------------------------------ file_icon
def test_file_icon_groups():
    assert file_icon(Path("a.mkv")) == "🎬"
    assert file_icon(Path("a.mp3")) == "🎵"
    assert file_icon(Path("a.jpg")) == "🖼"
    assert file_icon(Path("a.zip")) == "🗜"
    assert file_icon(Path("a.pdf")) == "📄"
    assert file_icon(Path("a.py")) == "💻"
    assert file_icon(Path("a.exe")) == "⚙"
    assert file_icon(Path("a.unknownext")) == "📦"


# ------------------------------------------------------------ reserved covers
def test_is_reserved_cover_by_suffix_and_stem():
    assert is_reserved_cover(Path("x.ico"))
    assert is_reserved_cover(Path("poster.jpg"))
    assert is_reserved_cover(Path("Cover.PNG"))
    assert not is_reserved_cover(Path("holiday.jpg"))


# ------------------------------------------------------------ poster finding
def test_find_poster_image_prefers_poster_names(tmp_path: Path):
    (tmp_path / "cover.png").write_bytes(b"fake")
    assert find_poster_image(tmp_path) == tmp_path / "cover.png"


def test_find_poster_image_none_without_images(tmp_path: Path):
    (tmp_path / "movie.mkv").write_bytes(b"fake")
    assert find_poster_image(tmp_path) is None


def test_find_any_image_deep(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "art.webp").write_bytes(b"fake")
    assert find_any_image(tmp_path) is None          # shallow: subdir is ignored
    assert find_any_image(tmp_path, deep=True) == sub / "art.webp"


def test_find_desktop_ini_icon(tmp_path: Path):
    (tmp_path / "desktop.ini").write_text(
        "[.ShellClassInfo]\nIconResource=icon.ico,0", encoding="utf-8")
    (tmp_path / "icon.ico").write_bytes(b"fake")
    assert find_desktop_ini_icon(tmp_path) == tmp_path / "icon.ico"


# ------------------------------------------------------------ gather_stats
def test_gather_stats_counts_files_and_folders(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f1.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "f2.mkv").write_bytes(b"x" * 100)
    files, folders, size, types = gather_stats(tmp_path)
    assert files == 2
    assert folders == 1
    assert size == 105
    assert types["video"] == 1
    assert types["doc"] == 1


# ------------------------------------------------------------ file_type_group
def test_file_type_group_buckets():
    assert file_type_group("movie.mkv") == "video"
    assert file_type_group("notes.pdf") == "doc"
    assert file_type_group("whatever.bin") in ("other",)


# ------------------------------------------------------------ MediaItem
def test_media_item_defaults_are_safe():
    item = MediaItem(name="Some Movie", path="C:/x/Some Movie", category="Movies")
    assert item.file_count == 0
    assert item.size_bytes == 0
    assert item.category == "Movies"


# ------------------------------------------------------------ overlapping roots
def test_overlapping_roots_no_duplicates(tmp_path: Path):
    """Both D:\\Movies and D:\\Movies\\Marvel are roots → the Marvel card appears only once."""
    from scanner import scan_roots
    root = tmp_path / "root"
    (root / "Alpha Movie").mkdir(parents=True)
    (root / "Zip Movie").mkdir()
    (root / "Zip Movie" / "note.txt").write_text("x", encoding="utf-8")
    roots = [{"path": str(root)},
             {"path": str(root / "Zip Movie")}]
    items = scan_roots(roots)
    keys = [str(i.path) for i in items]
    assert len(keys) == len(set(keys)), f"duplicate paths in scan result: {keys}"
    # the progress count must not double-count either (counts mode)
    totals = []
    scan_roots(roots, counts=lambda done, total: totals.append(total))
    assert totals and totals[-1] <= len(items) + 2, \
        f"counts estimate wildly off: {totals} vs {len(items)} items"


# -------------------------------- compound tar suffixes + details-tree
# symlink guard

def test_archive_ext_of_compound_bundles():
    """book.tar.bz2 used to read as ".bz2" (Path.suffix) and fell out of every
    archive check — the compound tar bundles must be matched by their full
    suffix, ordinary names must be unchanged."""
    from scanner import archive_ext_of
    assert archive_ext_of("book.tar.bz2") == ".tar.bz2"
    assert archive_ext_of("D:/lib/x.tar.gz") == ".tar.gz"
    assert archive_ext_of("manga.tar.xz") == ".tar.xz"
    assert archive_ext_of("pics.tar.zst") == ".tar.zst"
    assert archive_ext_of("log.tar.lz4") == ".tar.lz4"
    # unchanged behavior for everything else
    assert archive_ext_of("a.zip") == ".zip"
    assert archive_ext_of("b.tar") == ".tar"
    assert archive_ext_of("c.TGZ") == ".tgz"
    assert archive_ext_of("no_ext") == ""
    assert archive_ext_of(Path("n/c.tar.bz2")) == ".tar.bz2"


def test_file_type_group_and_icon_see_tar_bundles():
    from scanner import file_icon, file_type_group
    assert file_type_group("book.tar.bz2") == "archive"
    assert file_type_group("x.tar.lz4") == "archive"
    assert file_icon(Path("book.tar.bz2")) == "🗜"


def test_tar_bundles_are_archive_members_and_preview_candidates(tmp_path: Path):
    """A .tar.bz2 folder member must be picked like a plain .tar.gz for the
    folder-cover fallback chain."""
    from scanner import _arc_entry, find_preview_archive
    (tmp_path / "book.tar.bz2").write_bytes(b"x")
    assert [p.name for p in _arc_entry(tmp_path)] == ["book.tar.bz2"]
    found = find_preview_archive(tmp_path)
    assert found is not None and found.name == "book.tar.bz2"


def _make_symlink(target: Path, link: Path) -> bool:
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False        # Windows without developer mode / unsupported fs


def test_get_file_hierarchy_does_not_follow_dir_symlinks(tmp_path: Path):
    """The details tree must obey the main scan's followlinks=False: a folder
    symlink must not leak outside-of-collection files into the tree (a leaked
    tree would list the linked folder's private.txt)."""
    from scanner import get_file_hierarchy
    coll = tmp_path / "collection"
    (coll / "season1").mkdir(parents=True)
    (coll / "season1" / "ep1.mkv").write_text("v", encoding="utf-8")
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "private.txt").write_text("secret", encoding="utf-8")
    if not _make_symlink(secret, coll / "link"):
        pytest.skip("directory symlinks unsupported here (Windows/FS)")
    tree = get_file_hierarchy(coll)
    names = [d["path"].name for d in tree["dirs"]]
    assert "link" not in names, "folder symlink must not be walked"
    all_files = [str(p) for p in
                 [f for node in _iter_nodes(tree) for f in node["files"]]]
    assert not any("private.txt" in f for f in all_files), \
        "files from outside the collection must not appear in the tree"
    assert any(f.endswith("ep1.mkv") for f in all_files)


def test_get_file_hierarchy_survives_symlink_cycle(tmp_path: Path):
    from scanner import count_hierarchy_files, get_file_hierarchy
    coll = tmp_path / "collection"
    coll.mkdir()
    (coll / "a.mkv").write_text("v", encoding="utf-8")
    if not _make_symlink(coll, coll / "loop"):
        pytest.skip("directory symlinks unsupported here (Windows/FS)")
    tree = get_file_hierarchy(coll)      # must terminate and not explode
    assert count_hierarchy_files(tree) == 1


def _iter_nodes(node: dict):
    yield node
    for d in node.get("dirs", []):
        yield from _iter_nodes(d)
