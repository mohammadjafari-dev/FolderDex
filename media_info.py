# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""مدت‌زمان فایل‌های ویدیویی.

استراتژی سه‌لایه (به ترتیب سرعت):
    ۱) خواندن مستقیم چند کیلوبایت از هدر خودِ فایل (MP4/MKV/WebM/AVI/WMV)
       — کاملاً پایتونی، بدون هیچ برنامه‌ی خارجی؛ هر فایل ~یک‌هزارم ثانیه.
       برای ۱۴۰۰ ویدیو یعنی چند ثانیه، نه چند دقیقه.
    ۲) ffprobe (اگر روی سیستم باشد) برای فرمت‌های عجیب‌تر (TS/FLV/MPG…)
    ۳) ffmpeg (پشتیبان آخر)

- نتایج در durations.db (SQLite استاندارد) کش می‌شوند (پایدار بین اجراها) —
  هر save فقط کلیدهای تازه را می‌نویسد، نه بازنویسیِ کلِ یک JSON بزرگ؛
  durations.json قدیمی در اولین اجرا خودکار مهاجرت می‌کند
- همه‌ی فراخوانی‌های subprocess کاملاً مخفی‌اند (بدون پنجره‌ی کنسول)
- DurationJob برای محاسبه در پس‌زمینه (QThreadPool)
"""
from __future__ import annotations

from i18n import T

import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time

from portable import bundled_binary
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from watchtime import dbg_log

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
    _QT = True
except Exception as _exc_dbg:  # pragma: no cover
    dbg_log(f"media_info.<module> failed: {type(_exc_dbg).__name__}")
    _QT = False

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_MAX_SANE = 40 * 86400   # هر چیزی بیش از ۴۰ روز = خروجی خراب پارسر


def _d(x) -> str:
    """ارقام متناسب با زبانِ رابط (0.45.0): فارسی → ۰۱۲۳، انگلیسی → لاتین."""
    s = str(x)
    from i18n import is_rtl
    return s.translate(FA_DIGITS) if is_rtl() else s


def format_duration(sec) -> str:
    """مثل: «۲ ساعت و ۳۵ دقیقه» / «۴۵ دقیقه» / «۳۰ ثانیه»"""
    if not sec or sec <= 0:
        return ""
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    def n(x): return _d(x)
    if h and m:
        return T("{0} ساعت و {1} دقیقه").format(n(h), n(m))
    if h:
        return T("{0} ساعت").format(n(h))
    if m:
        return T("{0} دقیقه").format(n(m))
    return T("{0} ثانیه").format(n(s))


def format_duration_short(sec) -> str:
    """قالب جمع‌وجور برای کارت‌ها: «۲:۲۵» یا «۴۵ دقیقه»."""
    if not sec or sec <= 0:
        return ""
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{_d(h)}:{_d(str(m).zfill(2))}"
    if m:
        return T("{0} دقیقه").format(_d(m))
    return T("{0} ثانیه").format(_d(s))


def hidden_kwargs(timeout=30) -> dict:
    """آرگومان‌های subprocess بدون هیچ پنجره‌ای (ویندوز)."""
    kw = {"capture_output": True, "timeout": timeout}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


# ============================================================ خواندن سریع هدرها
# ============================================================ MKV / WebM (EBML)

_E_MASTERS = (0x1A45DFA3, 0x18538067, 0x1549A966)   # EBML, Segment, Info
_E_TIMESCALE = 0x2AD7B1
_E_DURATION = 0x4489


def _vint_len(buf: bytes, pos: int) -> int:
    """طول VINT بر اساس بایت اول (برای ID و Size مشترک)."""
    if pos >= len(buf):
        return 0
    b0 = buf[pos]
    if b0 == 0:
        return 0
    n, mask = 1, 0x80
    while n <= 8 and not (b0 & mask):
        mask >>= 1
        n += 1
    return n if pos + n <= len(buf) else 0


def _read_id(buf: bytes, pos: int):
    n = _vint_len(buf, pos)
    if not n:
        return None, 0
    return int.from_bytes(buf[pos:pos + n], "big"), n


def _read_size(buf: bytes, pos: int):
    """(مقدار، طول)؛ مقدار None یعنی «اندازه‌ی نامشخص» (همه بیت‌ها ۱)."""
    n = _vint_len(buf, pos)
    if not n:
        return None, 0
    v = int.from_bytes(buf[pos:pos + n], "big") & ((1 << (7 * n)) - 1)
    if v == (1 << (7 * n)) - 1:
        return None, n      # unknown size
    return v, n


def _mkv_scan(buf: bytes, start: int, end: int, scale, dur, depth: int):
    """اسکن عناصر EBML دنبال TimecodeScale و Duration (داخل Info)."""
    pos = start
    while pos + 2 <= end and (scale is None or dur is None):
        eid, il = _read_id(buf, pos)
        if eid is None:
            break
        size, sl = _read_size(buf, pos + il)
        if sl == 0:
            break
        dstart = pos + il + sl
        dend = end if size is None else min(dstart + size, end)
        if dend < dstart:
            break
        if eid == _E_TIMESCALE and 0 < dend - dstart <= 8:
            scale = int.from_bytes(buf[dstart:dend], "big")
        elif eid == _E_DURATION and dend - dstart in (4, 8):
            fmt = ">f" if dend - dstart == 4 else ">d"
            dur = struct.unpack(fmt, buf[dstart:dend])[0]
        elif eid in _E_MASTERS and depth < 3:
            scale, dur = _mkv_scan(buf, dstart, dend, scale, dur, depth + 1)
        pos = dend if dend > pos else dstart + 1
    return scale, dur



def _rb(path):
    r"""باز کردن فایل برای خواندن — با پشتیبانی مسیرهای بلند ویندوز (\\?\ پیشوند)."""
    p = str(path)
    if sys.platform == "win32" and len(p) > 240 and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return open(p, "rb")


def _mkv_duration(path) -> float | None:
    for chunk in (64 * 1024, 512 * 1024, 4 * 1024 * 1024):
        with _rb(path) as f:
            buf = f.read(chunk)
        if len(buf) < 32:
            return None
        scale, dur = _mkv_scan(buf, 0, len(buf), None, None, 0)
        if dur is not None:
            if chunk > 64 * 1024 and scale is None and dur == 0:
                return None
            scale = scale or 1_000_000          # پیش‌فرض ماتروسکا: ۱ میلی‌ثانیه
            sec = dur * scale / 1e9
            if 0 < sec < _MAX_SANE:
                return sec
            return None
        if len(buf) < chunk:                    # فایل کوچک‌تر از بافر — تمام شد
            return None
    return None


# ------------------------------------------------------------------- MP4/MOV

def _mvhd_parse(buf: bytes) -> float | None:
    if len(buf) < 20:
        return None
    ver = buf[0]
    if ver == 1:
        if len(buf) < 32:
            return None
        timescale = int.from_bytes(buf[20:24], "big")
        duration = int.from_bytes(buf[24:32], "big")
    else:
        timescale = int.from_bytes(buf[12:16], "big")
        duration = int.from_bytes(buf[16:20], "big")
    if not timescale or duration in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return None
    sec = duration / timescale
    return sec if 0 < sec < _MAX_SANE else None


def _mp4_walk(f, start: int, end: int, depth: int) -> float | None:
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size = int.from_bytes(hdr[:4], "big")
        typ = hdr[4:8]
        hsize = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size, hsize = int.from_bytes(ext, "big"), 16
        elif size == 0:
            size = end - pos
        if size < hsize:
            break
        cstart, cend = pos + hsize, min(pos + size, end)
        if typ == b"mvhd":
            f.seek(cstart)
            d = _mvhd_parse(f.read(min(cend - cstart, 64)))
            if d:
                return d
        elif typ in (b"moov", b"moof") and depth < 2:
            d = _mp4_walk(f, cstart, cend, depth + 1)
            if d:
                return d
        pos += size
    return None


def _mp4_duration(path) -> float | None:
    with _rb(path) as f:
        f.seek(0, os.SEEK_END)
        fsize = f.tell()
        if fsize < 16:
            return None
        return _mp4_walk(f, 0, fsize, 0)


# ------------------------------------------------------------------- AVI

def _avi_duration(path) -> float | None:
    with _rb(path) as f:
        head = f.read(256 * 1024)
    if len(head) < 40 or head[:4] != b"RIFF" or head[8:12] != b"AVI ":
        return None
    idx = head.find(b"avih", 12)
    if idx < 0 or idx + 8 + 20 > len(head):
        return None
    data = idx + 8
    usec = int.from_bytes(head[data:data + 4], "little")
    frames = int.from_bytes(head[data + 16:data + 20], "little")
    if not usec or not frames:
        return None
    sec = usec * frames / 1e6
    return sec if 0 < sec < _MAX_SANE else None


# ------------------------------------------------------------------- ASF/WMV

_ASF_HEADER_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_FILE_PROPS_GUID = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")


def _asf_duration(path) -> float | None:
    with _rb(path) as f:
        head = f.read(128 * 1024)
    if len(head) < 30 or head[:16] != _ASF_HEADER_GUID:
        return None
    hsize = int.from_bytes(head[16:24], "little")
    obj_count = int.from_bytes(head[24:28], "little")
    pos, hend = 30, min(hsize, len(head))
    for _ in range(min(obj_count, 200)):
        if pos + 24 > hend:
            break
        guid = head[pos:pos + 16]
        osize = int.from_bytes(head[pos + 16:pos + 24], "little")
        if osize < 24:
            break
        if guid == _ASF_FILE_PROPS_GUID and osize >= 24 + 64 and pos + osize <= hend:
            d0 = pos + 24
            play = int.from_bytes(head[d0 + 40:d0 + 48], "little")      # واحد: ۱۰۰ نانوثانیه
            preroll = int.from_bytes(head[d0 + 56:d0 + 64], "little")   # میلی‌ثانیه
            sec = play / 1e7 - preroll / 1000.0
            return sec if 0 < sec < _MAX_SANE else None
        pos += osize
    return None


# ------------------------------------------------------------------- ورودی واحد

def fast_duration(path) -> float | None:
    """خواندن مستقیم هدر فرمت‌های رایج — ~میلی‌ثانیه برای هر فایل، بدون subprocess."""
    ext = Path(path).suffix.lower()
    try:
        if ext in (".mkv", ".webm"):
            return _mkv_duration(path)
        if ext in (".mp4", ".m4v", ".mov", ".3gp"):
            return _mp4_duration(path)
        if ext == ".avi":
            return _avi_duration(path)
        if ext in (".wmv", ".asf", ".wma"):
            return _asf_duration(path)
    except (OSError, ValueError, struct.error, OverflowError, IndexError):
        return None
    return None


def probe_duration(path) -> float | None:
    """مدت‌زمان به ثانیه؛ یا None اگر نشد."""
    d = fast_duration(path)
    if d:
        return d
    # اولویت با باینری همراهِ برنامه (bin/ کنار exe — اجرای پرتابل بدون پیش‌نیاز)
    ffprobe = bundled_binary("ffprobe.exe") or shutil.which("ffprobe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                **hidden_kwargs(20),
            )
            txt = r.stdout.decode("utf-8", "ignore").strip()
            if txt:
                return float(txt)
        except Exception as _exc_dbg:
            dbg_log(f"probe_duration failed: {type(_exc_dbg).__name__}")
            pass
    ffmpeg = bundled_binary("ffmpeg.exe") or shutil.which("ffmpeg")
    if ffmpeg:
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)], **hidden_kwargs(20),
            )
            m = re.search(r"Duration\s*:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                          r.stderr.decode("utf-8", "ignore"))
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return h * 3600 + mi * 60 + s
        except Exception as _exc_dbg:
            dbg_log(f"probe_duration failed: {type(_exc_dbg).__name__}")
            pass
    return None


class DurationStore:
    """کش پایدار مدت‌زمان‌ها (SQLite داخل پوشه‌ی کش برنامه).

    رابطه: get/set/save/clear/close — مثل قبل، ولی هر save فقط کلیدهای کثیف
    را می‌نویسد؛ با هزاران فایل دیگر چندثانیه گیر نمی‌کند.
    """

    def __init__(self, cache_dir):
        # فال‌بکِ فقط‌خواندنی (D2) — مثل ThumbnailCache/FileThumbs؛ قبلاً
        # mkdirِ مستقیم روی پوشه‌ی برنامه‌ی فقط‌خواندنی برنامه را در استارت
        # می‌کشت. lazy import: media_info وابستگیِ جدیدِ سطحِ ماژول نمی‌گیرد.
        from imgcache import writable_cache_dir
        d, ro = writable_cache_dir(cache_dir)
        self.dir = d
        self.read_only_fallback = ro
        self.path = self.dir / "durations.json"      # قدیمی — فقط برای مهاجرت
        self.db_path = self.dir / "durations.db"
        self._lock = threading.RLock()
        # همان باگِ malformed: کشِ زمان‌ها هم خودترمیم است — فایلِ خراب
        # قرنطینه و تازه ساخته می‌شود (بدترین حالت = پروبِ دوباره‌ی زمان‌ها،
        # نه مرگِ برنامه).
        self._con = self._open_con_safe(self.db_path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS durations (k TEXT PRIMARY KEY, v REAL NOT NULL)")
        self._data: dict[str, float] = {}
        self._dirty: set[str] = set()
        try:
            for k, v in self._con.execute("SELECT k, v FROM durations"):
                self._data[k] = v
        except sqlite3.Error:
            pass
        self._migrate_legacy()

    @staticmethod
    def _open_con_safe(db_path: Path):
        """اتصال WAL امن + خودترمیمی (0.41.0) — خراب → قرنطینه + تازه (بدون کراش)."""
        def _fresh():
            con = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=5000")
            res = con.execute("PRAGMA quick_check").fetchone()
            if not res or str(res[0]).lower() != "ok":
                con.close()
                raise sqlite3.DatabaseError("durations quick_check failed")
            return con
        try:
            return _fresh()
        except sqlite3.DatabaseError:
            import datetime as _dt
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            db_path = Path(db_path)
            for cand in (db_path,) + tuple(
                    Path(str(db_path) + sfx) for sfx in ("-wal", "-shm", "-journal")):
                try:
                    if cand.exists():
                        cand.rename(cand.with_name(cand.name + ".corrupt-" + stamp))
                except OSError:
                    pass
            return _fresh()

    def _migrate_legacy(self):
        """ورود durations.json قدیمی به SQLite (یک‌بار) + تغییر نام به .bak."""
        if not self.path.exists():
            return
        if self._data:                       # دیتابیس پر است — JSON فقط کنار گذاشته شود
            try:
                self.path.rename(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            return
        try:
            legacy = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as _exc_dbg:
            dbg_log(f"_migrate_legacy failed: {type(_exc_dbg).__name__}")
            legacy = {}
        if isinstance(legacy, dict) and legacy:
            self._data.update(legacy)
            try:
                self._con.executemany(
                    "INSERT OR REPLACE INTO durations (k, v) VALUES (?, ?)",
                    [(str(k), float(v)) for k, v in legacy.items()])
                self._con.commit()
            except sqlite3.Error:
                pass
        try:
            self.path.rename(self.path.with_suffix(".json.bak"))
        except OSError:
            pass

    def _key(self, path) -> str:
        try:
            st = Path(path).stat()
            return f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            return str(path)

    def get(self, path):
        """ثانیه، یا -1 (نامشخص)، یا None (هنوز محاسبه نشده)."""
        return self._data.get(self._key(path))

    def set(self, path, value):
        """فقط در حافظه + صف کثیف — ذخیره‌ی دیسک با save() (زمان‌بندی‌شده در probe_many)."""
        k = self._key(path)
        self._data[k] = value
        self._dirty.add(k)

    def save(self):
        """فلاش کلیدهای کثیف به SQLite — یک تراکنش کوچک، بدون بازنویسی کل کش."""
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        rows = [(k, float(self._data[k])) for k in dirty if k in self._data]
        if not rows:
            return
        with self._lock:
            try:
                self._con.executemany(
                    "INSERT OR REPLACE INTO durations (k, v) VALUES (?, ?)", rows)
                self._con.commit()
            except sqlite3.Error:
                self._dirty.update(dirty)

    def clear(self):
        """پاک‌سازی کامل کش زمان‌ها (برای «بازمحاسبه‌ی همه»)."""
        self._data = {}
        self._dirty = set()
        with self._lock:
            try:
                self._con.execute("DELETE FROM durations")
                self._con.commit()
            except sqlite3.Error:
                pass
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def close(self):
        """فلاش نهایی + بستن اتصال (هنگام خروج برنامه)."""
        try:
            self.save()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._con.close()
        except Exception as _exc_dbg:
            dbg_log(f"close failed: {type(_exc_dbg).__name__}")
            pass


def probe_many(paths, store: DurationStore, workers: int = 16,
               progress=None, save_interval: float = 2.0) -> int:
    """پروب موازی چند فایل با ThreadPool.

    - خواندن هدر (MKV/MP4/AVI/WMV) فوری است؛ subprocess فقط برای فرمت‌های دیگر.
    - فقط فایل‌هایی که در کش نیستند پروب می‌شوند.
    - ذخیره‌ی دیسک زمان‌بندی‌شده (هر چند ثانیه یک‌بار) — نه به‌ازای هر چند فایل،
      تا با هزاران فایل هم زمان ذخیره‌سازی ثابت بماند.
    برمی‌گرداند: تعداد فایل‌های پروب‌شده.
    """
    todo = [p for p in paths if store.get(p) is None]
    total = len(todo)
    if not total:
        return 0
    done = 0
    last_save = time.monotonic()

    def _one(p):
        dur = probe_duration(p)
        return p, (dur if dur else -1)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p, val in ex.map(_one, todo):
            store.set(p, val)
            done += 1
            now = time.monotonic()
            if now - last_save >= save_interval:
                store.save()
                last_save = now
            if progress:
                progress(done, total)
    store.save()
    return done


if _QT:
    class DurationSignals(QObject):
        ready = Signal(str, float)   # مسیر فایل، مدت‌زمان (۰/منفی = نامشخص)

    class DurationJob(QRunnable):
        def __init__(self, store: DurationStore, path: str, signals: DurationSignals):
            super().__init__()
            self.store = store
            self.path = path
            self.signals = signals

        def run(self):
            d = probe_duration(self.path)
            self.store.set(self.path, d if d else -1)
            self.store.save()
            try:
                self.signals.ready.emit(self.path, d if d else -1.0)
            except RuntimeError:
                pass          # بستن برنامه — منبع سیگنال حذف شده
else:  # pragma: no cover
    DurationSignals = DurationJob = None
