# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for store.py — the SQLite layer, on a temp DB.

Everything runs against tmp_path: config.json, data.db and quarantine
behaviour are all exercised with no GUI and no Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import store as store_mod  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


# ------------------------------------------------------------ basic config
def test_new_store_writes_default_config(tmp_path: Path):
    cfg = tmp_path / "config.json"
    s = Store(cfg)
    try:
        assert cfg.is_file()
        assert (tmp_path / "data.db").is_file()
        assert s.categories() == []
        assert s.roots() == []
    finally:
        s.close()


def test_roots_round_trip(store: Store):
    roots = [{"path": "D:/Movies", "category": "Movies"}]
    store.set_roots(roots)
    assert store.roots() == roots


# ------------------------------------------------------------ categories
def test_category_and_color_round_trip(store: Store):
    store.ensure_category("Movies")
    assert "Movies" in store.categories()
    store.config["category_colors"]["Movies"] = "#a78bfa"
    store.save_config()
    assert store.color("Movies") == "#a78bfa"


def test_category_auto_registered_by_set_meta(store: Store):
    store.set_category("C:/x/Movie A", "Series")
    assert "Series" in store.categories()
    assert store.meta("C:/x/Movie A")["category"] == "Series"


def test_remove_category_only_when_unused(store: Store):
    store.ensure_category("Empty")
    store.ensure_category("Used")
    store.set_category("C:/x/Movie B", "Used")
    store.remove_category("Empty")
    assert "Empty" not in store.categories()
    store.remove_category("Used")          # in use -> stays
    assert "Used" in store.categories()


def test_matches_includes_subcategories():
    assert Store.matches("Movies/Anime", "Movies")
    assert not Store.matches("Movies", "Movies/Anime")


# ------------------------------------------------------------ metadata
def test_meta_round_trip_and_persistence(tmp_path: Path):
    cfg = tmp_path / "config.json"
    s1 = Store(cfg)
    try:
        s1.set_meta("C:/x/Movie A", rating=4, watched=True)
        s1.set_meta("C:/x/Movie A", favorite=True)
        meta = s1.meta("C:/x/Movie A")
        assert meta["rating"] == 4
        assert meta["watched"] is True
        assert meta["favorite"] is True
    finally:
        s1.close()

    s2 = Store(cfg)                        # reopen -> data survives
    try:
        meta = s2.meta("C:/x/Movie A")
        assert meta["rating"] == 4
        assert meta["favorite"] is True
    finally:
        s2.close()


def test_set_meta_empty_values_remove_keys(store: Store):
    store.set_meta("C:/x/Movie A", rating=4, note="hi")
    store.set_meta("C:/x/Movie A", rating=None)
    meta = store.meta("C:/x/Movie A")
    assert "rating" not in meta
    assert meta["note"] == "hi"


def test_meta_removed_when_empty_again(store: Store):
    store.set_meta("C:/x/Temp", note="x")
    store.set_meta("C:/x/Temp", note=None)     # last key gone -> item dropped
    assert store.meta("C:/x/Temp") == {}


# ------------------------------------------------------------ defer_save
def test_defer_save_batches_writes(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    try:
        with s.defer_save():
            s.set_meta("C:/x/A", rating=1)
            s.set_meta("C:/x/B", rating=2)
        # after the block both items are flushed to SQLite
        s2 = Store(tmp_path / "config.json")
        try:
            assert s2.meta("C:/x/A")["rating"] == 1
            assert s2.meta("C:/x/B")["rating"] == 2
        finally:
            s2.close()
    finally:
        s.close()


# ------------------------------------------------------------ self-healing DB
def test_corrupt_db_is_quarantined_and_store_survives(tmp_path: Path):
    (tmp_path / "data.db").write_bytes(b"definitely not sqlite" * 32)
    s = Store(tmp_path / "config.json")
    try:
        assert s.db_quarantined, "corrupt db should be quarantined"
        assert s.data["items"] == {}
        assert s.meta("C:/x/Nothing") == {}
        # and the store is fully usable afterwards
        s.set_meta("C:/x/New", rating=5)
        assert s.meta("C:/x/New")["rating"] == 5
    finally:
        s.close()
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert any(n.startswith("data.db.corrupt-") for n in leftovers)


# ------------------------------------------------------------ ui_clean
def test_ui_clean_strips_control_chars():
    from store import ui_clean
    assert ui_clean("a\x1b[31mb\n\tc  d") == "ab c d"
    assert ui_clean("a\x00b\nc  d") == "ab c d"
    assert ui_clean(None) == ""


# ------------------------------------------------------------ silent DB save failure
def test_save_data_failure_sets_flag_and_keeps_dirty(store: Store,
                                                     monkeypatch):
    """SQLite failure on flush: the save_failed flag goes on and paths stay dirty.

    Previously the error was swallowed silently — the data looked saved and
    watch state/rating/cover were lost when the app closed."""
    import sqlite3

    store.set_meta("C:/x/A", rating=3)          # successful flush
    assert store.save_failed is False

    class _BoomExec:
        def __init__(self, con):
            self._con = con

        def executemany(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def __getattr__(self, name):
            return getattr(self._con, name)

    real_con = store._con
    monkeypatch.setattr(store, "_con", _BoomExec(real_con))
    store.set_meta("C:/x/B", rating=5)          # the flush fails right here
    assert store.save_failed is True
    monkeypatch.undo()

    # the failed path is still dirty → the next flush really writes it
    # (the dirty key is always the canonical Path form — on Windows
    # str(Path("C:/x/B")) becomes backslashed; the comparison must use the
    # same _meta_key, otherwise the test failed on Windows and passed on Linux)
    assert store_mod._meta_key("C:/x/B") in store._dirty
    store.save_data()
    assert store.save_failed is False
    # and it is read back from the real database too (not memory-only)
    store2 = Store(store.config_path)
    try:
        assert store2.meta("C:/x/B").get("rating") == 5
    finally:
        store2.close()


def test_save_failed_flag_clears_on_success(store: Store):
    """After a successful flush the failure flag clears (the status bar clears)."""
    store.save_failed = True                    # simulate a previous failure
    store.set_meta("C:/x/A", rating=2)
    assert store.save_failed is False


# ------------------------------------------------------------ raw-key fallback
def test_set_total_durations_reads_raw_key_manifests(tmp_path: Path, monkeypatch):
    """The raw-key fallback actually runs.

    Previously path was rewritten with _meta_key before the fallback, so both
    lookups were identical — the fallback never ran and a manifest built with
    raw keys was silently lost. On Linux both forms are equal; _meta_key is
    faked here to canonicalize like Windows does (/ → \\)."""
    p = "D:/Movies/It"
    monkeypatch.setattr(store_mod, "_meta_key",
                        lambda x: str(x).replace("/", "\\"))
    s = Store(tmp_path / "config.json")
    try:
        s.set_total_durations({p: 42}, {p: {"a.mp4": [1, 2]}})
        m = s.meta(p)
        assert m.get("total_dur") == 42
        assert m.get("vids") == {"a.mp4": [1, 2]}
    finally:
        s.close()
