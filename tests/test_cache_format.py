# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for imgcache.py — the WebP-only thumbnail cache.

The cache format is a *product decision* (see requirements.txt): WebP writes
faster and decodes natively in Qt at thumbnail sizes, while JPEG XL needed a
native plugin + six DLLs and only ever beat PNG. These tests pin that decision
so nobody "optimizes" it back silently.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PIL import Image

import imgcache  # noqa: E402
from imgcache import cache_file, read_cache_pil, save_cache_image  # noqa: E402


def _rgba(w: int = 64, h: int = 64) -> Image.Image:
    return Image.new("RGBA", (w, h), (10, 20, 30, 255))


# ------------------------------------------------------------ cache format
def test_cache_ext_is_webp():
    assert imgcache.WEBP_OK is True
    assert imgcache.CACHE_EXT == ".webp"


def test_no_jxl_imports():
    # no JPEG XL plugin module may be imported by the app chain (the codec is
    # gone on purpose — see the note in requirements.txt)
    assert not [m for m in sys.modules if "jxl" in m.lower()]


# ------------------------------------------------------------ write/read
def test_save_and_read_roundtrip(tmp_path: Path):
    out = cache_file(tmp_path, "roundtrip")
    ret = save_cache_image(_rgba(), out)
    assert ret == out and out.suffix == ".webp" and out.is_file()
    im = read_cache_pil(out)
    assert im is not None and im.size == (64, 64)


def test_legacy_png_is_still_read(tmp_path: Path):
    stem = "legacy"
    (tmp_path / f"{stem}.png").write_bytes(b"not-a-real-png-but-present")
    # the legacy .png path is returned (reading it later yields None and it is
    # rebuilt then — B1)
    assert cache_file(tmp_path, stem) == tmp_path / f"{stem}.png"


def test_stale_jxl_is_ignored(tmp_path: Path):
    stem = "stale"
    (tmp_path / f"{stem}.jxl").write_bytes(b"orphaned-jxl-payload")
    # the .jxl cache behaves like a missing file so it is rebuilt as WebP automatically
    assert cache_file(tmp_path, stem) == tmp_path / f"{stem}.webp"


# ------------------------------------------------------------ atomic writes
def test_atomic_write_survives_parallel_saves(tmp_path: Path):
    out = cache_file(tmp_path, "atomic")
    errs: list[Exception] = []

    def _worker():
        try:
            for _ in range(8):
                save_cache_image(_rgba(), out)
        except Exception as exc:  # noqa: BLE001 — the catch-all is the test itself
            errs.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errs == []
    im = read_cache_pil(out)
    assert im is not None and im.size == (64, 64)


def test_png_fallback_path_when_forced(tmp_path: Path):
    """save must also work on a .png path (the format follows the extension)."""
    out = tmp_path / "aspng.png"
    ret = save_cache_image(_rgba(), out)
    assert ret == out and out.is_file()
    assert read_cache_pil(out) is not None


# ------------------------------------------------------------ naming changes
def test_module_has_no_jxl_symbols():
    for name in dir(imgcache):
        assert "jxl" not in name.lower()


# ------------------------------------------------------------ re-decode on the hot path
def test_corrupt_cache_is_rebuilt(tmp_path: Path):
    """A corrupt cache file (unknown signature) is not decodable →
    poster_png_fallback rebuilds it."""
    from filethumbs import _cache_decodable
    bad = tmp_path / "corrupt.webp"
    bad.write_bytes(b"JXLJXL-not-a-real-cache-file\x00\x01")
    assert _cache_decodable(bad) is False


def test_valid_webp_and_png_magic_pass(tmp_path: Path):
    """The fast path: healthy WebP/PNG are accepted without a full decode.
    A complete PNG must end with its fixed IEND chunk — a valid header alone
    is not enough any more (the truncated-cache guard)."""
    from filethumbs import _cache_decodable
    webp = tmp_path / "ok.webp"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert _cache_decodable(webp) is True
    png = tmp_path / "ok.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"rest"
                    + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    assert _cache_decodable(png) is True


def test_riff_non_webp_rejected(tmp_path: Path):
    """Non-WebP RIFF (e.g. WAV) is rejected — our cache is images only."""
    from filethumbs import _cache_decodable
    wav = tmp_path / "sound.webp"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert _cache_decodable(wav) is False


def test_real_webp_cache_file_decodes(tmp_path: Path):
    """A real cache file produced by the app (save_cache_image) passes the fast path."""
    from filethumbs import _cache_decodable
    out = cache_file(tmp_path, "real")
    save_cache_image(_rgba(), out)
    assert out.is_file()
    assert _cache_decodable(out) is True


# ------------------------------------------------------------ default cache cap
def test_cache_max_mb_default_is_capped(tmp_path: Path):
    """The default is no longer "unbounded" — a 2 GB LRU cap (an uncapped cache could
    grow several-fold as usage scales up)."""
    from store import Store
    s = Store(tmp_path / "config.json")
    assert s.cache_max_mb() == 2048
    s.close()


def test_explicit_unlimited_is_preserved(tmp_path: Path):
    """A user who explicitly set 0 (unlimited) is preserved."""
    from store import Store
    cfg = tmp_path / "config.json"
    cfg.write_text('{"cache_max_mb": 0}', encoding="utf-8")
    s = Store(cfg)
    assert s.cache_max_mb() == 0
    s.close()


def test_prune_scale_generation_only_on_change(tmp_path: Path):
    """The old generation is pruned only when the scale changed; a boot at the same
    scale prunes nothing."""
    from thumbs import prune_scale_generation
    d = tmp_path / ".thumb_cache"
    d.mkdir()
    old = d / "old.webp"
    new = d / "new.webp"
    old.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    new.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    import os as _os
    old_date = 0            # very old (1970)
    _os.utime(old, (old_date, old_date))
    future = 2_000_000_000  # far in the future
    _os.utime(new, (future, future))
    # first boot — only writes the marker, prunes nothing
    assert prune_scale_generation(d, 256) == (0, 0)
    assert old.is_file() and new.is_file()
    # boot at the same scale — no pruning
    assert prune_scale_generation(d, 256) == (0, 0)
    assert old.is_file()
    # scale change → the old (>7 days) file goes, the new one stays
    removed = prune_scale_generation(d, 512)
    assert removed[1] == 1
    assert not old.exists() and new.is_file()
    # next boot at the same new scale — nothing left to prune
    (d / "again.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    _os.utime(d / "again.webp", (old_date, old_date))
    assert prune_scale_generation(d, 512) == (0, 0)
    assert (d / "again.webp").exists()
