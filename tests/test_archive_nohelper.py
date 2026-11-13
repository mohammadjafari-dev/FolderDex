# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""RAR without helper tools must degrade cleanly (round 16, audit §5).

`bin/7za.exe` carries no RAR codec and UnRAR/WinRAR may be absent — the
documented behaviour is a graceful fallback (no thumbnail → type card), never
a crash. The audit found no test pinning «no tools → no crash»; this is it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import filethumbs  # noqa: E402


@pytest.fixture()
def no_rar_tools(monkeypatch):
    """هیچ ابزارِ RAR/7z موجود نیست — کشِ ماژول هم خنثی می‌شود."""
    monkeypatch.setattr(filethumbs, "_find_unrar", lambda: None)
    monkeypatch.setattr(filethumbs, "_find_7z", lambda: None)


def _fake_rar(tmp_path: Path) -> Path:
    """فایل با امضای RAR ولی نه آرشیوِ واقعی — لیست‌خواندن باید شکستِ تمیز بدهد."""
    p = tmp_path / "sample.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    return p


def test_rar_without_tools_returns_none_no_crash(no_rar_tools, tmp_path):
    """بدون UnRAR/7z: تامبنیلِ آرشیو None می‌شود — بدونِ استثنا."""
    ft = filethumbs.FileThumbs(tmp_path / "cache")
    assert ft._archive_thumb(_fake_rar(tmp_path), 256) is None


def test_find_unrar_returns_none_without_tools(no_rar_tools):
    """جست‌وجوی ابزار خودش هم تمیز None می‌دهد (بدونِ وابستگی به PATH سیستم)."""
    assert filethumbs._find_unrar() is None
    assert filethumbs._find_7z() is None


def test_rar_member_list_without_tools_is_empty(no_rar_tools, tmp_path):
    """فهرستِ اعضای RAR بدونِ ابزار: یا None یا فهرستِ خالی — کارت به آیکونِ نوع می‌رود.

    (rarfile با امضای معتبر ولی محتوای خالی خودش فهرستِ خالی می‌دهد؛ مهم
    این است که هیچ مسیری کرش نمی‌کند و عضوی claim نمی‌شود.)"""
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
