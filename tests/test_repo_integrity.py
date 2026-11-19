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

    It was accidentally committed once (37.9 MB of Windows DLLs, incl. the
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
# Exact values, not mere presence: a stray "SPDX-License-Identifier:
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
# errors); VOCAB applies to user-facing docs only (verbatim third-party
# texts live in licenses/*.txt and are out of scope for the docs guards).
_STRICT_DOC_TYPOS = (
    "پیش‌فرش",        # -> the correct spelling of the word for "default"
    "dítابیس",        # Latin letters inside a Persian word
    "انتشاردی",       # -> the corrected spelling of this misspelled word
    "هر سه OS",       # the CI matrix has two operating systems
    "جمعاً ≤۳۰۰",     # the retry cap is per attempt, not a total
)
_RETIRED_DOC_VOCAB = (
    "تایتل",          # -> the plain Persian word for "title"
    "فایلیوم",        # -> the plain Persian word for "library"
    "مسابقهٔ GC",     # -> the Persian phrasing for a GC race in unreferenced-cover cleanup
    "مسابقه‌ی GC",
    # Loanwords flagged by the Persian-text review; each was replaced with the
    # plain dictionary equivalent, and this list keeps them from returning:
    "فال‌بک",         # -> the Persian phrase for "fallback path"
    "اتمیک",          # -> the Persian phrase for "no partial-update corruption"
    "اسنپ‌شات",       # -> the Persian phrase for "point-in-time backup copy"
    "دیکود",          # -> the Persian word for "decode"
    "دکود",
    "دیکد",
    "انکد",           # -> the Persian word for "encode"
    "سایدبار",        # -> the Persian phrase for "sidebar / recent-items list"
    "سایدکار",
    "فروکرایه",
    "بک‌استاپ",
    "راند",           # internal per-run step numbers were dropped from public reports
    "لایسنس",         # -> the Persian word for "license"
    "پرتابل",         # -> the Persian word for "portable"
    "کلکسیون",        # -> the Persian word for "collection"
    "اتریبیوت",       # -> the Persian word for "attribute"
    "دیالوگ",         # -> the plain Persian word for "window"
    "مانیفست",        # -> the Persian phrase for "cache index file"
    "اسکیل",          # -> the Persian word for "scale"
    # More flagged output — foreign-looking jargon and letter-writing tone:
    "درون‌پروسسی",    # -> the Persian phrase for "in the app's own process"
    "تورِ ایمنی",      # -> the Persian phrase for "alternative method"
    "تور ایمنی",       # the same word without the vowel mark
    "کمال میل",        # first-person letter-writing tone — replaced with an impersonal phrasing
    "هوشمند",         # marketing adjective — replaced with a precise behavior description
    "هٔ",             # the arched ezafe form was unified to the plain ezafe form
    "ۀ",
    # Remainder of the four-way review vocabulary table — real UI labels
    # replaced in-app jargon, and translationese sentences were simplified:
    "ندیده / نیمه‌تمام / دیده‌شده",   # -> the real UI labels (ui/library.py)
    "نوار موارد اخیر",               # -> the UI label "recent items"
    "تک‌فایل",                       # -> the plain Persian phrase for "a single file"
    "بیرون از صفحه",                 # -> the plain Persian phrase for "prepared before display"
    "رمزگشایی داخلی Qt",             # -> the plain Persian phrase for "decoded inside Qt"
    "فایل‌های بدون پوشه",            # -> the Persian phrase for "files in the category folder"
    "دیتابیس",                       # -> the Persian word for "database" (like README.fa)
    "مسیرهای گم‌شده",                # -> the Persian phrase for "moved or unavailable folders"
    "گمشده",                         # missing the ZWNJ; the correct form has it
    "حذفِ برنامه = پاک‌کردن پوشه",   # -> the polite phrasing "delete the app folder to uninstall"
    "لانچر",                         # -> the Persian phrase for "launcher / the py launcher file"
    # Confirmed polish from the four doc reviews — literal calques and
    # colloquial/letter-writing tone that final editing simplified:
    "پشتیبان غریبه",       # -> the Persian phrase for "someone else's backup"
    "جان سالم به در",      # -> the Persian phrase for "are preserved"
    "خطاها صادقانه",       # -> the plain phrasing "error messages are clear and precise"
    "قرارداد کنتراست",     # -> the plain phrasing "meets the WCAG contrast criterion"
    "نگهبان",              # in-app jargon for "check / automated test"
    "مثل ناموجود",         # -> the plain phrasing "like a nonexistent file"
    "تعداد کارگر",         # -> the plain phrasing "amount of parallel work"
    "بازش کنید",           # -> the polite imperative "open it"
    "بی‌صدا می‌مرد",       # -> the plain phrasing "it closed without any message"
    "کامل کن، بعد بساز",   # -> the polite imperative "finish it, then build"
)


def test_fa_md_docs_do_not_regress_fixed_typos():
    md = [p for p in _repo_files() if p.suffix.lower() == ".md"]
    assert len(md) >= 10, f"sanity: expected the full .md tree, got {len(md)}"
    for p in md:
        text = p.read_text(encoding="utf-8", errors="replace")
        for typo in _STRICT_DOC_TYPOS:
            assert typo not in text, \
                f"{p.name}: fixed typo reappeared: {typo!r}"
        for word in _RETIRED_DOC_VOCAB:
            assert word not in text, \
                f"{p.name}: retired wording reappeared: {word!r}"
        # Code-related Python version numbers keep Latin digits even in Persian
        # text: "Python 3.11+", not Persian digits (BUILDING.fa.md).
        _fa_digit_py = re.search(r"پایتون\s*[۰-۹]", text)
        assert not _fa_digit_py, \
            f"{p.name}: code-related Python version numbers keep Latin digits " \
            f"(پایتون 3.11, not Persian digits): ...{_fa_digit_py.group(0)}..."


# ------------------------------------------------------------ release guide
def test_releasing_guide_carries_release_rules():
    """The three rules added after the pre-release reviews."""
    en = (REPO_ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    fa = (REPO_ROOT / "docs" / "RELEASING.fa.md").read_text(encoding="utf-8")
    # 1) the destination repository must hold the content, not just resolve
    assert "not a valid GPL source offer" in en
    assert "عرضه‌ی معتبرِ کد منبع طبق GPL نیست" in fa
    # 2) a public /with-ffmpeg build needs the corresponding source of that build
    assert "corresponding source and build configuration" in en
    assert "کدِ منبعِ متناظر و تنظیماتِ ساخت" in fa
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
            resolved = (p.parent / path_part).resolve()
            if REPO_ROOT != resolved and REPO_ROOT not in resolved.parents:
                continue                                  # GitHub route: ../../releases/latest
            assert resolved.is_file() or resolved.is_dir(), \
                f"{rel}: broken link -> {target}"


# ------------------------------------------------- in-page anchors
# Two broken Persian anchors: headings had changed from plural
# to singular ezafe form during a wording sweep, but the table-of-contents
# anchors kept the old form — clicking them in GitHub led nowhere. When
# building anchors GitHub drops U+200C and punctuation; the github-slugger
# algorithm is reimplemented here so the "TOC anchor ↔ heading" pair stays
# locked forever.


def _github_anchor_slug(title: str) -> str:
    """Exactly what GitHub does with headings: lowercase, drop everything but
    letters/digits/spaces/dashes/underscores (including U+200C and
    punctuation), then spaces to dashes. Combining marks (Mn) stay, like
    GitHub's own."""
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
# Per-release working files (reports/notes/patches) must never be committed to
# the repo — they are local artifacts, not repository content. One of them
# was once committed by accident and turned the whole CI red, because the
# version guard saw "the next release number of that time" inside its own
# text. Now:
# 1) these files are exempt from the version scan (mentioning "the next
#    release" in their text is natural);
# 2) the dedicated test_working_files_are_not_tracked guard keeps them out of
#    commits, with a message explaining why — not a misleading "stale
#    version".
# The same went for the local packaging helper sheet (_READ-ME-FIRST*) —
# it says to unstage itself before committing; the same
# guard now catches it too, and .gitignore also blocks git add -A.
# Delivered patch files once entered the repo via "git add ." and turned
#   the whole CI red. Now every .patch is banned, and *.patch in .gitignore
#   also blocks a repeat of "git add ."; legacy report-file names are
#   still detected, so none of them can come back through a careless add.
_LOCAL_ARTIFACT_RE = re.compile(r"(^ROUND\d+-|\.patch$)", re.IGNORECASE)
_DELIVERY_README_RE = re.compile(r"^_READ-ME-FIRST", re.IGNORECASE)


# ------------------------------------------------------------ version policy
# 1.0.0 is the first stable release. Until it is published, fixes are
# logged as short one-line bullets inside the [1.0.0] section of both
# changelogs; after publication, versions follow Semantic Versioning
# (1.0.1, 1.1.0, ...). This guard:
# 1) pins APP_VERSION to 1.0.0 (an intentional bump must update this test in
#    the same commit);
# 2) keeps the pre-1.0 era (0.5x.y numbers and the "0.x.y" series wording)
#    from returning as the app's version in docs;
# 3) keeps the old history file name from returning; licenses/ files are
#    exempt (third-party verbatim legal texts). THIRD_PARTY_NOTICES.md is
#    scanned because the real verbatim texts live in licenses/*.txt.
_FROZEN_APP_VERSION = "1.0.0"
_STALE_PRE_1X_RE = re.compile(r"0\.5[0-9]\.[0-9]")
_STALE_0XY_SERIES = "0.x.y"
_GUARD_ERA_EXEMPT_DIRS = ("licenses/",)


def test_no_stale_version_or_history_name_in_docs():
    # the freeze rule: bumping the version is an explicit maintainer
    # decision, never a side effect of fixing bugs
    assert _app_version() == _FROZEN_APP_VERSION, (
        "APP_VERSION must stay 1.0.0 until publication (fixes are logged as "
        "bullets under [1.0.0] in both changelogs); after publication an "
        "intentional bump must update this guard in the same commit")
    for p in _repo_files():
        if p.suffix.lower() != ".md":
            continue
        if _LOCAL_ARTIFACT_RE.match(p.name):
            continue                      # per-release report — the dedicated guard covers it
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(_GUARD_ERA_EXEMPT_DIRS):
            continue          # verbatim legal texts — exempt
        text = p.read_text(encoding="utf-8", errors="replace")
        assert "HISTORY-v1-v49" not in text, f"{rel}: old history file name"
        m = _STALE_PRE_1X_RE.search(text)
        assert not m, (
            f"{rel}: stale pre-1.0 version {m.group(0)!r} — the current "
            "version is 1.0.0; the 0.x numbering era must not return as the "
            "app version in public docs (licenses/ files are exempt)")
        assert _STALE_0XY_SERIES not in text, (
            f"{rel}: stale '0.x.y' series wording — the supported-version "
            "wording refers to the latest release without a series name")


def test_working_files_are_not_tracked():
    """Per-release working files and the "read this first" sheet have no
    place in the repo.

    One such report was committed by accident and, because it contained
    "the next release number of that time", the version guard failed on every
    platform. The packaging helper sheet repeated the same mistake — it is
    not repository content (the repo tests only see .md
    files, so CI stayed green and the error stayed hidden). If such files are
    committed again, this test fails with a clear message and the exact
    command to run.
    """
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no .git — fallback walk mode")
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                           capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")
    bad_artifact, bad_readme = [], []
    for raw in r.stdout.split(b"\x00"):
        if not raw:
            continue
        name = raw.decode("utf-8", "replace")
        base = Path(name).name
        if _LOCAL_ARTIFACT_RE.match(base):
            bad_artifact.append(name)
        elif _DELIVERY_README_RE.match(base):
            bad_readme.append(name)
    assert not bad_artifact, (
        "per-release working files (reports/notes/patches) are local "
        "artifacts, not repo content — run: git rm --cached <file> (files stay "
        f"local; the *.patch and *-report*.md rules in .gitignore keep them out): {bad_artifact}")
    assert not bad_readme, (
        '_READ-ME-FIRST* is the local packaging helper sheet, not repo '
        'content — run: git rm --cached "_READ-ME-FIRST.fa.txt"  '
        f"(file stays local; .gitignore keeps it out): {bad_readme}")


def test_development_diary_is_not_published():
    """The pre-1.0 development diary is internal working material — it must
    never be tracked in the public repo again."""
    tracked = {p.relative_to(REPO_ROOT).as_posix() for p in _repo_files()}
    assert "docs/HISTORY-pre-1.0.fa.md" not in tracked, (
        "docs/HISTORY-pre-1.0.fa.md must not be tracked again — the "
        "development diary stays out of the public repo"
    )


# ------------------------------------------------------------ internal labels
# Version-label rule: a bare internal label is a v-token with
# N in 1..51, not followed by '.'/digit/word (excludes v1.0.0, v25.01, v48r3)
# and not preceded by '|'/word/'@' (excludes |v4 cache eras, checkout@v4).
# Malformed labels fail too: 'v.' + digit, 0.0.N.0, and a
# 0.N.0 label glued directly to a Persian letter.
_VN_RE = re.compile(r"(?<![|\w.@])v(\d{1,2})(?![.\d\w])")
_VDOT_RE = re.compile(r"(?<![|\w.@])v\.(\d{1,2})(?!\d)")
_DBL_ZERO_RE = re.compile(r"0\.0\.(\d{1,2})\.0")
# Internal development-iteration labels must not re-enter comments — they
# were removed everywhere; the detection pattern keeps its keyword so a
# comeback is still caught. Hyphen-glued file-name mentions stay legal
# via the trailing "-".
_DEV_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])round\s+(\d+)(?![A-Za-z0-9_-])", re.I)
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
    for m in _DEV_LABEL_RE.finditer(text):
        yield "development-iteration label"


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
        "(drop the label or reword the sentence): "
        f"{offenders[:10]}"
    )


# ------------------------------------------------------------ developer text
# Contributor rule (CONTRIBUTING): developer-facing text — comments,
# docstrings — is English; Persian belongs to UI strings (i18n keys,
# T(...) sources) and the fa docs. The comment-translation pass proved
# that a plain tokenize scan is NOT enough: the stress test's child script
# lives inside a STRING constant, so its comments are string content and a
# COMMENT-token scan never sees them. This test therefore scans three
# surfaces: comment tokens, docstrings, and #-comment lines INSIDE string
# constants (embedded scripts).
_FA_RE = re.compile(r"[\u0600-\u06FF]")


def test_no_persian_in_developer_comments_docstrings_or_embedded_scripts():
    offenders = []
    for p in _repo_files():
        if p.suffix != ".py":
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        src = p.read_text(encoding="utf-8")
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
            tree = ast.parse(src)
        except (tokenize.TokenError, SyntaxError, IndentationError):
            continue                                  # compileall covers this
        for tok in toks:
            if tok.type == tokenize.COMMENT and _FA_RE.search(tok.string):
                offenders.append(f"{rel}:{tok.start[0]} comment "
                                 f"{tok.string.strip()[:60]!r}")
        doc_spans = _docstring_spans(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            lines = node.value.splitlines()
            at = node.lineno
            if (at, node.col_offset) in doc_spans:
                if _FA_RE.search(node.value):
                    offenders.append(f"{rel}:{at} docstring "
                                     f"{node.value.strip()[:60]!r}")
                continue
            for i, line in enumerate(lines, start=1):
                if line.lstrip().startswith("#") and _FA_RE.search(line):
                    offenders.append(f"{rel}:{at + i - 1} embedded-comment "
                                     f"{line.strip()[:60]!r}")
    assert not offenders, (
        "Persian remains in developer-facing text (comment / docstring / "
        "embedded script) — translate it or move it to a UI string: "
        f"{offenders[:10]}"
    )


def _docstring_spans(tree: ast.AST) -> set:
    """(lineno, col_offset) of every docstring Constant node."""
    spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
               and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                spans.add((body[0].value.lineno, body[0].value.col_offset))
    return spans


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
    """The GPL source URL must be identical in appinfo and BUILD.bat.

    BUILD.bat's own header says both must change "at the same time", otherwise
    the source offer inside the portable package (SOURCE.txt) breaks. appinfo
    is not imported (no app module is imported here) — it is read with ast."""
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
# The app was renamed to FolderDex — the official repo moved with it.
# These guards keep the old name/link from sneaking back.
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
    """The brand name is FolderDex in both languages — not the old Persian/English pair."""
    assert _appinfo_str("APP_NAME") == "FolderDex"
    assert _appinfo_str("APP_NAME_EN") == "FolderDex"


def test_build_appname_matches_the_brand():
    """APPNAME in BUILD.bat builds the exe/dist/zip names and must stay equal to
    the app's display name; otherwise the portable package drifts apart from
    the app UI."""
    build = (REPO_ROOT / "BUILD.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "APPNAME=(\S+)"', build)
    assert m, "APPNAME not found in BUILD.bat"
    assert m.group(1) == "FolderDex" == _appinfo_str("APP_NAME_EN"), \
        "BUILD.bat APPNAME diverged from the app brand"


def test_official_links_are_the_public_repo():
    """The GPL source offer must point to the official public repo everywhere —
    the old private repo address must not survive in user-facing docs."""
    build = (REPO_ROOT / "BUILD.bat").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'set "REPO_URL=(\S+)"', build)
    assert _appinfo_str("SOURCE_URL") == _OFFICIAL_REPO
    assert m and m.group(1) == _OFFICIAL_REPO
    for rel in ("README.md", "README.fa.md",
                "docs/RELEASING.md", "docs/RELEASING.fa.md",
                ".github/ISSUE_TEMPLATE/config.yml"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        # The retired owner/repo handle is assembled from char codes so the
        # literal never appears anywhere in this repository's text.
        _old = ("".join(map(chr, (103, 99, 97, 114, 116, 53, 52, 52, 54, 45, 99, 109, 100)))
                + "/" + "".join(map(chr, (116, 101, 115, 101, 114))))
        assert _old not in text, f"{rel}: old repo link remains"
        assert _OFFICIAL_REPO in text, f"{rel}: official repo link missing"


def test_readmes_are_fully_rebranded():
    """The README is the project's storefront: the new name, the new package
    name and no trace of the previous brand (history belongs in the
    CHANGELOG, not the README)."""
    for rel in ("README.md", "README.fa.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "CoverShelf" not in text, f"{rel}: old English brand remains"
        assert "قفسه‌ی کاور" not in text, f"{rel}: old Persian brand remains"
        assert "# FolderDex" in text, f"{rel}: title must be the new brand"
        assert "FolderDex-win64-portable.zip" in text, f"{rel}: package name"
        assert "FolderDex.exe" in text, f"{rel}: executable name"


# ------------------------------------------------------------ AI note
def test_ai_note_is_short_and_claim_free():
    """The development note is deliberately one short, claim-free sentence:
    it only discloses that the project was written with the help of AI. No
    review/ownership claims and no review-responsibility disclaimer — the
    exact wording is locked in both READMEs."""
    fa = (REPO_ROOT / "README.fa.md").read_text(encoding="utf-8")
    en = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert ("**ساخته‌شده با هوش مصنوعی:** این پروژه با کمک هوش مصنوعی"
            " نوشته شده است.") in fa, \
        "README.fa.md: AI note wording changed"
    assert ("**Built with AI:** this project was written with the help"
            " of AI.") in en, \
        "README.md: AI note wording changed"
    for rel, text in (("README.fa.md", fa), ("README.md", en)):
        assert "مسئولیت بررسی" not in text, \
            f"{rel}: the review-responsibility disclaimer must stay out"
        assert "Responsibility for reviewing" not in text, \
            f"{rel}: the review-responsibility disclaimer must stay out"
    # the previous wording claimed review/ownership of every change — it must
    # not sneak back into either README
    assert "owned by me" not in en, "README.md: ownership claim must stay out"
    assert "reviewed, tested" not in en, "README.md: review claim must stay out"
    assert "مسئولیت نهایی" not in fa, "README.fa.md: ownership claim must stay out"
    assert "بازبینی و تست" not in fa, "README.fa.md: review claim must stay out"


# ------------------------------------------------------------ BiDi punctuation
# Persian-text review, category 1.1: when Persian "," or ";" immediately
# follows a Latin letter/digit, the mark jumps to the other side of the word
# in RTL display. A closing parenthesis and backtick are direction-neutral and
# their pattern is allowed (review category 1.2). If truly needed, put an RLM
# (U+200F) between the Latin letter and the mark, or reorder the list.
_BIDI_PUNCT_RE = re.compile(r"[A-Za-z0-9][،؛؟]")


def test_fa_docs_no_latin_glued_to_persian_punctuation():
    offenders = []
    for p in _repo_files():
        rel = p.relative_to(REPO_ROOT).as_posix()
        if p.suffix.lower() != ".md":
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
    (Signal(object) payloads → bool_dealloc SIGABRT; see the changelog
    entries). requirements.txt must keep the python_version
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


# ------------------------------------------------------------ Dependabot pin
def test_dependabot_keeps_hands_off_pyside6_pin():
    """The PySide6-Essentials pin is managed by hand via python_version markers
    — both the requirements guard (the test above) and the CI step assert
    those exact lines. A one-shot Dependabot bump would edit those lines and
    turn all five jobs red (the dependabot/pip/pyside6-essentials-… branch).
    dependabot.yml must keep pyside6-essentials out of version updates;
    security updates come through the alerts tab and this ignore does not
    block them."""
    text = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert text.count("package-ecosystem: pip") == 1, \
        "dependabot.yml: expected exactly one pip update block"
    pip_block = text.split("package-ecosystem: pip", 1)[1]
    assert re.search(r"^\s+ignore:\s*$", pip_block, re.M), \
        "dependabot.yml: pip updates lost the ignore block"
    assert re.search(r"-\s*dependency-name:\s*pyside6-essentials\s*$", pip_block, re.M), \
        "dependabot.yml: pyside6-essentials must stay out of version updates"
    actions_block = text.split("package-ecosystem: pip", 1)[0]
    assert "pyside6-essentials" not in actions_block, \
        "dependabot.yml: the ignore belongs to the pip block, not actions"
