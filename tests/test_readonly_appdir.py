# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پوشه‌ی برنامه‌ی فقط‌خواندنی نباید برنامه را در استارت بکشد (D2).

قبل از این دور: `ThumbnailCache(...)` روی پوشه‌ی فقط‌خواندنی `PermissionError`
می‌داد که از `main()` بیرون می‌زد و excepthook هم لاگ را در همان پوشه می‌نوشت
(خودش خطا می‌داد) — کاربر فقط «برنامه باز نمی‌شود» می‌دید.

Expected behavior now: هر سه کش (پوسترها / فایل‌ها / مدت‌زمان‌ها) بدون
استثنا ساخته می‌شوند و کش به پوشه‌ی Temp می‌رود (پرچمِ read_only_fallback).

روی ویندوز skip می‌شود (chmod رفتارِ فقط‌خواندنیِ مطمئنی ندارد) و اگر آزمون
به‌عنوان root اجرا شود هم skip است — root از مجوزها عبور می‌کند.
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
    """پوشه‌ی برنامه‌ی فقط‌خواندنی + هدایتِ Temp به tmp_path (تمیزکاریِ مطمئن)."""
    import tempfile as _tf
    monkeypatch.setattr(_tf, "tempdir", str(tmp_path))
    base = tmp_path / "app"
    base.mkdir()
    os.chmod(base, 0o555)
    yield base
    os.chmod(base, 0o755)          # tmp_path بتواند پاک کند


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
    # زیرپوشه‌ی files/ در فال‌بک حفظ می‌شود تا با کشِ پوسترها قاطی نشود
    assert ft.dir == _expected_fallback(tmp_path) / "files"
    assert ft.dir.is_dir()


def test_duration_store_survives_readonly_appdir(readonly_app, tmp_path):
    d = media_info.DurationStore(readonly_app / ".thumb_cache")
    assert d.read_only_fallback is True
    assert d.dir == _expected_fallback(tmp_path)
    d.close()


def test_writable_appdir_has_no_fallback(tmp_path):
    """حالتِ سالم: هیچ فال‌بکی روشن نمی‌شود و مسیر همان خواسته‌شده است."""
    c = thumbs.ThumbnailCache(tmp_path / ".thumb_cache")
    assert c.read_only_fallback is False
    assert c.cache_dir == tmp_path / ".thumb_cache"
    d = media_info.DurationStore(tmp_path / ".thumb_cache2")
    assert d.read_only_fallback is False
    d.close()


def test_fallback_dir_actually_writable(readonly_app, tmp_path):
    """فال‌بک فقط اسم نیست — واقعاً می‌شود در آن نوشت (خودِ باگِ D2)."""
    c = thumbs.ThumbnailCache(readonly_app / ".thumb_cache")
    probe = c.cache_dir / f".probe-{os.getpid()}"
    probe.write_bytes(b"x")
    probe.unlink()
