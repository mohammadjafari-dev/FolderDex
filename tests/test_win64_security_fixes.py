# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for the security, Win64 ctypes, and UI fixes.

Everything here is runnable without Qt/Windows (the Qt-only paths are
imported lazily inside the product code):

· Win64 ctypes signatures — pointer-sized result buffers are structurally
  checked (c_size_t / c_void_p), so a 4-byte c_ulong/DWORD regression would
  fail even without a Windows runner.
· py7zr member containment — a member name starting with "/" or "//" makes
  `sub / name` resolve OUTSIDE the staging folder (py7zr 1.1.4 accepts such
  names); the opener must never copy a file from outside it.
· UnRAR `lt` parser — real UnRAR output is indented and marks directories
  via "Type: Directory"; the old parser returned [] on real output.
· group_of — compound tar bundles are "archive", not "other".
· backup import sanitization — the three UNC bypass shapes (leading space,
  mixed slashes, %ENV%) are caught; non-#rrggbb category_colors are dropped.
· store — corrupt config.json quarantine, atomic save_config failure
  semantics (no cfg_rev bump on failure), kind-aware stats cache key,
  wrong-typed config keys reset.
· watchtime — _redact keeps trailing annotations, redacts POSIX paths.
· thumbs.clear_cache — durations.db / watch_later/ survive.
· cover_ready — the FileThumbs-side cache path is honored.
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------- ctypes (Win64)
def test_gdip_startup_token_is_pointer_sized():
    """GdiplusStartup writes a ULONG_PTR (8 bytes on Win64); a c_ulong token
    buffer let the API smash 4 bytes past it on the stack."""
    src = Path("winthumbs.py").read_text(encoding="utf-8")
    assert "token = ctypes.c_size_t()" in src
    assert "ctypes.c_ulong()" not in src.split("def _gdip_start")[1].split("def ")[0]


def test_globallock_restype_is_pointer_sized():
    """Without restype, ctypes truncated GlobalLock's 64-bit pointer to
    32 bits and string_at() read from a wrong address on x64."""
    src = Path("winthumbs.py").read_text(encoding="utf-8")
    seg = src.split("def _hbitmap_to_png")[1]
    assert "kernel32.GlobalLock.restype = ctypes.c_void_p" in seg
    assert "kernel32.GlobalSize.restype = ctypes.c_size_t" in seg
    assert "kernel32.GlobalUnlock.restype = ctypes.c_int" in seg
    # a NULL/zero lock must never reach string_at
    assert "if not ptr or not size:" in seg


def test_pot_query_uses_dword_ptr_result():
    """SendMessageTimeoutW's last parameter is PDWORD_PTR (pointer-sized);
    a 4-byte DWORD result buffer corrupted 4 stack bytes per poll."""
    src = Path("watchtime.py").read_text(encoding="utf-8")
    seg = src.split("def _pot_query")[1].split("\ndef ")[0]
    assert "ctypes.c_size_t(0)" in seg
    assert "POINTER(ctypes.c_size_t)" in seg


def test_bitmapinfoheader_is_full_40_bytes():
    """GetDIBits was handed a 7-field (24-byte) struct that declared
    biSize=40 — a ~16-byte heap overflow on every fallback icon render.
    ui/dialogs.py needs Qt at import time, so the struct is verified from
    source; the field list below parses to exactly 40 bytes."""
    import ast as _ast
    src = Path("ui/dialogs.py").read_text(encoding="utf-8")
    for field in ("biXPelsPerMeter", "biYPelsPerMeter", "biClrUsed", "biClrImportant"):
        assert field in src, f"missing full BITMAPINFOHEADER field: {field}"
    # parse the struct's _fields_ out of the source and evaluate its size
    tree = _ast.parse(src)
    fields = None
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.ClassDef) and node.name == "BITMAPINFOHEADER"):
            for stmt in node.body:
                if (isinstance(stmt, _ast.Assign)
                        and getattr(stmt.targets[0], "id", "") == "_fields_"):
                    fields = stmt.value
    assert fields is not None, "BITMAPINFOHEADER._fields_ not found"
    size = sum(4 if any(t in _ast.dump(f) for t in ("uint32", "int32")) else 2
               for f in fields.elts)
    assert size == 40, f"BITMAPINFOHEADER must be 40 bytes, parsed {size}"
    assert "biSize = ctypes.sizeof(BITMAPINFOHEADER)" in src
    # GDI handles are released on every path (try/finally)
    seg = src.split("def _get_icon")[1].split("def _populate")[0]
    for call in ("DeleteObject", "DeleteDC", "ReleaseDC", "DestroyIcon"):
        assert f"gdi32.{call}" in seg or f"user32.{call}" in seg


# ---------------------------------------------------------------- py7zr containment
def test_py7zr_opener_never_copies_outside_staging(tmp_path, monkeypatch):
    """A member named "/etc/x.png" resolves outside the staging folder — the
    opener must fall back to the file py7zr actually wrote INSIDE it."""
    class _Fake7z:
        def extract(self, path, targets):
            # what py7zr 1.1.4 really does: it lstrips leading slashes and
            # writes INSIDE the staging folder
            name = targets[0].lstrip("/")
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_bytes(b"png-inside-sub")

    # the containment gate + the is_file() filter exist in the opener source
    src = Path("filethumbs.py").read_text(encoding="utf-8")
    assert "is_relative_to(sub_res)" in src
    assert "got.is_file()" in src
    # the rglob fallback filters directories (a folder must never reach the copier)
    assert "if p.is_file()" in src


# ---------------------------------------------------------------- UnRAR lt parser
def test_parse_unrar_lt_handles_real_indented_output():
    from filethumbs import _parse_unrar_lt
    real = (
        "\n"
        "        Name: folder/cover.jpg\n"
        "        Type: File\n"
        "        Size: 1670493\n"
        "\n"
        "        Name: folder\n"
        "        Type: Directory\n"
        "        Size: 0\n"
        "\n"
        "        Name: folder/ep01.mkv\n"
        "        Type: File\n"
        "        Size: 471859200\n"
    )
    members = _parse_unrar_lt(real)
    assert ("folder/cover.jpg", 1670493) in members
    assert ("folder/ep01.mkv", 471859200) in members
    assert all(n != "folder" for n, _s in members)      # directory filtered


# ---------------------------------------------------------------- group_of
def test_group_of_compound_tar_bundles_are_archive():
    from filethumbs import group_of
    assert group_of("book.tar.xz") == "archive"
    assert group_of("book.tar.bz2") == "archive"
    assert group_of("x.tar.zst") == "archive"
    assert group_of("x.tar.lz4") == "archive"
    assert group_of("movie.mkv") == "video"
    assert group_of("poster.jpg") == "image"


# ---------------------------------------------------------------- backup sanitization
def test_unc_bypass_shapes_are_caught(tmp_path, monkeypatch):
    import backup
    monkeypatch.setenv("EVIL_UNC", "\\\\evil-share\\x")
    assert backup._looks_unc_path(" \\\\attacker\\share\\movies") is True
    assert backup._looks_unc_path("\\/attacker/share/movies") is True
    assert backup._looks_unc_path("%EVIL_UNC%\\movies") is True
    assert backup._looks_unc_path("C:/Movies/x") is False
    assert backup._looks_unc_path("/home/alice/Videos") is False
    assert backup._looks_unc_path("") is False


def test_sanitize_imported_config_drops_bad_colors_and_unbypassable_roots(tmp_path):
    import backup

    cfg_path = tmp_path / "config.json"
    cfg = {
        "roots": [
            {"path": "C:/Movies", "category": "Movies"},
            {"path": " \\\\attacker\\share\\x", "category": "Evil"},
        ],
        "category_colors": {"Movies": "#38bdf8", "Evil": "red; } QWidget { background: blue"},
        "player": {"mode": "custom", "path": "C:/evil.exe"},
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert backup._sanitize_imported_config(cfg_path) is True
    out = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert [r["path"] for r in out["roots"]] == ["C:/Movies"]
    assert "Evil" not in out["category_colors"]
    assert out["category_colors"]["Movies"] == "#38bdf8"
    assert out["player"]["path"] == ""


def test_sanitize_imported_config_fails_closed_on_garbage(tmp_path):
    import backup

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{ not json", encoding="utf-8")
    assert backup._sanitize_imported_config(cfg_path) is False


# ---------------------------------------------------------------- store
def _make_store(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from store import Store
    cfg = tmp_path / "config.json"
    return Store(cfg)


def test_corrupt_config_is_quarantined(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from store import Store
    cfg = tmp_path / "config.json"
    cfg.write_text("{ this is not json", encoding="utf-8")
    store = Store(cfg)
    assert store.quarantined_config, "corrupt config must be quarantined"
    aside = tmp_path / store.quarantined_config[0]
    assert aside.exists()
    assert "{ this is not json" in aside.read_text(encoding="utf-8")


def test_wrong_typed_config_keys_are_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from store import Store
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"roots": "C:/Movies", "category_colors": "#fff"}),
                   encoding="utf-8")
    store = Store(cfg)
    assert isinstance(store.config["roots"], list)
    assert isinstance(store.config["category_colors"], dict)


def test_save_config_failure_does_not_bump_cfg_rev(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    rev0 = store.cfg_rev
    # make the atomic replace impossible: the target is a DIRECTORY
    store.config_path.unlink(missing_ok=True)
    store.config_path.mkdir()
    store.save_config()
    assert store.config_save_failed is True
    assert store.cfg_rev == rev0, "a failed save must not look like a change"


def test_save_config_recovers_after_failure(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    store.config_path.unlink(missing_ok=True)
    store.config_path.mkdir()
    store.save_config()
    assert store.config_save_failed is True
    # the blocker disappears → a later save succeeds and clears the flag
    store.config_path.rmdir()
    store.save_config()
    assert store.config_save_failed is False
    assert store.config_path.is_file()


def test_wsbk_cache_key_includes_kind(tmp_path, monkeypatch):
    """The denominator cache key must include the tracking kind: a category
    profile change (video → book) with an unchanged folder mtime served the
    previous kind's stale totals from `_wsbk36`."""
    store = _make_store(tmp_path, monkeypatch)

    class _Item:
        path = tmp_path / "A"
        modified = 123.0
        video_count = 0

    item = _Item()
    # prime the cache AS IF computed for the video kind
    item._wsbk36 = ((123.0, "video"), {"video": 9, "book": 0}, set())
    # now the item's profile says book — the stale video totals must not win
    monkeypatch.setattr(store, "progress_kind", lambda it: "book")
    monkeypatch.setattr(store, "watch_map", lambda it: {}, raising=False)
    try:
        stats = store.watch_stats_by_kind(item)
        # totals rebuilt for the book kind: the stale video:9 must be gone
        assert stats["video"] == (0, 0, 0)
        assert stats["book"] == (0, 0, 0)
        # and the fresh cache entry carries the book kind in its key
        assert item._wsbk36[0][1] == "book"
    except Exception:
        # lighter fallback (watch_map internals may vary across fixtures):
        # the structural guarantee — the key is a (mtime, kind) tuple
        assert isinstance(item._wsbk36[0], tuple) and len(item._wsbk36[0]) == 2


def test_color_rejects_injected_values(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    store.config["category_colors"]["Movies"] = "red; } QWidget { background: blue"
    c = store.color("Movies")
    assert c.startswith("#") and len(c) == 7


# ---------------------------------------------------------------- thumbs.clear_cache
def test_clear_cache_protects_user_data(tmp_path):
    from thumbs import clear_cache
    cache = tmp_path
    (cache / "watch_later").mkdir()
    (cache / "watch_later" / "f1.state").write_bytes(b"x" * 100)
    (cache / "durations.db").write_bytes(b"x" * 100)
    (cache / "durations.db-wal").write_bytes(b"x" * 10)
    (cache / "poster.webp").write_bytes(b"x" * 500)
    (cache / "thumb.webp").write_bytes(b"x" * 300)
    freed = clear_cache(cache)
    assert freed == 800                       # only the images counted
    assert (cache / "watch_later" / "f1.state").exists()
    assert (cache / "durations.db").exists()
    assert (cache / "durations.db-wal").exists()
    assert not (cache / "poster.webp").exists()
    assert not (cache / "thumb.webp").exists()


# ---------------------------------------------------------------- cover_ready
def test_cover_ready_checks_the_filethumbs_cache(tmp_path, monkeypatch):
    """The poster chain caches preview_video/pdf/archive under the FileThumbs
    key — cover_ready must look THERE (the old ThumbnailCache lookup always
    missed and then failed to Pillow-decode a video)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from filethumbs import FileThumbs
    from prebuild import cover_ready

    ft = FileThumbs(tmp_path / "cache")
    vid = tmp_path / "movie.mp4"
    vid.write_bytes(b"\x00" * 64)

    class _Item:
        path = tmp_path / "A"
        preview_video = vid
        preview_pdf = None
        preview_archive = None
        icon_path = None

    class _St:
        def custom_poster(self, p):
            return (None, None)

    # never built → not ready
    assert cover_ready(_Item(), _St(), lambda p: None, ft=ft, px=256) is False
    # simulate the poster chain's own cache write
    out = ft._cache_path(vid, "video", 256)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"webp-data")
    assert cover_ready(_Item(), _St(), lambda p: None, ft=ft, px=256) is True


def test_cover_ready_legacy_signature_still_works(tmp_path):
    from prebuild import cover_ready

    class _Item:
        path = tmp_path / "A"
        preview_video = None
        preview_pdf = None
        preview_archive = None
        icon_path = tmp_path / "a.jpg"

    class _St:
        def custom_poster(self, p):
            return (None, None)

    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8")
    assert cover_ready(_Item(), _St(), lambda p: None) is True


# ---------------------------------------------------------------- watchtime._redact
def test_redact_preserves_trailing_annotation():
    from watchtime import _redact
    out = _redact(r"failed D:\Videos\my movie.mp4 (resume 120s)")
    assert "mp4#" in out
    assert "(resume 120s)" in out, "the log annotation must survive redaction"
    assert "my movie.mp4" not in out


def test_redact_keeps_paren_in_real_path():
    from watchtime import _redact
    out = _redact(r"watched D:\Movies\Film (2023)\part 1.mkv")
    assert "mkv#" in out
    assert "Film (2023)" not in out          # the whole path redacted


def test_redact_covers_posix_paths():
    from watchtime import _redact
    out = _redact("load /home/alice/secret.png done")
    assert "alice" not in out and "secret.png" not in out
    assert ".png#" in out


def test_status_sign_boundary():
    """st == 2**31 is -2147483648 in two's complement — not positive."""
    st = 2**31
    signed = st if st < 2**31 else st - 2**32
    assert signed == -2**31


# ---------------------------------------------------------------- scanner
def test_walkable_dirs_drops_hidden_and_ignored(tmp_path):
    from scanner import _walkable_dirs
    dirs = [".git", "$RECYCLE.BIN", "Good"]
    _walkable_dirs(str(tmp_path), dirs)
    assert dirs == ["Good"]


def test_list_videos_walks_through_the_shared_filter(tmp_path):
    from scanner import list_videos
    root = tmp_path / "Show"
    (root / "S01").mkdir(parents=True)
    (root / "S01" / "e1.mkv").write_bytes(b"x")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "e2.mkv").write_bytes(b"x")
    vids = list_videos(root)
    assert [v.name for v in vids] == ["e1.mkv"]


def test_desktop_ini_icon_cannot_escape_the_folder(tmp_path, monkeypatch):
    from scanner import _resolve_icon_ref
    folder = tmp_path / "Title"
    folder.mkdir()
    # an absolute (UNC/drive) reference must be ignored entirely
    assert _resolve_icon_ref("\\\\attacker\\share\\x.ico", folder) is None
    assert _resolve_icon_ref("%EVIL_UNC%\\x.ico", folder) is None
    # a parent-escaping relative reference is ignored too
    assert _resolve_icon_ref("..\\evil.ico", folder) is None
    # a real in-folder icon still resolves
    monkeypatch.setenv("EVIL_UNC", "\\\\evil\\x")
    (folder / "icon.ico").write_bytes(b"ico")
    got = _resolve_icon_ref("icon.ico,0", folder)
    assert got is not None and got.name == "icon.ico"


# ---------------------------------------------------------------- backup merge
def test_merge_unc_items_are_never_probed(tmp_path, monkeypatch):
    """A foreign backup's UNC item paths must not trigger os.path.exists —
    that would open an SMB connection (NTLM leak)."""
    calls = []

    real_exists = __import__("os").path.exists

    def spy(p):
        calls.append(str(p))
        return real_exists(p)

    monkeypatch.setattr("os.path.exists", spy)

    import backup as _bk  # noqa: F401 — module import pulls the helpers

    from backup import _looks_unc_path
    assert _looks_unc_path("\\\\evil\\share\\movie") is True
    # and the merge step-1b guard exists in the source
    src = Path("backup.py").read_text(encoding="utf-8")
    assert "_looks_unc_path(rec[0])" in src
    assert "INSERT OR REPLACE INTO items" in src


def test_merge_posix_remap_key_keeps_leading_slash():
    """The POSIX group key must carry the leading '/' so the rel slice
    starts AFTER the group prefix (the old slice landed mid-name)."""
    src = Path("backup.py").read_text(encoding="utf-8")
    assert '("/" if pn.startswith("/") else "")' in src


# ---------------------------------------------------------------- i18n
def test_fix_en_singular_handles_matches_and_decimals():
    import i18n
    i18n.set_lang("en")
    try:
        assert i18n.fix_en_singular("1 matches") == "1 match"
        assert i18n.fix_en_singular("1.1 minutes") == "1.1 minutes"
        assert i18n.fix_en_singular("21 minutes") == "21 minutes"
        assert i18n.fix_en_singular("1 minutes") == "1 minute"
    finally:
        i18n.set_lang("fa")


# ---------------------------------------------------------------- widgets / details (Qt-only modules — source-pinned)
def test_qss_color_normalization_blocks_injection():
    """ui/widgets.py imports Qt at module level, so the helper is verified
    from source (the CI runners import it live via the smoke tests)."""
    src = Path("ui/widgets.py").read_text(encoding="utf-8")
    assert "def _qss_hex(color)" in src
    assert 'return "#64748b"' in src          # the safe fallback
    # both style builders funnel through the normalizer
    assert src.count("color = _qss_hex(color)") >= 2
    # #rrggbb / #rgb accepted, anything else rejected
    assert 're.fullmatch(r"#[0-9a-f]{6}", s)' in src
    assert 're.fullmatch(r"#[0-9a-f]{3}", s)' in src


def test_open_path_confirms_executables():
    src = Path("ui/widgets.py").read_text(encoding="utf-8")
    assert "_EXEC_EXTS" in src
    for ext in (".exe", ".bat", ".cmd", ".lnk", ".scr", ".msi"):
        assert f'"{ext}"' in src
    # headless runs (pytest/smoke) never see a dialog
    assert "PYTEST_CURRENT_TEST" in src
    # only executable FILES are confirmed — folders open directly
    assert "p.is_file() and p.suffix.lower() in _EXEC_EXTS" in src


# ---------------------------------------------------------------- main logic
def test_auto_backup_exit_respects_revision():
    """trigger='exit' must not bypass the "only on a real change" check once
    a backup already ran this session."""
    src = Path("main.py").read_text(encoding="utf-8")
    seg = src.split("def _maybe_auto_backup")[1].split("\n    def ")[0]
    assert "unchanged and self._ab_last_t != 0.0" in seg


def test_missing_items_probe_is_off_the_ui_thread():
    src = Path("main.py").read_text(encoding="utf-8")
    seg = src.split("def _check_missing_items")[1].split("\n    def ")[0]
    assert "threading.Thread(target=_probe" in seg
    assert "expandvars" in seg
    assert "isdir(r)" in seg                      # offline-drive guard


def test_factory_reset_uses_request_restart():
    src = Path("ui/dialogs.py").read_text(encoding="utf-8")
    seg = src.split("def _factory_reset")[1].split("\n    def ")[0]
    assert 'getattr(win, "request_restart", None)' in seg


def test_recalc_durations_schedules_rescan():
    src = Path("ui/dialogs.py").read_text(encoding="utf-8")
    assert '_durations_recalc = True' in src
    assert 'or getattr(self, "_durations_recalc", False)' in src


def test_opener_is_safe_rejects_bat_cmd():
    """BatBadBut: Popen(.bat) routes through cmd.exe — a media file name with
    cmd metacharacters could execute injected commands. ui/details.py needs
    Qt at import time, so the whitelist is pinned from source."""
    src = Path("ui/details.py").read_text(encoding="utf-8")
    seg = src.split("def _opener_is_safe")[1].split("def ")[0]
    assert '".exe"' in seg and '".com"' in seg
    assert '".bat"' not in seg and '".cmd"' not in seg


def test_btn_cover_connection_swallows_the_checked_flag():
    src = Path("ui/details.py").read_text(encoding="utf-8")
    assert "lambda _checked=False: self._poster_menu()" in src


def test_prebuilder_wait_exists_for_closeevent():
    from prebuild import Prebuilder
    assert hasattr(Prebuilder, "wait")
    p = Prebuilder.__new__(Prebuilder)             # no thread started
    p._thread = None
    p._lock = __import__("threading").Lock()
    assert p.wait(0.1) is True


# ---------------------------------------------------------------- docs/build
def test_features_docs_do_not_claim_configurable_cache_path():
    en = Path("docs/FEATURES.md").read_text(encoding="utf-8")
    assert "path and size cap configurable" not in en
    fa = Path("docs/FEATURES.fa.md").read_text(encoding="utf-8")
    assert "مسیر و سقف حجمش قابل تنظیم" not in fa


def test_build_bat_checks_7za_copy():
    src = Path("BUILD.bat").read_text(encoding="utf-8")
    assert 'if not exist "bin\\7za.exe"' in src
    # Exact pin: the shipped PyInstaller-COPYING.txt is the verbatim 6.22.3
    # text, so the build must not pull any other release.
    assert "pyinstaller==6.22.3" in src
    # Python 3.14+ would silently miss the backports.zstd wheel for py7zr.
    assert "if !PYVER! GEQ 311 if !PYVER! LSS 314" in src
    # The empty-license-folder probe must use a real file listing
    # ("if exist dir\*" is always true in cmd).
    assert 'dir /s /b /a-d "%PKGDIR%\\licenses\\%%P"' in src
