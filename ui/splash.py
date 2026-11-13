# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""صفحه‌ی لودینگ (Splash) — هنگام اسکن اولیه به‌جای پنجره‌ی ناتمام."""
from __future__ import annotations

from i18n import T



from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget

from portable import resource_path

# BUILD/exe: در حالت PyInstaller فایلِ __file__ داخل آرشیو PYZ است و روی
# دیسک نیست — منابع باید از _MEIPASS خوانده شوند (resource_path هر دو حالت را
# می‌پوشاند).
LOGO_PATH = resource_path("assets/icon.png")


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setFixedSize(430, 320)
        from i18n import is_rtl as _rtl45     # جهت چیدمان بر اساس زبان
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if _rtl45()
            else Qt.LayoutDirection.LeftToRight)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("Splash")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(30, 30, 30, 24)
        lay.setSpacing(8)

        logo = QLabel()
        pm = QPixmap(str(LOGO_PATH))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(T("FolderDex"))
        title.setObjectName("SplashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("OFFLINE · PORTABLE · LOCAL")
        sub.setObjectName("SplashSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(6)
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(18)

        self.bar = QProgressBar()
        self.bar.setObjectName("SplashBar")
        self.bar.setRange(0, 0)      # حالت نامعین (در حال حرکت)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(9)
        lay.addWidget(self.bar)

        self.status = QLabel(T("در حال آماده‌سازی…"))
        self.status.setObjectName("SplashStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.status)
        lay.addStretch(1)

        outer.addWidget(card)

        # وسط صفحه
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center() - self.rect().center())

    def setStatus(self, text: str):
        # کوتاه کردن متن‌های بلند (مسیرها)
        fm = QFontMetrics(self.status.font())
        self.status.setText(fm.elidedText(text, Qt.TextElideMode.ElideMiddle, 380))
