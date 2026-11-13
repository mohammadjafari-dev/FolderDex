# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Qt-runtime probe — round 14 (P0: ubuntu CI died with exit code 2).

Bundled PySide6 links against the *system* GL stack; GitHub runner images
(Ubuntu 24.04 and 26.04 alike) do not ship it. ``import PySide6.QtWidgets``
then dies with ``ImportError: libEGL.so.1: cannot open shared object file``,
which turns collection into ``Interrupted: N errors during collection``
(pytest exit code 2) with no test summary at all. Note that since pytest 8
``pytest.importorskip`` deliberately does NOT swallow such import errors
(it only skips on plain module-not-found), so it cannot mask this either.

Everything here is stdlib-only and importable from environments without
PySide6 (the repo-integrity job), so conftest can preflight safely.
"""

from __future__ import annotations

import importlib
import re

APT_LINE = (
    "sudo apt-get update && sudo apt-get install -y --no-install-recommends libegl1 libgl1"
)


def qt_runtime_problem() -> str | None:
    """Return the failure text if the Qt runtime cannot load, else ``None``.

    - Qt not installed at all (the repo-integrity job installs bare pytest)
      → ``None``: that job's file checks import nothing Qt-related.
    - Qt installed but its shared libraries cannot load (missing system
      libEGL/libGL on a bare runner or container) → the error text.
    """
    try:
        importlib.import_module("PySide6.QtWidgets")
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] == "PySide6":
            return None  # PySide6 simply not installed in this environment
        return f"{type(exc).__name__}: {exc}"  # broken install (e.g. no shiboken6)
    except Exception as exc:  # noqa: BLE001 — loader/OS errors come in many shapes
        return f"{type(exc).__name__}: {exc}"
    return None


def qt_runtime_hint(problem: str) -> str:
    """One actionable message for a Qt-runtime load failure."""
    sonames = sorted(set(re.findall(r"\blib[\w.+-]+\.so[\w.]*", problem)))
    missing = f"missing: {', '.join(sonames)}" if sonames else "loader failure"
    return (
        f"Qt runtime cannot be loaded in this environment ({missing}).\n"
        "This is an environment problem, not a code bug — the bundled PySide6\n"
        "links against the system GL stack (libEGL.so.1 / libGL.so.1).\n"
        f"On Debian/Ubuntu (CI runners included): {APT_LINE}\n"
        "See docs/BUILDING.md → 'Linux system dependencies'."
    )
