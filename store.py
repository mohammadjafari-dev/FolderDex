# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Settings and user-data management.

- config.json: paths, categories, category colors, options (small —
  human-readable/editable)
- data.db (SQLite): per-title info (display name, category, description,
  custom poster, favorite, times, file manifest, watch status) — key = folder
  path

Track profiles: each category can have its own marking scheme
— "Movies & Series" (watched/partial + auto tracking), "Books & Manga"
(read/half-read), "no tracking", or any custom profile the user builds (e.g.
"Workout": done / in progress / not done). Configured in config via
cat_track_profile + track_profiles.

Why SQLite: with thousands of titles, rewriting a big JSON file for every
tiny change (like saving the live position every 10 seconds) becomes slow
and janky; SQLite writes only the single affected row each time (WAL
journaling, no external dependency — Python's own sqlite3).

Automatic migration: if an old data.json exists, it is imported into data.db
on first run and the JSON file is renamed to .bak. The public Store interface
is untouched.

If --config points to another file (e.g. the demo), the matching database is
created (config.demo.json -> data.demo.db, migrating data.demo.json if
present).
"""
from __future__ import annotations

import i18n as _i18n
from i18n import T

import copy
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import portable
from watchtime import dbg_log

# Mangled names break button/header labels — control characters
# and ANSI escapes are stripped from display text; a file name with \x00 or a
# newline must not make a label multi-line or inject terminal-style colors.
_CTRL_ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


def ui_clean(text) -> str:
    """Safe single-line UI display text (no null/ANSI/line breaks, uniform spaces)."""
    s = _CTRL_ANSI_RE.sub("", str(text or ""))
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def _meta_key(path) -> str:
    """Meta key: canonical Path form — raw strings and Path objects map to the
    same key.

    On Windows str(Path("C:/a/b")) becomes plain "C:\\a\\b"; a key stored as
    the raw string "C:/a/b" (as the cover APIs and tests did) was not found,
    so that title's custom cover/rating went "missing" — two red Windows
    tests reproduced exactly this (on Linux both forms are identical, so the
    bug stayed invisible). Real Path objects yield exactly the previous str —
    existing behavior untouched.
    """
    try:
        return str(Path(path))
    except (TypeError, ValueError):
        return str(path)

PALETTE = [
    "#a78bfa",  # purple
    "#38bdf8",  # sky blue
    "#4ade80",  # green
    "#fbbf24",  # amber
    "#fb7185",  # pink
    "#2dd4bf",  # teal
    "#e879f9",  # magenta
    "#fb923c",  # orange
    "#a3e635",  # lime
    "#22d3ee",  # cyan
]

# Hand-picked category colors — only a valid #rrggbb is accepted
_HEX6_RE = re.compile(r"^#[0-9a-f]{6}$")

DEFAULT_CONFIG = {
    "roots": [],
    "categories": [],
    "category_colors": {},
    "include_image_fallback": True,
    "theme": "dark",
    "compute_durations": True,
    "cat_status_off": [],          # legacy — translated to the "none" profile
    "cat_track_profile": {},       # category -> profile key (inherited from parent)
    "track_profiles": {},          # user-defined custom profiles {id: {title, done, part, none}}
    "folder_thumbs_only": False,   # True -> no real thumbnails for individual files
    "prebuild_thumbs": True,    # on by default — background, UI-deferring
    # The app is bilingual — English by default (GitHub audience); the user's
    # choice is stored here and persists across runs.
    "language": "en",              # "en" | "fa"
    # Thumbnail-prebuild performance mode (settings review).
    # auto = detect disk type | high = always full speed (SSD) | safe = always
    # gentle (HDD)
    "perf_mode": "auto",
    # The "prebuild heavy files only" option was removed — enabling prebuild
    # means building *all* thumbnails (light+heavy) in the background.
    # "Everything beside the app — nothing in a global path": the cache
    # location is no longer configurable (cache_loc_* keys removed; leftovers
    # in old files are ignored). The cache always lives beside the app
    # (paths.thumb_cache_dir). Default cache cap used to be 0 (unlimited) —
    # the cache key changed with the display-scale setting, so the previous
    # generation was never purged; 2 GiB LRU is now the default.
    "cache_max_mb": 2048,
}

# ---------------------------------------------------------------- Built-in track profiles
# Three base states: not started (none) / partial (part) / done (done)
# auto = labels picked by file type (previous default behavior)
BUILTIN_TRACK_PROFILES = {
    "auto": {
        "title": "خودکار — بر اساس نوع فایل",
        "vid": {"done": "دیده شده", "part": "نیمه‌تمام", "act": "تماشا"},
        "doc": {"done": "خوانده شده", "part": "نیمه‌خوانده", "act": "خواندن"},
    },
    "video": {
        "title": "فیلم و سریال — تماشا",
        "done": "دیده شده", "part": "نیمه‌تمام", "act": "تماشا",
    },
    "book": {
        "title": "کتاب و مانگا — خواندن",
        "done": "خوانده شده", "part": "نیمه‌خوانده", "act": "خواندن",
    },
    "none": {"title": "بدون ترکینگ", "done": "", "part": "", "act": ""},
}

_BOOK_HINTS = ("کتاب", "مانگا", "کمیک", "رمان", "daisy", "manga", "comic",
               "book", "novel", "مانهوا", "مانوا", "وب‌تون", "وب تون")
DEFAULT_DATA = {"items": {}}


def _quarantine_db_files(db_path: Path) -> list[str]:
    """Moves a corrupt DB file plus its sidecars aside with a timestamped
    suffix (returns the list of names).

    The reason: the app failed to boot with "database disk image is
    malformed"; instead of crashing at boot, the damaged file is quarantined
    so the app starts with a fresh database; the quarantined file names are
    returned for a one-time user notice (names only, no paths)."""
    import datetime as _dt
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    moved: list[str] = []
    db_path = Path(db_path)
    for cand in (db_path,) + tuple(db_path.with_name(db_path.name + sfx)
                                   for sfx in ("-wal", "-shm", "-journal")):
        try:
            if cand.exists():
                dst = cand.with_name(cand.name + ".corrupt-" + stamp)
                try:
                    cand.rename(dst)
                except OSError:
                    import shutil as _sh
                    _sh.copy2(str(cand), str(dst))
                    cand.unlink()
                moved.append(dst.name)
        except OSError as e:
            # If quarantining the corrupt file fails, the next attempt to
            # open the database will fail too — keep the reason in the log
            try:
                from watchtime import _dbg
                _dbg(f"db quarantine failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_quarantine_db_files failed: {type(_exc_dbg).__name__}")
                pass
    return moved


def _open_db_fresh(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "  path TEXT PRIMARY KEY,"
            "  meta TEXT NOT NULL"
            ")"
)
        # Health check right here — hidden corruption cannot lurk until mid-session
        res = con.execute("PRAGMA quick_check").fetchone()
        if not res or str(res[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"quick_check failed: {res[0] if res else '?'}")
        return con
    except sqlite3.DatabaseError:
        # If PRAGMA/quick_check fails on a corrupt file, the connection must
        # be closed *before* the quarantine attempt — on Windows the open
        # file handle locked the quarantine rename/unlink with WinError 32,
        # crashing boot on a damaged database (Linux ignores the open
        # handle). The healthy path's behavior is unchanged.
        con.close()
        raise


def _open_db(db_path: Path) -> tuple:
    """Shared SQLite connection + table creation if missing -> (connection,
    quarantine list).

    If the base file is corrupt, it is
    quarantined together with its sidecars and a fresh database is created —
    the app always boots. But only for *genuine* corruption: a locked file,
    missing write access, or an I/O error is no longer quarantined; it is
    re-raised — previously every sqlite3.DatabaseError was quarantined, so a
    healthy database in use by another app (or on a read-only drive) could be
    moved aside and replaced with an empty one; the user believed their data
    had been deleted."""
    try:
        return _open_db_fresh(db_path), []
    except sqlite3.DatabaseError as exc:
        if not _db_is_corrupt(exc):
            raise
        moved = _quarantine_db_files(db_path)
        return _open_db_fresh(db_path), moved


# Genuine SQLite corruption codes: SQLITE_CORRUPT=11, SQLITE_NOTADB=26. All
# other errors (BUSY=5, LOCKED, READONLY, IOERR...) mean "cannot write right
# now", not "the file is corrupt" — quarantining is forbidden.
_SQLITE_CORRUPT_CODES = {
    getattr(sqlite3, "SQLITE_CORRUPT", 11),
    getattr(sqlite3, "SQLITE_NOTADB", 26),
}


def _db_is_corrupt(exc: sqlite3.DatabaseError) -> bool:
    """True only for verified database file corruption (quarantine-worthy).

    Uses sqlite_errorcode (Python >= 3.11) when present, otherwise the error
    text; the "quick_check failed" message comes from _open_db_fresh's own
    guard, which only raises after an actually executed quick_check — i.e.
    real damage."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        try:
            base = int(code) & 0xFF      # base code without the extension bits
        except (TypeError, ValueError):
            base = None
        if base is not None:
            return base in _SQLITE_CORRUPT_CODES
    msg = str(exc).lower()
    if "quick_check failed" in msg:
        return True
    return any(s in msg for s in ("malformed", "not a database", "encrypted",
                                  "corrupt"))


def _rel_is_video(rel) -> bool:
    """Does a watch relative key point to a video file?

    scanner is imported inside the function on purpose (no import cycle at
    load time); if it is unavailable, the record counts (previous behavior) —
    it must never be dropped the other way around."""
    try:
        from scanner import VIDEO_EXTS
        return os.path.splitext(str(rel))[1].lower() in VIDEO_EXTS
    except Exception:
        return True


def _rel_key(item, f) -> str:
    """Relative key of a file within a title — same rule as ui.details.rel_of
    (file name for a single-file title, "/"-separated relative path for a
    folder).

    Duplicated locally so store does not depend on ui; key normalization for
    marks and for current files must match, otherwise the "existing file"
    intersection breaks."""
    try:
        p = Path(f)
        if Path(item.path).is_file():
            return p.name
        return str(p.relative_to(item.path)).replace("\\", "/")
    except (ValueError, OSError, TypeError):
        try:
            return Path(f).name
        except (TypeError, ValueError):
            return str(f)


class Store:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        name = self.config_path.name
        base = name.replace("config", "data", 1) if name.startswith("config") else "data.json"
        base = Path(base).stem                      # data / data.demo
        self.data_path = self.config_path.with_name(base + ".json")   # legacy path (migration)
        self.db_path = self.config_path.with_name(base + ".db")       # new SQLite store

        self._lock = threading.RLock()
        # Hitting save used to hang the app for a few seconds — a single
        # save in settings made 8+ setters each do an atomic config write
        # (plus an SQLite flush) to disk — on Windows with real-time
        # antivirus that means seconds of UI freeze. defer_save coalesces
        # them into one flush.
        self._defer = 0
        self._cfg_dirty = False
        self._data_deferred = False
        # Self-healing for a corrupt database — quarantined files (names, no paths)
        self._con, self.db_quarantined = _open_db(self.db_path)
        self._dirty: set[str] = set()               # changed paths awaiting flush
        # Flag for the last failed DB flush — main's status bar watches it to
        # announce "could not save data" (read_only_fallback pattern)
        self.save_failed = False
        # Flag for the last failed config save (same pattern —)
        self.config_save_failed = False
        # Quarantine notice of a corrupt config.json (empty when nothing was)
        self.quarantined_config: list[str] = []
        # Data/config change counters — "auto backup only on a real change"
        # (must exist before the earliest possible save_config in __init__)
        self.data_rev = 0
        self.cfg_rev = 0

        # deepcopy is critical: with a shallow copy the categories list and
        # the colors/items dicts would be shared by every instance in the
        # process (categories leaking between stores)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                _raw_cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(_raw_cfg, dict):
                    # Language migration — the rule lives in one place: i18n.lang_from_config
                    _raw_cfg["language"] = _i18n.lang_from_config(_raw_cfg)
                    self.config.update(_raw_cfg)
                    self._sanitize_config_types(_raw_cfg)
                else:
                    raise ValueError("config.json is not a JSON object")
            except Exception as e:
                # Quarantine: a corrupt config.json used to stay in
                # place and the first settings-save overwrote it with defaults
                # — the user's roots/categories were silently lost with no
                # chance to recover. Same pattern as the corrupt-database
                # quarantine: set the unreadable file aside (timestamped),
                # start from defaults and announce it once.
                self.quarantined_config = self._quarantine_corrupt_config()
                try:
                    from watchtime import _dbg
                    _dbg(f"config load failed - defaults kept ({type(e).__name__}; "
                         f"quarantined={'yes' if self.quarantined_config else 'no'})")
                except Exception as _exc_dbg:
                    dbg_log(f"__init__ failed: {type(_exc_dbg).__name__}")
                    pass
        else:
            self.save_config()

        # Check the factory-reset flag — even if files were locked and not
        # deleted, wipe the data from within so no categories/subcategories/
        # watch state survive.
        self._check_factory_reset_flag()

        self.data = copy.deepcopy(DEFAULT_DATA)
        self.data.setdefault("items", {})
        self._load_items()
        self._migrate_legacy_json()
        # Raw keys from old versions/imported backups are canonicalized once
        # via _meta_key — otherwise the same title ends up with phantom/
        # duplicate half-metadata (all current APIs write canonically, legacy
        # reads did not).
        self._canonicalize_item_keys()

    def _quarantine_corrupt_config(self) -> list[str]:
        """Set an unreadable config.json aside (timestamped sibling file).

        parity with the database quarantine: the broken file must
        survive the next defaults-save so the user can recover their
        roots/categories by hand. Returns the [sibling name] for the startup
        notice; [] when the move was impossible (locked/read-only)."""
        import datetime as _dt
        try:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            aside = self.config_path.with_name(
                f"{self.config_path.name}.corrupt-{stamp}")
            os.replace(self.config_path, aside)
            return [aside.name]
        except OSError as _exc_dbg:
            dbg_log(f"_quarantine_corrupt_config failed: {type(_exc_dbg).__name__}")
            return []

    def _sanitize_config_types(self, raw: dict) -> None:
        """Type-check the critical config keys.

        `self.config.update(raw)` happily imports "roots": "C:/Movies" or a
        string in category_colors from a hand-edited/imported file; every
        later consumer then crashes or misrenders. Wrong-typed keys are
        replaced by their defaults (and written back on the next save)."""
        fixed = False
        checks = (
            ("roots", list),
            ("categories", list),
            ("track_profiles", dict),
            ("cat_track_profile", dict),
            ("category_colors", dict),
            ("recents", list),
            ("auto_backup", dict),
            ("player", dict),
)
        for key, typ in checks:
            v = self.config.get(key, None)
            if v is not None and not isinstance(v, typ):
                self.config[key] = copy.deepcopy(DEFAULT_CONFIG.get(key))
                fixed = True
        if fixed:
            try:
                from watchtime import _dbg
                _dbg("config type check: one or more keys had the wrong type "
                     "and were reset to defaults")
            except Exception as _exc_dbg:
                dbg_log(f"_sanitize_config_types failed: {type(_exc_dbg).__name__}")

    def _canonicalize_item_keys(self):
        """Raw legacy DB/JSON keys -> _meta_key canonical form (once, safely).

        Collision rule (never rename/merge blindly): only keys whose
        canonical form differs from themselves are moved; if the canonical
        key already exists, the dicts are merged *without overwriting the
        existing values* (setdefault — current data always wins) and the
        counts go to the log anonymously. Before any change, a one-time
        backup of data.db is set aside (.pre-canonical.bak — first run only)."""
        try:
            self._canonicalize_item_keys_impl()
        except Exception as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys failed: {type(_exc_dbg).__name__}")

    # ------------------------------------------------------------ SQLite layer
    def _load_items(self):
        """Read every row into memory (same model as before; same size as the old JSON)."""
        try:
            with self._lock:
                cur = self._con.execute("SELECT path, meta FROM items")
                for p, m in cur.fetchall():
                    try:
                        self.data["items"][p] = json.loads(m)
                    except ValueError:
                        continue
        except sqlite3.Error as e:
            # Reading the items failed (the library would look empty) — do
            # not swallow it silently
            try:
                from watchtime import _dbg
                _dbg(f"items load from SQLite failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_load_items failed: {type(_exc_dbg).__name__}")
                pass

    def _migrate_legacy_json(self):
        """Import a legacy data.json into SQLite (once) + rename the file to .bak."""
        if not self.data_path.exists():
            return
        if self.data["items"]:      # database already populated — just set the old JSON aside
            try:
                self.data_path.rename(self.data_path.with_suffix(".json.bak"))
            except OSError as e:
                try:
                    from watchtime import _dbg
                    _dbg(f"legacy data.json rename failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                    pass
            return
        try:
            legacy = json.loads(self.data_path.read_text(encoding="utf-8"))
        except Exception as _exc_dbg:
            dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
            legacy = {}
        items = legacy.get("items") or {}
        if items:
            self.data["items"] = items
            with self._lock:
                try:
                    self._con.executemany(
                        "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)",
                        [(p, json.dumps(m, ensure_ascii=False)) for p, m in items.items()],
)
                    self._con.commit()
                except sqlite3.Error as e:
                    # Importing the legacy data.json into SQLite failed —
                    # the items live only in memory and would be lost on
                    # exit; do not swallow it silently
                    try:
                        from watchtime import _dbg
                        _dbg(f"legacy data.json import into SQLite failed: "
                             f"{type(e).__name__}")
                    except Exception as _exc_dbg:
                        dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                        pass
        try:
            self.data_path.rename(self.data_path.with_suffix(".json.bak"))
        except OSError as e:
            try:
                from watchtime import _dbg
                _dbg(f"legacy data.json rename failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_migrate_legacy_json failed: {type(_exc_dbg).__name__}")
                pass

    def _check_factory_reset_flag(self):
        """Check the factory-reset flag — even if files were locked
        and not deleted, wipe the data from within so no categories/
        subcategories/watch state survive. The thumbnail cache is cleared
        too."""
        try:
            import paths as _p
            flag = _p.factory_reset_flag_path()
            if not flag.exists():
                return
            # Flag present -> full internal wipe
            # 1) Delete all items rows from SQLite
            try:
                with self._lock:
                    self._con.execute("DELETE FROM items")
                    self._con.commit()
            except Exception as e:
                # Wiping the database during a full reset failed — do not
                # swallow it silently (locked files may survive)
                try:
                    from watchtime import _dbg
                    _dbg(f"factory reset: items wipe failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                    pass
            # 2) Reset config to defaults
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.data = copy.deepcopy(DEFAULT_DATA)
            self.data.setdefault("items", {})
            # 3) Write the emptied config
            try:
                self.save_config()
            except Exception as e:
                try:
                    from watchtime import _dbg
                    _dbg(f"factory reset: config rewrite failed: {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                    pass
            # 4) clear the thumbnail cache on disk
            try:
                import shutil
                cache_dir = _p.thumb_cache_dir()
                if cache_dir and cache_dir.exists():
                    for item in cache_dir.iterdir():
                        try:
                            if item.is_dir():
                                shutil.rmtree(str(item))
                            else:
                                item.unlink()
                        except OSError:
                            pass
            except Exception as _exc_dbg:
                dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                pass
            # 5) Clear the in-app caches (durations and thumb_cache) + custom covers
            try:
                app_dir = self.config_path.parent
                for sub in (".thumb_cache", ".covers"):
                    d = app_dir / sub
                    if d.exists():
                        import shutil as _sh
                        _sh.rmtree(str(d), ignore_errors=True)
            except Exception as _exc_dbg:
                dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
                pass
            # 6) Remove the flag (reset ran successfully)
            try:
                flag.unlink(missing_ok=True)
            except OSError:
                pass
        except Exception as _exc_dbg:
            dbg_log(f"_check_factory_reset_flag failed: {type(_exc_dbg).__name__}")
            pass

    def _canonicalize_item_keys_impl(self):
        items = self.data.get("items", {})
        renames: list[tuple[str, str]] = []
        for p in list(items):
            if not isinstance(items.get(p), dict):
                continue
            canon = _meta_key(p)
            if canon != p:
                renames.append((p, canon))
        if not renames:
            return
        # One-time DB backup before the first migration — manual recovery stays possible
        try:
            if self.db_path.is_file():
                bak = self.db_path.with_name(self.db_path.name + ".pre-canonical.bak")
                if not bak.exists():
                    shutil.copy2(str(self.db_path), str(bak))
        except OSError as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys backup failed: {type(_exc_dbg).__name__}")
        merged = 0
        with self._lock:
            for old, canon in renames:
                old_meta = items.pop(old, None) or {}
                if canon in items and isinstance(items[canon], dict):
                    cur = items[canon]
                    for k, v in old_meta.items():
                        cur.setdefault(k, v)     # keep each copy's existing value
                    merged += 1
                else:
                    items[canon] = old_meta
            # Persistent rewrite in SQLite: delete the raw key, insert the canonical one
            try:
                self._con.executemany("DELETE FROM items WHERE path = ?",
                                      [(old,) for old, _c in renames])
                self._con.executemany(
                    "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)",
                    [(c, json.dumps(items[c], ensure_ascii=False))
                     for _o, c in renames if c in items])
                self._con.commit()
            except sqlite3.Error as e:
                # The key migration in SQLite failed — memory is canonical
                # and the first touch of each path fixes it; do not swallow
                # it silently
                self.save_failed = True
                dbg_log(f"_canonicalize_item_keys db rewrite failed: {type(e).__name__}")
        try:
            from watchtime import _dbg
            _dbg(f"meta key canonicalization: {len(renames)} renamed, "
                 f"{merged} merged")
        except Exception as _exc_dbg:
            dbg_log(f"_canonicalize_item_keys failed: {type(_exc_dbg).__name__}")

    def _touch(self, path: str):
        self._dirty.add(path)

    def close(self):
        """Final flush + close the connection (on app exit)."""
        try:
            self.save_data()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass
        try:
            with self._lock:
                self._con.close()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ Saving
    def save_config(self, _bump: bool = True):
        """Atomic config.json write — temporary file first, then an atomic
        replace.

        Writing directly left a half-written (or zero-byte) file behind on a
        crash or force-kill mid-save, losing the user's settings. os.replace
        is atomic on Windows/Unix. _bump advances the change counter
        (for the smart auto backup); "recents" keeps itself inert with
        _bump=False.

        on a transient replace failure (antivirus/file lock on
        Windows) the atomic replace is retried once after a short pause;
        the old direct write_text() fallback — a non-atomic overwrite of the
        real file — is gone. On total failure the config_save_failed flag is
        set (the in-memory config stays, so a later save retries) and
        cfg_rev is NOT advanced: the auto backup must not treat settings as
        "changed" when nothing reached the disk."""
        if self._defer:                             # batched save — flushed at the end
            self._cfg_dirty = True
            if _bump:
                self.cfg_rev += 1
            return
        # Optimization — "fast once, slow the next time" report: each drive's
        # disk-type detection (SSD/HDD) is persisted here with the settings
        # so the prebuild worker count stays stable across runs, instead of a
        # low-level probe answering differently on every boot. The key is
        # written only when data exists, keeping the default config.json
        # untouched and minimal.
        try:
            import hwinfo as _hw
            _kinds = _hw.persisted_kinds()
            if _kinds:
                self.config["disk_kinds"] = _kinds
        except Exception as _exc_dbg:
            dbg_log(f"save_config failed: {type(_exc_dbg).__name__}")
            pass
        data = json.dumps(self.config, ensure_ascii=False, indent=2)
        tmp = self.config_path.with_name(self.config_path.name + ".tmp")
        ok = False
        try:
            tmp.write_text(data, encoding="utf-8")
        except OSError as e0:
            self.config_save_failed = True
            try:
                from watchtime import _dbg
                _dbg(f"config save failed (temp write): {type(e0).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"save_config failed: {type(_exc_dbg).__name__}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            os.replace(tmp, self.config_path)
            ok = True
        except OSError:
            # Retry once — a just-closed file lock (real-time AV) usually
            # clears within milliseconds
            time.sleep(0.08)
            try:
                os.replace(tmp, self.config_path)
                ok = True
            except OSError as e2:
                self.config_save_failed = True
                try:
                    from watchtime import _dbg
                    _dbg(f"config save failed (atomic replace, retried): "
                         f"{type(e2).__name__} — in-memory config kept, "
                         f"a later save retries")
                except Exception as _exc_dbg:
                    dbg_log(f"save_config failed: {type(_exc_dbg).__name__}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        if ok:
            self.config_save_failed = False
            if _bump:
                self.cfg_rev += 1                    # smart auto backup

    def save_data(self, _bump: bool = True):
        """Flush changes to SQLite — only the affected rows, not a whole-DB
        rewrite.

        A successful flush advances the data change counter so "auto
        backup only on a real change" knows when a rewrite is worthwhile."""
        if self._defer:                             # batched save — flushed at the end
            self._data_deferred = True
            if _bump:
                self.data_rev += 1
            return
        with self._lock:
            # Swap the dirty set and read items inside the lock — this used
            # to happen outside the lock and raced with another thread's
            # setter (live watching/covers)
            if not self._dirty:
                return
            dirty, self._dirty = self._dirty, set()
            items = self.data.get("items", {})
            upserts, deletes = [], []
            for p in dirty:
                meta = items.get(p)
                if meta:
                    upserts.append((p, json.dumps(meta, ensure_ascii=False)))
                else:
                    deletes.append((p,))
            try:
                if upserts:
                    self._con.executemany(
                        "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)", upserts)
                if deletes:
                    self._con.executemany("DELETE FROM items WHERE path = ?", deletes)
                self._con.commit()
            except sqlite3.Error as e:
                # Transaction failed -> re-mark the paths dirty so they flush next time
                self._dirty.update(dirty)
                # SQLite errors are no longer swallowed silently — log +
                # flag; if the error is permanent (full disk/read-only
                # file/lock) the user no longer believes data was saved and
                # watch state/ratings/covers stop vanishing
                self.save_failed = True
                dbg_log(f"save_data failed: {type(e).__name__}")
                return
            self.save_failed = False
            if _bump:
                self.data_rev += 1               # successful flush = data really changed

    # ------------------------------------------------------------ Batched saving
    @contextmanager
    def defer_save(self):
        """Coalesces saves and flushes exactly once at the end (fixes the
        "save" freeze).

        Clicking save used to hang the app for a few seconds.
        Cause: every setter called save_config immediately (8+ sequential
        atomic disk writes). Inside this context setters only touch memory;
        leaving the block performs one save_config + one save_data. Nesting
        is safe.
        """
        self._defer += 1
        try:
            yield
        finally:
            self._defer -= 1
            if self._defer <= 0:
                self._defer = 0
                try:
                    if self._cfg_dirty:
                        self._cfg_dirty = False
                        self.save_config()
                finally:
                    if self._data_deferred:
                        self._data_deferred = False
                        self.save_data()

    # ------------------------------------------------------------ Roots
    def roots(self) -> list[dict]:
        return self.config.get("roots", [])

    def set_roots(self, roots: list[dict], include_image_fallback: bool | None = None):
        self.config["roots"] = roots
        if include_image_fallback is not None:
            self.config["include_image_fallback"] = include_image_fallback
        for r in roots:
            c = str(r.get("category", "")).strip()
            if c:
                self.ensure_category(c)
        self.save_config()

    @property
    def include_image_fallback(self) -> bool:
        return bool(self.config.get("include_image_fallback", True))

    @property
    def archive_thumbs(self) -> bool:
        """Thumbnails from archive content for folders without images/videos."""
        return bool(self.config.get("archive_thumbs", True))

    def set_archive_thumbs(self, flag: bool):
        self.config["archive_thumbs"] = bool(flag)
        # Save immediately — it used to change memory only, so quitting
        # without touching any other setting lost the user's choice (guide
        # note).
        self.save_config()

    @property
    def folder_thumbs_only(self) -> bool:
        """Build posters for folders only; files get file-type cards (saves
        cache space and time — user suggestion for the capped-cache mode)."""
        return bool(self.config.get("folder_thumbs_only", False))

    def set_folder_thumbs_only(self, flag: bool):
        self.config["folder_thumbs_only"] = bool(flag)
        self.save_config()   # persist the user's choice right away

    @property
    def prebuild_thumbs(self) -> bool:
        """The "prebuild thumbnails after scan" setting (on by
        default, since it no longer blocks the app; background work,
        deferring to the UI)."""
        return bool(self.config.get("prebuild_thumbs", True))

    def set_prebuild_thumbs(self, flag: bool):
        self.config["prebuild_thumbs"] = bool(flag)
        self.save_config()

    def cache_max_mb(self) -> int:
        """Image cache size cap in MB — 0 means unlimited (default: 2048)."""
        try:
            v = int(self.config.get("cache_max_mb", 2048) or 0)
        except (TypeError, ValueError):
            v = 2048
        return max(0, v)

    def set_cache_max_mb(self, mb: int):
        try:
            self.config["cache_max_mb"] = max(0, int(mb))
        except (TypeError, ValueError):
            self.config["cache_max_mb"] = 0
        # this setter used to skip save_config() — changed outside
        # SettingsDialog the value never reached the disk.
        self.save_config()

    # ------------------------------------------------------------ Categories
    SEP = "/"

    @staticmethod
    def parent_of(cat: str) -> str | None:
        """Parent of a category: 'Anime/Watched' -> 'Anime'"""
        return cat.rsplit("/", 1)[0] if "/" in cat else None

    @staticmethod
    def matches(cat: str, flt: str) -> bool:
        """Does cat match filter flt? (a parent covers all its subcategories)"""
        return cat == flt or cat.startswith(flt + "/")

    def categories(self) -> list[str]:
        # Iterate the shared dicts under the lock — otherwise another
        # thread's setter can raise "dictionary changed size during
        # iteration"
        with self._lock:
            out: list[str] = list(self.config.get("categories", []))
            for r in self.roots():
                c = str(r.get("category", "")).strip()
                if c and c not in out:
                    out.append(c)
            for m in self.data.get("items", {}).values():
                c = (m.get("category") or "").strip()
                if c and c not in out:
                    out.append(c)
            return out

    def ensure_category(self, name: str):
        """Register a category and its parents: Anime/Watched -> Anime + Anime/Watched"""
        parts = [p.strip() for p in (name or "").split("/") if p.strip()]
        if not parts:
            return
        cats = self.config.setdefault("categories", [])
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in cats:
                cats.append(sub)
            self.color(sub)          # reserve a stable color for it
        self.save_config()

    def color(self, name: str) -> str:
        colors = self.config.setdefault("category_colors", {})
        v = colors.get(name)
        # a hand-edited/imported config can hold anything ("red;")
        # — it lands inside QSS strings, so only #rrggbb is accepted. A wrong
        # value self-heals back to a stable palette color.
        if not isinstance(v, str) or not _HEX6_RE.match(v.strip().lower()):
            v = PALETTE[len(colors) % len(PALETTE)]
            colors[name] = v
        return v

    def set_category_color(self, name: str, color) -> bool:
        """Hand-picked color for a category/subcategory ("can't
        the category colors be changed?").

        color must be a valid #rrggbb; None/"" means "back to automatic" —
        the hand-picked color is removed and the next color() call picks from
        the palette again (the previous default behavior). Subcategories are
        independent because the category_colors key is the full name. Saves
        immediately — True if anything changed."""
        name = str(name or "").strip()
        if not name:
            return False
        colors = self.config.setdefault("category_colors", {})
        if color:
            c = str(color).strip().lower()
            if not _HEX6_RE.match(c):
                return False
            changed = colors.get(name) != c
            colors[name] = c
        else:
            changed = name in colors
            colors.pop(name, None)
        if changed:
            self.save_config()
        return changed

    # ---- Quick category switch on cards (root + all children in one box) ----
    def child_categories(self, cat: str) -> list[str]:
        """Direct children of a category (exactly one level down), in config order."""
        prefix = cat + "/"
        out = [c for c in self.categories()
               if c.startswith(prefix) and "/" not in c[len(prefix):]]
        order = {name: i for i, name in enumerate(self.config.get("categories", []))}
        out.sort(key=lambda c: order.get(c, 1_000_000))
        return out

    def sibling_subcategories(self, cat: str) -> list[str]:
        """Subcategories sharing cat's parent (children of the same parent)."""
        parent = self.parent_of(cat)
        return self.child_categories(parent) if parent else []

    def cat_family(self, item) -> tuple[str, list[str]] | None:
        """The category family of a title: (root, [root + children]) — for
        the card's quick-switch box.

        If the title sits in a subcategory, root = its parent; if it sits in
        the root itself and that root has children, root = the root. With no
        subcategories at all -> None (no box wanted). The root itself stays
        in the list so the original category can be restored.
        """
        cat = self.category_of(item)
        base = self.parent_of(cat) or cat
        children = self.child_categories(base)
        if not children:
            return None
        return base, [base] + children

    def cat_switch_view(self, item) -> dict | None:
        """View model for a title card's quick category switch box.

        Options = the root category plus all of its subcategories; picking
        any of them switches the category.
        """
        fam = self.cat_family(item)
        if not fam:
            return None
        base, options = fam
        cat = self.category_of(item)
        labels = {}
        for full in options:
            labels[full] = full if full == base else "↳ " + full[len(base) + 1:]
        return {
            "kind": "combo",
            "options": options,
            "labels": labels,
            "current": cat,
            "tip": T("تغییر سریع دسته‌ی این عنوان — دسته‌ی اصلی و همه‌ی زیردسته‌ها"),
        }

    def remove_category(self, cat: str):
        """Remove an unused category from the list (leaves meta/roots alone)."""
        cats = self.config.setdefault("categories", [])
        if cat in cats:
            cats.remove(cat)
        self.config.get("category_colors", {}).pop(cat, None)
        self.save_config()

    def category_in_use(self, cat: str) -> bool:
        """Does any root or title use this category directly?"""
        with self._lock:                      # iterate under the lock
            for r in self.roots():
                if str(r.get("category", "")) == cat:
                    return True
            for m in self.data.get("items", {}).values():
                if (m.get("category") or "") == cat:
                    return True
        return False

    def set_category(self, path, cat: str):
        """Change a title's category (auto-registers the category and its parents)."""
        self.set_meta(path, category=cat)

    # ------------------------------------------------------------ Metadata
    def meta(self, path) -> dict:
        return self.data.get("items", {}).get(_meta_key(path), {})

    def set_meta(self, path, **kw):
        # snapshot->mutate->touch->save all under the same RLock — the shared
        # dict used to be mutated outside the lock and raced with another
        # thread's setter (live watching/prebuild): last-writer-wins dropped
        # the other's field.
        with self._lock:
            path = _meta_key(path)
            items = self.data.setdefault("items", {})
            meta = dict(items.get(path, {}))
            for k, v in kw.items():
                if v in (None, ""):
                    meta.pop(k, None)
                else:
                    meta[k] = v
            if "category" in kw and kw.get("category"):
                self.ensure_category(kw["category"])
            if meta:
                items[path] = meta
            else:
                items.pop(path, None)
            self._touch(path)
            self.save_data()

    # ---- Effective values (user edits applied) ----
    def title(self, item) -> str:
        # A mangled title (null/ANSI/newline in the file name or hand-edited
        # meta) is displayed clean; the stored value stays untouched.
        return ui_clean(self.meta(item.path).get("title") or item.name)

    def category_of(self, item) -> str:
        return self.meta(item.path).get("category") or item.category

    def poster_of(self, item, static: bool = False):
        """The title's effective poster: custom cover if present, else the
        folder icon.

        static=True wants the still frame of an animated cover — for the card
        grid and hero; the animation plays only in the details page."""
        poster, static_poster = self.custom_poster(item.path)
        if static and static_poster:
            return static_poster
        if poster:
            return poster
        return item.icon_path

    # ---- Custom covers ----
    def _resolve_cover_value(self, v) -> Path | None:
        """poster/poster_static meta value -> existing absolute path or None.

        New values are relative (.covers/<name>) and resolved via app_dir()
        so moving the whole portable folder does not break covers; old values
        were absolute paths from the legacy editor — still supported as-is. A
        missing file is logged and None returned; the meta is deliberately
        not cleared because the source drive may just be temporarily
        unmounted.
        """
        if not v:
            return None
        v = str(v).replace("\\", "/")
        if v.startswith("//"):
            # Security : a UNC value (possible via an imported
            # database) must not make the app connect to a network share
            # (NTLMv2 leak) just by displaying a title's poster.
            return None
        if v.startswith(".covers/"):
            p = portable.app_dir() / v
        else:
            p = Path(v)
        try:
            if p.is_file():
                return p
        except OSError as _exc_dbg:
            dbg_log(f"_resolve_cover_value failed: {type(_exc_dbg).__name__}")
            return None
        with self._lock:
            logged = getattr(self, "_cover_missing_logged", None)
            if logged is None:
                logged = self._cover_missing_logged = set()
            if str(p) not in logged:
                if len(logged) > 1024:
                    logged.clear()
                logged.add(str(p))
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"cover file missing: {safe_ref(p)}")
                except Exception as _exc_dbg:
                    dbg_log(f"_resolve_cover_value failed: {type(_exc_dbg).__name__}")
        return None

    def custom_poster(self, path) -> tuple[Path | None, Path | None]:
        """(cover, still frame of that cover) — absolute paths or None."""
        m = self.meta(path)
        poster = self._resolve_cover_value(m.get("poster"))
        if poster is None:
            return None, None
        static = self._resolve_cover_value(m.get("poster_static"))
        return poster, static

    def cover_refs(self) -> set[str]:
        """Every poster/poster_static value in the database — for orphan-cover
        garbage collection.

        The source must be the database itself, not the currently scanned
        items; otherwise the covers of titles under a temporarily offline
        root (unmounted drive/down network share) would look "orphaned" and
        be deleted — data loss. When the root returns, the cover is still in
        place because the meta was never touched.
        """
        with self._lock:
            out: set[str] = set()
            for meta in self.data.get("items", {}).values():
                for k in ("poster", "poster_static"):
                    v = meta.get(k)
                    if v:
                        out.add(str(v).replace("\\", "/"))
            return out

    def cover_status(self, path) -> dict:
        """Cover status for the UI (editor form): kind: custom|legacy|none + missing."""
        m = self.meta(path)
        v = m.get("poster")
        if not v:
            return {"kind": "none", "name": "", "missing": False}
        kind = "custom" if str(v).replace("\\", "/").startswith(".covers/") \
            else "legacy"
        p = self._resolve_cover_value(v)
        return {"kind": kind, "name": Path(str(v)).name,
                "missing": p is None}

    def set_custom_poster(self, item_path, info: dict) -> None:
        """Stores poster/poster_static meta (relative to the app folder).

        An old unreferenced file is deleted right away (so .covers/ does not
        bloat) — but file names are content hashes and two titles can share
        one file; deleting the old file unchecked also destroyed the other
        title's cover. Now, after the meta update, only files with no
        remaining reference in the database (cover_refs) are deleted; the
        rest go through collect_garbage. The whole operation runs under the
        same Store lock — with cover GC unlinking under that same lock,
        deleting a just-referenced file becomes impossible."""
        with self._lock:
            m = self.meta(item_path)
            old = (m.get("poster"), m.get("poster_static"))
            new = (info.get("poster") or None, info.get("poster_static") or None)
            self.set_meta(item_path, poster=new[0], poster_static=new[1])
            refs = self.cover_refs()            # after set_meta — includes other titles' refs
            stale = [v for v in old
                     if v and str(v).replace("\\", "/") not in refs]
            if stale:
                try:
                    from covers import delete_cover_files
                    delete_cover_files(stale[0],
                                       stale[1] if len(stale) > 1 else None)
                except Exception as _exc_dbg:
                    dbg_log(f"set_custom_poster cleanup failed: {type(_exc_dbg).__name__}")

    def clear_custom_poster(self, item_path) -> None:
        """Removes the keys + unreferenced files — idempotent (a missing file
        is not an error).

        Meta is cleared first, then only files no title references anymore
        are deleted — a cover shared with another title survives."""
        with self._lock:
            m = self.meta(item_path)
            old = (m.get("poster"), m.get("poster_static"))
            self.set_meta(item_path, poster=None, poster_static=None)
            refs = self.cover_refs()
            stale = [v for v in old
                     if v and str(v).replace("\\", "/") not in refs]
            if stale:
                try:
                    from covers import delete_cover_files
                    delete_cover_files(stale[0],
                                       stale[1] if len(stale) > 1 else None)
                except Exception as _exc_dbg:
                    dbg_log(f"clear_custom_poster failed: {type(_exc_dbg).__name__}")

    def desc(self, path) -> str:
        return self.meta(path).get("description", "")

    def fav(self, path) -> bool:
        return bool(self.meta(path).get("favorite", False))

    # ------------------------------------------------------
    def rating(self, path) -> int:
        """Personal rating 0-5 (star in details/sorting)."""
        try:
            v = int(self.meta(path).get("rating") or 0)
        except (TypeError, ValueError):
            v = 0
        return min(max(v, 0), 5)

    # "🕘 Recents" — the last played/opened titles (the name was the user's
    # own suggestion). At most RECENT_MAX entries, newest first.
    RECENT_MAX = 15

    @staticmethod
    def norm_path(p) -> str:
        r"""One comparable key for paths (recents once said 15 but
        shows 9").

        Comparison used to be raw string equality; "C:\Lib\Movies" versus
        "c:\lib\movies" or "C:\Lib\Movies\..\Movies" (case, separator, a ..\
        segment or its absence) produced two "new" records and the sidebar
        counter disagreed with the filtered list. normcase+normpath+abspath
        yields the Windows-style key, insensitive to case and separators."""
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))
        except (OSError, ValueError, TypeError):
            return str(p)

    def recents(self) -> list:
        r = self.config.get("recents")
        return [str(p) for p in r] if isinstance(r, list) else []

    def add_recent(self, path):
        path = str(path)
        if not path:
            return
        # Duplicates are removed by the normalized key (not just the literal
        # string) — the same path in a different separator/case spelling
        # cannot double up.
        nk = self.norm_path(path)
        r = [p for p in self.recents() if self.norm_path(p) != nk]
        r.insert(0, path)
        self.config["recents"] = r[: self.RECENT_MAX]
        # Deliberately _bump=False: a mere visit is not a "data change", so
        # the auto backup is not rewritten for nothing (user rule: only on a
        # real change).
        self.save_config(_bump=False)

    def prune_recents(self, valid_paths, protected=()) -> int:
        """Prune orphaned recents (the count once said 15 but showed 9).

        Called after every full scan: paths that no longer belong to any
        library title (moved/deleted/renamed folder) vanished from the
        sidebar count but stayed in config, creating that mismatch. The
        history itself is now cleaned so "stored == displayed". Protection:
        if the library is empty (failed scan/drive off) nothing is pruned, so
        one transient failure cannot wipe the whole history. Returns the
        number of removed entries."""
        valid = {self.norm_path(p) for p in (valid_paths or [])}
        keep_set = {self.norm_path(p) for p in (protected or ())}
        if not valid:
            return 0
        # Older configs can hold the same path twice in different spellings;
        # keep only the newest spelling.
        # protected: paths that must never be removed - main passes every
        # root this scan did NOT successfully read (offline drive) so a
        # partial multi-root scan cannot wipe that history.
        kept, removed, seen = [], 0, set()
        for p in self.recents():
            nk = self.norm_path(p)
            if nk in seen:
                removed += 1        # duplicate - newest spelling stays
                continue
            if nk in valid or nk in keep_set:
                kept.append(p)
                seen.add(nk)
            else:
                removed += 1
        if removed:
            self.config["recents"] = kept
            self.save_config(_bump=False)
        return removed

    # ---- Remove/move an item (missing-items manager: "delete" / "new path")
    def remove_item(self, path) -> bool:
        path = _meta_key(path)
        with self._lock:
            if path not in self.data.get("items", {}):
                return False
            self.data["items"].pop(path, None)
            self._touch(path)                   # DELETE via save_data
            self.save_data()
        return True

    def move_item(self, old, new) -> bool:
        """Moves a title's entire info (watch/category/description/rating) to
        a new path.

        Watch keys are relative (to the title folder) so they survive. If the
        destination already has info, the move is refused (nothing gets
        crushed)."""
        old, new = _meta_key(old), _meta_key(new)
        if old == new:
            return True
        with self._lock:
            items = self.data.setdefault("items", {})
            meta = items.pop(old, None)
            if meta is None or new in items:
                if meta is not None:
                    items[old] = meta           # restore — destination was occupied
                return False
            items[new] = meta
            self._touch(old)                    # remove the old key
            self._touch(new)                    # insert the new key
            self.save_data()
        return True

    # ---- Smart auto backup ----
    def auto_backup_cfg(self) -> dict:
        cfg = self.config.get("auto_backup")
        return dict(cfg) if isinstance(cfg, dict) else {}

    def set_auto_backup(self, enabled: bool, path: str | None = None,
                        include_cache: bool | None = None):
        """Auto backup switch + path + kind; None keeps the previous value.

        Two kinds — "simple" holds only data (paths, watch state,
        ratings, categories; light and fast) and "full" includes the
        thumbnail cache (instant restore, big). include_cache changes only
        when given explicitly; the default is "simple" to keep everyday
        rewrites cheap."""
        cur = self.auto_backup_cfg()
        cur["enabled"] = bool(enabled)
        if path is not None:
            cur["path"] = str(path)
        if include_cache is not None:
            cur["include_cache"] = bool(include_cache)
        if cur["enabled"] and not str(cur.get("path") or "").strip():
            cur["path"] = str(self.config_path.parent / "backups" / "auto-backup.mcbak")
        self.config["auto_backup"] = cur
        # save here too — this setter is also reachable without
        # SettingsDialog, and the change must survive a restart.
        self.save_config()

    # ---- Dedicated opener apps per category ----
    # "In the games section archives should open with WinRAR, manga with a
    # comic reader".
    # config["cat_openers"] = {"<category>": [{"exts": ["zip","rar"], "app": "C:/..."}]}
    # Inherits from the parent like track profiles; built at runtime (the
    # config default is static).
    def cat_openers(self, cat: str) -> list[dict]:
        """Effective opener rules for a category (nearest configured parent wins)."""
        m = self.config.get("cat_openers") or {}
        c = str(cat or "").strip()
        while c:
            rules = m.get(c)
            if isinstance(rules, list) and rules:
                out = []
                for r in rules:
                    try:
                        exts = [str(e).lower().lstrip(".") for e in (r.get("exts") or [])]
                        app = str(r.get("app") or "").strip()
                    except (AttributeError, TypeError):
                        continue
                    if exts and app:
                        out.append({"exts": exts, "app": app})
                if out:
                    return out
            c = self.parent_of(c)
        return []

    def set_cat_openers(self, cat: str, rules: list):
        """Rewrite a category's opener rules; empty list = remove (inherit from parent)."""
        cat = str(cat or "").strip()
        if not cat:
            return
        m = dict(self.config.get("cat_openers") or {})
        cur = m.get(cat) or []
        if list(rules or []) == list(cur):
            return
        if rules:
            m[cat] = rules
        else:
            m.pop(cat, None)
        if m:
            self.config["cat_openers"] = m
        else:
            self.config.pop("cat_openers", None)
        self.save_config()

    def cat_opener_for(self, cat: str, suffix: str) -> str:
        """Opener app path for this extension in this category (or "" = system default)."""
        sfx = str(suffix or "").lower().lstrip(".")
        if not sfx:
            return ""
        for r in self.cat_openers(cat):
            if sfx in r["exts"]:
                return r["app"]
        return ""

    def cat_openers_map(self) -> dict:
        """Copy of all opener rules (categories tab/tests)."""
        m = self.config.get("cat_openers") or {}
        return {c: [dict(r) for r in rs if isinstance(r, dict)]
                for c, rs in m.items() if isinstance(rs, list)}

    # ------------------------------------------------------------ Watch state
    # watch: {"<relative file path>": {"m": 1|2, "p": seconds}}
    #   m=1 watched ✓ | m=2 partial ◐ (with p = resume position)
    WATCH_DONE, WATCH_PART = 1, 2

    def watch_map(self, item) -> dict:
        m = self.meta(item.path).get("watch")
        return m if isinstance(m, dict) else {}

    # ---- Track profile per category (video/book/none/custom — inherited from parent) ----
    @staticmethod
    def builtin_profile(key: str) -> dict | None:
        return BUILTIN_TRACK_PROFILES.get(key)

    def custom_profiles(self) -> dict:
        m = self.config.get("track_profiles")
        return m if isinstance(m, dict) else {}

    def save_custom_profile(self, title: str, done: str, part: str,
                            none: str = "", pid: str | None = None) -> str:
        """Add/edit a custom profile -> returns the profile key."""
        import hashlib as _hl
        profs = dict(self.config.setdefault("track_profiles", {}))
        pid = pid or ("u_" + _hl.sha1(
            f"{title}|{done}|{part}".encode("utf-8")).hexdigest()[:8])
        if pid in BUILTIN_TRACK_PROFILES:   # must not collide with a built-in key
            pid = "u_" + pid
        profs[pid] = {
            "title": str(title).strip() or T("الگو"),
            "done": str(done).strip() or T("تمام شد"),
            "part": str(part).strip() or T("نیمه"),
            "none": str(none).strip(),
        }
        self.config["track_profiles"] = profs
        self.save_config()
        return pid

    def delete_custom_profile(self, pid: str):
        profs = dict(self.config.setdefault("track_profiles", {}))
        profs.pop(pid, None)
        self.config["track_profiles"] = profs
        # Categories that had it -> unassigned (auto)
        m = {c: k for c, k in (self.config.get("cat_track_profile") or {}).items()
             if k != pid}
        self.config["cat_track_profile"] = m
        self.save_config()

    def profile_options(self) -> list[tuple[str, str]]:
        """Profile box options: (key, display name) — built-ins + customs."""
        out = [(k, v["title"]) for k, v in BUILTIN_TRACK_PROFILES.items()]
        for pid, p in self.custom_profiles().items():
            out.append((pid, p.get("title") or pid))
        return out

    def cat_track_profile_key(self, cat: str) -> str | None:
        """Explicit key assigned to the category or one of its parents (None = auto)."""
        m = self.config.get("cat_track_profile") or {}
        c = str(cat or "").strip()
        while c:
            if c in m:
                return m[c]
            c = self.parent_of(c)
        return None

    def set_cat_track_profile(self, cat: str, key: str | None):
        """Assign/remove a category's profile (key=None or "auto" = unassign)."""
        m = dict(self.config.get("cat_track_profile") or {})
        c = str(cat or "").strip()
        if not c:
            return
        if key and key != "auto" and (
                key in BUILTIN_TRACK_PROFILES or key in self.custom_profiles()):
            m[c] = key
        else:
            m.pop(c, None)
        self.config["cat_track_profile"] = m
        self.save_config()

    def _legacy_off_key(self, cat: str) -> str | None:
        """Legacy compatibility: the old cat_status_off list -> the "none" profile."""
        cat = str(cat or "").strip()
        for o in (self.config.get("cat_status_off") or []):
            o = str(o).strip()
            if o and (cat == o or cat.startswith(o + "/")):
                return "none"
        return None

    def track_profile(self, cat: str) -> dict:
        """The effective profile for a category — always returns a full dict
        of labels.

        Output: {"id": key, "title": name, plus for auto: vid/doc label
        dicts, for the rest: done/part/act, plus the custom and enabled
        flags}
        """
        key = (self.cat_track_profile_key(cat)
               or self._legacy_off_key(cat) or "auto")
        custom = self.custom_profiles().get(key)
        base = dict(BUILTIN_TRACK_PROFILES.get(key) or {})
        if custom and not base:
            base = {"title": custom.get("title", key),
                    "done": custom.get("done", T("تمام شد")),
                    "part": custom.get("part", T("نیمه")),
                    "act": custom.get("title", T("پیشرفت"))}
        if key not in BUILTIN_TRACK_PROFILES and not custom:
            key, base = "auto", dict(BUILTIN_TRACK_PROFILES["auto"])  # broken -> auto
        return {
            "id": key,
            "title": base.get("title", key),
            "done": base.get("done", ""),
            "part": base.get("part", ""),
            "act": base.get("act", ""),
            "vid": base.get("vid", BUILTIN_TRACK_PROFILES["auto"]["vid"]),
            "doc": base.get("doc", BUILTIN_TRACK_PROFILES["auto"]["doc"]),
            "enabled": key != "none",
            "custom": custom is not None,
        }

    def cat_track_status(self, cat: str) -> bool:
        """Do this category's titles get status marks (✓/◐)? (profile != no tracking)"""
        return bool(self.track_profile(cat)["enabled"])

    def cat_status_off_list(self) -> list[str]:
        """Categories assigned to "none" (plus legacy cat_status_off entries)."""
        out = [str(c).strip() for c in (self.config.get("cat_status_off") or [])
               if str(c).strip()]
        for c, k in (self.config.get("cat_track_profile") or {}).items():
            if k == "none" and c not in out:
                out.append(c)
        return out

    def set_cat_track_statuses(self, off: list):
        """Legacy compatibility — the off list becomes the "none" profile (the rest are freed)."""
        m = dict(self.config.get("cat_track_profile") or {})
        off_set = {str(c).strip() for c in (off or []) if str(c).strip()}
        for c in off_set:
            m[c] = "none"
        for c in [c for c, k in m.items() if k == "none" and c not in off_set]:
            m.pop(c, None)
        self.config["cat_track_profile"] = m
        self.config["cat_status_off"] = []
        self.save_config()

    def guess_profile_key(self, cat: str) -> str:
        """Smart default when adding a path: book/manga-like name -> book, else video."""
        low = str(cat or "").casefold()
        return "book" if any(h in low for h in _BOOK_HINTS) else "video"

    def watch_get(self, item, rel: str) -> tuple[int, float]:
        e = self.watch_map(item).get(rel) or {}
        try:
            return int(e.get("m") or 0), float(e.get("p") or 0.0)
        except (TypeError, ValueError):
            return 0, 0.0

    def watch_set(self, item, rel: str, mode: int, pos: float = 0.0):
        # Canonical key + the whole operation under the lock — the raw key
        # used to be written as "C:/x/y" while watch_get read via _meta_key
        # as "C:\x\y", losing watch state for items with non-canonical keys.
        path = _meta_key(item.path)
        with self._lock:
            items = self.data.setdefault("items", {})
            meta = dict(items.get(path, {}))
            watch = dict(meta.get("watch") or {})
            if mode:
                watch[rel] = {"m": int(mode), "p": round(float(pos), 1)}
            else:
                watch.pop(rel, None)
            if watch:
                meta["watch"] = watch
            else:
                meta.pop("watch", None)
            if meta:
                items[path] = meta
            else:
                items.pop(path, None)
            self._touch(path)
            self.save_data()

    def watch_progress(self, item) -> tuple[int, int]:
        """Returns: (number watched, number partial)"""
        # Count only records that belong to the item's watchable_total basis:
        # a video title counts only video-file records (poster/pdf sidecar
        # records written by W are NOT episodes); a non-video title counts
        # all records (the same set item_watchables returns). Previously
        # every watch record counted, so 10 real episodes showed as
        # "13 episodes watched (130%)".
        vids_only = (getattr(item, "video_count", 0) or 0) > 0
        done = part = 0
        for rel, e in self.watch_map(item).items():
            if vids_only and not _rel_is_video(rel):
                continue
            try:
                m = int(e.get("m") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if m == self.WATCH_DONE:
                done += 1
            elif m == self.WATCH_PART:
                part += 1
        return done, part

    # "Readable" extensions: documents + compressed books (comics/e-books).
    # Note: .cbz/.cbr/.cb7/.epub are in scanner's ARCHIVE_EXTS and group_of
    # reports them as "archive"; for the "reading %" stat they must count as
    # books.
    _READING_FILE_EXTS = frozenset({
        ".pdf", ".txt", ".md", ".doc", ".docx", ".rtf", ".odt",
        ".cbz", ".cbr", ".cb7", ".epub",
    })

    def progress_kind(self, item) -> str:
        """The trackable content kind of a title: "video" | "book" | "none" |
        "files".

        Basis for splitting the sidebar stat lines ("video
        watch %" and "book reading %" separate and explicit; a category with
        neither — like Games — gets no percentage line):
          · An explicit category profile (cat_track_profile, inherited from
            the parent) wins: video -> video, book -> book, none -> nothing;
          · auto/custom: a video-bearing title -> video; a book-like category
            name -> book; otherwise "files" means bucketing by the files'
            real types (book/image/other — games and non-book archives never
            reach a percentage).
        """
        try:
            cat = str(self.meta(item.path).get("category")
                      or getattr(item, "category", "") or "").strip()
        except (AttributeError, TypeError):
            cat = str(getattr(item, "category", "") or "").strip()
        prof = self.track_profile(cat)
        if not prof.get("enabled", True):
            return "none"                       # "no tracking" -> no stat line
        pid = str(prof.get("id") or "auto")
        if pid == "video":
            return "video"
        if pid == "book":
            return "book"
        # auto or a custom profile -> decided by the title's actual content
        if (getattr(item, "video_count", 0) or 0) > 0:
            return "video"
        if self.guess_profile_key(cat) == "book":
            return "book"
        return "files"

    def _file_bucket(self, rel: str) -> str:
        """A file's bucket for the auto/custom profile: "video" | "book" |
        "image" | "other".

        Extension only (the project's real classification, not the folder
        name); "other" files (games, generic archives, audio, executables)
        never appear in any percentage."""
        try:
            from scanner import VIDEO_EXTS, IMAGE_EXTS_SET
            ext = os.path.splitext(str(rel))[1].lower()
            if ext in VIDEO_EXTS:
                return "video"
            if ext in self._READING_FILE_EXTS:
                return "book"
            if ext in IMAGE_EXTS_SET:
                return "image"
        except Exception as _exc_dbg:
            dbg_log(f"_file_bucket failed: {type(_exc_dbg).__name__}")
        return "other"

    def watch_stats_by_kind(self, item) -> dict:
        """Per-title progress stats split by content bucket.

        Output: {"video": (done, part, total), "book": ..., "image": ...,
        "other": ...}

          · Video title: the denominator is the *current* video files, or
            video_count when the disk is unreachable (same basis as the
            episode count); only video records count (a poster/PDF sidecar
            is not an episode).
          · Others: the denominator comes from the *current* files on disk
            and a mark counts only while its file still exists — an orphaned
            record for a deleted file never enters the percentage (user
            request; root cause of "0 episodes but 3 watched" and
            percentages over 100).
          · The "no tracking" profile returns empty buckets.
          · Denominators are cached on the item itself (same pattern as
            watchable_total) so typing in the search filter does not hit the
            disk on every keystroke; marks are read live so status changes
            show up immediately.
        """
        out = {"video": (0, 0, 0), "book": (0, 0, 0),
               "image": (0, 0, 0), "other": (0, 0, 0)}
        kind = self.progress_kind(item)
        if kind == "none":
            return out
        # ---- Denominators: current files (light disk cache, same key as watchable_total)
        # the key must include the tracking kind — the cached
        # totals depend on the item's progress_kind, and a category/profile
        # change (video → book) with an unchanged folder mtime used to serve
        # the previous kind's stale denominator.
        key = ((getattr(item, "modified", 0.0) or 0.0), kind)
        cached = getattr(item, "_wsbk36", None)
        if cached is not None and cached[0] == key:
            totals, cur = cached[1], cached[2]
        else:
            totals = {"video": 0, "book": 0, "image": 0, "other": 0}
            cur: set = set()
            if kind == "video":
                try:
                    from scanner import item_watchables
                    files = item_watchables(item)
                except Exception as _exc_dbg:
                    dbg_log(f"watch_stats_by_kind failed: {type(_exc_dbg).__name__}")
                    files = []
                if files:
                    totals["video"] = len(files)          # disk reachable -> real basis
                    cur = {_rel_key(item, f).lower() for f in files}
                else:
                    totals["video"] = max(0, (getattr(item, "video_count", 0) or 0))
                    cur = None                            # drive offline -> video_count basis
            else:
                try:
                    from scanner import item_watchables
                    files = item_watchables(item)
                except Exception as _exc_dbg:
                    dbg_log(f"watch_stats_by_kind failed: {type(_exc_dbg).__name__}")
                    files = []
                if kind == "book":
                    # Picture comics and book files are all "book" — one bucket
                    totals["book"] = len(files)
                else:
                    for f in files:
                        totals[self._file_bucket(_rel_key(item, f))] += 1
                cur = {_rel_key(item, f).lower() for f in files}
            try:
                item._wsbk36 = (key, totals, cur)
            except Exception as _exc_dbg:
                dbg_log(f"watch_stats_by_kind failed: {type(_exc_dbg).__name__}")
        # ---- Numerators: valid marks — existing files only, DONE/PART only
        marks = {"video": [0, 0], "book": [0, 0], "image": [0, 0], "other": [0, 0]}
        for rel, e in self.watch_map(item).items():
            try:
                m = int((e or {}).get("m") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if m not in (self.WATCH_DONE, self.WATCH_PART):
                continue
            r = str(rel).replace("\\", "/").lower()
            if kind == "video":
                if not _rel_is_video(r):
                    continue            # a poster/PDF sidecar is not an episode
                if cur is not None and r not in cur:
                    continue            # orphan: deleted episode, not counted until the next scan
                b = "video"
            elif kind == "book":
                if r not in cur:
                    continue            # orphan
                b = "book"              # picture comics count as "book" too
            else:
                if r not in cur:
                    continue            # orphan
                b = self._file_bucket(r)
            marks[b][0 if m == self.WATCH_DONE else 1] += 1
        for b in out:
            d, p = marks[b]
            out[b] = (d, p, totals.get(b, 0))
        return out

    def scope_progress_by_kind(self, items) -> dict:
        """Sidebar progress totals split by content bucket (replaces the
        earlier dual-basis scope_progress).

        The mixed "N of M items completed" line (books+images+
        games in one denominator) must go; each scope reports only the
        percentages of its own content kinds, and "no tracking" contributes
        nothing. Output:
        {"video": (done, part, total), "book": ..., "image": ..., "other": ...}
        """
        acc = {"video": [0, 0, 0], "book": [0, 0, 0],
               "image": [0, 0, 0], "other": [0, 0, 0]}
        for it in items:
            for b, (d, p, t) in self.watch_stats_by_kind(it).items():
                a = acc[b]
                a[0] += d
                a[1] += p
                a[2] += t
        return {b: (a[0], a[1], a[2]) for b, a in acc.items()}

    def has_partial(self, item) -> bool:
        return self.watch_progress(item)[1] > 0

    def watchable_total(self, item) -> int:
        """Total consumable files of a title — the basis for "fully watched".

        The "make the watched part category-aware and cover all
        categories". For a video title it is the video count; for the rest
        (books, compressed manga, images, audio, documents, apps) the count
        of consumable files on disk — subtitles and sidecars are never part
        of the total. The result is cached on the item (key = the folder
        mtime recorded by the scan) so typing in the search filter does not
        re-hit the disk.
        """
        v = getattr(item, "video_count", 0) or 0
        if v > 0:
            return v
        key = getattr(item, "modified", 0.0) or 0.0
        cached = getattr(item, "_wc48", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            from scanner import item_watchables
            n = len(item_watchables(item))
        except Exception as _exc_dbg:
            dbg_log(f"watchable_total failed: {type(_exc_dbg).__name__}")
            n = 0
        try:
            item._wc48 = (key, n)
        except Exception as _exc_dbg:
            dbg_log(f"watchable_total failed: {type(_exc_dbg).__name__}")
            pass
        return n

    # ---- Theme and durations ----
    @property
    def theme(self) -> str:
        return (self.config.get("theme") or "dark").strip() or "dark"

    def set_theme(self, name: str):
        self.config["theme"] = name
        self.save_config()

    # ---- Language ----
    @property
    def language(self) -> str:
        v = (self.config.get("language") or "en").strip().lower()
        return "fa" if v == "fa" else "en"

    def set_language(self, code: str):
        self.config["language"] = "fa" if str(code).strip().lower() == "fa" else "en"
        self.save_config()

    # ---- Performance mode ----
    @property
    def perf_mode(self) -> str:
        v = (self.config.get("perf_mode") or "auto").strip().lower()
        return v if v in ("auto", "high", "safe") else "auto"

    def set_perf_mode(self, mode: str):
        v = str(mode).strip().lower()
        self.config["perf_mode"] = v if v in ("auto", "high", "safe") else "auto"
        self.save_config()

    # ---- Persisted disk kinds (stable worker counts across runs) ----
    @property
    def disk_kinds(self) -> dict:
        d = self.config.get("disk_kinds")
        return d if isinstance(d, dict) else {}

    def set_disk_kinds(self, kinds: dict):
        if isinstance(kinds, dict) and kinds:
            self.config["disk_kinds"] = kinds
            self.save_config()

    @property
    def compute_durations(self) -> bool:
        return bool(self.config.get("compute_durations", True))

    def set_compute_durations(self, flag: bool):
        self.config["compute_durations"] = bool(flag)
        self.save_config()

    # ---- Player (for resume) ----
    # config["player"]: {"mode": "auto|mpv|vlc|default|custom", "path": "..."}
    @property
    def player_cfg(self) -> dict:
        p = self.config.get("player")
        return p if isinstance(p, dict) else {"mode": "auto", "path": ""}

    def set_player_cfg(self, mode: str, path: str = ""):
        self.config["player"] = {"mode": mode, "path": path}
        self.save_config()

    def total_duration(self, item) -> int:
        """A title's computed total duration (seconds). 0 = unknown."""
        try:
            return int(self.meta(item.path).get("total_dur") or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------ Duration manifests
    # Manifest = map {relative video path: [size, mtime_ns]} — when the
    # folder is unchanged, duration computation is skipped entirely (fast).
    def manifest_of(self, item) -> dict | None:
        m = self.meta(item.path).get("vids")
        return m if isinstance(m, dict) else None

    def set_total_durations(self, durations: dict, manifests: dict | None = None):
        """Bulk-record every title's total duration after a scan (a single
        file save).

        Under the lock + canonical keys — the durations worker runs on a
        background thread while the UI can concurrently write watch/meta;
        the shared dict used to be mutated outside the lock."""
        with self._lock:
            items = self.data.setdefault("items", {})
            for path, dur in durations.items():
                # The raw key is kept before canonicalization — the "raw
                # key" fallback used to be read after path had already been
                # rewritten to the canonical key, so it never fired; a future
                # caller building manifests with raw keys was silently lost
                raw = path
                path = _meta_key(path)
                meta = dict(items.get(path, {}))
                if dur:
                    meta["total_dur"] = int(dur)
                else:
                    meta.pop("total_dur", None)
                if manifests is not None:
                    man = manifests.get(str(path))
                    if not man:
                        man = manifests.get(str(raw))   # compatibility with old callers' raw keys
                    if man:
                        meta["vids"] = man
                    else:
                        meta.pop("vids", None)
                if meta:
                    items[path] = meta
                else:
                    items.pop(path, None)
                self._touch(path)
            self.save_data()

    def clear_all_manifests(self):
        """Remove every title's duration manifest and total (full recompute on
        the next scan).

        "or" bug — when vids existed, the right-hand pop of the or never ran
        and a stale total_dur survived; now both are popped independently and
        the changed flag is built from both results."""
        with self._lock:
            items = self.data.get("items", {})
            for p, meta in list(items.items()):      # list(): we may delete while iterating
                vids = meta.pop("vids", None)
                total = meta.pop("total_dur", None)
                if vids is not None or total is not None:
                    if not meta:
                        items.pop(p, None)
                    self._touch(p)
            self.save_data()

    # ------------------------------------------------------------ Thumbnail manifests
    # Thumbnail manifest = record {"m": {relative path: [size, mtime_sec]},
    # "r": retries, "e": era} — the same pattern as the duration manifest but
    # for thumbnail prebuild ("skip files that already have
    # thumbnails and re-check only when changed"). The prebuilder owns the
    # field semantics; here it is only bulk save/load (important: living
    # inside data.db it also lands in .mcbak backups, so skipping on import
    # keeps working).
    def thumb_state_of(self, item) -> dict | None:
        """A title's thumbnail manifest record (or None if not recorded yet)."""
        m = self.meta(item.path).get("thumbs")
        return m if isinstance(m, dict) and isinstance(m.get("m"), dict) else None

    def set_thumb_states(self, states: dict):
        """Bulk-record titles' thumbnail manifests after a full prebuild (one
        save).

        The prebuilder calls from a background thread — under the lock +
        canonical keys."""
        with self._lock:
            items = self.data.setdefault("items", {})
            for path, rec in states.items():
                path = _meta_key(path)
                meta = dict(items.get(path, {}))
                if rec and isinstance(rec.get("m"), dict):
                    meta["thumbs"] = {"m": rec["m"],
                                      "r": int(rec.get("r") or 0),
                                      "e": str(rec.get("e") or "")}
                else:
                    meta.pop("thumbs", None)
                if meta:
                    items[path] = meta
                else:
                    items.pop(path, None)
                self._touch(path)
            self.save_data()

    def clear_thumb_manifests(self):
        """Remove every title's thumbnail manifest — after clearing/trimming
        the cache.

        When the thumbnail cache is cleared or trimmed by the size cap (LRU),
        the manifests no longer guarantee "everything was built"; wipe them
        so the next run's prebuild re-checks every title once, smartly."""
        with self._lock:                      # iterate/mutate under the lock
            items = self.data.get("items", {})
            changed = False
            for p, meta in list(items.items()):      # list(): we may delete while iterating
                if isinstance(meta, dict) and meta.pop("thumbs", None) is not None:
                    if not meta:
                        items.pop(p, None)
                    self._touch(p)
                    changed = True
            if changed:
                self.save_data()


