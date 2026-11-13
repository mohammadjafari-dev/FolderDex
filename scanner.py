# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""اسکن کتابخانه (FolderDex).

ساختار مورد انتظار:
    <ریشه (دسته)> / <پوشه‌ی عنوان> / فایل‌ها...
    مثلاً:  D:\\انیمه / Attack on Titan / E01.mkv ...

پوستر هر عنوان از این مسیرها پیدا می‌شود (به ترتیب اولویت):
    ۱) آیکون تعریف‌شده در desktop.ini (IconResource / IconFile)
    ۲) هر فایل .ico داخل پوشه
    ۳) (اختیاری) عکس‌هایی مثل poster.jpg / cover.jpg / folder.jpg
"""
from __future__ import annotations

from i18n import T

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from watchtime import dbg_log

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m2ts", ".ts",
    ".flv", ".webm", ".mpg", ".mpeg", ".m4v",
}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus"}
IMAGE_EXTS_SET = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
# ‎.jxl حذف شد: با حذفِ پلاگین JPEG XL هیچ دیکودری برای پوستر/آیکونِ .jxl نیست —
# فهرست‌کردنش فقط placeholder خالی می‌ساخت.
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".cbz", ".cbr", ".cb7", ".epub",
                ".tgz", ".tbz2", ".txz", ".cab", ".iso"}
DOC_EXTS = {".pdf", ".txt", ".md", ".doc", ".docx", ".srt", ".ass", ".ssa", ".vtt"}
# فایل‌های «سند» که در خودِ ریشه مثل ویدیوها «شلخته» گروه‌بندی می‌شوند (درخواست کاربر:
# پوشه‌ی پر از فایل فشرده/PDF → یک کارت واحد به نام همان پوشه، نه صفر و نه ده‌ها کارت)
LOOSE_DOC_EXTS = ARCHIVE_EXTS | {".pdf"}

# باگ گزارش‌شده: «پوشه ۱۰ فایل دارد، ۹ تا نشان می‌دهد — آن یکی همان
# تامبنیل پوشه است» — فقط فایل‌های «پوسترِ رزروشده» از فهرست فایل‌ها حذف
# می‌شوند (poster/cover/folder/... و آیکون‌های .ico) چون واقعاً متادیتا هستند.
# تصویرِ فال‌بکِ معمولی (وقتی هیچ پوستر رزروی نیست و اولین عکس پوشه به‌عنوان
# تامبنیل انتخاب می‌شود) محتواست و حذفش از فهرست باگ بود.
RESERVED_COVER_STEMS = frozenset({
    "poster", "cover", "folder", "front", "thumb", "banner", "icon",
})


def is_reserved_cover(p) -> bool:
    """آیا این فایل پوسترِ رزروشده است (متادیتا، نه محتوا)؟"""
    try:
        p = Path(p)
        return p.suffix.lower() in (".ico", ".icl") or \
            p.stem.strip().lower() in RESERVED_COVER_STEMS
    except Exception as _exc_dbg:
        dbg_log(f"is_reserved_cover failed: {type(_exc_dbg).__name__}")
        return False
CODE_EXTS = {".py", ".js", ".html", ".css", ".json", ".ini", ".xml", ".bat", ".ps1"}
EXE_EXTS = {".exe", ".msi", ".bat", ".lnk"}

# درخواست کاربر — «دیده‌شده باید هوشمند باشه»: فایل‌های «قابل‌مصرف» برای
# محاسبه‌ی وضعیت تکمیلِ یک عنوان — شامل همه‌ی دسته‌ها، نه فقط ویدیویی‌ها.
#   · زیرنویس‌ها اصلاً دیده/خوانده نمی‌شوند → هرگز جزو «کل» نیستند.
#   · فشرده (zip/rar/cbz…) «خوانده/دیده» می‌شود → حساب می‌شود.
#   · سندها/تصاویر/صوت‌ها و اجرایی‌ها هم قابل‌مصرف‌اند.
#   · سایدکارهای ماشینی (nfo/xml/json/lnk/…) جزو کل نیستند تا «علامت همه»
#     همیشه به تکمیلِ واقعی برسد.
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}
WATCHABLE_EXTS = (VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS_SET | ARCHIVE_EXTS
                  | {".pdf", ".txt", ".md", ".doc", ".docx", ".rtf", ".odt",
                     ".exe", ".msi"}) - SUBTITLE_EXTS

ICON_EXTS = {".ico", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}
POSTER_NAMES = ("poster", "cover", "folder", "front", "thumbnail")
POSTER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
IGNORED_DIRS = {"$recycle.bin", "system volume information", "found.000", ".thumb_cache"}
HIDDEN_FILES = {"desktop.ini", "thumbs.db"}

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(text) -> str:
    """تبدیل ارقام به فارسی برای نمایش — 0.45.0: فقط در حالت فارسی؛ در انگلیسی ارقام لاتین.

    همین یک نقطه باعث می‌شود صدها شمارنده در رابط (و لاگ) بدون دست‌خوردنِ
    محل‌های فراخوانی دوزبانه شوند."""
    s = str(text)
    from i18n import is_rtl
    return s.translate(FA_DIGITS) if is_rtl() else s


def fa_mmss(sec: float) -> str:
    """قالب زمانِ گذشته/باقی‌مانده با ارقام فارسی: «۰۲:۱۹» و برای بیش از یک ساعت «۱:۰۰:۰۳».

    نسخه‌ی 0.23.0 (درخواست کاربر): روی نوار پیشرفت بنویسد «چقدر طول کشیده و چقدر
    مانده» — هم کاربر حس زمان دارد هم خودش هنگام تست می‌فهمد دقیقاً چقدر طول
    کشیده. ورودی منفی/نامعتبر → «۰۰:۰۰».
    """
    try:
        s = int(max(0.0, float(sec)))
    except (TypeError, ValueError):
        s = 0
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return fa(f"{h}:{m:02d}:{s:02d}")
    return fa(f"{m:02d}:{s:02d}")


# یکسان‌سازی متن فارسی/عربی برای جستجو: «ي/ى→ی»، «ك→ک»، حذف کشیده و نیم‌فاصله.
# بدون این، جستجوی «سریال» پوشه‌ی «سريال» (یِ عربی) را پیدا نمی‌کرد.
_SEARCH_CHARS = str.maketrans({
    "ي": "ی", "ى": "ی",
    "ك": "ک",
    "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "ٱ": "ا", "أ": "ا", "إ": "ا",
})


def fa_norm(text) -> str:
    """نرمال‌سازی متن برای جستجو/تطبیق (عربی→فارسی، حذف کشیده و ZWNJ، کوچک‌کردن)."""
    s = str(text or "").translate(_SEARCH_CHARS)
    # نکته‌ی 0.45.0: کاراکترهای این‌جا «معنایی»‌اند (قواعد جستجو)، نه متنِ نمایش —
    # به‌هیچ‌وجه نباید ترجمه شوند (T_ توسط بازبینی دستی برگردانده شد).
    return s.replace("ـ", "").replace("‌", " ").casefold()


def _winfix(p) -> str:
    """ویندوز: پشتیبانی از مسیرهای بلندتر از MAX_PATH (۲۶۰ کاراکتر) با پیشوند \\\\?\\.

    فقط برای فراخوانی‌های os/open استفاده می‌شود؛ در داده‌ی ذخیره‌شده مسیر خام می‌ماند.
    (round 19 §۱۰) مسیرِ UNC (‎\\\\NAS\\share\\...‎) پیشوندِ درستش ‎\\\\?\\UNC\\‎ است نه
    چسباندنِ ساده — ‎\\\\?\\\\NAS\\share‎ نامعتبر است و پیمایش/خواندن می‌شکست."""
    s = str(p)
    if sys.platform == "win32" and len(s) > 240 and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            # UNC: \\\\server\\share\\... → \\\\?\\UNC\\server\\share\\...
            return "\\\\?\\UNC\\" + s.lstrip("\\")
        return "\\\\?\\" + s
    return s


# ثابت‌های reparse-point ویندوز — ماژولِ stat فقط روی بیلدِ ویندوزیِ پایتون
# آن‌ها را دارد (stat.FILE_ATTRIBUTE_REPARSE_POINT / IO_REPARSE_TAG_*)، پس
# عددی نوشته می‌شوند تا همین فایل روی لینوکس هم ایمپورت‌پذیر بماند (round 23).
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400      # بیتِ st_file_attributes
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003   # junction — تگِ symlink
                                           # (0xA000000C) عمداً junction نیست


def _is_junction(path) -> bool:
    """ویندوز: junction/reparse-point — پیمایش نباید داخلش برود (round 19 §۱۶).

    os.path.isjunction فقط از پایتون ۳٫۱۲ هست؛ در ۳٫۱۱ غایب است، پس فال‌بکِ
    ویندوزی با os.lstat خودمان تشخیص می‌دهیم: بیتِ REPARSE_POINT + تگِ
    MOUNT_POINT. تگِ symlink (0xA000000C) عمداً junction حساب نمی‌شود —
    symlink را followlinks=False خودِ os.walk کنترل می‌کند. هر خطایی → False:
    این گارد هیچ‌وقت نباید اسکن را بکشد (round 23 — روی ویندوزِ ۳٫۱۱
    فراخوانیِ getattrِ خالی TypeError می‌داد و کلِ اسکن می‌مرد)."""
    if sys.platform != "win32":
        return False
    try:
        isj = getattr(os.path, "isjunction", None)
        if callable(isj):                       # پایتون ۳٫۱۲+ — مسیرِ سریعِ API
            return bool(isj(str(path)))
        # پایتون ۳٫۱۱: تشخیص با lstat (st_reparse_tag از ۳٫۸ روی ویندوز هست)
        st = os.lstat(str(path))
        attrs = getattr(st, "st_file_attributes", 0)
        tag = getattr(st, "st_reparse_tag", 0)
        return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT) \
            and tag == _IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def natural_key(text):
    """کلید مرتب‌سازی طبیعی: E2 قبل از E10 می‌آید."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", str(text))]


def human_size(n: int) -> str:
    if n >= 1 << 30:
        return T("{0:.1f} گیگابایت").format(n / (1 << 30))
    if n >= 1 << 20:
        return T("{0:.1f} مگابایت").format(n / (1 << 20))
    if n >= 1 << 10:
        return T("{0:.0f} کیلوبایت").format(n / (1 << 10))
    return T("{0} بایت").format(n)


def file_icon(path: Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "🎬"
    if ext in AUDIO_EXTS:
        return "🎵"
    if ext in IMAGE_EXTS_SET:
        return "🖼"
    if ext in ARCHIVE_EXTS:
        return "🗜"
    if ext in DOC_EXTS:
        return "📄"
    if ext in CODE_EXTS:
        return "💻"
    if ext in EXE_EXTS:
        return "⚙"
    return "📦"


@dataclass
class MediaItem:
    name: str
    path: Path
    category: str
    icon_path: Path | None = None
    file_count: int = 0
    video_count: int = 0
    size_bytes: int = 0
    modified: float = 0.0   # زمان آخرین تغییر پوشه
    preview_video: Path | None = None  # ویدیویی که تامبنیلش جایگزین پوستر می‌شود (بدون آیکون)
    preview_pdf: Path | None = None    # PDF ای که صفحه‌ی اولش جایگزین پوستر می‌شود (بدون تصویر/ویدیو)
    preview_archive: Path | None = None  # فشرده‌ای که محتوایش جایگزین پوستر می‌شود (بدون تصویر/ویدیو/PDF)
    loose_group: bool = False          # گروه «فایل‌های مستقیمِ ریشه» در یک کارت واحد
    loose_docs_only: bool = False      # گروه ریشه‌ی مخصوص سندها: فقط فشرده/PDF نمایش داده شود
    dom_type: str = ""                 # نوعِ غالب محتوا: video/image/archive/doc/audio/other (0.23.0)

    @property
    def size_text(self) -> str:
        return human_size(self.size_bytes)

    @property
    def info_text(self) -> str:
        # round 33 (پرسش کاربر: «کاور را نمی‌شمارد، ولی نشان می‌دهد — همین‌جوری
        # خوبه؟») — بله، عمدی است: برای پوشه‌ی ویدیویی عددِ معنادار «قسمت» است و
        # تصویرِ کاور یک قسمت نیست؛ اگر در عدد شمرده شود «۳ قسمت» دروغ می‌شود.
        # کاور از file_count حذف *نشده* (مرتب‌سازیِ «تعداد فایل‌ها» و پوشه‌ی
        # بی‌ویدیو همان را نشان می‌دهند) و در صفحه‌ی جزئیات هم در فهرست است.
        if self.video_count:
            return T("{0} قسمت • {1}").format(fa(self.video_count), self.size_text)
        return T("{0} فایل • {1}").format(fa(self.file_count), self.size_text)

    @property
    def info_short(self) -> str:
        if self.video_count:
            return f"🎬 {fa(self.video_count)} · {self.size_text}"
        return f"📦 {fa(self.file_count)} · {self.size_text}"


# ---------------------------------------------------------------- desktop.ini

def _read_desktop_ini(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore").replace("\x00", "")
    for enc in ("utf-8-sig", "cp1256", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


def _resolve_icon_ref(ref: str, folder: Path) -> Path | None:
    ref = ref.strip().strip('"')
    if not ref:
        return None
    if "," in ref:  # جدا کردن ایندکس انتهایی مثل C:\x\icon.ico,0
        head, _, tail = ref.rpartition(",")
        if tail.strip().lstrip("-").isdigit():
            ref = head
    ref = os.path.expandvars(ref.strip().strip('"'))
    p = Path(ref)
    candidates = [p] if p.is_absolute() else [folder / ref]
    for c in candidates:
        try:
            if c.is_file() and c.suffix.lower() in ICON_EXTS:
                return c
        except OSError:
            continue
    return None


def find_desktop_ini_icon(folder: Path) -> Path | None:
    ini = folder / "desktop.ini"
    if not ini.is_file():
        return None
    try:
        text = _read_desktop_ini(ini)
    except OSError:
        return None
    icon_resource = icon_file = None
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith(("[", ";")):
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key == "iconresource":
            icon_resource = value
        elif key == "iconfile":
            icon_file = value
    for ref in (icon_resource, icon_file):
        p = _resolve_icon_ref(ref or "", folder)
        if p:
            return p
    return None


def find_loose_icon(folder: Path) -> Path | None:
    try:
        icos = sorted(p for p in folder.glob("*.ico") if p.is_file())
    except OSError:
        return None
    if not icos:
        return None
    low = folder.name.lower()
    for key in (low, "icon", "folder", "poster", "cover"):
        for ico in icos:
            if key and key in ico.stem.lower():
                return ico
    return icos[0]


def find_poster_image(folder: Path) -> Path | None:
    for stem in POSTER_NAMES:
        for ext in POSTER_EXTS:
            p = folder / (stem + ext)
            if p.is_file():
                return p
    return None


def find_any_image(folder: Path, deep: bool = False, _breadcrumb: set | None = None) -> Path | None:
    """هر تصویری (نه فقط poster/cover): داخل خود پوشه، و در حالت deep یک سطح پایین‌تر.

    برای پوشه‌های بدون آیکون: اولین عکس موجود همان به‌صورت پوستر نمایش داده می‌شود
    (درخواست کاربر — «اگه تصویری تو پوشه هست اونو نشون بده»).
    """
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_file() and e.suffix.lower() in IMAGE_EXTS_SET:
                return e
            if deep and e.is_dir() and e.name.lower() not in IGNORED_DIRS:
                subs.append(e)
        except OSError:
            continue
    if deep:  # فقط یک سطح — جست‌وجوی عمیق هزینه‌بر و ناخواسته است
        for sub in subs[:24]:
            img = find_any_image(sub, deep=False)
            if img:
                return img
    return None


def find_preview_video(folder: Path) -> Path | None:
    """اولین ویدیو برای ساخت تامبنیل در نبودِ هیچ تصویری (خود پوشه + یک سطح زیرپوشه)."""
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        try:
            if e.is_file() and e.suffix.lower() in VIDEO_EXTS:
                return e
            if e.is_dir() and e.name.lower() not in IGNORED_DIRS and not e.name.startswith("."):
                subs.append(e)
        except OSError:
            continue
    for sub in subs[:24]:
        try:
            vids = sorted((p for p in sub.iterdir()
                           if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
                          key=lambda p: natural_key(p.name))
        except OSError:
            continue
        if vids:
            return vids[0]
    return None


def find_loose_video_files(folder: Path) -> list[Path]:
    """فایل‌های ویدیوییِ مستقیم (غیربازگشتی) یک پوشه، به ترتیب طبیعی."""
    try:
        out = [p for p in folder.iterdir()
               if p.is_file() and not p.name.startswith(".")
               and p.suffix.lower() in VIDEO_EXTS]
    except OSError:
        return []
    out.sort(key=lambda p: natural_key(p.name))
    return out


def find_preview_pdf(folder: Path) -> Path | None:
    """اولین PDF برای ساخت تامبنیل (خود پوشه + یک سطح زیرپوشه) — قبل از فال‌بک فشرده."""
    subs: list[Path] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return None
    for e in entries:
        try:
            if e.is_file() and e.suffix.lower() == ".pdf":
                return e
            if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in IGNORED_DIRS:
                subs.append(e)
        except OSError:
            continue
    for sub in subs[:24]:
        try:
            pdfs = sorted((p for p in sub.iterdir()
                           if p.is_file() and p.suffix.lower() == ".pdf"),
                          key=lambda p: natural_key(p.name))
        except OSError:
            continue
        if pdfs:
            return pdfs[0]
    return None


# ترجیح خوانایی آرشیو: خانواده‌ی ZIP/TAR بومی پایتون است؛ RAR/7z به ابزار/پکیج نیاز دارند
# (7za.exe همراه برنامه همه‌ی این‌ها را می‌خواند — تنظیمات کاربر لازم نیست)
_ARC_PREF = {".zip": 0, ".cbz": 0, ".epub": 0, ".tar": 1, ".gz": 1, ".tgz": 1,
             ".tbz2": 1, ".txz": 1, ".rar": 2, ".cbr": 2, ".7z": 3, ".cb7": 3,
             ".cab": 3, ".iso": 4}


def _arc_entry(folder: Path) -> list[Path]:
    try:
        return [p for p in folder.iterdir()
                if p.is_file() and not p.name.startswith(".")
                and p.suffix.lower() in _ARC_PREF]
    except OSError:
        return []


def find_preview_archive(folder: Path) -> Path | None:
    """اولین فایل فشرده‌ی قابل‌استفاده برای تامبنیل — برای پوشه‌هایی که نه عکس دارند
    نه ویدیو (خود پوشه + یک سطح زیرپوشه؛ مثل بقیه‌ی زنجیره‌ی فال‌بک)."""
    cands = _arc_entry(folder)
    if not cands:
        subs: list[Path] = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        except OSError:
            return None
        for e in entries:
            try:
                if e.is_dir() and not e.name.startswith(".") and e.name.lower() not in IGNORED_DIRS:
                    subs.append(e)
            except OSError:
                continue
        for sub in subs[:24]:
            cands.extend(_arc_entry(sub))
            if cands:
                break
    if not cands:
        return None
    cands.sort(key=lambda p: (_ARC_PREF[p.suffix.lower()], natural_key(p.name)))
    return cands[0]


def find_icon(folder: Path, include_image_fallback: bool = True) -> Path | None:
    """زنجیره‌ی اولویت پوستر: desktop.ini → هر .ico → poster/cover → هر تصویر (عمق ۱)."""
    icon = find_desktop_ini_icon(folder) or find_loose_icon(folder)
    if icon is None and include_image_fallback:
        icon = (find_poster_image(folder)
                or find_any_image(folder, deep=True))
    return icon


# ------------------------------------------------------------------ آمار و درخت

def file_type_group(name) -> str:
    """دسته‌ی کلیِ یک فایل از روی پسوند — برای مرتب‌سازی «بر اساس نوع فایل» (0.23.0).

    نکته‌ی کاربر: اسم فایل می‌تواند هر زبانی باشد؛ همه‌چیز فقط روی «پسوند»
    کار می‌کند و خودِ نام هیچ نقشی ندارد.
    """
    ext = Path(name).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS_SET:
        return "image"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in DOC_EXTS:
        return "doc"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


# ترتیب دسته‌ها برای گزینه‌ی «نوع فایل/نوع محتوا» — هم صفحه‌ی اصلی هم جزئیات
TYPE_ORDER = ("video", "image", "archive", "doc", "audio", "other")


def gather_stats(folder: Path) -> tuple[int, int, int, dict]:
    """(تعداد فایل، تعداد ویدیو، حجم، شمارش هر نوع) — شمارش نوع‌ها برای
    «نوع غالب محتوا» در مرتب‌سازی صفحه‌ی اصلی استفاده می‌شود (0.23.0) و چون از
    همان پیمایش همیشگیِ پوشه می‌آید هزینه‌ی اضافی تقریباً صفر است.

    round 32 (گزارش کاربر: «صفحه‌ی اصلی می‌گوید ۱ فایل، جزئیات می‌گوید ۰ فایل»):
    فیلترِ فایل‌ها دقیقاً همان فیلترِ get_file_tree شد — فایل‌های نقطه‌ای و
    سیستمیِ ویندوز (desktop.ini/thumbs.db) از شمارش و حجم حذف شدند تا
    شمارنده‌ی کارت و شمارنده‌ی صفحه‌ی جزئیات همیشه از یک قانون بیایند.
    (تصویرِ کاور دیگر از فهرستِ جزئیات حذف نمی‌شود، پس هر دو یک عدد می‌گویند.)"""
    files = videos = size = 0
    type_counts = {"video": 0, "image": 0, "archive": 0,
                   "doc": 0, "audio": 0, "other": 0}
    # _winfix: مسیرهای خیلی بلند ویندوز (>۲۴۰) هم با پیشوند \\?\ خوانده شوند
    for _root, dirs, names in os.walk(_winfix(folder)):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS
                   and not d.startswith(".")
                   and not _is_junction(os.path.join(_root, d))]   # round 19 §16
        for name in names:
            if name.startswith(".") or name.lower() in HIDDEN_FILES:
                continue                       # round 32 — هماهنگ با get_file_tree
            files += 1
            try:
                size += (Path(_root) / name).lstat().st_size
            except OSError:
                pass
            ext = Path(name).suffix.lower()
            type_counts[file_type_group(name)] += 1
            if ext in VIDEO_EXTS:
                videos += 1
    return files, videos, size, type_counts


def dominant_type(type_counts: dict) -> str:
    """نوعِ غالب از روی شمارش‌ها؛ تعارض به ترتیب TYPE_ORDER حل می‌شود."""
    best, best_n = "", 0
    for t in TYPE_ORDER:
        n = int((type_counts or {}).get(t, 0) or 0)
        if n > best_n:
            best, best_n = t, n
    return best or "other"


def _direct_files(path: Path, exclude: set[str], only_exts=None) -> list[Path]:
    """فایل‌های مستقیم یک پوشه (غیربازگشتی)، بدون مخفی/سیستمی — مرتب طبیعی.

    only_exts: اگر داده شود فقط فایل‌هایی با آن پسوندها (مثل LOOSE_DOC_EXTS برای
    گروهِ اسنادِ ریشه — تا ویدیوهایی که کارت جدای خودشان را دارند تکرار نشوند).
    """
    try:
        out = [
            p for p in Path(path).iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.name.lower() not in HIDDEN_FILES
            and str(p) not in exclude
            and (only_exts is None or p.suffix.lower() in only_exts)
        ]
    except OSError:
        return []
    out.sort(key=lambda p: natural_key(p.name))
    return out


def get_file_tree(path: Path, exclude: set[Path] | None = None, loose: bool = False,
                  only_exts=None):
    """فایل‌های یک عنوان را به‌صورت بخش‌بندی‌شده برمی‌گرداند.

    خروجی: لیستی از {"title": str, "files": [Path, ...]} که فایل‌ها و بخش‌ها
    به‌ترتیب طبیعی مرتب شده‌اند. پوشه‌های تودرتو هر کدام یک بخش می‌شوند.
    loose=True → فقط فایل‌های مستقیم (برای گروه «فایل‌های شلخته‌ی ریشه»).
    only_exts → در حالت loose فقط این پسوندها (گروه اسناد: فشرده/PDF).
    """
    path = Path(path)
    exclude = {str(p) for p in (exclude or set())}
    if path.is_file():
        return [{"title": T("فایل"), "files": [path]}]   # عنوان نمایشی گروه (کلید نیست)
    if loose:
        return [{"title": T("فایل‌ها"), "files": _direct_files(path, exclude, only_exts)}]

    sections: list[dict] = []
    for root, dirs, names in os.walk(path):
        dirs[:] = sorted(
            (d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")
             and not _is_junction(os.path.join(root, d))),      # round 19 §16
            key=natural_key,
        )
        files = [
            Path(root) / n for n in names
            if n.lower() not in HIDDEN_FILES
            and str(Path(root) / n) not in exclude
            and not n.startswith(".")
        ]
        files.sort(key=lambda p: natural_key(p.name))
        if not files:
            continue
        rel = os.path.relpath(root, path)
        title = T("فایل‌های اصلی") if rel == "." else rel
        sections.append({"title": title, "files": files})

    sections.sort(key=lambda s: (s["title"] != T("فایل‌های اصلی"), natural_key(s["title"])))
    return sections


def thumb_manifest(path, loose: bool = False):
    """اثر انگشتِ محتوای یک عنوان + فهرستِ تختِ فایل‌هایش (0.42.0 — ردکردنِ هوشمند).

    درخواست کاربر: «وقتی فایل پشتیبان کامله نباید دوباره بررسی کنه — فقط وقتی
    فایلی تغییری کرده باشه». مانیفست = نقشه‌ی {مسیرِ نسبی: [size, mtime_sec]}
    (همان الگوی مانیفستِ زمان‌ها). با مانیفستِ ذخیره‌شده مقایسه می‌شود؛ برابر
    یعنی هیچ فایلی اضافه/حذف/ویرایش نشده و پیش‌ساز بدون هیچ کارِ اضافی رد می‌شود.

    os.scandir روی ویندوز stat را از خودِ enumeration می‌دهد، پس این مرحله فقط
    هزینه‌ی «لیست‌کردنِ پوشه‌ها» را دارد — نه خواندنِ اضافی از دیسک؛ خروجیِ
    دوم (فهرست تخت) هم برای همان اجرا نگه داشته می‌شود تا get_file_tree دوباره
    راه نیفتد. قواعدِ فیلتر دقیقاً مثل get_file_tree: نادیده‌گرفتنِ پوشه‌های
    IGNORED_DIRS/مخفی/نقطه‌ای و فایل‌های HIDDEN_FILES/نقطه‌ای.
    """
    path = Path(path)
    man: dict = {}
    files: list = []
    if path.is_file():
        try:
            st = path.stat()
            man[path.name] = [st.st_size, int(st.st_mtime_ns)]   # ns (round 19 §14)
        except OSError:
            pass
        return man, [path]

    def _note(e, rel: str):
        if e.name.startswith(".") or e.name.lower() in HIDDEN_FILES:
            return
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            return
        man[rel + e.name] = [st.st_size, int(st.st_mtime_ns)]   # ns (round 19 §14)
        files.append(Path(e.path))

    if loose:
        # گروهِ «فایل‌های مستقیمِ ریشه» — فقط فایل‌های همان سطح (بدون زیرپوشه)
        try:
            with os.scandir(path) as it:
                ents = sorted(it, key=lambda e: natural_key(e.name))
        except OSError:
            return man, files
        for e in ents:
            try:
                if e.is_file(follow_symlinks=False):
                    _note(e, "")
            except OSError:
                continue
        return man, files

    def rec(d: str, rel: str):
        try:
            with os.scandir(d) as it:
                ents = sorted(it, key=lambda e: natural_key(e.name))
        except OSError:
            return
        for e in ents:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in IGNORED_DIRS:
                        continue
                    if _is_junction(e.path):        # round 19 §16
                        continue
                    rec(e.path, rel + e.name + "/")
                elif e.is_file(follow_symlinks=False):
                    _note(e, rel)
            except OSError:
                continue

    rec(str(path), "")
    return man, files


def get_file_hierarchy(path: Path, exclude: set[Path] | None = None, loose: bool = False,
                       only_exts=None):
    """درخت سلسله‌مراتبی فایل‌ها (برای نمای درختی قابل‌بازوبسته).

    خروجی: {"path": Path, "dirs": [node, ...], "files": [Path, ...]}
    loose=True → فقط فایل‌های مستقیم، بدون زیرپوشه (گروه فایل‌های ریشه).
    only_exts → در حالت loose فقط این پسوندها (گروه اسناد: فشرده/PDF).
    """
    path = Path(path)
    exclude = {str(p) for p in (exclude or set())}
    if path.is_file():
        return {"path": path.parent, "dirs": [], "files": [path]}
    if loose:
        return {"path": path, "dirs": [], "files": _direct_files(path, exclude, only_exts)}

    def build(p: Path) -> dict:
        node = {"path": p, "dirs": [], "files": []}
        try:
            kids = list(p.iterdir())
        except OSError:
            return node
        dirs, files = [], []
        for c in kids:
            n = c.name
            if n.startswith(".") or n.lower() in HIDDEN_FILES or str(c) in exclude:
                continue
            if c.is_dir():
                if n.lower() not in IGNORED_DIRS and not _is_junction(c):  # round 19 §16
                    dirs.append(c)
            else:
                files.append(c)
        dirs.sort(key=lambda c: natural_key(c.name))
        files.sort(key=lambda c: natural_key(c.name))
        node["dirs"] = [build(d) for d in dirs]
        node["files"] = files
        return node

    return build(path)


def count_hierarchy_files(node: dict) -> int:
    return len(node["files"]) + sum(count_hierarchy_files(d) for d in node["dirs"])


def item_videos(item) -> list[Path]:
    """ویدیوهای یک عنوان با احترام به loose_group (گروه فایل‌های مستقیم ریشه)."""
    return list_videos(item.path, loose=getattr(item, "loose_group", False))


def item_watchables(item) -> list[Path]:
    """فایل‌های «قابل‌مصرف» یک عنوان — مبنای هوشمندِ «کامل دیده‌شده» (0.48.0).

    قانون (درخواست کاربر): اگر عنوان ویدیو دارد همان ویدیوها ملاک‌اند (زیرنویس
    و سندهای کناری مهم نیستند)؛ وگرنه همه‌ی فایل‌های قابل‌مصرف — کتاب/سند،
    فشرده (مانگا)، تصویر، صوت، برنامه — جزو کل‌اند و زیرنویس/سایدکارها نیستند.
    با loose_group فقط فایل‌های مستقیم شمرده می‌شوند (هم‌راستا با list_videos).
    """
    vids = item_videos(item)
    if vids or (getattr(item, "video_count", 0) or 0) > 0:
        return vids
    path = Path(item.path)

    def _ok48(p: Path) -> bool:
        return (p.is_file() and not p.name.startswith(".")
                and p.suffix.lower() in WATCHABLE_EXTS)

    if path.is_file():
        return [path] if _ok48(path) else []
    if getattr(item, "loose_group", False):
        try:
            out = [p for p in path.iterdir() if _ok48(p)]
        except OSError:
            return []
        out.sort(key=lambda p: natural_key(p.name))
        return out
    out: list[Path] = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
        for n in names:
            if not n.startswith(".") and Path(n).suffix.lower() in WATCHABLE_EXTS:
                out.append(Path(root) / n)
    out.sort(key=lambda p: natural_key(p.name))
    return out


def list_videos(path, loose: bool = False) -> list[Path]:
    """همه‌ی فایل‌های ویدیویی یک عنوان (بازگشتی، بدون پوشه‌های مخفی/سیستمی).

    loose=True → فقط ویدیوهای مستقیم (برای گروه فایل‌های شلخته‌ی ریشه).
    """
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in VIDEO_EXTS else []
    if loose:
        return find_loose_video_files(path)
    out: list[Path] = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
        for n in names:
            if not n.startswith(".") and Path(n).suffix.lower() in VIDEO_EXTS:
                out.append(Path(root) / n)
    out.sort(key=lambda p: natural_key(p.name))
    return out


# ------------------------------------------------------------------ اسکن اصلی

def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def scan_roots(roots, include_image_fallback=True, progress=None, counts=None):
    """roots: لیست دیکشنری‌های {"path": ..., "category": ...}

    هر زیرپوشه‌ی مستقیمِ ریشه = یک عنوان. فایل‌های ویدیوییِ شلخته در خودِ ریشه
    (حالت رایج برای فیلم‌ها) هم به‌عنوان یک عنوان جداگانه ثبت می‌شوند.

    counts(done, total): نسخه‌ی 0.23.0 — گذر سبکِ شمارشِ اولیه (فقط خواندن ورودی‌های
    هر ریشه، بدون کاوش پوستر/زمان) تا نوار پیشرفتِ اسکن «واقعی» باشد و عددِ
    «چند از چند» نشان بدهد (درخواست کاربر — قبلاً فقط حالت نامعین می‌چرخید).
    """
    items: list[MediaItem] = []

    def say(msg):
        if progress:
            progress(msg)

    total_titles = 0
    _seen_count_paths: set[str] = set()      # ریشه‌های روی‌هم‌افتاده دوبار نشمارند
    if counts:
        try:
            for root in roots:
                rp = Path(os.path.expandvars(str(root.get("path", "")).strip()))
                if not rp.is_dir():
                    continue
                try:
                    ch = list(rp.iterdir())
                except OSError:
                    continue
                subs = [c for c in ch
                        if c.is_dir() and not c.name.startswith(".")
                        and c.name.lower() not in IGNORED_DIRS]
                lvid = [c for c in ch
                        if c.is_file() and c.suffix.lower() in VIDEO_EXTS]
                ldoc = [c for c in ch
                        if c.is_file() and not c.name.startswith(".")
                        and c.suffix.lower() in LOOSE_DOC_EXTS]
                # D7: اگر هم D:\Movies و هم D:\Movies\Marvel ریشه‌اند، کارتِ
                # Marvel در هر دو ریشه می‌آید — شمارشِ پیشرفت هم نباید دوبار بشمارد
                def _fresh(paths):
                    out2 = []
                    for c in paths:
                        k = os.path.normcase(os.path.abspath(str(c)))
                        if k in _seen_count_paths:
                            continue
                        _seen_count_paths.add(k)
                        out2.append(c)
                    return out2
                subs, lvid, ldoc = _fresh(subs), _fresh(lvid), _fresh(ldoc)
                if subs:
                    total_titles += len(subs)
                    if lvid or ldoc:
                        total_titles += 1          # کارت گروهیِ فایل‌های مستقیمِ ریشه
                else:
                    total_titles += len(lvid)
                    if ldoc:
                        total_titles += 1          # کارت گروهیِ سند/فشرده‌ی مستقیم
        except Exception as _exc_dbg:
            dbg_log(f"scan_roots failed: {type(_exc_dbg).__name__}")
            total_titles = 0
    done_titles = [0]

    def tick():
        done_titles[0] += 1
        if counts:
            try:
                counts(done_titles[0], max(total_titles, done_titles[0]))
            except Exception as _exc_dbg:
                dbg_log(f"scan_roots.tick failed: {type(_exc_dbg).__name__}")
                pass

    for root in roots:
        rpath = Path(os.path.expandvars(str(root.get("path", "")).strip()))
        category = str(root.get("category", "")).strip() or rpath.name
        if not rpath.is_dir():
            say(T("⚠ مسیر پیدا نشد: {0}").format(rpath))
            continue
        say(T("اسکن {0} …").format(rpath))

        try:
            children = sorted(rpath.iterdir(), key=lambda p: natural_key(p.name))
        except OSError as e:
            say(T("⚠ خطا در خواندن {0}: {1}").format(rpath, e))
            continue

        subdirs = [
            c for c in children
            if c.is_dir() and not c.name.startswith(".")
            and c.name.lower() not in IGNORED_DIRS
        ]

        for child in subdirs:  # هر پوشه = یک عنوان
            # زنجیره‌ی پوستر (ترتیب درخواستی کاربر):
            # آیکون/تصویر ← ویدیوی داخل پوشه ← PDF داخل پوشه ← محتوای فایل فشرده
            # هر سه‌ی «کاندید»ها همیشه پر می‌شوند (نه فقط وقتی قبلی پیدا نشود) تا در
            # مرحله‌ی ساخت تامبنیل اگر آیکون/ویدیو خراب بود و تامبنیل نداد، زنجیره
            # خودکار سراغ مرحله‌ی بعد برود (گزارش کاربر: پوستر نمی‌افتاد). نکته: قبل
            # از این اصلاح، در حضور آیکون کاندیدهای دیگر None ثبت می‌شدند و خرابیِ
            # آیکون هیچ جایگزینی نداشت — یعنی واقعاً «عبور» اتفاق نمی‌افتاد.
            icon = find_icon(child, include_image_fallback)
            preview = find_preview_video(child)
            prevpdf = find_preview_pdf(child)
            prevarc = find_preview_archive(child)
            files, videos, size, tcounts = gather_stats(child)
            items.append(MediaItem(
                name=child.name, path=child, category=category,
                icon_path=icon, file_count=files, video_count=videos,
                size_bytes=size, modified=_mtime(child),
                preview_video=preview, preview_pdf=prevpdf, preview_archive=prevarc,
                dom_type=dominant_type(tcounts),
            ))
            tick()
            say(f"✔ {category} / {child.name}")

        # فایل‌های شلخته‌ی مستقیمِ خودِ ریشه
        loose_vids = [f for f in children
                      if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
        loose_imgs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() in IMAGE_EXTS_SET]
        loose_imgs.sort(key=lambda p: natural_key(p.name))
        # اسنادِ مستقیم ریشه: فایل‌های فشرده و PDF
        loose_pdfs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() == ".pdf"]
        loose_arcs = [f for f in children
                      if f.is_file() and not f.name.startswith(".")
                      and f.suffix.lower() in _ARC_PREF]
        loose_arcs.sort(key=lambda p: (_ARC_PREF[p.suffix.lower()],
                                       natural_key(p.name)))
        loose_docs = loose_pdfs + loose_arcs

        # همه‌ی فایل‌های مستقیم ریشه (تصویر، ویدیو، سند) در یک کارت گروهی
        all_loose = loose_vids + loose_imgs + loose_docs
        all_loose.sort(key=lambda p: natural_key(p.name))

        def _root_icon(root: Path):
            # پارامتر به‌جای بستنِ متغیرِ حلقه: بستنِ متغیرِ حلقه فقط چون
            # بلافاصله صدا زده می‌شد کار می‌کرد و برای خواننده گمراه‌کننده بود.
            return (find_desktop_ini_icon(root) or find_loose_icon(root)
                    or (find_poster_image(root) if include_image_fallback else None))

        if all_loose:
            # ریشه = یک کارت گروهی به نام خودش (مثل زیرپوشه‌ها)
            # شامل همه‌ی فایل‌های مستقیم: تصویر، ویدیو، فشرده، PDF
            dfiles = len(all_loose)
            dvids = len(loose_vids)
            dsize = 0
            dcounts = {t: 0 for t in TYPE_ORDER}
            for f in all_loose:
                try:
                    dsize += f.lstat().st_size
                except OSError:
                    pass
                dcounts[file_type_group(f)] += 1
            icon = _root_icon(rpath)
            preview = loose_vids[0] if loose_vids else None
            prevpdf = loose_pdfs[0] if loose_pdfs else None
            prevarc = loose_arcs[0] if loose_arcs else None
            # اگر ویدیو نیست ولی تصویر هست، اولین تصویر به عنوان preview
            if not preview and loose_imgs:
                preview = loose_imgs[0]
            items.append(MediaItem(
                name=rpath.name, path=rpath, category=category,
                icon_path=icon, file_count=dfiles, video_count=dvids,
                size_bytes=dsize, modified=_mtime(rpath),
                preview_video=preview, preview_pdf=prevpdf,
                preview_archive=prevarc, loose_group=True,
                dom_type=dominant_type(dcounts),
            ))
            tick()
            from i18n import disp_cat as _dc45
            say(T("✔ {0} / {1} (گروه {2} فایل مستقیم)").format(_dc45(category), rpath.name, fa(dfiles)))

    items.sort(key=lambda i: (i.category, natural_key(i.name)))
    # D7 — کاربر که هم D:\Movies و هم D:\Movies\Marvel را ریشه کرده، Marvel
    # را دو بار می‌دید (دو کارتِ مستقل با متای مشترک). dedupe بر اساس
    # normcase+abspath (نه resolve — همان قراردادِ کاراییِ ویندوز/شبکه).
    seen: set[str] = set()
    out: list[MediaItem] = []
    for it in items:
        key = os.path.normcase(os.path.abspath(str(it.path)))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
