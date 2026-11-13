# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Repository integrity checks — pure file checks, no GUI, no PySide6.

Runs in its own CI job (`repo-integrity`, ubuntu) and inside the normal
matrix. Everything is stdlib + pytest on purpose: it must never import an
application module that pulls in Qt, so appinfo/i18n are parsed with `ast`
instead of imported.
"""

from __future__ import annotations

import ast
import hashlib
import io
import re
import subprocess
import tokenize
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Fallback walk only (no git available) — generous so that local untracked
# folders (vendor/, .thumb_cache, demo_library, ...) never leak into checks.
_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", ".build-venv", "vendor", "build",
    "dist", ".thumb_cache", "demo_library", ".pytest_cache", "htmlcov",
    "node_modules",
}


def _tracked_files():
    """Tracked files via `git ls-files` — None when git/.git unavailable.

    NUL-delimited output (safe for odd names), decoded as UTF-8; paths that
    no longer exist on disk (staged deletions) are skipped. Only tracked
    files must be checked — untracked local folders are nobody's business.
    """
    if not (REPO_ROOT / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                           capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for raw in r.stdout.split(b"\x00"):
        if not raw:
            continue
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        p = REPO_ROOT / name
        if p.is_file():
            out.append(p)
    return sorted(out)


def _repo_files():
    tracked = _tracked_files()
    if tracked is not None:
        yield from tracked
        return
    for p in sorted(REPO_ROOT.rglob("*")):
        if not p.is_file():
            continue
        if _SKIP_DIRS & set(p.relative_to(REPO_ROOT).parts):
            continue
        yield p


# ------------------------------------------------------------ vendor untracked
def test_vendor_is_not_tracked():
    """vendor/ must stay an untracked local folder (see .gitignore).

    Round 11 accidentally committed it (37.9 MB of Windows DLLs, incl. the
    JPEG-XL codec this same release removed) and the SPDX integrity test
    broke on the vendored plugin sources that carry no SPDX header.
    """
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no .git — fallback walk mode")
    try:
        r = subprocess.run(["git", "ls-files", "-z", "vendor"], cwd=REPO_ROOT,
                           capture_output=True, text=False, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    tracked = [raw.decode("utf-8") for raw in r.stdout.split(b"\x00") if raw]
    assert not tracked, "vendor/ must stay an untracked local folder (see .gitignore)"


# ------------------------------------------------------------ SPDX headers
# Exact values, not mere presence (round 25): a stray "SPDX-License-Identifier:
# MIT" or a wrong year/name in the copyright line must fail the guard. The
# line scans below are anchored to real header lines (optional comment
# prefixes @/#/::/rem) so string literals inside this guard file never match.
_SPDX_LICENSE_LINE = "SPDX-License-Identifier: GPL-3.0-or-later"
_SPDX_COPYRIGHT_LINE = "SPDX-FileCopyrightText: 2026 Mohammad Jafari"
_SPDX_LICENSE_HEADER_RE = re.compile(
    r"^[ \t]*[@#:]*[ \t]*(?:rem[ \t]+)?[ \t]*SPDX-License-Identifier:[ \t]*"
    r"([A-Za-z0-9.+\-]+)[ \t]*$", re.M)
_SPDX_COPYRIGHT_HEADER_RE = re.compile(
    r"^[ \t]*[@#:]*[ \t]*(?:rem[ \t]+)?[ \t]*SPDX-FileCopyrightText:[ \t]*"
    r"(.+?)[ \t]*$", re.M)


def test_spdx_headers_on_python_and_bat_files():
    checked = 0
    for p in _repo_files():
        if p.suffix.lower() not in (".py", ".bat"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert _SPDX_LICENSE_LINE in text, \
            f"missing/incorrect SPDX-License-Identifier (must be exactly {_SPDX_LICENSE_LINE!r}): {p.name}"
        assert _SPDX_COPYRIGHT_LINE in text, \
            f"missing/incorrect SPDX-FileCopyrightText (must be exactly {_SPDX_COPYRIGHT_LINE!r}): {p.name}"
        # every header line in the file must state exactly the project's own
        # values — no second license/copyright claim on project code
        headers = _SPDX_LICENSE_HEADER_RE.findall(text)
        assert headers, f"no SPDX-License-Identifier header line found: {p.name}"
        for value in headers:
            assert value == "GPL-3.0-or-later", \
                f"unexpected SPDX-License-Identifier {value!r}: {p.name}"
        copyrights = _SPDX_COPYRIGHT_HEADER_RE.findall(text)
        assert copyrights, f"no SPDX-FileCopyrightText header line found: {p.name}"
        for value in copyrights:
            assert value == "2026 Mohammad Jafari", \
                f"unexpected SPDX-FileCopyrightText {value!r}: {p.name}"
        checked += 1
    assert checked >= 40, f"sanity: expected the full .py/.bat tree, got {checked}"


# ------------------------------------------------------------ docs hygiene
# Misspellings that were fixed at some point — they must never come back.
# STRICT applies to every tracked *.md (they are objective spelling/fact
# errors); VOCAB applies to user-facing docs only, because the declared raw
# internal archive (docs/HISTORY-pre-1.0.fa.md) keeps its era vocabulary on
# purpose.
_STRICT_DOC_TYPOS = (
    "پیش‌فرش",        # -> پیش‌فرض
    "dítابیس",        # Latin letters inside a Persian word
    "انتشاردی",       # -> انتشاری
    "هر سه OS",       # the CI matrix has two operating systems
    "جمعاً ≤۳۰۰",     # the retry cap is per attempt, not a total
)
_RETIRED_DOC_VOCAB = (
    "تایتل",          # -> عنوان
    "فایلیوم",        # -> کتابخانه
    "مسابقهٔ GC",     # -> شرط رقابتی در پاک‌سازی کاورهای بدون ارجاع
    "مسابقه‌ی GC",
    # (round 26) واژه‌های نیم‌بند و واژگانِ سنگینِ ممیزیِ متن فارسی — معادلِ
    # ساده‌ی واژه‌نامه جایگزین شد و این فهرست جلوی بازگشتشان را می‌گیرد:
    "فال‌بک",         # -> مسیر/روش جایگزین
    "اتمیک",          # -> بدون خطر خرابی نیمه‌کاره
    "اسنپ‌شات",       # -> نسخه‌ی پشتیبان لحظه‌ای
    "دیکود",          # -> رمزگشایی
    "دکود",
    "دیکد",
    "انکد",           # -> رمزگذاری
    "سایدبار",        # -> نوار کناری / فهرست موارد اخیر
    "سایدکار",
    "فروکرایه",
    "بک‌استاپ",
    "راند",           # شماره‌ی مراحل داخلی از گزارشِ عمومی حذف شد
    "لایسنس",         # -> مجوز
    "پرتابل",         # -> قابل‌حمل
    "کلکسیون",        # -> مجموعه
    "اتریبیوت",       # -> ویژگی
    "دیالوگ",         # -> پنجره
    "مانیفست",        # -> فایل فهرستِ کش
    "اسکیل",          # -> مقیاس
    # (round 29) خروجیِ ممیزیِ بعدی — اصطلاحِ بیگانه‌نما و لحنِ نامه‌نگارانه:
    "درون‌پروسسی",    # -> در همان فرایندِ برنامه
    "تورِ ایمنی",      # -> روشِ جایگزین (هم‌ترازِ معادلِ رسمیِ «فال‌بک»)
    "تور ایمنی",       # همان واژه بدونِ کسره
    "کمال میل",        # لحنِ اول‌شخصِ نامه‌نگارانه — صورتِ بی‌شخص جایگزین شد
    "هوشمند",         # صفتِ بازاری‌سازی — توصیفِ دقیقِ رفتار جایگزین شد
    "هٔ",             # کسرهٔ تاق‌دار به «ه‌ی» یکدست شد
    "ۀ",
    # (round 30) باقیمانده‌ی جدولِ واژگانِ ممیزیِ چهارگانه — برچسب‌های واقعیِ UI
    # جای واژه‌های درون‌ساختِ برنامه نشستند و جملات ترجمه‌ای ساده شدند:
    "ندیده / نیمه‌تمام / دیده‌شده",   # -> دیده‌نشده / نیمه‌تمام / کامل دیده‌شده (ui/library.py)
    "نوار موارد اخیر",               # -> فهرست موارد اخیر (برچسبِ UI: «موارد اخیر»)
    "تک‌فایل",                       # -> یک فایل / قالبِ یک‌فایلی
    "بیرون از صفحه",                 # -> پیش از نمایش آماده می‌شوند
    "رمزگشایی داخلی Qt",             # -> رمزگشایی در خودِ Qt
    "فایل‌های بدون پوشه",            # -> فایل‌هایی که مستقیم داخل پوشه‌ی اصلی دسته‌اند
    "دیتابیس",                       # -> پایگاه داده (هم‌سو با مقدمه‌ی README.fa)
    "مسیرهای گم‌شده",                # -> پوشه‌های جابه‌جاشده یا در دسترس‌نبوده
    "گمشده",                         # نیم‌فاصله ندارد؛ صورتِ درست: گم‌شده (فقط در جمله‌های بازنویسی‌شده نمی‌ماند)
    "حذفِ برنامه = پاک‌کردن پوشه",   # -> برای حذفِ برنامه کافی است پوشه‌ی آن را پاک کنید
    "لانچر",                         # -> فایل راه‌انداز / راه‌اندازِ `py`
    # (round 31) پولیشِ تأییدشده‌ی داوریِ چهار تحلیل — عبارت‌های تحت‌اللفظی و
    # لحنِ محاوره‌ای/نامه‌نگارانه که با ویرایشِ نهایی ساده شدند:
    "پشتیبان غریبه",       # -> پشتیبانِ دیگران (هم‌سو با SECURITY.fa و FEATURES.fa)
    "جان سالم به در",      # -> حفظ می‌شوند
    "خطاها صادقانه",       # -> پیام‌های خطا روشن و دقیق‌اند
    "قرارداد کنتراست",     # -> رعایت معیار کنتراست WCAG
    "نگهبان",              # -> بررسی / آزمونِ خودکار — واژه‌ی درون‌ساختِ برنامه است
    "مثل ناموجود",         # -> مثل فایلِ ناموجود
    "تعداد کارگر",         # -> تعداد کارِ موازی
    "بازش کنید",           # -> آن را باز کنید
    "بی‌صدا می‌مرد",       # -> بدون هیچ پیامی بسته می‌شد
    "کامل کن، بعد بساز",   # -> کامل کنید، بعد بسازید (خطابِ جمعِ بقیه‌ی سند)
)


def test_fa_md_docs_do_not_regress_fixed_typos():
    md = [p for p in _repo_files() if p.suffix.lower() == ".md"]
    assert len(md) >= 10, f"sanity: expected the full .md tree, got {len(md)}"
    for p in md:
        text = p.read_text(encoding="utf-8", errors="replace")
        for typo in _STRICT_DOC_TYPOS:
            assert typo not in text, \
                f"{p.name}: fixed typo reappeared: {typo!r}"
        if p.name == "HISTORY-pre-1.0.fa.md":
            continue          # declared raw internal archive — vocab exempt
        for word in _RETIRED_DOC_VOCAB:
            assert word not in text, \
                f"{p.name}: retired wording reappeared: {word!r}"
        # نسخه‌های پایتون در متنِ فارسی هم رقمِ لاتین می‌مانند (رقمِ مرتبط با کد)،
        # مثل «پایتون 3.11+» — نه «پایتون ۳.۱۱+» (round 30، BUILDING.fa.md)
        _fa_digit_py = re.search(r"پایتون\s*[۰-۹]", text)
        assert not _fa_digit_py, \
            f"{p.name}: code-related Python version numbers keep Latin digits " \
            f"(پایتون 3.11, not Persian digits): ...{_fa_digit_py.group(0)}..."


# ------------------------------------------------------------ release guide
def test_releasing_guide_carries_release_rules():
    """The three rules added after the pre-release audits (round 25)."""
    en = (REPO_ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    fa = (REPO_ROOT / "docs" / "RELEASING.fa.md").read_text(encoding="utf-8")
    # 1) the destination repository must hold the content, not just resolve
    assert "not a valid GPL source offer" in en
    assert "پیشنهاد سورسِ GPL معتبر نیست" in fa
    # 2) a public /with-ffmpeg build needs the corresponding source of that build
    assert "corresponding source and build configuration" in en
    assert "سورسِ متناظرِ دقیق و تنظیماتِ ساخت" in fa
    # 3) the CHANGELOG date must be the actual release day before tagging
    assert "actual release date" in en
    assert "تاریخِ واقعیِ انتشار" in fa
    # and the 7za hash is verified against upstream, not just the recorded value
    assert "official 7-Zip download" in en
    assert "دانلودِ" in fa and "رسمیِ 7-Zip" in fa


# ------------------------------------------------------------ LICENSE
def test_license_is_gpl3():
    head = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8", errors="replace").lstrip()
    assert head.startswith("GNU GENERAL PUBLIC LICENSE")


# ------------------------------------------------------------ EN/FA parity
_MD_PAIRS = [
    ("README.md", "README.fa.md"),
    ("CHANGELOG.md", "CHANGELOG.fa.md"),
    ("CONTRIBUTING.md", "CONTRIBUTING.fa.md"),
    ("docs/BUILDING.md", "docs/BUILDING.fa.md"),
    ("docs/FEATURES.md", "docs/FEATURES.fa.md"),
    ("docs/RELEASING.md", "docs/RELEASING.fa.md"),
    ("SECURITY.md", "SECURITY.fa.md"),
]

_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^\s*#{1,6} ")
_BULLET_RE = re.compile(r"^\s*[-*] ")


def _md_metrics(text: str) -> tuple:
    """(headings, bullets, fence lines, table rows) — fences toggle code mode."""
    headings = bullets = fences = table_rows = 0
    in_code = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_code = not in_code
            fences += 1
            continue
        if in_code:
            continue
        if _HEADING_RE.match(line):
            headings += 1
        if _BULLET_RE.match(line):
            bullets += 1
        if line.lstrip().startswith("|"):
            table_rows += 1
    return headings, bullets, fences, table_rows


@pytest.mark.parametrize("en_rel,fa_rel", _MD_PAIRS)
def test_en_fa_md_pairs_have_identical_structure(en_rel, fa_rel):
    en = _md_metrics((REPO_ROOT / en_rel).read_text(encoding="utf-8"))
    fa = _md_metrics((REPO_ROOT / fa_rel).read_text(encoding="utf-8"))
    assert en == fa, f"structure mismatch {en_rel} vs {fa_rel}: EN={en} FA={fa}"


# ------------------------------------------------------------ link resolution
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_IMG_SRC_RE = re.compile(r"<img\s[^>]*?src=\"([^\"]+)\"", re.IGNORECASE)
_EXTERNAL_RE = re.compile(r"^(https?:|mailto:|#)", re.IGNORECASE)


def _md_text_outside_fences(text: str) -> str:
    out, in_code = [], False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_code = not in_code
            continue
        if not in_code:
            out.append(line)
    return "\n".join(out)


def test_relative_links_in_md_resolve():
    for p in _repo_files():
        if p.suffix.lower() != ".md":
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        text = _md_text_outside_fences(p.read_text(encoding="utf-8", errors="replace"))
        targets = _MD_LINK_RE.findall(text) + _IMG_SRC_RE.findall(text)
        for target in targets:
            path_part = target.split("#", 1)[0]
            if not path_part or _EXTERNAL_RE.match(target):
                continue                                  # anchor-only / external
            if path_part.startswith("screenshots/"):
                # EXCEPTION (on purpose): the six screenshot PNGs are added to
                # the repo at release time by the owner; the README references
                # them ahead of the files existing. These are the ONLY targets
                # allowed to be missing.
                continue
            resolved = (p.parent / path_part).resolve()
            if REPO_ROOT != resolved and REPO_ROOT not in resolved.parents:
                continue                                  # GitHub route: ../../releases/latest
            assert resolved.is_file() or resolved.is_dir(), \
                f"{rel}: broken link -> {target}"


# ------------------------------------------------- in-page anchors (r29)
# (round 29) ممیزیِ مخزن دو لنگرِ فارسیِ خراب را نشان داد: سربرگ‌ها هنگامِ
# یکدست‌سازیِ کسره از جمع («بسته‌های») به مفردِ کسره‌دار («بسته‌ی») تغییر
# کرده بودند ولی لنگرِ فهرستِ مطالب روی فرمِ قدیمی مانده بود — کلیک رویشان
# در GitHub هیچ‌جا نمی‌برد. GitHub هنگامِ ساختِ لنگر، نیم‌فاصله‌ی U+200C و
# نشانه‌گذاری‌ها را حذف می‌کند («بسته‌ی» ← «بستهی»)؛ الگوریتمِ github-slugger
# اینجا بازسازی شده تا «لنگرِ فهرست ↔ سربرگ» برای همیشه قفل شود. آرشیوِ خامِ
# تاریخچه (که لینک‌های دورانِ خودش را حفظ) مستثناست — همان استثنایِ گاردِ واژگان.
_ANCHOR_HISTORY = "docs/HISTORY-pre-1.0.fa.md"


def _github_anchor_slug(title: str) -> str:
    """همان کاری که GitHub با سربرگ‌ها می‌کند: حرفِ کوچک، حذفِ هرچه جز
    حرف/رقم/فاصله/خط‌تیره/زیرخط (شامل نیم‌فاصله‌ی U+200C و علائم)، بعد
    فاصله‌ها به خط‌تیره. علامت‌های اعراب (Mn) مثل خودِ GitHub می‌مانند."""
    out = []
    for ch in title.lower():
        if ch in (" ", "-") or ch == "_":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N") or cat == "Mn":
            out.append(ch)
    return "".join(out).replace(" ", "-")


def _md_heading_slugs(path: Path) -> set:
    text = _md_text_outside_fences(path.read_text(encoding="utf-8", errors="replace"))
    slugs = set()
    for line in text.splitlines():
        m = re.match(r"\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            slugs.add(_github_anchor_slug(re.sub(r"[`*_~]", "", m.group(2).strip())))
    return slugs


def test_md_in_page_anchors_match_headings():
    checked = 0
    for p in _repo_files():
        if p.suffix.lower() != ".md":
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel == _ANCHOR_HISTORY:
            continue          # declared raw internal archive — era links kept
        text = _md_text_outside_fences(p.read_text(encoding="utf-8", errors="replace"))
        slugs_cache = {}
        for m in _MD_LINK_RE.finditer(text):
            target = m.group(1).strip()
            path_part, _, frag = target.partition("#")
            if not frag:
                continue
            if path_part and _EXTERNAL_RE.match(target):
                continue          # external URL with a fragment — out of scope
            frag = frag.strip()
            if not frag:
                continue
            if path_part:
                resolved = (p.parent / path_part).resolve()
                if resolved.suffix.lower() != ".md" or not resolved.is_file():
                    continue       # other targets belong to the link test
                key = str(resolved)
            else:
                key = str(p)
            if key not in slugs_cache:
                slugs_cache[key] = _md_heading_slugs(Path(key))
            if _github_anchor_slug(frag) not in slugs_cache[key]:
                line_no = text[: m.start()].count("\n") + 1
                raise AssertionError(
                    f"{rel}:{line_no}: in-page anchor #{frag!r} matches no "
                    f"heading in {Path(key).name} (GitHub slugs drop ZWNJ and "
                    "punctuation: بسته‌ی -> بستهی)")
            checked += 1
    assert checked >= 25, f"sanity: expected many in-page anchors, got {checked}"


# ------------------------------------------------------------ 7za checksum
def test_bundled_7za_hash_is_documented():
    exe = REPO_ROOT / "bin" / "7za.exe"
    assert exe.is_file(), "bin/7za.exe must stay in the repo"
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    readme = (REPO_ROOT / "bin" / "README-7zip.txt").read_text(encoding="utf-8", errors="replace")
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8", errors="replace")
    assert digest in readme.lower(), "7za.exe hash missing from bin/README-7zip.txt"
    assert digest in notices.lower(), "7za.exe hash missing from THIRD_PARTY_NOTICES.md"


# ------------------------------------------------------------ version match
def _app_version() -> str:
    tree = ast.parse((REPO_ROOT / "appinfo.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "APP_VERSION":
                    return node.value.value
    raise AssertionError("APP_VERSION not found in appinfo.py")


@pytest.mark.parametrize("name", ["CHANGELOG.md", "CHANGELOG.fa.md"])
def test_appinfo_version_matches_changelog(name):
    version = _app_version()
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    releases = re.findall(r"^## \[([^\]]+)\]", text, re.M)
    assert releases, f"no version headings in {name}"
    if releases[0].lower() == "unreleased":
        assert len(releases) > 1, f"{name}: Unreleased needs a released section below it"
        assert releases[1] == version, f"{name}: top release {releases[1]} != appinfo {version}"
    else:
        assert releases[0] == version, f"{name}: top release {releases[0]} != appinfo {version}"


# ------------------------------------------------------------ version naming
# (round 20 §۱) گزارش/ممیزیِ نوبتی هرگز نباید در مخزن کامیت شوند (فقط داخلِ
# زیپِ تحویل‌اند) — کامیتِ تصادفیِ ROUND19-report.fa.md در round 19 کلِ CI را
# قرمز کرد، چون نگهبانِ نسخه «شماره‌ی نسخه‌ی بعدیِ آن زمان» را در متنِ خودِ گزارش می‌دید. حالا:
# ۱) این فایل‌ها از scanِ نسخه مستثنا هستند (ذکرِ «نسخهٔ بعدی» در متنشان طبیعی است)
# ۲) نگهبانِ جداگانه‌ی test_round_reports_are_not_tracked جلوی کامیت‌شدنشان را می‌گیرد
#    با پیامی که علت را می‌گوید — نه پیامِ گمراه‌کنندهٔ «stale version».
# (round 21) همین سرنوشت برای _READ-ME-FIRST.fa.txt در round 20 تکرار شد —
# برگهٔ دستورالعملِ داخلِ زیپِ تحویل است و خودش می‌گوید قبل از کامیت از index
# خارج شود؛ حالا همان نگهبان آن را هم می‌گیرد و .gitignore هم جلوی git add -A را می‌گیرد.
_ROUND_ARTIFACT_RE = re.compile(r"^ROUND\d+-", re.IGNORECASE)
_DELIVERY_README_RE = re.compile(r"^_READ-ME-FIRST", re.IGNORECASE)


# ------------------------------------------------------------ version policy
# (round 27) سیاست نسخه‌گذاری: نخستین انتشار پایدارِ آینده 1.0.0 خواهد بود و
# تا آن زمان همه‌ی نسخه‌های برنامه با الگوی 0.n.m شماره می‌خورند. نگاشتِ
# دوره‌ی عمومیِ اولیه: 1.0.0 پیشین = 0.52.0 ؛ 1.0.1 پیشین = 0.53.0 ؛
# 1.0.2 پیشین = 0.54.0. این نگهبان:
# ۱) نسخه‌ی بعدیِ واقعی را در مستندات ممنوع می‌کند (با هر bump جلو می‌رود)؛
# ۲) جلوی بازگشتِ شماره‌های 1.0.x به‌عنوان نسخه‌ی برنامه را می‌گیرد — فقط
#    اشاره‌ی «نخستین انتشار پایدارِ آینده» مجاز است. آرشیوِ خامِ
#    docs/HISTORY-pre-1.0.fa.md معاف است؛ فایل‌های licenses/ هم معاف‌اند
#    (متن‌های verbatim حقوقیِ شخص‌ثالث). متنِ THIRD_PARTY_NOTICES.md اسکن
#    می‌شود چون متن‌های verbatim واقعی در licenses/*.txt هستند و .md نیستند.
_APP_1X_RE = re.compile(r"(?<!\d)1\.0\.[0-9x](?!\d)")
_FUTURE_STABLE_100 = (
    "first stable release",        # transition line in CHANGELOG.md
    "نخستین نسخه‌ی پایدار",         # transition line in CHANGELOG.fa.md
    "نخستین انتشار پایدار",
    "از نسخه‌ی 1.0.0 به بعد",
)
_GUARD_1X_EXEMPT_FILES = {
    "docs/HISTORY-pre-1.0.fa.md",   # declared raw internal archive
}
_GUARD_1X_EXEMPT_DIRS = ("licenses/",)


def test_no_stale_version_or_history_name_in_docs():
    for p in _repo_files():
        if p.suffix.lower() != ".md":
            continue
        if _ROUND_ARTIFACT_RE.match(p.name):
            continue                      # گزارشِ نوبتی — نگهبانِ جداگانه دارد
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel in _GUARD_1X_EXEMPT_FILES or rel.startswith(_GUARD_1X_EXEMPT_DIRS):
            continue          # آرشیوِ خام / متن‌های verbatim حقوقی — معاف
        text = p.read_text(encoding="utf-8", errors="replace")
        # هر نسخه‌ای که هنوز منتشر نشده نباید در مستندات دیده شود —
        # با هر bump این نگهبان به نسخه‌ی بعدی می‌رود (round 33: انتشارِ فعلی
        # 0.55.1 است — نگهبان روی 0.56.0)
        assert "0.56.0" not in text, f"{rel}: stale 0.56.0 reference (release is 0.55.1)"
        assert "HISTORY-v1-v49" not in text, f"{rel}: old history file name"
        # هیچ 1.0.xی نباید به‌عنوان نسخه‌ی فعلی/گذشته‌ی برنامه بماند؛ فقط
        # خطِ گذارِ «نخستین انتشار پایدار» می‌تواند به 1.0.0 اشاره کند
        offenders = []
        line_no = 0
        for line in text.splitlines(keepends=True):
            line_no += 1
            for m in _APP_1X_RE.finditer(line):
                if not any(ok in line for ok in _FUTURE_STABLE_100):
                    offenders.append(f"{rel}:{line_no}: {m.group(0)}")
        assert not offenders, (
            "1.0.x must not appear as the application version in docs "
            "(pre-1.0 releases are 0.n.m; only the future first-stable "
            f"mention is allowed): {offenders[:8]}"
        )


def test_round_reports_are_not_tracked():
    """گزارش/ممیزیِ نوبتی و برگهٔ «اول این را بخوان» در مخزن جا ندارند.

    ROUND19-report.fa.md تصادفی کامیت شد و چون «شماره‌ی نسخه‌ی بعدیِ آن زمان» را در متن داشت،
    نگهبانِ نسخه روی همهٔ پلتفرم‌ها شکست خورد (round 19).
    _READ-ME-FIRST.fa.txt هم در round 20 همان اشتباه را تکرار کرد — این
    فایلِ دستورالعملِ داخلِ زیپِ تحویل است (تستِ مخزن فقط ‎.md‎ را می‌بیند،
    پس CI سبز می‌ماند و خطا پنهان می‌ماند). اگر دوباره کامیت شوند، این
    تست با پیامِ روشن و دستورِ دقیق شکست می‌دهد.
    """
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no .git — fallback walk mode")
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                           capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    bad_round, bad_readme = [], []
    for raw in r.stdout.split(b"\x00"):
        if not raw:
            continue
        name = raw.decode("utf-8", "replace")
        base = Path(name).name
        if _ROUND_ARTIFACT_RE.match(base):
            bad_round.append(name)
        elif _DELIVERY_README_RE.match(base):
            bad_readme.append(name)
    assert not bad_round, (
        "round reports/audits belong in the delivery ZIP, not the repo "
        f"(git rm them): {bad_round}")
    assert not bad_readme, (
        '_READ-ME-FIRST* is the delivery ZIP instruction sheet, not repo '
        'content — run: git rm --cached "_READ-ME-FIRST.fa.txt"  '
        f"(file stays local; .gitignore keeps it out): {bad_readme}")


def test_history_file_lives_at_its_renamed_path():
    hist = REPO_ROOT / "docs" / "HISTORY-pre-1.0.fa.md"
    assert hist.is_file(), (
        "docs/HISTORY-pre-1.0.fa.md must exist "
        "(renamed from docs/HISTORY-v1-v49.fa.md)"
    )


# ------------------------------------------------------------ internal labels
# Same rule as the renumbering pass: a bare internal label is a v-token with
# N in 1..51, not followed by '.'/digit/word (excludes v0.52.0, v25.01, v48r3)
# and not preceded by '|'/word/'@' (excludes |v4 cache eras, checkout@v4).
# Malformed renumbering leftovers fail too: 'v.' + digit, 0.0.N.0, and a
# 0.N.0 label glued directly to a Persian letter.
_VN_RE = re.compile(r"(?<![|\w.@])v(\d{1,2})(?![.\d\w])")
_VDOT_RE = re.compile(r"(?<![|\w.@])v\.(\d{1,2})(?!\d)")
_DBL_ZERO_RE = re.compile(r"0\.0\.(\d{1,2})\.0")
_FA_GLUE_RE = re.compile(
    r"0\.\d{1,2}\.0(?=[\u200c]?[\u0621-\u064A\u067E\u0686\u0698\u06A9\u06AF\u06CC])")


def _bad_label_kinds(text: str):
    """Kinds of internal-label patterns present in *text*."""
    for m in _VN_RE.finditer(text):
        if 1 <= int(m.group(1)) <= 51:
            yield "bare vN"
    for m in _VDOT_RE.finditer(text):
        if 1 <= int(m.group(1)) <= 51:
            yield "v.N"
    for m in _DBL_ZERO_RE.finditer(text):
        if 1 <= int(m.group(1)) <= 51:
            yield "0.0.N.0"
    if _FA_GLUE_RE.search(text):
        yield "0.N.0 glued to a Persian letter"


def _docstring_nodes(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
               and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                yield node.body[0]


def test_no_bare_internal_version_label_in_comments_or_docstrings():
    offenders = []
    for p in _repo_files():
        if p.suffix != ".py":
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        src = p.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                for kind in _bad_label_kinds(tok.string):
                    offenders.append(
                        f"{rel}:{tok.start[0]} comment {kind} {tok.string[:60]!r}")
        for node in _docstring_nodes(ast.parse(src)):
            for kind in _bad_label_kinds(node.value.value):
                offenders.append(f"{rel}:{node.lineno} docstring {kind}")
    assert not offenders, (
        "internal version labels remain in comments/docstrings "
        "(use the 0.N.0 form or drop the label): "
        f"{offenders[:10]}"
    )


# ------------------------------------------------------------ i18n coverage
def _tr_names(tree: ast.AST) -> set:
    """Local names that refer to i18n.tr / i18n.T (including aliased imports)."""
    names = {"tr", "T"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "i18n":
            for alias in node.names:
                if alias.name in ("tr", "T"):
                    names.add(alias.asname or alias.name)
    return names


def test_every_literal_passed_to_tr_or_t_is_translated():
    call_sites = {}
    for p in _repo_files():
        if p.suffix != ".py" or p.name.startswith("i18n"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = _tr_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname in names and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    # an empty string never reaches the dictionary (T() returns
                    # it as-is), so it is exempt by definition
                    if arg.value == "":
                        continue
                    call_sites.setdefault(arg.value, p.name)
    en_tree = ast.parse((REPO_ROOT / "i18n_en.py").read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(en_tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    missing = sorted(set(call_sites) - keys)
    assert not missing, \
        f"literals passed to tr()/T() without an i18n_en entry: {missing[:10]} ..."


# ------------------------------------------------------------ docs vs CI matrix
def test_documented_python_versions_match_ci_matrix():
    wf = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    versions = set(re.findall(r'"(\d+\.\d+)"', wf))
    assert versions == {"3.11", "3.12", "3.13"}, f"unexpected CI matrix: {versions}"
    lo, hi = min(versions), max(versions)
    badge = f"badge/python-{lo}--{hi}-blue"
    for name in ("README.md", "README.fa.md"):
        assert badge in (REPO_ROOT / name).read_text(encoding="utf-8"), \
            f"{name}: Python badge must state {lo}–{hi}"
    for name in ("docs/BUILDING.md", "docs/BUILDING.fa.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert lo in text and hi in text, f"{name}: must document Python {lo}–{hi}"


# ------------------------------------------------------------ GPL source offer
def test_source_url_matches_build_repo_url():
    """آدرسِ سورسِ GPL باید در appinfo و BUILD.bat یکی باشد (L1).

    مقدمه‌ی BUILD.bat خودش می‌گوید: هر دو باید «همزمان» عوض شوند وگرنه
    پیشنهادِ سورسِ داخلِ بسته‌ی پرتابل (SOURCE.txt) می‌شکند. appinfo ایمپورت
    نمی‌شود (هیچ ماژولِ برنامه اینجا ایمپورت نمی‌شود) — با ast خوانده می‌شود."""
    tree = ast.parse((REPO_ROOT / "appinfo.py").read_text(encoding="utf-8"))
    src_url = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SOURCE_URL":
                    src_url = node.value.value
    build = (REPO_ROOT / "BUILD.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "REPO_URL=(\S+)"', build)
    assert src_url, "SOURCE_URL not found in appinfo.py"
    assert m, "REPO_URL not found in BUILD.bat"
    assert src_url == m.group(1), \
        f"appinfo.SOURCE_URL ({src_url}) != BUILD.bat REPO_URL ({m.group(1)})"


# ------------------------------------------------------------ brand identity
# (round 24) برنامه به FolderDex تغییر نام کرد — مخزنِ رسمی هم جابه‌جا شد.
# این نگهبان‌ها جلوی برگشتِ اتفاقیِ نام/لینکِ قدیمی را می‌گیرند.
_OFFICIAL_REPO = "https://github.com/mohammadjafari-dev/FolderDex"


def _appinfo_str(const_name: str) -> str:
    tree = ast.parse((REPO_ROOT / "appinfo.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == const_name:
                    value = node.value.value
                    assert isinstance(value, str), f"{const_name} is not a str"
                    return value
    raise AssertionError(f"{const_name} not found in appinfo.py")


def test_app_name_is_the_folderdex_brand():
    """نامِ برند در هر دو زبان FolderDex است — نه جفتِ فارسی/انگلیسیِ قدیمی."""
    assert _appinfo_str("APP_NAME") == "FolderDex"
    assert _appinfo_str("APP_NAME_EN") == "FolderDex"


def test_build_appname_matches_the_brand():
    """APPNAME در BUILD.bat نامِ exe/dist/zip را می‌سازد و باید با نامِ نمایشی
    برنامه یکی بماند؛ وگرنه بسته‌ی پرتابل از رابطِ برنامه جدا می‌افتد."""
    build = (REPO_ROOT / "BUILD.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "APPNAME=(\S+)"', build)
    assert m, "APPNAME not found in BUILD.bat"
    assert m.group(1) == "FolderDex" == _appinfo_str("APP_NAME_EN"), \
        "BUILD.bat APPNAME diverged from the app brand"


def test_official_links_are_the_public_repo():
    """پیشنهادِ سورسِ GPL باید همه‌جا به مخزنِ عمومیِ رسمی برسد — آدرسِ مخزنِ
    خصوصیِ قدیمی نباید در مستنداتِ کاربر-رو بماند."""
    build = (REPO_ROOT / "BUILD.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "REPO_URL=(\S+)"', build)
    assert _appinfo_str("SOURCE_URL") == _OFFICIAL_REPO
    assert m and m.group(1) == _OFFICIAL_REPO
    for rel in ("README.md", "README.fa.md",
                "docs/RELEASING.md", "docs/RELEASING.fa.md",
                ".github/ISSUE_TEMPLATE/config.yml"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "gcart5446-cmd/teser" not in text, f"{rel}: old repo link remains"
        assert _OFFICIAL_REPO in text, f"{rel}: official repo link missing"


def test_readmes_are_fully_rebranded():
    """README ویترینِ پروژه است: نامِ جدید، نامِ بسته‌ی جدید و هیچ نشانه‌ای از
    برندِ قبلی (تاریخچه در CHANGELOG می‌ماند، نه در README)."""
    for rel in ("README.md", "README.fa.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "CoverShelf" not in text, f"{rel}: old English brand remains"
        assert "قفسه‌ی کاور" not in text, f"{rel}: old Persian brand remains"
        assert "# FolderDex" in text, f"{rel}: title must be the new brand"
        assert "FolderDex-win64-portable.zip" in text, f"{rel}: package name"
        assert "FolderDex.exe" in text, f"{rel}: executable name"


# ------------------------------------------------------------ AI note (round 26)
def test_ai_note_is_informational_only():
    """یادداشتِ توسعه فقط اطلاع‌رسانی است: «با کمک هوش مصنوعی نوشته شده» —
    نه سلبِ مسئولیت و نه تعریفِ مسئولیتِ نگه‌دارنده (ممیزیِ متن‌های فارسی،
    دسته‌ی ۳). متنِ دقیق در هر دو README قفل می‌شود."""
    fa = (REPO_ROOT / "README.fa.md").read_text(encoding="utf-8")
    en = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "**نکته:** کد این برنامه و فایل‌های Markdown آن با کمک هوش مصنوعی" \
        " نوشته شده‌اند." in fa, "README.fa.md: AI note wording changed"
    assert ("**Note:** The application code and this project's Markdown files"
            " were written with the help of AI.") in en, \
        "README.md: AI note wording changed"
    for rel, text in (("README.fa.md", fa), ("README.md", en)):
        assert "مسئولیت بررسی" not in text, \
            f"{rel}: the responsibility sentence must stay out"
        assert "Responsibility for reviewing" not in text, \
            f"{rel}: the responsibility sentence must stay out"


# ------------------------------------------------------------ BiDi punctuation (round 26)
# ممیزیِ متن‌های فارسی، دسته‌ی ۱.۱: وقتی «،» یا «؛» فارسی بلافاصله بعد از حرف/
# رقمِ لاتین بیاید، در نمایشِ RTL علامت به طرفِ دیگرِ کلمه می‌پرد. پرانتزِ
# بسته و بک‌تیک جهت‌خنثی‌اند و الگویشان مجاز است (دسته‌ی ۱.۲ ممیزی). اگر واقعاً
# لازم شد بین حرفِ لاتین و علامت RLM (U+200F) بگذارید یا فهرست را جابه‌جا کنید.
_BIDI_PUNCT_RE = re.compile(r"[A-Za-z0-9][،؛؟]")
_BIDI_HISTORY = "docs/HISTORY-pre-1.0.fa.md"      # declared raw archive


def test_fa_docs_no_latin_glued_to_persian_punctuation():
    offenders = []
    for p in _repo_files():
        rel = p.relative_to(REPO_ROOT).as_posix()
        if p.suffix.lower() != ".md" or rel == _BIDI_HISTORY:
            continue
        in_fence = False
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.match(r"^\s*```", line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in _BIDI_PUNCT_RE.finditer(line):
                j = m.start()
                offenders.append(f"{rel}:{i}: …{line[max(0, j - 30):j + 12]}…")
    assert not offenders, (
        "Persian punctuation glued to a Latin letter/digit — reorder the list, "
        "use a Latin comma between Latin words, or insert RLM: "
        + "; ".join(offenders[:8])
    )


# ------------------------------------------------------------ PySide6 / 3.11 pin
def test_pyside_capped_below_6_12_on_python_311():
    """shiboken6 6.12.0 crashes Python 3.11 at interpreter shutdown
    (Signal(object) payloads → bool_dealloc SIGABRT; see the round 22/23
    changelog entries). requirements.txt must keep the python_version
    markers that cap PySide6-Essentials below 6.12 on 3.11 — and leave
    3.12+ unconstrained — otherwise a plain `pip install -r
    requirements.txt` on 3.11 re-arms the crash. The CI guard step
    (`Guard: shiboken6 must be < 6.12 on Python 3.11`) asserts the
    resolved version; this test guards the file itself.
    """
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert 'PySide6-Essentials>=6.6,<6.12; python_version < "3.12"' in text, \
        "requirements.txt lost the PySide6 <6.12 cap for Python 3.11"
    assert 'PySide6-Essentials>=6.6,<7; python_version >= "3.12"' in text, \
        "requirements.txt lost the unconstrained PySide6 line for Python 3.12+"


# ------------------------------------------------------------ Dependabot (round 28)
def test_dependabot_keeps_hands_off_pyside6_pin():
    """(round 28) پینِ PySide6-Essentials با مارکرهای python_version مدیریتِ
    دستی است — هم گاردِ requirements (تستِ بالایی) و هم گامِ CI همان خط‌ها
    را assert می‌کنند. بامپِ یکجای Dependabot همان خط‌ها را ویرایش می‌کند و
    هر ۵ جاب را قرمز می‌کند (شاخه‌ی dependabot/pip/pyside6-essentials-…،
    راند ۲۳). dependabot.yml باید pyside6-essentials را از version updates
    خارج نگه دارد؛ به‌روزرسانیِ امنیتی از تبِ alerts می‌آید و این ignore
    جلویش را نمی‌گیرد."""
    text = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert text.count("package-ecosystem: pip") == 1, \
        "dependabot.yml: expected exactly one pip update block"
    pip_block = text.split("package-ecosystem: pip", 1)[1]
    assert re.search(r"^\s+ignore:\s*$", pip_block, re.M), \
        "dependabot.yml: pip updates lost the ignore block (round 28)"
    assert re.search(r"-\s*dependency-name:\s*pyside6-essentials\s*$", pip_block, re.M), \
        "dependabot.yml: pyside6-essentials must stay out of version updates"
    actions_block = text.split("package-ecosystem: pip", 1)[0]
    assert "pyside6-essentials" not in actions_block, \
        "dependabot.yml: the ignore belongs to the pip block, not actions"
