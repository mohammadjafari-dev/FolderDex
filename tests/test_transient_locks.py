# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests — Windows transient file locks on atomic file replacement.

Windows refuses os.replace on an *open* file (sharing violation —
winerror 32/33): antivirus/indexers briefly scan freshly written files and
several concurrent workers race for the same destination. Before this fix
only covers.py had a retry while imgcache.py's replacement was bare; the
result was that in a storm of concurrent
workers a locked replacement carried the error path all the way to a
QMessageBox and the cover stress subprocess exited with SystemExit.

These tests do not reproduce the real Windows race — reproducing it here is
impossible and its timing is nondeterministic — instead they pin the same
behavior via *fault injection* and run identically everywhere
(Linux/Windows):
  - a transient lock (a few PermissionErrors after initial success) must be
    absorbed.
  - a persistent lock must surface after the attempt cap, not be silently
    swallowed.
"""

from __future__ import annotations

import os

import pytest
from PIL import Image

import covers
from imgcache import read_cache_pil, replace_with_retry, save_cache_image


def _tiny() -> Image.Image:
    return Image.new("RGB", (64, 64), (12, 34, 56))


def _inject_transient_replace(monkeypatch, failures: int) -> dict:
    """Swaps the real os.replace with a fake that raises a Windows PermissionError
    (winerror 32) "failures" times first and then passes through — simulating
    an antivirus/indexer transient lock or a race between two concurrent
    workers. Because the patch sits on the os module, both atomic writers
    (imgcache and covers) go through the same fake — exactly what happens on
    real Windows."""

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


# ------------------------------------------------------ helper for the helper
def test_replace_with_retry_absorbs_transient_locks(tmp_path, monkeypatch):
    """Seven back-to-back locks must be absorbed — the old 5-attempt budget was not
    enough and ran out in a storm of concurrent workers (the mechanism behind
    the transient save error)."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"old")

    _inject_transient_replace(monkeypatch, 7)

    replace_with_retry(src, dst)
    assert dst.read_bytes() == b"payload"


def test_replace_with_retry_raises_after_persistent_lock(tmp_path, monkeypatch):
    """A *persistent* lock is never swallowed: it surfaces after the attempt cap so
    a full disk/permanent denial is still seen immediately — and the short cap
    also means the worker thread does not stall in the loop."""
    src = tmp_path / "src2.bin"
    src.write_bytes(b"payload")
    dst = tmp_path / "dst2.bin"

    def always_locked(src, dst, *a, **kw):
        raise PermissionError(32, "locked forever")

    monkeypatch.setattr(os, "replace", always_locked)

    with pytest.raises(PermissionError):
        replace_with_retry(src, dst, max_attempts=3)
    assert not dst.exists()          # an incomplete replacement must not leave a half-written file


# ------------------------------------------------------ thumbnail cache
def test_save_cache_image_keeps_webp_under_transient_lock(tmp_path, monkeypatch):
    """Two more transient locks must not silently demote the cache to PNG — the
    .webp file must be built in place and the PNG fallback must not happen at
    all. (Before the fix, this scenario returned "atomic.png" and the parallel
    test on one shared file failed on unlucky Windows runs.)"""
    out = tmp_path / "atomic.webp"

    _inject_transient_replace(monkeypatch, 2)

    ret = save_cache_image(_tiny(), out)
    assert ret == out and out.is_file()
    assert read_cache_pil(out) is not None
    assert not out.with_suffix(".png").exists()


# ------------------------------------------------------ custom cover
def test_covers_atomic_save_survives_lock_burst_beyond_old_budget(tmp_path,
                                                                  monkeypatch):
    """Six back-to-back locks in the cover _atomic_save must be absorbed — the old
    5-attempt budget (~300ms total) ran out against an antivirus scan wave over
    50 simultaneous files, CoverWriteError propagated and, in the cover stress
    subprocess, reached a QMessageBox and SystemExit. Now 8 attempts (~0.8s)
    ride out a short wave, and a persistent error exits non-zero right there."""
    out = tmp_path / "cover.webp"

    _inject_transient_replace(monkeypatch, 6)

    covers._atomic_save(_tiny(), out, quality=covers.STATIC_QUALITY)
    assert out.is_file()
    assert read_cache_pil(out) is not None
