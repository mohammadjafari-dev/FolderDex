# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Round 14 regression — ubuntu CI died with pytest exit code 2 (Interrupted).

``import PySide6.QtWidgets`` on a runner without the system GL stack raises
``ImportError: libEGL.so.1: cannot open shared object file …``; pytest treats
that as a collection error of every Qt-importing module → ``Interrupted:
N errors during collection`` → exit code 2, with no summary at all (and
``importorskip`` cannot mask it since pytest 8). These tests pin the two
halves of the fix: the preflight verdict agrees with a real import, and the
failure message names the missing library plus the apt command.
"""

from __future__ import annotations

import importlib

import qt_runtime_probe as probe

_LIBEGL_ERR = (
    "ImportError: libEGL.so.1: cannot open shared object file: No such file or directory"
)


def test_preflight_hint_names_the_missing_library_and_the_fix():
    hint = probe.qt_runtime_hint(_LIBEGL_ERR)
    assert "libEGL.so.1" in hint  # exact .so named
    assert "libegl1" in hint and "libgl1" in hint  # exact apt packages
    assert "apt-get" in hint
    assert "docs/BUILDING.md" in hint


def test_preflight_hint_without_a_soname_still_points_at_the_fix():
    hint = probe.qt_runtime_hint("OSError: dlopen failed for some opaque reason")
    assert "libegl1" in hint and "libgl1" in hint
    assert "docs/BUILDING.md" in hint


def test_probe_verdict_matches_a_raw_import():
    """On any healthy environment the probe must agree with a real import."""
    try:
        importlib.import_module("PySide6.QtWidgets")
        raw = "loads"
    except ModuleNotFoundError as exc:
        raw = "not-installed" if (exc.name or "").split(".")[0] == "PySide6" else "broken"
    except Exception:  # noqa: BLE001 — same shapes the probe must catch
        raw = "broken"

    problem = probe.qt_runtime_problem()
    if raw in ("loads", "not-installed"):
        assert problem is None
    else:
        assert problem is not None
        assert "apt-get" in probe.qt_runtime_hint(problem)
