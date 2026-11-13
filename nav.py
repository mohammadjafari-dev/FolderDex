# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""تاریخچه‌ی ناوبری سطح-صفحه — 0.47.0 (درخواست کاربر: «دکمه‌های کناری موس»).

ماژولِ خالصِ بدون Qt تا هم در main.py و هم در تست‌های منطقی (بدون رابط)
قابل استفاده باشد.

مدل دقیقاً مثل مرورگر است: یک لیست + یک نشانگر.
  • record از وسطِ تاریخچه، دُم (همه‌ی Forwardها) را حذف می‌کند.
  • ورودیِ مساویِ متوالی ثبت نمی‌شود (کلیکِ روی همان دسته دوباره history را
    شلوغ نمی‌کند).
  • سقفِ CAP — ورودی‌های خیلی قدیمی حذف می‌شوند تا حافظه محدود بماند.

وضعیت (state) یک تاپلِ چهارتایی است که خودِ main.py می‌سازد/تفسیر می‌کند:
    ("lib", filter_kind, filter_cat, "")     ← صفحه‌ی کتابخانه
    ("det", "", "", str(item.path))          ← صفحه‌ی جزئیاتِ یک عنوان
"""
from __future__ import annotations


class NavHistory:
    CAP = 120

    def __init__(self):
        self.states: list = []
        self.idx: int = -1

    # ------------------------------------------------------------- ثبت
    def record(self, state) -> None:
        """ثبت یک وضعیت تازه؛ از میانه‌ی تاریخچه → دُم حذف می‌شود (رفتار مرورگر)."""
        if 0 <= self.idx < len(self.states) and self.states[self.idx] == state:
            return                                  # تکراریِ متوالی — نادیده
        del self.states[self.idx + 1:]
        self.states.append(state)
        if len(self.states) > self.CAP:
            del self.states[: len(self.states) - self.CAP]
        self.idx = len(self.states) - 1

    # ------------------------------------------------------------ پرس‌وجو
    def can_back(self) -> bool:
        return self.idx > 0

    def can_forward(self) -> bool:
        return 0 <= self.idx < len(self.states) - 1

    def current(self):
        return self.states[self.idx] if 0 <= self.idx < len(self.states) else None

    # ------------------------------------------------------------ حرکت
    def back(self):
        """یک گام عقب → وضعیتِ مقصد (caller باید میزبان را روی آن اعمال کند)."""
        if not self.can_back():
            return self.current()
        self.idx -= 1
        return self.states[self.idx]

    def forward(self):
        if not self.can_forward():
            return self.current()
        self.idx += 1
        return self.states[self.idx]
