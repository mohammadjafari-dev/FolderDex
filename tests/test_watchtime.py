# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for watchtime.py — PURE logic only.

No player launching, no window probing, no threads: just state-file parsing,
redaction and helper helpers. Must pass on any OS without a display.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import watchtime  # noqa: E402
from watchtime import (  # noqa: E402
    WATCH_DIR_NAME,
    _hash_str,
    _redact,
    _state_candidates,
    parse_state_file,
    safe_ref,
    watch_dir,
)


# ------------------------------------------------------------ mpv state file
def test_parse_state_file_reads_start(tmp_path: Path):
    f = tmp_path / "state"
    f.write_text("start=123.5\nsomething=else\n", encoding="utf-8")
    assert parse_state_file(f) == 123.5


def test_parse_state_file_integer_start(tmp_path: Path):
    f = tmp_path / "state"
    f.write_text("start=42\n", encoding="utf-8")
    assert parse_state_file(f) == 42.0


def test_parse_state_file_without_start_returns_none(tmp_path: Path):
    f = tmp_path / "state"
    f.write_text("pause=no\n", encoding="utf-8")
    assert parse_state_file(f) is None


def test_parse_state_file_missing_file_returns_none(tmp_path: Path):
    assert parse_state_file(tmp_path / "nope") is None


# ------------------------------------------------------------ watch_dir
def test_watch_dir_creates_watch_later(tmp_path: Path):
    d = watch_dir(tmp_path)
    assert d == tmp_path / WATCH_DIR_NAME
    assert d.is_dir()


# ------------------------------------------------------------ state candidates
def test_state_candidates_cover_separator_variants(tmp_path: Path):
    p = "C:/Movies/Best Film/file.mkv"
    cands = _state_candidates(tmp_path, p)
    hashes = {hashlib.md5(v.encode()).hexdigest()
              for v in (p, p.replace("/", "\\"))}
    expected = {h for h in hashes for h in (h, h.upper())}
    assert {c.name for c in cands} == expected
    assert all(c.parent == tmp_path for c in cands)


# ------------------------------------------------------------ safe_ref
def test_safe_ref_hides_the_path():
    ref = safe_ref(Path("C:/Very Secret Folder/movie.mkv"))
    assert ref.startswith(".mkv#")
    tail = ref.split("#", 1)[1]
    assert len(tail) == 8 and all(c in "0123456789abcdef" for c in tail)
    assert "Secret" not in ref


def test_safe_ref_is_stable_for_same_path():
    assert safe_ref("C:/x/a.mp4") == safe_ref("C:/x/a.mp4")


def test_safe_ref_marks_directories():
    ref = safe_ref(Path("C:/Some Folder"))
    assert ref.startswith("[dir]#") or ref.endswith("#?")


# ------------------------------------------------------------ _redact
def test_redact_strips_windows_drive_paths():
    # note: the path must end the line - the regex swallows trailing spaces too
    out = _redact("tried C:/Users/someone/Secrets/file.mkv")
    assert "Secrets" not in out and "someone" not in out
    assert out.startswith("tried .mkv#")


def test_redact_strips_unc_paths():
    out = _redact(r"scan \\\\NAS\\media\\private\\doc.pdf done")
    assert "private" not in out
    assert ".pdf#" in out or "[dir]" in out


def test_redact_leaves_normal_text_alone():
    assert _redact("no paths in here") == "no paths in here"


# ------------------------------------------------------------ _hash_str
def test_hash_str_short_and_stable():
    h1 = _hash_str("some window title with a movie name.mkv")
    assert h1.startswith("#") and len(h1) == 9
    assert h1 == _hash_str("some window title with a movie name.mkv")
    assert h1 != _hash_str("other title")


# ------------------------------------------------------------ debug log wiring
def test_dbg_log_is_safe_without_a_log_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(watchtime, "_DEBUG_LOG", None)
    watchtime.dbg_log("must not raise")          # no-op path


def test_dbg_log_writes_redacted_lines(tmp_path: Path, monkeypatch):
    logfile = tmp_path / "folderdex-debug.log"
    monkeypatch.setattr(watchtime, "_DEBUG_LOG", logfile)
    watchtime.dbg_log("watching C:/Private/Name/file.mkv")
    text = logfile.read_text(encoding="utf-8")
    assert "Private" not in text
    assert ".mkv#" in text and "watching" in text


def test_set_debug_log_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(watchtime, "_DEBUG_LOG", None)
    watchtime.set_debug_log(tmp_path / "dbg.log")
    assert watchtime._DEBUG_LOG == tmp_path / "dbg.log"
    watchtime.set_debug_log(None)
    assert watchtime._DEBUG_LOG is None
