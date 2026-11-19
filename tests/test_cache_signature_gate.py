# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for the _cache_decodable signature-gate fix.

The JPEG and BMP tail checks were DEAD CODE: the cache fast path gated every
signature branch behind `head[:4] in _MAGIC_OK_HEADS`, but the JPEG member
is 3 bytes (SOI: ff d8 ff) and the BMP member 2 bytes (BM) — a 4-byte slice
can never be equal to either member, so every JPEG/BMP cache file fell
through to the lenient full decode. A decoder that accepts a truncated JPEG (libjpeg pads the missing scan
and reports success) made `_cache_decodable(cut.jpg)` return True —
machines with a stricter PIL rejected the same file, so the bug stayed
invisible there.

These tests pin the fix WITHOUT depending on which decoder is installed:
the full-decode fallback is monkeypatched into an oracle that either
accepts everything or refuses everything, so only the byte-level fast path
can produce the verdict.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

import filethumbs
from filethumbs import _cache_decodable


class _LenientDecoder:
    """Oracle that vouches for EVERY file — simulates Qt's tolerance for a
    truncated JPEG. If the fast path ever lets a file through to the full
    decode, this oracle will accept it."""

    @staticmethod
    def isNull() -> bool:
        return False


def _accept_everything(path):
    return _LenientDecoder()


def _refuse_everything(path):
    raise RuntimeError("full decode must not be consulted for this head")


def _patch_decode(monkeypatch, oracle) -> None:
    # load_cache_qimage is only defined when PySide6 imported cleanly (the
    # `if _QT:` block); on a decoder-less machine the attribute is absent —
    # create/replace it on demand so the simulated branch works in both.
    monkeypatch.setattr(filethumbs, "_QT", True, raising=False)
    monkeypatch.setattr(filethumbs, "load_cache_qimage", oracle, raising=False)


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 9, 9)).save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (4, 5, 6)).save(buf, format="WEBP")
    return buf.getvalue()


# ---------------------------------------------------------------- JPEG gate


def test_truncated_jpeg_rejected_before_any_decode(monkeypatch, tmp_path: Path):
    """cut.jpg (EOI marker removed) must be refused by the fast path — even
    though the (simulated) decoder would accept it. This is the exact CI
    failure of test_cache_decodable_jpeg_and_webp, reproduced decoder-free."""
    _patch_decode(monkeypatch, _accept_everything)
    cut = tmp_path / "cut.jpg"
    cut.write_bytes(_jpeg_bytes()[:-4])           # EOI (FF D9) chopped off
    assert _cache_decodable(cut) is False


def test_healthy_jpeg_accepted_without_decoding(monkeypatch, tmp_path: Path):
    """A healthy JPEG must pass by signature alone — the decoder (which here
    refuses everything) must never be consulted."""
    _patch_decode(monkeypatch, _refuse_everything)
    ok = tmp_path / "ok.jpg"
    ok.write_bytes(_jpeg_bytes())
    assert _cache_decodable(ok) is True


def test_jpeg_with_trailing_pad_bytes_accepted(monkeypatch, tmp_path: Path):
    """Real-world JPEGs sometimes carry 1-2 bytes of padding after EOI; the
    established semantics (FFD9 within the last 4 bytes) keep accepting
    them."""
    _patch_decode(monkeypatch, _refuse_everything)
    padded = tmp_path / "padded.jpg"
    padded.write_bytes(_jpeg_bytes() + b"\x00")   # one pad byte after EOI
    assert _cache_decodable(padded) is True


# ---------------------------------------------------------------- BMP gate


def test_bmp_accepted_by_signature_without_decoding(monkeypatch, tmp_path: Path):
    """BMP's 2-byte signature could never match the old 4-byte gate either —
    the file used to fall through to the full decode. Now the signature
    accepts it without any decoder being consulted."""
    _patch_decode(monkeypatch, _refuse_everything)
    bmp = tmp_path / "ok.bmp"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(bmp, format="BMP")
    assert _cache_decodable(bmp) is True


# ---------------------------------------------------------------- WebP tail


def test_truncated_webp_rejected_before_any_decode(monkeypatch, tmp_path: Path):
    """A truncated WebP carries a perfect RIFF/WEBP header; the RIFF chunk
    size field is what exposes the cut. The decoder would accept, the fast
    path must refuse."""
    _patch_decode(monkeypatch, _accept_everything)
    raw = _webp_bytes()
    ok = tmp_path / "ok.webp"
    ok.write_bytes(raw)
    assert _cache_decodable(ok) is True
    cut = tmp_path / "cut.webp"
    cut.write_bytes(raw[:-10])                    # chunks chopped, header intact
    assert _cache_decodable(cut) is False


# ------------------------------------------------------- formats unchanged


def test_png_gif_and_non_webp_riff_gates_unchanged(tmp_path: Path):
    """The formats that already worked must not regress: PNG needs its IEND
    chunk, GIF its trailer byte, and a non-WebP RIFF is still refused."""
    png = tmp_path / "ok.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png, format="PNG")
    assert _cache_decodable(png) is True
    png_raw = png.read_bytes()
    cut_png = tmp_path / "cut.png"
    cut_png.write_bytes(png_raw[:-12])            # IEND chopped off
    assert _cache_decodable(cut_png) is False

    gif = tmp_path / "ok.gif"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(gif, format="GIF")
    assert _cache_decodable(gif) is True

    riff_junk = tmp_path / "junk.wav"
    riff_junk.write_bytes(b"RIFF" + (99).to_bytes(4, "little")
                          + b"WAVE" + b"\x00" * 32)
    assert _cache_decodable(riff_junk) is False
