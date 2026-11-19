# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests — watch-progress basis, recents dedupe, db quarantine.

1) The >100% sidebar bug: watch_progress must count only
   the records that belong to the item's watchable_total basis (video title
   -> video files only; non-video title -> all watchables). The sidebar
   aggregation uses the same basis and the percentage is clamped to 0..100.
2) prune_recents: legacy duplicate spellings dedupe + protected paths so a
   partial multi-root scan (offline drive) cannot wipe that root's history.
3) _open_db: quarantine ONLY on real corruption - a busy/locked healthy db
   must never be moved aside (that would be silent data loss).
4) Imported config sanitization: auto_backup is dropped, UNC roots rejected.
5) scan_roots status: reports which roots actually scanned ok/failed.
6) Archive extraction hard cap: _copy_capped stops oversized members.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from store import Store, _db_is_corrupt, _open_db, _rel_is_video  # noqa: E402
from backup import _sanitize_imported_config  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


class _Item:
    """Minimal item stand-in: watch_map/watch_progress need only these."""

    def __init__(self, path, video_count=0):
        self.path = path
        self.video_count = video_count


# --------------------------------------------------- 1) the "130%" bug
def test_watch_progress_video_item_ignores_poster_and_docs(store: Store):
    it = _Item("D:\\Lib\\Movie A", video_count=2)
    store.watch_set(it, "S01E01.mkv", Store.WATCH_DONE, 0.0)
    store.watch_set(it, "poster.jpg", Store.WATCH_DONE, 0.0)   # W sidecar record
    store.watch_set(it, "book.pdf", Store.WATCH_PART, 5.0)
    # poster/pdf records are NOT episodes -> 1/2 episodes, never >100%
    assert store.watch_progress(it) == (1, 0)


def test_watch_progress_non_video_item_counts_its_watchables(store: Store):
    it = _Item("D:\\Lib\\Book B", video_count=0)
    store.watch_set(it, "poster.jpg", Store.WATCH_DONE, 0.0)
    store.watch_set(it, "book.pdf", Store.WATCH_PART, 9.0)
    assert store.watch_progress(it) == (1, 1)


def test_rel_is_video_suffix_rule():
    assert _rel_is_video("Season 1/S01E02.mp4") is True
    assert _rel_is_video("poster.jpg") is False
    assert _rel_is_video("noext") is False


# --------------------------------------------------- 2) recents
def test_prune_recents_dedupes_legacy_duplicates(store: Store):
    # legacy config stored the same path twice in different
    # spellings; prune must keep the newest spelling only.
    store.config["recents"] = ["/lib/Movie/", "/lib/Movie2", "/lib/Movie"]
    removed = store.prune_recents(["/lib/Movie", "/lib/Movie2"])
    assert removed == 1
    assert store.recents() == ["/lib/Movie/", "/lib/Movie2"]


def test_prune_recents_protected_keeps_offline_root_history(store: Store):
    store.add_recent("/ok/A")
    store.add_recent("/off/B")
    # scan succeeded only for /ok -> /off history must survive
    assert store.prune_recents(["/ok/A"], protected=["/off/B"]) == 0
    assert store.recents() == ["/off/B", "/ok/A"]   # newest first, both kept
    # without protection the same call removes the orphan
    assert store.prune_recents(["/ok/A"]) == 1
    assert store.recents() == ["/ok/A"]


# --------------------------------------------------- 3) db quarantine
def test_open_db_quarantines_garbage_but_not_locked_db(tmp_path: Path):
    db = tmp_path / "data.db"
    db.write_bytes(b"this is definitely not a sqlite database file" * 4)
    con, moved = _open_db(db)
    try:
        assert moved, "a garbage file must be quarantined"
    finally:
        con.close()

    # healthy db held by an EXCLUSIVE lock: _open_db must fail WITHOUT
    # moving the file (the old catch-all quarantine lost real data)
    db2 = tmp_path / "data2.db"
    holder = sqlite3.connect(str(db2))
    holder.execute("CREATE TABLE t (x)")
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("INSERT INTO t VALUES (1)")
    before = db2.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        _open_db(db2)
    assert db2.read_bytes() == before
    assert not list(tmp_path.glob("data2.db.corrupt-*"))
    holder.rollback()
    holder.close()


def test_db_corrupt_classification():
    assert _db_is_corrupt(sqlite3.DatabaseError("file is not a database"))
    assert _db_is_corrupt(sqlite3.DatabaseError(
        "database disk image is malformed"))
    assert _db_is_corrupt(sqlite3.DatabaseError("quick_check failed: non-ok"))
    # busy / locked / readonly are NOT corruption - quarantine is forbidden
    assert not _db_is_corrupt(sqlite3.OperationalError("database is locked"))
    assert not _db_is_corrupt(sqlite3.OperationalError(
        "attempt to write a readonly database"))
    if hasattr(sqlite3, "SQLITE_BUSY"):
        e = sqlite3.OperationalError("database is locked")
        e.sqlite_errorcode = sqlite3.SQLITE_BUSY
        assert not _db_is_corrupt(e)
    if hasattr(sqlite3, "SQLITE_NOTADB"):
        e = sqlite3.DatabaseError("not an error")
        e.sqlite_errorcode = sqlite3.SQLITE_NOTADB
        assert _db_is_corrupt(e)


# --------------------------------------------------- 4) import sanitization
def test_sanitize_neutralizes_auto_backup_and_unc_roots(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "auto_backup": {"enabled": True, "path": "\\\\attacker\\share"},
        "roots": [{"path": "D:\\Lib", "category": "Movies"},
                  {"path": "\\\\srv\\share", "category": "Net"}],
        "player": {"mode": "custom", "path": "C:\\evil.exe"},
    }, ensure_ascii=False), encoding="utf-8")
    assert _sanitize_imported_config(p) is True
    cfg = json.loads(p.read_text(encoding="utf-8"))
    assert "auto_backup" not in cfg
    assert [r["path"] for r in cfg["roots"]] == ["D:\\Lib"]
    assert cfg["player"] == {"mode": "auto", "path": ""}


def test_sanitize_keeps_clean_config_intact(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"theme": "dark", "roots": []}),
                 encoding="utf-8")
    assert _sanitize_imported_config(p) is True
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "theme": "dark", "roots": []}


# --------------------------------------------------- 5) scan_roots status
def test_scan_roots_reports_ok_and_failed_roots(tmp_path: Path):
    from scanner import scan_roots
    r1 = tmp_path / "Movies"
    (r1 / "Movie A").mkdir(parents=True)
    (r1 / "Movie A" / "file.mp4").write_bytes(b"\x00" * 64)
    (r1 / "Movie A" / "poster.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 32)
    missing = tmp_path / "Gone"
    status = {}
    items = scan_roots([{"path": str(r1), "category": "Movies"},
                        {"path": str(missing), "category": "Gone"}],
                       status=status)
    assert status["ok"] == [str(r1)]
    assert status["failed"] == [str(missing)]
    assert len(items) >= 1


# --------------------------------------------------- 6) extraction cap
def test_copy_capped_stops_oversized_member(tmp_path: Path):
    from filethumbs import _copy_capped
    out = tmp_path / "ok.bin"
    with open(out, "wb") as d:
        assert _copy_capped(io.BytesIO(b"x" * 10), d, cap=500) == 10
    big = tmp_path / "big.bin"
    with pytest.raises(OSError):
        with open(big, "wb") as d:
            _copy_capped(io.BytesIO(b"x" * 1000), d, cap=500)
    # nothing beyond the cap may have hit the disk
    assert big.stat().st_size <= 768   # <= one chunk past the cap


# ------------------------------------- 7) the >100% progress replay
#
# Stats tooltip + pixel-level component analysis:
#   "📊 Library stats / 🎬 27 titles · 10 episodes / 💢 Size: 5.2 MB /
#     👁 13 episodes watched (130%) · ◐ 4 in progress" (Persian original)
# The library is the imported demo library: 10 real video episodes and a
# watch map carrying 13 DONE records (2 episodes + 9 posters + 1 pdf +
# 1 archive) and 4 PART records (2 episodes + 1 poster + 1 pdf).
# Pre-fix code summed every record -> 13 "episodes" / 10 = 130%.
# Fixed code must count video episodes only for video titles and skip
# non-video titles in the aggregate -> 2 done, 2 part, 20%.
DEMO_WATCH = {
    # video titles (video_count > 0)
    ("HOLLOW CREEK", 2): [("Season 1/S01E01.mp4", 1), ("Season 1/S01E02.mp4", 1)],
    ("STARFALL ACADEMY", 3): [("Season 1/S01E01.mp4", 2)],
    ("SKY DRIFTERS", 2): [("Season 1/EP01.mp4", 2)],
    ("NORTH SHORE", 1): [],
    ("COSMIC DRIFT - Teaser", 1): [],
    ("Sample Clips", 1): [],
    # non-video titles (poster/pdf/archive based tracking)
    ("NEON HARBOR", 0): [("poster.jpg", 1)],
    ("THE LAST LANTERN", 0): [("poster.jpg", 1)],
    ("MIDNIGHT FREQUENCY", 0): [("poster.jpg", 1)],
    ("GLASS OCEAN", 0): [("poster.jpg", 1)],
    ("THE SILENT COURT", 0): [("poster.jpg", 1)],
    ("SAKURA SIGNAL", 0): [("poster.jpg", 1)],
    ("LOST REALMS", 0): [("lost-realms-build.7z", 1), ("poster.jpg", 1)],
    ("NEON DRIFT", 0): [("poster.jpg", 1)],
    ("The Cartographer's Daughter", 0): [("The Cartographer's Daughter.pdf", 1),
                                         ("poster.jpg", 1)],
    ("DEEP SIGNAL", 0): [("poster.jpg", 2)],
    ("Stargazer's Atlas", 0): [("Stargazer's Atlas.pdf", 2)],
}


def test_progress_over_100_scenario_replay(store: Store):
    items = []
    for (name, vc), recs in DEMO_WATCH.items():
        it = _Item("D:\\Library-Demo\\demo-library\\" + name, video_count=vc)
        for rel, m in recs:
            store.watch_set(it, rel, m, 0.0)
        items.append(it)

    # per-title progress on the watchable basis
    by_name = {it.path.rsplit("\\", 1)[-1]: store.watch_progress(it) for it in items}
    assert by_name["HOLLOW CREEK"] == (2, 0)      # 2 real episodes done
    assert by_name["STARFALL ACADEMY"] == (0, 1)  # S01E01 partial
    assert by_name["SKY DRIFTERS"] == (0, 1)      # EP01 partial
    assert by_name["LOST REALMS"] == (2, 0)       # own watchables (7z+poster)
    assert by_name["Stargazer's Atlas"] == (0, 1)

    # sidebar aggregation mirrors ui.library._update_stats — the
    # aggregate is bucketed by content kind now (scope_progress_by_kind):
    # video titles feed the "video" bucket, everything else is bucketed by
    # its real file types. These fake paths have no files on disk, so the
    # video bucket falls back to the video_count basis and the
    # other buckets stay empty (offline titles cannot be counted).
    prog = store.scope_progress_by_kind(items)
    assert prog["video"] == (2, 2, 10)          # pre-fix this was 13 done / 10 = 130%
    pct = max(0, min(100, int(round(100 * prog["video"][0] / prog["video"][2]))))
    assert pct == 20
    assert prog["book"] == (0, 0, 0) \
        and prog["image"] == (0, 0, 0) \
        and prog["other"] == (0, 0, 0)   # virtual paths — no current file

    # title-level tracking itself (watch_progress) is unchanged — a video-free
    # scope still carries full title-level marks: 11 done / 2 part
    nonvid = [it for it in items if not it.video_count]
    nonvid_done = sum(store.watch_progress(it)[0] for it in nonvid)
    nonvid_part = sum(store.watch_progress(it)[1] for it in nonvid)
    assert (nonvid_done, nonvid_part) == (11, 2)
