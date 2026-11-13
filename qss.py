# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""پوسته‌های برنامه (تم‌ها).

هشت تم داخلی:
    dark   → تاریک آبی    (پیش‌فرض)
    dim    → تاریک خنثی   (شبیه GitHub Dark)
    amoled → مشکی خالص
    light  → روشن
    forest → سبزِ خزه‌ایِ آرام        (round 13 §۸)
    sunset → کهربا/آجریِ گرم          (round 13 §۸)
    ocean  → فیروزه‌ایِ عمیق          (round 13 §۸)
    paper  → روشنِ سپیای گرم          (round 13 §۸)

هر تم یک دیکشنری رنگ است با دقیقاً همان کلیدهای «dark»؛ قالب QSS با نشانه‌های
@نام@ پر می‌شود. کنتراستِ چهار پالتِ جدیدِ §۸ با luminance استاندارد محاسبه
شده (text/bg ≥ 7، muted/bg ≥ 4.5، accent/bg ≥ 3 — جدولِ بریف) و با

tests/test_themes_natural.py محافظت می‌شود. تم انتخابی در config.json ذخیره
می‌شود (کلید «theme»). قانونِ ثابت: Hero و اسپلش در همه‌ی تم‌ها تیره می‌مانند
(رنگ‌هایِ #Hero/#Splash در _TEMPLATE ثابت‌اند).
"""
from __future__ import annotations

THEMES = {
    "dark": {
        "label": "تاریک آبی",
        "bg": "#0c111a", "side": "#0a0e15",
        "panel": "#111927", "panel2": "#131b29",
        "cardg1": "#141d2c", "cardg2": "#101826",
        "border": "#1c2534", "border2": "#26334a",
        "hover": "#16202f", "hover2": "#182338", "sel": "#1d2b42",
        "text": "#e7edf6", "muted": "#93a1b5", "dim": "#64748b",
        "accent": "#3d5af1", "accent_h": "#4c68f5",
        "accent2": "#6a5cff", "accent2_h": "#7b6dff",
        "scroll": "#26334a", "scroll_h": "#33507c",
        "tip_bg": "#131b29", "tip_border": "#33507c", "disabled": "#5b6b82",
    },
    "dim": {
        "label": "تاریک خنثی",
        "bg": "#0d1117", "side": "#090c10",
        "panel": "#161b22", "panel2": "#1a212b",
        "cardg1": "#1a212b", "cardg2": "#151a22",
        "border": "#2d333b", "border2": "#373e47",
        "hover": "#1c2129", "hover2": "#21262e", "sel": "#2a3340",
        "text": "#e6edf3", "muted": "#9198a1", "dim": "#636e7b",
        "accent": "#2f81f7", "accent_h": "#3f8cff",
        "accent2": "#6e56cf", "accent2_h": "#7f68dd",
        "scroll": "#30363d", "scroll_h": "#444c56",
        "tip_bg": "#1a212b", "tip_border": "#444c56", "disabled": "#5a636d",
    },
    "amoled": {
        "label": "مشکی خالص",
        "bg": "#000000", "side": "#050507",
        "panel": "#0d0d12", "panel2": "#111117",
        "cardg1": "#13131a", "cardg2": "#0c0c11",
        "border": "#20202a", "border2": "#2b2b38",
        "hover": "#16161e", "hover2": "#1a1a24", "sel": "#23233a",
        "text": "#f2f2f5", "muted": "#9a9aa8", "dim": "#6a6a78",
        "accent": "#7c6cff", "accent_h": "#8d7eff",
        "accent2": "#5ac8fa", "accent2_h": "#6fd2ff",
        "scroll": "#262633", "scroll_h": "#38384a",
        "tip_bg": "#14141c", "tip_border": "#3a3a4d", "disabled": "#565664",
    },
    "light": {
        "label": "روشن",
        "bg": "#f3f5f9", "side": "#e9edf3",
        "panel": "#ffffff", "panel2": "#f6f8fb",
        "cardg1": "#ffffff", "cardg2": "#eef1f6",
        "border": "#d7dde6", "border2": "#c6cfdb",
        "hover": "#e8edf4", "hover2": "#dde4ee", "sel": "#d3deee",
        "text": "#1c2430", "muted": "#5c6672", "dim": "#8a94a1",
        "accent": "#3d5af1", "accent_h": "#2f4bd8",
        "accent2": "#6a5cff", "accent2_h": "#5948e8",
        "scroll": "#c3ccd8", "scroll_h": "#a8b4c4",
        "tip_bg": "#ffffff", "tip_border": "#a8b4c4", "disabled": "#9aa4b0",
    },
    # ---- round 13 §۸ — چهار پالتِ طبیعی‌تر (خواسته‌ی کاربر: «تم‌ها زیباتر و
    # طبیعی‌تر شوند؛ هیچ تصویر/بلوری به‌عنوان پس‌زمینه»). کلیدها عیناً همان
    # کلیدهایِ «dark»؛ کنتراست‌ها با luminance استاندارد محاسبه و تأیید شده‌اند:
    # text/bg ≈ ۱۵٫۹–۱۳٫۵، muted/bg ≈ ۸٫۱–۵٫۴، accent/bg ≈ ۵٫۵–۴٫۲.
    "forest": {
        "label": "جنگل",
        "bg": "#0e1512", "side": "#0a110e",
        "panel": "#141f19", "panel2": "#18241d",
        "cardg1": "#18241d", "cardg2": "#131d17",
        "border": "#22322a", "border2": "#2e4438",
        "hover": "#17261e", "hover2": "#1b2d23", "sel": "#1f3a2b",
        "text": "#e6f0ea", "muted": "#9bb0a4", "dim": "#6f8477",
        "accent": "#2f9e66", "accent_h": "#3cae75",
        "accent2": "#b8862f", "accent2_h": "#c99439",
        "scroll": "#2e4438", "scroll_h": "#3d5a49",
        "tip_bg": "#18241d", "tip_border": "#3d5a49", "disabled": "#5f7367",
    },
    "sunset": {
        "label": "غروب",
        "bg": "#171211", "side": "#110d0c",
        "panel": "#1f1816", "panel2": "#241c19",
        "cardg1": "#241c19", "cardg2": "#1c1513",
        "border": "#332622", "border2": "#443229",
        "hover": "#241a17", "hover2": "#2a1e1a", "sel": "#3a271f",
        "text": "#f5ece5", "muted": "#b3a196", "dim": "#867469",
        "accent": "#d46a33", "accent_h": "#e07a42",
        "accent2": "#b5527a", "accent2_h": "#c46489",
        "scroll": "#443229", "scroll_h": "#5a4238",
        "tip_bg": "#241c19", "tip_border": "#5a4238", "disabled": "#7a675c",
    },
    "ocean": {
        "label": "اقیانوس",
        "bg": "#0a1317", "side": "#070f12",
        "panel": "#0f1c22", "panel2": "#122128",
        "cardg1": "#122128", "cardg2": "#0e1a1f",
        "border": "#1b2c34", "border2": "#26404b",
        "hover": "#11222a", "hover2": "#14282f", "sel": "#183542",
        "text": "#e2eef2", "muted": "#8fa9b3", "dim": "#647d87",
        "accent": "#1f93a8", "accent_h": "#2aa3b8",
        "accent2": "#5b7ae8", "accent2_h": "#6b8af7",
        "scroll": "#26404b", "scroll_h": "#335563",
        "tip_bg": "#122128", "tip_border": "#335563", "disabled": "#566c76",
    },
    "paper": {
        "label": "کاغذ",
        "bg": "#f5f0e6", "side": "#ece5d7",
        "panel": "#fffdf8", "panel2": "#f7f2e8",
        "cardg1": "#fffdf8", "cardg2": "#f2ece0",
        "border": "#ddd3c2", "border2": "#cbbea9",
        "hover": "#eee7d9", "hover2": "#e6ddcc", "sel": "#e0d5c0",
        "text": "#2b241b", "muted": "#6a6053", "dim": "#8d8273",
        "accent": "#a8612b", "accent_h": "#94531f",
        "accent2": "#7a7f3d", "accent2_h": "#687030",
        "scroll": "#cbbea9", "scroll_h": "#b0a48d",
        "tip_bg": "#fffdf8", "tip_border": "#b0a48d", "disabled": "#9c9184",
    },
}

DEFAULT_THEME = "dark"


def theme_names() -> list[str]:
    return list(THEMES.keys())


def theme_bg(name: str) -> str:
    """رنگِ پس‌زمینه‌ی صفحه (@bg@) برای یک تم — 0.32.0: پس‌زمینه‌ی پوسترهای
    letterbox باید دقیقاً رنگِ خودِ تم باشد (نه بلورِ تصویر)."""
    return THEMES.get(name, THEMES[DEFAULT_THEME]).get("bg", "#0c111a")


def theme_label(name: str) -> str:
    return THEMES.get(name, THEMES[DEFAULT_THEME])["label"]


def build_theme(name: str) -> str:
    c = THEMES.get(name, THEMES[DEFAULT_THEME])
    css = _TEMPLATE
    for k, v in c.items():
        if k != "label":
            css = css.replace("@" + k + "@", v)
    return css


# قانون: هدر Hero و اسپلش در همه‌ی تم‌ها تیره می‌مانند (سبک بنر تیره).
_TEMPLATE = """
* { outline: none; }
QWidget { background: @bg@; color: @text@; font-size: 13px; }

/* ---------------- سایدبار ---------------- */
#SideBar { background: @side@; border-left: 1px solid @border@; }
#AppTitle { font-size: 17px; font-weight: 800; padding: 6px 2px 2px; background: transparent; }
#BrandSub { color: @dim@; font-size: 10px; letter-spacing: 1px; background: transparent; }
#SideLabel { color: @muted@; font-size: 11px; padding-top: 8px; background: transparent; }
#StatsLabel { color: @muted@; font-size: 11.5px; padding: 6px 2px;
              border-top: 1px solid @border@; background: transparent; }

QListWidget#CatList { background: transparent; border: none; }
QListWidget#CatList::item { padding: 8px 10px; border-radius: 8px; margin: 1px 0; }
QListWidget#CatList::item:hover { background: @hover@; }
QListWidget#CatList::item:selected { background: @sel@; border: none; }

/* درخت دسته‌بندی سایدبار */
QTreeWidget#CatTree { background: transparent; border: none; }
QTreeWidget#CatTree::item { padding: 7px 8px; border-radius: 8px; margin: 1px 0; }
QTreeWidget#CatTree::item:hover { background: @hover@; }
QTreeWidget#CatTree::item:selected { background: @sel@; }
QTreeWidget#CatTree::branch { background: transparent; }
QTreeWidget#CatTree::branch:has-children:closed { image: none; margin: 4px; }
QTreeWidget#CatTree::branch:has-children:open { image: none; margin: 4px; }

/* درخت فایل‌ها در صفحه‌ی جزئیات */
QTreeWidget#FileTree { background: @panel@; border: 1px solid @border@;
                       border-radius: 12px; padding: 6px; }
QTreeWidget#FileTree::item { padding: 5px 4px; border-radius: 7px; }
QTreeWidget#FileTree::item:hover { background: @hover2@; }
QTreeWidget#FileTree::item:selected { background: @sel@; }
QTreeWidget#FileTree::branch { background: transparent; }
QTreeWidget#FileTree::branch:has-children:closed {
  image: none; border: none; }
QTreeWidget#FileTree::branch:has-children:open {
  image: none; border: none; }

/* هدر Hero صفحه‌ی جزئیات — در همه‌ی تم‌ها تیره (سبک بنر) */
#Hero { background: #111927; border: 1px solid #26334a; border-radius: 14px; }
#HeroTitle { color: #ffffff; font-size: 21px; font-weight: 800; background: transparent; }
#Hero QLabel { background: transparent; }

/* کارت بندانگشتی فایل‌ها */
#ThumbCard { background: @panel@; border: 1px solid @border@; border-radius: 12px; }
#ThumbCard:hover { background: @hover2@; border-color: @accent@; }

/* دکمه‌ی تغییر حالت نمایش فایل‌ها */
#ModeBtn { padding: 5px 12px; font-size: 11.5px; border-radius: 8px; }
#ModeBtn:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                   stop:0 @accent@, stop:1 @accent2@);
                   border-color: transparent; color: #fff; font-weight: bold; }

/* سربرگ سکشن‌ها در حالت بندانگشتی */
#SectionToggle { background: @panel@; border: 1px solid @border@; border-radius: 9px;
                 text-align: right; padding: 7px 12px; font-weight: bold; color: @text@; }
#SectionToggle:hover { border-color: @scroll_h@; background: @hover@; }

QGroupBox { border: 1px solid @border2@; border-radius: 10px; margin-top: 12px;
            padding: 10px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; right: 12px; padding: 0 6px; }

/* ---------------- هدر بخش محتوا ---------------- */
#PageTitle { font-size: 19px; font-weight: 800; background: transparent; }
#PageSub { color: @muted@; font-size: 12px; padding-bottom: 3px; background: transparent; }

/* ---------------- صفحه‌ی لودینگ (همیشه تیره) ---------------- */
#Splash { background: #0f1524; border: 1px solid #26334a; border-radius: 20px; }
#SplashTitle { color: #fff; font-size: 20px; font-weight: 800; background: transparent; }
#SplashSub { color: #64748b; font-size: 10px; letter-spacing: 3px; background: transparent; }
#SplashStatus { color: #93a1b5; font-size: 11px; background: transparent; }
QProgressBar, #SplashBar { background: @panel@; border: none; border-radius: 4px; }
QProgressBar::chunk, #SplashBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 @accent@, stop:1 @accent2@);
    border-radius: 4px;
}

/* ---------------- کارت‌های کتابخانه ---------------- */
#Card { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 @cardg1@, stop:1 @cardg2@);
        border: 1px solid @border@; border-radius: 14px; }
#Card:hover { background: @hover2@; border-color: @accent@; }
#CardTitle { font-size: 13px; font-weight: 600; background: transparent; }
#MutedLabel { color: @muted@; font-size: 11px; background: transparent; }

/* ---------------- دکمه‌ها ---------------- */
QPushButton { background: @hover@; border: 1px solid @border2@; border-radius: 10px;
              padding: 8px 14px; color: @text@; }
QPushButton:hover { background: @hover2@; border-color: @scroll_h@; }
QPushButton:pressed { background: @sel@; }
QPushButton:disabled { color: @disabled@; }
#PrimaryBtn { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 @accent@, stop:1 @accent2@);
              border: none; color: #fff; font-weight: bold; }
#PrimaryBtn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 @accent_h@, stop:1 @accent2_h@); }
#PrimaryBtn:pressed { background: @accent@; }
#MiniBtn { padding: 3px 12px; border-radius: 7px; font-size: 11.5px; }
#FlatBtn { background: transparent; border: 1px solid @border2@; }
#FlatBtn:hover { background: @hover@; }

/* ---------------- ورودی‌ها ---------------- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
    background: @panel@; border: 1px solid @border2@; border-radius: 10px;
    padding: 8px 12px; color: @text@; selection-background-color: @accent@; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: @accent@; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: @panel@; color: @text@; border: 1px solid @border2@;
    selection-background-color: @sel@; padding: 4px; }
QCheckBox { spacing: 8px; background: transparent; }

/* باکس تغییر سریع دسته روی کارت‌ها */
QComboBox#CardSwitch { font-size: 11px; padding: 2px 8px; border-radius: 7px; color: @muted@; }
QComboBox#CardSwitch:hover { color: @text@; border-color: @accent@; }

/* ---------------- صفحه‌ی جزئیات ---------------- */
#H1 { font-size: 20px; font-weight: 800; background: transparent; }
#SectionHeader { font-size: 14px; font-weight: 700; color: @text@;
                 padding: 6px 2px 2px; background: transparent; }
#FileSection { color: @muted@; font-weight: bold; font-size: 12.5px;
               padding: 4px 2px; background: transparent; }
#FileRow { background: @panel@; border: 1px solid @border@; border-radius: 10px; }
#FileRow:hover { background: @hover2@; border-color: @scroll_h@; }
#FileName { font-size: 12.5px; background: transparent; }
#DescBox { background: @panel@; border: 1px solid @border@; border-radius: 12px;
           padding: 10px 12px; }
#DescEmpty { color: @dim@; font-style: italic; }

/* ---------------- اسکرول و بقیه ---------------- */
QScrollArea, #ScrollContent { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @scroll@; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: @scroll_h@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @scroll@; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QMenu { background: @panel2@; color: @text@; border: 1px solid @border2@;
        border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 6px; }
QMenu::item:selected { background: @sel@; }
QStatusBar { background: @side@; border-top: 1px solid @border@; color: @muted@; }
QToolTip { background: @tip_bg@; color: @text@; border: 1px solid @tip_border@; padding: 6px; }
QTableWidget { background: @panel@; color: @text@; border: 1px solid @border2@;
               border-radius: 10px; gridline-color: @border@; }
QHeaderView::section { background: @side@; color: @text@; border: none;
                       border-bottom: 1px solid @border2@; padding: 6px; font-weight: bold; }
QDialog { background: @bg@; }
QMessageBox QLabel { color: @text@; }

/* دیدگاه‌های درخت/لیست عمومی — شامل انتخابگر پوشه‌ی داخلی و دیالوگ‌های فایل Qt.
   (هیچ پنجره/بخشی از برنامه رنگِ پیش‌فرضِ سفید سیستم را نمی‌گیرد) */
QTreeView, QListView { background: @panel@; color: @text@;
                       border: 1px solid @border2@; border-radius: 10px; }
QTreeView::item, QListView::item { padding: 5px 8px; border-radius: 6px; }
QTreeView::item:hover, QListView::item:hover { background: @hover2@; }
QTreeView::item:selected, QListView::item:selected { background: @sel@; color: @text@; }
QTreeView::branch:hover, QTreeView::branch:selected { background: @hover2@; }
QFileDialog QLineEdit, QFileDialog QComboBox { background: @panel2@; color: @text@; }
QFileDialog QToolButton { background: @panel2@; color: @text@; border-radius: 8px; padding: 4px; }
QFileDialog QLabel { background: transparent; }

/* تب‌های پنجره‌ی تنظیمات */
QTabWidget::pane { border: 1px solid @border@; border-radius: 10px; top: -1px; }
QTabBar::tab { background: @panel@; color: @muted@; padding: 8px 18px;
               border: 1px solid @border@; border-bottom: none;
               border-top-left-radius: 9px; border-top-right-radius: 9px; margin-left: 4px; }
QTabBar::tab:selected { background: @sel@; color: @text@; font-weight: bold; }
QTabBar::tab:hover:!selected { background: @hover2@; }
QListWidget#CatListBox { background: @panel@; border: 1px solid @border@;
                         border-radius: 12px; padding: 6px; }
QListWidget#CatListBox::item { padding: 6px 10px; border-radius: 8px; }
QListWidget#CatListBox::item:hover { background: @hover2@; }
QListWidget#CatListBox::item:selected { background: @sel@; }
"""


# پیش‌فرض برای سازگاری با ایمپورت قدیمی
THEME = build_theme(DEFAULT_THEME)
