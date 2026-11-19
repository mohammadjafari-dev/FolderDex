# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Page-level navigation history (browser-style back/forward
for the mouse side buttons).

Pure, Qt-free module so it can be used both from main.py and from headless
logic tests (no UI).

Browser-like model: one list plus one cursor.
  • record() called from the middle of the history truncates the tail
    (everything forward of it).
  • Consecutive identical entries are not recorded (clicking the same
    category again does not clutter the history).
  • The CAP limit drops the oldest entries so memory stays bounded.

A state is a 4-tuple built/interpreted by main.py itself:
    ("lib", filter_kind, filter_cat, "")     ← library page
    ("det", "", "", str(item.path))          ← details page of a title
"""
from __future__ import annotations


class NavHistory:
    CAP = 120

    def __init__(self):
        self.states: list = []
        self.idx: int = -1

    # ------------------------------------------------------------- record
    def record(self, state) -> None:
        """Record a new state; recording from mid-history drops the tail (browser behavior)."""
        if 0 <= self.idx < len(self.states) and self.states[self.idx] == state:
            return                                  # consecutive duplicate — ignore
        del self.states[self.idx + 1:]
        self.states.append(state)
        if len(self.states) > self.CAP:
            del self.states[: len(self.states) - self.CAP]
        self.idx = len(self.states) - 1

    # ------------------------------------------------------------ queries
    def can_back(self) -> bool:
        return self.idx > 0

    def can_forward(self) -> bool:
        return 0 <= self.idx < len(self.states) - 1

    def current(self):
        return self.states[self.idx] if 0 <= self.idx < len(self.states) else None

    # ------------------------------------------------------------ movement
    def back(self):
        """One step back → the destination state (caller must apply it to the host)."""
        if not self.can_back():
            return self.current()
        self.idx -= 1
        return self.states[self.idx]

    def forward(self):
        if not self.can_forward():
            return self.current()
        self.idx += 1
        return self.states[self.idx]
