# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""بندانگشتی فایل‌ها — زنجیره‌ی اولویت:

1) فایل‌های تصویری → خود تصویر
2) ویدیوها → بندانگشتی Shell ویندوز (همان که اکسپلورر نشان می‌دهد)
3) ویدیوها → ffmpeg (تک‌شاتِ فریمِ میانیِ ویدیو + حداکثر یک بازخوانیِ فریمِ سیاه)
4) سایر فایل‌ها → کارت گرادیانی نوع‌فایل (مثل MKV / PDF / ZIP)

دو روش استفاده:
- get(path, px): همگام (برای پس‌زمینه)
- peek(path, px) + card(path, px) + ThumbJob: الگوی ناهمزمان — اول کارت نوع
  فایل نمایش داده می‌شود و تامبنیل واقعی در پس‌زمینه آماده و جایگزین می‌شود.

همه چیز به‌صورت اتمیک کش می‌شود.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from imgcache import (apply_exif_transpose, cache_file, read_cache_pil,
                      replace_with_retry, save_cache_image)
from portable import bundled_binary, resource_path

from scanner import (
    ARCHIVE_EXTS, AUDIO_EXTS, CODE_EXTS, DOC_EXTS, EXE_EXTS,
    IMAGE_EXTS_SET, VIDEO_EXTS,
)
from winthumbs import get_shell_thumbnail, shell_thumbnail_stats
from media_info import hidden_kwargs, fast_duration
from watchtime import dbg_log

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
    from PySide6.QtGui import QImage, QPixmap
    _QT = True
except Exception as _exc_dbg:  # pragma: no cover — خارج از محیط اپ
    dbg_log(f"filethumbs.<module> failed: {type(_exc_dbg).__name__}")
    _QT = False

GROUP_STYLE = {
    "video":   ("#5b7cf5", "#33247e", "VIDEO"),
    "audio":   ("#2dd4bf", "#0f766e", "AUDIO"),
    "image":   ("#38bdf8", "#1e3a8a", "IMAGE"),
    "archive": ("#fbbf24", "#92400e", "ARCHIVE"),
    "doc":     ("#fb7185", "#9f1239", "DOC"),
    "sub":     ("#e879a9", "#9d2457", "SUB"),          # زیرنویس‌ها استایل اختصاصی
    "code":    ("#a3e635", "#3f6212", "CODE"),
    "exe":     ("#94a3b8", "#334155", "APP"),
    "other":   ("#8b98ad", "#25334a", "FILE"),
}

# زیرنویس‌ها — ذاتاً تامبنیلی ندارند و کاربر خواهانِ کارتِ زیبای اختصاصی است
_SUBTITLE_EXTS = frozenset({
    "srt", "ass", "ssa", "vtt", "sub", "idx", "stl", "smi", "sbv", "usf",
})

# استایل کارت‌ها به «آیکون واقعیِ دانلودشده» ارتقا یافت (پک متن‌باز Material
# Icon Theme — MIT، داخل assets/icons/) → برچسب کش «|c3» و زیرنویس‌ها «sub2»
# تا کارت‌های نسلِ قبل (گلیفِ رسم‌شده) فقط یک‌بار ارزان بازسازی شوند؛ تامبنیل‌های
# واقعیِ کش‌شده (تصویر/ویدیو/pdf/آرشیو) با همان کلید قبلی دست‌نخورده می‌مانند.
# گروه doc فقط برای غیرِ pdf (pdf تامبنیل واقعی دارد).
_CARD_STYLE_GROUPS = frozenset({"other", "code", "exe", "audio", "doc"})

# درخواست کاربر: «آیکون از اینترنت دانلود کن — پکی که به تم برنامه بخوره» —
# نگاشت پسوند/گروه → نام فایل PNGِ دانلودشده در assets/icons (۳۰ آیکونِ پک
# متن‌باز Material Icon Theme، رستر ۵۱۲px روی ابرِ سفید — همان سبکی که کاربر
# از سایت‌های flaticon/vscode-icons/catppuccin خواست؛ آن سایت‌ها کپچا/ضدربات
# دارند و قابل اسکریپ نیستند، پس از مخزنِ رسمی گیت‌هاب دریافت شد = آفلاین‌پذیر).
_EXT_ICON = {
    # کد
    ".py": "python", ".pyc": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".html": "html", ".htm": "html", ".css": "css",
    ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".rs": "rust",
    ".cpp": "cpp", ".cxx": "cpp", ".cc": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".go": "go", ".php": "php", ".rb": "ruby", ".java": "java",
    ".log": "log",
    # اجرایی / اسکریپت
    ".exe": "exe", ".msi": "exe", ".lnk": "exe",
    ".bat": "console", ".cmd": "console", ".ps1": "console", ".sh": "console",
    # سند
    ".txt": "document", ".pdf": "pdf",
    ".doc": "word", ".docx": "word",
    ".ppt": "powerpoint", ".pptx": "powerpoint",
    ".xls": "table", ".xlsx": "table", ".csv": "table",
    ".epub": "epub",
}
for _se in _SUBTITLE_EXTS:
    _EXT_ICON["." + _se] = "subtitles"

_GRP_ICON = {
    "video": "video", "audio": "audio", "image": "image",
    "archive": "zip", "sub": "subtitles", "doc": "document",
    "code": "console", "code2": "console",
    "exe": "exe", "other": "document",
}

_ICON_MEM: dict = {}                        # نام → Image (RGBA) یا None (جاافتاده)


def _card_icon_name(suf: str, grp: str) -> str | None:
    """نام آیکونِ دانلودشده برای این فایل — اول نگاشتِ دقیقِ پسوند، بعد گروه."""
    suf = suf.lower()
    if not suf.startswith("."):
        suf = "." + suf
    return _EXT_ICON.get(suf) or _GRP_ICON.get(grp)


def _load_card_icon(name: str | None) -> Image.Image | None:
    """آیکون PNG دانلودشده (assets/icons/<name>.png) → PIL RGBA؛ کش می‌شود.

    نبودِ فایل/خطا → None تا گلیفِ برداریِ داخلی (فالبک همیشگی) رسم شود —
    برنامه بدون پوشه‌ی آیکون‌ها هم باید کار کند.
    """
    if not name:
        return None
    hit = _ICON_MEM.get(name, False)
    if hit is not False:
        return hit
    try:
        p = resource_path(f"assets/icons/{name}.png")
        with Image.open(p) as src:
            im = src.convert("RGBA")
        _ICON_MEM[name] = im
        return im
    except Exception as _exc_dbg:
        dbg_log(f"_load_card_icon failed: {type(_exc_dbg).__name__}")
        _ICON_MEM[name] = None
        return None

# کش کارت‌های رسم‌شده (کلید: (گروه، پسوند، سایز)) — هزاران fایلِ هم‌پسوند یک‌بار رسم
_TC_CACHE: dict = {}

_FFMPEG_FALLBACK_SEEKS = ("30", "3")   # وقتی مدت‌زمان هیچ‌جوره خوانده نشد — دو شات ثابت
_BLACK_MEAN = 16                        # آستانه‌ی فریم تقریباً سیاه

# محدودیت‌های امنیتی/کارایی کاوش داخل آرشیو (جلوگیری از فریز روی آرشیوهای غول و بمب فشرده‌سازی)
_ARC_IMG_CAP = 80 * 1024 * 1024       # تصویر داخلی بزرگ‌تر از این رد شود
# گزارش کاربر: «سه آرشیو هم‌قسم با ویدیو، برای یکی تامبنیل نیامد» — سقف
# ۲GB برای قسمت‌های سینمایی/بلند کافی نبود — ویدیوی ۲.۲GB بی‌صدا رد می‌شد و چون
# داخل آن آرشیو جز ویدیو چیزی نبود، هیچ تامبنیلی ساخته نمی‌شد. سقف رفت روی ~۴GB
# و تایم‌اوتِ استخراج بر اساس حجم عضو سازش‌پذیر شد (پایین را در همان تابع ببین).
_ARC_VID_CAP = 4_000_000_000          # حد خارج‌کردن ویدیو از آرشیو (~۴GB؛ یک‌بار، نتیجه کش می‌شود)
_ARC_PDF_CAP = 800 * 1024 * 1024      # حد خارج‌کردن PDF از آرشیو
_ARC_LIST_CAP = 8000                  # حداکثر تعداد عضو اسکن‌شده‌ی فهرست (0.23.0: ۴هزار→۸هزار — در آرشیوهای شلوغ برخی تصاویر پشت مرز می‌ماندند)
_UNRAR_TOOL: str | None = None        # نتیجه‌ی جست‌وجوی ابزار RAR کش می‌شود ("" = پیدا نشد)
_SEVENZ_TOOL: str | None = None       # نتیجه‌ی جست‌وجوی 7-Zip کش می‌شود


def _find_unrar() -> str | None:
    """UnRAR را پیدا می‌کند — اول باینری همراه برنامه (bin/)، بعد PATH و مسیرهای
    متداول نصب ویندوز.

    خواندن محتوای RAR با rarfile انجام می‌شود ولی استخراج به ابزار خارجی نیاز دارد؛
    با نصب رایج WinRAR فایل UnRAR.exe همیشه همراهش است.
    """
    global _UNRAR_TOOL
    if _UNRAR_TOOL is not None:
        return _UNRAR_TOOL or None
    t = bundled_binary("UnRAR.exe") or bundled_binary("unrar.exe")
    if not t:
        t = shutil.which("unrar") or shutil.which("rar")
    if not t and sys.platform == "win32":
        for cand in (
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
            r"C:\Program Files\WinRAR\RAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        ):
            if os.path.isfile(cand):
                t = cand
                break
    _UNRAR_TOOL = t or ""
    return t or None


def _find_7z() -> str | None:
    """7-Zip نصب‌شده را پیدا می‌کند — اول PATH بعد مسیر نصب متداول ویندوز.

    7z.exe تقریباً همه‌ی فرمت‌های فشرده را می‌خواند (zip/rar/7z/tar/cab/…) و روی
    بیشتر سیستم‌های کاربران نصب است؛ به پکیج پایتونی هم وابسته نیست.
    """
    global _SEVENZ_TOOL
    if _SEVENZ_TOOL is not None:
        return _SEVENZ_TOOL or None
    # اولویت با 7-Zip همراهِ برنامه (bin/7za.exe) — بدون این‌که کاربر چیزی نصب کرده
    # باشد، همه‌ی فرمت‌های فشرده (7z/rar/zip/tar/…) تامبنیل می‌گیرند (درخواست کاربر).
    t = bundled_binary("7za.exe") or bundled_binary("7z.exe")
    if not t:
        t = (shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
             or shutil.which("7zz"))
    if not t and sys.platform == "win32":
        for cand in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            r"C:\Program Files\7-Zip\7za.exe",
        ):
            if os.path.isfile(cand):
                t = cand
                break
    _SEVENZ_TOOL = t or ""
    return t or None


def _parse_7z_slt(text: str) -> list:
    """خروجی `7z l -slt` → فهرست (نام، حجم) اعضا.

    نسخه‌ی 0.23.0 (ریشه‌ی واقعیِ گزارش کاربر: «بعضی فایل‌های 7z تامبنیل ندارند»):
    7-Zip ۲۵.۰۱ برای اعضای عادی دیگر فیلد `Folder` چاپ نمی‌کند و پارسر قبلی
    فقط بلوک‌های دارای آن فیلد را می‌گرفت — یعنی کل فهرست خالی برمی‌گشت و
    پروب آرشیو عملاً هرگز از مسیر CLI کار نمی‌کرد. قاعده‌ی سالم: بلوکی که فیلد
    «Size» دارد و `Folder = +` (پوشه) نیست، عضوِ فایل است. بلوکِ مشخصات خودِ
    آرشیو فقط «Physical Size» دارد، نه «Size»، پس رد می‌شود.
    """
    members = []
    path = size = folder = None
    has_size = False

    def flush():
        nonlocal path, size, folder, has_size
        if has_size and path and folder != "+" and not path.endswith("/"):
            members.append((path, size or 0))
        path = size = folder = None
        has_size = False

    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            flush()
            continue
        if " = " not in line:
            continue
        key, val = line.split(" = ", 1)
        key = key.strip()
        if key == "Path":
            flush()
            path = val
        elif key == "Size":
            has_size = True
            try:
                size = int(val.strip())
            except ValueError:
                size = 0
        elif key == "Folder":
            folder = val.strip()
    flush()
    return members[:_ARC_LIST_CAP]


def _parse_unrar_lt(text: str) -> list:
    """خروجی `UnRAR lt -c-` → فهرست (نام، حجم) اعضا."""
    members = []
    name = size = None

    def flush():
        nonlocal name, size
        if name and not name.endswith("/"):
            members.append((name, size or 0))
        name = size = None

    for line in text.replace("\r", "").split("\n"):
        if not line.strip():
            flush()
        elif line.startswith("Name: "):
            flush()
            name = line[6:].strip()
        elif line.startswith("Size: "):
            try:
                size = int(line[6:].strip())
            except ValueError:
                size = 0
    flush()
    return members[:_ARC_LIST_CAP]


def _quiet_kw(timeout: int) -> dict:
    """آرگ‌های پنهان‌کردن پنجره‌ی subprocess بدون capture (stdout خودمان مدیریت می‌شود)."""
    kw = {"timeout": timeout}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


class _CliArc:
    """بک‌اند آرشیو بر پایه‌ی ابزارِ نصب‌شده‌ی سیستم — بدون هیچ پکیج پایتونی.

    kind="7z": `7z l -slt -sccUTF-8` برای لیست، `7z e -o<tmp>` (تخت) برای استخراج.
    kind="unrar": `UnRAR lt -c-` برای لیست، `UnRAR p -inul` (روی stdout) برای استخراج.
    هر کار در یک فرایند تازه و بدون پنجره انجام می‌شود.

    list_members=False → حالت «مستقیم»: بدون لیست‌خواندن (چند ثانیه صرفه‌جویی روی
    آرشیوهای بزرگ) برای استخراج عضوی که قبلاً کش انتخابش کرده (_archive_picks).
    """

    def __init__(self, tool: str, path, kind: str, list_members: bool = True):
        self.tool = tool
        self.path = Path(path)
        self.kind = kind
        self.members: list = self._list() if list_members else []

    def close(self):
        pass                                         # چیزی باز نمی‌ماند

    def _list(self) -> list:
        if self.kind == "7z":
            out = subprocess.run(
                [self.tool, "l", "-slt", "-sccUTF-8", str(self.path)],
                **hidden_kwargs(40))
            return _parse_7z_slt((out.stdout or b"").decode("utf-8", errors="replace"))
        out = subprocess.run(
            [self.tool, "lt", "-c-", str(self.path)],
            **hidden_kwargs(40))
        return _parse_unrar_lt((out.stdout or b"").decode("utf-8", errors="replace"))

    def extract(self, name: str, dest, timeout: int = 240):
        dest = Path(dest)
        if self.kind == "7z":
            dest.parent.mkdir(parents=True, exist_ok=True)
            # «--» (0.51.0): نامِ عضوِ آرشیو بعد از این نقطه هرگز سوییچ خوانده
            # نمی‌شود — عضوی که نامش با «-» شروع شود استخراج را خراب نمی‌کند.
            # «-spd» (امنیت انتشار عمومی): تطبیقِ وایلدکارد خاموش می‌شود تا
            # نامِ عضوی که * یا ? دارد دقیق/لیترال برداشته شود، نه الگو.
            subprocess.run(
                [self.tool, "e", str(self.path), f"-o{dest.parent}", "-y",
                 "-spd", "--", name],
                **hidden_kwargs(timeout))
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            got = dest.parent / base
            if got != dest and got.exists():
                # round 29: جایگزینیِ اتمیک با تلاشِ مجددِ قفلِ گذرای ویندوز —
                # همان هلپرِ round 28 (imgcache)؛ قفلِ آنتی‌ویروس/ایندکسر روی
                # فایلِ تازه‌استخراج‌شده دیگر تامبنیلِ اولین تلاش را از بین نمی‌برد.
                replace_with_retry(got, dest)
            if not dest.exists():          # 7z گاهی نام را تغییر می‌دهد — اولین خروجی را برمی‌داریم
                cands = sorted(p for p in dest.parent.glob("*") if p.is_file())
                if cands:
                    replace_with_retry(cands[0], dest)
            return
        with open(dest, "wb") as d:        # unrar p — محتوای عضو روی stdout
            # «--» (امنیت انتشار عمومی): مثل بک‌اندِ 7z — نامِ عضوِ شروع‌شده با
            # «-» دیگر هرگز سوییچِ UnRAR خوانده نمی‌شود.
            subprocess.run(
                [self.tool, "p", "-inul", str(self.path), "--", name],
                stdout=d, stderr=subprocess.DEVNULL, check=False,
                **_quiet_kw(timeout))


def _is_tar_bundle(path) -> bool:
    """تشخیص آرشیوهای چندپسوندی tar — Path.suffix فقط آخرین پسوند را می‌دهد و
    .tar.gz را «.gz» می‌بیند (راهنما). بومیِ tarfile هر کدام را می‌خواند."""
    try:
        n = Path(path).name.lower()
    except Exception as _exc_dbg:
        dbg_log(f"_is_tar_bundle failed: {type(_exc_dbg).__name__}")
        return False
    return n.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                       ".tar.xz", ".txz", ".tar.zst", ".tzst", ".tar.lz4"))


def _arc_open(path: Path):
    """آرشیو را باز می‌کند → (handle, members, opener) یا None.

    members: [(نام داخلی، حجم غیرفشرده)] · opener(name, dest) عضو را روی دیسک می‌ریزد.
    ترتیب بک‌اندها (درخواست کاربر — «برای همه‌ی فشرده‌ها تامبنیل»):
    ۱) ZIP/EPUB/CBZ و TAR بومیِ پایتون (فوری، بدون فرایند)
    ۲) 7z.exe / UnRAR.exe خودِ سیستم (بدون نیاز به پکیج — کاربران ایرانی معمولاً
       به VPN برای pip نیاز دارند؛ پس وابستگی حیاتی نباید به pip باشد)
    ۳) پکیج‌های اختیاری rarfile / py7zr اگر نصب باشند
    هر خطایی = None (کارت نوع‌فایل می‌ماند و نتیجه‌ی منفی کش می‌شود).
    """
    suf = path.suffix.lower()
    # ۱) مسیرهای بومی
    try:
        if suf in (".zip", ".cbz", ".epub"):
            import zipfile
            zf = zipfile.ZipFile(path)
            try:
                members = [
                    (i.filename, i.file_size) for i in zf.infolist()[:_ARC_LIST_CAP]
                    if not i.is_dir()
                ]
            except Exception:
                zf.close()          # عضوِ بد/آرشیوِ خراب — هندل نشت نکند (ویندوز قفل می‌ماند)
                raise

            def opener(name, dest, timeout=None):
                with zf.open(name) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 256)
            return zf, members, opener
        # تشخیص چندپسوندی (گزارش کاربر): .tar.gz / .tgz / .tar.bz2 / .tar.xz — قبلاً
        # فقط suffix دیده می‌شد (مثلاً «.gz») و بومی باز نمی‌شد.
        _tar_like = suf == ".tar" or _is_tar_bundle(path)
        if _tar_like:
            import tarfile
            tf = tarfile.open(path, "r:*")     # r:* خودش gz/bz2/xz را تشخیص می‌دهد
            try:
                members = [
                    (m.name, m.size) for m in tf.getmembers()[:_ARC_LIST_CAP] if m.isfile()
                ]
            except Exception:
                tf.close()          # عضوِ بد/آرشیوِ خراب — هندل نشت نکند
                raise

            def opener(name, dest, timeout=None):
                with tf.extractfile(name) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 256)
            return tf, members, opener
    except Exception as _exc_dbg:
        dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
        pass                            # خراب/رمزدار → سراغ بک‌انده بعدی می‌رویم

    # ۲) ابزارهای CLI خودِ سیستم (7z همه‌ی فرمت‌ها؛ UnRAR مخصوص RAR)
    try:
        tool = _find_7z()
        if tool:
            arc = _CliArc(tool, path, "7z")
            if arc.members:
                return arc, arc.members, arc.extract
        else:
            tool = None
        if suf in (".rar", ".cbr"):
            toolu = _find_unrar()
            if toolu:
                arc = _CliArc(toolu, path, "unrar")
                if arc.members:
                    return arc, arc.members, arc.extract
    except Exception as e:
        # انتشار عمومی: شکستِ بازکردنِ آرشیو بی‌صدا نماند (بدون مسیر — safe_ref)
        try:
            from watchtime import _dbg, safe_ref
            _dbg(f"archive open (cli) failed: {safe_ref(path)}: {type(e).__name__}")
        except Exception as _exc_dbg:
            dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
            pass

    # ۳) پکیج‌های اختیاری
    try:
        if suf in (".rar", ".cbr"):
            import rarfile
            tool = _find_unrar()
            if tool:
                rarfile.UNRAR_TOOL = tool
            rf = rarfile.RarFile(path)
            try:
                members = [
                    (i.filename, i.file_size) for i in rf.infolist()[:_ARC_LIST_CAP]
                    if not i.isdir()
                ]
            except Exception:
                rf.close()          # هندل نشت نکند (B8)
                raise

            def opener(name, dest, timeout=None):
                with rf.open(name) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 256)
            return rf, members, opener
        if suf in (".7z", ".cb7"):
            import py7zr
            z = py7zr.SevenZipFile(path)
            try:
                names = z.getnames()[:_ARC_LIST_CAP]
                sizes = {}
                for info in z.list():
                    sizes[info.filename] = getattr(info, "uncompressed", 0)
            except Exception:
                z.close()           # هندل نشت نکند (B8)
                raise
            members = [(n, sizes.get(n, 0)) for n in names]
            def opener(name, dest, timeout=None):
                # py7zr فقط extract رودیسک دارد؛ در پوشه‌ی کنار کش، بعد جابه‌جا می‌شود
                sub = dest.parent / ("ax7_" + hashlib.sha1(name.encode()).hexdigest()[:8])
                sub.mkdir(exist_ok=True)
                z.extract(path=sub, targets=[name])
                got = sub / name
                if not got.exists():
                    cand = list(sub.rglob("*"))
                    got = cand[0] if cand else got
                # round 29: مثل بک‌اندِ 7z — قفلِ گذرای ویندوز (round 28) جذب شود
                replace_with_retry(got, dest)
                shutil.rmtree(sub, ignore_errors=True)
            return z, members, opener
    except Exception as _exc_dbg:
        dbg_log(f"_arc_open failed: {type(_exc_dbg).__name__}")
        return None
    return None


def _arc_direct(path: Path):
    """بازکردن مستقیم آرشیو برای استخراج «عضو کش‌شده» → (handle, opener) یا None.

    فرق با _arc_open: هیچ لیستی از اعضا خوانده نمی‌شود — روی آرشیوهای چندهزارعضویی
    (گزارش کاربر: «پوشه‌های فقط‌فشرده دیر تامبنیل می‌گیرند») هر بازدید بعدی چند
    ثانیه سریع‌تر است، چون کش «دفعه‌ی قبل کدام عضو انتخاب شد» را داریم.
    بک‌اندهای بدون-فرایند (zip/tar) مثل همیشه؛ راست‌آمد py7zr عمداً گذاشته نمی‌شود —
    در نبود 7z.exe/UntRAR مسیر کامل _arc_open خودش وظیفه‌اش را انجام می‌دهد.
    """
    suf = path.suffix.lower()
    try:
        if suf in (".zip", ".cbz", ".epub"):
            import zipfile
            zf = zipfile.ZipFile(path)
            def opener(name, dest, timeout=None):
                with zf.open(name) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 256)
            return zf, opener
        if suf == ".tar" or _is_tar_bundle(path):
            import tarfile
            tf = tarfile.open(path, "r:*")
            def opener(name, dest, timeout=None):
                with tf.extractfile(name) as s, open(dest, "wb") as d:
                    shutil.copyfileobj(s, d, 1024 * 256)
            return tf, opener
    except Exception as e:
        # انتشار عمومی: شکستِ بازکردنِ ZIP/tar بی‌صدا نماند (بدون مسیر — safe_ref)
        try:
            from watchtime import _dbg, safe_ref
            _dbg(f"archive open (native) failed: {safe_ref(path)}: {type(e).__name__}")
        except Exception as _exc_dbg:
            dbg_log(f"_arc_direct failed: {type(_exc_dbg).__name__}")
            pass
    try:
        tool = _find_7z()
        if tool:
            arc = _CliArc(tool, path, "7z", list_members=False)
            return arc, arc.extract
        if suf in (".rar", ".cbr"):
            toolu = _find_unrar()
            if toolu:
                arc = _CliArc(toolu, path, "unrar", list_members=False)
                return arc, arc.extract
    except Exception as _exc_dbg:
        dbg_log(f"_arc_direct failed: {type(_exc_dbg).__name__}")
        return None
    return None


_ARC_CAND_LIMIT = 2           # حداکثر کاندید برای تلاش پیاپی در هر آرشیو (0.27.0: ۵←۲ — see below)

# گزارش کاربر: «تو پوشه‌های دیگه هم بعضی فشرده‌ها تامبنیل نمی‌گیرن» —
# برای انتخابِ عضوِ ویدیوییِ داخل آرشیو ابرمجموعه‌ی پسوندهای کتابخانه لحاظ
# می‌شود — ffmpeg همه را می‌خواند (rm/rmvb/vob/3gp/mts/ogv/divx/...). فقط در
# همین انتخابِ عضو به‌کار می‌رود؛ منطقِ بقیه‌ی برنامه (اسکن/پخش) دست‌نخورده.
_ARC_MEMBER_VIDEO_EXTS = frozenset(
    VIDEO_EXTS | {".rm", ".rmvb", ".vob", ".3gp", ".3g2", ".mts",
                  ".ogv", ".divx", ".f4v", ".mxf", ".asf"})

# نام‌های معروفِ کاور — 0.27.0 (درخواست کاربر + هر دو راهنما: اول اسمِ معروف، بعد
# اولین تصویر): اگر آرشیو cover/folder/poster دارد همان اول امتحان شود؛ تجربه
# می‌گوید اغلب همان یک استخراجِ تنها کافی است و بقیه‌ی کاندیدها لازم نمی‌شوند.
_COVERISH = ("cover", "folder", "poster", "thumb", "front", "banner")


def _pick_arc_members(members, limit: int = _ARC_CAND_LIMIT):
    """لیست کاندیدهای تامبنیل به ترتیب ترجیح: چند تصویر ← چند ویدیو ← چند PDF.

    نسخه‌ی 0.23.0 (گزارش کاربر: «بعضی فایل‌های 7z تامبنیل ندارند با اینکه پر از
    تصویرند»): قبلاً فقط *یک* عضو از هر نوع برگزیده می‌شد؛ اگر همان یکی خراب،
    رمزدار یا استخراج‌ناپذیر بود کل آرشیو بدون تامبنیل می‌ماند — حتی با ده
    تصویرِ سالمِ بعدی. حالا مصرف‌کننده یکی‌یکی امتحان می‌کند تا اولین موفقیت.
    نسخه‌ی 0.27.0 (گزارش کاربر: «برای فایل فشرده فقط فایل‌های توشو بررسی کن به
    ترتیب الویت» + راهنما۲: «تعداد کاندیدها را کم کن — به محض اولین موفقیت
    stop»): سقفِ تلاشِ پیاپی از ۵ به ۲ رسید (بدترین حالتِ هر آرشیو از حداکثر
    ۱۵ استخراج به ۶ — و در عمل معمولاً همان ۱ استخراج) و نام‌های معروفِ کاور
    (cover/folder/poster/… خودکار اول) می‌آیند تا همان تلاشِ اول جواب بدهد.
    (زیرشاخه‌های زائد مثل __MACOSX رد؛ پسوندها بدون حساسیت به بزرگی/کوچکی حروف؛
    نام اعضا به هر زبانی می‌تواند باشد — کار فقط روی «پسوند» است.)
    """
    imgs, vids, pdfs = [], [], []
    for name, size in members:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if base.startswith(".") or name.startswith("__MACOSX"):
            continue
        suf = Path(base).suffix.lower()
        if suf in IMAGE_EXTS_SET and 256 < size <= _ARC_IMG_CAP:
            imgs.append((name, size, "image"))
        elif suf in _ARC_MEMBER_VIDEO_EXTS and 0 < size <= _ARC_VID_CAP:
            vids.append((name, size, "video"))
        elif suf == ".pdf" and 0 < size <= _ARC_PDF_CAP:
            pdfs.append((name, size, "pdf"))
    imgs.sort(key=lambda it: 0 if it[0].replace("\\", "/").rsplit("/", 1)[-1]
              .lower().startswith(_COVERISH) else 1)
    return imgs[:limit] + vids[:limit] + pdfs[:limit]


def _pick_arc_member(members):
    """نخستین کاندید (سازگاری با استفاده‌های قدیمی) — همان منطق _pick_arc_members."""
    cands = _pick_arc_members(members, 1)
    return cands[0] if cands else None


# ---------------------------------------------------------------- PDF: رندر واقعی
# تامبنیل Shell ویندوز فقط وقتی جواب می‌دهد که یک PDF-reader با «thumbnail handler»
# (مثل Acrobat) نصب باشد؛ وگرنه همیشه None می‌دهد (گزارش کاربر). فال‌بک: رندر واقعی
# صفحه‌ی اول با pypdfium2 (پکیج سبک و بدون وابستگی — run.bat سعی می‌کند نصبش کند)،
# وگرنه PyMuPDF/fitz اگر از قبل نصب باشد (AGPL-3.0/تجاری — THIRD_PARTY_NOTICES.md
# §5). هیچ‌کدام نبودند → None (کارت نوع‌فایل).
_PDF_LIB: str | None = None      # "pypdfium2" / "fitz" / "" — کش یک‌باره‌ی تشخیص


def _pdf_lib() -> str | None:
    global _PDF_LIB
    if _PDF_LIB is not None:
        return _PDF_LIB or None
    import importlib.util
    for name in ("pypdfium2", "fitz"):
        try:
            if importlib.util.find_spec(name) is not None:
                _PDF_LIB = name
                return name
        except Exception as _exc_dbg:
            dbg_log(f"_pdf_lib failed: {type(_exc_dbg).__name__}")
            continue
    _PDF_LIB = ""
    return None


# PDFium/fitz thread-safe نیستند (رندر هم‌زمان از چند نخ کرش می‌دهد) — از وقتی که
# بارگذارهای تامبنیل موازی شدند، همه‌ی رندرهای PDF پشت این قفلِ مشترک سری می‌شوند.
_PDF_LOCK = threading.Lock()


def _pdf_render_thumb(path: Path, px: int) -> Image.Image | None:
    """رندر واقعی صفحه‌ی اول PDF — فال‌بکی برای وقتی Shell ویندوز تامبنیل نمی‌دهد."""
    with _PDF_LOCK:
        return _pdf_render_thumb_inner(path, px)


def _pdf_render_thumb_inner(path: Path, px: int) -> Image.Image | None:
    lib = _pdf_lib()
    try:
        if lib == "pypdfium2":
            import pypdfium2 as pdfium
            try:
                pdf = pdfium.PdfDocument(str(path))
            except TypeError:                       # نسخه‌های خیلی قدیمی بدون close
                return None
            try:
                if len(pdf) < 1:
                    return None
                page = pdf[0]
                w, h = page.get_size()
                base = max(w, h) or 600
                im = page.render(scale=max(px, 256) / base).to_pil()
                return _cover_square(im.convert("RGB"), px)
            finally:
                try:
                    pdf.close()
                except Exception as _exc_dbg:
                    dbg_log(f"_pdf_render_thumb_inner failed: {type(_exc_dbg).__name__}")
                    pass
        elif lib == "fitz":
            import fitz
            doc = fitz.open(str(path))
            try:
                if doc.page_count < 1:
                    return None
                page = doc.load_page(0)
                rect = page.rect
                base = max(rect.width, rect.height) or 600
                zoom = max(px, 256) / base
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                return _cover_square(im, px)
            finally:
                doc.close()
    except Exception as _exc_dbg:
        dbg_log(f"_pdf_render_thumb_inner failed: {type(_exc_dbg).__name__}")
        return None
    return None


_MAGIC_OK_HEADS = (b"RIFF", b"GIF8", b"\xff\xd8\xff", b"BM")   # WebP/GIF/JPEG/BMP
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _cache_decodable(path) -> bool:
    """آیا فایلِ کش واقعاً دیکود می‌شود؟ (B1 — کشِ ناسازگار هرگز نباید
    برای همیشه placeholder بماند.)

    مسیرِ سریع (D9): فقط امضای بایت‌ها خوانده می‌شود (۱۲ بایت) — قبلاً هر
    کارتِ دارای کش یک دیکدِ کامل می‌گرفت (۰٫۶۷–۰٫۹ms × تعدادِ کارت‌ها ≈
    نیم‌ثانیه برای ۵۰۰ کارت) و PosterWorker بعداً همان فایل را دوباره دیکد
    می‌کرد. کشِ ما فقط WebP/PNG است؛ پسوندِ ناشناس/کهنه فقط در همان حالتِ
    نادر به دیکدِ کامل می‌رود (تضمینِ B1 حفظ می‌شود).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError as _exc_dbg:
        dbg_log(f"_cache_decodable failed: {type(_exc_dbg).__name__}")
        return False
    if head[:8] == _PNG_SIG:
        return True
    if head[:4] in _MAGIC_OK_HEADS:
        # RIFF فقط وقتی WebP معتبر است — RIFFِ غیرWebP (WAV/AVI بی‌مقدار)
        if head[:4] == b"RIFF" and head[8:12] != b"WEBP":
            return False
        return True
    # امضای ناشناس/قالبِ کهنه — دیکدِ کامل (مسیرِ سنگین فقط استثناست)
    try:
        if _QT:
            q = load_cache_qimage(path)
            return q is not None and not q.isNull()
        return read_cache_pil(Path(path)) is not None
    except Exception as _exc_dbg:
        dbg_log(f"_cache_decodable failed: {type(_exc_dbg).__name__}")
        return False


def poster_png_fallback(cache_get, fthumbs, src, vid, pdf, arc, px: int = 256,
                        force: bool = False):
    """زنجیره‌ی کامل پوستر عنوان با عبور خودکار از مرحله‌ی شکست‌خورده (درخواست کاربر):
    آیکون/تصویر ← تامبنیل واقعی ویدیو ← صفحه‌ی اول PDF ← محتوای فایل فشرده.

    نکته‌ی مهم: اینجا *فقط تامبنیل واقعی* قبول می‌شود (نه کارت گرادیانی نوع‌فایل) و
    اگر مثلاً ویدیو خراب باشد و تامبنیلی ندهد، زنجیره سراغ PDF و بعد فشرده می‌رود
    تا بالاخره «یک» تصویر برای پوشه ساخته شود.
    cache_get: تابع کش تصاویر (مثل ThumbnailCache.get) → Path یا None.
    force: فقط برای بازتلاش زمان‌بندی‌شده — کش منفیِ مراحل نادیده گرفته می‌شود.
    خروجی: مسیر فایلِ کشِ آماده (WebP/PNG) یا None (یعنی placeholder حروفی می‌ماند).
    """
    if src:
        png = cache_get(src)
        if png is not None and not _cache_decodable(png):
            # کشِ کهنه‌ای که دیگر دیکود نمی‌شود (فایلِ ناقص/قالبِ بی‌دیکودر):
            # حذف و یک‌بار از نو ساخته می‌شود — وگرنه بازتلاشِ بعدی دوباره
            # همان فایلِ خراب را می‌گیرد و کارت برای همیشه placeholder می‌ماند.
            try:
                Path(png).unlink(missing_ok=True)
            except OSError:
                pass
            png = cache_get(src)              # حالا miss است → بازسازی از منبع
            if png is not None and not _cache_decodable(png):
                png = None                    # بازسازی هم خراب — زنجیره ادامه یابد
        if png:
            return png
    for cand, fn in ((vid, fthumbs.video_thumb_only),
                     (pdf, fthumbs.pdf_thumb_only),
                     (arc, fthumbs.archive_thumb_only)):
        if not cand:
            continue
        try:
            # force فقط هنگام بازتلاش پاس داده می‌شود — امضای دوآرگومانتیِ قدیمی
            # (و mockهای تست) در حالت عادی دست‌نخورده می‌ماند
            png = fn(cand, px, force=True) if force else fn(cand, px)
        except Exception as _exc_dbg:
            dbg_log(f"poster_png_fallback failed: {type(_exc_dbg).__name__}")
            png = None
        if png:
            return png
    return None


def group_of(path) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS_SET:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in DOC_EXTS:
        return "doc"
    if ext in CODE_EXTS:
        return "code"
    if ext in EXE_EXTS:
        return "exe"
    return "other"


def _cover_square(im: Image.Image, px: int) -> Image.Image:
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    # تصویر خیلی بزرگ (مثلاً PNG چندهزارپیکسلی): LANCZOS مستقیم روی کل پیکسل‌ها
    # ثانیه‌ها طول می‌کشد. reduce (فیلتر جعبه‌ایِ C و فاکتور صحیح) اول ابعاد را
    # به چند برابر px می‌رساند — برای تامبنیل بصری همان، چند برابر تندتر.
    if side > px * 4:
        try:
            im = im.reduce(max(1, side // (px * 4)))
        except Exception as _exc_dbg:
            dbg_log(f"_cover_square failed: {type(_exc_dbg).__name__}")
            pass
    return im.resize((px, px), Image.Resampling.LANCZOS)


def _load_cover_image(path: Path, px: int) -> Image.Image:
    """بازکردن و کراپ مربعیِ *سریع* یک تصویر از روی دیسک.

    با draft به دیکدر JPEG اجازه می‌دهد مستقیم در اندازه‌ی
    ۱/۲، ۱/۴ یا ۱/۸ رمزگشایی کند (به‌جای دیکد کامل + کوچک‌کردن) — چند برابر سریع‌تر.
    (round 20 §۲) چرخشِ EXIF هم این‌جا اعمال می‌شود — پوسترِ پوشه‌ای که عکسِ
    گوشیِ چرخیده است قبلاً خوابیده کش می‌شد.
    """
    with Image.open(path) as src:
        try:
            if (src.format or "") == "JPEG":
                src.draft("RGB", (px * 2, px * 2))
        except Exception as _exc_dbg:
            dbg_log(f"_load_cover_image failed: {type(_exc_dbg).__name__}")
            pass
        im = apply_exif_transpose(src)
        return _cover_square(im.convert("RGB"), px)


def _is_black(im: Image.Image) -> bool:
    try:
        return ImageStat.Stat(im.convert("L").resize((8, 8))).mean[0] < _BLACK_MEAN
    except Exception as _exc_dbg:
        dbg_log(f"_is_black failed: {type(_exc_dbg).__name__}")
        return False


# کش پس‌زمینه‌ی گرادیانی کارت‌های نوع‌فایل — قبلاً گرادیان px×px پیکسل‌به‌پیکسل با
# پایتون خالص ساخته می‌شد (~۲۵ میلی‌ثانیه برای هر کارت!)؛ حالا یک گرادیان ۱۲×۱۲
# ریسایز می‌شود (بصری یکسان) و برای همه‌ی کارت‌های هم‌گروه کش می‌ماند → کسری از میلی‌ثانیه.
_GRAD_CACHE: dict = {}


def _grad_bg(c1: tuple, c2: tuple, px: int) -> Image.Image:
    key = (c1, c2, px)
    im = _GRAD_CACHE.get(key)
    if im is None:
        small = Image.new("RGB", (12, 12))
        m = small.load()
        for y in range(12):
            for x in range(12):
                t = (x + y) / 22.0
                m[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        im = small.resize((px, px), Image.Resampling.BILINEAR)
        if len(_GRAD_CACHE) >= 64:
            _GRAD_CACHE.clear()
        _GRAD_CACHE[key] = im
    return im.copy()      # نقاشی روی کپی — نسخه‌ی کش‌شده دست نمی‌خورد


def _draw_card_glyph(d: ImageDraw.ImageDraw, grp: str, cx: int, cy: int,
                     g: int, sw: int, sh: int, x0: int, y0: int,
                     ac: tuple, lw: int, lighten: tuple, px: int) -> None:
    """گلیف‌های برداریِ داخلی هر دسته — فالبکِ همیشگی وقتی آیکونِ دانلودشده
    در دسترس نباشد (بدون پوشه‌ی assets/icons هم برنامه باید کار کند)."""
    # ---------- گلیف هر دسته (اشکال برداری — در ۱۳۲ و ۲۵۶ هر دو تیز) ----------
    if grp == "video":
        t = int(g * 0.27)
        d.polygon([(cx - int(t * 0.55), cy - t), (cx - int(t * 0.55), cy + t),
                   (cx + t, cy)], fill=ac)
    elif grp == "audio":
        r2 = int(g * 0.13) or 2
        d.ellipse((cx - int(g * 0.24), cy + int(g * 0.02),
                   cx - int(g * 0.24) + 2 * r2, cy + int(g * 0.02) + 2 * r2), fill=ac)
        d.rectangle((cx - max(1, lw // 2), cy - int(g * 0.30), cx, cy + int(g * 0.12)),
                    fill=ac)
        d.polygon([(cx, cy - int(g * 0.30)), (cx + int(g * 0.24), cy - int(g * 0.20)),
                   (cx, cy - int(g * 0.10))], fill=ac)
    elif grp == "image":
        sr = int(g * 0.10) or 2
        d.ellipse((cx - int(g * 0.26), cy - int(g * 0.28),
                   cx - int(g * 0.26) + 2 * sr, cy - int(g * 0.28) + 2 * sr),
                  fill=(246, 190, 70))
        d.polygon([(cx - int(g * 0.28), cy + int(g * 0.24)),
                   (cx - int(g * 0.05), cy - int(g * 0.05)),
                   (cx + int(g * 0.09), cy + int(g * 0.24))], fill=ac)
        d.polygon([(cx - int(g * 0.01), cy + int(g * 0.24)),
                   (cx + int(g * 0.16), cy + int(g * 0.01)),
                   (cx + int(g * 0.31), cy + int(g * 0.24))], fill=lighten)
    elif grp == "archive":
        bw, bh = int(g * 0.56), int(g * 0.42)
        bx0, by0 = cx - bw // 2, cy - bh // 2
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(2, px // 56),
                            outline=ac, width=lw)
        d.line((bx0 + lw, cy, bx0 + bw - lw, cy), fill=ac, width=max(1, lw // 2))
        d.rectangle((cx - int(g * 0.09), by0 - max(1, lw // 2),
                     cx + int(g * 0.09), by0 + int(g * 0.12)), fill=ac)
    elif grp == "sub":
        bw, bh = int(g * 0.60), int(g * 0.36)
        bx0, by0 = cx - bw // 2, cy - int(bh * 0.62)
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(3, px // 36),
                            outline=ac, width=lw)
        d.polygon([(cx - int(g * 0.05), by0 + bh - 1), (cx + int(g * 0.11), by0 + bh - 1),
                   (cx - int(g * 0.01), by0 + bh + int(g * 0.17))], fill=ac)
        for i, frac in enumerate((0.34, 0.62)):
            lw2 = int(bw * (0.60 - i * 0.16))
            d.line((bx0 + int(bw * 0.18), by0 + int(bh * frac),
                    bx0 + int(bw * 0.18) + lw2, by0 + int(bh * frac)),
                   fill=ac, width=max(1, lw // 2 + 1))
    elif grp == "doc":
        for i, frac in enumerate((0.30, 0.48, 0.66)):
            lw2 = int(sw * (0.60 - i * 0.11))
            d.line((x0 + int(sw * 0.19), y0 + int(sh * frac),
                    x0 + int(sw * 0.19) + lw2, y0 + int(sh * frac)),
                   fill=ac, width=max(2, lw // 2 + 1))
    elif grp == "code":
        try:
            f_code = ImageFont.load_default(size=max(8, int(g * 0.34)))
        except Exception as _exc_dbg:
            dbg_log(f"_draw_card_glyph failed: {type(_exc_dbg).__name__}")
            f_code = ImageFont.load_default()
        tagc = "</>"
        bb = d.textbbox((0, 0), tagc, font=f_code)
        d.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - bb[1]),
               tagc, fill=ac, font=f_code)
    elif grp == "exe":
        bw, bh = int(g * 0.56), int(g * 0.44)
        bx0, by0 = cx - bw // 2, cy - bh // 2
        d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh), max(3, px // 44),
                            outline=ac, width=lw)
        d.rectangle((bx0 + lw, by0 + lw, bx0 + bw - lw, by0 + int(bh * 0.30)),
                    fill=lighten)
        d.rectangle((bx0 + int(bw * 0.12), by0 + int(bh * 0.50),
                     bx0 + int(bw * 0.55), by0 + int(bh * 0.50) + max(2, lw // 2)),
                    fill=ac)
        d.rectangle((bx0 + int(bw * 0.12), by0 + int(bh * 0.68),
                     bx0 + int(bw * 0.40), by0 + int(bh * 0.68) + max(2, lw // 2)),
                    fill=lighten)
    else:   # other — ستاره‌ی ※
        r3 = int(g * 0.22)
        d.line((cx - r3, cy, cx + r3, cy), fill=ac, width=lw)
        d.line((cx - int(r3 * 0.7), cy - int(r3 * 0.7), cx + int(r3 * 0.7),
                cy + int(r3 * 0.7)), fill=ac, width=lw)
        d.line((cx - int(r3 * 0.7), cy + int(r3 * 0.7), cx + int(r3 * 0.7),
                cy - int(r3 * 0.7)), fill=ac, width=lw)



class FileThumbs:
    # شمارنده‌ی کلاس‌سطح برای خلاصه‌ی پیش‌ساخت — کاربر می‌خواهد بداند ffmpeg
    # (مسیر کندِ فال‌بک) دقیقاً برای چند ویدیو افتاده است
    ffmpeg_video_uses: int = 0
    # گزارش کاربر: «چرا بدتر شد؟ GPU ۲٬۰۸۰ ولی ffmpeg ۳١٬۳۳٠» + تأییدِ
    # راهنما — شتاب GPU برای استخراجِ «یک فریم» سودی نداشت — با سیکِ سریع، دکودِ
    # CPU چند میلی‌ثانیه است و GPU فقط سربارِ ساختِ کانتکستِ CUDA و کپیِ
    # VRAM→RAM می‌افزود؛ بدتر آن‌که حلقه‌ی امتحانِ حالت‌ها وقتی خروجی خالی می‌داد
    # برای همان فایل چند بار ffmpeg را اجرا می‌کرد. به تصمیمِ کاربر مسیرِ GPU
    # کاملاً حذف شد؛ شمارندهٔ یادگیریِ GPU (فایلِ v_gpu در کش) و پروبِ حالت‌های
    # شتاب دیگر وجود ندارند. سقفِ سختِ «حداکثر ۲ فرآیند برای هر ویدیو» دوباره
    # واقعی است.
    # (گزارش کاربر: «ffmpeg 31320 بار!» — در یک اجرای عادی باید ≈۱ بار برای هر
    # ویدیو باشد): شمارشِ پروبِ هر فایل در همین اجرا. هر پروبِ تکراریِ همان فایل
    # یعنی کش موفق ذخیره نشده یا مسیرِ دیگری دورِ کش می‌زند — از سومین بار در لاگ
    # دیباگ ثبت می‌شود و مجموعِ تکرارها در خلاصه‌ی پیش‌ساخت ظاهر می‌شود تا ریشه‌ی
    # آن عددِ نجومی محلی شود.
    _probe_counts: dict = {}          # (مسیر فایل، px) → تعداد پروب در این اجرا
    _REPROBE_LOG_AT = 3               # از این تکرار به بعد در لاگ دیباگ فریاد بزن
    # قفل در-پرواز — برای هر کلید در هر لحظه حداکثر یک پروبِ واقعی (رفع
    # دوبله‌های هم‌زمانِ دو استخرِ پوستر/پیش‌ساخت همان اجرا)
    _probe_inflight: dict = {}
    _probe_inflight_lock = threading.Lock()
    # شمارِ آرشیوهایی که کاندید داشتند ولی همه‌ی کاندیدها شکست خوردند —
    # این موارد کشِ منفی نمی‌گیرند و در خلاصه‌ی پیش‌ساخت گزارش می‌شوند
    _archive_soft_fails: int = 0
    # مسیرِ دقیقِ آرشیوهای شکست‌نرمِ این اجرا — پیش‌ساز با آن «بازتلاشِ
    # کران‌دارِ» پر-عنوان را می‌سازد (ft.get موقعِ شکست «کارت نوع‌فایل» برمی‌گرداند
    # نه None، پس نتیجه‌ی تابع به‌تنهایی شکست را نشان نمی‌دهد)
    _archive_soft_files: set = set()
    # گزارش کاربر: کش‌ویندوز ۰، رندر ۰ — هندلر تامبنیل روی سیستمِ او نیست —
    # اگر دنده‌های Shell ویندوز هرگز جواب ندهند، دیگر برای تک‌تکِ هزاران ویدیو
    # کورکورانه صدا زده نمی‌شوند — هر فراخوانِ رندرِ شکست‌خورده صدها میلی‌ثانیه
    # هزینه دارد و در ۲۳هزار فایل خودش چند دقیقه هدررفت خالص است. در سیستم‌هایی
    # که هندلر دارند (MP4 وطنی ویندوز، Icaros و…) هیت‌ها پرچم را زنده نگه‌می‌دارند.
    _shell_video_dead: bool = False
    _SHELL_KILL_AFTER = 8              # میس/خطای پیاپی با هیتِ صفر → خاموش (0.28.0: ۱۶←۸ — هر رندر شکست‌خورده صدها میلی‌ثانیه ضرر است)
    _SHELL_RATIO_AFTER = 40            # راهنما۲: ۱۰۰←۴۰ — روی سیستم بدون‌هندلر زودتر به ffmpeg می‌افتیم
    _SHELL_RATIO_MIN = 0.02
    # راهنما۳: یادگیری پر-پسوند — کشِ ویندوز برای MP4 وطنی دارد ولی برای MKV
    # نه؛ نباید سرنوشت MP4 به MKV گره بخورد. برای هر پسوند جدا (miss×hit) نگه
    # می‌داریم و پسوندِ بی‌فایده را فقط برای همان پسوند کنار می‌گذاریم.
    _shell_ext_stats: dict = {}        # ext → [misses, hits]
    _shell_ext_dead: set = set()
    _SHELL_EXT_KILL = 6                # این‌قدر میس پیاپی برای همان پسوند (بدون هیچ هیت) → مرده
    _SHELL_EXT_RATIO_AFTER = 30

    @classmethod
    def _shell_for_video_alive(cls) -> bool:
        if cls._shell_video_dead:
            return False
        try:
            s = shell_thumbnail_stats()
        except Exception as _exc_dbg:
            dbg_log(f"_shell_for_video_alive failed: {type(_exc_dbg).__name__}")
            return True                  # آمار در دسترس نیست → محتاطانه فعال نگه دار
        hits = s.get("cache_hit", 0) + s.get("render_hit", 0)
        tries = hits + s.get("miss", 0) + s.get("error", 0)
        dead = False
        if s.get("miss", 0) + s.get("error", 0) >= cls._SHELL_KILL_AFTER and hits == 0:
            dead = True
        elif tries >= cls._SHELL_RATIO_AFTER and hits / max(tries, 1) < cls._SHELL_RATIO_MIN:
            dead = True                  # گزارش کاربر: ۵ هیت از ~۶۰۰۰ تلاش — عملاً مرده
        if dead:
            cls._shell_video_dead = True
            try:
                from watchtime import _dbg
                _dbg(f"video thumbs: دنده‌ی Shell ویندوز روی این سیستم بی‌فایده است "
                     f"({s.get('miss', 0)} miss/{s.get('error', 0)} err، "
                     f"{hits} hit از {tries}) "
                     f"— ادامه‌ی ویدیوها مستقیم به ffmpeg می‌روند")
            except Exception as _exc_dbg:
                dbg_log(f"_shell_for_video_alive failed: {type(_exc_dbg).__name__}")
                pass
        return not cls._shell_video_dead

    @classmethod
    def _shell_for_ext_alive(cls, ext: str) -> bool:
        """آیا برای این پسوند خاص هنوز ارزش دارد Shell امتحان شود؟ (0.28.0)"""
        return ext not in cls._shell_ext_dead

    @classmethod
    def _shell_ext_note(cls, ext: str, ok: bool):
        """نتیجه‌ی یک تلاش Shell برای پسوند را یاد بگیر (0.28.0 — راهنما۳)."""
        st = cls._shell_ext_stats.get(ext)
        if st is None:
            st = cls._shell_ext_stats[ext] = [0, 0]
        st[0 if not ok else 1] += 1
        misses, hits = st
        exts_tried = misses + hits
        if ext in cls._shell_ext_dead:
            return
        if misses >= cls._SHELL_EXT_KILL and hits == 0:
            cls._shell_ext_dead.add(ext)
        elif exts_tried >= cls._SHELL_EXT_RATIO_AFTER and \
                hits / max(exts_tried, 1) < cls._SHELL_RATIO_MIN:
            cls._shell_ext_dead.add(ext)
        else:
            return
        try:
            from watchtime import _dbg
            _dbg(f"video thumbs: Shell برای «{ext}» مرده اعلام شد "
                 f"({misses} miss/{hits} hit) — این پسوند دیگر مستقیم به ffmpeg می‌رود")
        except Exception as _exc_dbg:
            dbg_log(f"_shell_ext_note failed: {type(_exc_dbg).__name__}")
            pass

    def __init__(self, cache_dir):
        # همان فال‌بکِ فقط‌خواندنیِ ThumbnailCache (D2) — «files/» هم زیرِ
        # پوشه‌ی فال‌بک حفظ می‌شود تا با کشِ پوسترها قاطی نشود
        from imgcache import writable_cache_dir
        d, ro = writable_cache_dir(Path(cache_dir) / "files",
                                   fallback_name="FolderDex-cache/files")
        self.dir = d
        self.read_only_fallback = ro
        # اولویت با ffmpeg همراهِ برنامه (bin/ کنار exe — اجرای پرتابل بدون پیش‌نیاز)
        self.ffmpeg = bundled_binary("ffmpeg.exe") or shutil.which("ffmpeg")
        # تامبنیل از داخل فایل‌های فشرده — از تنظیمات برنامه تغذیه می‌شود (صفحات مقدارش را ست می‌کنند)
        self.archive_enabled = True
        # کش «عضو انتخاب‌شده‌ی آرشیو» — دفعات بعد بدون لیست‌خواندن اعضا، مستقیم
        self._arc_picks: dict | None = None
        self._arc_picks_lock = threading.Lock()
        # وضعیت نخ‌محل برای علامت «کاندید داشت ولی همه شکست خوردند» —
        # _probe_cached با این پرچم مارکر منفیِ چسبنده نمی‌نویسد (تلاش مجدد در
        # اجرای بعدِ برنامه — گزارش کاربر: «یکی از سه آرشیو تامبنیل نگرفت»)
        self._tls = threading.local()

    # ------------------------------------------------- کش عضو انتخاب‌شده‌ی آرشیو
    @staticmethod
    def _arc_key(path: Path) -> str | None:
        try:
            st = path.stat()
        except OSError:
            return None
        p = os.path.normcase(os.path.abspath(str(path)))
        return hashlib.sha1(
            f"{p}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()

    def _arc_picks_load(self) -> dict:
        if self._arc_picks is not None:
            return self._arc_picks              # مسیر سریع بدون قفل (دیکت جایگزین می‌شود، نه درجا)
        with self._arc_picks_lock:
            if self._arc_picks is None:
                try:
                    fp = self.dir / "archive_picks.json"
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    self._arc_picks = data if isinstance(data, dict) else {}
                except Exception as _exc_dbg:
                    dbg_log(f"_arc_picks_load failed: {type(_exc_dbg).__name__}")
                    self._arc_picks = {}
            return self._arc_picks

    def _arc_picks_save(self):
        """نوشتن اتمیک کش روی دیسک — فرض: زیر قفل صدا زده می‌شود."""
        try:
            tmp = self.dir / (f"ap_{os.getpid()}_{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self._arc_picks or {}, ensure_ascii=False),
                           encoding="utf-8")
            # round 29: قفلِ گذرای ویندوز روی کشِ انتخاب‌ها هم جذب شود
            replace_with_retry(tmp, self.dir / "archive_picks.json")
        except Exception as e:
            # انتشار عمومی: شکستِ نوشتنِ کش روی دیسک بی‌صدا نماند
            try:
                from watchtime import _dbg
                _dbg(f"archive_picks save failed: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"_arc_picks_save failed: {type(_exc_dbg).__name__}")
                pass

    def _arc_pick_get(self, path: Path) -> dict | None:
        key = self._arc_key(path)
        if not key:
            return None
        rec = self._arc_picks_load().get(key)
        if not rec or not rec.get("n") or rec.get("k") not in ("image", "video", "pdf"):
            return None
        return rec

    def _arc_pick_put(self, path: Path, name: str, kind: str, size: int = 0):
        key = self._arc_key(path)
        if not key:
            return
        picks = self._arc_picks_load()          # بارگذاری *خارج* از قفل (رفع ددلاک)
        with self._arc_picks_lock:
            # حجم عضو هم ذخیره می‌شود تا تایم‌اوتِ استخراجِ دفعات بعد (بدون
            # لیست‌خواندن) هم بر اساس حجم سازش‌پذیر بماند
            picks[key] = {"n": name, "k": kind, "s": int(size or 0)}
            while len(picks) > 4000:            # سقف کش — LRU ساده (ترتیب درج)
                try:
                    picks.pop(next(iter(picks)))
                except StopIteration:
                    break
            self._arc_picks_save()

    def _arc_pick_drop(self, path: Path):
        key = self._arc_key(path)
        if not key:
            return
        picks = self._arc_picks_load()          # بارگذاری *خارج* از قفل (رفع ددلاک)
        with self._arc_picks_lock:
            if picks.pop(key, None) is not None:
                self._arc_picks_save()

    # ---------------------------------------------------------------- کش
    def _cache_path(self, src: Path, tag: str, px: int) -> Path:
        try:
            st = src.stat()
            # normcase+abspath به‌جای resolve: resolve() در ویندوز برای لینک‌خوانی
            # به دیسک می‌زند و در کشِ هر فایل (درایو شبکه/HDD) زمان‌بر بود (گزارش
            # کاربر). پسوند نسل |v5: چرخشِ EXIF در رندر (round 20 §۲) —
            # تامبنیل‌های خوابیدهٔ نسلِ قبل خودکار بازسازی می‌شوند (یک‌بار).
            p = os.path.normcase(os.path.abspath(str(src)))
            raw = f"{p}|{st.st_mtime_ns}|{st.st_size}|{tag}|{px}|v5"
        except OSError:
            raw = f"{src}|{tag}|{px}|v5"
        # کشِ کهنه‌ی .png هم به‌عنوان hit شناخته می‌شود تا تامبنیل‌های
        # ازپیش‌ساخته از نو پردازش نشوند (cache_file).
        stem = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return cache_file(self.dir, stem)

    @staticmethod
    def _save(im: Image.Image, out: Path) -> Path:
        """ذخیره‌ی اتمیک (WebP/PNG فال‌بک) — بدون نشت پروفایل رنگی؛ مسیر نهایی را می‌دهد."""
        return save_cache_image(im, out)

    # ---------------------------------------------------------------- API
    @staticmethod
    def _card_tag(path: Path, grp: str) -> str:
        """برچسب کشِ نتیجه برای یک فایل (0.35.0) — قانونِ مشترک peek/_get_inner:
        زیرنویس‌ها «sub3» و گروه‌های کارت‌محور «|c4» می‌گیرند؛ بقیه همان گروه."""
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            return "sub3"
        if grp in _CARD_STYLE_GROUPS and suf != ".pdf":
            return grp + "|c4"
        return grp

    def peek(self, path, px: int) -> Path | None:
        """فقط اگر در کش باشد برمی‌گرداند (بدون تولید)."""
        path = Path(path)
        out = self._cache_path(path, self._card_tag(path, group_of(path)), px)
        return out if out.exists() else None

    def card(self, path, px: int) -> Path | None:
        """کارت نوع‌فایل (بلا تأخیر) — برای نمایش موقت تا تامبنیل واقعی آماده شود."""
        path = Path(path)
        # «|c4» = نسلِ «فقط آیکون» (پلیسهولدرهای نسلِ قبل یک‌بار ارزان
        # بازسازی می‌شوند؛ محتوای این برچسب همیشه کارتِ رسم‌شده است)
        out = self._cache_path(path, "card" + group_of(path) + "|c4", px)
        if not out.exists():
            try:
                out = self._save(self._type_card(path, group_of(path), px), out)
            except Exception as _exc_dbg:
                dbg_log(f"card failed: {type(_exc_dbg).__name__}")
                return None
        return out

    def get(self, path, px: int = 160) -> Path | None:
        """تامبنیل واقعی (همگام، برای استفاده در رشته‌ی پس‌زمینه).

        تامبنیل‌های خیلی کند (>۰.۸ ثانیه) در لاگ دیباگ ثبت می‌شوند تا فایلِ مشکل‌دار
        (PDF خراب، ویدیوی سنگین، آرشیو روی درایو کند) قابل ردیابی باشد.
        """
        import time as _time
        _t0 = _time.monotonic()
        try:
            return self._get_inner(path, px)
        finally:
            _dt = _time.monotonic() - _t0
            if _dt > 0.8:
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"slow thumb {_dt:.2f}s: {safe_ref(path)}")
                except Exception as _exc_dbg:
                    dbg_log(f"get failed: {type(_exc_dbg).__name__}")
                    pass

    def _get_inner(self, path, px: int = 160) -> Path | None:
        path = Path(path)
        grp = group_of(path)
        # برچسب کش برای گروه‌های کارت‌محور + زیرنویس‌ها (نسلِ «فقط آیکون») —
        # کلیدِ تازه یعنی کارت‌های قاب‌دارِ نسلِ قبل یک‌بار (ارزان) بازسازی می‌شوند؛
        # تامبنیل‌های واقعی (تصویر/ویدیو/pdf/آرشیو) با همان کلید قبلی حفظ می‌مانند.
        card_tag = self._card_tag(path, grp)
        if grp == "archive" and self.archive_enabled:
            hit = self.archive_thumb_only(path, px)
            if hit:
                return hit                      # وگرنه: کارت نوع‌فایل (بقیه‌ی تابع)
        out = self._cache_path(path, card_tag, px)
        if out.exists():
            return out
        # تامبنیل واقعی برای PDF (نه فقط در پوستر عناوین — کارت‌های خودِ فایل هم
        # صفحه‌ی اول را نشان بدهند؛ درخواست کاربر). شکست → کارت نوع‌فایل مثل قبل.
        if grp == "doc" and path.suffix.lower() == ".pdf":
            hit = self.pdf_thumb_only(path, px)
            if hit:
                return hit
        # مسیر ویدیو حالا از همان wrapper مشترکِ پروب می‌گذرد (کش منفی +
        # پذیرشِ سایز + شمارش reprobe) — قبلاً اینجا مستقیم _video_thumb صدا
        # زده می‌شد و کشِ پوسترِ ۲۵۶ برای گریدِ ۱۳۲ بدون استفاده می‌ماند؛ یعنی هر
        # ویدیو دوبار ffmpeg می‌دید. شکست → کارت نوع‌فایل مثل قبل.
        if grp == "video":
            hit = self.video_thumb_only(path, px)
            if hit:
                return hit
        im = None
        try:
            if grp == "image":
                im = _load_cover_image(path, px)   # draft برای JPEG → چند برابر تندتر
        except Exception as _exc_dbg:
            dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
            im = None
        if im is None:
            try:
                im = self._type_card(path, grp, px)
            except Exception as _exc_dbg:
                dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
                return None
        try:
            return self._save(im, out)
        except Exception as _exc_dbg:
            dbg_log(f"_get_inner failed: {type(_exc_dbg).__name__}")
            return None

    # ------------------------------------------------- امضای قابلیت‌ها برای کش منفی
    def _capability_key(self) -> str:
        """اثر انگشت ابزارهای موجود — برای اعتبارسنجی مارکر منفی (.bad).

        باگ گزارش‌شده: اگر وقتی 7za/ffmpeg/pypdfium2 در دسترس نبودند پروب یک فایل
        شکست می‌خورد، مارکر .bad برای همیشه می‌ماند و حتی بعد از همراه‌شدن ابزار
        (نسخه‌ی جدید برنامه) تامبنیل آن فایل هرگز دوباره تلاش نمی‌شد — مثلاً پوشه‌ای
        با «ویدیوی خراب + آرشیو سالم» هرگز سراغ آرشیو نمی‌رفت چون آرشیو از قبل
        .bad بود. حالا مارکر فقط وقتی معتبر است که امضای قابلیت‌ها همان باشد.
        """
        try:
            bits = [
                "7" if _find_7z() else "-",
                "u" if _find_unrar() else "-",
                "f" if self.ffmpeg else "-",
                "p" if _pdf_lib() else "-",
                "a" if self.archive_enabled else "-",
            ]
        except Exception as _exc_dbg:
            dbg_log(f"_capability_key failed: {type(_exc_dbg).__name__}")
            bits = ["?"]
        # منطق آرشیو به‌جای «یک کاندید» چند کاندید می‌کوشد — مارکرهای شکستِ
        # نسخه‌های قبل (|v2) دیگر معتبر نیستند و آن 7zهای تامبنیل‌نگرفته دوباره
        # پروب می‌شوند (گزارش کاربر).
        # (|v4): زنجیره‌ی ویدیو عوض شد — کشِ اکسپلورر ← رندرِ Shellِ
        # THUMBNAILONLY ← ffmpegِ یک‌شاتیِ کلید‌فریم؛ فایل‌هایی که قبلاً شکست
        # خورده بودند (مثلاً ffmpegِ قدیمی با سیک‌های ثابت) با مسیر تازه دوباره
        # پروب می‌شوند و فایل‌هایی که به‌اشتباه «آیکنِ ویندوز» کش شده بودند هم
        # از تامبنیلِ واقعی بهره می‌برند.
        # (|v5): منطقِ ffmpeg به «تک‌شاتِ میانی» تغییر کرد و کاندیدهای
        # آرشیو کمتر شدند — فایل‌های شکست‌خورده‌ی قبلی (مارکر .bad) دقیقاً یک‌بار
        # با مسیرِ تازه دوباره پروب می‌شوند؛ اما تامبنیل‌های موفقِ موجود
        # دست‌نخورده و معتبر می‌مانند (نسخه‌ی مسیرِ کش |v3 عمداً ثابت است —
        # بازسازیِ کلِ هجده‌هزار کشِ کاربر خلافِ خواستِ اوست).
        # (|v6): ریشه‌ی واقعیِ گزارش «آرشیوِ ۵۴۷ مگی هنوز تامبنیل ندارد» —
        # حالا که کش بین نسخه‌ها *پایدار* و به‌ارث می‌رسد (0.34.0)، مارکرهای منفیِی
        # که با منطقِ قدیمی (سقف ۲GB، تایم‌اوت ثابت، پسوندهای کمتر) نوشته شده‌اند
        # همراهِ کش می‌آیند و پروبِ تازه را برای همیشه مسدود می‌کردند. با هر تغییرِ
        # منطق باید دوره (era) عوض شود تا شکست‌های قدیمی دقیقاً یک‌بار با منطقِ
        # تازه پروب شوند؛ موفقیت‌های کش‌شده (تامبنیل‌های واقعی) همچنان دست‌نخورده.
        return "+".join(bits) + "|v6"

    def _probe_cached(self, path, px: int, grp_req: str, probe,
                      force: bool = False) -> Path | None:
        """الگوی مشترک «پروب + کش دیسکی + کش منفی» برای تامبنیل‌های واقعی.

        نتیجه‌ی منفی با مارکر .bad کنار خود کش ثبت می‌شود (کلید کش شامل mtime/size است،
        پس تغییر فایل = پروب تازه) تا فایل خراب/ناشناخته در هر بازسازی دوباره کاوش نشود.
        محتوای مارکر = امضای قابلیت‌ها: اگر ابزار تازه‌ای همراه برنامه شده باشد،
        مارکر کهنه نادیده گرفته و پروب تازه انجام می‌شود. موفقیت، مارکر را پاک می‌کند.
        force=True فقط برای «بازتلاش زمان‌بندی‌شده»‌ی زنجیره‌ی پوستر است: مارکر منفی
        (که شاید حاصل یک خرابیِ گذرا بوده) نادیده گرفته و حتماً دوباره پروب می‌کند —
        موفقیت، مارکر چسبنده‌ی کهنه را برای همیشه پاک می‌کند (گزارش کاربر:
        «پوشه‌ی فقط‌فشرده هرگز پوستر نمی‌گیرد»)."""
        path = Path(path)
        grp = group_of(path)
        if grp != grp_req:
            return None
        out = self._cache_path(path, grp, px)
        if out.exists():
            return out
        # پذیرش کشِ سایزِ دیگر — اگر تامبنیلِ همین فایل در سایزِ بزرگ‌تری
        # (مثلاً پوسترِ ۲۵۶) کش شده، نسخه‌ی درخواستی (مثلاً گریدِ ۱۳۲) از همان
        # مشتق می‌شود. هر فایل در کلِ عمرش فقط *یک‌بار* پروب می‌شود؛ تا امروز
        # هر ویدیو دو‌بار ffmpeg می‌دید: یکی برای پوستر و یکی برای گرید.
        donor = self._adopt_from_other_px(path, grp, px)
        if donor is not None:
            out.with_suffix(".bad").unlink(missing_ok=True)
            return donor
        bad = out.with_suffix(".bad")
        if bad.exists():
            try:
                same_cap = bad.read_text(encoding="ascii", errors="replace") == \
                    self._capability_key()
            except OSError:
                same_cap = False              # خواندن نشد → محتاطانه از نو پروب کن
            if same_cap:
                if not force:
                    return None               # با همین ابزارها ناممکن بود — نتیجه معتبر
                # گزارش کاربر: «reprobe ۱۲۸» در اجرای تنبل — بازتلاشِ اجباریِ
                # زنجیره‌ی پوستر هم در هر اجرا فقط *یک‌بار* — تایمرِ بازتلاشِ
                # پوستر صدها فایلِ شکست‌خورده‌ی همین اجرا (مارکر .bad با همین
                # قابلیت‌ها) را مدام دوباره پروب می‌کرد؛ هر بار تا ۲ فرآیند
                # ffmpeg با تایم‌اوت ۸ ثانیه → عمده‌ی کندیِ اجرای سنگین.
                if FileThumbs._probe_counts.get((str(path), px), 0) >= 1:
                    return None               # امسال یک‌بار تلاش شد و شکست خورد — بس است
        # شمارش پروب برای شکارِ دوباره‌کاری‌ها (رفتار عادی = هر فایل یک‌بار؛
        # تکرار یعنی ذخیره‌ی کش شکسته یا چند مسیرِ موازی دورِ هم‌کلید می‌زنند).
        rkey = (str(path), px)
        # گزارش کاربر: reprobe ۱۲۸ در اجرای تنبل — حتی بعد از سرکوبِ force —
        # دو استخرِ مستقل (فازِ پوسترِ پیش‌ساخت با POSTER_WORKERS نخ + کارگرِ
        # پوسترِ صفحه‌ی کتابخانه) هم‌زمان سراغ یک فایل می‌روند؛ چون هنوز نه کش
        # هست نه مارکر .bad، هر دو پروب واقعی می‌زنند. حالا پروبِ دوم منتظر
        # پروبِ اول می‌ماند و نتیجه را از روی کش برمی‌دارد — به ازای هر کلید
        # در هر لحظه حداکثر یک پروبِ واقعی.
        with FileThumbs._probe_inflight_lock:
            ev = FileThumbs._probe_inflight.get(rkey)
            if ev is None:
                FileThumbs._probe_inflight[rkey] = ev = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(timeout=180)         # آرشیوهای بزرگ روی درایو کند هم پوشش داده می‌شوند
            if out.exists():
                return out
            try:
                if bad.exists() and bad.read_text(
                        encoding="ascii", errors="replace") == self._capability_key():
                    return None
            except OSError:
                pass
            return None                  # صاحبِ پروب شکست خورد — تکرار نمی‌کنیم
        try:
            rn = FileThumbs._probe_counts.get(rkey, 0) + 1
            FileThumbs._probe_counts[rkey] = rn
            if rn == FileThumbs._REPROBE_LOG_AT:
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"reprobe x{rn}: {safe_ref(path)} (px={px}) — علی‌رغم قفل در-پرواز!")
                except Exception as _exc_dbg:
                    dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                    pass
            try:
                im = probe(path, px)
            except Exception as e:
                # درخواست کاربر: «چیزی اضافه کن که بشه فهمید چرا نشد» —
                # خطای پروب در لاگ ثبت می‌شود — بدون مسیر/نام، فقط safe_ref.
                try:
                    from watchtime import _dbg, safe_ref
                    _dbg(f"خطای بازتولید: {safe_ref(path)} (px={px}): {type(e).__name__}")
                except Exception as _exc_dbg:
                    dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                    pass
                im = None
            if im is None:
                # شکستِ «نرم» آرشیو (کاندید داشت ولی گذراً شکست خورد) مارکر
                # منفی نمی‌گیرد — اجرای بعد دوباره تلاش می‌کند (گزارش کاربر:
                # «یکی از سه آرشیو تامبنیل نگرفت»)
                _soft = (grp_req == "archive"
                         and getattr(self._tls, "arc_soft", False))
                if not _soft:
                    try:
                        bad.write_text(self._capability_key(), encoding="ascii")
                    except Exception as _exc_dbg:
                        dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                        pass
                return None
            try:
                out = self._save(im, out)
                bad.unlink(missing_ok=True)
                return out
            except Exception as _exc_dbg:
                dbg_log(f"_probe_cached failed: {type(_exc_dbg).__name__}")
                return None
        finally:
            with FileThumbs._probe_inflight_lock:
                FileThumbs._probe_inflight.pop(rkey, None)
            ev.set()

    @classmethod
    def reprobe_total(cls) -> int:
        """مجموع پروبهای تکراریِ این اجرا (رفتار عادی = صفر) — 0.28.0."""
        return sum(n - 1 for n in cls._probe_counts.values() if n > 1)

    # درخواست کاربر + راهنما: «اولویت با چیزی است که کاربر می‌بیند» —
    # زمانِ آخرین درخواستِ تامبنیلِ UI (ویوپورتِ گرید جزئیات / کارت‌های کتابخانه /
    # دیالوگ پیشنهاد). فازهای پیش‌ساخت هنگام فعالیتِ تازه‌ی UI مکث می‌کنند تا
    # تامبنیلِ همین صفحه‌ای که کاربر نگاهش می‌کند فوری ساخته شود.
    _last_ui_request: float = 0.0

    @classmethod
    def ui_request_tick(cls):
        """یک درخواستِ UI را ثبت کن (ارزان — فقط یک مهر زمانی)."""
        cls._last_ui_request = time.monotonic()

    @classmethod
    def ui_active(cls, window: float = 2.5) -> bool:
        """آیا کاربر همین حالا در حال مرور است (درخواستِ UI در چند ثانیه‌ی اخیر)؟"""
        return (time.monotonic() - float(cls._last_ui_request or 0.0)) < window

    # سایزهای واقعیِ استفاده‌شده در برنامه (پوستر کتابخانه ۲۵۶، گرید جزئیات ۱۳۲)
    _ADOPT_PX = (256, 160, 132)

    def _adopt_from_other_px(self, path, grp: str, px: int) -> Path | None:
        """سایزِ درخواستی را از کشِ سایزِ بزرگ‌ترِ *همین فایل* مشتق کن (0.29.0).
        کوچک‌کردنِ ۲۵۶←۱۳۲ تمیز است؛ بزرگ‌کردن (مشتق از سایز کوچک‌تر) تار می‌شود —
        پس فقط اهداکننده‌ی بزرگ‌تر قبول است و آن‌هم کوچک‌ترینِ کافی."""
        for dpx in sorted(p for p in self._ADOPT_PX if p > px):
            try:
                d = self._cache_path(path, grp, dpx)
            except Exception as _exc_dbg:
                dbg_log(f"_adopt_from_other_px failed: {type(_exc_dbg).__name__}")
                continue
            if not d.exists():
                continue
            try:
                im = read_cache_pil(d)
                if im is None:
                    continue
                small = _cover_square(im.convert("RGB"), px)
                return self._save(small, self._cache_path(path, grp, px))
            except Exception as _exc_dbg:
                dbg_log(f"_adopt_from_other_px failed: {type(_exc_dbg).__name__}")
                continue
        return None

    # ------------------------------------------------ روش دیگر ← آخرِ کار همان ویدیو
    def _sidecar_thumb(self, path: Path, px: int) -> Image.Image | None:
        """«اگر روش دیگری بود، بهتر است از روش دیگر — فقط وقتی مجبوریم از خودِ
        ویدیو» (درخواست کاربر، 0.29.0): برای ویدیویی که فریمِ سالمی نمی‌دهد، ابتدا
        تصاویرِ کنار فایل را امتحان کن: تصویرِ هم‌نام (movie.jpg کنار movie.mkv)،
        سپس نام‌های پوستری (cover/poster/folder/…) در همان پوشه. فقط اگر هیچ‌کدام
        نبود، خودِ ویدیو آخرین راه است."""
        try:
            d = path.parent
            names = [path.stem] + [n for n in _COVERISH if n != path.stem.lower()]
            for n in names:
                for e in sorted(IMAGE_EXTS_SET):
                    try:
                        cand = d / (n + e)
                        if cand.exists():
                            return _load_cover_image(cand, px)
                    except Exception as _exc_dbg:
                        dbg_log(f"_sidecar_thumb failed: {type(_exc_dbg).__name__}")
                        continue
        except Exception as _exc_dbg:
            dbg_log(f"_sidecar_thumb failed: {type(_exc_dbg).__name__}")
            pass
        return None

    def video_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """تامبنیل واقعی ویدیو — *بدون* فال‌بک به کارت نوع‌فایل (برای پوستر عناوین)."""
        return self._probe_cached(path, px, "video", self._video_thumb, force=force)

    def pdf_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """صفحه‌ی اول PDF به‌عنوان تامبنیل (تامبنیل Shell ویندوز) — کش + کش منفی.

        یک پل از زنجیره‌ی پوستر عناوین (درخواست کاربر): «PDF داخل پوشه ← قبل از فشرده».
        اگر سیستم هندلر PDF نداشته باشد None می‌دهد تا پل بعدی (فشرده) امتحان شود."""
        if Path(str(path)).suffix.lower() != ".pdf":
            return None
        return self._probe_cached(path, px, "doc", self._pdf_thumb, force=force)

    def _pdf_thumb(self, path: Path, px: int) -> Image.Image | None:
        # ۱) تامبنیل Shell ویندوز (سریع — وقتی PDF-reader هندلر نصب کرده باشد)
        png = get_shell_thumbnail(path, max(px, 256))
        if png:
            try:
                with Image.open(io.BytesIO(png)) as src:
                    return _cover_square(src.convert("RGB"), px)
            except Exception as _exc_dbg:
                dbg_log(f"_pdf_thumb failed: {type(_exc_dbg).__name__}")
                pass
        # ۲) رندر واقعی صفحه‌ی اول با pypdfium2 / PyMuPDF (فال‌بک — گزارش کاربر:
        # «برای فایل‌های PDF هم تامبنیل درست شود» حتی بدون هندلر Shell ویندوز)
        return _pdf_render_thumb(path, px)

    def archive_thumb_only(self, path, px: int = 256, force: bool = False) -> Path | None:
        """تامبنیل واقعیِ محتوای فایل فشرده — *بدون* فال‌بک به کارت نوع‌فایل.

        اولین عضو مناسب داخل آرشیو: تصویر → ویدیو → PDF. در نبود/خطا None تا
        placeholder بماند. غیرفعال‌بودن تنظیم → None (بدون نوشتن مارکر منفی)."""
        if not self.archive_enabled:
            return None
        return self._probe_cached(path, px, "archive", self._archive_thumb, force=force)

    def _archive_thumb(self, path: Path, px: int) -> Image.Image | None:
        """اولین تصویر/ویدیو/PDF داخل آرشیو را خارج کرده و تامبنیلش را می‌سازد.

        دو مسیر: اگر «عضو انتخاب‌شده» از قبل کش شده باشد (دفعه‌های بعدی) بدون
        لیست‌خواندن اعضا مستقیم همان عضو خارج می‌شود — روی آرشیوهایی با
        هزاران عضو این لیست‌خواندن چند ثانیه بود (گزارش کاربر: کندی پوشه‌های
        فقط‌فشرده). شکست مسیر مستقیم → کش دوران‌دازه و مسیر کامل.
        """
        path = Path(path)
        self._tls.arc_soft = False            # پیش‌فرض؛ پایین در صورت نیاز ست می‌شود
        pick = self._arc_pick_get(path)
        if pick:
            im = self._archive_member_thumb(path, pick["n"], pick["k"], px,
                                            size=pick.get("s", 0))
            if im is not None:
                return im
            self._arc_pick_drop(path)         # کش کهنه/خراب — مسیر کامل از نو
        opened = _arc_open(path)
        if not opened:
            return None
        handle, members, _op = opened
        try:
            # به‌جای «یک کاندید» چند کاندید — اگر اولی خراب/رمزدار بود
            # سراغ بعدی می‌رویم (گزارش کاربر: «7zهای پر از تصویر بدون تامبنیل»).
            cands = _pick_arc_members(members)
            for name, size, kind in cands:
                im = self._archive_member_thumb(path, name, kind, px,
                                                opened=opened, size=size)
                if im is not None:
                    self._arc_pick_put(path, name, kind, size)   # دفعه‌ی بعد بدون لیست‌خواندن
                    return im
            # گزارش کاربر: «یکی از سه آرشیو تامبنیل نگرفت» — شکستِ پس از
            # یافتنِ کاندید تقریباً همیشه گذراست (تایم‌اوتِ استخراج روی درایو
            # کند، رقابتِ I/O فاز موازی، ffmpeg که زیر فشار سرسام نزد) — مارکر
            # منفیِ چسبنده ننویس تا اجرای بعدِ برنامه دوباره تلاش کند. در همین
            # اجرا، قفل در-پرواز تکرارِ هم‌زمان را جمع می‌کند.
            if cands:
                self._tls.arc_soft = True
                FileThumbs._archive_soft_fails += 1
                try:
                    FileThumbs._archive_soft_files.add(str(path))
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_thumb failed: {type(_exc_dbg).__name__}")
                    pass
            return None
        finally:
            try:
                handle.close()
            except Exception as _exc_dbg:
                dbg_log(f"_archive_thumb failed: {type(_exc_dbg).__name__}")
                pass

    def _archive_member_thumb(self, path: Path, name: str, kind: str, px: int,
                              opened=None, size: int = 0) -> Image.Image | None:
        """استخراج یک عضو مشخص از آرشیو و ساخت تامبنیلاش (تصویر/ویدیو/PDF).

        opened=(handle,members,opener) از _arc_open باشد، بستن هندل با صدا‌زننده است؛
        وگرنه خودش با _arc_direct (بدون لیست‌خواندن) باز و می‌بندد.

        0.34.0: تایم‌اوتِ استخراج برای ویدیو بر اساس حجمِ عضو سازش‌پذیر است — یک
        قسمت‌فیلمِ چندگیگابایتی داخل zip روی درایو خارجی بیش از ۲۴۰ثانیه استخراج
        می‌خواست و بی‌صدا شکست می‌خورد (همان «یکی از سه آرشیو»‌ی گزارش‌کاربر).
        تخمین: حداقل ۳۰۰ثانیه و بعد ~۸MB/s محافظه‌کارانه تا سقف ۲۰دقیقه.
        """
        own = None
        if opened is not None:
            opener = opened[2]
        else:
            d = _arc_direct(path)
            if not d:
                return None
            own, opener = d
        if kind == "video":
            tout = 300 if size <= 0 else max(300, min(1200, size // (8 * 1024 * 1024)))
        else:
            tout = 240
        tmp = self.dir / ("ax_" + hashlib.sha1(str(path).encode()).hexdigest()[:12]
                          + Path(name.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower())
        try:
            try:
                opener(name, tmp, timeout=tout)
            except Exception as _exc_dbg:
                dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                return None
            if not (tmp.exists() and tmp.stat().st_size > 0):
                return None
            if kind == "image":
                try:
                    return _load_cover_image(tmp, px)   # با draft برای JPEG
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                    return None
            if kind == "video":
                return self._video_thumb(tmp, px)
            # pdf → همان مسیر مشترک صفحه‌ی اول (تامبنیل Shell ویندوز + pypdfium2)
            return self._pdf_thumb(tmp, px)
        finally:
            tmp.unlink(missing_ok=True)
            if own is not None:
                try:
                    own.close()
                except Exception as _exc_dbg:
                    dbg_log(f"_archive_member_thumb failed: {type(_exc_dbg).__name__}")
                    pass

    # ---------------------------------------------------------------- ویدیو
    def _video_thumb(self, path: Path, px: int) -> Image.Image | None:
        # گزارش کاربر: «مثل خودِ اکسپلورر بلافاصله» — سه مرحله به
        # ترتیب سرعت — ۱) کشِ آماده‌ی ویندوز (چند میلی‌ثانیه، همان چیزی که
        # اکسپلورر وقتی «آنی» است می‌خواند)؛ ۲) رندرِ Shell ویندوز (صدهای
        # میلی‌ثانیه و کشِ سیستم را هم برای دفعات بعد پر می‌کند)؛ ۳) ffmpeg بهینه
        # (فقط کلید‌فریم، بدون هدر وقتی مدت لازم نباشد) به‌عنوان فال‌بک نهایی.
        # دنده‌های Shell فقط تا وقتی امتحان می‌شوند که حداقل یک‌بار در این
        # اجرا جواب داده باشند — روی سیستمِ گزارش‌دهنده (بدون هندلر MKV) هر
        # فراخوانِ رندر صدها میلی‌ثانیه‌ی هدررفتِ خالص بود.
        # (راهنما۳): یادگیری پر-پسوند — وقتی برای MKV هندلری نیست، MP4 هم
        # نباید قربانی شود (کشِ ویندوز برای MP4 وطنی است). هر پسوند جدا یاد
        # می‌گیرد؛ پسوندِ بی‌فایده پس از چند میسِ پیاپی کنار گذاشته می‌شود.
        ext = path.suffix.lower()
        if self._shell_for_video_alive() and self._shell_for_ext_alive(ext):
            for gear in (True, False):        # True=فقط کش آماده، False=رندر واقعی
                png = get_shell_thumbnail(path, max(px, 256), cached_only=gear)
                if png:
                    try:
                        with Image.open(io.BytesIO(png)) as src:
                            im = _cover_square(src.convert("RGB"), px)
                        self._shell_ext_note(ext, True)
                        return im
                    except Exception as _exc_dbg:
                        dbg_log(f"_video_thumb failed: {type(_exc_dbg).__name__}")
                        pass
            self._shell_ext_note(ext, False)  # هر فایل یک رأی میس (نه به‌ازای هر دنده)
        # درخواست کاربر: «اگر روش دیگری بود، از روش دیگر — فقط وقتی مجبوریم
        # از خودِ ویدیو» — تصویرِ کناری/هم‌نام به شاتِ اضطراریِ ffmpeg ترجیح دارد.
        if self.ffmpeg:
            return self._ffmpeg_thumb(path, px, last_resort=self._sidecar_thumb)
        return self._sidecar_thumb(path, px)   # بدون ffmpeg: روشِ دیگر، تنها راهِ باقی‌مانده

    def _ffmpeg_thumb(self, path: Path, px: int,
                      last_resort=None) -> Image.Image | None:
        # هدف کاربر: «تامبنیل ویدیو به تندیِ تامبنیل تصویر» — سه تنگنای
        # واقعیِ نسخه‌ی قبلی برداشته شدند (راهنما۲، تحلیلِ روی کدِ ما):
        #  ۱) ffmpeg بدون -threads روی هر فرآیند چندترد می‌گرفت؛ با ۱۶ ورکرِ
        #     موازی CPU چند برابر فروکرایه می‌شد → حالا «-threads 1»: هر فرآیند
        #     دقیقاً یک هسته؛ ۱۶ فرآیند = ۱۶ ترد — دیگر گلوگاه‌سازی داخلی نداریم.
        #  ۲) هر شات یک JPG روی دیسک می‌نوشت و دوباره می‌خواند → حالا خروجی
        #     با image2pipe مستقیم روی stdout استریم و در حافظه دکود می‌شود؛
        #     هیچ فایل tempی در کار نیست.
        #  ۳) پروبِ بزرگ ffmpeg برای یک فریم اضافی بود → probesize 32k /
        #     analyzeduration 0 و تایم‌اوتِ هر شات از ۲۰ به ۸ ثانیه.
        # سقفِ سخت: حداکثر ۲ فرآیند برای هر ویدیو (حالت معمول = دقیقاً ۱).
        FileThumbs.ffmpeg_video_uses += 1        # شمارش برای خلاصه‌ی پیش‌ساخت
        kwargs = hidden_kwargs(8)                # 20s→8s — شاتِ گیرکرده زودتر می‌میرد
        scale = f"scale={max(px, 256)}:-2:flags=fast_bilinear"

        def _decode(buf: bytes) -> Image.Image | None:
            if not buf:
                return None
            try:
                with Image.open(io.BytesIO(buf)) as src:
                    return src.convert("RGB")
            except Exception as _exc_dbg:
                dbg_log(f"_ffmpeg_thumb._decode failed: {type(_exc_dbg).__name__}")
                return None

        def _run(argv_extra: list) -> tuple:
            """یک اجرای ffmpeg → (تصویرِ کاور|None، وضعیت).
            وضعیت: ok (تصویر سالم) / black (فریم سیاه) / empty (خروجی‌ای نداد).
            0.38.0: مسیرِ GPU (-hwaccel) حذف شد — برای استخراجِ یک فریم با سیکِ
            سریع، دکودِ CPU سریع‌تر و پایدارتر است (گزارش کاربر + راهنما)."""
            try:
                proc = subprocess.run([
                    self.ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-probesize", "32k", "-analyzeduration", "0",
                    *argv_extra,
                    "-i", str(path), "-frames:v", "1", "-threads", "1",
                    "-an", "-sn", "-dn", "-vf", scale,
                    "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                ], **kwargs)
            except Exception as _exc_dbg:
                dbg_log(f"_ffmpeg_thumb._run failed: {type(_exc_dbg).__name__}")
                return None, "empty"
            im = _decode(proc.stdout)
            if im is None:
                return None, "empty"
            if _is_black(im):
                return None, "black"
            return _cover_square(im, px), "ok"

        def _shot(ss: str) -> tuple:
            """یک فریم از نقطه‌ی ss — فقط کلید‌فریم (skip_frame nokey)."""
            return _run(["-ss", ss, "-noaccurate_seek", "-skip_frame", "nokey"])

        def _rescue() -> Image.Image | None:
            """پس‌گذرِ اضطراری: دکودِ واقعی از ابتدای فایل (بدون سیک و بدون
            skip_frame) — برای فایل‌های ناقص/خراب که سیکِ کلید‌فریم خروجی‌ای
            نمی‌دهد؛ حداکثر یک‌بار و فقط بعد از شکستِ شاتِ اول."""
            im, _ = _run([])
            return im

        # مدت از خواندنِ درون‌فرآیندیِ هدرِ کانتینر (fast_duration — چند
        # میلی‌ثانیه، بدون هیچ subprocessای؛ MKV/MP4/AVI/ASF را پوشش می‌دهد).
        dur = None
        try:
            dur = fast_duration(path)
        except Exception as _exc_dbg:
            dbg_log(f"_ffmpeg_thumb failed: {type(_exc_dbg).__name__}")
            dur = None
        if dur and dur >= 20:
            primary, alt = f"{dur * 0.5:.1f}", f"{dur * 0.22:.1f}"
        elif dur and dur > 0:
            primary, alt = f"{max(dur * 0.4, 0.5):.1f}", None
        else:
            primary, alt = _FFMPEG_FALLBACK_SEEKS
        # مسیرِ GPU حذف شد (به تصمیمِ کاربر) — دکودِ CPU مستقیم.
        im, st = _shot(primary)             # مسیرِ CPUِ همیشه‌جواب (همان 0.28.0+)
        if im is not None:
            return im
        if st == "black" and alt is not None:
            im2, _ = _shot(alt)            # فریم سیاه → یک نقطه‌ی دیگر؛ جمعاً ۲ فرآیند
            if im2 is not None:
                return im2
            # دو نقطه‌ی متفاوت سیاه/خالی بود — 0.29.0: روشِ دیگر (تصویر کناری)، نه
            # دیکدِ اجباری؛ سقفِ سختِ ۲ فرآیند حفظ می‌شود.
            return last_resort(path, px) if last_resort is not None else None
        # خروجی نداد (یا سیاهِ بی‌جایگزین) → 0.29.0: اول روشِ دیگر؛ فقط اگر هیچ
        # تصویرِ کناری‌ای نبود، آخرین راه = دیکدِ اجباری از همان ویدیو (مجبوریم).
        if last_resort is not None:
            im3 = last_resort(path, px)
            if im3 is not None:
                return im3
        return _rescue()

    # ---------------------------------------------------------------- کارت نوع‌فایل
    def _type_card(self, path: Path, grp: str, px: int) -> Image.Image:
        """تامبنیل نوع‌فایل = فقط خودِ آیکونِ دانلودشده، هیچ چیزِ دیگر.

        0.35.0 (درخواست کاربر: «فقط همون تصویرها رو نشون بده، قاب رنگی و نوشته
        نمی‌خوام»): به‌جای کارتِ قاب‌دار 0.34.0 (سند+برچسب+نشان پسوند)، تامبنیل
        فقط آیکونِ واقعیِ پسوند است که تمام‌صفحه (contain) در قابِ px می‌نشیند
        — تمیز و نزدیک به حسِ آیکون‌های خودِ اکسپلورر. اگر فایلِ آیکونی در
        دسترس نبود، همان کارتِ قاب‌دارِ 0.33.0/0.34.0 به‌عنوان فالبک رسم می‌شود.
        """
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            grp = "sub"
        ext = path.suffix.lstrip(".").upper()[:6] or "FILE"
        key = (grp, ext, px)
        img = _TC_CACHE.get(key)
        if img is not None:
            return img
        ico = _load_card_icon(_card_icon_name(suf, grp))
        if ico is None:                       # فالبکِ همیشگی — کارتِ قاب‌دارِ قبلی
            return self._type_card_framed(path, grp, px)
        # آیکون‌ها مربعِ سفیدزمینه‌اند (۵۱۲px با حاشیه‌ی ۶٪) — فقط ریسایز باکیفیت
        img = ico.convert("RGB").resize((px, px), Image.Resampling.LANCZOS)
        if len(_TC_CACHE) >= 160:
            _TC_CACHE.clear()
        _TC_CACHE[key] = img
        return img

    def _type_card_framed(self, path: Path, grp: str, px: int) -> Image.Image:
        """کارتِ قاب‌دارِ 0.34.0 (سند+آیکون+نشان) — فقط فالبکِ نبودِ آیکون.

        کارتِ نوع‌فایلِ طراحی‌شده (0.34.0: آیکون واقعیِ دانلودشده از پک متن‌باز).

        درخواست کاربر: «آیکون دانلود کن از اینترنت — پکی که به تم برنامه بخوره
        مثل flaticon / vscode-icons / catppuccin». چون آن سایت‌ها اسکریپ‌ناپذیرند
        (کپچا/ضدربات)، ۳۰ آیکون رسمیِ پک متن‌باز Material Icon Theme از مخزنِ
        گیت‌هاب گرفته و داخل assets/icons/ همراه برنامه شده — همان حسِ بصریِ
        پک‌های خواسته‌شده، بدون هیچ دانلودِ لحظه‌ای (صفر پیش‌نیاز، کاملاً آفلاین).

        چیدمان: آیکنِ *سند* با گوشه‌ی تا‌شده + آیکون واقعیِ پسوند/گروه در مرکزِ
        سند (مارِ پایتون برای .py، حبابِ زیرنویس برای .srt، …؛ نگاشت کامل در
        _EXT_ICON/_GRP_ICON) + نشانِ پسوند در نوارِ تیره‌ی پایین. اگر فایلِ
        آیکونی در دسترس نباشد، گلیفِ برداری (_draw_card_glyph) فال‌بک است."""
        suf = path.suffix.lower()
        if suf.lstrip(".") in _SUBTITLE_EXTS:
            grp = "sub"
        c1, c2, label = GROUP_STYLE.get(grp, GROUP_STYLE["other"])
        c1 = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
        c2 = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
        ext = path.suffix.lstrip(".").upper()[:6] or "FILE"
        key = (grp + "|f", ext, px)
        img = _TC_CACHE.get(key)
        if img is not None:
            return img
        img = _grad_bg(c1, c2, px)
        d = ImageDraw.Draw(img)
        ac = c2                                        # لهجه‌ی تیره روی سفیدِ سند
        # ---------- سند: ورق‌ی سفید با گوشه‌ی تاشده ----------
        sw, sh = int(px * 0.46), int(px * 0.52)
        x0, y0 = (px - sw) // 2, int(px * 0.105)
        r = max(3, px // 40)
        fold = max(5, int(sw * 0.26))
        d.rounded_rectangle((x0 + 2, y0 + 3, x0 + sw + 2, y0 + sh + 3), r,
                            fill=(30, 38, 52))          # سایه‌ی نرم
        d.rounded_rectangle((x0, y0, x0 + sw, y0 + sh), r, fill=(248, 250, 253))
        d.polygon([(x0 + sw - fold, y0), (x0 + sw, y0 + fold),
                   (x0 + sw - fold, y0 + fold)], fill=(199, 209, 225))
        cx, cy = x0 + sw // 2, y0 + sh // 2 + int(px * 0.012)
        g = min(sw, sh)
        lw = max(2, px // 48)
        lighten = tuple(min(255, v + 42) for v in ac)
        # ---------- آیکون واقعیِ دانلودشده (فالبک: گلیف برداری) ----------
        ico = _load_card_icon(_card_icon_name(suf, grp))
        if ico is not None:
            tgt = max(10, int(min(sw, sh) * 0.80))
            ic = ico.resize((tgt, tgt), Image.Resampling.LANCZOS)
            img.paste(ic, (cx - ic.width // 2, cy - ic.height // 2), ic)
        else:
            _draw_card_glyph(d, grp, cx, cy, g, sw, sh, x0, y0,
                             ac, lw, lighten, px)
        # ---------- برچسب گروه (کوچک، بالای نوار) ----------
        try:
            f_lab = ImageFont.load_default(size=max(7, int(px * 0.085)))
            bb = d.textbbox((0, 0), label, font=f_lab)
            d.text(((px - (bb[2] - bb[0])) / 2, px * 0.655 - bb[1]), label,
                   fill=(236, 241, 250), font=f_lab)
        except Exception as _exc_dbg:
            dbg_log(f"_type_card_framed failed: {type(_exc_dbg).__name__}")
            pass
        # ---------- نوارِ نشانِ پسوند در پایین ----------
        by = int(px * 0.755)
        badge_c = tuple(int(v * 0.52) for v in c2)
        d.rectangle((0, by, px, px), fill=badge_c)
        try:
            f_ext = ImageFont.load_default(size=max(8, int(px * 0.125)))
            bb = d.textbbox((0, 0), ext, font=f_ext)
            d.text(((px - (bb[2] - bb[0])) / 2, (by + px) / 2 - (bb[3] - bb[1]) / 2 - bb[1]),
                   ext, fill=(255, 255, 255), font=f_ext)
        except Exception as _exc_dbg:
            dbg_log(f"_type_card_framed failed: {type(_exc_dbg).__name__}")
            pass
        if len(_TC_CACHE) >= 160:
            _TC_CACHE.clear()
        _TC_CACHE[key] = img
        return img

    # ---------------------------------------------------------------- هدر بلارشده
    def hero_backdrop(self, poster_png, w: int = 1200, h: int = 420):
        poster_png = Path(poster_png)
        stem = hashlib.sha1(f"hero|{poster_png}|{w}x{h}".encode()).hexdigest()[:16]
        out = cache_file(self.dir, stem)
        if out.exists():
            return out
        try:
            with Image.open(poster_png) as src:
                im = src.convert("RGB")
            scale = max(w / im.width, h / im.height)
            im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1),
                           Image.Resampling.LANCZOS)
            x = (im.width - w) // 2
            y = (im.height - h) // 2
            im = im.crop((max(x, 0), max(y, 0), max(x, 0) + w, max(y, 0) + h))
            im = im.filter(ImageFilter.GaussianBlur(28))
            dark = Image.new("RGB", im.size, (12, 17, 26))
            im = Image.blend(im, dark, 0.55)
            return save_cache_image(im, out)
        except Exception as _exc_dbg:
            dbg_log(f"hero_backdrop failed: {type(_exc_dbg).__name__}")
            return None


# ---------------------------------------------------------------- بارگذاری ناهمزمان
if _QT:
    def load_cache_qimage(path) -> "QImage | None":
        """خواندن فایل کش به QImage — اول دیکودرِ native خودِ Qt (WebP/PNG/…)،
        و اگر Qt نشناخت، از PIL (فقط اگر قالبش پشتیبانی شود)."""
        p = Path(str(path))
        try:
            q = QImage(str(p))
            if not q.isNull():
                return q
            im = read_cache_pil(p)
            if im is None:
                return None
            im = im.convert("RGBA")
            data = im.tobytes("raw", "RGBA")
            q = QImage(data, im.width, im.height, im.width * 4,
                       QImage.Format.Format_RGBA8888)
            return q.copy()          # از بافر موقت پایتون مستقل شود
        except Exception as _exc_dbg:
            dbg_log(f"load_cache_qimage failed: {type(_exc_dbg).__name__}")
            return None

    def load_cache_pixmap(path) -> "QPixmap | None":
        """همان بالایی به‌صورت QPixmap (برای نمایش در ویجت‌ها)."""
        q = load_cache_qimage(path)
        if q is None or q.isNull():
            return None
        return QPixmap.fromImage(q)

    class ThumbSignals(QObject):
        ready = Signal(str, str, int)   # مسیر فایل، مسیر png آماده، px

    class ThumbJob(QRunnable):
        """تولید تامبنیل در رشته‌ی پس‌زمینه."""

        def __init__(self, ft: FileThumbs, path: str, px: int, signals: ThumbSignals):
            super().__init__()
            self.ft = ft
            self.path = path
            self.px = px
            self.signals = signals

        def run(self):
            # این یک درخواستِ UI است — فازهای پیش‌ساخت باید رعایتش کنند
            try:
                FileThumbs.ui_request_tick()
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                pass
            try:
                out = self.ft.get(self.path, self.px)
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                out = None
            # همیشه سیگنال — حتی با شکست (مثل ویدیوی خراب): قبلاً ساکت می‌مرد و
            # کلید کار در صف «در حال اجرا» می‌چسبید تا همیشه و کارتش placeholder می‌ماند
            try:
                self.signals.ready.emit(self.path, str(out) if out else "", self.px)
            except RuntimeError:
                pass          # برنامه/صفحه در حال بسته شدن — منبع سیگنال حذف شده
else:  # pragma: no cover
    ThumbSignals = ThumbJob = None
