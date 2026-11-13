# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""تست‌های امنیتیِ انتشار عمومی — بدون GUI، بدون ویندوز.

پوشش:
  1) backup._safe_member — محافظت zip-slip در نامِ اعضای آرشیو
  2) backup._sanitize_imported_config — حذفِ cat_openers و رفتارِ fail-closed
  3) آرگومان‌های استخراج 7z/UnRAR — «--» قبل از نامِ عضو (و «-spd» برای 7z)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backup import _safe_member, _sanitize_imported_config  # noqa: E402


# ---------------------------------------------------------------- 1) _safe_member
class TestSafeMember:
    """نامِ عضوِ آرشیو باید مسطح/نسبی و بدون تراِورسال باشد."""

    @pytest.mark.parametrize("bad", [
        "../evil.json",            # تراِورسال به بالا
        "a/../../evil.json",       # تراِورسال وسط مسیر
        "..",                      # خودِ والد
        ".",                       # خودِ پوشه
        "/abs/path.json",          # مسیر مطلقِ posix
        "C:/evil.json",            # درایوِ ویندوزی
        "C:\\evil.json",           # درایو + بک‌اسلش
        "a\\b.json",               # بک‌اسلش (ویندوزی — جداکننده‌ی مجاز نیست)
        "",                        # خالی
        "a//b.json",               # سگمنتِ خالی
        "./a.json",                # سگمنتِ نقطه
    ])
    def test_rejects(self, bad):
        assert _safe_member(bad) is False, f"must reject: {bad!r}"

    @pytest.mark.parametrize("good", [
        "config.json",
        "data.db",
        "thumb_cache/durations.db",
        ".mc_data/misc.json",
    ])
    def test_accepts(self, good):
        assert _safe_member(good) is True, f"must accept: {good!r}"


# ------------------------------------------- 2) _sanitize_imported_config
class TestSanitizeImportedConfig:
    """پاک‌سازیِ config واردشده: حذفِ cat_openers و fail-closed بودن."""

    def _write(self, tmp_path, payload):
        p = tmp_path / "config.json"
        p.write_text(payload if isinstance(payload, str)
                     else json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def test_removes_cat_openers(self, tmp_path):
        p = self._write(tmp_path, {
            "theme": "dark",
            "cat_openers": {"بازی": [{"exts": ["zip"], "app": "C:/x.exe"}]},
        })
        assert _sanitize_imported_config(p) is True
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "cat_openers" not in data
        assert data["theme"] == "dark"          # بقیه‌ی کلیدها سالم‌اند

    def test_valid_without_key_passes(self, tmp_path):
        p = self._write(tmp_path, {"theme": "dark"})
        assert _sanitize_imported_config(p) is True
        assert json.loads(p.read_text(encoding="utf-8")) == {"theme": "dark"}

    def test_invalid_json_fails_closed(self, tmp_path):
        p = self._write(tmp_path, "{not valid json ..")
        assert _sanitize_imported_config(p) is False

    def test_unreadable_file_fails_closed(self, tmp_path):
        p = tmp_path / "missing.json"           # وجود ندارد → خواندن شکست می‌خورد
        assert _sanitize_imported_config(p) is False

    def test_unwritable_file_fails_closed(self, tmp_path):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: the read-only bit (chmod 444) has no effect")
        p = self._write(tmp_path, {
            "cat_openers": {"فیلم": [{"exts": ["mkv"], "app": "C:/y.exe"}]},
        })
        p.chmod(0o444)                          # فقط‌خواندنی → بازنویسی شکست می‌خورد
        try:
            assert _sanitize_imported_config(p) is False
        finally:
            p.chmod(0o644)


# --------------------------------------- 3) آرگومان‌های استخراج 7z / UnRAR
class TestExtractionArgv:
    """نامِ عضو باید بعد از «--» بیاید (و 7z با -spd) تا سوییچ خوانده نشود."""

    @pytest.fixture()
    def argv_capture(self, monkeypatch):
        import filethumbs
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(list(argv))

            class _R:                            # CompletedProcess مینیمال
                stdout = b""
                stderr = b""
                returncode = 0

            return _R()

        monkeypatch.setattr(filethumbs.subprocess, "run", fake_run)
        return seen

    def test_7z_has_spd_and_double_dash(self, argv_capture, tmp_path):
        import filethumbs
        arc = filethumbs._CliArc("7z.exe", tmp_path / "a.7z", "7z",
                                 list_members=False)
        dest = tmp_path / "out" / "pic.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        arc.extract("weird -name*.png", dest)
        argv = argv_capture[0]
        assert "--" in argv, "7z extraction must pass '--' before the member name"
        assert argv[argv.index("--") + 1] == "weird -name*.png"
        assert "-spd" in argv, "7z extraction must disable wildcard matching"

    def test_unrar_has_double_dash(self, argv_capture, tmp_path):
        import filethumbs
        arc = filethumbs._CliArc("UnRAR.exe", tmp_path / "a.rar", "unrar",
                                 list_members=False)
        dest = tmp_path / "out" / "pic.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        arc.extract("-tricky-name.png", dest)
        argv = argv_capture[0]
        assert argv[-2:] == ["--", "-tricky-name.png"], \
            "unrar extraction must pass '--' immediately before the member name"
