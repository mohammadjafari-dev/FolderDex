#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""ممیزیِ مکانیکیِ راند ۱۲ — هر موردِ SPEC-round12-for-AI.md با یک چکِ واقعی.

استفاده:
    python tools/audit_round12.py <repo-root>
خروجی: PASS/FAIL برای D1 تا D16 + جمعِ نهایی؛ exit 1 اگر هر موردی FAILED شد.

همه‌چیز stdlib است — هیچ Qt/pytest لازم نیست؛ روی هر ماشینی (ویندوز/لینوکس)
روی ریشه‌ی مخزن اجرا می‌شود.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []


def check(item: str, title: str, ok: bool, note: str = "") -> None:
    RESULTS.append((item, title, "PASS" if ok else "FAILED" + (f" — {note}" if note else "")))


def read(repo: Path, rel: str) -> str:
    return (repo / rel).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- D1
def audit_d1(repo: Path) -> None:
    ok_gitignore = re.search(r"^vendor/?\s*$", read(repo, ".gitignore"), re.M) is not None
    tracked = ""
    try:
        r = subprocess.run(["git", "ls-files", "vendor"], cwd=repo,
                           capture_output=True, text=True, timeout=60)
        tracked = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    check("D1", "vendor/ untracked + back in .gitignore",
          ok_gitignore and tracked == "",
          f"gitignore={'vendor/' in read(repo, '.gitignore')} tracked={tracked!r}")


# ---------------------------------------------------------------- D2
def audit_d2(repo: Path) -> None:
    thumbs = read(repo, "thumbs.py")
    filethumbs = read(repo, "filethumbs.py")
    media_info = read(repo, "media_info.py")
    main = read(repo, "main.py")
    imgcache = read(repo, "imgcache.py")
    ok = ("writable_cache_dir" in thumbs and "read_only_fallback" in thumbs
          and "writable_cache_dir" in filethumbs and "read_only_fallback" in filethumbs
          and "writable_cache_dir" in media_info and "read_only_fallback" in media_info
          and "def writable_cache_dir" in imgcache
          and "_writable_error_log_path" in main
          and "MainWindow init failed" in main)
    check("D2", "read-only app dir fallback in 3 caches + main + error-log path", ok)


# ---------------------------------------------------------------- D3
def audit_d3(repo: Path) -> None:
    covers = read(repo, "covers.py")
    store = read(repo, "store.py")
    main = read(repo, "main.py")
    ok = ("def collect_garbage" in covers
          and "delete_cover_files(stale[0]" in store
          and "_cover_gc_async" in main
          and "collect_garbage(refs)" in main)
    check("D3", "stale cover deleted on replace + collect_garbage after scan", ok)


# ---------------------------------------------------------------- D4
def audit_d4(repo: Path) -> None:
    bad = []
    for p in sorted(repo.rglob("*.py")):
        parts = p.relative_to(repo).parts
        if {"vendor", "__pycache__", ".venv", ".build-venv", "build",
                "dist", ".git"} & set(parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        t_names = {"T", "tr"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "i18n":
                t_names |= {a.asname or a.name for a in node.names
                            if a.name in ("T", "tr")}
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in list(node.args.defaults) + \
                    [d for d in node.args.kw_defaults if d]:
                if isinstance(d, ast.Call) and \
                        getattr(d.func, "id", getattr(d.func, "attr", "")) in t_names:
                    bad.append(f"{p.relative_to(repo)}:{node.lineno}")
    check("D4", "no T()/tr() in default arguments", not bad, ", ".join(bad[:5]))


# ---------------------------------------------------------------- D5
def audit_d5(repo: Path) -> None:
    covers = read(repo, "covers.py")
    actions = read(repo, "ui/cover_actions.py")
    ok = ("class CoverReadError(CoverError)" in covers
          and "class CoverWriteError(CoverError)" in covers
          and "WRITE_ERR_TEXT" in covers
          and "covers.WRITE_ERR_TEXT" in actions
          and "except OSError" in actions)
    check("D5", "CoverReadError/CoverWriteError + distinct write-error message", ok)


# ---------------------------------------------------------------- D6
def audit_d6(repo: Path) -> None:
    covers = read(repo, "covers.py")
    ok = ("MAX_SOURCE_BYTES = 32 * 1024 * 1024" in covers
          and "MAX_SOURCE_PIXELS = 40_000_000" in covers
          and "def _digest_of" in covers
          and ".read_bytes()" not in covers          # هشِ بلوکی جایگزین شد
          and "f.read(1 << 20)" in covers)
    check("D6", "32MB/40MP source caps + block-wise digest", ok)


# ---------------------------------------------------------------- D7
def audit_d7(repo: Path) -> None:
    scanner = read(repo, "scanner.py")
    # داخلِ scan_roots (بعد از تعریفش) باید dedupe با normcase باشد
    idx = scanner.find("def scan_roots")
    body = scanner[idx:] if idx >= 0 else ""
    ok = ("normcase(os.path.abspath" in body
          and "if key in seen" in body)
    check("D7", "overlapping roots deduped in scan_roots", ok)


# ---------------------------------------------------------------- D8
def audit_d8(repo: Path) -> None:
    covers = read(repo, "covers.py")
    test = read(repo, "tests/test_covers.py")
    idx = covers.find("def covers_dir")
    body = covers[idx:covers.find("def is_animated")] if idx >= 0 else ""
    ok = ("base / COVERS_DIR_NAME" in body and "Path(app_dir_path)" in body
          and 'd.name == COVERS_DIR_NAME' in test
          and 'd.glob("*.webp")' in test)
    check("D8", "covers_dir means <app>/.covers in both branches + real idempotency test", ok)


# ---------------------------------------------------------------- D9
def audit_d9(repo: Path) -> None:
    ft = read(repo, "filethumbs.py")
    idx = ft.find("def _cache_decodable")
    body = ft[idx:ft.find("def poster_png_fallback")] if idx >= 0 else ""
    ok = ("_MAGIC_OK_HEADS" in body or "_MAGIC_OK_HEADS" in ft) \
        and "f.read(12)" in body and "WEBP" in body
    check("D9", "cache-decodable uses 12-byte magic check (no full decode)", ok)


# ---------------------------------------------------------------- D10
def audit_d10(repo: Path) -> None:
    store = read(repo, "store.py")
    thumbs = read(repo, "thumbs.py")
    main = read(repo, "main.py")
    ok = ('"cache_max_mb": 2048' in store
          and "def prune_scale_generation" in thumbs
          and "_prune_scale_generation_async" in main
          and ".scale_gen" in thumbs)
    check("D10", "cache cap defaults to 2048 + old scale-generation prune", ok)


# ---------------------------------------------------------------- D11
def audit_d11(repo: Path) -> None:
    main = read(repo, "main.py")
    idx = main.find("while self._ab_running")
    body = main[idx:idx + 900] if idx >= 0 else ""
    ok = "processEvents" in body
    check("D11", "exit wait loop pumps events (no 10s UI freeze)", ok)


# ---------------------------------------------------------------- D12
def audit_d12(repo: Path) -> None:
    details = read(repo, "ui/details.py")
    ok = ("def hideEvent" in details and "def showEvent" in details
          and "_poster_media_src" in details
          and details.count("_stop_poster_movie()") >= 2)
    check("D12", "animated cover pauses when details page is hidden", ok)


# ---------------------------------------------------------------- D13
def audit_d13(repo: Path) -> None:
    en = read(repo, "docs/BUILDING.md")
    fa = read(repo, "docs/BUILDING.fa.md")
    ok = ("#the-vendor-folder" not in en and "پوشهی-vendor" not in fa)
    check("D13", "dead vendor TOC links removed from both building docs", ok)


# ---------------------------------------------------------------- D14
def audit_d14(repo: Path) -> None:
    i18n = read(repo, "i18n.py")
    store = read(repo, "store.py")
    main = read(repo, "main.py")
    dup = re.search(r'"language" not in _raw_cfg', store) is not None
    ok = ("def lang_from_config" in i18n
          and "lang_from_config(_raw_cfg)" in store
          and "lang_from_config(_raw45)" in main
          and not dup)
    check("D14", "language-migration rule lives in one place (i18n.lang_from_config)", ok)


# ---------------------------------------------------------------- D15
def audit_d15(repo: Path) -> None:
    pyproject = read(repo, "pyproject.toml")
    ok = '"B008"' in pyproject and '"vendor"' in pyproject
    check("D15", "ruff selects B008 + vendor excluded", ok)


# ---------------------------------------------------------------- D16
def audit_d16(repo: Path) -> None:
    req = read(repo, "requirements.txt")
    lines = [ln.strip() for ln in req.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    need = ("PySide6-Essentials>=6.6,<7", "Pillow>=10.0,<13", "py7zr>=1.0,<2",
            "rarfile>=4.1,<5", "pypdfium2>=4.30,<6")
    ok = all(any(ln.startswith(n.split(">=")[0]) and n.split(">=")[1] in ln
                 for ln in lines) for n in need)
    check("D16", "dependency versions capped with <major+1", ok)


def main() -> int:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    if not (repo / "main.py").is_file():
        print(f"usage: {sys.argv[0]} <repo-root>  (got {repo})")
        return 2
    fns = [globals()[n] for n in sorted(globals())
           if n.startswith("audit_d") and callable(globals()[n])]
    for fn in fns:
        fn(repo)
    failed = 0
    print(f"audit_round12 — {repo}")
    for item, title, status in RESULTS:
        mark = "✔" if status == "PASS" else "✘"
        print(f"  [{mark}] {item:<4} {status:<9} {title}")
        if status != "PASS":
            failed += 1
    print(f"RESULT: {len(RESULTS) - failed} PASS / {failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
