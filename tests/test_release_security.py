# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Public-release security tests — no GUI, no Windows.

Coverage:
  1) backup._safe_member — zip-slip protection on archive member names
  2) backup._sanitize_imported_config — removal of cat_openers and fail-closed behavior
  3) 7z/UnRAR extraction argv — "--" before the member name (and "-spd" for 7z)
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
    """Archive member names must be flat/relative and traversal-free."""

    @pytest.mark.parametrize("bad", [
        "../evil.json",            # upward traversal
        "a/../../evil.json",       # mid-path traversal
        "..",                      # the parent itself
        ".",                       # the folder itself
        "/abs/path.json",          # absolute posix path
        "C:/evil.json",            # Windows drive
        "C:\\evil.json",           # drive + backslash
        "a\\b.json",               # backslash (Windows-style — not an allowed separator)
        "",                        # empty
        "a//b.json",               # empty segment
        "./a.json",                # dot segment
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
    """Sanitizing an imported config: remove cat_openers and stay fail-closed."""

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
        assert data["theme"] == "dark"          # the other keys stay intact

    def test_valid_without_key_passes(self, tmp_path):
        p = self._write(tmp_path, {"theme": "dark"})
        assert _sanitize_imported_config(p) is True
        assert json.loads(p.read_text(encoding="utf-8")) == {"theme": "dark"}

    def test_invalid_json_fails_closed(self, tmp_path):
        p = self._write(tmp_path, "{not valid json ..")
        assert _sanitize_imported_config(p) is False

    def test_unreadable_file_fails_closed(self, tmp_path):
        p = tmp_path / "missing.json"           # does not exist → reading fails
        assert _sanitize_imported_config(p) is False

    def test_unwritable_file_fails_closed(self, tmp_path):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: the read-only bit (chmod 444) has no effect")
        p = self._write(tmp_path, {
            "cat_openers": {"فیلم": [{"exts": ["mkv"], "app": "C:/y.exe"}]},
        })
        p.chmod(0o444)                          # read-only → the rewrite fails
        try:
            assert _sanitize_imported_config(p) is False
        finally:
            p.chmod(0o644)


# --------------------------------------- 3) 7z / UnRAR extraction arguments
class TestExtractionArgv:
    """The member name must come after "--" (and 7z with -spd) so it is not read as a switch."""

    @pytest.fixture()
    def argv_capture(self, monkeypatch):
        import io as _io
        import filethumbs
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(list(argv))

            class _R:                            # minimal CompletedProcess
                stdout = b""
                stderr = b""
                returncode = 0

            return _R()

        def fake_popen(argv, **kwargs):
            seen.append(list(argv))

            class _P:                            # minimal Popen for the drain loop
                stdout = _io.BytesIO(b"")       # read() -> b"" ends the drain

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

                def kill(self):
                    pass

            return _P()

        # the unrar backend streams stdout through a PIPE via Popen since the
        # extraction cap rides on the sink
        monkeypatch.setattr(filethumbs.subprocess, "run", fake_run)
        monkeypatch.setattr(filethumbs.subprocess, "Popen", fake_popen)
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
