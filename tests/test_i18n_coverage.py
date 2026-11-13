# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پوششِ i18n — هر رشته‌ی فارسیِ داخل T(…)" باید کلیدِ انگلیسی داشته باشد.

A missing key does NOT crash anything: T() falls back to the Persian source
string, so the English UI silently shows Persian. That regression is what
this test pins. stdlib + ast only — no Qt import, so it also runs in the
repo-integrity job.

Also printed (non-fatal): the number of EN dictionary keys that no T("…")
call site uses literally — candidates for cleanup, not an error (some keys
are reached through .format() templates or disp_cat segments).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_SKIP_FILES = {"i18n.py", "i18n_en.py"}
_SKIP_DIRS = {"tests", ".git", ".venv", ".build-venv", "build", "dist",
              "__pycache__", "tools", "vendor"}


def _tracked_or_walked() -> list[Path]:
    if (REPO_ROOT / ".git").exists():
        try:
            r = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                               capture_output=True, timeout=60, check=True)
            out = []
            for raw in r.stdout.split(b"\x00"):
                if not raw:
                    continue
                name = raw.decode("utf-8")
                if name.endswith(".py"):
                    out.append(REPO_ROOT / name)
            return [p for p in out if p.is_file()]
        except (OSError, subprocess.SubprocessError):
            pass
    files = []
    for p in sorted(REPO_ROOT.rglob("*.py")):
        if _SKIP_DIRS & set(p.relative_to(REPO_ROOT).parts):
            continue
        files.append(p)
    return files


def _t_names(tree: ast.AST) -> set:
    """نام‌های محلیِ T / tr (با احتسابِ import با نامِ مستعار)."""
    names = {"tr", "T"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "i18n":
            for alias in node.names:
                if alias.name in ("tr", "T"):
                    names.add(alias.asname or alias.name)
    return names


def _en_keys() -> set:
    src = (REPO_ROOT / "i18n_en.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def test_every_t_literal_has_an_english_key():
    used: set = set()
    for p in _tracked_or_walked():
        if p.name in _SKIP_FILES or "tests" in p.parts or "tools" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = _t_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) \
                or getattr(node.func, "attr", None)
            if fname not in names or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value:                      # رشته‌ی خالی فال‌بکِ امن است
                    used.add(arg.value)
    missing = sorted(used - _en_keys())
    assert not missing, (
        "literals passed to T() without an i18n_en entry "
        f"({len(missing)}): {missing[:12]} ..."
    )


def test_unused_english_keys_are_reported_not_failed(capsys):
    keys = _en_keys()
    used: set = set()
    for p in _tracked_or_walked():
        if p.name in _SKIP_FILES or "tests" in p.parts or "tools" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = _t_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) \
                or getattr(node.func, "attr", None)
            if fname not in names or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                used.add(arg.value)
    unused = len(keys - used)
    print(f"i18n: {len(keys)} EN keys, {len(used & keys)} used literally, "
          f"{unused} never used as a T() literal (informational)")
    assert unused >= 0                    # همیشه درست — فقط گزارش


# ------------------------------------------------------------ D4 — T() در آرگومانِ پیش‌فرض
def test_no_translated_default_arguments():
    """T(...) در آرگومانِ پیش‌فرض ⇒ رشته در زمانِ import منجمد می‌شود (باگِ زبان).

    زبانِ واقعی بعد از خواندنِ config.json در main() ست می‌شود؛ کاربرِ فارسی
    در دیالوگِ انتخابِ پوشه/فایل متنِ انگلیسی می‌دید (باگِ واقعیِ
    ui/widgets.py قبل از این دور). هیچ `ast.arguments.defaults` در کلِ مخزن
    نباید شاملِ Call(func=T|tr) باشد.
    """
    bad = []
    for p in _tracked_or_walked():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in list(node.args.defaults) + \
                    [d for d in node.args.kw_defaults if d]:
                if isinstance(d, ast.Call) and \
                        getattr(d.func, "id", getattr(d.func, "attr", "")) \
                        in _t_names(tree):
                    bad.append(f"{p.relative_to(REPO_ROOT)}:{node.lineno} "
                               f"{node.name}()")
    assert not bad, \
        "translated default argument(s): " + ", ".join(bad) + \
        " — evaluate T() inside the function body instead"
