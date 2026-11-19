# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""App data paths — **everything next to the app; nothing global.**

All app settings and data live next to the app itself; nothing is
written to a global path. An earlier
model (keeping data in %LOCALAPPDATA%\\MediaCataloger for cross-version
stability) was later reverted; data transfer between versions
is now user-driven: the "📤 Export / 📥 Import data" buttons in Settings
(backup.py).

One-time migration (migrate_shared_to_app): on the first run after that change, if the
old global folder still exists, config/data.db is *moved* from it to beside
the app (the old global folder was the source of truth there) and the global cache is
moved only if there is no app-side cache; the global folder is removed once
empty.

Tests/troubleshooting: the MC_DATA_DIR (data root) and MC_CACHE_DIR (cache)
environment variables.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from portable import app_dir
from watchtime import dbg_log

_DIR_NAME = "MediaCataloger"          # legacy global folder name (read-only)
_DATA_SUBDIR = ".mc_data"             # small data folder next to the app (misc)


def user_data_dir() -> Path:
    """Small data folder — **next to the app**. Created once and returned."""
    env = os.environ.get("MC_DATA_DIR", "").strip()
    p = Path(env) if env else app_dir() / _DATA_SUBDIR
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def default_config_path() -> Path:
    """Path of config.json — **right next to the app** (the file shipped in the zip)."""
    env = os.environ.get("MC_DATA_DIR", "").strip()
    return (Path(env) if env else app_dir()) / "config.json"


def thumb_cache_dir() -> Path:
    """Thumbnail cache folder — **next to the app** (`.thumb_cache`).

    The duration cache (durations.db) and watch_later live inside the same
    folder, so they sit beside the app too. Manual override for tests/
    troubleshooting: MC_CACHE_DIR."""
    env = os.environ.get("MC_CACHE_DIR", "").strip()
    p = Path(env) if env else app_dir() / ".thumb_cache"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _move(old: Path, new: Path) -> bool:
    """Move old to new if old exists and new doesn't. Returns: whether the move happened."""
    try:
        if not old.exists() or new.exists():
            return False
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(old, new)             # same drive: instant
        except OSError:
            shutil.move(str(old), str(new))  # different drive: copy + delete
        return True
    except OSError:
        return False


def _replace_or_copy(src: Path, dst: Path) -> bool:
    """Overwrite dst with src (move when possible; otherwise copy + delete)."""
    try:
        if not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)             # atomic overwrite on the same drive
        except OSError:
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
                src.unlink()
        return True
    except OSError:
        return False


# ============================================== one-time migration from the global path
_MIGRATE_FILES = ("config.json", "data.json", "data.db",
                  "data.db-shm", "data.db-wal", "data.db-journal")


def _shared_data_dir() -> Path | None:
    """Path of the legacy global folder — **read only for
    migration**.

    Nothing is ever created/written there (original request: "nothing may be
    stored in a global path"). Missing common variables -> None."""
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA")
            if not base:
                up = os.environ.get("USERPROFILE")
                base = str(Path(up) / "AppData" / "Local") if up else ""
            return Path(base) / _DIR_NAME if base else None
        base = os.environ.get("XDG_DATA_HOME",
                              str(Path.home() / ".local" / "share"))
        return Path(base) / _DIR_NAME
    except Exception as _exc_dbg:
        dbg_log(f"_shared_data_dir failed: {type(_exc_dbg).__name__}")
        return None


def migrate_shared_to_app() -> list[str]:
    """One-time move of legacy global-path data to beside the app.

    Known files (config/data.db/…) are always moved and overwrite the app
    side — in that earlier era the global path was the source of
    truth and the app side only held the empty zip template. The global
    cache moves only when there is no app-side cache (we never silently
    merge two caches). At the end, if the global folder is empty it is
    removed; if anything remains it stays untouched and the "full cleanup"
    button targets it too. Does nothing when MC_DATA_DIR is set (tests /
    manual runs).

    Returns the list of moved item names (for logging — no paths)."""
    moved: list[str] = []
    if os.environ.get("MC_DATA_DIR", "").strip():
        return moved                            # tests/manual runs — no migration
    try:
        shared = _shared_data_dir()
    except Exception as _exc_dbg:
        dbg_log(f"migrate_shared_to_app failed: {type(_exc_dbg).__name__}")
        shared = None
    try:
        if not shared or not shared.is_dir():
            return moved
        app = app_dir()
        for name in _MIGRATE_FILES:
            try:
                if _replace_or_copy(shared / name, app / name):
                    moved.append(name)
            except OSError:
                pass
        try:                                   # global cache — only when no local cache exists
            sc = shared / "thumb_cache"
            dc = app / ".thumb_cache"
            if sc.is_dir() and not dc.exists():
                if _replace_or_copy(sc, dc):
                    moved.append("thumb_cache")
        except OSError:
            pass
        try:
            shared.rmdir()                     # only when empty (nothing global must remain)
            moved.append("(پوشه‌ی سراسری حذف شد)")
        except OSError:
            pass
    except Exception as _exc_dbg:
        dbg_log(f"migrate_shared_to_app failed: {type(_exc_dbg).__name__}")
        pass
    return moved


# ================================================================ factory reset
# The "full cleanup" button in Settings: all app data —
# config/data.db/the app-side cache, plus any leftovers of the legacy global
# path — is wiped and the app returns to day one. Because database/config
# files are open and locked while running (Windows refuses to delete them),
# the cleanup is deferred to the *next* run via a flag next to the app: the
# button writes the flag and restarts the app; at boot, before any database
# opens, the targets are removed.

_RESET_FLAG_NAME = "._mc_reset"


def factory_reset_flag_path() -> Path:
    """Path of the reset flag — next to the app (so wiping the data itself cannot harm it)."""
    return app_dir() / _RESET_FLAG_NAME


def write_factory_reset_flag(targets) -> Path:
    """Atomically write the flag with the list of targets (absolute path strings)."""
    import json as _json
    p = factory_reset_flag_path()
    uniq = []
    for t in targets or []:
        try:
            s = str(Path(str(t)))
            if s not in uniq:
                uniq.append(s)
        except Exception as _exc_dbg:
            dbg_log(f"write_factory_reset_flag failed: {type(_exc_dbg).__name__}")
            continue
    tmp = p.with_name(_RESET_FLAG_NAME + ".tmp")
    tmp.write_text(_json.dumps({"targets": uniq}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def factory_reset_if_pending() -> list:
    """If a reset flag exists, delete its targets — call only at boot start.

    Returns the list of wiped targets (no paths in the log — just a count).
    If deleting a target fails (locked file, …) the flag stays so the next
    run retries. Guard: a drive root, the home folder, or the app folder
    itself is never deleted."""
    import json as _json
    p = factory_reset_flag_path()
    try:
        if not p.exists():
            return []
        data = _json.loads(p.read_text(encoding="utf-8"))
        targets = data.get("targets") if isinstance(data, dict) else None
        if not isinstance(targets, list):
            targets = []
    except Exception as _exc_dbg:
        dbg_log(f"factory_reset_if_pending failed: {type(_exc_dbg).__name__}")
        targets = []
    wiped: list = []
    ok = True
    try:
        _app_res = app_dir().resolve()
        _home_res = Path.home().resolve()
    except OSError:
        _app_res = _home_res = None
    for t in targets:
        try:
            tp = Path(str(t))
            try:
                tr = tp.resolve()
            except OSError:
                tr = tp
            # Safety guard: only paths actually allocated to the app
            if (_app_res is not None and tr == _app_res) or \
               (_home_res is not None and tr == _home_res) or \
               len(tr.parts) < 3:
                ok = False
                continue
            if tp.is_dir():
                shutil.rmtree(str(tp))
            elif tp.exists():
                tp.unlink()
            wiped.append(str(tp))
        except OSError:
            ok = False                    # locked — retried on the next boot
    try:
        user_data_dir()                   # make sure a fresh, empty data folder exists
    except OSError:
        pass
    # Custom covers are app data too — a full reset removes them
    try:
        from covers import COVERS_DIR_NAME
        cov = app_dir() / COVERS_DIR_NAME
        if cov.is_dir():
            shutil.rmtree(str(cov))
    except OSError:
        ok = False                        # locked — retried on the next boot
    except Exception as _exc_dbg:
        dbg_log(f"factory_reset_if_pending failed: {type(_exc_dbg).__name__}")
    if ok:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return wiped
