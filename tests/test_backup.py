# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for backup.py — export / validate / merge round trip.

A `.mcbak` is created from a Store living in a fake app dir (backup.app_dir is
monkeypatched), validated, and merged back into a second Store. All title
paths exist on the local filesystem (created under tmp_path), so the merge
"existing paths only" rule passes on every OS.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import backup  # noqa: E402
from backup import export_backup, merge_backup, stage_import, validate_backup  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture()
def app_dir(tmp_path: Path, monkeypatch):
    """A fake portable app directory for export_backup."""
    d = tmp_path / "appA"
    d.mkdir()
    monkeypatch.setattr(backup, "app_dir", lambda: d)
    return d


def _titles_dir(tmp_path: Path) -> Path:
    """Two real title folders the 'local system' (merge target) can see."""
    d = tmp_path / "local"
    (d / "Movie A").mkdir(parents=True)
    (d / "Movie B").mkdir(parents=True)
    return d


def _fill_store(store: Store, titles: Path):
    store.set_meta(str(titles / "Movie A"), rating=4, category="Movies")
    store.set_meta(str(titles / "Movie B"), rating=2, watched=True,
                   category="Movies")
    store.save_data()


# ------------------------------------------------------------ export
def test_export_backup_creates_valid_mcbak(app_dir: Path, tmp_path: Path):
    titles = _titles_dir(tmp_path)
    s = Store(app_dir / "config.json")
    try:
        _fill_store(s, titles)
    finally:
        s.close()

    dest = tmp_path / "out" / "my-backup.mcbak"
    path, files, size = export_backup(dest, include_cache=False)
    assert path.suffix == ".mcbak"
    assert path.is_file() and files > 0 and size > 0

    ok, msg = validate_backup(path)
    assert ok, msg

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert "config.json" in names
    assert "data.db" in names
    # sqlite sidecars must never travel inside the backup
    assert not any(n.endswith(("-wal", "-shm", "-journal")) for n in names)


def test_export_backup_forces_mcbak_suffix(app_dir: Path, tmp_path: Path):
    path, _files, _size = export_backup(tmp_path / "noext", include_cache=False)
    assert path.suffix == ".mcbak"
    assert path.is_file()


# ------------------------------------------------------------ validate
def test_validate_backup_rejects_garbage(tmp_path: Path):
    junk = tmp_path / "junk.mcbak"
    junk.write_bytes(b"plain text, not a zip")
    ok, msg = validate_backup(junk)
    assert not ok and msg

    missing = tmp_path / "missing.mcbak"
    ok, _msg = validate_backup(missing)
    assert not ok


def test_validate_backup_rejects_zip_without_store_files(tmp_path: Path):
    fake = tmp_path / "fake.mcbak"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("random.txt", "nothing useful")
    ok, msg = validate_backup(fake)
    assert not ok and msg


def test_validate_backup_accepts_legacy_folder(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    ok, msg = validate_backup(legacy)
    assert ok and msg == ""


# ------------------------------------------------------------ merge (restore)
def test_merge_backup_round_trip(app_dir: Path, tmp_path: Path):
    titles = _titles_dir(tmp_path)

    app_a = app_dir                                  # source app dir
    s_a = Store(app_a / "config.json")
    try:
        _fill_store(s_a, titles)
    finally:
        s_a.close()

    path, _files, _size = export_backup(tmp_path / "roundtrip",
                                        include_cache=False)

    # a second, empty store on a different "app dir" merges the backup:
    monkey_target = tmp_path / "appB"
    monkey_target.mkdir()
    real_app_dir = backup.app_dir
    backup.app_dir = lambda: monkey_target           # merge reads the zip only
    try:
        s_b = Store(monkey_target / "config.json")
        try:
            result = merge_backup(path, s_b)
            assert result["items_added"] == 2
            assert result["items_existing"] == 0
            assert result["paths_missing"] == 0
            meta = s_b.meta(str(titles / "Movie A"))
            assert meta["rating"] == 4
            assert meta["category"] == "Movies"
            assert (s_b.meta(str(titles / "Movie B"))["watched"]) is True
            assert "Movies" in s_b.categories()
        finally:
            s_b.close()
    finally:
        backup.app_dir = real_app_dir


def test_merge_backup_rejects_invalid_file(tmp_path: Path):
    junk = tmp_path / "junk.mcbak"
    junk.write_bytes(b"not a zip")
    s = Store(tmp_path / "config.json")
    try:
        with pytest.raises(ValueError):
            merge_backup(junk, s)
    finally:
        s.close()


# ------------------------------------------------------------ covers (round 11)
def test_backup_includes_covers_and_restores(app_dir: Path, tmp_path: Path,
                                             monkeypatch):
    """کاورهای سفارشی داخل ‎.mcbak‎ سفر می‌کنند و در پوشه‌ی تمیز برمی‌گردند."""
    import io as _io

    import portable
    import covers as _covers
    from PIL import Image as _Image

    titles = _titles_dir(tmp_path)
    s = Store(app_dir / "config.json")
    try:
        _fill_store(s, titles)
        # یک کاورِ سفارشی واقعی کنارِ برنامه‌ی منبع
        monkeypatch.setattr(portable, "app_dir", lambda: app_dir)
        monkeypatch.setattr(_covers, "app_dir", lambda: app_dir)
        src = tmp_path / "cover-src.png"
        b = _io.BytesIO()
        _Image.new("RGB", (200, 300), (200, 10, 10)).save(b, "PNG")
        src.write_bytes(b.getvalue())
        info = _covers.import_cover(src, app_dir)
        s.set_custom_poster(str(titles / "Movie A"), info)
    finally:
        s.close()

    path, _files, _size = export_backup(tmp_path / "with-covers",
                                        include_cache=False)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert info["poster"] in names

    # بازگردانی در پوشه‌ی برنامه‌ی تازه و خالی — مسیرِ کاملِ stage → apply
    restore_dir = tmp_path / "appB"
    restore_dir.mkdir()
    monkeypatch.setattr(backup, "app_dir", lambda: restore_dir)
    monkeypatch.setattr(portable, "app_dir", lambda: restore_dir)
    monkeypatch.setattr(_covers, "app_dir", lambda: restore_dir)
    pending = stage_import(path)                       # خروجی: مسیرِ پرچم
    assert pending.name == backup._IMPORT_FLAG_NAME
    staged_dir = restore_dir / backup._IMPORT_PENDING_NAME
    assert (staged_dir / info["poster"]).is_file()
    applied = backup.apply_pending_import()
    assert any(str(a).startswith(".covers") for a in applied)
    assert (restore_dir / info["poster"]).is_file()
    # پشتیبانِ قدیمی بدون ‎.covers/‎ هم تمیز restore می‌شود (سازگاری رو به عقب)
    s2 = Store(restore_dir / "config.json")
    try:
        assert (restore_dir / ".covers").is_dir()
    finally:
        s2.close()


def test_safe_member_accepts_covers_entries():
    """‎.covers/<name>‎ اعضای مجازِ آرشیو پشتیبان‌اند (zip-slip هنوز رد می‌شود)."""
    assert backup._safe_member(".covers/ab12.webp") is True
    assert backup._safe_member(".covers/sub/x.webp") is True
    assert backup._safe_member(".covers/../evil.webp") is False
    assert backup._safe_member("covers/ab12.webp") is True   # بدون نقطه هم امن است
