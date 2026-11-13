# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پشتیبان‌گیری/بازیابیِ اطلاعات — نسخه‌ی 0.41.0 (رفع باگ بحرانیِ گزارش‌شده).

0.40.0: خروجی به‌جای پوشه، *یک فایلِ تکی* با پسوندِ اختصاصی ‎.mcbak‎ شد
(استاندارد ZIP فشرده): config.json + data.db + داده‌ها و در صورت انتخاب، کلِ
‎`.thumb_cache/`‎ + مانیفستِ کوچک.

0.41.0 (گزارشِ کاربر: «بعد از واردکردن، برنامه با خطای
sqlite3.DatabaseError: database disk image is malformed باز نشد») — سه
اصلاحِ ساختاری برای امنیتِ SQLite:

۱) **اسنپ‌شاتِ اتمیک در خروجی**: data.db و durations.db هر دو WAL‌اند؛ رونوشتِ
   خامِ فایلِ زنده + سایدکارهای -wal/-shm در لحظه‌های متفاوت ناسازگار می‌شد.
   حالا خروجی با SQLite Backup API یک نسخه‌ی *تک‌فایلیِ خودکفا و سازگار*
   می‌گیرد؛ فایل‌های -wal/-shm/-journal دیگر هرگز داخل پشتیبان نمی‌روند.

۲) **هرزداییِ سایدکارِ یتیم در اعمال**: پیش از بازنویسیِ data.db، هر
   سایدکارِ قدیمیِ مقصد که در پشتیبان نیست حذف می‌شود — ترکیبِ «db جدید +
   WAL قدیمی» (ریشه‌ی اصلیِ malformed) ناممکن می‌شود.

۳) **راستی‌آزماییِ دیتابیس**: quick_check هم هنگام آماده‌سازی (پیش از ریلانچ)
   هم هنگام اعمال در بوت اجرا می‌شود؛ پشتیبانِ خراب هرگز روی دیتای سالم
   نمی‌نشیند. در import، پیشرفتِ بایتی به رابط گزارش می‌شود (پروگرس‌بار+ETA
   در تنظیمات — درخواستِ کاربر).

بازیابی همان الگوی امنِ پرچم است (0.37.0→): استخراج در ‎`.mc_import_pending`‎ +
پرچمِ ‎`._mc_import`‎ و بازنویسیِ واقعی در بوتِ بعدی، پیش از بازشدنِ هر
دیتابیسی. سازگاری: پوشه‌ی پشتیبانِ 0.39.0 و فایلِ 0.40.0 هم پذیرفته می‌شوند.
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

# فایل‌های هسته‌ی اطلاعات کنار برنامه (کش به انتخاب کاربر جداست)
DATA_ITEMS = ("config.json", "data.json", "data.db",
              "data.db-shm", "data.db-wal", "data.db-journal")
CACHE_DIR_NAME = ".thumb_cache"

# پسوندِ اختصاصی فایلِ پشتیبان (درخواست کاربر — 0.40.0)
BACKUP_EXT = ".mcbak"

# مانیفستِ داخل فایل — برای اعتبارسنجیِ سریع و شفافیتِ کاربر
_MANIFEST_NAME = "backup-manifest.json"

_IMPORT_FLAG_NAME = "._mc_import"
_IMPORT_PENDING_NAME = ".mc_import_pending"

# دیتابیس‌هایی که با Backup API اسنپ‌شات می‌شوند (هرگز رونوشتِ خامِ زنده نیست)
_SNAPSHOT_DBS = ("data.db", f"{CACHE_DIR_NAME}/durations.db")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _cache_root() -> Path:
    """مسیرِ واقعیِ کش تامبنیل — (round 19 §۲۰) دیگر همیشه ‎app/.thumb_cache‎ نیست.

    با ‎MC_CACHE_DIR‎ کش جای دیگری می‌رود و در fallbackِ فقط‌خواندنی هم مسیر
    مؤثر می‌تواند جدا باشد؛ backup که مسیرِ ثابت می‌خواند/می‌نویسد یا کشِ
    واقعی را جا می‌انداخت یا (بدتر) هنگام اعمال، کشِ سفارشیِ کاربر را پاک
    می‌کرد. حالا export/restore از همان paths.thumb_cache_dir می‌خوانند (قاعده
    در یک جا). نامِ اعضا داخلِ zip همچنان ثابت ‎.thumb_cache/...‎ می‌ماند —
    فرمتِ فایلِ پشتیبان دست نمی‌خورد."""
    try:
        from paths import thumb_cache_dir
        return Path(thumb_cache_dir())
    except Exception as _exc_dbg:
        dbg_log(f"_cache_root fallback: {type(_exc_dbg).__name__}")
        return Path(app_dir()) / CACHE_DIR_NAME


def _data_items(app) -> list[str]:
    """فایل‌های داده‌ی کاربر (config/db + کاورهای سفارشی ‎.covers/‎).

    پشتیبان‌گیریِ کاورها (round 11): فایل‌های کاور در ‎APP_DIR/.covers/‎ زندگی
    می‌کنند و باید مثل دیتابیس سفر کنند — وگرنه بعد از restore همه‌ی کاورهای
    سفارشی به آیکونِ پوشه برمی‌گردند.
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
    """نامِ پیشنهادیِ فایلِ پشتیبان — برای دیالوگِ ذخیره."""
    import datetime as _dt
    return ("FolderDex-backup-"
            + _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + BACKUP_EXT)


# ------------------------------------------------------------------- SQLite
def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """اسنپ‌شاتِ سازگارِ یک SQLiteِ زنده (WAL) به یک فایلِ تکی — Backup API.

    حتی وقتی برنامه دیتابیس را باز دارد، این اتصالِ موازی یک کپیِ از نظرِ
    تراکنش سازگار می‌گیرد (مثل VACUUM INTO ایمن). قفلِ لحظه‌ای: یک بازآزمایی."""
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
    """quick_check روی یک فایل SQLite — True فقط اگر کاملاً سالم باز شود."""
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
    """آیا این عضو یکی از دیتابیس‌های اسنپ‌شاتی است؟ (مسیرِ نسبیِ POSIX درون فایل)"""
    return arc_name in _SNAPSHOT_DBS


def _is_snapshot_sidecar(arc_name: str) -> bool:
    """سایدکارِ -wal/-shm/-journal یکی از دیتابیس‌های اسنپ‌شاتی است؟"""
    return any(arc_name == db + sfx for db in _SNAPSHOT_DBS for sfx in _SIDECAR_SUFFIXES)


# ------------------------------------------------------------------- تخمین حجم
def estimate_export_size(include_cache: bool) -> tuple[int, int]:
    """(تعداد فایل، مجموع بایت) داده‌هایی که خروجی می‌روند — برای متنِ دیالوگ."""
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
        cache = _cache_root()          # (round 19 §۲۰) مسیرِ مؤثرِ کش — نه مسیرِ ثابت
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
    """(تعداد فایل، مجموع بایتِ غیرفشرده) محتوای یک فایل/پوشه‌ی پشتیبان."""
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


# ---------------------------------------------------------------------- خروجی
def export_backup(dest_path, include_cache: bool = True, progress=None,
                  atomic: bool = False) -> tuple:
    """ساختِ یک فایلِ پشتیبانِ ‎.mcbak‎ در مسیرِ dest_path → (فایل، تعداد، بایت).

    0.41.0: data.db و .thumb_cache/durations.db با Backup API اسنپ‌شات می‌شوند —
    سایدکارهای -wal/-shm/-journal هرگز داخل فایل نمی‌روند (رفع malformed).
    progress(done_bytes, total_bytes, files_done, files_total) پس از هر فایل
    صدا زده می‌شود (پروگرس‌بارِ تنظیمات — درخواست کاربر).
    خطاها به فراخواننده پرتاب می‌شوند و فایلِ نیمه‌کاره پاک می‌شود.
    0.48.0: atomic=True برای «پشتیبانِ خودکار» — اول در ‎.part نوشته و در پایانِ
    کامل اتمیک جایگزین می‌شود تا هدفِ بازنویسی‌شونده هرگز نیمه‌کاره نماند."""
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
        "snapshot": True,          # دیتابیس‌ها اسنپ‌شاتِ سازگارند
        # محتوای فایلِ پشتیبان «قالبِ فایل» است و باید بین زبان‌ها/نسخه‌ها
        # ثابت بماند (بازبینی دستی — این رشته ترجمه نمی‌شود)
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
                """یک منبع (فایل واقعی یا اسنپ‌شات) را وارد zip می‌کند."""
                if _is_snapshot_db(arc):
                    snap = tmp / ("snap-" + Path(arc).name)
                    try:
                        _snapshot_sqlite(src, snap)
                    except sqlite3.Error:
                        if arc == "data.db":
                            raise          # داده‌ی کاربر — خروجیِ ناسازگار ممنوع
                        return               # کشِ زمان‌ها: قابل‌بازسازی ← فقط جا می‌افتد
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
                    # سایدکارهای WAL هرگز در خروجی نیستند — اسنپ‌شات کافی است
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
                    cache = _cache_root()      # (round 19 §20) مسیرِ مؤثرِ کش
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
            out.unlink(missing_ok=True)         # فایلِ نیمه‌کاره باقی نماند
        except OSError:
            pass
        raise
    # latchِ نهایی: حجمِ اسنپ‌شات می‌تواند اندکی با منبع فرق کند — نوار دقیقاً ۱۰۰٪ بسته شود
    if progress is not None:
        try:
            progress(max(total_bytes, 1), max(total_bytes, 1),
                     files, max(_files_t, 1))
        except Exception as _exc_dbg:
            dbg_log(f"export_backup failed: {type(_exc_dbg).__name__}")
            pass
    if atomic:                                   # جایگزینیِ اتمیکِ هدفِ نهایی
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


# ---------------------------------------------------------------------- ورود
# فایل‌های دیتا + مانیفست؛ پوشه‌های .thumb_cache/ و .covers/ با پیشوندِ خودشان
# پذیرفته می‌شوند (عناصرشان با _safe_member و فیلترِ پیشوند کنترل می‌شوند)
_ALLOWED = set(DATA_ITEMS) | {_MANIFEST_NAME}


def _safe_member(name: str) -> bool:
    """نامِ عضوِ آرشیو امن است؟ — مسطح/نسبی، بدون «..»، مطلق، درایو یا بک‌اسلش."""
    if not name or "\\" in name or ":" in name:
        return False
    p = PurePosixPath(name)
    if p.is_absolute() or not name == p.as_posix():
        return False
    if not p.parts:                     # امنیت (انتشار عمومی): «.» → parts خالی
        return False
    return all(part not in ("", ".", "..") for part in p.parts)


# ------------------------------------------------ سقفِ منابعِ فایلِ پشتیبان (round 19 §۷)
# نام‌ها از قبل zip-slip را می‌بندند؛ این سقف‌ها zip-bomb/پرکردنِ دیسک/اتلافِ RAM
# را می‌بندند: پشتیبانِ نامطمئن نمی‌تواند چند صد گیگِ «واقعاً غیرفشرده» را روی
# دیسک بریزد یا با صدها هزار عضو منابع را ببلعد. سقف‌ها از بزرگ‌ترینِ پشتیبانِ
# واقعی (کشِ کامل + کاورها) چند برابر بالاترند — هیچ پشتیبانِ سالمی رد نمی‌شود.
ZIP_MAX_MEMBERS = 200_000
ZIP_MAX_TOTAL_BYTES = 32 << 30          # ۳۲ گیگابایت مجموعِ غیرفشرده
ZIP_MAX_FILE_BYTES = 4 << 30            # ۴ گیگابایت برای یک عضو
ZIP_MAX_RATIO = 1500                    # deflate واقعی نهایتاً ~۱۰۳۲:۱ — بزرگ‌تر = جعلی
ZIP_MIN_RATIO_BYTES = 1 << 20           # نسبت فقط برای فایل‌های ≥۱مگ سنجیده شود
ZIP_MAX_DEPTH = 12


def _zip_resource_violation(zf) -> str | None:
    """اولین نقضِ سقفِ منابع (برچسبِ کوتاه) یا None — فقط از central directory.

    هیچ بایتی استخراج نمی‌شود؛ اندازه‌ها همان ادعاهای سرآیند هستند و دقیقاً
    همان چیزی‌اند که هنگام extract روی دیسک نوشته می‌شد — پس سنجش از همین‌جا
    قبل از هر نوشتنی کافی و ارزان است."""
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
    """آیا path یک پشتیبانِ قابل‌بازیابی است؟ → (قبول؟، پیامِ فارسی کوتاه).

    هم فایلِ ‎.mcbak‎ (0.40.0+) هم پوشه‌ی پشتیبانِ قدیمی (0.39.0) پذیرفته می‌شود.
    راستی‌آزماییِ عمیقِ دیتابیس در stage_import انجام می‌شود."""
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
            # سقفِ منابع (round 19 §۷) — بمبِ zip قبل از هر استخراجی رد می‌شود
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
    """راستی‌آزماییِ دیتابیس‌های استخراج‌شده — 0.41.0: هرگز روی دیتای خراب ننشین."""
    for rel in ("data.db", CACHE_DIR_NAME + "/durations.db"):
        p = pending / rel
        try:
            if p.is_file() and not _db_ok(p):
                return False
        except OSError:
            return False
    return True


def stage_import(path, progress=None, extra: dict | None = None) -> Path:
    """استخراج/رونوشتِ پشتیبان به آماده‌سازیِ کنار برنامه + ثبتِ پرچم → مسیرِ پرچم.

    0.41.0: ۱) progress(done_bytes, total_bytes, files_done, files_total) برای
    پروگرس‌بار (درخواست کاربر) ۲) پیش از ثبتِ پرچم، دیتابیس‌های استخراج‌شده
    quick_check می‌شوند — اگر خراب باشند، آماده‌سازی پاک و خطا پرتاب می‌شود و
    هیچ تغییری روی فایل‌های برنامه رخ نمی‌دهد (رفعِ سناریوی malformed).
    0.47.0: extra (در صورت وجود) داخل JSONِ پرچم ذخیره می‌شود — مثلاً نامِ
    اسنپ‌شاتِ امنیتیِ خودکارِ پیش از جایگزینی."""
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
            # سازگاری: پوشه‌ی پشتیبانِ 0.39.0
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
                    zf.extract(name, pending)      # نام‌ها از validate امن‌اند
                    if name != _MANIFEST_NAME:
                        files += 1                 # کاورها هم داده‌ی واقعی کاربرند
                    dst = pending / Path(name)
                    try:
                        _tick(dst.stat().st_size)
                    except OSError:
                        _tick(0)
        # راستی‌آزماییِ دیتابیس *پیش* از پرچم/ریلانچ — دیتابیسِ خراب
        # هرگز به مرحله‌ی اعمال نمی‌رسد.
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
    # latchِ نهایی — نوار دقیقاً به ۱۰۰٪ می‌رسد (تخمینِ بایتی تقریبی است)
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
        _flag_doc.update(extra)                     # داده‌های تکمیلیِ پرچم
    tmp.write_text(_json.dumps(_flag_doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, flag)
    return flag


def import_pending_flag_path() -> Path:
    return app_dir() / _IMPORT_FLAG_NAME


def _sanitize_imported_config(cfg_path: Path) -> bool:
    """امنیت (0.51.0): کلیدِ «برنامه‌های بازکننده‌ی دسته» (‎cat_openers‎) مسیرِ
    برنامه‌ی اجرایی را نگه می‌دارد و موقعِ بازکردنِ سند با Popen اجرا می‌شود؛
    پس یک فایلِ پشتیبانِ دریافت‌شده از دیگران می‌تواند openerِ دلخواهِ خودش را
    بکارد و بازکردنِ یک سند به اجرای برنامه‌ی دلخواهِ سازنده‌اش بینجامد.

    هنگامِ واردکردنِ هر پشتیبانی این کلید از config.json حذف می‌شود — تعریفِ
    دوباره‌ی بازکننده‌ها یک تنظیمِ دو دقیقه‌ای است و بستنِ این درِ امنیتی
    مهم‌تر. حالتِ «ادغام» بی‌تأثیر است چون فقط track_profiles می‌خواند.

    امنیت (انتشار عمومی — fail-closed): اگر خواندن/بازنویسیِ امنِ این فایل
    ممکن نشد، مقدارِ False برمی‌گردد و caller موظف است config.jsonِ واردشده
    را *اعمال نکند* (تنظیماتِ فعلیِ کاربر می‌ماند) و پیام بدهد. سکوتِ قبلی
    (except: pass) یعنی یک configِ تجزیه‌ناپذیر بدونِ پاک‌سازی وارد می‌شد —
    همین درِ نیمه‌باز بسته شد. هیچ‌وقت مانعِ بازیابیِ بقیه‌ی فایل‌ها نمی‌شود."""
    import json as _json_s
    try:
        cfg = _json_s.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        try:
            from watchtime import dbg_log
            dbg_log("backup: imported config.json is not readable/valid JSON — "
                    "apply skipped, current config kept")
        except Exception as _exc_dbg:
            dbg_log(f"_sanitize_imported_config failed: {type(_exc_dbg).__name__}")
            pass
        return False
    if isinstance(cfg, dict):
        changed = False
        if "cat_openers" in cfg:
            cfg.pop("cat_openers")
            changed = True
        # امنیت (round 19 §۱): player.path هم اجرایی است — resolve_player مسیرِ
        # سفارشی را قبول و پخشِ رسانه با subprocess آن را اجرا می‌کند؛ restore
        # از پشتیبانِ نامطمئن نباید فایلِ اجراییِ سازنده‌ی پشتیبان را در اولین
        # کلیکِ پخش اجرا کند. هر player دارای مسیر → «auto».
        try:
            pl = cfg.get("player")
            if isinstance(pl, dict) and str(pl.get("path") or "").strip():
                cfg["player"] = {"mode": "auto", "path": ""}
                changed = True
                _SKIPPED_IMPORTS.append("player")   # اطلاعِ کاربر پس از apply
        except Exception as _exc_dbg:
            dbg_log(f"_sanitize_imported_config player failed: {type(_exc_dbg).__name__}")
            return False
        if changed:
            try:
                cfg_path.write_text(
                    _json_s.dumps(cfg, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except OSError:
                try:
                    from watchtime import dbg_log
                    dbg_log("backup: imported config.json could not be rewritten "
                            "after removing executable settings — apply skipped")
                except Exception as _exc_dbg:
                    dbg_log(f"_sanitize_imported_config failed: {type(_exc_dbg).__name__}")
                    pass
                return False
    return True


_SKIPPED_IMPORTS: list[str] = []   # مواردِ عمداً اعمال‌نشده (برای پیامِ کاربر)


def skipped_imports() -> list[str]:
    """نامِ مواردی که در آخرین apply_pending_import عمداً اعمال نشدند.

    امنیت (انتشار عمومی — fail-closed): config.json (پاک‌سازیِ امن ممکن نشد)
    و «player» (بازنشانیِ خودکارِ مسیرِ اجرایی — round 19 §۱). main.py بعد از
    ساختِ QApplication این فهرست را به کاربر نشان می‌دهد."""
    return list(_SKIPPED_IMPORTS)


_MAX_APPLY_ATTEMPTS = 2          # (round 19 §۶) بعد از دو بوتِ شکست‌خورده → قرنطینه


def _rewrite_import_flag(flag: Path, doc: dict) -> None:
    """بازنویسیِ اتمیکِ پرچم — پیشرفتِ هر مرحله بلافاصله پایدار می‌شود."""
    import json as _json_f
    try:
        tmp = flag.with_name(flag.name + ".tmp")
        tmp.write_text(_json_f.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, flag)
    except OSError as _exc_dbg:
        dbg_log(f"_rewrite_import_flag failed: {type(_exc_dbg).__name__}")


def _quarantine_pending_import(app: Path, pending: Path, flag: Path,
                               note: str) -> None:
    """آماده‌سازیِ ناتمام کنار می‌رود، پرچم بسته می‌شود و کاربر آگاه می‌شود.

    (round 19 §۶) قبلاً شکستِ جزئی پرچم را برای همیشه زنده نگه می‌داشت و هر
    بوتِ بعد بخش‌هایِ موفقِ قبلی را دوباره روی داده‌ی تازه می‌نوشت. حالا بعد از
    سقفِ تلاش، آماده‌سازی به ‎.mc_import_bad-<زمان>‎ منتقل، پرچم برداشته و
    بازیابیِ ناقص صریحاً اعلام می‌شود — هیچ‌چیز بی‌نهایت تکرار نمی‌شود."""
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
    """اگر پرچمِ ورود باشد، فایل‌های آماده‌سازی را روی جای اصلی بازنویسی می‌کند.

    فقط در آغازِ بوت — قبل از بازشدنِ هر دیتابیسی — صدا زده شود. برمی‌گرداند
    فهرستِ نامِ مواردِ بازنویسی‌شده (برای لاگ — بدون مسیر). در موفقیت پرچم و
    آماده‌سازی پاک می‌شوند.
    0.41.0 (رفعِ باگِ malformedِ گزارش‌شده): ۱) دیتابیسِ آماده‌سازی
    دوباره quick_check می‌شود — اگر خراب باشد، به‌جای خراب‌کردنِ دیتای کاربر،
    آماده‌سازی به ‎`.mc_import_bad-<زمان>`‎ کنار می‌رود و پرچم برداشته می‌شود؛
    ۲) پیش از بازنویسیِ data.db، هر سایدکارِ ‎-wal/-shm/-journal‎ قدیمیِ مقصد
    که در پشتیبان نیست حذف می‌شود — ترکیبِ ناسازگارِ «db جدید + WAL قدیمی»
    دیگر ممکن نیست.
    (round 19 §۶) اعمالِ مرحله‌ای/idempotent: هر مرحلهٔ موفق بلافاصله در
    فهرستِ `applied` داخلِ پرچم ثبت می‌شود و بوتِ بعد فقط مراحلِ *انجام‌نشده*
    را دوباره اجرا می‌کند — قبلاً اگر data.db جایگزین می‌شد ولی مثلاً کشِ قفل
    move نمی‌شد، بوتِ بعد همان پشتیبانِ قدیمی را دوباره روی داده‌های جدیدِ
    کاربر می‌نوشت (از‌دست‌رفتنِ داده). سقفِ تلاش: بعد از دو بوتِ ناموفق،
    آماده‌سازی قرنطینه و بازیابیِ ناقص صریحاً اعلام می‌شود."""
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
    # محافظت: آماده‌سازی فقط پوشه‌ی هم‌نامِ کنار برنامه با نامِ دقیق
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
    # دیتابیسِ آماده‌سازی خراب؟ → قرنطینه و پاک‌کردنِ پرچم (هرگز دیتای
    # سالمِ کاربر را با فایلِ خراب بازنویسی نمی‌کنیم)
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

    # هرزداییِ سایدکارهای یتیم *پیش* از بازنویسی — ریشه‌ی اصلیِ خطای
    # database disk image is malformed که کاربر گزارش کرد (db جدید + WAL قدیمی)
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
            # (round 19 §۶) این مرحله در بوتِ قبلی موفق ثبت شده — حتی اگر
            # فایلِ منبع هنوز در آماده‌سازی مانده (مسیرِ کپیِ جایگزین)، دوباره
            # روی داده‌ی تازه‌ترِ کاربر نوشته نمی‌شود.
            continue
        try:
            # امنیت (0.51.0): قبل از بازنویسیِ config.json، کلیدهای اجراییِ
            # حساس (cat_openers/player.path) از نسخه‌ی واردشده حذف می‌شوند.
            # امنیت (انتشار عمومی — fail-closed): اگر پاک‌سازیِ امن ممکن
            # نشد، config.jsonِ واردشده اعمال *نمی‌شود* — تنظیماتِ فعلیِ
            # کاربر دست‌نخورده می‌ماند؛ بقیه‌ی فایل‌ها عادی بازیابی می‌شوند.
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
            dst_cache = _cache_root()   # (round 19 §۲۰) مقصد = مسیرِ مؤثرِ کش
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
        # (round 19 §۶) دو بوت پیاپی شکست خورد — دیگر بی‌نهایت تکرار ممنوع:
        # هرچه مانده قرنطینه می‌شود و بازیابیِ ناقص اعلام می‌شود
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
    """move روی dst (بازنویسی) — همان درایو os.replace، غیره کپی+حذف. خطا پرتاب.

    (round 19 §۶) اگر copy2 مقصد را با موفقیت بازنویسی کرد ولی حذفِ src ممکن
    نشد (فایلِ قفل در آماده‌سازی)، این «موفقیت» است نه شکست — وگرنه مرحله در
    پرچم ثبت نمی‌شد و بوتِ بعد همان فایلِ قدیمی را دوباره روی مقصدِ تازه
    می‌نوشت (دقیقاً سناریویِ از‌دست‌رفتنِ داده)."""
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
# درخواست کاربر: «قابلیت حفظ فایل‌های الان بعد از واردکردن بکاپ».
# دو ابزار: ۱) اسنپ‌شاتِ امنیتیِ خودکار پیش از جایگزینیِ کامل ۲) حالتِ «ادغام»
# که هیچ‌کدام از اطلاعاتِ فعلی را پاک نمی‌کند.

def auto_snapshot_path() -> Path:
    """مسیرِ یکتای اسنپ‌شاتِ خودکارِ پیش از جایگزینی — زیر backups/ کنار برنامه.

    اسنپ‌شات سبک است (بدون کش) و با همان «واردکردن اطلاعات» قابل بازگردانی است،
    پس حالتِ جایگزینی هم دیگر بی‌رحمانه نیست: اگر پشیمان شدی، همین فایل برمی‌گرداند."""
    import datetime as _dt
    d = app_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    base = "auto-before-restore-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = d / (base + BACKUP_EXT)
    n = 2
    while p.exists():                        # دو جایگزینی در یک ثانیه → شمارنده
        p = d / ("{0}-{1}{2}".format(base, n, BACKUP_EXT))
        n += 1
    return p


def _extract_backup_core(path: Path, tmp: Path, _tick) -> None:
    """فقط هسته‌ی داده (config.json + data.db + کاورهای ‎.covers/‎) را بیرون می‌کشد."""
    if path.is_dir():                        # سازگاری: پوشه‌ی پشتیبانِ 0.39.0
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
                continue                    # کش در ادغام اعمال نمی‌شود (دست‌نخورده می‌ماند)
            if not _safe_member(name):      # نام‌ها از validate امن‌اند — اطمینانِ دوباره
                continue
            zf.extract(name, tmp)
            try:
                _tick((tmp / Path(name)).stat().st_size)
            except OSError:
                _tick(0)


def merge_backup(path, store, progress=None, remap_roots=None) -> dict:
    """ادغامِ محتوای یک فایلِ پشتیبان با اطلاعاتِ فعلی (0.47.0/0.48.0 — درخواست کاربر).

    برخلاف stage/apply (جایگزینیِ کامل در بوت):
      • مواردِ تازه‌ی دیتابیس ADD می‌شوند؛ مواردِ موجود *با نسخه‌ی فعلی* می‌مانند
      • دسته‌ها/رنگ‌ها/الگوهای ترک فقط در صورت نبودن در اطلاعاتِ فعلی افزوده می‌شوند
        (0.48.0: دسته‌ها فقط اگر به مواردِ ادغام‌شده تعلق داشته باشند — نویزِ پشتیبانِ
        بیگانه به رابط راه پیدا نمی‌کند)
      • کشِ تامبنیل کاملاً دست‌نخورده می‌ماند — تامبنیلِ مواردِ تازه هنگام
        نمایش/پیش‌ساختِ بعدی خودکار ساخته می‌شود
      • همه‌چیز روی دیتابیسِ زنده و در حافظه انجام می‌شود

    0.48.0 (درخواست کاربر — «مواردی که مسیرشان نیست اضافه نشوند»):
      • مواردِ بدون‌مسیر روی *این* سیستم اصلاً اضافه نمی‌شوند («رد خودکار»)؛
      • پیش از رد، «نقشه‌ی مسیر»: برای هر گروهِ مسیر (درایو+پوشه‌ی مدار) اگر ۱۰۰٪
        نمونه زیر یکی از ریشه‌های فعلی (remap_roots) پیدا شود، همان پیشوند به‌طور
        خودکار با ریشه‌ی محلی جایگزین می‌شود و موارد با مسیرِ اصلاح‌شده ادغام
        می‌شوند — سناریوی «پوشه‌ها را به درایو/مسیر دیگری منتقل کردم».

    خروجی: {'items_total', 'items_added', 'items_existing', 'cats_added',
            'profiles_added', 'paths_found', 'paths_missing', 'skipped_missing',
            'remapped_pairs' (فهرستِ (پیشوندِ قدیم، ریشه‌ی تازه) — یکتا)}"""
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
    staged_items: list = []                  # (path, meta_str) از دیتابیسِ پشتیبان
    with _tmp.TemporaryDirectory(prefix="mcmerge-") as tdir:
        tmp = Path(tdir)
        _extract_backup_core(path, tmp, _tick)
        # کاورهای سفارشیِ پشتیبان — فقط جاهایی که از قبل نیستند کپی می‌شوند؛
        # نام‌ها هشِ محتواست، پس «موجود» یعنی همان فایل (هیچ‌چیز بازنویسی نمی‌شود)
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
            if not _db_ok(db_p):             # هرگز دیتای خراب را به دیتای سالم نچسبان
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

    # ---- مرحله‌ی ۱الف: تفکیک مواردِ تازه از تکراری‌ها (قفل؛ اتصال cross-thread)
    # (round 19 §۱۲) کلیدِ هر ردیف پیش از هر مقایسه/درج به فرمِ کانونیِ Store
    # (_meta_key) می‌رود — پشتیبان با «C:/x/B» روی ویندوز دیگر یک ردیفِ
    # تکراری/نامرئی نمی‌سازد و متای آن با watch/meta همان عنوان دیده می‌شود.
    from store import _meta_key as _canon_key
    existing = 0
    candidates: list = []                      # (path, meta_str, meta_obj) موارد تازه
    with store._lock:
        cur_items = store.data.setdefault("items", {})
        for p, m in staged_items:
            p = _canon_key(p)
            if p in cur_items:               # تکراری → نسخه‌ی فعلی همیشه برنده است
                existing += 1
                continue
            try:
                meta_obj = _json.loads(m)
                if not isinstance(meta_obj, dict):
                    continue
            except ValueError:
                continue
            candidates.append((p, m, meta_obj))

    # ---- مرحله‌ی ۱ب: رد خودکارِ موارد بدون‌مسیر + نقشه‌ی جابه‌جایی
    present: list = []
    missing: list = []
    for rec in candidates:
        (present if os.path.exists(rec[0]) else missing).append(rec)
    remapped_pairs: list = []
    remapped_items = 0
    skipped_missing = 0
    if missing and remap_roots:
        from collections import defaultdict as _dd
        groups: dict = _dd(list)
        for rec in missing:
            parts = [q for q in _norm(rec[0]).split("/") if q]
            key = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if parts else "")
            groups[key].append(rec)
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
                for p, _m, _o in sample:
                    rel = _norm(p)[len(g):].lstrip("/")
                    if rel and os.path.exists(rnorm + "/" + rel):
                        hits += 1
                if sample and hits == len(sample):   # ۱۰۰٪ نمونه → نقشه‌ی مطمئن
                    mapped = rnorm
                    break
            if mapped is None:
                skipped_missing += len(rows_g)
                continue
            for p, m, obj in rows_g:
                rel = _norm(p)[len(g):].lstrip("/")
                newp = _canon_key(mapped + "/" + rel)   # (round 19 §۱۲) کلیدِ کانونی
                if os.path.exists(newp):
                    present.append((newp, m, obj))
                    remapped_items += 1
                    pair = (g, mapped)
                    if pair not in remapped_pairs:
                        remapped_pairs.append(pair)
                else:
                    skipped_missing += 1
    else:
        skipped_missing = len(missing)

    # ---- مرحله‌ی ۱ج: درجِ نهایی در دیتابیسِ زنده + حافظه
    added = 0
    new_paths: list = []
    new_metas: dict = {}
    with store._lock:
        cur_items = store.data.setdefault("items", {})
        rows = []
        for p, m, meta_obj in present:
            if p in cur_items:               # بعد از نقشه ممکن است تکراری شود
                existing += 1
                continue
            cur_items[p] = meta_obj
            rows.append((p, m))
            new_paths.append(p)
            new_metas[p] = meta_obj
            added += 1
        if rows:
            store._con.executemany(
                "INSERT INTO items (path, meta) VALUES (?, ?)", rows)
            store._con.commit()

    # ---- مرحله‌ی ۲: دسته‌ها + رنگ‌ها (ساختار درختیِ پشتیبان با همان ترتیب حفظ می‌شود)
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
            if sub in staged_colors:         # رنگِ خودِ پشتیبان برای دسته‌ی تازه
                cur_colors[sub] = str(staged_colors[sub])

    # دسته فقط اگر به مواردِ واقعاً ادغام‌شده تعلق داشته باشد (نه کلِ فهرستِ
    # پشتیبان) — دسته‌های مواردِ ردشده نویز نمی‌سازند.
    _used48 = set()
    for _o in new_metas.values():
        _parts = [x.strip() for x in str(_o.get("category") or "").split("/") if x.strip()]
        for _i in range(1, len(_parts) + 1):
            _used48.add("/".join(_parts[:_i]))
    _sc = staged_cfg.get("categories")
    for c in (_sc if isinstance(_sc, list) else []):
        if str(c).strip() in _used48:
            _add_cat(c)
    for p in new_paths:                      # دسته‌هایی که فقط در متا آمده‌اند هم ثبت شود
        meta = new_metas.get(p) or {}
        _add_cat(meta.get("category") or "")
    # رنگِ دسته‌های تازه‌ای که پشتیبان رنگی برایشان نداشت → پالت (رزرو پایدار)
    for sub in cur_cats:
        if sub not in cur_colors:
            store.color(sub)

    # ---- مرحله‌ی ۳: الگوهای سفارشی + تخصیص الگو به دسته — فقط جاهای خالی
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
    store.save_config()                      # یک نوشتن اتمیک — نه یکی به ازای هر دسته

    # ---- مرحله‌ی ۴: شمارش‌های گزارش — مواردِ ادغام‌شده تعریفاً موجودند؛
    # «ناموجود» همان ردشده‌هاست (سناریوی پشتیبانِ سیستم دیگر — گزارش کاربر)
    found = len(new_paths)
    missing = skipped_missing
    if progress is not None:                 # latchِ نهایی — نوار دقیقاً ۱۰۰٪
        try:
            progress(total, total, added, max(added, 1))
        except Exception as _exc_dbg:
            dbg_log(f"merge_backup failed: {type(_exc_dbg).__name__}")
            pass
    return {"items_total": added + existing + skipped_missing, "items_added": added,
            "items_existing": existing, "cats_added": cats_added,
            "profiles_added": profiles_added,
            "paths_found": found, "paths_missing": missing,
            "skipped_missing": skipped_missing,          # رد خودکارِ بدون‌مسیر
            "remapped_items": remapped_items,            # با مسیرِ تازه ادغام‌شده
            "remapped_pairs": remapped_pairs}            # نقشه‌های اعمال‌شده
