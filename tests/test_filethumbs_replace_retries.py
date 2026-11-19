# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests — four bare os.replace points in filethumbs.py.

An earlier fix brought Windows transient-lock retries only to the two main
writers (imgcache and covers) and deliberately left filethumbs' four
replacements bare as "error-tolerable" paths that only log: they do not
crash, but an antivirus transient lock on the freshly extracted file means
losing that archive's thumbnail on the first try — precisely for the
feature it was built for. Now all four points use
the canonical imgcache.replace_with_retry helper:

  - the two renames of the 7z CLI backend (_CliArc.extract)
  - moving the extracted member in the py7zr backend (the opener inside
    _arc_open)
  - the atomic write of the "selected member" cache (_arc_picks_save)

As in tests/test_transient_locks.py, real Windows is not reproduced — the
behavior is pinned via *fault injection* and the tests run identically
everywhere (Linux/Windows). The opener test deliberately uses the full
_arc_open so whichever backend is picked on that host (CLI 7z on Windows,
py7zr on Linux) is covered.
"""

from __future__ import annotations

import json
import os

from PIL import Image

import filethumbs


def _inject_transient_replace(monkeypatch, failures: int) -> dict:
    """Same fault injection as tests/test_transient_locks.py: replaces the real
    os.replace with a fake that raises a Windows PermissionError (winerror 32)
    "failures" times first and then passes through — simulating an
    antivirus/indexer transient lock on a freshly written file. Because the
    patch sits on the os module, filethumbs' backends go through the same fake."""

    real_replace = os.replace
    state = {"left": failures}

    def flaky(src, dst, *a, **kw):
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(
                32, "The process cannot access the file because it is being "
                    "used by another process")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", flaky)
    return state


# ------------------------------------------------- "selected member" cache
def test_arc_picks_save_survives_transient_lock(tmp_path, monkeypatch):
    """The atomic write of archive_picks.json must absorb a transient lock — in the
    worst case a single PermissionError killed the cache write entirely (the
    old behavior: a silent except) and the next selection started from
    scratch."""
    ft = filethumbs.FileThumbs(tmp_path / "cache")
    ft._arc_picks = {"/lib/films.zip": {"member": "a.png", "kind": "image"}}

    _inject_transient_replace(monkeypatch, 2)

    ft._arc_picks_save()
    out = ft.dir / "archive_picks.json"
    assert out.exists(), "archive_picks.json must land despite transient locks"
    assert json.loads(out.read_text(encoding="utf-8")) == ft._arc_picks


# ------------------------------------------- archive member opener (any backend)
def test_archive_member_opener_survives_transient_lock(tmp_path, monkeypatch):
    """Extracting an archive member must absorb a Windows transient lock and land the
    member file fully on disk — otherwise a single PermissionError carries the
    error path to "no thumbnail built this pass" and the card shows a
    placeholder."""
    src = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), (12, 34, 56)).save(src, "PNG")
    arc = tmp_path / "sample.7z"
    import py7zr
    with py7zr.SevenZipFile(arc, "w") as z:
        z.write(src, arcname="frame.png")

    opened = filethumbs._arc_open(arc)
    assert opened, "no .7z backend available (CLI 7z and py7zr both missing?)"
    handle, members, opener = opened
    try:
        names = [n for n, _size in members]
        assert "frame.png" in names, f"member list missing frame.png: {names}"
        dest = tmp_path / "out" / "frame.png"
        dest.parent.mkdir(parents=True, exist_ok=True)

        _inject_transient_replace(monkeypatch, 2)

        opener("frame.png", dest, timeout=60)
        assert dest.exists() and dest.stat().st_size > 0
        with Image.open(dest) as im:
            assert im.size == (64, 64)
    finally:
        try:
            handle.close()
        except Exception:
            pass
