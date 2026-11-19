# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for archive-extraction hardening — extraction isolation
and caps, cache-tail validation and the launcher's dependency preflight.

Covers the hardening fixes:
1) CLI/UnRAR extraction now runs in a PRIVATE per-call staging folder (two
   archives containing "cover.jpg" can no longer overwrite each other's
   output inside the shared thumbnail cache, and the missing-file fallback
   can no longer kidnap an unrelated cached thumbnail);
2) the extraction cap is enforced DURING the write on every backend
   (capped copy for 7z-CLI, cap-on-sink for UnRAR, max_extract_size +
   capped copy for py7zr) — a lying archive header cannot fill the disk;
3) a truncated PNG/JPEG/GIF cache file is detected and rebuilt instead of
   passing the "valid header" check forever;
4) run.bat checks installed VERSIONS (deps_probe.py), not just imports.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from PIL import Image

import deps_probe
import filethumbs
from filethumbs import _CapWriter, _cache_decodable

# ---------------------------------------------------------------- _CapWriter


def test_cap_writer_enforces_cap_mid_stream(tmp_path: Path):
    dst = tmp_path / "out.bin"
    with open(dst, "wb") as f:
        w = _CapWriter(f, cap=8)
        assert w.write(b"12345678") == 8          # exactly at the cap: ok
        with pytest.raises(OSError, match="extraction cap"):
            w.write(b"9")                          # one byte over: hard stop
    # the write that tripped the cap must not have landed
    assert dst.stat().st_size == 8


# ------------------------------------------------------------ py7zr backend

pytest.importorskip("py7zr", reason="py7zr is an optional archive backend")


def _make_7z_with_cover(path: Path, blob: bytes) -> None:
    import py7zr
    src = path.with_suffix(".jpg")
    src.write_bytes(blob)
    with py7zr.SevenZipFile(path, "w") as z:
        z.write(src, arcname="cover.jpg")
    src.unlink(missing_ok=True)


def test_py7zr_opener_is_concurrent_safe(tmp_path: Path):
    """Two archives both containing cover.jpg, extracted in parallel — the
    old name-derived shared staging folder (ax7_<sha1(member)>) let the two
    workers overwrite/rmtree each other's output."""
    blob_a = b"A" * 2048
    blob_b = b"B" * 2048
    arc_a = tmp_path / "a.7z"
    arc_b = tmp_path / "b.7z"
    _make_7z_with_cover(arc_a, blob_a)
    _make_7z_with_cover(arc_b, blob_b)

    open_a = filethumbs._arc_open(arc_a)
    open_b = filethumbs._arc_open(arc_b)
    assert open_a and open_b, "_arc_open must serve both archives"
    dest_a = tmp_path / "covers" / "a.jpg"
    dest_b = tmp_path / "covers" / "b.jpg"

    errs: list[str] = []

    def run(d, opener, dest):
        try:
            opener("cover.jpg", dest)
        except Exception as e:                    # noqa: BLE001 — test records
            errs.append(repr(e))

    ta = threading.Thread(target=run, args=(open_a, open_a[2], dest_a))
    tb = threading.Thread(target=run, args=(open_b, open_b[2], dest_b))
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errs, f"opener raised: {errs}"
    assert dest_a.read_bytes() == blob_a, "archive A got the wrong cover"
    assert dest_b.read_bytes() == blob_b, "archive B got the wrong cover"
    # no staging leftovers next to the destinations
    leftovers = [p.name for p in (tmp_path / "covers").iterdir()
                 if p.name not in ("a.jpg", "b.jpg")]
    assert not leftovers, f"staging folders leaked: {leftovers}"


def test_py7zr_opener_enforces_cap(tmp_path: Path, monkeypatch):
    """A member bigger than the cap must be refused BEFORE landing on disk —
    the old code only measured the file after extraction."""
    blob = b"C" * 4096
    arc = tmp_path / "big.7z"
    _make_7z_with_cover(arc, blob)
    monkeypatch.setattr(filethumbs, "_ARC_EXTRACT_CAP", 1024)
    opened = filethumbs._arc_open(arc)
    assert opened, "_arc_open must still open the archive under a small cap"
    dest = tmp_path / "out.jpg"
    with pytest.raises(OSError, match="extraction cap"):
        opened[2]("cover.jpg", dest)
    assert not dest.exists(), "the over-cap member must not reach the disk"


# ------------------------------------------------------------ cache tails


def test_cache_decodable_png_tail_check(tmp_path: Path):
    """A truncated PNG used to pass on its 8-byte signature alone; only the
    IEND chunk at the end proves the file is complete."""
    png = tmp_path / "ok.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png, format="PNG")
    assert _cache_decodable(png) is True
    raw = png.read_bytes()
    cut = tmp_path / "cut.png"
    cut.write_bytes(raw[:-12])                    # IEND chopped off
    assert _cache_decodable(cut) is False


def test_cache_decodable_jpeg_and_webp(tmp_path: Path):
    jpg = tmp_path / "ok.jpg"
    Image.new("RGB", (8, 8), (9, 9, 9)).save(jpg, format="JPEG")
    assert _cache_decodable(jpg) is True
    raw = jpg.read_bytes()
    cut = tmp_path / "cut.jpg"
    cut.write_bytes(raw[:-4])                     # EOI marker gone
    assert _cache_decodable(cut) is False

    webp = tmp_path / "ok.webp"
    Image.new("RGB", (8, 8), (4, 5, 6)).save(webp, format="WEBP")
    assert _cache_decodable(webp) is True


# ------------------------------------------------- CLI backend staging


_STUB_7Z = r"""#!/bin/sh
# Minimal 7z stand-in: understands `e <archive> -o<dir> -y -spd -- <name>`
# (writes <name> into the -o dir) and answers `l -slt` with empty output.
mode=""
out=""
name=""
prev=""
for a in "$@"; do
    [ "$prev" = "--" ] && name="$a"
    case "$a" in
        e|l) mode="$a" ;;
        -o*) out="${a#-o}" ;;
    esac
    prev="$a"
done
if [ "$mode" = "e" ]; then
    printf '%s' "$STUB_BLOB" > "$out/$name"
fi
exit 0
"""


@pytest.mark.skipif(os.name != "posix", reason="shell stub tool (POSIX only)")
def test_cli_7z_extract_uses_private_staging(tmp_path: Path, monkeypatch):
    """The old backend extracted straight into dest.parent — the SHARED cache
    root — so two concurrent archives clobbered each other and the fallback
    glob could grab ANY file of that folder (and then delete it). Now the
    output must come from a private staging folder that is removed again."""
    tool = tmp_path / "stub-7z.sh"
    tool.write_text(_STUB_7Z, encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("STUB_BLOB", "STUB-CONTENT")

    shared = tmp_path / "cache"                   # plays the shared cache root
    shared.mkdir()
    # an unrelated cached thumbnail that the old cands[0] glob would kidnap
    victim = shared / "0000-other-item.webp"
    victim.write_bytes(b"unrelated cached thumbnail")
    dest = shared / "ax_target.jpg"

    arc = filethumbs._CliArc(str(tool), tmp_path / "fake.7z", "7z",
                             list_members=False)
    arc.extract("cover.jpg", dest, timeout=30)

    assert dest.read_bytes() == b"STUB-CONTENT", "member content must arrive"
    assert victim.read_bytes() == b"unrelated cached thumbnail", \
        "the fallback must never kidnap another cache entry"
    leftovers = [p.name for p in shared.iterdir()
                 if p.name not in ("ax_target.jpg", victim.name)]
    assert not leftovers, \
        f"extraction must not dump files into the shared folder: {leftovers}"


@pytest.mark.skipif(os.name != "posix", reason="shell stub tool (POSIX only)")
def test_cli_7z_extract_enforces_cap(tmp_path: Path, monkeypatch):
    """A CLI extraction over the cap must fail without writing dest — the
    old path had no cap at all (only a timeout)."""
    tool = tmp_path / "stub-7z.sh"
    tool.write_text(_STUB_7Z, encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("STUB_BLOB", "X" * 4096)
    monkeypatch.setattr(filethumbs, "_ARC_EXTRACT_CAP", 1024)

    shared = tmp_path / "cache"
    shared.mkdir()
    dest = shared / "ax_target.jpg"
    arc = filethumbs._CliArc(str(tool), tmp_path / "fake.7z", "7z",
                             list_members=False)
    with pytest.raises(OSError, match="extraction cap"):
        arc.extract("cover.jpg", dest, timeout=30)
    assert not dest.exists(), "the over-cap member must not reach the disk"
    assert not [p for p in shared.iterdir()
                if p.name != "ax_target.jpg"], \
        "staging must be cleaned up even on a cap failure"


# ------------------------------------------------- launcher dependency check


def test_deps_probe_reads_floors_from_requirements():
    mins = deps_probe.required_min()
    assert mins.get("py7zr") == "1.1.4", "requirements.txt floor drifted"
    assert mins.get("rarfile") == "4.5"
    assert mins.get("pypdfium2") == "5.15.2"


def test_deps_probe_ver_tuple_ordering():
    f = deps_probe._ver_tuple
    assert f("1.1.4") > f("1.1.3")
    assert f("1.10.0") > f("1.9.9")
    assert f("4.5") == f("4.5.0")
    assert f("5.15.2.post1") >= f("5.15.2")
    assert f("junk") == (0,)


def test_deps_probe_flags_missing_and_stale(monkeypatch):
    import importlib.metadata

    real_version = importlib.metadata.version

    def fake_version(name):
        if name == "py7zr":
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    assert deps_probe.probe() == 1, "a missing core backend must fail the probe"

    def stale_version(name):
        return {"py7zr": "1.0.0"}.get(name, real_version(name))

    monkeypatch.setattr(importlib.metadata, "version", stale_version)
    assert deps_probe.probe() == 1, "a below-floor py7zr must fail the probe"


def test_deps_probe_passes_on_floored_env():
    # CI installs exactly requirements.txt before running the suite, so the
    # probe must succeed there; locally it only passes with the floors met.
    assert deps_probe.probe() == 0, (
        "deps_probe failed — the installed archive/PDF backends are below the "
        f"requirements floors: {deps_probe.required_min()}")
