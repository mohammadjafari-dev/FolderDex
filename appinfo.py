# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""App version metadata — shown in the title bar, sidebar and settings.

The title bar displays the version number, so the exact
build a report comes from is always identifiable.

The brand name is "FolderDex" in both UI languages — the trademark is not
translated.
"""

APP_NAME = "FolderDex"
APP_NAME_EN = "FolderDex"
APP_VERSION = "1.0.0"
# 1.0.0 is the first stable release. Until it is published, fixes are
# recorded as short one-line bullets under the [1.0.0] section in
# CHANGELOG.md / CHANGELOG.fa.md; after publication every fix gets its
# own version number (Semantic Versioning: 1.0.1, 1.1.0, ...).
# Official source repository (GPL source offer) — same as REPO_URL in BUILD.bat;
# if the repository moves, both must be changed together.
SOURCE_URL = "https://github.com/mohammadjafari-dev/FolderDex"
APP_TITLE = f"{APP_NAME} — نسخه‌ی {APP_VERSION}"


def app_title() -> str:
    """Window title by UI language: Persian as before, English as the new default."""
    from i18n import lang
    if lang() == "fa":
        return f"{APP_NAME} — نسخه‌ی {APP_VERSION}"
    return f"{APP_NAME_EN} — v{APP_VERSION}"
