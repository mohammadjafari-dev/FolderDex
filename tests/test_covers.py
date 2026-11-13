# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Unit tests for covers.py + the Store custom-poster API (round 11).

Rules under test (must never be violated):
- the user's image is NORMALIZED and COPIED into APP_DIR/.covers/ — the app
  never writes into the user's folders;
- names are content hashes, so re-import is idempotent;
- animated covers keep a static frame-0 file for the card grid;
- a missing cover file falls back to the folder icon without crashing.

Round 12 additions: covers_dir() means <app>/.covers in BOTH branches (D8),
replacing a cover deletes the stale file (D3), write errors are not reported
as “invalid image” (D5), and oversized sources are rejected before decode
(D6).
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import covers  # noqa: E402
import imgcache  # noqa: E402
import portable  # noqa: E402
from covers import (  # noqa: E402
    COVERS_DIR_NAME,
    CoverError,
    CoverReadError,
    CoverWriteError,
    collect_garbage,
    covers_dir,
    import_cover,
    is_animated,
)
from store import Store  # noqa: E402


@pytest.fixture()
def fake_app(tmp_path: Path, monkeypatch):
    """پوشه‌ی برنامه‌ی قلابی — هم covers هم store (از مسیرِ portable)."""
    monkeypatch.setattr(portable, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(covers, "app_dir", lambda: tmp_path)
    return tmp_path


def _png_bytes(w=2000, h=3000, color=(120, 40, 200, 255)) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(b, "PNG")
    return b.getvalue()


# ------------------------------------------------------------ import ساکن
def test_import_static_png(fake_app: Path, tmp_path: Path):
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(2000, 3000))
    info = import_cover(src, fake_app)
    assert info["animated"] is False
    assert info["poster"].startswith(".covers/") and info["poster"].endswith(".webp")
    digest = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
    assert info["poster"] == f".covers/{digest}.webp"
    p = fake_app / info["poster"]
    assert p.is_file() and info["bytes"] == p.stat().st_size
    with Image.open(p) as im:
        # سقفِ ابعادِ کاور ساکن: ۱۰۲۴
        assert max(im.size) <= 1024


def test_import_is_idempotent(fake_app: Path, tmp_path: Path):
    src = tmp_path / "poster.jpg"
    b = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 200, 30)).save(b, "JPEG")
    src.write_bytes(b.getvalue())
    info1 = import_cover(src, fake_app)
    d = covers_dir(fake_app)
    # D8: covers_dir دیگر خودِ پوشه‌ی برنامه را برنمی‌گرداند — معنا در هر دو
    # شاخه یکسان است و شمردنِ فایل‌ها واقعی است (قبلاً ۱۰۰ فایلِ تکراری هم
    # پاس می‌شد چون iterdir فقط « خودِ ‎.covers» را می‌دید)
    assert d.name == COVERS_DIR_NAME and d.parent == fake_app
    files_before = {p.name for p in d.glob("*.webp")}
    info2 = import_cover(src, fake_app)
    files_after = {p.name for p in d.glob("*.webp")}
    assert info1 == info2
    assert files_before == files_after == {Path(info1["poster"]).name}
    assert len(files_after) == len({p.name for p in d.glob("*.webp")})


# ------------------------------------------------------------ import متحرک
def _animated_gif(tmp_path: Path, n_frames=3) -> Path:
    frames = [Image.new("RGB", (400, 400), (i * 40 % 256, 90, 160))
              for i in range(n_frames)]
    src = tmp_path / "anim.gif"
    frames[0].save(src, save_all=True, append_images=frames[1:],
                   duration=60, loop=0)
    return src


def test_import_animated_gif(fake_app: Path, tmp_path: Path):
    src = _animated_gif(tmp_path, 3)
    info = import_cover(src, fake_app)
    assert info["animated"] is True
    digest = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
    assert info["poster"] == f".covers/{digest}.anim.webp"
    assert info["poster_static"] == f".covers/{digest}.webp"
    anim = fake_app / info["poster"]
    static = fake_app / info["poster_static"]
    assert anim.is_file() and static.is_file()
    with Image.open(anim) as im:
        assert int(getattr(im, "n_frames", 1)) > 1
        assert max(im.size) <= covers.ANIM_MAX_PX
    with Image.open(static) as im:
        assert im.size == (400, 400)          # فریمِ ۰ با کیفیتِ کاملِ ساکن


def test_frame_and_duration_caps(fake_app: Path, tmp_path: Path):
    src = _animated_gif(tmp_path, 80)         # بیش از سقفِ ۶۰ فریم
    info = import_cover(src, fake_app)
    with Image.open(fake_app / info["poster"]) as im:
        assert int(getattr(im, "n_frames", 1)) <= covers.ANIM_MAX_FRAMES


def test_is_animated_helper(tmp_path: Path):
    assert is_animated(_animated_gif(tmp_path, 3)) is True
    src = tmp_path / "s.png"
    src.write_bytes(_png_bytes(50, 50))
    assert is_animated(src) is False


# ------------------------------------------------------------ ردِ ورودی
def test_import_rejects_text_file(fake_app: Path, tmp_path: Path):
    src = tmp_path / "fake.png"
    src.write_bytes(b"this is definitely not an image")
    with pytest.raises(CoverError):
        import_cover(src, fake_app)


def test_import_rejects_wrong_ext(fake_app: Path, tmp_path: Path):
    src = tmp_path / "notes.txt"
    src.write_bytes(_png_bytes(64, 64))
    with pytest.raises(CoverError):
        import_cover(src, fake_app)


def test_import_rejects_missing_file(fake_app: Path, tmp_path: Path):
    with pytest.raises(CoverError):
        import_cover(tmp_path / "nope.png", fake_app)


# ------------------------------------------------------------ D8 — معنای covers_dir
def test_covers_dir_means_app_covers_in_both_branches(fake_app: Path):
    """covers_dir(fake_app) باید ‎fake_app/.covers‎ بدهد، نه خودِ fake_app."""
    d = covers_dir(fake_app)
    assert d.name == COVERS_DIR_NAME
    assert d.parent == fake_app
    assert d == fake_app / COVERS_DIR_NAME
    assert covers_dir() == fake_app / COVERS_DIR_NAME   # با monkeypatchِ app_dir


# ------------------------------------------------------------ Store API
@pytest.fixture()
def store(tmp_path: Path, fake_app: Path):
    s = Store(tmp_path / "config.json")
    yield s
    s.close()


def _item(path="C:/media/show"):
    return SimpleNamespace(path=Path(path), icon_path=Path(path) / "icon.png")


def test_store_set_and_clear_custom_poster(store: Store, fake_app: Path,
                                           tmp_path: Path):
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(300, 300))
    info = import_cover(src, fake_app)
    item_path = "C:/media/show"
    store.set_custom_poster(item_path, info)
    m = store.meta(item_path)
    assert m["poster"] == info["poster"]
    assert (fake_app / m["poster"]).is_file()
    # پاک‌سازی — فایل‌ها هم حذف می‌شوند (idempotent)
    store.clear_custom_poster(item_path)
    assert "poster" not in store.meta(item_path)
    assert not (fake_app / info["poster"]).exists()
    # دوباره: خطا نمی‌دهد
    store.clear_custom_poster(item_path)


def test_poster_of_prefers_custom(store: Store, fake_app: Path, tmp_path: Path):
    src = _animated_gif(tmp_path, 2)
    info = import_cover(src, fake_app)
    item_path = "C:/media/show"
    store.set_custom_poster(item_path, info)
    it = _item(item_path)
    assert store.poster_of(it) == fake_app / info["poster"]
    assert store.poster_of(it, static=True) == fake_app / info["poster_static"]
    # بدون کاور → آیکونِ پوشه
    store.clear_custom_poster(item_path)
    assert store.poster_of(it) == it.icon_path


def test_missing_cover_file_is_none_and_does_not_crash(store: Store,
                                                       fake_app: Path):
    item_path = "C:/media/gone"
    store.set_meta(item_path, poster=".covers/does-not-exist.webp")
    poster, static = store.custom_poster(item_path)
    assert poster is None and static is None
    it = _item(item_path)
    assert store.poster_of(it) == it.icon_path
    # مسیرِ مطلقِ قدیمی (فرمِ ویرایشِ قبل) — گم‌شده هم کرش نمی‌کند
    store.set_meta(item_path, poster="C:/nowhere/old.jpg")
    assert store.custom_poster(item_path) == (None, None)


def test_legacy_absolute_poster_still_works(store: Store, fake_app: Path,
                                            tmp_path: Path):
    src = tmp_path / "old-style.jpg"
    import io
    b = io.BytesIO()
    Image.new("RGB", (100, 100), (5, 5, 5)).save(b, "JPEG")
    src.write_bytes(b.getvalue())
    item_path = "C:/media/legacy"
    store.set_meta(item_path, poster=str(src))     # مسیرِ مطلق — رفتارِ قدیمی
    it = _item(item_path)
    assert store.poster_of(it) == src


# -------------------------------------------- کلیدِ متا: رشته‌ی خام ≡ Path
def test_meta_key_normalizes_string_and_path(store: Store):
    """کلیدِ متا برای «رشته‌ی خام» و «Path» باید یکی باشد.

    روی ویندوز str(Path("C:/media/show")) بک‌اسلش می‌شود ولی رشته‌ی خامِ
    «C:/media/show» دست‌نخورده می‌ماند — همین ناهماهنگی، کاور/امتیازِ عنوانِ
    نوشته‌شده با رشته‌ی خام را «گم» می‌کرد (۲ تستِ قرمزِ ویندوزِ round 12؛
    روی لینوکس هر دو فرم یکی‌اند و باگ دیده نمی‌شد). فرمِ «//» همان شکافِ
    نرمال‌سازی را روی *هر* OS بازتولید می‌کند چون Path دو اسلش متوالی را له
    می‌کند ولی رشته‌ی خام نه.
    """
    store.set_meta("C:/media//show", rating=4)
    assert store.meta(Path("C:/media/show")).get("rating") == 4
    # فرمِ دیگرِ همان مسیر هم باید همان خانه را ببیند
    assert store.meta("C:/media/./show").get("rating") == 4


def test_poster_of_with_raw_string_key(store: Store, fake_app: Path,
                                       tmp_path: Path):
    """کاورِ سفارشیِ نوشته‌شده با کلیدِ رشته‌ی خام، با Path هم پیدا شود."""
    src = tmp_path / "p.png"
    src.write_bytes(_png_bytes(64, 64))
    info = import_cover(src, fake_app)
    store.set_custom_poster("C:/media//show", info)
    it = _item("C:/media/show")          # خواندن با Path — قبلاً به آیکون می‌افتاد
    assert store.poster_of(it) == fake_app / info["poster"]
    assert store.poster_of(it, static=True) == fake_app / info["poster"]


def test_cover_status_for_dialog(store: Store, fake_app: Path, tmp_path: Path):
    item_path = "C:/media/show"
    assert store.cover_status(item_path)["kind"] == "none"
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(100, 100))
    info = import_cover(src, fake_app)
    store.set_custom_poster(item_path, info)
    stc = store.cover_status(item_path)
    assert stc["kind"] == "custom" and stc["missing"] is False
    # فایل حذف شد → missing=True (برای هشدارِ نارنجیِ فرمِ ویرایش)
    (fake_app / info["poster"]).unlink()
    assert store.cover_status(item_path)["missing"] is True


# ------------------------------------------------------------ D3 — تعویض/جمع‌آوری
def test_replace_cover_leaves_one_file(store: Store, fake_app: Path,
                                       tmp_path: Path):
    """هر تعویض کاور باید فایلِ قبلی را پاک کند — ‎.covers/‎ پف نمی‌کند."""
    item_path = "C:/media/show"
    src1 = tmp_path / "a.png"
    src1.write_bytes(_png_bytes(300, 300, (255, 0, 0, 255)))
    info1 = import_cover(src1, fake_app)
    store.set_custom_poster(item_path, info1)
    d = covers_dir(fake_app)
    assert len(list(d.glob("*.webp"))) == 1

    src2 = tmp_path / "b.png"
    src2.write_bytes(_png_bytes(300, 300, (0, 255, 0, 255)))
    info2 = import_cover(src2, fake_app)
    store.set_custom_poster(item_path, info2)
    files = [p.name for p in d.glob("*.webp")]
    assert files == [Path(info2["poster"]).name], \
        f"stale cover file left behind: {files}"
    assert (fake_app / info1["poster"]).exists() is False


def test_gc_removes_only_unreferenced(fake_app: Path, tmp_path: Path):
    """collect_garbage فقط یتیم‌ها را پاک می‌کند — ارجاع‌دارها دست‌نخورده.

    (round 13 §۲: فایلِ تازه — کمتر از ۶۰ ثانیه — رها می‌شود؛ برای تستِ
    حذفِ واقعیِ یتیم، mtime را ۲ دقیقه عقب می‌بریم.)"""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    keep = d / "keep.webp"
    orphan = d / "orphan.webp"
    tmpfile = d / ".tmp-123-keep.webp"
    keep.write_bytes(b"RIFF0000WEBP")
    orphan.write_bytes(b"RIFF0000WEBP")
    tmpfile.write_bytes(b"partial")
    old = time.time() - 120
    for f in (keep, orphan):
        os.utime(f, (old, old))
    n = collect_garbage({f"{COVERS_DIR_NAME}/keep.webp"}, fake_app)
    assert n == 1
    assert keep.is_file() and not orphan.exists()
    assert tmpfile.is_file()          # فایلِ موقتِ نوشتنِ اتمیک کاری نداریم
    # idempotent
    assert collect_garbage(set(), fake_app) == 1
    assert collect_garbage(set(), fake_app) == 0


def test_gc_accepts_backslash_refs(fake_app: Path, tmp_path: Path):
    """متای قدیمی با \‎ هم ارجاع حساب می‌شود."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "back.webp"
    p.write_bytes(b"x")
    assert collect_garbage({".covers\\back.webp"}, fake_app) == 0
    assert p.is_file()


# --------------------------------- round 13 §۲ — منبعِ GC دیتابیس است نه اسکن
def test_gc_keeps_covers_of_unscanned_items(store: Store, fake_app: Path,
                                            tmp_path: Path):
    """متا در DB هست ولی آیتم در اسکنِ فعلی نیست → کاورِ همان عنوان پاک نمی‌شود.

    ریشه‌ی موقتاً آفلاین (درایوِ بیرونی جدا شده) در self.items نیست؛ قبلاً
    refs از آیتم‌های اسکن ساخته می‌شد و کاورِ عنوان‌های آفلاین پاک می‌شد —
    از‌دست‌رفتنِ داده. الان منبعِ refs خودِ دیتابیس است (store.cover_refs).
    """
    src = _animated_gif(tmp_path, 2)
    info = import_cover(src, fake_app)
    store.set_meta("D:/Movies/Offline Title", poster=info["poster"],
                   poster_static=info["poster_static"])
    old = time.time() - 120           # قدیمی‌سازی تا graceِ ۶۰ ثانیه‌ای مانعِ تست نشود
    os.utime(fake_app / info["poster"], (old, old))
    os.utime(fake_app / info["poster_static"], (old, old))
    n = collect_garbage(store.cover_refs(), fake_app)
    assert n == 0
    assert (fake_app / info["poster"]).is_file()
    assert (fake_app / info["poster_static"]).is_file()
    # با برگشتنِ ریشه، کاور سرِ جایش است
    it = _item("D:/Movies/Offline Title")
    assert store.poster_of(it) == fake_app / info["poster"]


def test_gc_still_removes_true_orphans(store: Store, fake_app: Path,
                                       tmp_path: Path):
    """فایلِ بدونِ هیچ ارجاعی در دیتابیس و قدیمی (≥۶۰s) → حذف می‌شود."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "true-orphan.webp"
    p.write_bytes(b"RIFF0000WEBP")
    old = time.time() - 120
    os.utime(p, (old, old))
    assert collect_garbage(store.cover_refs(), fake_app) == 1
    assert not p.exists()


def test_gc_spares_fresh_unreferenced_file(fake_app: Path, tmp_path: Path):
    """فایلِ تازه‌ساخت بدونِ ارجاع (<۶۰s) → رها می‌شود.

    کاربر ممکن است همین حالا کاوری را وارد کرده باشد و متایش هنوز در
    دیتابیس نوشته نشده باشد؛ grace نگهبانِ همین مسابقه‌ی زمانی است."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "fresh-orphan.webp"
    p.write_bytes(b"RIFF0000WEBP")     # mtime الان
    assert collect_garbage(set(), fake_app) == 0
    assert p.is_file()


# --------------- round 20 §۳ — گاردهای ضدِ پاک‌شدنِ جمعیِ کاورها بعد از خرابیِ DB
def test_gc_skipped_while_db_quarantined(store: Store, fake_app: Path):
    """DB قرنطینه‌شده → GC هیچ چیزی پاک نمی‌کند (حتی یتیمِ قدیمی).

    سناریوی ممیزی: دیتابیس malformed → قرنطینه → Store تازه و خالی؛
    بدونِ گارد، اسکنِ بعدی *همه‌ی* کاورهای قابل‌بازیابی را پاک می‌کرد
    (پاک‌شدنِ جمعیِ داده — رفعِ پوشِ round 20 §۳)."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    orphan = d / "orphan-q.webp"
    orphan.write_bytes(b"RIFF0000WEBP")
    old = time.time() - 120
    os.utime(orphan, (old, old))
    store.db_quarantined = True            # شبیه‌سازیِ قرنطینهٔ _open_db
    try:
        assert collect_garbage(set(), fake_app, store=store) == 0
        assert orphan.is_file()
    finally:
        store.db_quarantined = False
    # یک اجرا بعدتر: پرچم ریست شده ولی DB تازه هنوز آیتم ندارد — گاردِ
    # «بدونِ آیتم اما ‎.covers پُر» همان‌جا می‌ایستد؛ فایل همچنان سالم
    assert collect_garbage(set(), fake_app, store=store) == 0
    assert orphan.is_file()


def test_gc_protects_covers_when_store_has_no_items(store: Store, fake_app: Path):
    """DBِ بدونِ آیتم اما ‎.covers/‎ پُر → حذفِ جمعی ممنوع.

    هم سناریوی «یک اجرا بعد از قرنطینه» و هم «کتابخانهٔ واقعاً خالی» را
    می‌پوشاند — در هر دو، حذفِ جمعی بی‌راه‌حل است و یتیمِ احتمالی بی‌زیان است."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    keep1 = d / "a.webp"
    keep2 = d / "b.webp"
    keep1.write_bytes(b"a" * 32)
    keep2.write_bytes(b"b" * 32)
    old = time.time() - 120
    for f in (keep1, keep2):
        os.utime(f, (old, old))
    assert store.cover_refs() == set()     # DB تازه — هیچ آیتمی نیست
    assert collect_garbage(store.cover_refs(), fake_app, store=store) == 0
    assert keep1.is_file() and keep2.is_file()


def test_gc_with_items_still_removes_true_orphans(store: Store, fake_app: Path,
                                                  tmp_path: Path):
    """گاردها بیش‌ازحد سخت‌گیر نیستند: با آیتمِ موجود، یتیمِ واقعی پاک می‌شود.

    آیتم‌ها موجودند ولی به هیچ کاوری ارجاع نمی‌دهند → فایلِ قدیمیِ بدونِ
    ارجاع واقعاً یتیم است و باید پاک شود (رفتارِ قبل از گاردها حفظ می‌شود)."""
    src = tmp_path / "p.png"
    src.write_bytes(_png_bytes(64, 64))
    info = import_cover(src, fake_app)
    store.set_meta("C:/media/it", poster=info["poster"])
    d = covers_dir(fake_app)
    orphan = d / "true-orphan.webp"
    orphan.write_bytes(b"RIFF0000WEBP")
    old = time.time() - 120
    os.utime(orphan, (old, old))
    assert collect_garbage(set(), fake_app, store=store) == 1
    assert not orphan.exists()
    assert (fake_app / info["poster"]).is_file()   # ارجاع‌دار دست‌نخورده


# ------------------------------------------------------------ D5 — پیام‌های متمایز
def test_cover_error_messages(fake_app: Path, tmp_path: Path, monkeypatch):
    """خطای نوشتن نباید به‌عنوان «فایل تصویرِ معتبر نیست» نشان داده شود."""
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(64, 64))
    monkeypatch.setattr(covers, "_atomic_save",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("disk full")))
    with pytest.raises(covers.CoverWriteError) as ei:
        import_cover(src, fake_app)
    assert str(ei.value) == covers.T(covers.WRITE_ERR_TEXT)
    assert covers.T(covers.READ_ERR_TEXT) not in str(ei.value)


def test_read_error_is_subclass_of_cover_error(fake_app: Path, tmp_path: Path):
    """except CoverErrorهای موجود دست‌نخورده می‌مانند (سازگاریِ رابط)."""
    src = tmp_path / "fake.png"
    src.write_bytes(b"not an image at all")
    with pytest.raises(CoverError):
        import_cover(src, fake_app)
    with pytest.raises(CoverReadError):
        import_cover(src, fake_app)


def test_mkdir_failure_is_write_error(fake_app: Path, tmp_path: Path,
                                      monkeypatch):
    """پوشه‌ی برنامه‌ی فقط‌خواندنی → CoverWriteError، نه پیامِ «عکسِ خراب»."""
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(64, 64))
    real_mkdir = Path.mkdir

    def _boom(self, *a, **k):
        if self.name == COVERS_DIR_NAME:
            raise OSError(13, "Permission denied")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", _boom)
    with pytest.raises(CoverWriteError):
        import_cover(src, fake_app)


# ------------------------------------------------------------ D6 — سقفِ ورودی
def _png_header_with_size(w: int, h: int) -> bytes:
    """PNG با IHDRِ دست‌ساز — فقط هدر؛ دیکدِ کامل لازم نیست (D6 قبل از دیکد رد می‌کند)."""
    import struct
    import zlib
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    ihdr = b"IHDR" + ihdr_data
    chunk = struct.pack(">I", len(ihdr_data)) + ihdr + \
        struct.pack(">I", zlib.crc32(ihdr) & 0xFFFFFFFF)
    return sig + chunk + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def test_rejects_huge_file(fake_app: Path, tmp_path: Path):
    """فایلِ منبعِ بیش از سقفِ حجمی قبل از هر دیکدی رد می‌شود.

    (C4-c) پیامِ سقفِ حجمی حالا *عددِ سقف* را هم دارد تا کاربر بداند چقدر
    باید کوچک کند — پس به‌جای برابریِ کامل، پیشوندِ پیام چک می‌شود."""
    import unittest.mock as _mock
    src = tmp_path / "huge.png"
    with open(src, "wb") as f:
        f.write(b"\x00" * 1024)          # ۱KB — از سقفِ کوچک‌شده‌ی تست بزرگ‌تر
    with _mock.patch.object(covers, "MAX_SOURCE_BYTES", 512):
        with pytest.raises(CoverReadError) as ei:
            import_cover(src, fake_app)
    assert str(ei.value).startswith(covers.T(covers.TOO_BIG_ERR_TEXT))
    assert "MB" in str(ei.value) or "مگابایت" in str(ei.value)


def test_rejects_huge_dimensions(fake_app: Path, tmp_path: Path):
    """تصویری که ابعادِ ۴۰+ مگاپیکسلی اعلام می‌کند رد می‌شود — قبل از دیکدِ کامل."""
    src = tmp_path / "big.png"
    src.write_bytes(_png_header_with_size(10_000, 9_000))   # 90MP > 40MP
    # سیاستِ روشن (round 20): ‎Image.open‎ هشدارِ DecompressionBomb می‌دهد چون
    # این تست *عمداً* از آستانهٔ خودِ Pillow (~89MP) هم رد می‌شود — فقط همین
    # هشدارِ مشخص بی‌صدا می‌شود، نه هشدارهای عمومی.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore",
                                category=Image.DecompressionBombWarning)
        with pytest.raises(CoverReadError) as ei:
            import_cover(src, fake_app)
    assert str(ei.value) == covers.T(covers.TOO_BIG_ERR_TEXT)


def test_normal_size_still_imports(fake_app: Path, tmp_path: Path):
    """سقف‌ها فقط بزرگ‌ها را می‌گیرند — عکسِ معمولی همان‌طور که قبلاً می‌آمد."""
    src = tmp_path / "ok.png"
    src.write_bytes(_png_bytes(800, 600))
    info = import_cover(src, fake_app)
    assert info["animated"] is False and info["poster"].startswith(".covers/")


# ------------------------- round 20 §۲ — چرخشِ EXIF (عکسِ گوشی خوابیده نبود)
def _jpeg_with_orientation(w: int, h: int, orientation: int,
                           tmp_path: Path, name: str = "phone.jpg") -> Path:
    """JPEG با تگِ EXIFِ orientation (274) — orientation=1 هم صریح نوشته می‌شود."""
    src = tmp_path / name
    ex = Image.Exif()
    ex[274] = orientation
    Image.new("RGB", (w, h), (200, 60, 60)).save(src, "JPEG", exif=ex)
    return src


def test_exif_rotated_jpeg_imports_upright(fake_app: Path, tmp_path: Path):
    """JPEG افقیِ ۴۰۰×۲۰۰ با EXIF orientation=6 باید ۲۰۰×۴۰۰ ذخیره شود.

    قبلاً تگِ چرخش نادیده گرفته می‌شد (هیچ exif_transposeای در کلِ پروژه
    نبود — یافتهٔ ممیزی) و عکسِ گوشی که به‌عنوان کاور انتخاب می‌شد خوابیده/
    وارونه می‌نشست. حالا چرخش پیش از resize در خودِ WebP پخته می‌شود."""
    src = _jpeg_with_orientation(400, 200, 6, tmp_path)
    info = import_cover(src, fake_app)
    assert info["animated"] is False
    with Image.open(fake_app / info["poster"]) as im:
        assert im.size == (200, 400), f"expected upright 200x400, got {im.size}"


def test_exif_free_jpeg_is_not_rotated(fake_app: Path, tmp_path: Path):
    """منفی: عکسِ بدونِ چرخشِ مؤثر (orientation=1) عیناً landscape می‌ماند."""
    src = _jpeg_with_orientation(400, 200, 1, tmp_path)
    info = import_cover(src, fake_app)
    with Image.open(fake_app / info["poster"]) as im:
        assert im.size == (400, 200), f"expected untouched 400x200, got {im.size}"


def test_exif_helper_leaves_animated_gif_untouched(tmp_path: Path):
    """exif_transpose روی تصویرِ چندفریمی فریم‌ها می‌اندازد (آزمایش با Pillow
    11.3: n_frames ‎2→1‎) — هلپر باید متحرک را عیناً برگرداند، وگرنه کاورِ
    متحرک بی‌صدا ساکن می‌شد."""
    from imgcache import apply_exif_transpose
    src = _animated_gif(tmp_path, 2)
    with Image.open(src) as im:
        assert int(getattr(im, "n_frames", 1)) == 2
        out = apply_exif_transpose(im)
        assert int(getattr(out, "n_frames", 1)) == 2


# --------------------------------------- C3 — کاورِ مشترکِ دو عنوان
def test_shared_cover_survives_replace(store: Store, fake_app: Path,
                                       tmp_path: Path):
    """دو عنوان با یک عکس: تعویضِ کاورِ یکی، کاورِ دیگری را نمی‌پراند (C3).

    نامِ فایل‌های ‎.covers/‎ هشِ محتواست؛ قبلاً حذفِ بی‌چکِ فایلِ «قدیمی»،
    فایلِ مشترکِ عنوانِ دیگر را هم می‌کشت و کاورِ آن به آیکونِ پوشه می‌باخت."""
    src = tmp_path / "shared.png"
    src.write_bytes(_png_bytes(300, 300, (255, 0, 0, 255)))
    info = import_cover(src, fake_app)
    store.set_custom_poster("C:/media/A", info)
    store.set_custom_poster("C:/media/B", info)
    # فایلِ مشترک: هر دو به یک نام اشاره می‌کنند
    assert store.meta("C:/media/A")["poster"] == \
        store.meta("C:/media/B")["poster"]

    info2 = import_cover(_mk_png(tmp_path), fake_app)
    store.set_custom_poster("C:/media/B", info2)
    # کاورِ A سرِ جایش است — هم متا هم فایل
    assert store.custom_poster("C:/media/A")[0] == fake_app / info["poster"]
    assert (fake_app / info["poster"]).is_file()
    # کاورِ B تازه شد
    assert store.meta("C:/media/B")["poster"] == info2["poster"]


def test_shared_cover_survives_clear(store: Store, fake_app: Path,
                                     tmp_path: Path):
    """دو عنوان با یک عکس: حذفِ کاورِ یکی، کاورِ دیگری را نمی‌پراند (C3)."""
    src = tmp_path / "shared.png"
    src.write_bytes(_png_bytes(300, 300, (0, 128, 255, 255)))
    info = import_cover(src, fake_app)
    store.set_custom_poster("C:/media/A", info)
    store.set_custom_poster("C:/media/B", info)
    store.clear_custom_poster("C:/media/B")
    assert store.custom_poster("C:/media/A") == \
        (fake_app / info["poster"], None)
    assert (fake_app / info["poster"]).is_file()
    assert "poster" not in store.meta("C:/media/B")


def test_shared_cover_removed_when_last_ref_goes(store: Store, fake_app: Path,
                                                 tmp_path: Path):
    """وقتی *آخرین* ارجاع هم رفت، فایلِ مشترک حذف می‌شود (رفتارِ D3 حفظ است)."""
    src = tmp_path / "shared.png"
    src.write_bytes(_png_bytes(300, 300, (90, 200, 90, 255)))
    info = import_cover(src, fake_app)
    store.set_custom_poster("C:/media/A", info)
    store.set_custom_poster("C:/media/B", info)
    store.clear_custom_poster("C:/media/B")
    assert (fake_app / info["poster"]).is_file()
    store.clear_custom_poster("C:/media/A")
    assert not (fake_app / info["poster"]).exists()


def _mk_png(tmp_path: Path) -> Path:
    src = tmp_path / "other.png"
    src.write_bytes(_png_bytes(300, 300, (0, 0, 255, 255)))
    return src


# --------------------------------------- C4 — خطاهای کاورِ GIF
def test_truncated_gif_is_read_error_or_partial(fake_app: Path, tmp_path: Path):
    """GIF ناقص: خطای کنترل‌شده یا کاورِ جزئی — هرگز خطای خامِ دیکد (C4-a).

    قبلاً `OSError: image file is truncated` خام از import_cover می‌پرید و UI
    پیامِ غلطِ «ذخیره‌ی کاور ممکن نشد (فضای دیسک…)» نشان می‌داد."""
    src = _animated_gif(tmp_path, 3)
    raw = src.read_bytes()
    (tmp_path / "trunc.gif").write_bytes(raw[: int(len(raw) * 2 / 3)])
    try:
        info = import_cover(tmp_path / "trunc.gif", fake_app)
    except CoverReadError:
        return                                  # سیاستِ رد — درست
    except (CoverWriteError, OSError) as e:     # noqa: B014
        pytest.fail(f"wrong error class for truncated GIF: {type(e).__name__}")
    else:
        # کاورِ جزئی — فایل‌ها باید واقعی باشند
        assert (fake_app / info["poster"]).is_file()


def test_heavy_gif_yields_valid_result_fast(fake_app: Path, tmp_path: Path):
    """GIFِ نویزیِ ۶۰ فریمی: نتیجه‌ی معتبر در زمانِ مهارشده — نه خطای بی‌راه‌حل (C4-b)."""
    import random
    rnd = random.Random(42)
    frames = []
    for i in range(60):
        im = Image.effect_noise((512, 512), 64).convert("RGB")
        px = im.load()
        for _ in range(64):                     # کمی ساختارِ رنگی
            x, y = rnd.randrange(512), rnd.randrange(512)
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        frames.append(im)
    src = tmp_path / "heavy.gif"
    frames[0].save(src, save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    t0 = time.monotonic()
    info = import_cover(src, fake_app)
    dt = time.monotonic() - t0
    assert (fake_app / info["poster"]).is_file()
    assert dt < 30, f"heavy GIF took {dt:.1f}s"
    if info.get("degraded"):
        # کاور ساکن (فریمِ اول) — پیامِ UI هم آماده است
        assert info["animated"] is False
        assert covers.T(covers.DEGRADED_MSG_TEXT)
    else:
        with Image.open(fake_app / info["poster"]) as im:
            assert max(im.size) <= covers.ANIM_MAX_PX


def test_gif_gets_higher_size_cap(fake_app: Path, tmp_path: Path,
                                  monkeypatch):
    """سقفِ حجمیِ GIF جدا است (۱۲۸MB) — PNG همان ۳۲MB می‌ماند (C4-c)."""
    import unittest.mock as _mock
    src = tmp_path / "anim.gif"
    _animated_gif(tmp_path, 2)                  # بسازد و مسیر را برگرداند
    src = tmp_path / "anim.gif"
    data = src.read_bytes()                     # چند کیلوبایت
    big_png = tmp_path / "big.png"
    big_png.write_bytes(_png_bytes(64, 64) + b"\x00" * 4096)
    big_gif = tmp_path / "big.gif"
    big_gif.write_bytes(data + b"\x00" * 4096)
    with _mock.patch.object(covers, "MAX_SOURCE_BYTES", 1024), \
         _mock.patch.object(covers, "MAX_GIF_SOURCE_BYTES", 8192):
        with pytest.raises(CoverReadError):
            import_cover(big_png, fake_app)     # PNG زیرِ سقفِ خودش نیست
        info = import_cover(big_gif, fake_app)  # GIF سقفِ بزرگ‌تر دارد
    assert info["poster"].startswith(".covers/")


# --------------------------------------- C6 — پسماندِ ‎.tmp-*
def test_gc_removes_stale_tmp_files(fake_app: Path, tmp_path: Path):
    """‎.tmp-*‎ قدیمی‌تر از یک ساعت پاک می‌شود؛ تازه‌ها دست‌نخورده می‌مانند (C6)."""
    d = covers_dir(fake_app)
    d.mkdir(parents=True, exist_ok=True)
    stale = d / ".tmp-1-2-x.webp"
    fresh = d / ".tmp-1-2-y.webp"
    stale.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    old = time.time() - 2 * covers.TMP_MAX_AGE_S
    os.utime(stale, (old, old))
    n = collect_garbage(set(), fake_app)
    assert n == 1
    assert not stale.exists() and fresh.is_file()


# --------------------------------- round 17 §۱ — idempotentِ واقعی + قفلِ گذرای ویندوز
def test_reimport_same_source_does_not_rewrite(fake_app: Path, tmp_path: Path):
    """importِ دوباره‌ی همان عکس = بدونِ نوشتن (docstringِ ماژول حالا واقعاً برقرار است).

    قبلاً import_cover هر بار دوباره انکد و os.replace می‌زد؛ وقتی همان GIF
    در صفحه‌ی جزئیات در حالِ پخش است، QMovie فایلِ ‎.anim.webp را باز نگه
    می‌دارد و ویندوز جایگزینیِ فایلِ باز را با PermissionError می‌بندد →
    پیامِ گمراه‌کننده‌ی «فضای دیسک یا دسترسی» (گزارش کاربر — round 17 §۱)."""
    src = _animated_gif(tmp_path, 2)
    info1 = import_cover(src, fake_app)
    anim = fake_app / info1["poster"]
    static = fake_app / info1["poster_static"]
    mtime_anim = anim.stat().st_mtime_ns
    mtime_static = static.stat().st_mtime_ns

    info2 = import_cover(src, fake_app)

    assert info2["animated"] is True and info2["poster"] == info1["poster"]
    assert anim.stat().st_mtime_ns == mtime_anim        # بدونِ انکد/replaceِ دوباره
    assert static.stat().st_mtime_ns == mtime_static


def test_corrupt_dest_is_rebuilt_not_kept(fake_app: Path, tmp_path: Path):
    """مقصدِ ناقص (پسماندِ کرش) باید بازسازی شود — چکِ idempotent فایلِ خراب را معتبر نمی‌گیرد."""
    src = _animated_gif(tmp_path, 2)
    info = import_cover(src, fake_app)
    anim = fake_app / info["poster"]
    anim.write_bytes(b"GIF89a-broken-not-webp" + b"x" * 48)
    info2 = import_cover(src, fake_app)
    assert (fake_app / info2["poster"]).read_bytes()[:4] == b"RIFF"
    with Image.open(fake_app / info2["poster"]) as im:
        assert getattr(im, "is_animated", False) is True


def test_replace_locked_retries_then_succeeds(fake_app: Path, tmp_path: Path,
                                              monkeypatch):
    """قفلِ گذرای os.replace (آنتی‌ویروس/ایندکسر/مسابقه‌ی کارگرها) تلاشِ مجددِ کوتاه می‌گیرد.

    (round 17 §۱) «بعضی وقت‌ها موقع تعویض کاور خطا می‌دهد» روی ویندوز —
    sharing violationِ لحظه‌ای؛ ۵ بارِ محدود در نخِ کارگر، نه دیالوگِ خطا.
    (round 28) هلپرِ تلاشِ مجدد به imgcache منتقل شد و هر دو نویسنده‌ی اتمیک
    از نسخه‌ی ۸تاییِ مشترک استفاده می‌کنند؛ لاگِ retry حالا از imgcache.dbg_log
    می‌گذرد، پس هر دو لاگ‌گیر اینجا جمع می‌شوند."""
    src = tmp_path / "poster.png"
    src.write_bytes(_png_bytes(64, 64))
    real_replace = os.replace
    calls = {"n": 0}
    logs: list[str] = []
    monkeypatch.setattr(covers, "dbg_log", logs.append)
    # (round 28) لاگِ حلقه‌ی retry از imgcache می‌گذرد — همان لیست را پُر می‌کند
    monkeypatch.setattr(imgcache, "dbg_log", logs.append)

    def flaky(a, b, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "locked by antivirus")
        return real_replace(a, b, **k)

    monkeypatch.setattr(os, "replace", flaky)
    info = import_cover(src, fake_app)
    assert calls["n"] == 3
    assert (fake_app / info["poster"]).is_file()
    assert any("replace-retry" in m for m in logs)      # علت در لاگِ بی‌نام می‌آید


def test_replace_locked_forever_is_write_error_and_keeps_old_cover(
        fake_app: Path, tmp_path: Path, monkeypatch):
    """قفلِ دائمی → CoverWriteError؛ فایلِ قبلی سالم می‌ماند و tmp پسماند نمی‌ماند.

    (round 17 §۱) سناریویِ کاربر: همان GIF دوباره انتخاب شده و متحرکش خراب
    شده است؛ replace روی قفل گیر می‌کند — staticِ سابق باید دست‌نخورده
    بماند (atomicity) و پیامِ درستِ «ذخیره ممکن نشد» بیاید، نه crash."""
    src = _animated_gif(tmp_path, 2)
    info = import_cover(src, fake_app)
    anim = fake_app / info["poster"]
    static = fake_app / info["poster_static"]
    anim.write_bytes(b"GIF89a-broken" + b"x" * 48)      # فقط متحرک خراب شود
    static_before = static.read_bytes()

    def always_locked(a, b, **k):
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(os, "replace", always_locked)
    with pytest.raises(CoverWriteError):
        import_cover(src, fake_app)
    assert static.read_bytes() == static_before          # کاورِ قبلی سالم ماند
    assert anim.read_bytes() == b"GIF89a-broken" + b"x" * 48   # مقصد هم خراب‌تر نشد
    assert not list(covers_dir(fake_app).glob(".tmp-*"))


def test_failed_anim_import_cleans_only_own_static(fake_app: Path,
                                                   tmp_path: Path,
                                                   monkeypatch):
    """شکستِ مرحله‌ی متحرک: فقط staticِ *تازه‌ساختِ همین تلاش* پاک می‌شود (round 17 §۱).

    قبلاً static_path.unlink بدونِ چکِ مالکیت بود — اگر staticِ همان GIF از
    importِ قبلی وجود داشت (متای قبلی/عنوانِ دیگر به آن ارجاع دارد)، پاک
    می‌شد و کاورِ ساکنِ سالم از بین می‌رفت."""
    src = _animated_gif(tmp_path, 2)

    def boom(*a, **k):
        raise CoverWriteError(covers.T(covers.WRITE_ERR_TEXT))

    monkeypatch.setattr(covers, "_encode_animated", boom)
    with pytest.raises(CoverError):
        import_cover(src, fake_app)
    assert not list(covers_dir(fake_app).glob("*.webp"))    # ساختِ خودش بود → پاک شد


def test_failed_anim_import_keeps_preexisting_static(fake_app: Path,
                                                     tmp_path: Path,
                                                     monkeypatch):
    """staticِ از-قبل-موجود در شکستِ مرحله‌ی متحرک دست‌نخورده می‌ماند (round 17 §۱)."""
    src = _animated_gif(tmp_path, 2)
    info1 = import_cover(src, fake_app)                     # importِ سالمِ اول
    (fake_app / info1["poster"]).unlink()                   # فقط متحرک حذف شود
    static = fake_app / info1["poster_static"]
    static_before = static.read_bytes()

    def boom(*a, **k):
        raise CoverWriteError(covers.T(covers.WRITE_ERR_TEXT))

    monkeypatch.setattr(covers, "_encode_animated", boom)
    with pytest.raises(CoverError):
        import_cover(src, fake_app)
    assert static.read_bytes() == static_before             # مالِ تلاشِ قبلی است — ماند
