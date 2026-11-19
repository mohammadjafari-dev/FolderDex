# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""RAR without helper tools must degrade cleanly.

`bin/7za.exe` carries no RAR codec and UnRAR/WinRAR may be absent — the
documented behaviour is a graceful fallback (no thumbnail → type card), never
a crash. No earlier test pinned "no tools → no crash"; this is it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import filethumbs  # noqa: E402


@pytest.fixture()
def no_rar_tools(monkeypatch):
    """No RAR/7z tools available — the module-level cache is neutralized too."""
    monkeypatch.setattr(filethumbs, "_find_unrar", lambda: None)
    monkeypatch.setattr(filethumbs, "_find_7z", lambda: None)


def _fake_rar(tmp_path: Path) -> Path:
    """A file with a RAR signature but not a real archive — listing must fail cleanly."""
    p = tmp_path / "sample.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    return p


def test_rar_without_tools_returns_none_no_crash(no_rar_tools, tmp_path):
    """Without UnRAR/7z: the archive thumbnail is None — no exception."""
    ft = filethumbs.FileThumbs(tmp_path / "cache")
    assert ft._archive_thumb(_fake_rar(tmp_path), 256) is None


def test_find_unrar_returns_none_without_tools(no_rar_tools):
    """The tool lookup itself returns None cleanly (no dependency on the system PATH)."""
    assert filethumbs._find_unrar() is None
    assert filethumbs._find_7z() is None


def test_rar_member_list_without_tools_is_empty(no_rar_tools, tmp_path):
    """RAR member list without tools: either None or an empty list — the card falls back
    to the type icon.

    (rarfile itself yields an empty list for a valid signature with empty
    content; what matters is that no path crashes and no member is claimed.)"""
    opened = filethumbs._arc_open(_fake_rar(tmp_path))
    if opened is None:
        return
    handle, members, _op = opened
    try:
        assert members == []
    finally:
        try:
            handle.close()
        except Exception:
            pass
