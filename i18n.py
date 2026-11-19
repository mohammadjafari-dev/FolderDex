# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""The app's bilingual engine.

Design (default **English** + persist the user's choice):
  · The translation key is the Persian string embedded in the code itself —
    hundreds of call sites stay untouched, they are just wrapped in T(…).
    A key missing from the dictionary returns the input text unchanged
    (safe fallback — never a crash or an empty string).
  · Templates with {0}… were built from the old f-strings and are filled
    with .format; the English translation may reorder the placeholders.
  · Layout direction (RTL/LTR) and digit shaping are read from here too.

The language comes from config.json (main.py calls
set_lang(store.language()) after building the Store). For tests/manual runs,
the MC_LANG=fa|en environment variable sets the initial default (saved
settings take precedence over it).
"""
from __future__ import annotations

import os
import re

_LANG = os.environ.get("MC_LANG", "en").strip().lower()
if _LANG not in ("fa", "en"):
    _LANG = "en"

LANGUAGES = (("en", "English"), ("fa", "فارسی"))

# English singular for the number 1 — only a whitelist of words (regular
# plurals); "1 series" and "21 minutes" stay untouched.
# Fixes: (1) \b1 matched the "1" inside "1.1" — a decimal fraction
# stays plural, hence the (?<![\w.]) guard; (2) "matches" was chopped with
# [:-1] into the nonsense "1 matche" — irregular plurals get a real map.
_EN_SINGULAR_RE = re.compile(
    r"(?<![\w.])1 (minutes|seconds|hours|days|weeks|episodes|titles|items|files|"
    r"folders|posters|thumbnails|thumbs|matches|results|roots|images|"
    r"videos|documents|archives|tracks|scenes|covers)\b")

_EN_SINGULAR_IRREGULAR = {"matches": "match"}


def fix_en_singular(s: str) -> str:
    """In English text, the number 1 becomes singular: "1 minutes" -> "1 minute".

    In Persian mode the input is returned unchanged (zero cost). Call it only
    on *displayable* strings (after format), not on {0}-style templates."""
    if _LANG != "en" or not s:
        return s
    return _EN_SINGULAR_RE.sub(
        lambda m: "1 " + _EN_SINGULAR_IRREGULAR.get(m.group(1), m.group(1)[:-1]),
        s)


def lang() -> str:
    """Current language code: "fa" or "en"."""
    return _LANG


def set_lang(code: str) -> None:
    """Set the running language. Unknown -> en (the safe default)."""
    global _LANG
    _LANG = "fa" if str(code).strip().lower() == "fa" else "en"


def lang_from_config(raw) -> str:
    """Effective language from a raw config — the migration rule lives in
    exactly one place.

    The same rule used to be copied in store.py and main.py (and the
    --config path); every small change missed one copy. The rule:
    · a language key of "fa" -> fa; anything else -> en.
    · a config written by an older build has no language key — if there are signs of prior
      use (paths/categories) the user is Persian-speaking: stay fa (the en
      default is only for fresh installs).
    """
    if not isinstance(raw, dict):
        return "en"
    lang = raw.get("language")
    if lang is None and (raw.get("roots") or raw.get("categories")):
        lang = "fa"
    return "fa" if str(lang).strip().lower() == "fa" else "en"


def is_rtl() -> bool:
    """Right-to-left layout only for Persian."""
    return _LANG == "fa"


def disp_cat(cat: str) -> str:
    """Category name for *display* ("when it's English,
    everything is English").

    The category name is user data and always stays the original Persian in
    config/the database; this function only passes the *display text*
    through the dictionary segment by segment ("Anime/Watched" -> each
    part): common Persian names become English, and custom names missing
    from the dictionary stay untouched (safe fallback)."""
    s = str(cat or "")
    if _LANG == "fa" or not s:
        return s
    return "/".join(T(seg.strip()) if seg.strip() else seg for seg in s.split("/"))


def T(text: str) -> str:
    """Translate a Persian string (or its template) to the current language
    on the fly.

    In Persian mode the input is returned as-is (zero string cost). In
    English mode it is looked up in the i18n_en.EN dictionary; a missing key
    -> the input itself (safe fallback). The dictionary loads lazily so
    importing i18n always stays light."""
    if _LANG == "fa" or not text:
        return text
    try:
        return _EN.get(text, text)
    except NameError:
        try:
            from i18n_en import EN as _loaded
        except Exception:
            _loaded = {}
        globals()["_EN"] = _loaded
        return _loaded.get(text, text)


def digits(s) -> str:
    """Digits matching the language: Persian -> Persian numerals,
    English -> Latin numerals.

    A smart replacement for hand-translated digits in fa()/format_duration —
    with it, every counter becomes bilingual without touching hundreds of
    call sites."""
    s = str(s)
    if _LANG == "fa":
        return s.translate(_FA_TABLE)
    return s


_FA_TABLE = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


# -------------------------------------------------- standard dialog buttons
# "In the Persian UI everything must be Persian, even yes/no":
# Qt reads the Yes/No/OK/… button texts of QMessageBox and QInputDialog from
# its own built-in translation, which is absent from the distributed build ->
# they stay English. Instead of touching hundreds of call sites, a global
# event guard localizes the standard button texts when each internal dialog
# is polished — including the static boxes of QMessageBox.question and the
# OK/Cancel inside QInputDialog. In English mode it does nothing (Qt's
# default is already the desired English). The OS's native file dialogs
# (folder/file pickers) follow the Windows language instead.
_STD_BUTTON_TEXTS = {
    "&yes": "بله", "yes": "بله", "yes to all": "بله برای همه",
    "&yes to all": "بله برای همه", "yestoall": "بله برای همه",
    "&no": "خیر", "no": "خیر", "no to all": "خیر برای همه",
    "&no to all": "خیر برای همه", "notoall": "خیر برای همه",
    "ok": "باشه", "&ok": "باشه",
    "cancel": "انصراف", "&cancel": "انصراف",
    "save": "ذخیره", "&save": "ذخیره", "save all": "ذخیره‌ی همه",
    "don't save": "ذخیره نکن", "don'tsave": "ذخیره نکن", "discard": "ذخیره نکن",
    "close": "بستن", "&close": "بستن",
    "retry": "تلاش دوباره", "&retry": "تلاش دوباره",
    "abort": "لغو", "&abort": "لغو",
    "ignore": "نادیده گرفتن", "&ignore": "نادیده گرفتن",
    "apply": "اعمال", "&apply": "اعمال",
    "reset": "بازنشانی", "&reset": "بازنشانی",
    "restore defaults": "بازگردانی پیش‌فرض‌ها",
    "help": "راهنما", "&help": "راهنما",
    "open": "باز کردن", "&open": "باز کردن",
}


def localize_std_buttons(widget) -> int:
    """Rewrite a window's English standard button texts into Persian.

    For direct use on a freshly built QMessageBox/QInputDialog, and from
    inside the event guard. Only has an effect in Persian mode. Returns:
    the number of rewritten buttons (for tests/diagnostics)."""
    if _LANG != "fa":
        return 0
    try:
        from PySide6.QtWidgets import QAbstractButton
    except Exception:
        return 0
    changed = 0
    try:
        for b in widget.findChildren(QAbstractButton):
            try:
                txt = (b.text() or "").strip()
            except Exception:
                continue
            key = txt.replace(" ", " ").lower()
            local = _STD_BUTTON_TEXTS.get(key)
            if local is None and key.startswith("&"):
                local = _STD_BUTTON_TEXTS.get(key.lstrip("&"))
            if local:
                b.setText(T(local))
                changed += 1
    except Exception:
        # hot path: runs for every dialog polish/show — silence keeps this
        # free of log spam and it must never break showing a dialog.
        pass
    return changed


class _StdButtonGuard:
    """Global guard: every standard dialog that gets polished/shown ends up with localized buttons."""

    def __init__(self, app):
        self._app = app
        self._filter = None

    def install(self):
        try:
            from PySide6.QtCore import QObject, QEvent

            class _Filter(QObject):
                def eventFilter(self, obj, ev):
                    try:
                        if ev.type() in (QEvent.Type.Polish, QEvent.Type.Show):
                            from PySide6.QtWidgets import QMessageBox, QInputDialog, QErrorMessage
                            if isinstance(obj, (QMessageBox, QInputDialog, QErrorMessage)):
                                localize_std_buttons(obj)
                    except Exception:
                        # global event filter: fires for EVERY Qt event — it
                        # must never raise or log-spam; return False always.
                        pass
                    return False

            self._filter = _Filter(self._app)
            self._app.installEventFilter(self._filter)
        except Exception:
            self._filter = None


def install_std_button_localizer(app) -> None:
    """Install the standard-button localizer guard on the QApplication (once)."""
    try:
        if getattr(app, "_mc_std_guard48", None) is None:
            g = _StdButtonGuard(app)
            g.install()
            app._mc_std_guard48 = g
    except Exception as _exc_dbg:
        try:
            from watchtime import dbg_log   # lazy import — avoids a module cycle
            dbg_log(f"install_std_button_localizer failed: {type(_exc_dbg).__name__}")
        except Exception:
            pass  # one-shot startup nicety — must never break app boot
