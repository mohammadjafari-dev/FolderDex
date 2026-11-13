# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Shared pytest setup — no display, no Windows required.

- QT_QPA_PLATFORM=offscreen is set BEFORE any repo import, so tests never need
  a monitor even if a module pulls PySide6 transitively.
- REPO_ROOT goes on sys.path so `import scanner` / `from store import Store`
  work no matter where pytest is invoked from.
- Round 14 preflight: the bundled PySide6 links against the *system* GL stack
  (libEGL.so.1 / libGL.so.1). Bare CI runner images ship without it, and the
  first Qt-importing test modules (round 11) made collection fail there with
  `ImportError: libEGL.so.1 …` → `Interrupted: N errors during collection`
  → pytest exit code 2, with no summary at all. The preflight below fails
  the run once, before collection, with one actionable line instead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from qt_runtime_probe import qt_runtime_hint, qt_runtime_problem


def pytest_configure(config):
    """Preflight the Qt runtime BEFORE collection (round 14).

    A missing system library used to surface as `ImportError: libEGL.so.1 …`
    inside `Interrupted: N errors during collection` (pytest exit code 2,
    no summary). One UsageError before collection instead: pytest exit code
    4, one readable `ERROR:` block naming the `.so` and the apt command.
    """
    _problem = qt_runtime_problem()
    if _problem is not None:
        raise pytest.UsageError(qt_runtime_hint(_problem))
