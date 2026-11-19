# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Watch tracking for common players.

How it works per player (automatic, no guessing):

- **mpv** (best):
    mpv --save-position-on-quit=yes --watch-later-directory=<cache>/watch_later
        --input-ipc-server=<pipe/socket> [--start=X] file
  Live tracking: a built-in monitor reads time-pos from the IPC every 1.5 s
  and the app saves it right away — even if the app closes mid-watch, nothing
  is lost. On close: no state file → mpv cleaned it up after playing to the
  end → "watched ✓"; otherwise start=X from the state file → "in progress ◐"
  (≥95% → ✓). Resume with --start=X.

- **PotPlayer** (real tracking, no extra install/setup):
  PotPlayer has a window-message API; every 1.5 s we SendMessage its window
  (the very instance we launched — matched by PID) and ask for:
      WM_USER+0x5004 → current position (ms)
      WM_USER+0x5002 → total duration (ms)
      WM_USER+0x5006 → status (stopped/paused/playing)
  So the "real" position is saved (seeks are reflected) and on close:
      ≥95% watched → ✓ | less → ◐ with the same position.
  The file is opened with /new in an isolated instance (so the process stays
  trackable) and resumes with /seek=hh:mm:ss. If the window is not found →
  silent fallback to elapsed-time estimation.

- **VLC**: opened with --start-time=X (resume) + estimated tracking (playing
  time elapsed; VLC does not expose its position — linear watching is
  assumed). For full accuracy, mpv or PotPlayer is better.

- Custom player (custom exe): estimated tracking (no side-channel resume).
- Windows default: the process is not reachable → no tracking (with a clear
  warning).

Failed paths always silently fall back to the next-lower mode and the "note"
key says why.
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

# ------------------------------------------------------------------ playback-tracker debug log
# Key playback-tracker events (player selection, finding the PotPlayer window,
# episode switches...) are written to a lightweight log file so that if
# something fails on the user's machine, the reason is visible without
# reproducing the problem. No noticeable performance impact.

_DEBUG_LOG: Path | None = None
_DEBUG_LOCK = threading.Lock()


def set_debug_log(path):
    """Enable/disable the tracking log — main.py sets it next to the app."""
    global _DEBUG_LOG
    _DEBUG_LOG = Path(path) if path else None


def dbg_log(msg: str):
    """Write one line to the debug log — the public name of _dbg for other modules.

    Privacy (public release): paths are redacted into anonymous log entries —
    absolute file paths never land in the log (via _redact). The backup/store/
    filethumbs/ui modules use it to record silent failures (except: pass) so
    troubleshooting is possible without leaking paths."""
    _dbg(msg)


# ------------------------------------------------------------------ anonymous logging
def safe_ref(p) -> str:
    """A log-safe reference: extension + a short fingerprint (8 hex chars of
    the sha1 of the normalized path) — not a single character of the file
    name/path. The rule: "write the log so it shows no path or file name
    so I can send the whole log in". The same file always gets the same code,
    so log lines stay groupable."""
    try:
        s = os.path.normcase(str(p)).lower()   # Windows paths are case-insensitive
        dig = hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:8]
        suf = Path(s).suffix.lower()
        return f"{suf or '[dir]'}#{dig}"
    except Exception:
        return ".#?"


def _hash_str(s: str) -> str:
    """A short hash for free text (like a player window title carrying a file name)."""
    try:
        return "#" + hashlib.sha1(str(s).encode("utf-8", "replace")).hexdigest()[:8]
    except Exception:
        return "#?"


def _dbg_pot_window(pid: int, title: str) -> None:
    """Log "player window found" — the title is never logged raw.

    The PotPlayer window title carries the episode/media name, and safe_ref's
    docstring explicitly says file names must not go into the log; _redact
    only catches absolute/UNC paths, not free text — so the title is hashed
    right here (the same pattern as _hash_str)."""
    _dbg(T("potplayer: پنجره یافت شد (pid={0}, title={1})").format(
        pid, _hash_str(title)))


# Backstop: even if a call site forgets, no absolute path ever reaches the log.
# Three path families are recognized and replaced with "ext#hash":
#   · Windows drive paths  (C:\x\y  /  C:/x/y)   — components may contain spaces
#     ("C:\Program Files") but never a ":" (so a "movie.mp4: Access denied"
#     tail survives) and never a separator-crossing.
#   · UNC paths            (\\server\share\...)
#   · POSIX absolute paths (/home/alice/...) — segments stop at whitespace,
#     as POSIX paths in log lines are space-separated ( they used to
#     be logged in the clear).
# The matched span is post-processed in _rep: a trailing " (note)" glued to a
# space-containing final component ("... movie.mp4 (resume 120s)") is trimmed
# off when the remainder clearly is not part of the path (no slash, no dot) —
# previously the greedy class swallowed the whole rest of the line and the
# log lost its annotations.
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/](?:[^\"'\r\n<>`:/\\]+[\\/])*[^\"'\r\n<>`:/\\]*"
    r"|\\\\(?:[^\"'\r\n<>`:/\\]+[\\/])+[^\"'\r\n<>`:/\\]*"
    r"|(?<![\w:])/(?:[^/\s:\"'<>`]+/)*[^/\s:\"'<>`]+)")


def _redact(msg: str) -> str:
    def _trim_point(s: str) -> int:
        """Index where a glued non-path annotation starts, or len(s).

        '...\\my movie.mp4 (resume 120s)' → the path plus ' (resume 120s)'.
        Structured trailers (" (" / " [") are tried first; then a plain
        space-trailer ('...\\x.png ok'). The remainder must clearly not be
        part of the path (no slash, no dot, no bracket) — otherwise it is
        genuinely part of the name ('Film (2023)\\part 1.mkv') and the whole
        span stays redacted."""
        n = len(s)
        # 1) structured trailers — " (note)" / " [note]"; parens/brackets in
        #    the tail are expected here, only a slash/dot means the "note" is
        #    really part of the path ("Film (2023)\\part 1.mkv")
        for marker in (" (", " ["):
            i = s.rfind(marker)
            while i > 0:
                tail = s[i:]
                if not any(ch in tail for ch in ("\\", "/", ".")):
                    return i
                i = s.rfind(marker, 0, i)
        # 2) plain trailing words ("...\\x.png ok") — any bracket/paren in the
        #    tail keeps the whole span (those were handled above)
        i = s.rfind(" ")
        while i > 0:
            tail = s[i:]
            if not any(ch in tail for ch in ("\\", "/", ".", "(", "[")):
                return i
            i = s.rfind(" ", 0, i)
        return n

    def _rep(m):
        s = m.group(0)
        i = _trim_point(s)
        head, tail = s[:i], s[i:]
        base = head.replace("\\", "/").rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[1].lower() if "." in base else ""
        suf = f".{ext}" if re.fullmatch(r"[a-z0-9]{1,6}", ext or "") else "[dir]"
        dig = hashlib.sha1(os.path.normcase(head).lower().encode("utf-8", "replace")).hexdigest()[:8]
        return f"{suf}#{dig}{tail}"
    try:
        return _PATH_RE.sub(_rep, msg)
    except Exception:
        return msg


def _dbg(msg: str):
    """One debug-log line (error-safe; the file rotates above ~256KB).
    Every line automatically passes through _redact — no file path lands in the log."""
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

# ------------------------------------------------------------------ player discovery


def _which(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _win_reg_app(exe_name: str) -> str | None:
    """Read the install path from the registry (Windows only, fully guarded)."""
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
    """Resolve from the user's settings: {"mode": auto|mpv|vlc|potplayer|default|custom, "path": str}.

    Returned kind:
      mpv       → full live (IPC) tracking, precise + resume from position
      potplayer → real live tracking (window messages) + resume via /seek
      vlc       → resume via --start-time + estimated tracking (play time)
      elapsed   → any other custom player — estimated tracking (no resume)
      default   → the system default player (no tracking — process unreachable)

    The result is cached so typing in settings or back-to-back plays does not
    repeat the system-wide search. The "note" key is filled when the user's
    choice was not found and something else was substituted — the UI shows it
    transparently.
    """
    cfg = player_cfg or {}
    mode = (cfg.get("mode") or "auto").strip()
    path = (cfg.get("path") or "").strip()
    key = (mode, path)
    if not _fresh and key in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[key]

    result = _resolve_player_uncached(mode, path)
    # keep the cache bounded (the possible modes are few)
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
    # auto: richer tracking first — PotPlayer (real position + "next episode"
    # follow-up), then mpv (precise IPC), then VLC (estimated). Per user
    # request, PotPlayer has priority.
    for finder, kind in ((find_potplayer, "potplayer"), (find_mpv, "mpv"),
                         (find_vlc, "vlc")):
        p = finder()
        if p:
            return {"kind": kind, "path": p}
    return {"kind": "default", "path": None}


# ------------------------------------------------------------------ mpv paths and state

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
    """Read start= from the mpv state file; the value is seconds."""
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


# ------------------------------------------------------------------ mpv live monitor

class MpvIpcMonitor(threading.Thread):
    """Periodically poll time-pos and path from mpv over JSON IPC.

    time-pos → live position saving;
    path → detect an episode switch (mpv/user moving on to the next file) so
    tracking continues on the new episode instead of recording only the first
    episode of a "smart play".
    """

    INTERVAL = 1.5          # seconds between position polls
    CONNECT_TIMEOUT = 8.0   # max wait for the pipe/socket to come up

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
        """Process one JSON IPC message (split out — directly testable)."""
        if not isinstance(msg, dict) or msg.get("error") != "success":
            return
        rid = msg.get("request_id")
        data = msg.get("data")
        try:
            if rid == 1 and isinstance(data, (int, float)) and self.on_pos:
                self.on_pos(float(data))
            elif rid == 2 and isinstance(data, str):
                # the first path report is the baseline; later changes mean a file switch
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
                if sys.platform == "win32":           # pipe name: \\.\pipe\...
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
                    # send both requests in one go (request_id tells the replies apart)
                    payload = (
                        b'{"command": ["get_property", "time-pos"], "request_id": 1}\n'
                        b'{"command": ["get_property", "path"], "request_id": 2}\n'
)
                    conn.write(payload)
                    conn.flush()
                except OSError:
                    break
                # read both replies (extra lines = unsolicited events)
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


# ------------------------------------------------------------------ API PotPlayer window API
# (sources: unofficial AHK Library docs — WM_USER=0x0400; commands 0x5000..0x5010)

_POT_WM_USER = 0x0400
_POT_GET_TOTAL_TIME = 0x5002     # milliseconds
_POT_GET_CURRENT_TIME = 0x5004   # milliseconds
_POT_GET_PLAY_STATUS = 0x5006    # -1 stopped | 1 paused | 2 playing
_SMTO_ABORTIFHUNG = 0x0002

_pot_u32 = None        # user32 (ctypes) — lazy
_pot_enum_proc_type = None


def _pot_user32():
    """ctypes user32 — Windows only; None on other platforms."""
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
    """All visible windows belonging to one PID (no pywin32 dependency)."""
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
    """The main PotPlayer window of this PID — exact classes first, then any PotPlayer* class."""
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
    """SendMessageTimeout(WM_USER, command) → the return value (uint32) or None."""
    import ctypes
    from ctypes import wintypes
    u32 = _pot_user32()
    if u32 is None or not hwnd:
        return None
    # Win64 safety : the last parameter of SendMessageTimeoutW is
    # PDWORD_PTR — pointer-sized (8 bytes on x64). A 4-byte DWORD let the
    # API write 4 bytes past it on the stack, every poll cycle. c_size_t
    # matches the pointer width on both 32- and 64-bit Windows.
    result = ctypes.c_size_t(0)
    u32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u32.SendMessageTimeoutW.restype = ctypes.c_longlong   # LRESULT (pointer-sized)
    ok = u32.SendMessageTimeoutW(
        wintypes.HWND(hwnd), _POT_WM_USER, wintypes.WPARAM(command),
        wintypes.LPARAM(0), _SMTO_ABORTIFHUNG, timeout_ms,
        ctypes.byref(result),
)
    if not ok:
        return None
    return int(result.value) & 0xFFFFFFFF                # the WM_USER result is 32-bit


class PotPlayerMonitor(threading.Thread):
    """Real live tracking of PotPlayer via window messages (~every 1.5 s).

    on_pos(seconds) — the latest real position (seeks are reflected)
    on_dur(seconds) — total duration reported by the player (if > 0)
    on_status(k)    — -1 stopped | 1 paused | 2 playing
    on_file(path)   — PotPlayer moved on to another file (auto-play of the
                      next episode, or Next); detected from the title bar.
                      Invoked on the *monitor thread* — the consumer must only
                      flip an atomic attribute, not do heavy work or saving.
    If the window/messages stop working, the thread ends silently (the
    estimated fallback stays active).
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
        self._unmatched = 0     # count of unmatched titles (for rate-limited logging)

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
        if _pot_user32() is None:          # non-Windows — nothing can be done
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
                if fails >= 4:             # window gone/recreated? find it again
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
                        self.on_status(st if st < 2**31 else st - 2**32)
                # ---- detect a file switch from the title bar (auto-play of the next episode) ----
                if self.on_file and self.candidates:
                    title_now = _pot_window_title(h)
                    m = _match_media_title(title_now, self.candidates)
                    if m and m != self._current:
                        self._current = m
                        _dbg(T("potplayer: تعویض قسمت از روی عنوانِ پنجره → {0}").format(safe_ref(m)))
                        self.on_file(m)
                    elif not m and title_now:
                        # an idle title (no file) is not logged; the rest are sampled every 30th time
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
    """The PotPlayer window's title-bar text — to detect "which file is
    playing right now".

    After an episode ends, PotPlayer automatically moves on to the next file
    in the folder; the current file name is in the title bar (by default), so
    we can detect the episode switch.
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
    """Candidates from "episodes in the same folder" for title matching:
    [(normalized stem, path)].

    Longest stem first — so similar names (episode 1 / episode 10) are told
    apart correctly.
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
    """Title bar → path of the currently playing file (if it is among the
    candidates).

    Names are normalized with fa_norm: Arabic yeh/kaf, ZWNJ, kashida and
    letter case make no difference (Persian-named files are common).
    """
    from scanner import fa_norm
    t = fa_norm((title or "").strip())
    if not t:
        return None
    for stem, full in candidates:          # first: the title starts with the stem (the default layout)
        if stem and t.startswith(stem):
            return full
    for stem, full in candidates:          # fallback: shells that glue extra text onto the title
        if len(stem) >= 6 and stem in t:
            return full
    return None


# ------------------------------------------------------------------ tracked playback: mpv

class WatchProc:
    """One playback in progress — once the process closes, finalize decides
    the outcome.

    Live attributes (updated while playing):
        live_pos        the latest position reported by the monitor (seconds)
        last_saved_pos  the latest position the app wrote to the store (seconds)
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
        self.pending_media = None   # episode switch mid-session (from the IPC monitor — consumed on the main thread)

    def done(self) -> bool:
        return self.proc.poll() is not None

    def stop_monitor(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def finalize(self, duration: float) -> tuple[int, float] | None:
        """(mode, pos): 1=watched ✓ | 2=in progress ◐ with pos | None=undetermined."""
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        pos = None
        for cand in _state_candidates(self.state_dir, self.media):
            pos = parse_state_file(cand)
            if pos is not None:
                break
        if pos is None:
            # no state file → mpv deletes the state after the file plays to the end
            if time.time() - self.started > 10:
                return (1, 0.0)
            return None
        if duration and pos >= duration * 0.95:
            return (1, 0.0)
        return (2, pos)


def _hidden_popen_kwargs() -> dict:
    """Launch the GUI player without an extra console window."""
    kw: dict = {"close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _ipc_server_name(base_cache) -> str | None:
    """A unique pipe (Windows) or socket (Unix) name for this mpv instance's IPC."""
    tag = f"mcc-mpv-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{tag}"
    try:
        p = str(Path(base_cache) / (tag + ".sock"))
        if len(p.encode()) > 100:      # the Unix-socket sun_path limit (~108 bytes)
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
                # episode switch in the same window (playlist/Next) → record for the main thread
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


# ------------------------------------------------------------------ estimated tracking (base)

class ElapsedProc:
    """Estimated playback: for players that do not expose their position
    (VLC and custom exes).

    Position ≈ start position + elapsed play time (continuous, linear
    watching is assumed):
      ≥95% of duration → "watched ✓"   less → "in progress ◐" with the
      estimated position
    live_pos grows live (driven by PlayTracker) so the progress badge stays
    current too.
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
        """(mode, pos): 1=watched ✓ | 2=in progress ◐ | None=too short (accidental)."""
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        pos = self._elapsed_pos()
        if time.time() - self.started < 15:      # quick open/close → probably not an intentional watch
            return None
        if duration and pos >= duration * 0.95:
            return (1, 0.0)
        return (2, pos)


class PotPlayerProc(ElapsedProc):
    """PotPlayer playback with "real" tracking from its window messages.

    The monitor writes the real position into live_pos (elapsed_based is off
    so PlayTracker does not estimate over it). If no real signal ever arrives,
    finalize falls back to elapsed-time estimation (the ElapsedProc logic).
    """

    elapsed_based = False

    def __init__(self, proc: subprocess.Popen, media: Path, start_pos: float):
        super().__init__(proc, media, start_pos)
        self.player_dur = 0.0     # total duration as reported by the player (seconds)
        self.real_pos = 0.0       # the latest real position received (seconds)
        self.last_status = 2
        self.got_real = False
        self.prev_pos = 0.0       # the position one step back (for the moment of an episode switch)
        self.prev_dur = 0.0
        self.pending_media = None  # new file path on an episode switch (consumed on the main thread)

    def finalize(self, duration: float) -> tuple[int, float] | None:
        self.stop_monitor()
        if self.proc.returncode not in (0, None):
            return None
        dur = self.player_dur or duration
        if self.got_real and self.real_pos > 0:
            pos = self.real_pos
            if dur and pos >= dur * 0.95:
                return (1, 0.0)
            # stopping near the end also means "watched" (the player sometimes reports 0 at the very end)
            if self.last_status == -1 and dur and pos >= dur * 0.80:
                return (1, 0.0)
            if pos >= 15:
                return (2, pos)
            return None
        # no real data arrived → elapsed-time estimation
        return ElapsedProc.finalize(self, duration)


def _hms(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}"


def launch_potplayer(player_path: str, media, resume_pos: float) -> PotPlayerProc | None:
    """PotPlayer in an isolated instance (/new → trackable process) + resume
    via /seek.

    If PotPlayer itself moves on to the next episode in the folder after one
    ends, the monitor notices from the title bar and sets pending_media so
    the central tracker (main thread) records the previous episode's ✅/◐
    outcome and continues tracking on the new episode.
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
    """VLC with --start-time resume and estimated tracking (VLC does not expose its position)."""
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
    """Open any other custom player with estimated tracking (no side-channel resume)."""
    media = Path(media)
    try:
        proc = subprocess.Popen([player_path, str(media)], **_hidden_popen_kwargs())
        return ElapsedProc(proc, media, resume_pos)
    except Exception as _exc_dbg:
        _dbg(f"launch_elapsed failed: {type(_exc_dbg).__name__}")
        return None


# ------------------------------------------------------------------ central playback tracker

class PlayEntry:
    """One playback in progress + its durable data (independent of the page that started it)."""

    __slots__ = ("proc", "item", "rel", "duration", "kind")

    def __init__(self, proc, item, rel: str, duration: float, kind: str):
        self.proc = proc
        self.item = item
        self.rel = rel
        self.duration = float(duration or 0)
        self.kind = kind


class PlayTracker:
    """Keep every current playback in one place (instead of the details page).

    Critical benefit: if the user switches pages mid-watch or closes the app,
    the watch result is not lost. The UI only calls poll() on a timer and
    learns of changes via on_update:

        on_update(entry, "live", (2, pos))    — live save of an in-progress watch
        on_update(entry, "final", (m, pos))   — the final result (1 ✓ / 2 ◐)
        on_update(entry, "switch", "path")    — the player moved on to the next episode

    Protective rules:
    - a previous "✓ watched" status is never downgraded to an "◐ in progress"
      result.
    - if PotPlayer switches episodes itself, the previous episode is recorded
      with the last position it was seen at (≥90% → ✓, because a switch means
      the end was reached or skipped) and tracking continues on the new
      episode — the result is never attributed to the wrong file.
    """

    LIVE_MIN = 8.0        # below this position, do not save live
    LIVE_DELTA = 8.0      # minimum change before the next live save
    SWITCH_DONE_RATIO = 0.90   # an episode switch at ≥90% position means "watched"

    def __init__(self, store, rel_resolver=None):
        self.store = store
        self.entries: list[PlayEntry] = []
        self.on_update = None          # callable(entry, kind, result)
        # (item, file path) → rel — injected from main so we do not depend on the UI
        self._rel = rel_resolver or (lambda item, f: Path(f).name)

    # ---- list management ----
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
        """Write the final result with a guard against downgrading a previous "watched"."""
        if mode != 1 and self.store.watch_get(entry.item, entry.rel)[0] == 1:
            return False
        try:
            self.store.watch_set(entry.item, entry.rel, mode, pos)
            return True
        except Exception as _exc_dbg:
            _dbg(f"_write_final failed: {type(_exc_dbg).__name__}")
            return False

    # ---- episode switch during a playback session (PotPlayer auto-playlist) ----
    def switch_media(self, entry: PlayEntry, new_media: str):
        """The player moved from the current file to another one — settle the
        previous episode's result and continue.

        This method runs only on the main thread (inside poll); the monitor
        thread only sets pending_media.
        """
        wp = entry.proc
        if str(wp.media) == str(new_media):
            return
        dur = (getattr(wp, "prev_dur", 0.0) or entry.duration
               or getattr(wp, "player_dur", 0.0) or 0.0)
        # prev_pos/real_pos are PotPlayer-specific; for mpv the latest live_pos is the reference
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
            self._emit(entry, "final", res)   # entry still holds the old rel here ✓

        # switch to the new episode
        entry.rel = self._rel(entry.item, new_media)
        entry.duration = 0.0                      # the new player_dur arrives soon
        wp.media = Path(new_media)
        # player_dur/prev_dur/got_real must reset too : with the
        # previous episode's duration left in player_dur, the next (shorter)
        # episode could never reach its own 90%/80% threshold — it was
        # measured against the old, longer duration and never marked watched.
        for attr, val in (("real_pos", 0.0), ("prev_pos", 0.0), ("live_pos", 0.0),
                          ("last_saved_pos", 0.0), ("player_dur", 0.0),
                          ("prev_dur", 0.0), ("got_real", False),
                          ("started", time.time())):
            try:
                setattr(wp, attr, val)
            except Exception as _exc_dbg:
                _dbg(f"switch_media failed: {type(_exc_dbg).__name__}")
                pass
        self._emit(entry, "switch", new_media)

    # ---- monitoring cycle ----
    def poll(self):
        """One pass — called by the UI timer every ~1.5 s."""
        alive: list[PlayEntry] = []
        for entry in self.entries:
            wp = entry.proc
            # 0) episode switch (arrived from the monitor thread) — before anything else
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
                # VLC/custom: grow the estimated position (for PotPlayer/mpv the monitor writes it)
                if getattr(wp, "elapsed_based", False):
                    try:
                        wp.live_pos = wp._elapsed_pos()
                    except Exception as _exc_dbg:
                        _dbg(f"poll failed: {type(_exc_dbg).__name__}")
                        pass
                # live position saving (nothing is lost even if the app closes mid-watch)
                live = float(getattr(wp, "live_pos", 0.0) or 0.0)
                if live > self.LIVE_MIN and abs(live - wp.last_saved_pos) >= self.LIVE_DELTA:
                    wp.last_saved_pos = live
                    if self._write_final(entry, 2, live):
                        self._emit(entry, "live", (2, live))
                alive.append(entry)
                continue
            # ---- playback ended: final verdict ----
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
        """App shutdown: the current position of every still-open playback is
        saved as "in progress".

        No playback is ever marked "watched" (the app cannot know how far the
        user got; for mpv/PotPlayer the live position is up to date — that is
        what saves it).
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
