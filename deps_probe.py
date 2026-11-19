# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari
"""Dependency preflight for the launcher (run.bat).

An import-only check cannot see a stale environment: an old py7zr/rarfile
stays importable forever, so raising the version floors in requirements.txt
never reached already-installed .venvs — they kept running the versions with
the known security fixes missing. This probe therefore compares each core
backend's installed version against the floor declared in requirements.txt
(the single source of truth — the numbers are NOT duplicated here).

Exit code 0 → every core backend imports and meets its floor.
Exit code 1 → something is missing or stale; run.bat installs requirements
again. The offline behavior is unchanged: a failed pip attempt writes the
`.requirements_failed` marker and later launches skip the install.

Everything is stdlib on purpose — this runs before any dependency exists.
"""
from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path

_CORE = ("py7zr", "rarfile", "pypdfium2")


def required_min() -> dict:
    """name → version floor from requirements.txt (only `name>=x` lines)."""
    mins = {}
    try:
        text = (Path(__file__).resolve().parent / "requirements.txt").read_text(
            encoding="utf-8")
    except OSError:
        return mins
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ";" in line:                       # strip environment markers
            line = line.split(";", 1)[0].strip()
        m = re.match(rf'^({"|".join(_CORE)})>=\s*([0-9][0-9A-Za-z_.]*)', line)
        if m:
            mins[m.group(1)] = m.group(2)
    return mins


def _ver_tuple(v: str) -> tuple:
    """Numeric prefix of a version as a comparable tuple ('1.1.4' → (1, 1, 4)).

    Deliberately simple: the floors are plain x.y.z numbers written by this
    repo, and a launcher preflight must not depend on the `packaging`
    library. A suffix (.post1 / rc1) is ignored — it never lowers the floor."""
    parts = []
    for piece in re.split(r"[._\-+]", v):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    while len(parts) > 1 and parts[-1] == 0:      # "4.5" == "4.5.0"
        parts.pop()
    return tuple(parts) or (0,)


def probe() -> int:
    """0 = ok, 1 = a core backend is missing or below its requirements floor."""
    mins = required_min()
    for name, floor in mins.items():
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            print(f"missing: {name} (required >={floor})")
            return 1
        if _ver_tuple(have) < _ver_tuple(floor):
            print(f"stale: {name} {have} < required {floor}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(probe())
