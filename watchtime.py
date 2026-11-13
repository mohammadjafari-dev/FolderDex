# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""ردیابی تماشا با پخش‌کننده‌های رایج.

روش کار به‌ازای هر پخش‌کننده (خودکار، بدون حدس‌زدن):

- **mpv** (بهترین):
    mpv --save-position-on-quit=yes --watch-later-directory=<کش>/watch_later
        --input-ipc-server=<پایپ/سوکت> [--start=X] فایل
  ردیابی زنده: مانیتور داخلی هر ۱.۵ ثانیه time-pos را از IPC می‌خواند و برنامه
  همان‌جا ذخیره‌اش می‌کند — حتی اگر برنامه وسط تماشا بسته شود چیزی گم نمی‌شود.
  موقع بستن: فایل وضعیت نیست → mpv خودش هنگام پایان کامل پاکش کرده → «دیده‌شده ✓»؛
  وگرنه start=X از فایل وضعیت → «نیمه‌تمام ◐» (≥۹۵٪ → ✓). ادامه با --start=X.

- **PotPlayer** (ردیابی واقعی، بدون هیچ نصب/تنظیم اضافه):
  PotPlayer یک API پیام-پنجره‌ای دارد؛ هر ۱.۵ ثانیه با SendMessage به پنجره‌اش
  (همان نمونه‌ای که خودمان ساخته‌ایم — تشخیص با PID) این‌ها را می‌پرسیم:
      WM_USER+0x5004 → موقعیت فعلی (میلی‌ثانیه)
      WM_USER+0x5002 → مدت کل (میلی‌ثانیه)
      WM_USER+0x5006 → وضعیت (توقف/پاز/پخش)
  پس موقعیت «واقعی» ذخیره می‌شود (سیک‌جلو/عقب هم لحاظ می‌شود) و هنگام بستن:
      ≥۹۵٪ دیده → ✓ | کمتر → ◐ با همان موقعیت.
  فایل با ‎/new در نمونه‌ی مستقل باز می‌شود (تا پروسه قابل‌ردیابی بماند) و
  رزومه با ‎/seek=hh:mm:ss. اگر پنجره پیدا نشود → برگشت بی‌صدا به تخمین زمان پخش.

- **VLC**: باز کردن با --start-time=X (رزومه) + ردیابی تخمینی (زمان پخش مداوم؛
  VLC موقعیت را به بیرون نمی‌دهد — تماشای خطی فرض می‌شود). برای دقتِ کامل،
  mpv یا PotPlayer بهتر است.

- پخش‌کننده‌ی دلخواه (custom exe): ردیابی تخمینی (بدون رزومه‌ی جانبی).
- پیش‌فرض ویندوز: پروسه در دسترس نیست → بدون ردیابی (با هشدار شفاف).

مسیرهای ناموفق همیشه بی‌صدا به حالت پایین‌تر برمی‌گردند و «note» دلیلش را می‌گوید.
"""
from __future__ import annotations

from i18n import T

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

WATCH_DIR_NAME = "watch_later"

# ------------------------------------------------------------------ لاگ عیب‌یابی ردیابی
# رویدادهای کلیدی ردیاب پخش (انتخاب پلیر، یافتن پنجره‌ی PotPlayer، تعویض قسمت…)
# در یک فایل لاگ سبک ثبت می‌شود تا اگر روی سیستم کاربر چیزی کار نکرد، دلیلش
# بدون نیاز به بازتولید مشکل مشخص شود. بدون افکت محسوس روی کارایی.

_DEBUG_LOG: Path | None = None
_DEBUG_LOCK = threading.Lock()


def set_debug_log(path):
    """فعال/غیرفعال‌کردن لاگ ردیابی — main.py آن را کنار برنامه تنظیم می‌کند."""
    global _DEBUG_LOG
    _DEBUG_LOG = Path(path) if path else None


def dbg_log(msg: str):
    """نوشتن یک خط در لاگ عیب‌یابی — نام عمومیِ _dbg برای ماژول‌های دیگر.

    امنیت (انتشار عمومی): مسیرهای جایگزین‌شده در لاگِ بی‌نام — مسیرِ مطلقِ
    فایل‌ها هرگز در لاگ نمی‌نشیند (همان _redact). ماژول‌های backup/store/
    filethumbs/ui از این برای ثبتِ خطاهای بی‌صدا (except: pass) استفاده
    می‌کنند تا عیب‌یابی بدون فاش‌کردن مسیر ممکن باشد."""
    _dbg(msg)


# ------------------------------------------------------------------ لاگِ بی‌نام
def safe_ref(p) -> str:
    """ارجاعِ امن برای لاگ: فقط پسوند + اثر انگشتِ کوتاه (۸ هگز از sha1 مسیرِ
    نرمال‌شده) — بدون حتی یک حرف از اسم/مسیر فایل. درخواست کاربر: «لاگ طوری
    بنویس که مسیر و اسم فایل ننویسه تا کل لاگ رو بتونم بفرستم». فایلِ یکسان
    همیشه کدِ یکسان می‌گیرد پس خطوط لاگ قابل دسته‌بندی می‌مانند."""
    try:
        s = os.path.normcase(str(p)).lower()   # ویندوز درایو/مسیر حساس به حروف نیست
        dig = hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:8]
        suf = Path(s).suffix.lower()
        return f"{suf or '[dir]'}#{dig}"
    except Exception:
        return ".#?"


def _hash_str(s: str) -> str:
    """هشِ کوتاه برای متنِ آزاد (مثل تایتل پنجره‌ی پلیر که اسم فایل دارد)."""
    try:
        return "#" + hashlib.sha1(str(s).encode("utf-8", "replace")).hexdigest()[:8]
    except Exception:
        return "#?"


def _dbg_pot_window(pid: int, title: str) -> None:
    """لاگِ «پنجره‌ی پلیر یافت شد» — تایتل هرگز خام لاگ نمی‌شود (round 19 §۱۱).

    تایتلِ پنجره‌ی PotPlayer اسمِ قسمت/رسانه را دارد و docstringِ safe_ref صریحاً
    می‌گوید نامِ فایل نباید در لاگ برود؛ _redact هم فقط مسیرِ مطلق/UNC را
    می‌گیرد نه متنِ آزاد — پس همین‌جا هش می‌شود (همان الگوی _hash_str)."""
    _dbg(T("potplayer: پنجره یافت شد (pid={0}, title={1})").format(
        pid, _hash_str(title)))


# بک‌استاپ: حتی اگر جایی فراموش شود، هیچ مسیرِ مطلقی به لاگ نمی‌رسد. مسیرهای
# درایو‌دار ویندوز و UNC شناسایی و با «پسوند#هش» جایگزین می‌شوند (گروه‌بندی
# حفظ می‌شود ولی نام/مسیر هرگز پیدا نیست).
_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\r\n\"'<>`]+|\\\\[^\s\r\n\"'<>`]+)")


def _redact(msg: str) -> str:
    def _rep(m):
        s = m.group(0)
        base = s.replace("\\", "/").rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[1].lower() if "." in base else ""
        suf = f".{ext}" if re.fullmatch(r"[a-z0-9]{1,6}", ext or "") else "[dir]"
        dig = hashlib.sha1(os.path.normcase(s).lower().encode("utf-8", "replace")).hexdigest()[:8]
        return f"{suf}#{dig}"
    try:
        return _PATH_RE.sub(_rep, msg)
    except Exception:
        return msg


def _dbg(msg: str):
    """یک خط لاگ عیب‌یابی (امن در برابر خطا؛ چرخش فایل بالای ~۲۵۶KB).
    0.30.0: خط خودکار از _redact می‌گذرد — هیچ مسیر فایلی در لاگ نمی‌نشیند."""
    path = _DEBUG_LOG
    if path is None:
        return
    try:
        msg = _redact(msg)

        with _DEBUG_LOCK:
            try:
                if path.exists() and path.stat().st_size > 256 * 1024:
                    tail = path.read_bytes()[-128 * 1024:]
                    path.write_bytes(b"[...rotated...]\n" + tail)
            except OSError:
                pass
            import datetime as _dt
            line = f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # this IS the logger: logging a failure of the logger itself would
        # recurse into the same code path — silence here is required.
        pass

# ------------------------------------------------------------------ یافتن پخش‌کننده


def _which(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _win_reg_app(exe_name: str) -> str | None:
    """خواندن مسیر نصب از رجیستری (فقط ویندوز، محافظه‌شده)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for sub in (
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                rf"Software\Clients\Media\{exe_name.split('.')[0]}\shell\open\command",
            ):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        val, _ = winreg.QueryValueEx(k, "")
                except OSError:
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            val, _ = winreg.QueryValueEx(k, "Path")
                    except OSError:
                        continue
                if val:
                    m = re.search(r'"([^"]+\.exe)"', val) or re.search(r"([^\s]+\.exe)", val)
                    if m and Path(m.group(1)).is_file():
                        return m.group(1)
    except Exception as _exc_dbg:
        _dbg(f"_win_reg_app failed: {type(_exc_dbg).__name__}")
        pass
    return None


def find_mpv() -> str | None:
    p = _which(("mpv", "mpv.exe"))
    if p:
        return p
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\mpv\mpv.exe",
            r"C:\Program Files (x86)\mpv\mpv.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\mpv\mpv.exe"),
            os.path.expandvars(r"%USERPROFILE%\scoop\apps\mpv\current\mpv.exe"),
            os.path.expandvars(r"%ProgramData%\chocolatey\bin\mpv.exe"),
        ):
            if Path(cand).is_file():
                return cand
        return _win_reg_app("mpv.exe")
    for cand in ("/usr/bin/mpv", "/usr/local/bin/mpv", "/snap/bin/mpv"):
        if Path(cand).is_file():
            return cand
    return None


def find_vlc() -> str | None:
    p = _which(("vlc", "vlc.exe"))
    if p:
        return p
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ):
            if Path(cand).is_file():
                return cand
        return _win_reg_app("vlc.exe")
    for cand in ("/usr/bin/vlc", "/usr/local/bin/vlc", "/snap/bin/vlc"):
        if Path(cand).is_file():
            return cand
    return None


def find_potplayer() -> str | None:
    p = _which(("PotPlayerMini64", "PotPlayerMini", "PotPlayer64",
                "PotPlayerMini64.exe", "PotPlayer.exe"))
    if p:
        return p
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe",
            r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\DAUM\PotPlayer\PotPlayerMini64.exe"),
        ):
            if Path(cand).is_file():
                return cand
        return (_win_reg_app("PotPlayerMini64.exe")
                or _win_reg_app("PotPlayer.exe")
                or _win_reg_app("PotPlayerMini.exe"))
    return None


def _custom_kind(exe_name: str) -> str:
    low = os.path.basename(exe_name).lower()
    if "potplayer" in low:
        return "potplayer"
    if "mpv" in low:
        return "mpv"
    if "vlc" in low:
        return "vlc"
    return "elapsed"


_RESOLVE_CACHE: dict[tuple, dict] = {}


def resolve_player(player_cfg: dict, _fresh: bool = False) -> dict:
    """بر اساس تنظیمات کاربر: {"mode": auto|mpv|vlc|potplayer|default|custom, "path": str}.

    kind خروجی:
      mpv       → ردیابی کامل زنده (IPC) دقیق + ادامه از موقعیت
      potplayer → ردیابی واقعی زنده (پیام‌های ویندوز) + رزومه با /seek
      vlc       → رزومه با --start-time + ردیابی تخمینی (زمان پخش)
      elapsed   → هر پخش‌کننده‌ی دلخواه دیگر — ردیابی تخمینی (بدون رزومه)
      default   → پخش‌کننده‌ی پیش‌فرض سیستم (بدون ردیابی — پروسه در دسترس نیست)

    نتیجه کش می‌شود تا تایپ کردن در تنظیمات/پخش‌های پشت‌سرهم جست‌وجوی سیستم
    را تکرار نکند. کلید «note» وقتی پر است که انتخاب کاربر پیدا نشده و چیز
    دیگری جایگزین شده — UI آن را شفاف نشان می‌دهد.
    """
    cfg = player_cfg or {}
    mode = (cfg.get("mode") or "auto").strip()
    path = (cfg.get("path") or "").strip()
    key = (mode, path)
    if not _fresh and key in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[key]

    result = _resolve_player_uncached(mode, path)
    # کش را محدود نگه دار (حالت‌های ممکن کم‌اند)
    if len(_RESOLVE_CACHE) > 32:
        _RESOLVE_CACHE.clear()
    _RESOLVE_CACHE[key] = result
    return result


def invalidate_player_cache():
    _RESOLVE_CACHE.clear()


def _resolve_player_uncached(mode: str, path: str) -> dict:
    ok = lambda p: bool(p) and Path(p).is_file()

    if mode == "custom":
        if ok(path):
            return {"kind": _custom_kind(path), "path": path}
        p2 = find_potplayer()
        if p2:
            return {"kind": "potplayer", "path": p2,
                    "note": T("مسیر دستی معتبر نیست → PotPlayer")}
        return {"kind": "default", "path": None,
                "note": T("مسیر دستی معتبر نیست → پخش‌کننده‌ی پیش‌فرض")}
    if mode == "mpv":
        p = path if ok(path) else find_mpv()
        if p:
            return {"kind": "mpv", "path": p}
        p2 = find_potplayer()
        if p2:
            return {"kind": "potplayer", "path": p2, "note": T("mpv پیدا نشد → PotPlayer")}
        return {"kind": "default", "path": None,
                "note": T("mpv پیدا نشد (نصب نیست) → پخش‌کننده‌ی پیش‌فرض")}
    if mode == "vlc":
        p = path if ok(path) else find_vlc()
        if p:
            return {"kind": "vlc", "path": p}
        p2 = find_potplayer()
        if p2:
            return {"kind": "potplayer", "path": p2, "note": T("VLC پیدا نشد → PotPlayer")}
        return {"kind": "default", "path": None,
                "note": T("VLC پیدا نشد (نصب نیست) → پخش‌کننده‌ی پیش‌فرض")}
    if mode == "potplayer":
        p = path if ok(path) else find_potplayer()
        if p:
            return {"kind": "potplayer", "path": p}
        return {"kind": "default", "path": None,
                "note": T("PotPlayer پیدا نشد → پخش‌کننده‌ی پیش‌فرض")}
    if mode == "default":
        return {"kind": "default", "path": None}
    # auto: ردیابیِ کامل‌تر اول — PotPlayer (موقعیت واقعی + تعقیب «قسمت بعدی»)
    # ← mpv (IPC دقیق) ← VLC (تخمینی). درخواست کاربر: PotPlayer در اولویت.
    for finder, kind in ((find_potplayer, "potplayer"), (find_mpv, "mpv"),
                         (find_vlc, "vlc")):
        p = finder()
        if p:
            return {"kind": kind, "path": p}
    return {"kind": "default", "path": None}


# ------------------------------------------------------------------ مسیرها و حالت mpv

def watch_dir(base_cache) -> Path:
    d = Path(base_cache) / WATCH_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_candidates(state_dir: Path, media_path: Path) -> list[Path]:
    p = str(media_path)
    h1 = hashlib.md5(p.encode("utf-8")).hexdigest()
    h2 = hashlib.md5(p.replace("/", "\\").encode("utf-8")).hexdigest()
    h3 = hashlib.md5(p.replace("\\", "/").encode("utf-8")).hexdigest()
    out = []
    for h in dict.fromkeys((h1, h2, h3)):
        out.append(state_dir / h)
        out.append(state_dir / h.upper())
    return out


def parse_state_file(path: Path) -> float | None:
    """خواندن start= از فایل وضعیت mpv؛ مقدار = ثانیه."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r"(?m)^start=([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ مانیتور زنده‌ی mpv

class MpvIpcMonitor(threading.Thread):
    """پرسش دوره‌ای time-pos و path از mpv از طریق JSON IPC.

    time-pos → ذخیره‌ی زنده‌ی موقعیت؛
    path → تشخیص تعویض قسمت (اگر mpv/کاربر سراغ فایل بعدی رفت) تا ردیابی روی
    قسمت جدید ادامه پیدا کند و فقط قسمت اولِ «پخش هوشمند» ثبت نشود.
    """

    INTERVAL = 1.5          # هر چند ثانیه موقعیت بپرسد
    CONNECT_TIMEOUT = 8.0   # حداکثر انتظار برای بالا آمدن پایپ/سوکت

    def __init__(self, server: str, proc: subprocess.Popen, on_pos, on_path=None):
        super().__init__(daemon=True)
        self.server = server
        self.proc = proc
        self.on_pos = on_pos
        self.on_path = on_path
        self._last_path: str | None = None
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    def _dispatch(self, msg) -> None:
        """پردازش یک پیام JSON IPC (جداگانه — قابل تست مستقیم)."""
        if not isinstance(msg, dict) or msg.get("error") != "success":
            return
        rid = msg.get("request_id")
        data = msg.get("data")
        try:
            if rid == 1 and isinstance(data, (int, float)) and self.on_pos:
                self.on_pos(float(data))
            elif rid == 2 and isinstance(data, str):
                # اولین گزارش path مبناست؛ تغییرهای بعدی یعنی تعویض فایل
                base = data.replace("/", os.sep)
                if self._last_path is None:
                    self._last_path = os.path.normcase(os.path.normpath(base))
                else:
                    cur = os.path.normcase(os.path.normpath(base))
                    if cur != self._last_path:
                        self._last_path = cur
                        if self.on_path:
                            self.on_path(data)
        except Exception as _exc_dbg:
            _dbg(f"_dispatch failed: {type(_exc_dbg).__name__}")
            pass

    def _connect(self):
        deadline = time.monotonic() + self.CONNECT_TIMEOUT
        while (time.monotonic() < deadline and self.proc.poll() is None
               and not self._stop_evt.is_set()):
            try:
                if sys.platform == "win32":           # نام پایپ: \\.\pipe\...
                    return open(self.server, "r+b", buffering=0)
                import socket
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect(self.server)
                return s.makefile("rwb", buffering=0)
            except OSError:
                self._stop_evt.wait(0.25)
        return None

    def run(self):
        conn = None
        try:
            conn = self._connect()
            if conn is None:
                _dbg(T("mpv: اتصال IPC برقرار نشد — ردیابی زنده غیرفعال ماند"))
                return
            _dbg(T("mpv: اتصال IPC برقرار شد — ردیابی زنده فعال"))
            buf = bytearray()
            while not self._stop_evt.is_set() and self.proc.poll() is None:
                try:
                    # هر دو خواسته را یکجا می‌فرستیم (request_id برای تشخیص پاسخ‌ها)
                    payload = (
                        b'{"command": ["get_property", "time-pos"], "request_id": 1}\n'
                        b'{"command": ["get_property", "path"], "request_id": 2}\n'
                    )
                    conn.write(payload)
                    conn.flush()
                except OSError:
                    break
                # پاسخ هر دو را می‌خوانیم (خط‌های بیشتر = رویدادهای بدون درخواست)
                got = 0
                while got < 2:
                    try:
                        while b"\n" not in buf:
                            chunk = conn.read(4096)
                            if not chunk:
                                raise EOFError
                            buf.extend(chunk)
                    except (OSError, EOFError):
                        got = -1
                        break
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf = bytearray(rest)
                    try:
                        msg = json.loads(line.decode("utf-8", "ignore"))
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("request_id"):
                        got += 1
                    self._dispatch(msg)
                if got < 0:
                    break
                self._stop_evt.wait(self.INTERVAL)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as _exc_dbg:
                    _dbg(f"run failed: {type(_exc_dbg).__name__}")
                    pass


# ------------------------------------------------------------------ API پنجره‌ی PotPlayer
# (منابع: مستندات غیررسمی AHK Library — WM_USER=0x0400؛ دستورات 0x5000..0x5010)

_POT_WM_USER = 0x0400
_POT_GET_TOTAL_TIME = 0x5002     # میلی‌ثانیه
_POT_GET_CURRENT_TIME = 0x5004   # میلی‌ثانیه
_POT_GET_PLAY_STATUS = 0x5006    # -1 توقف | 1 پاز | 2 پخش
_SMTO_ABORTIFHUNG = 0x0002

_pot_u32 = None        # user32 (ctypes) — lazy
_pot_enum_proc_type = None


def _pot_user32():
    """ctypes user32 — فقط روی ویندوز؛ روی پلتفرم‌های دیگر None."""
    global _pot_u32, _pot_enum_proc_type
    if sys.platform != "win32":
        return None
    if _pot_u32 is None:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        _pot_enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        _pot_u32 = u32
    return _pot_u32


def _pot_enum_hwnds_for_pid(pid: int) -> list[int]:
    """همه‌ی پنجره‌های قابل‌مشاهده‌ی متعلق به یک PID (بدون وابستگی به pywin32)."""
    import ctypes
    from ctypes import wintypes
    u32 = _pot_user32()
    if u32 is None:
        return []
    found: list[int] = []

    @_pot_enum_proc_type
    def _cb(hwnd, _lparam):
        proc_id = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and u32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    u32.EnumWindows(_cb, 0)
    return found


def _pot_class_name(hwnd: int) -> str:
    import ctypes
    from ctypes import wintypes
    u32 = _pot_user32()
    if u32 is None:
        return ""
    buf = ctypes.create_unicode_buffer(128)
    n = u32.GetClassNameW(wintypes.HWND(hwnd), buf, 128)
    return buf.value[:n] if n else ""


def _pot_pick_hwnd(pid: int) -> int | None:
    """پنجره‌ی اصلی PotPlayer این PID — اول کلاس‌های دقیق، بعد هر کلاس PotPlayer*."""
    hwnds = _pot_enum_hwnds_for_pid(pid)
    if not hwnds:
        return None
    exact = ("PotPlayer64", "PotPlayer Mini", "PotPlayer")
    ranked: list[int] = []
    for h in hwnds:
        cls = _pot_class_name(h)
        if cls in exact:
            return h
        if cls.lower().startswith("potplayer"):
            ranked.append(h)
    return ranked[0] if ranked else None


def _pot_query(hwnd: int, command: int, timeout_ms: int = 700) -> int | None:
    """SendMessageTimeout(WM_USER, command) → مقدار برگشتی (uint32) یا None."""
    import ctypes
    from ctypes import wintypes
    u32 = _pot_user32()
    if u32 is None or not hwnd:
        return None
    result = wintypes.DWORD(0)
    ok = u32.SendMessageTimeoutW(
        wintypes.HWND(hwnd), _POT_WM_USER, wintypes.WPARAM(command),
        wintypes.LPARAM(0), _SMTO_ABORTIFHUNG, timeout_ms,
        ctypes.byref(result),
    )
    if not ok:
        return None
    return int(result.value)


class PotPlayerMonitor(threading.Thread):
    """ردیابی زنده‌ی واقعی PotPlayer از طریق پیام‌های پنجره (هر ~۱.۵ ثانیه).

    on_pos(ثانیه) — آخرین موقعیت واقعی (سیک هم لحاظ می‌شود)
    on_dur(ثانیه) — مدت کل گزارش‌شده توسط پلیر (اگر >۰)
    on_status(k)  — -1 توقف | 1 پاز | 2 پخش
    on_file(مسیر) — PotPlayer رفت سراغ فایل دیگر (پخش خودکار قسمت بعدی یا Next)؛
                    تشخیص از روی تایتل‌بار. فراخوانی در *نخ مانیتور* است — مصرف‌کننده
                    فقط صفت اتمی را عوض کند، نه کار سنگین/ذخیره.
    اگر پنجره/پیام کار نکند، بی‌صدا تمام می‌شود (fallback تخمینی فعال می‌ماند).
    """

    INTERVAL = 1.5
    FIND_TIMEOUT = 12.0

    def __init__(self, proc: subprocess.Popen, on_pos, on_dur=None, on_status=None,
                 on_file=None, candidates: list | None = None, current_media: str = ""):
        super().__init__(daemon=True)
        self.proc = proc
        self.on_pos = on_pos
        self.on_dur = on_dur
        self.on_status = on_status
        self.on_file = on_file
        self.candidates = candidates or []
        self._current = current_media
        self.hwnd: int | None = None
        self._stop_evt = threading.Event()
        self._unmatched = 0     # شمارش تایتل‌های بی‌تطابق (برای لاگ محدود)

    def stop(self):
        self._stop_evt.set()

    def _ensure_hwnd(self, deadline: float) -> int | None:
        if self.hwnd and _pot_user32() is not None:
            try:
                if _pot_user32().IsWindow(wintypes_hwnd(self.hwnd)):
                    return self.hwnd
            except Exception as _exc_dbg:
                _dbg(f"_ensure_hwnd failed: {type(_exc_dbg).__name__}")
                pass
        while (time.monotonic() < deadline and self.proc.poll() is None
               and not self._stop_evt.is_set()):
            h = _pot_pick_hwnd(self.proc.pid)
            if h:
                self.hwnd = h
                try:
                    _dbg_pot_window(self.proc.pid, _pot_window_title(h))
                except Exception as _exc_dbg:
                    _dbg(f"_ensure_hwnd failed: {type(_exc_dbg).__name__}")
                    pass
                return h
            self._stop_evt.wait(0.4)
        return None

    def run(self):
        if _pot_user32() is None:          # غیر ویندوز — کاری نمی‌توان کرد
            return
        h = self._ensure_hwnd(time.monotonic() + self.FIND_TIMEOUT)
        if not h:
            _dbg(T("potplayer: پنجره‌ی پلیر در مهلت تعیین‌شده پیدا نشد — "
                 "ردیابی واقعی کنار گذاشته شد (تخمین زمان پخش فعال است)"))
            return
        fails = 0
        while not self._stop_evt.is_set() and self.proc.poll() is None:
            pos_ms = _pot_query(h, _POT_GET_CURRENT_TIME)
            if pos_ms is None:
                fails += 1
                if fails >= 4:             # پنجره جابه‌جا/بسته شده؟ دوباره پیدا کن
                    self.hwnd = None
                    h = self._ensure_hwnd(time.monotonic() + 4)
                    if not h:
                        return
                    fails = 0
            else:
                fails = 0
                if pos_ms > 0 and self.on_pos:
                    try:
                        self.on_pos(pos_ms / 1000.0)
                    except Exception as _exc_dbg:
                        _dbg(f"run failed: {type(_exc_dbg).__name__}")
                        pass
            try:
                if self.on_dur:
                    tot = _pot_query(h, _POT_GET_TOTAL_TIME)
                    if tot and tot > 0:
                        self.on_dur(tot / 1000.0)
                if self.on_status:
                    st = _pot_query(h, _POT_GET_PLAY_STATUS)
                    if st is not None:
                        self.on_status(st if st <= 2**31 else st - 2**32)
                # ---- تشخیص تعویض فایل از روی تایتل‌بار (پخش خودکار قسمت بعدی) ----
                if self.on_file and self.candidates:
                    title_now = _pot_window_title(h)
                    m = _match_media_title(title_now, self.candidates)
                    if m and m != self._current:
                        self._current = m
                        _dbg(T("potplayer: تعویض قسمت از روی عنوانِ پنجره → {0}").format(safe_ref(m)))
                        self.on_file(m)
                    elif not m and title_now:
                        # تایتل بیکار (بدون فایل) لاگ نمی‌شود؛ بقیه نمونه‌برداری هر ۳۰ بار
                        idle = title_now.strip().lower() in ("potplayer", "daum potplayer")
                        self._unmatched += 1
                        if not idle and self._unmatched % 30 == 1:
                            _dbg(T("potplayer: عنوانِ پنجره با هیچ قسمتی نخورد (len={0}{1})").format(len(title_now), _hash_str(title_now)))
            except Exception as _exc_dbg:
                _dbg(f"run failed: {type(_exc_dbg).__name__}")
                pass
            self._stop_evt.wait(self.INTERVAL)


def wintypes_hwnd(h):
    from ctypes import wintypes
    return wintypes.HWND(h)


def _pot_window_title(hwnd: int) -> str:
    """متن تایتل‌بار پنجره‌ی PotPlayer — برای تشخیص «کدام فایل الان دارد پخش می‌شود».

    PotPlayer پس از پایان یک قسمت، خودکار سراغ فایل بعدیِ پوشه می‌رود؛ نام فایلِ
    جاری در تایتل‌بار است (پیش‌فرض)، پس می‌توانیم تعویض قسمت را بفهمیم.
    """
    import ctypes
    from ctypes import wintypes
    u32 = _pot_user32()
    if u32 is None or not hwnd:
        return ""
    n = u32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if n <= 0 or n > 4096:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    if not u32.GetWindowTextW(wintypes.HWND(hwnd), buf, n + 1):
        return ""
    return buf.value


def _video_candidates(media: Path) -> list[tuple[str, str]]:
    """نامزدهای «قسمت‌های همان پوشه» برای تطبیق با تایتل: [(stem نرمال‌شده، مسیر)].

    بلندترین stem اول — تا مشابه‌نام‌ها (قسمت 1 / قسمت 10) درست تشخیص داده شوند.
    """
    from scanner import VIDEO_EXTS, fa_norm
    out: list[tuple[str, str]] = []
    try:
        for p in Path(media).parent.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                out.append((fa_norm(p.stem), str(p)))
    except OSError:
        pass
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out


def _match_media_title(title: str, candidates: list[tuple[str, str]]) -> str | None:
    """تایتل‌بار → مسیر فایل در حال پخش (اگر بین نامزدها باشد).

    نام‌ها با fa_norm یکسان‌سازی می‌شوند: «ي/ك» عربی، نیم‌فاصله، کشیده و
    حروف بزرگ اختلاف ایجاد نمی‌کنند (فایل‌های فارسی رایج‌اند).
    """
    from scanner import fa_norm
    t = fa_norm((title or "").strip())
    if not t:
        return None
    for stem, full in candidates:          # اول: شروع عنوان با stem (حالت پیش‌فرض)
        if stem and t.startswith(stem):
            return full
    for stem, full in candidates:          # بد: پوسته‌هایی که چیزهایی به تایتل می‌چسبانند
        if len(stem) >= 6 and stem in t:
            return full
    return None


# ------------------------------------------------------------------ پخش ردیابی‌شده: mpv

class WatchProc:
    """یک پخش در حال انجام — پس از بسته شدن پروسه، finalize وضعیت را تعیین می‌کند.

    صفت‌های زنده (حین پخش به‌روز می‌شوند):
        live_pos        آخرین موقعیت گزارش‌شده توسط مانیتور (ثانیه)
        last_saved_pos  آخرین موقعیتی که برنامه در استور نوشت (ثانیه)
    """

    elapsed_based = False

    def __init__(self, proc: subprocess.Popen, media: Path, state_dir: Path,
                 resume_pos: float, had_state: bool):
        self.proc = proc
        self.media = Path(media)
        self.state_dir = state_dir
        self.resume_pos = resume_pos
        self.had_state = had_state
        self.started = time.time()
        self.live_pos = 0.0
        self.last_saved_pos = 0.0
        self.monitor = None
        self.pending_media = None   # تعویض قسمت حین نشست (از مانیتور IPC — مصرف در نخ اصلی)

    def done(self) -> bool:
        return self.proc.poll() is not None

    def stop_monitor(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def finalize(self, duration: float) -> tuple[int, float] | None:
        """(mode, pos): 1=دیده‌شده ✓ | 2=نیمه‌تمام ◐ با pos | None=نامشخص."""
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        pos = None
        for cand in _state_candidates(self.state_dir, self.media):
            pos = parse_state_file(cand)
            if pos is not None:
                break
        if pos is None:
            # فایل وضعیتی نیست → mpv در پایان کامل فایل، وضعیت را حذف می‌کند
            if time.time() - self.started > 10:
                return (1, 0.0)
            return None
        if duration and pos >= duration * 0.95:
            return (1, 0.0)
        return (2, pos)


def _hidden_popen_kwargs() -> dict:
    """باز کردن پخش‌کننده‌ی GUI بدون پنجره‌ی کنسول اضافه."""
    kw: dict = {"close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _ipc_server_name(base_cache) -> str | None:
    """نام یکتای پایپ (ویندوز) یا سوکت (یونیکس) برای IPC این نمونه‌ی mpv."""
    tag = f"mcc-mpv-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{tag}"
    try:
        p = str(Path(base_cache) / (tag + ".sock"))
        if len(p.encode()) > 100:      # محدودیت sun_path سوکت یونیکس (~۱۰۸ بایت)
            return None
        Path(p).unlink(missing_ok=True)
        return p
    except OSError:
        return None


def launch_mpv(mpv_path: str, media, resume_pos: float, base_cache) -> WatchProc | None:
    media = Path(media)
    try:
        sdir = watch_dir(base_cache)
        had_state = any(c.exists() for c in _state_candidates(sdir, media))
        server = _ipc_server_name(base_cache)
        cmd = [mpv_path,
               "--save-position-on-quit=yes",
               f"--watch-later-directory={sdir}",
               "--really-quiet=no"]
        if server:
            cmd.append(f"--input-ipc-server={server}")
        if resume_pos and resume_pos > 5:
            cmd.append(f"--start={resume_pos:.1f}")
        cmd.append(str(media))
        proc = subprocess.Popen(cmd, **_hidden_popen_kwargs())
        wp = WatchProc(proc, media, sdir, resume_pos, had_state)
        _dbg(T("لانچ mpv: {0} ← {1} (رزومه {2}s)").format(Path(mpv_path).name, safe_ref(media), int(resume_pos)))
        if server:
            def _on_path(np_, _wp=wp):
                # تعویض قسمت در همان پنجره (پلی‌لیست/Next) → ثبت برای نخ اصلی
                from scanner import VIDEO_EXTS as _VX
                try:
                    if not np_:
                        return
                    p = Path(np_)
                    if p.is_file() and p.suffix.lower() in _VX \
                            and os.path.normcase(str(p)) != os.path.normcase(str(_wp.media)):
                        _dbg(T("mpv: تعویض فایل → {0}").format(safe_ref(p)))
                        _wp.pending_media = str(p)
                except (OSError, ValueError):
                    pass

            wp.monitor = MpvIpcMonitor(
                server, proc, lambda p: setattr(wp, "live_pos", p),
                on_path=_on_path)
            wp.monitor.start()
        return wp
    except Exception as _exc_dbg:
        _dbg(f"launch_mpv failed: {type(_exc_dbg).__name__}")
        return None


# ------------------------------------------------------------------ ردیابی تخمینی (پایه)

class ElapsedProc:
    """پخش تخمینی: پخش‌کننده‌هایی که موقعیت را به بیرون نمی‌دهند (VLC و exe دلخواه).

    موقعیت ≈ موقعیت شروع + زمان سپری‌شده‌ی پخش (تماشای مداوم و خطی فرض می‌شود):
      ≥۹۵٪ مدت  → «دیده‌شده ✓»      کمتر → «نیمه‌تمام ◐» با موقعیت تخمینی
    live_pos به‌صورت زنده رشد می‌کند (توسط PlayTracker) تا نشان پیشرفت هم به‌روز بماند.
    """

    elapsed_based = True

    def __init__(self, proc: subprocess.Popen, media: Path, start_pos: float):
        self.proc = proc
        self.media = Path(media)
        self.start_pos = float(start_pos or 0.0)
        self.started = time.time()
        self.resume_pos = self.start_pos
        self.live_pos = 0.0
        self.last_saved_pos = 0.0
        self.monitor = None

    def done(self) -> bool:
        return self.proc.poll() is not None

    def stop_monitor(self):
        self.monitor = None

    def _elapsed_pos(self) -> float:
        return self.start_pos + max(0.0, time.time() - self.started)

    def finalize(self, duration: float) -> tuple[int, float] | None:
        """(mode, pos): 1=دیده‌شده ✓ | 2=نیمه‌تمام ◐ | None=بسیار کوتاه (تصادفی)."""
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        pos = self._elapsed_pos()
        if time.time() - self.started < 15:      # باز و بسته‌ی سریع → احتمالاً هدفی نبوده
            return None
        if duration and pos >= duration * 0.95:
            return (1, 0.0)
        return (2, pos)


class PotPlayerProc(ElapsedProc):
    """پخش PotPlayer با ردیابی «واقعی» از پیام‌های پنجره‌اش.

    monitor موقعیت واقعی را در live_pos می‌نویسد (elapsed_based خاموش است تا
    PlayTracker سرِ آن تخمین نزند). اگر هیچ سیگنال واقعی نرسید، finalize به
    تخمینِ زمان پخش برمی‌گردد (همان منطق ElapsedProc).
    """

    elapsed_based = False

    def __init__(self, proc: subprocess.Popen, media: Path, start_pos: float):
        super().__init__(proc, media, start_pos)
        self.player_dur = 0.0     # مدت کل طبق گزارش خود پلیر (ثانیه)
        self.real_pos = 0.0       # آخرین موقعیت واقعی دریافتی (ثانیه)
        self.last_status = 2
        self.got_real = False
        self.prev_pos = 0.0       # موقعیتِ یک گام قبلی (برای لحظه‌ی تعویض قسمت)
        self.prev_dur = 0.0
        self.pending_media = None  # مسیر فایل جدید در صورت تعویض قسمت (مصرف در نخ اصلی)

    def finalize(self, duration: float) -> tuple[int, float] | None:
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        dur = self.player_dur or duration
        if self.got_real and self.real_pos > 0:
            pos = self.real_pos
            if dur and pos >= dur * 0.95:
                return (1, 0.0)
            # توقف نزدیکِ پایان هم یعنی «دیده شده» (پلیر در انتها گاهی ۰ برمی‌گرداند)
            if self.last_status == -1 and dur and pos >= dur * 0.80:
                return (1, 0.0)
            if pos >= 15:
                return (2, pos)
            return None
        # هیچ داده‌ی واقعی نرسید → تخمین زمان پخش
        return ElapsedProc.finalize(self, duration)


def _hms(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}"


def launch_potplayer(player_path: str, media, resume_pos: float) -> PotPlayerProc | None:
    """PotPlayer در نمونه‌ی مستقل (/new → پروسه قابل‌ردیابی) + رزومه با /seek.

    اگر PotPlayer خودش بعد از پایان سراغ قسمت بعدیِ پوشه برود، مانیتور از روی
    تایتل‌بار می‌فهمد و «pending_media» را ست می‌کند تا ردیاب مرکزی (نخ اصلی)
    وضعیت قسمت قبلی را ✅/◐ ثبت کند و ردیابی را روی قسمت جدید ادامه دهد.
    """
    media = Path(media)
    try:
        cmd = [player_path, "/new"]
        if resume_pos and resume_pos > 5:
            cmd.append(f"/seek={_hms(resume_pos)}")
        cmd.append(str(media))
        proc = subprocess.Popen(cmd, **_hidden_popen_kwargs())
        wp = PotPlayerProc(proc, media, resume_pos)
        candidates = _video_candidates(media) if media.is_file() else []
        _dbg(T("لانچ PotPlayer: {0} ← {1} (رزومه {2}s · {3} نامزد پلی‌لیست)").format(Path(player_path).name, safe_ref(media), int(resume_pos), len(candidates)))

        def _on_pos(p, _wp=wp):
            _wp.prev_pos, _wp.real_pos = _wp.real_pos, p
            _wp.live_pos = p
            _wp.got_real = True

        def _on_dur(d, _wp=wp):
            if d > 0 and abs(d - _wp.player_dur) > 1:
                _wp.prev_dur = _wp.player_dur
            _wp.player_dur = max(_wp.player_dur, d)

        wp.monitor = PotPlayerMonitor(
            proc,
            on_pos=_on_pos,
            on_dur=_on_dur,
            on_status=lambda s: setattr(wp, "last_status", s),
            on_file=(lambda np, _wp=wp: setattr(_wp, "pending_media", np))
                    if candidates else None,
            candidates=candidates,
            current_media=str(media),
        )
        wp.monitor.start()
        return wp
    except Exception as _exc_dbg:
        _dbg(f"launch_potplayer failed: {type(_exc_dbg).__name__}")
        return None


def launch_vlc(vlc_path: str, media, resume_pos: float) -> ElapsedProc | None:
    """VLC با رزومه‌ی --start-time و ردیابی تخمینی (VLC موقعیت بیرون نمی‌دهد)."""
    media = Path(media)
    try:
        cmd = [vlc_path, str(media)]
        if resume_pos and resume_pos > 5:
            cmd.insert(1, f"--start-time={int(resume_pos)}")
        proc = subprocess.Popen(cmd, **_hidden_popen_kwargs())
        return ElapsedProc(proc, media, resume_pos)
    except Exception as _exc_dbg:
        _dbg(f"launch_vlc failed: {type(_exc_dbg).__name__}")
        return None


def launch_elapsed(player_path: str, media, resume_pos: float) -> ElapsedProc | None:
    """باز کردن هر پخش‌کننده‌ی دلخواه دیگر با ردیابی تخمینی (بدون رزومه‌ی جانبی)."""
    media = Path(media)
    try:
        proc = subprocess.Popen([player_path, str(media)], **_hidden_popen_kwargs())
        return ElapsedProc(proc, media, resume_pos)
    except Exception as _exc_dbg:
        _dbg(f"launch_elapsed failed: {type(_exc_dbg).__name__}")
        return None


# ------------------------------------------------------------------ ردیاب مرکزی پخش‌ها

class PlayEntry:
    """یک پخش در حال انجام + داده‌ی پایدارش (مستقل از صفحه‌ای که شروعش کرد)."""

    __slots__ = ("proc", "item", "rel", "duration", "kind")

    def __init__(self, proc, item, rel: str, duration: float, kind: str):
        self.proc = proc
        self.item = item
        self.rel = rel
        self.duration = float(duration or 0)
        self.kind = kind


class PlayTracker:
    """نگه‌داشتن همه‌ی پخش‌های جاری در یک جا (به‌جای صفحه‌ی جزئیات).

    فایده‌ی حیاتی: اگر کاربر وسط تماشا صفحه را عوض کند/برنامه را ببندد،
    نتیجه‌ی تماشا گم نمی‌شود. UI فقط poll() را با تایمر صدا می‌زند و با
    on_update از تغییرات باخبر می‌شود:

        on_update(entry, "live", (2, pos))    — ذخیره‌ی زنده‌ی نیمه‌تمام
        on_update(entry, "final", (m, pos))   — نتیجه‌ی نهایی (1 ✓ / 2 ◐)
        on_update(entry, "switch", "مسیر")    — پلیر رفت سراغ قسمت بعدی

    قواعد حفاظتی:
    - هیچ‌وقت وضعیت «✓ دیده‌شده»‌ی قبلی با نتیجه‌ی «◐ نیمه‌تمام» پایین نمی‌آید.
    - اگر PotPlayer خودش قسمت عوض کند، قسمت قبلی با آخرین موقعیتِ دیده‌شده‌اش
      ثبت می‌شود (≥۹۰٪ ⇐ ✓ چون تعویض یعنی رسیده به ته/رد شده) و ردیابی روی
      قسمت جدید ادامه می‌یابد — نتیجه هرگز به فایل اشتباه نسبت داده نمی‌شود.
    """

    LIVE_MIN = 8.0        # زیر این موقعیت، لایو ذخیره نشود
    LIVE_DELTA = 8.0      # حداقل تغییر برای ذخیره‌ی لایو‌ی بعدی
    SWITCH_DONE_RATIO = 0.90   # تعویض قسمت با موقعیت ≥۹۰٪ یعنی «دیده‌شده»

    def __init__(self, store, rel_resolver=None):
        self.store = store
        self.entries: list[PlayEntry] = []
        self.on_update = None          # callable(entry, kind, result)
        # (item, مسیر فایل) → rel — از main تزریق می‌شود تا به UI وابسته نباشیم
        self._rel = rel_resolver or (lambda item, f: Path(f).name)

    # ---- مدیریت لیست ----
    def has_media(self, media) -> bool:
        p = str(Path(media))
        for e in self.entries:
            if str(e.proc.media) == p and not e.proc.done():
                return True
        return False

    def add(self, proc, item, rel: str, duration: float, kind: str) -> PlayEntry:
        e = PlayEntry(proc, item, rel, duration, kind)
        self.entries.append(e)
        _dbg(T("ردیابی شروع شد [{0}]: {1}").format(kind, safe_ref(getattr(proc.media, 'name', rel))))
        return e

    def _emit(self, entry, kind, result):
        cb = self.on_update
        if cb:
            try:
                cb(entry, kind, result)
            except Exception as _exc_dbg:
                _dbg(f"_emit failed: {type(_exc_dbg).__name__}")
                pass

    def _write_final(self, entry, mode: int, pos: float) -> bool:
        """نوشتن نتیجه‌ی نهایی با گارد عدم‌پایین‌آمدن از «دیده‌شده‌ی قبلی»."""
        if mode != 1 and self.store.watch_get(entry.item, entry.rel)[0] == 1:
            return False
        try:
            self.store.watch_set(entry.item, entry.rel, mode, pos)
            return True
        except Exception as _exc_dbg:
            _dbg(f"_write_final failed: {type(_exc_dbg).__name__}")
            return False

    # ---- تعویض قسمت حین یک نشست پخش (PotPlayer پلی‌لیست خودکار) ----
    def switch_media(self, entry: PlayEntry, new_media: str):
        """پلیر از فایل جاری رفت روی فایل دیگر — قسمت قبلی را نتیجه بگیر و ادامه بده.

        این متد فقط در نخ اصلی (داخل poll) اجرا می‌شود؛ نخ مانیتور فقط
        pending_media را ست می‌کند.
        """
        wp = entry.proc
        if str(wp.media) == str(new_media):
            return
        dur = (getattr(wp, "prev_dur", 0.0) or entry.duration
               or getattr(wp, "player_dur", 0.0) or 0.0)
        # prev_pos/real_pos مخصوص PotPlayer است؛ برای mpv آخرین live_pos مرجع است
        pos = (getattr(wp, "prev_pos", 0.0) or getattr(wp, "real_pos", 0.0)
               or getattr(wp, "live_pos", 0.0) or 0.0)
        res = None
        if dur and pos >= dur * self.SWITCH_DONE_RATIO:
            res = (1, 0.0)
        elif getattr(wp, "last_status", 2) == -1 and dur and pos >= dur * 0.80:
            res = (1, 0.0)
        elif pos >= 15:
            res = (2, pos)
        _dbg(T("تعویض قسمت: {0} → {1} (pos={2}/{3} → {4})").format(safe_ref(wp.media), safe_ref(new_media), int(pos), int(dur), res))
        if res is not None and self._write_final(entry, res[0], res[1]):
            self._emit(entry, "final", res)   # entry هنوز rel قدیمی را دارد ✓

        # سوئیچ به قسمت جدید
        entry.rel = self._rel(entry.item, new_media)
        entry.duration = 0.0                      # player_dur جدید زود می‌رسد
        wp.media = Path(new_media)
        for attr, val in (("real_pos", 0.0), ("prev_pos", 0.0), ("live_pos", 0.0),
                          ("last_saved_pos", 0.0), ("started", time.time())):
            try:
                setattr(wp, attr, val)
            except Exception as _exc_dbg:
                _dbg(f"switch_media failed: {type(_exc_dbg).__name__}")
                pass
        self._emit(entry, "switch", new_media)

    # ---- چرخه‌ی نظارت ----
    def poll(self):
        """یک دور بررسی — توسط تایمر UI هر ~۱.۵ ثانیه صدا زده می‌شود."""
        alive: list[PlayEntry] = []
        for entry in self.entries:
            wp = entry.proc
            # ۰) تعویض قسمت (از نخ مانیتور آمده) — قبل از هر پردازش دیگر
            pm = getattr(wp, "pending_media", None)
            if pm:
                wp.pending_media = None
                try:
                    self.switch_media(entry, pm)
                except Exception as _exc_dbg:
                    _dbg(f"poll failed: {type(_exc_dbg).__name__}")
                    pass
            try:
                running = not wp.done()
            except Exception as _exc_dbg:
                _dbg(f"poll failed: {type(_exc_dbg).__name__}")
                running = False
            if running:
                # VLC/سفارشی: رشد موقعیت تخمینی (برای PotPlayer/mpv مانیتور می‌نویسد)
                if getattr(wp, "elapsed_based", False):
                    try:
                        wp.live_pos = wp._elapsed_pos()
                    except Exception as _exc_dbg:
                        _dbg(f"poll failed: {type(_exc_dbg).__name__}")
                        pass
                # ذخیره‌ی زنده‌ی موقعیت (حتی اگر برنامه وسط تماشا بسته شود گم نشود)
                live = float(getattr(wp, "live_pos", 0.0) or 0.0)
                if live > self.LIVE_MIN and abs(live - wp.last_saved_pos) >= self.LIVE_DELTA:
                    wp.last_saved_pos = live
                    if self._write_final(entry, 2, live):
                        self._emit(entry, "live", (2, live))
                alive.append(entry)
                continue
            # ---- پایان پخش: نتیجه‌گیری نهایی ----
            try:
                dur = entry.duration or 0
                if not dur and getattr(wp, "player_dur", 0):
                    dur = wp.player_dur
                res = wp.finalize(dur)
            except Exception as _exc_dbg:
                _dbg(f"poll failed: {type(_exc_dbg).__name__}")
                res = None
            if res is not None:
                mode, pos = res
                _dbg(T("نتیجه‌ی نهایی [{0}] {1}: mode={2} pos={3}").format(entry.kind, safe_ref(entry.rel), mode, int(pos)))
                if self._write_final(entry, mode, pos):
                    self._emit(entry, "final", res)
            else:
                _dbg(T("پایان بدون ثبت [{0}] {1} (پخش خیلی کوتاه/تصادفی)").format(entry.kind, safe_ref(entry.rel)))
        self.entries = alive
        return bool(self.entries)

    def shutdown(self):
        """بستن برنامه: موقعیت فعلیِ هر پخشِ هنوز-باز «نیمه‌تمام» ذخیره می‌شود.

        هیچ پخشی «دیده‌شده» علامت نمی‌خورد (برنامه نمی‌داند کاربر تا کجا رسیده؛
        برای mpv/PotPlayer موقعیت زنده به‌روز است و همین نجات‌دهنده است).
        """
        for entry in self.entries:
            wp = entry.proc
            try:
                wp.stop_monitor()
                live = float(getattr(wp, "live_pos", 0.0) or 0.0)
                if not live and getattr(wp, "elapsed_based", False):
                    live = wp._elapsed_pos()
                if live > 20:
                    mode0, _ = self.store.watch_get(entry.item, entry.rel)
                    if mode0 != 1:
                        self.store.watch_set(entry.item, entry.rel, 2, live)
            except Exception as _exc_dbg:
                _dbg(f"shutdown failed: {type(_exc_dbg).__name__}")
                pass
        self.entries = []
