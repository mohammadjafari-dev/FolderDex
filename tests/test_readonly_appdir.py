# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""A read-only app folder must not kill the app at startup.

Before the fix: `ThumbnailCache(...)` raised `PermissionError` on a read-only
folder, which escaped `main()`, and the excepthook then tried to write the
log into that same folder (failing itself) — the only symptom was "the app
won't open".

Expected behavior now: all three caches (posters / files / durations) are
created without an exception and the cache moves to the Temp folder (the
read_only_fallback flag).

Skipped on Windows (chmod has no reliable read-only behavior) and when the
suite runs as root — root bypasses permissions.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

import filethumbs  # noqa: E402
import media_info  # noqa: E402
import thumbs  # noqa: E402

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or _IS_ROOT,
    reason="chmod-based read-only dirs need a non-root POSIX sandbox")


@pytest.fixture()
def readonly_app(tmp_path: Path, monkeypatch):
    """Read-only app folder + Temp redirected to tmp_path (reliable cleanup)."""
    import tempfile as _tf
    monkeypatch.setattr(_tf, "tempdir", str(tmp_path))
    base = tmp_path / "app"
    base.mkdir()
    os.chmod(base, 0o555)
    yield base
    os.chmod(base, 0o755)          # let tmp_path clean up


def _expected_fallback(tmp_path: Path) -> Path:
    return Path(tempfile.gettempdir()) / "FolderDex-cache"


def test_thumbnail_cache_survives_readonly_appdir(readonly_app, tmp_path):
    c = thumbs.ThumbnailCache(readonly_app / ".thumb_cache")
    assert c.read_only_fallback is True
    assert c.cache_dir == _expected_fallback(tmp_path)
    assert c.cache_dir.is_dir()


def test_file_thumbs_survives_readonly_appdir(readonly_app, tmp_path):
    ft = filethumbs.FileThumbs(readonly_app / ".thumb_cache")
    assert ft.read_only_fallback is True
    # the files/ subfolder is preserved in the fallback so it does not mix with the poster cache
    assert ft.dir == _expected_fallback(tmp_path) / "files"
    assert ft.dir.is_dir()


def test_duration_store_survives_readonly_appdir(readonly_app, tmp_path):
    d = media_info.DurationStore(readonly_app / ".thumb_cache")
    assert d.read_only_fallback is True
    assert d.dir == _expected_fallback(tmp_path)
    d.close()


def test_writable_appdir_has_no_fallback(tmp_path):
    """Healthy case: no fallback engages and the path is exactly the requested one."""
    c = thumbs.ThumbnailCache(tmp_path / ".thumb_cache")
    assert c.read_only_fallback is False
    assert c.cache_dir == tmp_path / ".thumb_cache"
    d = media_info.DurationStore(tmp_path / ".thumb_cache2")
    assert d.read_only_fallback is False
    d.close()


def test_fallback_dir_actually_writable(readonly_app, tmp_path):
    """The fallback is not just a name — it is genuinely writable (the original bug)."""
    c = thumbs.ThumbnailCache(readonly_app / ".thumb_cache")
    probe = c.cache_dir / f".probe-{os.getpid()}"
    probe.write_bytes(b"x")
    probe.unlink()
