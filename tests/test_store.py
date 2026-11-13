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


# ------------------------------------------------------------ C8 — شکستِ ذخیره‌ی دیتابیس بی‌صدا نماند
def test_save_data_failure_sets_flag_and_keeps_dirty(store: Store,
                                                     monkeypatch):
    """شکستِ SQLite در فلاش: پرچمِ save_failed روشن و مسیرها dirty می‌مانند (C8).

    قبلاً خطا فقط بی‌صدا بلعیده می‌شد — کاربر فکر می‌کرد ذخیره شده و
    تماشا/امتیاز/کاور با بستنِ برنامه از دست می‌رفت."""
    import sqlite3

    store.set_meta("C:/x/A", rating=3)          # فلاشِ موفق
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
    store.set_meta("C:/x/B", rating=5)          # فلاشِ همین جا شکست می‌خورد
    assert store.save_failed is True
    monkeypatch.undo()

    # مسیرِ شکست‌خورده هنوز dirty است → فلاشِ بعدی واقعاً می‌نویسد
    # (round 17: کلیدِ dirty همیشه «فرمِ کانونیِ Path» است — روی ویندوز
    # str(Path("C:/x/B")) بک‌اسلش می‌شود؛ مقایسه باید با همان _meta_key باشد
    # وگرنه روی ویندوز قرمز می‌شد، روی لینوکس سبز — دقیقاً همان دامِ round 12)
    assert store_mod._meta_key("C:/x/B") in store._dirty
    store.save_data()
    assert store.save_failed is False
    # و از دیتابیسِ واقعی هم خوانده می‌شود (فقط حافظه نمانده)
    store2 = Store(store.config_path)
    try:
        assert store2.meta("C:/x/B").get("rating") == 5
    finally:
        store2.close()


def test_save_failed_flag_clears_on_success(store: Store):
    """بعد از یک فلاشِ موفق، پرچمِ شکست خاموش می‌شود (نوار وضعیت پاک می‌شود)."""
    store.save_failed = True                    # شبیه‌سازیِ شکستِ قبلی
    store.set_meta("C:/x/A", rating=2)
    assert store.save_failed is False


# ------------------------------------------------------------ round 20 — F2
def test_set_total_durations_reads_raw_key_manifests(tmp_path: Path, monkeypatch):
    """(ممیزیِ F2) fallbackِ «کلیدِ خام» واقعاً اجرا می‌شود.

    قبلاً ‎path‎ پیش از fallback با ‎_meta_key‎ بازنویسی می‌شد و هر دو lookup
    یکی بودند — fallback هرگز اجرا نمی‌شد و مانیفستِ فراخوانی که با کلیدِ
    خام می‌سازد بی‌صدا گم می‌شد. روی لینوکس دو فرم یکی‌اند؛ ‎_meta_key‎
    قلابی می‌شود تا مثل ویندوز کانونی‌سازی کند (‎/‎ → ‎\\‎)."""
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
