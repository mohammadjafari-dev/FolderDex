# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Data backup/restore.

The export is a *single file* with the dedicated .mcbak extension
(deflate-standard ZIP): config.json + data.db + data, and optionally the
whole `.thumb_cache/` plus a small manifest.

A backup import could leave the app unable to start with
sqlite3.DatabaseError: database disk image is malformed; three structural
fixes for SQLite safety:

1) **Crash-safe snapshot at export time**: data.db and durations.db are both
   WAL databases; a raw copy of the live file plus its -wal/-shm sidecars
   captured at different moments was inconsistent. The export now uses the
   SQLite Backup API to take a *self-contained single-file, consistent*
   copy; -wal/-shm/-journal files never enter the backup.

2) **Orphan-sidecar cleanup at apply time**: before data.db is overwritten,
   any old destination sidecar that is not in the backup is removed — the
   "new db + old WAL" combination (the root cause of malformed) becomes
   impossible.

3) **Database validation**: quick_check runs both while staging (before the
   relaunch) and while applying at boot; a corrupt backup never lands on
   healthy data. On import, byte progress is reported to the UI (progress
   bar + ETA in Settings).

Restore follows the same safe flag pattern: extraction into
`.mc_import_pending` + the `._mc_import` flag, with the real overwrite on the
next boot, before any database is opened. Compatibility: the legacy backup
folder and the current single-file backup are both accepted.
"""
from __future__ import annotations

from i18n import T

import os
import shutil
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath

from portable import app_dir
from watchtime import dbg_log
from covers import COVERS_DIR_NAME

# Core data files next to the app (the cache is a separate user choice)
DATA_ITEMS = ("config.json", "data.json", "data.db",
              "data.db-shm", "data.db-wal", "data.db-journal")
CACHE_DIR_NAME = ".thumb_cache"

# Dedicated backup-file extension
BACKUP_EXT = ".mcbak"

# Manifest inside the file — for quick validation and user transparency
_MANIFEST_NAME = "backup-manifest.json"

_IMPORT_FLAG_NAME = "._mc_import"
_IMPORT_PENDING_NAME = ".mc_import_pending"

# Databases snapshotted via the Backup API (never a raw copy of the live file)
_SNAPSHOT_DBS = ("data.db", f"{CACHE_DIR_NAME}/durations.db")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _cache_root() -> Path:
    """The thumbnail cache's real path — no longer always app/.thumb_cache.

    With MC_CACHE_DIR the cache can live elsewhere, and in the read-only
    fallback the effective path can differ too; a backup that read/wrote a
    fixed path either missed the real cache or — worse — wiped the user's
    custom cache at apply time. export/restore now read the same
    paths.thumb_cache_dir (one rule, one place). Member names inside the zip
    stay fixed `.thumb_cache/...` — the backup file format is untouched."""
    try:
        from paths import thumb_cache_dir
        return Path(thumb_cache_dir())
    except Exception as _exc_dbg:
        dbg_log(f"_cache_root fallback: {type(_exc_dbg).__name__}")
        return Path(app_dir()) / CACHE_DIR_NAME


def _data_items(app) -> list[str]:
    """User data files (config/db + custom covers in .covers/).

    Cover backup: cover files live in APP_DIR/.covers/ and must travel with
    the database — otherwise after a restore every custom cover falls back to
    the folder icon.
    """
    items = list(DATA_ITEMS)
    cov = Path(app) / COVERS_DIR_NAME
    try:
        if cov.is_dir():
            items += [f"{COVERS_DIR_NAME}/{p.name}" for p in sorted(cov.iterdir())
                      if p.is_file()]
    except OSError:
        pass
    return items


def default_backup_name() -> str:
    """Suggested backup file name — for the save dialog."""
    import datetime as _dt
    return ("FolderDex-backup-"
            + _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + BACKUP_EXT)


# ------------------------------------------------------------------- SQLite
def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """Consistent snapshot of a live (WAL) SQLite into a single file — Backup API.

    Even while the app holds the database open, this parallel connection takes
    a transactionally consistent copy (as safe as VACUUM INTO). Momentary
    locks: one retry."""
    import time as _t
    last = None
    for _try in range(2):
        s = None
        d = None
        try:
            s = sqlite3.connect(str(src), timeout=15.0)
            d = sqlite3.connect(str(dst))
            s.backup(d)
            return
        except sqlite3.Error as e:
            last = e
            _t.sleep(0.4)
        finally:
            for c in (d, s):
                try:
                    if c is not None:
                        c.close()
                except Exception as _exc_dbg:
                    dbg_log(f"_snapshot_sqlite failed: {type(_exc_dbg).__name__}")
                    pass
    raise last or sqlite3.Error("snapshot failed")


def _db_ok(path) -> bool:
    """quick_check on a SQLite file — True only if it opens fully healthy."""
    con = None
    try:
        con = sqlite3.connect(str(path), timeout=8.0)
        res = con.execute("PRAGMA quick_check").fetchone()
        return bool(res and str(res[0]).lower() == "ok")
    except sqlite3.Error:
        return False
    finally:
        try:
            if con is not None:
                con.close()
        except Exception as _exc_dbg:
            dbg_log(f"_db_ok failed: {type(_exc_dbg).__name__}")
            pass


def _is_snapshot_db(arc_name: str) -> bool:
    """Is this member one of the snapshotted databases? (POSIX relative path inside the file)"""
    return arc_name in _SNAPSHOT_DBS


def _is_snapshot_sidecar(arc_name: str) -> bool:
    """Is this a -wal/-shm/-journal sidecar of a snapshotted database?"""
    return any(arc_name == db + sfx for db in _SNAPSHOT_DBS for sfx in _SIDECAR_SUFFIXES)


# ------------------------------------------------------------- size estimate
def estimate_export_size(include_cache: bool) -> tuple[int, int]:
    """(file count, total bytes) of the data that will be exported — for the dialog text."""
    app = app_dir()
    files = 0
    bytes_ = 0
    for name in _data_items(app):
        p = app / name
        try:
            if p.is_file():
                files += 1
                bytes_ += p.stat().st_size
        except OSError:
            pass
    if include_cache:
        cache = _cache_root()          # the effective cache path — not the fixed one
        try:
            if cache.is_dir():
                for p in cache.rglob("*"):
                    if not p.is_file():
                        continue
                    rel = (PurePosixPath(CACHE_DIR_NAME)
                           / p.relative_to(cache).as_posix()).as_posix()
                    if _is_snapshot_sidecar(rel):
                        continue
                    files += 1
                    try:
                        bytes_ += p.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return files, bytes_


def estimate_backup_size(path) -> tuple[int, int]:
    """(file count, total uncompressed bytes) of a backup file/folder's contents."""
    path = Path(path)
    files = 0
    bytes_ = 0
    try:
        if path.is_file():
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    n = info.filename
                    if n in DATA_ITEMS or n.startswith(CACHE_DIR_NAME + "/") \
                            or n.startswith(COVERS_DIR_NAME + "/"):
                        files += 1
                        bytes_ += max(0, int(info.file_size))
        elif path.is_dir():
            for name in DATA_ITEMS:
                p = path / name
                if p.is_file():
                    files += 1
                    bytes_ += p.stat().st_size
            cov = path / COVERS_DIR_NAME
            if cov.is_dir():
                for p in cov.rglob("*"):
                    if p.is_file():
                        files += 1
                        try:
                            bytes_ += p.stat().st_size
                        except OSError:
                            pass
            cache = path / CACHE_DIR_NAME
            if cache.is_dir():
                for p in cache.rglob("*"):
                    if p.is_file():
                        files += 1
                        try:
                            bytes_ += p.stat().st_size
                        except OSError:
                            pass
    except (zipfile.BadZipFile, OSError):
        pass
    return files, bytes_


# ---------------------------------------------------------------------- export
def export_backup(dest_path, include_cache: bool = True, progress=None,
                  atomic: bool = False) -> tuple:
    """Build a .mcbak backup file at dest_path → (file, count, bytes).

    data.db and .thumb_cache/durations.db are snapshotted via the
    Backup API — -wal/-shm/-journal sidecars never enter the file (malformed
    fix). progress(done_bytes, total_bytes, files_done, files_total) is called
    after each file (Settings progress bar).
    Errors are raised to the caller and the half-written file is removed.
    atomic=True is for "automatic backup" — written first as .part
    and replaced crash-safely only when complete, so the overwritten target
    never stays half-done."""
    final = Path(dest_path)
    if final.suffix.lower() != BACKUP_EXT:
        final = final.with_suffix(BACKUP_EXT)
    final.parent.mkdir(parents=True, exist_ok=True)
    out = final.with_name(final.name + ".part") if atomic else final

    import datetime as _dt
    import json as _json
    import tempfile as _tmp
    try:
        from appinfo import APP_VERSION
    except Exception as _exc_dbg:  # pragma: no cover
        dbg_log(f"export_backup failed: {type(_exc_dbg).__name__}")
        APP_VERSION = "?"
    manifest = {
        "app": "FolderDex",
        "version": APP_VERSION,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "include_cache": bool(include_cache),
        "snapshot": True,          # databases are consistent snapshots
        # The backup file's content is a "file format" and must stay stable
        # across languages/versions (manual review — this string is not translated)
        "note": "فایلِ پشتیبانِ FolderDex — استاندارد ZIP با پسوند اختصاصی",
    }

    app = app_dir()
    _files_t, total_bytes = estimate_export_size(include_cache)
    files = 0
    done = 0

    def _tick(b: int = 0):
        nonlocal done
        done += abs(int(b or 0))
        if progress is not None:
            try:
                progress(min(done, max(total_bytes, 1)), max(total_bytes, 1),
                         files, max(_files_t, 1))
            except Exception as _exc_dbg:
                dbg_log(f"export_backup._tick failed: {type(_exc_dbg).__name__}")
                pass

    try:
        with _tmp.TemporaryDirectory(prefix="mcbak-") as tdir:
            tmp = Path(tdir)

            def _emit(zf, src: Path, arc: str):
                """Adds one source (real file or snapshot) into the zip."""
                if _is_snapshot_db(arc):
                    snap = tmp / ("snap-" + Path(arc).name)
                    try:
                        _snapshot_sqlite(src, snap)
                    except sqlite3.Error:
                        if arc == "data.db":
                            raise          # user data — an inconsistent export is forbidden
                        return               # durations cache: reproducible → just omitted
                    zf.write(snap, arcname=arc)
                    try:
                        _tick(snap.stat().st_size)
                    except OSError:
                        _tick(0)
                else:
                    zf.write(src, arcname=arc)
                    try:
                        _tick(src.stat().st_size)
                    except OSError:
                        _tick(0)

            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zf:
                zf.writestr(_MANIFEST_NAME,
                            _json.dumps(manifest, ensure_ascii=False, indent=2))
                for name in _data_items(app):
                    # WAL sidecars never appear in the export — the snapshot suffices
                    if _is_snapshot_sidecar(name):
                        continue
                    src = app / name
                    try:
                        if src.is_file():
                            _emit(zf, src, name)
                            files += 1
                    except OSError:
                        continue
                if include_cache:
                    cache = _cache_root()      # the effective cache path
                    if cache.is_dir():
                        for p in cache.rglob("*"):
                            if not p.is_file():
                                continue
                            arc = (PurePosixPath(CACHE_DIR_NAME)
                                   / p.relative_to(cache).as_posix()).as_posix()
                            if _is_snapshot_sidecar(arc):
                                continue
                            try:
                                _emit(zf, p, arc)
                                files += 1
                            except OSError:
                                continue
    except Exception as _exc_dbg:
        dbg_log(f"export_backup failed: {type(_exc_dbg).__name__}")
        try:
            out.unlink(missing_ok=True)         # do not leave a half-written file
        except OSError:
            pass
        raise
    # final latch: the snapshot size may differ slightly from the source — close the bar at 100%
    if progress is not None:
        try:
            progress(max(total_bytes, 1), max(total_bytes, 1),
                     files, max(_files_t, 1))
        except Exception as _exc_dbg:
            dbg_log(f"export_backup failed: {type(_exc_dbg).__name__}")
            pass
    if atomic:                                   # crash-safe replacement of the final target
        try:
            os.replace(out, final)
            out = final
        except OSError:
            shutil.copy2(str(out), str(final))
            out.unlink(missing_ok=True)
            out = final
    try:
        size = out.stat().st_size
    except OSError:
        size = 0
    return out, files, size


# ---------------------------------------------------------------------- import
# Data files + manifest; the .thumb_cache/ and .covers/ folders are accepted
# with their own prefixes (their members are checked by _safe_member and the
# prefix filter)
_ALLOWED = set(DATA_ITEMS) | {_MANIFEST_NAME}


def _safe_member(name: str) -> bool:
    """Is this archive member name safe? — flat/relative, no "..", absolute, drive or backslash."""
    if not name or "\\" in name or ":" in name:
        return False
    p = PurePosixPath(name)
    if p.is_absolute() or not name == p.as_posix():
        return False
    if not p.parts:                     # security (public release): "." yields empty parts
        return False
    return all(part not in ("", ".", "..") for part in p.parts)


# --------------------------------------- backup-file resource caps
# The name checks already close zip-slip; these caps close zip bombs, disk
# filling and RAM exhaustion: an untrusted backup cannot spill hundreds of
# gigabytes of "actually uncompressed" data onto disk or bloat resources with
# hundreds of thousands of members. The caps sit several times above the
# largest real backup (full cache + covers) — no healthy backup is rejected.
ZIP_MAX_MEMBERS = 200_000
ZIP_MAX_TOTAL_BYTES = 32 << 30          # 32 GiB total uncompressed
ZIP_MAX_FILE_BYTES = 4 << 30            # 4 GiB for a single member
ZIP_MAX_RATIO = 1500                    # real deflate tops out ~1032:1 — larger means fake
ZIP_MIN_RATIO_BYTES = 1 << 20           # the ratio is checked only for files >= 1 MiB
ZIP_MAX_DEPTH = 12


def _zip_resource_violation(zf) -> str | None:
    """The first resource-cap violation (short label) or None — from the
    central directory only.

    No bytes are extracted; the sizes are the header claims and exactly what
    extract would have written to disk — so measuring here, before any write,
    is sufficient and cheap."""
    try:
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        return "unreadable"
    if len(infos) > ZIP_MAX_MEMBERS:
        return "too-many-members"
    total = 0
    seen: set[str] = set()
    for info in infos:
        n = info.filename
        if n in seen:
            return "duplicate-member"
        seen.add(n)
        try:
            if int(info.flag_bits) & 0x1:
                return "encrypted"
        except (AttributeError, TypeError):
            pass
        usize = max(0, int(info.file_size))
        csize = max(0, int(info.compress_size))
        if usize > ZIP_MAX_FILE_BYTES:
            return "member-too-large"
        total += usize
        if total > ZIP_MAX_TOTAL_BYTES:
            return "total-too-large"
        if usize >= ZIP_MIN_RATIO_BYTES and csize > 0 \
                and usize // csize > ZIP_MAX_RATIO:
            return "compression-ratio"
        if len(PurePosixPath(n).parts) > ZIP_MAX_DEPTH:
            return "path-too-deep"
    return None


def _zip_members_ok(names: list) -> bool:
    return all(_safe_member(n) for n in names)


def validate_backup(path) -> tuple[bool, str]:
    """Is path a restorable backup? → (accepted?, short Persian message).

    Both the .mcbak file and the legacy backup folder are
    accepted. Deep database validation happens in stage_import."""
    try:
        path = Path(path)
        if path.is_dir():
            if not (path / "config.json").is_file() \
                    and not (path / "data.db").is_file():
                return False, (T("این پوشه شبیه پشتیبانِ برنامه نیست — "
                               "نه config.json نه data.db در آن هست."))
            return True, ""
        if not path.is_file():
            return False, T("فایلِ انتخاب‌شده وجود ندارد.")
        if not zipfile.is_zipfile(path):
            return False, (T("این فایل شبیه پشتیبانِ برنامه نیست — فایل‌های {0} آرشیوِ استاندارد ZIP‌اند.").format(BACKUP_EXT))
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            # resource caps — a zip bomb is rejected before any extraction
            viol = _zip_resource_violation(zf)
        if viol is not None:
            return False, (T("این فایلِ پشتیبان از سقفِ مجازِ منابع بیرون می‌زند و پذیرفته نشد "
                            "(برچسب: {0}). اگر این پشتیبانِ واقعیِ خودتان است، با نسخه‌ی جدید "
                            "دوباره خروجی بگیرید.").format(viol))
        if not _zip_members_ok(names):
            return False, T("ساختارِ این فایلِ پشتیبان امن/معتبر نیست.")
        if "config.json" not in names and "data.db" not in names:
            return False, (T("این فایل پشتیبانِ برنامه نیست — "
                           "نه config.json نه data.db در آن هست."))
        return True, ""
    except (zipfile.BadZipFile, OSError):
        return False, T("خواندنِ فایلِ پشتیبان ممکن نشد.")


def _check_pending_dbs(pending: Path) -> bool:
    """Validate the extracted databases — never land on corrupt data."""
    for rel in ("data.db", CACHE_DIR_NAME + "/durations.db"):
        p = pending / rel
        try:
            if p.is_file() and not _db_ok(p):
                return False
        except OSError:
            return False
    return True


def stage_import(path, progress=None, extra: dict | None = None) -> Path:
    """Extract/copy the backup into the app-side staging area + write the flag →
    flag path.

    1) progress(done_bytes, total_bytes, files_done, files_total) for
    the progress bar; 2) before the flag is written, the
    extracted databases are quick_check-ed — if corrupt, staging is wiped and
    an error is raised, and no application file changes at all (the malformed
    scenario fix).
    extra (if given) is stored inside the flag JSON — e.g. the name of
    the automatic safety snapshot taken before the replacement."""
    import json as _json
    ok, msg = validate_backup(path)
    if not ok:
        raise ValueError(msg)
    path = Path(path)
    app = app_dir()
    pending = app / _IMPORT_PENDING_NAME
    if pending.exists():
        shutil.rmtree(str(pending), ignore_errors=True)
    pending.mkdir(parents=True)
    files = 0
    include_cache = False
    _ftotal, total_bytes = estimate_backup_size(path)
    done = 0

    def _tick(b: int):
        nonlocal done
        done += abs(int(b or 0))
        if progress is not None:
            try:
                progress(min(done, max(total_bytes, 1)), max(total_bytes, 1),
                         files, max(_ftotal, 1))
            except Exception as _exc_dbg:
                dbg_log(f"stage_import._tick failed: {type(_exc_dbg).__name__}")
                pass

    try:
        if path.is_dir():
            # compatibility: the legacy backup folder
            for name in DATA_ITEMS:
                src = path / name
                try:
                    if src.is_file():
                        shutil.copy2(str(src), str(pending / name))
                        files += 1
                        _tick(src.stat().st_size)
                except OSError:
                    continue
            include_cache = (path / CACHE_DIR_NAME).is_dir()
            if include_cache:
                shutil.copytree(str(path / CACHE_DIR_NAME),
                                str(pending / CACHE_DIR_NAME))
                for p in (pending / CACHE_DIR_NAME).rglob("*"):
                    if p.is_file():
                        try:
                            _tick(p.stat().st_size)
                        except OSError:
                            pass
            src_covers = path / COVERS_DIR_NAME
            if src_covers.is_dir():
                shutil.copytree(str(src_covers), str(pending / COVERS_DIR_NAME))
                for p in (pending / COVERS_DIR_NAME).rglob("*"):
                    if p.is_file():
                        files += 1
                        try:
                            _tick(p.stat().st_size)
                        except OSError:
                            pass
        else:
            cache_prefix = CACHE_DIR_NAME + "/"
            covers_prefix = COVERS_DIR_NAME + "/"
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name not in DATA_ITEMS \
                            and not name.startswith(cache_prefix) \
                            and not name.startswith(covers_prefix):
                        continue
                    if name.startswith(cache_prefix):
                        include_cache = True
                    zf.extract(name, pending)      # names are known safe from validate
                    if name != _MANIFEST_NAME:
                        files += 1                 # covers are real user data too
                    dst = pending / Path(name)
                    try:
                        _tick(dst.stat().st_size)
                    except OSError:
                        _tick(0)
        # Database validation *before* the flag/relaunch — a corrupt database
        # never reaches the apply stage.
        if not _check_pending_dbs(pending):
            shutil.rmtree(str(pending), ignore_errors=True)
            raise ValueError(
                T("دیتابیسِ داخل این فایلِ پشتیبان سالم نیست (آزمونِ quick_check "
                "رد شد). هیچ تغییری روی اطلاعاتِ فعلی اعمال نشد.\n"
                "اگر ممکن است با نسخه‌ی جدید دوباره خروجی بگیر؛ خروجیِ جدید "
                "دیتابیس را به‌صورت اسنپ‌شاتِ سازگار ذخیره می‌کند."))
    except Exception as _exc_dbg:
        dbg_log(f"stage_import failed: {type(_exc_dbg).__name__}")
        shutil.rmtree(str(pending), ignore_errors=True)
        raise
    # final latch — the bar reaches exactly 100% (the byte estimate is approximate)
    if progress is not None:
        try:
            progress(max(total_bytes, 1), max(total_bytes, 1),
                     files, max(_ftotal, 1))
        except Exception as _exc_dbg:
            dbg_log(f"stage_import failed: {type(_exc_dbg).__name__}")
            pass
    flag = app / _IMPORT_FLAG_NAME
    tmp = flag.with_name(_IMPORT_FLAG_NAME + ".tmp")
    _flag_doc = {"pending": str(pending), "files": files,
                 "include_cache": bool(include_cache)}
    if isinstance(extra, dict):
        _flag_doc.update(extra)                     # extra flag payload
    tmp.write_text(_json.dumps(_flag_doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, flag)
    return flag


def import_pending_flag_path() -> Path:
    return app_dir() / _IMPORT_FLAG_NAME


def _looks_unc_path(p) -> bool:
    """a stricter UNC test for imported data.

    The old `startswith("\\\\") or startswith("//")` was bypassed by a leading
    space, a mixed "\\/..." slash pair and "%EVIL_UNC%\\movies" (expanded
    later by expandvars). Strip, normalize the slashes and expand variables
    before the check so all three shapes are caught."""
    s = str(p or "").strip()
    if not s:
        return False
    try:
        s = os.path.expandvars(s)
    except Exception:
        pass
    if "%" in s:
        # %VAR% is Windows syntax — POSIX expandvars leaves it alone, but an
        # attacker-crafted backup relies on exactly that variable. Expand it
        # manually so the check is platform-independent.
        import re as _re49
        s = _re49.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%",
                      lambda m: os.environ.get(m.group(1), m.group(0)), s)
    s = s.strip().replace("/", "\\")
    return s.startswith("\\\\")


def _sanitize_imported_config(cfg_path: Path) -> bool:
    """Security: the "category opener programs" key (cat_openers)
    holds executable paths that are run via Popen when a document is opened;
    so a backup file received from someone else could plant its own opener
    and turn opening a document into running an arbitrary program.

    On every backup import this key is removed from config.json — re-defining
    openers is a two-minute settings job and closing this security hole
    matters more. The "merge" path is unaffected because it only reads
    track_profiles.

    Security (public release — fail-closed): if the file cannot be safely
    read/rewritten, False is returned and the caller must *not apply* the
    imported config.json (the user's current settings stay) and must notify
    the user. The previous silence (except: pass) meant an unparsable config
    was imported without sanitization — that half-open door is now closed.
    This never blocks restoring the rest of the files.

    no more function-local `from watchtime import dbg_log` — a
    local import makes the name function-local for the WHOLE body, so an
    except branch that referenced it before the import line ran would raise
    UnboundLocalError. dbg_log is already a module-level import.
    """
    import json as _json_s
    try:
        cfg = _json_s.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        dbg_log("backup: imported config.json is not readable/valid JSON — "
                "apply skipped, current config kept")
        return False
    if isinstance(cfg, dict):
        changed = False
        if "cat_openers" in cfg:
            cfg.pop("cat_openers")
            changed = True
        # security: auto_backup.path is where the app
        # AUTOMATICALLY writes backups. An untrusted .mcbak can point it at
        # an attacker-chosen location (even a UNC network share) - importing
        # it would stream the user's data there. Drop the whole key, same
        # rationale as player above; re-enabling it is a 10-second setting.
        if "auto_backup" in cfg:
            cfg.pop("auto_backup")
            changed = True
            _SKIPPED_IMPORTS.append("auto_backup")
        # security: UNC/network roots are dropped too -
        # scanning them on Windows would send NTLM credentials to the share
        # chosen by whoever crafted the backup. the check is
        # stricter (_looks_unc_path — strips, normalizes slashes and expands
        # variables first).
        roots = cfg.get("roots")
        if isinstance(roots, list):
            clean = []
            dropped = 0
            for r in roots:
                p = str((r or {}).get("path") or "") if isinstance(r, dict) else str(r or "")
                if _looks_unc_path(p):
                    dropped += 1
                    continue
                clean.append(r)
            if dropped:
                cfg["roots"] = clean
                changed = True
                _SKIPPED_IMPORTS.append("roots(network)")
        # Security : category_colors land directly inside QSS
        # style strings (badge_style/badge_combo_style) — a hand-crafted
        # value like "red; } QWidget { background: blue" injects stylesheet
        # rules, and anything that is not #RRGGBB renders garbage. Only
        # well-formed hex colors are imported.
        colors = cfg.get("category_colors")
        if isinstance(colors, dict):
            from store import _HEX6_RE as _hex_re
            bad = [k for k, v in colors.items()
                   if not isinstance(v, str) or not _hex_re.match(v.strip().lower())]
            if bad:
                for k in bad:
                    colors.pop(k, None)
                changed = True
                _SKIPPED_IMPORTS.append("category_colors")
        # Security: player.path is an executable too — resolve_player accepts
        # the custom path and media playback runs it via subprocess; a restore
        # from an untrusted backup must not launch the backup creator's
        # executable on the first play click. Any player with a path → "auto".
        try:
            pl = cfg.get("player")
            if isinstance(pl, dict) and str(pl.get("path") or "").strip():
                cfg["player"] = {"mode": "auto", "path": ""}
                changed = True
                _SKIPPED_IMPORTS.append("player")   # user is notified after apply
        except Exception as _exc_dbg:
            dbg_log(f"_sanitize_imported_config player failed: {type(_exc_dbg).__name__}")
            return False
        if changed:
            try:
                cfg_path.write_text(
                    _json_s.dumps(cfg, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except OSError:
                dbg_log("backup: imported config.json could not be rewritten "
                        "after removing executable settings — apply skipped")
                return False
    return True


_SKIPPED_IMPORTS: list[str] = []   # items deliberately not applied (for the user-facing message)


def skipped_imports() -> list[str]:
    """Names of items the last apply_pending_import deliberately did not apply.

    Security (public release — fail-closed): config.json (safe sanitization
    failed) and "player" (the executable path is auto-reset). main.py shows
    this list to the user after building the QApplication."""
    return list(_SKIPPED_IMPORTS)


_MAX_APPLY_ATTEMPTS = 2          # after two failed boots → quarantine


def _rewrite_import_flag(flag: Path, doc: dict) -> None:
    """Crash-safe flag rewrite — each step's progress becomes durable immediately."""
    import json as _json_f
    try:
        tmp = flag.with_name(flag.name + ".tmp")
        tmp.write_text(_json_f.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, flag)
    except OSError as _exc_dbg:
        dbg_log(f"_rewrite_import_flag failed: {type(_exc_dbg).__name__}")


def _quarantine_pending_import(app: Path, pending: Path, flag: Path,
                               note: str) -> None:
    """The unfinished staging is set aside, the flag is closed and the user is
    notified.

    Previously a partial failure kept the flag alive forever and every later
    boot rewrote the earlier successful parts over fresher data. Now, once
    the attempt cap is reached, staging moves to .mc_import_bad-<timestamp>,
    the flag is removed and the partial restore is announced explicitly —
    nothing repeats infinitely."""
    import datetime as _dt
    bad = app / (".mc_import_bad-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.move(str(pending), str(bad))
    except OSError:
        shutil.rmtree(str(pending), ignore_errors=True)
    try:
        flag.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        _SKIPPED_IMPORTS.append(note)
    except Exception as _exc_dbg:
        dbg_log(f"_quarantine_pending_import failed: {type(_exc_dbg).__name__}")


def apply_pending_import() -> list[str]:
    """If the import flag exists, overwrite the staged files onto their real
    locations.

    Call only at the very start of boot — before any database is opened.
    Returns the list of overwritten item names (for logging — no paths). On
    success the flag and staging are removed.
    1) the staged databases are
    quick_check-ed again — if corrupt, staging moves aside to
    `.mc_import_bad-<timestamp>` and the flag is removed instead of
    corrupting the user's data; 2) before data.db is overwritten, every old
    destination -wal/-shm/-journal sidecar that is not in the backup is
    removed — the inconsistent "new db + old WAL" combination is no longer
    possible.
    Stepwise/idempotent apply: each successful step is recorded immediately
    in the flag's `applied` list and the next boot reruns only the
    *not-yet-done* steps — previously, if data.db was replaced but, say, the
    locked cache failed to move, the next boot would write the same old
    backup over the user's newer data (data loss). Attempt cap: after two
    failed boots, staging is quarantined and the partial restore is announced
    explicitly."""
    import json as _json
    _SKIPPED_IMPORTS.clear()
    flag = import_pending_flag_path()
    try:
        if not flag.exists():
            return []
        data = _json.loads(flag.read_text(encoding="utf-8"))
        pending = Path(str(data.get("pending") or ""))
    except Exception as _exc_dbg:
        dbg_log(f"apply_pending_import failed: {type(_exc_dbg).__name__}")
        return []
    # Guard: staging must be the app-side folder with the exact expected name
    app = app_dir()
    try:
        inside = pending.parent.resolve() == app.resolve() \
            and pending.name == _IMPORT_PENDING_NAME
    except OSError:
        inside = False
    if not inside or not pending.is_dir():
        try:
            flag.unlink(missing_ok=True)
        except OSError:
            pass
        return []
    applied: list[str] = [str(v) for v in (data.get("applied") or [])
                          if isinstance(v, str)]
    try:
        attempts = int(data.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    # Staged database corrupt? → quarantine and drop the flag (we never
    # overwrite the user's healthy data with a corrupt file)
    try:
        if not _check_pending_dbs(pending):
            import datetime as _dt
            bad = app / (".mc_import_bad-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
            try:
                shutil.move(str(pending), str(bad))
            except OSError:
                shutil.rmtree(str(pending), ignore_errors=True)
            try:
                flag.unlink(missing_ok=True)
            except OSError:
                pass
            return []
    except Exception as _exc_dbg:
        dbg_log(f"apply_pending_import failed: {type(_exc_dbg).__name__}")
        return []
    ok = True

    def _mark(name: str):
        if name not in applied:
            applied.append(name)
        data["applied"] = applied
        data["attempts"] = attempts + 1
        _rewrite_import_flag(flag, data)

    # Orphan-sidecar cleanup *before* overwriting — the root cause of the
    # reported "database disk image is malformed" error (new db + old WAL)
    if (pending / "data.db").is_file() and "data.db" not in applied:
        for sfx in _SIDECAR_SUFFIXES:
            side = "data.db" + sfx
            try:
                if not (pending / side).is_file():
                    (app / side).unlink(missing_ok=True)      # pathlib≥3.8
            except OSError:
                ok = False
    for name in _data_items(pending):
        src = pending / name
        if not src.is_file():
            continue
        if name in applied:
            # This step was already recorded as done in a previous boot — even
            # if the source file is still in staging (the copy fallback path),
            # it is not written over the user's newer data again.
            continue
        try:
            # Security: before config.json is overwritten, sensitive
            # executable keys (cat_openers/player.path) are removed from the
            # imported copy.
            # Security (public release — fail-closed): if safe sanitization
            # fails, the imported config.json is *not* applied — the user's
            # current settings stay untouched; the remaining files restore
            # normally.
            if name == "config.json" and not _sanitize_imported_config(src):
                ok = False
                _SKIPPED_IMPORTS.append(name)
                continue
            paths_replace(src, app / name)
            _mark(name)
        except OSError:
            ok = False
    src_cache = pending / CACHE_DIR_NAME
    if src_cache.is_dir() and CACHE_DIR_NAME not in applied:
        try:
            dst_cache = _cache_root()   # destination = the effective cache path
            if dst_cache.exists():
                shutil.rmtree(str(dst_cache))
            shutil.move(str(src_cache), str(dst_cache))
            _mark(CACHE_DIR_NAME)
        except OSError:
            ok = False
    if ok:
        shutil.rmtree(str(pending), ignore_errors=True)
        try:
            flag.unlink(missing_ok=True)
        except OSError:
            pass
    elif attempts + 1 >= _MAX_APPLY_ATTEMPTS:
        # Two consecutive boots failed — no infinite retries:
        # whatever remains is quarantined and the partial restore is announced
        _quarantine_pending_import(
            app, pending, flag,
            T("بازیابیِ پشتیبان ناقص ماند (فایل‌های قفل‌شده) — محتوای ناتمام "
              "کنار گذاشته شد؛ دیتابیس/تنظیماتِ بازنویسی‌شده سالم‌اند. "
              "دوباره تلاش کنید."))
    else:
        data["attempts"] = attempts + 1
        _rewrite_import_flag(flag, data)
    return applied


def paths_replace(src: Path, dst: Path) -> None:
    """Move onto dst (overwrite) — same drive: os.replace, otherwise copy+delete.
    Raises on error.

    If copy2 successfully overwrites the destination but src cannot be deleted
    (a file locked in staging), that is a *success*, not a failure — otherwise
    the step would not be recorded in the flag and the next boot would write
    the same old file over the fresh destination (exactly the data-loss
    scenario)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
        return
    except OSError as _exc_dbg:
        dbg_log(f"paths_replace fallback to copy: {type(_exc_dbg).__name__}")
    shutil.copy2(str(src), str(dst))
    try:
        src.unlink()
    except OSError as e:
        dbg_log(f"paths_replace src unlink failed (applied anyway): {type(e).__name__}")


# -------------------------------------------------------------------
# Contract: "keep the current files after importing a backup".
# Two tools: 1) an automatic safety snapshot before a full replacement,
# 2) a "merge" mode that wipes none of the current data.

def auto_snapshot_path() -> Path:
    """Unique path for the automatic pre-replacement snapshot — under backups/
    next to the app.

    The snapshot is light (no cache) and can be restored through the same
    "import data" flow, so full replacement is no longer ruthless: if you
    regret it, this file brings everything back."""
    import datetime as _dt
    d = app_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    base = "auto-before-restore-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = d / (base + BACKUP_EXT)
    n = 2
    while p.exists():                        # two replacements within one second → counter
        p = d / ("{0}-{1}{2}".format(base, n, BACKUP_EXT))
        n += 1
    return p


def _extract_backup_core(path: Path, tmp: Path, _tick) -> None:
    """Pulls out only the data core (config.json + data.db + .covers/ covers)."""
    if path.is_dir():                        # compatibility: the legacy backup folder
        for name in ("config.json", "data.db"):
            src = path / name
            try:
                if src.is_file():
                    shutil.copy2(str(src), str(tmp / name))
                    _tick(src.stat().st_size)
            except OSError:
                continue
        src_covers = path / COVERS_DIR_NAME
        if src_covers.is_dir():
            try:
                shutil.copytree(str(src_covers), str(tmp / COVERS_DIR_NAME))
            except OSError:
                pass
        return
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name not in ("config.json", "data.db") \
                    and not name.startswith(COVERS_DIR_NAME + "/"):
                continue                    # the cache is not touched by merge (stays as is)
            if not _safe_member(name):      # names are safe from validate — re-checked anyway
                continue
            zf.extract(name, tmp)
            try:
                _tick((tmp / Path(name)).stat().st_size)
            except OSError:
                _tick(0)


def merge_backup(path, store, progress=None, remap_roots=None) -> dict:
    """Merge a backup file's contents into the current data.

    Unlike stage/apply (full replacement at boot):
      • new database items are ADDED; existing items keep *the current version*
      • categories/colors/track profiles are added only when absent from the
        current data (categories only if they belong to merged items —
        noise from a stranger's backup never reaches the UI)
      • the thumbnail cache is left completely untouched — thumbnails of new
        items are built on demand at the next display/prebuild
      • everything happens on the live, in-memory database

    Rule ("items whose paths don't exist must not be added"):
      • items missing on *this* system are not added at all ("auto-skip");
      • before skipping, "path remapping": for each path group (drive + orbit
        folder), if 100% of the sampled items resolve under one of the current
        roots (remap_roots), that prefix is automatically replaced with the
        local root and the items merge with corrected paths — the "I moved
        folders to another drive/path" scenario.

    Returns: {'items_total', 'items_added', 'items_existing', 'cats_added',
              'profiles_added', 'paths_found', 'paths_missing',
              'skipped_missing',
              'remapped_pairs' (list of (old prefix, new root) — unique)}"""
    import json as _json
    import tempfile as _tmp
    ok, msg = validate_backup(path)
    if not ok:
        raise ValueError(msg)
    path = Path(path)
    _ft, total = estimate_backup_size(path)
    total = max(int(total), 1)
    done = 0

    def _tick(b: int = 0):
        nonlocal done
        done += abs(int(b or 0))
        if progress is not None:
            try:
                progress(min(done, total), total, 0, max(_ft, 1))
            except Exception as _exc_dbg:
                dbg_log(f"merge_backup._tick failed: {type(_exc_dbg).__name__}")
                pass

    staged_cfg: dict = {}
    staged_items: list = []                  # (path, meta_str) rows from the backup database
    with _tmp.TemporaryDirectory(prefix="mcmerge-") as tdir:
        tmp = Path(tdir)
        _extract_backup_core(path, tmp, _tick)
        # The backup's custom covers — copied only where not already present;
        # names are content hashes, so "present" means the same file
        # (nothing is overwritten)
        staged_covers = tmp / COVERS_DIR_NAME
        if staged_covers.is_dir():
            try:
                dst_covers = app_dir() / COVERS_DIR_NAME
                dst_covers.mkdir(parents=True, exist_ok=True)
                for p in sorted(staged_covers.iterdir()):
                    if not p.is_file():
                        continue
                    target = dst_covers / p.name
                    if target.exists():
                        continue
                    try:
                        shutil.copy2(str(p), str(target))
                    except OSError as _exc_dbg:
                        dbg_log(f"merge_backup covers failed: {type(_exc_dbg).__name__}")
            except OSError as _exc_dbg:
                dbg_log(f"merge_backup covers failed: {type(_exc_dbg).__name__}")
        cfg_p = tmp / "config.json"
        try:
            if cfg_p.is_file():
                _raw = _json.loads(cfg_p.read_text(encoding="utf-8"))
                if isinstance(_raw, dict):
                    staged_cfg = _raw
        except Exception as _exc_dbg:
            dbg_log(f"merge_backup failed: {type(_exc_dbg).__name__}")
            staged_cfg = {}
        db_p = tmp / "data.db"
        if db_p.is_file():
            if not _db_ok(db_p):             # never graft corrupt data onto healthy data
                raise ValueError(
                    T("دیتابیسِ داخل این فایلِ پشتیبان سالم نیست (آزمونِ quick_check "
                      "رد شد). هیچ تغییری روی اطلاعاتِ فعلی اعمال نشد."))
            con = None
            try:
                con = sqlite3.connect(str(db_p), timeout=8.0)
                cur = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
                if cur.fetchone():
                    staged_items = con.execute(
                        "SELECT path, meta FROM items").fetchall()
            finally:
                try:
                    if con is not None:
                        con.close()
                except Exception as _exc_dbg:
                    dbg_log(f"merge_backup failed: {type(_exc_dbg).__name__}")
                    pass

    def _norm(p: str) -> str:
        return str(p).replace("\\", "/").rstrip("/")

    # ---- Step 1a: separate new items from duplicates (locked; cross-thread connection)
    # Every row key is canonicalized through the Store's canonical form
    # (_meta_key) before any comparison/insert — a backup carrying "C:/x/B"
    # cannot create a duplicate/invisible row on another Windows machine, and
    # its meta is seen as watch/meta of the same title.
    from store import _meta_key as _canon_key
    existing = 0
    candidates: list = []                      # (path, meta_str, meta_obj) new items
    with store._lock:
        cur_items = store.data.setdefault("items", {})
        for p, m in staged_items:
            p = _canon_key(p)
            if p in cur_items:               # duplicate → the current version always wins
                existing += 1
                continue
            try:
                meta_obj = _json.loads(m)
                if not isinstance(meta_obj, dict):
                    continue
            except ValueError:
                continue
            candidates.append((p, m, meta_obj))

    # ---- Step 1b: auto-skip of path-less items + path remapping
    present: list = []
    missing: list = []
    skipped_missing = 0
    remapped_pairs: list = []
    remapped_items = 0
    # UNC/network item paths from a foreign backup are never probed
    # (os.path.exists on \\server\share would send NTLM credentials to a
    # machine chosen by the backup creator) — they count as missing, but are
    # excluded from remap grouping so nothing ever touches the share.
    unc_skipped = 0
    for rec in candidates:
        if _looks_unc_path(rec[0]):
            unc_skipped += 1
            continue
        (present if os.path.exists(rec[0]) else missing).append(rec)
    skipped_missing += unc_skipped
    if missing and remap_roots:
        from collections import defaultdict as _dd
        groups: dict = _dd(list)
        for rec in missing:
            pn = _norm(rec[0])
            parts = [q for q in pn.split("/") if q]
            # POSIX remap fix: the old key took the first two
            # segments and sliced `_norm(p)[len(key):]` — but for a POSIX
            # absolute path (_norm keeps the leading "/") the key lacked the
            # leading slash and the slice landed mid-name ("e/Videos/X"), so
            # every POSIX remap missed and everything became
            # skipped_missing. The key now carries the leading slash for
            # POSIX paths and the slice is always taken from the same
            # normalized string the key came from.
            if len(parts) > 2:
                key = ("/" if pn.startswith("/") else "") + "/".join(parts[:2])
            else:
                key = pn
            groups[key].append((rec, pn))
        roots_n = [_norm(str(r)) for r in remap_roots if r]
        for g, rows_g in groups.items():
            if not g:
                skipped_missing += len(rows_g)
                continue
            mapped = None
            for rnorm in roots_n:
                if not os.path.isdir(rnorm):
                    continue
                sample = rows_g[: min(25, len(rows_g))]
                hits = 0
                for _rec, pn in sample:
                    rel = pn[len(g):].lstrip("/")
                    if rel and os.path.exists(rnorm + "/" + rel):
                        hits += 1
                if sample and hits == len(sample):   # 100% of the sample → a confident remap
                    mapped = rnorm
                    break
            if mapped is None:
                skipped_missing += len(rows_g)
                continue
            for rec, pn in rows_g:
                rel = pn[len(g):].lstrip("/")
                m = rec[1]
                obj = rec[2]
                newp = _canon_key(mapped + "/" + rel)   # canonical key
                if os.path.exists(newp):
                    present.append((newp, m, obj))
                    remapped_items += 1
                    pair = (g, mapped)
                    if pair not in remapped_pairs:
                        remapped_pairs.append(pair)
                else:
                    skipped_missing += 1
    else:
        skipped_missing += len(missing)

    # ---- Step 1c: final insert into the live database + memory
    added = 0
    new_paths: list = []
    new_metas: dict = {}
    with store._lock:
        cur_items = store.data.setdefault("items", {})
        rows = []
        for p, m, meta_obj in present:
            if p in cur_items:               # may become a duplicate after remapping
                existing += 1
                continue
            cur_items[p] = meta_obj
            rows.append((p, m))
            new_paths.append(p)
            new_metas[p] = meta_obj
            added += 1
        if rows:
            # INSERT OR REPLACE : a memory/DB desync row (the key
            # exists on disk but not in the memory dict — crash mid-save)
            # raised IntegrityError here and left half the merge applied in
            # memory with no commit. Idempotent insert + commit-on-success
            # keeps memory and disk consistent.
            store._con.executemany(
                "INSERT OR REPLACE INTO items (path, meta) VALUES (?, ?)", rows)
            store._con.commit()

    # ---- Step 2: categories + colors (the backup's tree structure is kept in order)
    cats_added = 0
    cfg = store.config
    cur_cats = cfg.setdefault("categories", [])
    cur_colors = cfg.setdefault("category_colors", {})
    staged_colors = staged_cfg.get("category_colors")
    if not isinstance(staged_colors, dict):
        staged_colors = {}

    def _add_cat(name: str):
        nonlocal cats_added
        parts = [x.strip() for x in str(name).split("/") if x.strip()]
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in cur_cats:
                continue
            cur_cats.append(sub)
            cats_added += 1
            if sub in staged_colors:         # the backup's own color for the new category
                cur_colors[sub] = str(staged_colors[sub])

    # A category is added only if it belongs to items actually merged (not the
    # whole backup list) — categories of skipped items create no noise.
    _used48 = set()
    for _o in new_metas.values():
        _parts = [x.strip() for x in str(_o.get("category") or "").split("/") if x.strip()]
        for _i in range(1, len(_parts) + 1):
            _used48.add("/".join(_parts[:_i]))
    _sc = staged_cfg.get("categories")
    for c in (_sc if isinstance(_sc, list) else []):
        if str(c).strip() in _used48:
            _add_cat(c)
    for p in new_paths:                      # also register categories that appear only in meta
        meta = new_metas.get(p) or {}
        _add_cat(meta.get("category") or "")
    # New categories the backup had no color for → palette (stable reservation)
    for sub in cur_cats:
        if sub not in cur_colors:
            store.color(sub)

    # ---- Step 3: custom profiles + category profile assignment — only empty slots
    profiles_added = 0
    _sp = staged_cfg.get("track_profiles")
    if isinstance(_sp, dict):
        profs = cfg.setdefault("track_profiles", {})
        for pid, prof in _sp.items():
            if pid not in profs and isinstance(prof, dict):
                profs[pid] = prof
                profiles_added += 1
    _ctp = staged_cfg.get("cat_track_profile")
    if isinstance(_ctp, dict):
        cur_ctp = cfg.setdefault("cat_track_profile", {})
        for cat, key in _ctp.items():
            if cat not in cur_ctp:
                cur_ctp[cat] = key
    store.save_config()                      # one crash-safe write — not one per category

    # ---- Step 4: report counts — merged items are by definition present;
    # "missing" means the skipped ones (the other-system backup scenario)
    found = len(new_paths)
    missing = skipped_missing
    if progress is not None:                 # final latch — the bar hits exactly 100%
        try:
            progress(total, total, added, max(added, 1))
        except Exception as _exc_dbg:
            dbg_log(f"merge_backup failed: {type(_exc_dbg).__name__}")
            pass
    return {"items_total": added + existing + skipped_missing, "items_added": added,
            "items_existing": existing, "cats_added": cats_added,
            "profiles_added": profiles_added,
            "paths_found": found, "paths_missing": missing,
            "skipped_missing": skipped_missing,          # auto-skipped, path absent
            "remapped_items": remapped_items,            # merged with a remapped path
            "remapped_pairs": remapped_pairs}            # remaps actually applied
