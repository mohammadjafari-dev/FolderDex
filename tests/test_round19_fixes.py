# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""رگرسیون‌های Round 19 — یکی‌به‌یکی برای مواردِ بریفِ یکپارچه.

هر تست به یک § از ROUND19-consolidated-remaining-issues.fa.md وصل است:
  §۱  player.path اجرایی از پشتیبان  → test_sanitize_imported_config_neutralizes_player
  §۲  قفلِ Store در mutatorها        → test_store_mutators_thread_safe
  §۳  GC با snapshot کهنه            → test_gc_keeps_cover_relinked_after_snapshot
  §۴  QMovie دو‌باره‌سازی             → test_movie_replace_stops_previous /
                                        test_show_event_does_not_start_second_movie
  §۵  کاور در دیالوگِ ویرایش + Cancel → test_edit_dialog_cover_touched_signal /
                                        test_details_edit_dialog_wires_cover_touch
  §۶  اعمالِ ناتمامِ پشتیبان          → test_apply_skips_already_applied_steps /
                                        test_apply_quarantines_after_retry_budget
  §۷  سقفِ منابعِ zip                → test_zip_limits_*
  §۸  WebPِ خراب با سرآیندِ سالم      → test_corrupt_webp_payload_rebuilt_on_reimport
  §۹  clear_all_manifests و or       → test_clear_all_manifests_pops_both_keys
  §۱۰ UNC و _winfix                 → test_winfix_unc_gets_unc_prefix
  §۱۱ تایتلِ خام در لاگ              → test_pot_window_title_never_plaintext
  §۱۲ کلیدِ کانونی در merge          → test_merge_backup_canonicalizes_keys
  §۱۳ مهاجرتِ کلیدهای خام            → test_legacy_raw_keys_canonicalized_on_load
  §۱۴ دقتِ نانوثانیهٔ مانیفست        → test_manifest_distinguishes_same_second
  §۱۵ era وابسته به px              → test_manifest_era_includes_cache_px
  §۱۶ junction گارد                 → test_is_junction_guard_platform_semantics
                                        + round 23: test_is_junction_py311_* /
                                        test_gather_stats_prunes_junction_* /
                                        test_is_junction_real_* (فقط ویندوز)
  §۲۰ مسیرِ مؤثرِ کش در backup       → test_backup_uses_effective_cache_dir
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT  # noqa: F401  (sys.path already set)

import backup
import covers
import prebuild
import scanner
from store import Store, _meta_key


# ---------------------------------------------------------------- §۱ security
def test_sanitize_imported_config_neutralizes_player(tmp_path):
    """player.path مسیرِ اجرایی است — restore نباید فایلِ اجراییِ غریبه بکارد."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "keep": "yes",
        "cat_openers": {"گیم": [{"exts": ["zip"], "app": "C:/evil/run.exe"}]},
        "player": {"mode": "custom", "path": "C:/evil/player.exe"},
    }, ensure_ascii=False), encoding="utf-8")
    backup._SKIPPED_IMPORTS.clear()
    assert backup._sanitize_imported_config(cfg_path) is True
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "cat_openers" not in cfg
    assert cfg["player"] == {"mode": "auto", "path": ""}
    assert cfg["keep"] == "yes"
    assert "player" in backup.skipped_imports()
    backup._SKIPPED_IMPORTS.clear()


def test_sanitize_imported_config_keeps_clean_config_intact(tmp_path):
    cfg_path = tmp_path / "config.json"
    before = '{"theme": "paper"}'
    cfg_path.write_text(before, encoding="utf-8")
    assert backup._sanitize_imported_config(cfg_path) is True
    assert cfg_path.read_text(encoding="utf-8") == before   # دست نخورده


def test_sanitize_imported_config_fail_closed_on_garbage(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{not json", encoding="utf-8")
    assert backup._sanitize_imported_config(cfg_path) is False


# ---------------------------------------------------------------- §۲ locking
def test_store_mutators_thread_safe(tmp_path):
    """هم‌زمانیِ watch/thumb/meta/categories — هیچ به‌روزرسانی‌ای گم نمی‌شود."""
    st = Store(tmp_path / "config.json")
    n_items, n_threads, rounds = 12, 8, 40
    paths = [str(tmp_path / f"item{i}") for i in range(n_items)]
    expected_watch: dict[str, set] = {p: set() for p in paths}
    errors: list[BaseException] = []

    def worker(t: int):
        try:
            for r in range(rounds):
                p = paths[(t + r) % n_items]
                rel = f"f{r}.mp4"
                st.watch_set(SimpleNamespace(path=p), rel, 1, float(r))
                expected_watch[p].add(rel)
                st.set_thumb_states({p: {"m": {"a.mp4": [1, 2]},
                                         "r": 0, "e": "t2-256"}})
                st.set_meta(p, rating=r % 6)
                st.categories()            # خوانندهٔ پیمایش‌گر — بدون RuntimeError
        except BaseException as e:         # noqa: BLE001 — جمع برای assert
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not errors
    for p in paths:
        m = st.meta(p)
        assert m.get("thumbs", {}).get("m") == {"a.mp4": [1, 2]}
        got = set((m.get("watch") or {}).keys())
        assert expected_watch[p] <= got    # هیچ relی نوشته‌شده گم نشده
    st.close()


def test_clear_all_manifests_pops_both_keys(tmp_path):
    """(§۹) orِ کوتاه‌شده total_dur را جا می‌گذاشت — حالا هر دو مستقل pop می‌شوند."""
    st = Store(tmp_path / "config.json")
    p = str(tmp_path / "it1")
    st.set_meta(p, vids={"a.mp4": [1, 2]}, total_dur=99)
    st.clear_all_manifests()
    m = st.meta(p)
    assert "vids" not in m and "total_dur" not in m
    st.set_meta(p, total_dur=5)            # فقط total_dur — حالتِ قبلاً جاافتاده
    st.clear_all_manifests()
    assert "total_dur" not in st.meta(p)
    st.close()


# ---------------------------------------------------------------- §۳ GC race
def test_gc_keeps_cover_relinked_after_snapshot(tmp_path):
    """refsِ snapshot کهنه + اتصالِ مجدد پیش از unlink → فایل باید بماند."""
    cdir = tmp_path / ".covers"
    cdir.mkdir()
    f = cdir / "abc123.webp"
    f.write_bytes(b"x" * 64)
    old = time.time() - 3600               # از گاردِ ۶۰ثانیه‌ای رد شود
    os.utime(f, (old, old))

    class FakeStore:
        def __init__(self):
            self._lock = threading.RLock()
            self.refs = set()

        def cover_refs(self):
            with self._lock:
                return set(self.refs)

    st = FakeStore()
    st.refs = {".covers/abc123.webp"}      # همین حالا به متا وصل شده
    # snapshotِ پاس‌داده‌شده (مثل main در لحظهٔ شروعِ نخ) فایل را ندارد
    removed = covers.collect_garbage(set(), app_dir_path=tmp_path, store=st)
    assert removed == 0
    assert f.exists()
    # بدونِ store (رفتارِ قدیمی) همان فراخوانی فایل را پاک می‌کرد
    removed_old = covers.collect_garbage(set(), app_dir_path=tmp_path)
    assert removed_old == 1


def test_gc_still_removes_true_orphans(tmp_path):
    """رفتارِ عادی: یتیمِ واقعیِ قدیمی حذف می‌شود (رگرسیونِ منفی)."""
    cdir = tmp_path / ".covers"
    cdir.mkdir()
    f = cdir / "orphan.webp"
    f.write_bytes(b"y" * 64)
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert covers.collect_garbage(set(), app_dir_path=tmp_path) == 1
    assert not f.exists()


# ---------------------------------------------------------------- §۴/§۵ UI
pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal                       # noqa: E402
from PySide6.QtGui import QMovie                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from PIL import Image                                            # noqa: E402
from thumbs import ThumbnailCache                                # noqa: E402
from ui.details import DetailsPage                               # noqa: E402
from ui.dialogs import ItemEditDialog                            # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _gif(path: Path, w: int = 320, h: int = 180) -> Path:
    frs = [Image.new("RGB", (w, h), (c * 50 % 256, 120, 90)) for c in range(2)]
    frs[0].save(path, save_all=True, append_images=frs[1:], duration=60, loop=0)
    return path


def _static(path: Path) -> Path:
    Image.new("RGB", (320, 180), (9, 9, 9)).save(path, "PNG")
    return path


@pytest.fixture()
def page(qapp, tmp_path: Path):
    store = Store(tmp_path / "config.json")
    pg = DetailsPage(store, ThumbnailCache(tmp_path / ".thumb_cache"))
    yield pg
    pg.deleteLater()
    qapp.processEvents()
    store.close()


def test_movie_replace_stops_previous(page, qapp, tmp_path: Path):
    """(§۴) جایگزینی فیلم: نمونهٔ قبلی stop — هیچ نمونهٔ رهاشده‌ای نمی‌ماند."""
    src = _gif(tmp_path / "a.gif")
    page._set_poster_media(src, _static(tmp_path / "a_f0.png"))
    qapp.processEvents()
    m1 = page._movie
    assert m1 is not None
    page._set_poster_media(src, _static(tmp_path / "a_f0.png"))   # جایگزینی
    qapp.processEvents()
    m2 = page._movie
    assert m2 is not None and m2 is not m1
    assert m2.state() == QMovie.MovieState.Running
    assert m1.state() != QMovie.MovieState.Running     # قدیمی متوقف شد
    page._stop_poster_movie()
    qapp.processEvents()


def test_show_event_does_not_start_second_movie(page, qapp, tmp_path: Path):
    """ورودِ اولیه: set_item (پنهان) فیلم را می‌سازد؛ showEvent نمونهٔ دوم نمی‌سازد."""
    src = _gif(tmp_path / "b.gif")
    st = _static(tmp_path / "b_f0.png")
    page._set_poster_media(src, st)        # صفحه هنوز پنهان است (سناریوی show_details)
    m1 = page._movie
    assert m1 is not None
    page.show()                            # showEvent واقعی
    qapp.processEvents()
    assert page._movie is m1               # بدونِ نمونهٔ دوم
    assert m1.state() == QMovie.MovieState.Running
    page.hide()
    qapp.processEvents()
    assert page._movie is None             # hideEvent متوقف کرده


def test_edit_dialog_cover_touched_signal(qapp, tmp_path: Path):
    """(§۵) coverTouched فقط برای تغییرِ واقعی emit می‌شود (Save/Cancel بی‌تفاوت)."""
    store = Store(tmp_path / "config.json")
    item = SimpleNamespace(path=tmp_path / "x", name="x", category="")
    dlg = ItemEditDialog(item, store)
    hits: list = []
    dlg.coverTouched.connect(hits.append)
    dlg._cover_changed(True)
    assert len(hits) == 1 and hits[0] is item
    dlg._cover_changed(False)              # عملِ ناموفق خبر نمی‌دهد
    assert len(hits) == 1
    dlg.deleteLater()
    store.close()


def test_details_edit_dialog_wires_cover_touch(qapp, tmp_path, monkeypatch):
    """(§۵) _edit_dialog سیگنال را به مسیرِ C1 وصل می‌کند — Cancel هم تازه می‌کند."""
    store = Store(tmp_path / "config.json")
    pg = DetailsPage(store, ThumbnailCache(tmp_path / ".thumb_cache"))

    class StubDlg(QObject):
        coverTouched = Signal(object)

        def __init__(self, item, store_, parent=None):
            super().__init__(parent)
            self.item = item
            self.store = store_

        def exec(self):                    # Cancel
            return 0

    created: list = []

    def factory(item, store_, parent=None):
        d = StubDlg(item, store_, parent)
        created.append(d)
        return d

    monkeypatch.setattr("ui.details.ItemEditDialog", factory)
    pg.item = SimpleNamespace(path=tmp_path / "z")
    rebuilt: list = []
    monkeypatch.setattr(pg, "set_item", lambda it: rebuilt.append(it))
    monkeypatch.setattr(pg, "_view_state_snapshot", lambda: {})
    monkeypatch.setattr(pg, "_restore_view_state", lambda snap: None)
    events: list = []
    pg.coverChanged.connect(events.append)
    pg._edit_dialog()
    assert created, "dialog built"
    created[0].coverTouched.emit(created[0].item)   # مثل pick_cover در دیالوگ
    assert rebuilt == [pg.item]            # بدونِ accept هم rebuild شد
    assert events == [pg.item]             # کتابخانه هم خبردار شد
    pg.deleteLater()
    qapp.processEvents()
    store.close()


# ---------------------------------------------------------------- §۶/§۷ backup
@pytest.fixture()
def fake_app(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(backup, "app_dir", lambda: app)
    return app


def _mk_flag(app: Path, applied=(), attempts: int = 0,
             include=("config.json",)) -> Path:
    pending = app / ".mc_import_pending"
    pending.mkdir(parents=True, exist_ok=True)
    for n in include:
        # config.json باید JSONِ معتبر باشد —消毒ِ fail-closed زباله را رد می‌کند (درست است)
        (pending / n).write_text(
            json.dumps({"imported": n}), encoding="utf-8")
    doc = {"pending": str(pending), "files": len(include),
           "include_cache": False, "applied": list(applied),
           "attempts": attempts}
    (app / "._mc_import").write_text(json.dumps(doc), encoding="utf-8")
    return pending


def test_apply_skips_already_applied_steps(fake_app, monkeypatch):
    """(§۶) مرحلهٔ ثبت‌شده در applied حتی اگر src هنوز هست دوباره نوشته نمی‌شود."""
    calls: list[str] = []
    monkeypatch.setattr(backup, "paths_replace",
                        lambda src, dst: calls.append(Path(dst).name))
    _mk_flag(fake_app, applied=["config.json"])
    got = backup.apply_pending_import()
    assert calls == []                     # هیچ بازنویسیِ دوباره
    assert got == ["config.json"]


def test_apply_quarantines_after_retry_budget(fake_app, monkeypatch):
    """(§۶) بعد از سقفِ تلاش: قرنطینه + پرچمِ بسته + اطلاعِ صریحِ بازیابیِ ناقص."""

    def boom(src, dst):
        raise OSError("locked")

    monkeypatch.setattr(backup, "paths_replace", boom)
    _mk_flag(fake_app, applied=[], attempts=1)
    backup._SKIPPED_IMPORTS.clear()
    got = backup.apply_pending_import()
    assert got == []
        # پرچم بسته شد
    assert not (fake_app / "._mc_import").exists()
        # آماده‌سازی قرنطینه/پاک شد
    leftovers = list(fake_app.glob(".mc_import_bad-*"))
    assert leftovers or not (fake_app / ".mc_import_pending").exists()
    assert any(("بازیابی" in s) or ("restore" in s.lower())
               for s in backup.skipped_imports())


def test_apply_happy_path_still_cleans_up(fake_app):
    """رفتارِ عادی پس از بازنویسیِ apply — پاک‌سازیِ کامل (رگرسیونِ منفی)."""
    _mk_flag(fake_app, applied=[], attempts=0)
    got = backup.apply_pending_import()
    assert got == ["config.json"]
    assert (fake_app / "config.json").is_file()
    assert not (fake_app / "._mc_import").exists()
    assert not (fake_app / ".mc_import_pending").exists()


def _raw_zip_entry(path: Path, *, flags: int = 0, method: int = 0,
                   usize: int = 200_000_000, csize: int = 100,
                   payload_len: int = 100, name: bytes = b"bomb.bin") -> None:
    """zip خام با سرآیندِ دست‌ساز — برای سناریوهایی که zipfile نمی‌تواند بنویسد."""
    import struct
    payload = b"0" * payload_len
    # ترتیبِ واقعیِ سرآیندِ محلی: crc، csize، usize — نه برعکس
    lf = struct.pack("<4s5H3I2H", b"PK\x03\x04", 20, flags, method, 0, 0,
                     0, csize, usize, len(name), 0) + name
    cd = struct.pack("<4s6H3I5H2I", b"PK\x01\x02", 20, 20, flags, method, 0, 0,
                     0, csize, usize, len(name), 0, 0, 0,
                     0, 0, len(lf)) + name
    eocd = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1,
                       len(cd), len(lf) + len(payload) + len(cd), 0)
    path.write_bytes(lf + payload + cd + eocd)


def _raw_bomb_zip(path: Path) -> None:
    """zip خام با سرآیندِ جعلی: usize=۲۰۰MB، csize=۱۰۰B — نسبتِ ~۲٬۰۰۰٬۰۰۰."""
    _raw_zip_entry(path)


def test_zip_limits_reject_bombs_and_junk(tmp_path, monkeypatch):
    """(§۷) تعداد/تکراری/رمز/عمق/نسبت — قبل از هر استخراجی رد می‌شود."""
    ok = tmp_path / "ok.mcbak"
    with zipfile.ZipFile(ok, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr("data.db", b"x")
    assert backup.validate_backup(ok)[0] is True

    # member count
    many = tmp_path / "many.mcbak"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(5):
            zf.writestr(f"config{i}.json", "{}")
    monkeypatch.setattr(backup, "ZIP_MAX_MEMBERS", 2)
    okk, msg = backup.validate_backup(many)
    assert not okk and "too-many-members" in msg
    monkeypatch.setattr(backup, "ZIP_MAX_MEMBERS", 200_000)

    # duplicate names — عضوِ تکراری *عمدی* است؛ هشدارِ خودِ zipfile
    # (Duplicate name) نویزِ شناخته‌شدهٔ همین ساختِ عمدی است و فقط همین‌جا
    # بی‌صدا می‌شود، نه سراسری (بهداشتِ هشدار — round 20)
    dup = tmp_path / "dup.mcbak"
    with zipfile.ZipFile(dup, "w") as zf:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Duplicate name:",
                                    category=UserWarning)
            zf.writestr("config.json", "{}")
            zf.writestr("config.json", "{}")
    okk, msg = backup.validate_backup(dup)
    assert not okk and "duplicate-member" in msg

    # encrypted flag — سرآیندِ خام (zipfile خودش بیتِ رمز را برای writestr پاک می‌کند)
    enc = tmp_path / "enc.mcbak"
    _raw_zip_entry(enc, flags=0x1, usize=16, csize=16, payload_len=16,
                   name=b"config.json")
    okk, msg = backup.validate_backup(enc)
    assert not okk and "encrypted" in msg

    # path depth
    deep = tmp_path / "deep.mcbak"
    with zipfile.ZipFile(deep, "w") as zf:
        zf.writestr("/".join(f"d{i}" for i in range(20)) + "/f.bin", "x")
    okk, msg = backup.validate_backup(deep)
    assert not okk and "path-too-deep" in msg

    # forged compression ratio
    bomb = tmp_path / "bomb.mcbak"
    _raw_bomb_zip(bomb)
    assert zipfile.is_zipfile(bomb)
    okk, msg = backup.validate_backup(bomb)
    assert not okk and "compression-ratio" in msg


# ---------------------------------------------------------------- §۸ webp
def test_corrupt_webp_payload_rebuilt_on_reimport(tmp_path, monkeypatch):
    """(§۸) payloadِ خراب با سرآیندِ RIFF/WEBPِ سالم تعمیر می‌شود، نه قبولِ دوباره."""
    monkeypatch.setattr(covers, "app_dir", lambda: tmp_path)
    src = tmp_path / "s.png"
    Image.new("RGB", (40, 40), (1, 2, 3)).save(src)
    info1 = covers.import_cover(src, tmp_path)
    dest = tmp_path / info1["poster"]
    assert covers._looks_like_webp(dest) is True
    raw = bytearray(dest.read_bytes())
    raw[12:16] = b"XXXX"                   # fourccِ chunk — سرآیندِ کلی سالم می‌ماند
    dest.write_bytes(bytes(raw))
    assert covers._looks_like_webp(dest) is False     # دیکدِ واقعی رد می‌کند
    info2 = covers.import_cover(src, tmp_path)
    dest2 = tmp_path / info2["poster"]
    assert dest2.read_bytes() != bytes(raw)
    with Image.open(dest2) as im:
        im.load()                          # فایلِ تازه واقعاً دیکد می‌شود


# ---------------------------------------------------------------- §۱۰/§۱۶ scanner
def test_winfix_unc_gets_unc_prefix(monkeypatch):
    r"""(§10) UNC بلند → \\?\UNC\server\share؛ درایو → \\?\C:\؛ کوتاه دست‌نخورده."""
    monkeypatch.setattr(sys, "platform", "win32")
    unc = "\\\\" + "NAS\\share" + "\\a" * 240
    fixed = scanner._winfix(unc)
    assert fixed.startswith("\\\\?\\UNC\\NAS\\share\\")
    drive = "C:\\" + "a" * 240
    assert scanner._winfix(drive).startswith("\\\\?\\C:\\")
    assert scanner._winfix("C:\\short") == "C:\\short"
    already = "\\\\?\\C:\\long" + "b" * 240
    assert scanner._winfix(already) == already


def test_winfix_is_noop_on_posix(monkeypatch):
    """(§10) قراردادِ میزبانِ غیرویندوز: _winfix هیچ مسیری را دست نمی‌زند.

    روی ویندوزِ واقعی sys.platform خودش «win32» است و تابع *باید* تبدیل کند
    (test_winfix_unc_gets_unc_prefix همان را می‌سنجد). میزبان این‌جا آگاهانه
    غیرویندوز شبیه‌سازی می‌شود تا قراردادِ no-op روی هر سه OSِ ماتریسِ CI
    آزمایش‌پذیر بماند — نسخهٔ قبلیِ بدونِ شبیه‌سازی روی ویندوزِ واقعی قرمز
    می‌شد (تنِ تست را می‌شکست، نه کد را)."""
    monkeypatch.setattr(sys, "platform", "linux")
    long_unc = "\\\\" + "NAS\\share" + "\\a" * 240
    assert scanner._winfix(long_unc) == long_unc
    assert scanner._winfix("C:\\short") == "C:\\short"


def test_is_junction_guard_platform_semantics(monkeypatch):
    """(§۱۶) قراردادِ پلتفرمی: میزبانِ غیرویندوز False؛ ویندوز + isjunction
    موجود → همان تصمیمِ API.

    میزبان آگاهانه شبیه‌سازی می‌شود (round 23) — نسخهٔ قبلی برای شاخهٔ
    «غیرویندوز» به sys.platform واقعیِ میزبان تکیه می‌کرد و روی ویندوزِ
    واقعی اولین assert به جای قراردادِ POSIX، رفتارِ ویندوزی را می‌سنجید
    (همان تنِ تستی که راندِ ۲۱ در _winfix هم درست شد)."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert scanner._is_junction("C:/x") is False
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os.path, "isjunction", lambda p: True,
                        raising=False)
    assert scanner._is_junction("anything") is True
    monkeypatch.setattr(os.path, "isjunction", lambda p: False,
                        raising=False)
    assert scanner._is_junction("anything") is False


# ------------------------------------------------- §۱۶ / round 23: پایتون ۳٫۱۱
# os.path.isjunction فقط از پایتون ۳٫۱۲ هست؛ روی ویندوزِ ۳٫۱۱ getattr مقدار
# None می‌داد و فراخوانی‌اش TypeError می‌شد — gather_stats و هر اسکنِ کتابخانه
# می‌مرد. فال‌بکِ lstat باید بدونِ crash تصمیمِ درست بدهد و هر خطایی را به
# False تنزل دهد. (شبیه‌سازیِ ۳٫۱۱: حذفِ isjunction از os.path — میزبانِ
# لینوکسیِ CI هم همین حالت را دارد، پس تست‌ها همه‌جا قابلِ اجرا هستند.)

_REPARSE = 0x400                 # FILE_ATTRIBUTE_REPARSE_POINT
_TAG_JUNCTION = 0xA0000003       # IO_REPARSE_TAG_MOUNT_POINT
_TAG_SYMLINK = 0xA000000C        # IO_REPARSE_TAG_SYMLINK — junction نیست


def _fake_stat(attrs=0, tag=0):
    """stat_result ویندوزیِ جعلی با فقط دو اتریبیوتِ موردِ استفاده."""
    return SimpleNamespace(st_file_attributes=attrs, st_reparse_tag=tag)


def test_is_junction_py311_fallback_decides_by_lstat(monkeypatch):
    """round 23: بدونِ isjunction (پایتون ۳٫۱۱)، تشخیص از روی lstat:
    junction → True، symlink → False، پوشهٔ معمولی → False."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os.path, "isjunction", raising=False)
    monkeypatch.setattr(os, "lstat", lambda p: _fake_stat(_REPARSE, _TAG_JUNCTION))
    assert scanner._is_junction("C:/lib") is True
    monkeypatch.setattr(os, "lstat", lambda p: _fake_stat(_REPARSE, _TAG_SYMLINK))
    assert scanner._is_junction("C:/lib") is False      # symlink، نه junction
    monkeypatch.setattr(os, "lstat", lambda p: _fake_stat(0x10, 0))
    assert scanner._is_junction("C:/lib") is False      # پوشهٔ معمولی


def test_is_junction_py311_fallback_errors_degrade_to_false(monkeypatch):
    """round 23: خطای lstat (OSError) و نبودنِ اتریبیوت‌ها (AttributeError)
    هرگز بیرون نمی‌زنند — False؛ گارد هیچ‌وقت نباید اسکن را بکشد."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os.path, "isjunction", raising=False)

    def boom(p):
        raise OSError("access denied")

    monkeypatch.setattr(os, "lstat", boom)
    assert scanner._is_junction("C:/locked") is False
    monkeypatch.setattr(os, "lstat", lambda p: SimpleNamespace())
    assert scanner._is_junction("C:/weird") is False


def test_is_junction_broken_api_degrades_to_false(monkeypatch):
    """round 23: حتی if isjunction موجودِ هم خراب شده باشد (TypeError)،
    گارد False برمی‌گرداند — except قدیمی فقط OSError/ValueError را
    می‌گرفت و همین TypeError در ویندوزِ ۳٫۱۱ اسکن را کشت."""
    monkeypatch.setattr(sys, "platform", "win32")

    def broken(p):
        raise TypeError("bool_dealloc")

    monkeypatch.setattr(os.path, "isjunction", broken, raising=False)
    assert scanner._is_junction("C:/x") is False


def test_gather_stats_prunes_junction_without_isjunction_api(tmp_path, monkeypatch):
    """round 23: شبیه‌سازیِ کاملِ ویندوزِ ۳٫۱۱ در gather_stats — پوشهٔ
    junction-نما هرزِ پیمایش می‌شود و فایلِ داخلش شمرده نمی‌شود."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os.path, "isjunction", raising=False)
    real_lstat = os.lstat

    def fake_lstat(p):
        if str(p).endswith("junc"):
            return _fake_stat(_REPARSE, _TAG_JUNCTION)
        return real_lstat(p)

    monkeypatch.setattr(os, "lstat", fake_lstat)
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "junc").mkdir()
    (tmp_path / "junc" / "hidden.txt").write_text("y", encoding="utf-8")
    files, videos, size, types = scanner.gather_stats(tmp_path)
    assert files == 1
    assert types["doc"] == 1


@pytest.mark.skipif(sys.platform != "win32",
                    reason="فقط ویندوز: junction واقعی با mklink /J — «تأییدِ ویندوزی» بریف؛ روی اوبونتوی CI skip")
def test_is_junction_real_junction_vs_symlink(tmp_path):
    """(فقط ویندوز) junction واقعی → True؛ پوشهٔ معمولی → False؛
    symlinkِ دایرکتوری → False. روی ۳٫۱۱ مسیرِ فال‌بکِ lstat را می‌سنجد،
    روی ۳٫۱۲+ مسیرِ os.path.isjunction را."""
    real = tmp_path / "target"
    real.mkdir()
    junc = tmp_path / "junc"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junc), str(real)],
                   check=True, capture_output=True)
    assert scanner._is_junction(junc) is True
    assert scanner._is_junction(real) is False
    try:
        slink = tmp_path / "slink"
        os.symlink(real, slink, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("ساخت symlink به امتیازِ مدیر/Developer Mode نیاز دارد")
    assert scanner._is_junction(slink) is False


@pytest.mark.skipif(sys.platform != "win32",
                    reason="فقط ویندوز: junction واقعی با mklink /J")
def test_gather_stats_real_junction_counted_once(tmp_path):
    """(فقط ویندوز) فایلِ داخلِ هدفِ junction نباید دوبار شمرده شود — پیمایش
    واردِ junction نمی‌شود؛ بدونِ گارد در ویندوزِ ۳٫۱۱ walk واقعاً واردش
    می‌شد (islink آن را نمی‌گیرد) و شمارش دوبار می‌شد."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "junc"), str(real)],
                   check=True, capture_output=True)
    files, videos, size, types = scanner.gather_stats(tmp_path)
    assert files == 1
    assert types["doc"] == 1


@pytest.mark.skipif(sys.platform != "win32",
                    reason="فقط ویندوز: چرخهٔ junction واقعی — walk باید تمام شود")
def test_gather_stats_junction_cycle_terminates(tmp_path):
    """(فقط ویندوز) junction به خودِ ریشه — پیمایش باید تمام شود نه بی‌نهایت.

    در زیرفرایند با timeout اجرا می‌شود تا اگر گاردی روزی شکست، سوئیت هنگ
    نکند. returncode عمداً نادیده گرفته می‌شود و sentinel از stdout می‌آید:
    همان crashِ خاموشیِ shiboken6/۳٫۱۱ (راندِ ۲۲) می‌تواند returncode را
    خراب کند در حالی که کارِ پیمایش سالم انجام شده است."""
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "loop"), str(tmp_path)],
                   check=True, capture_output=True)
    repo = Path(__file__).resolve().parent.parent
    code = ("import sys; sys.path.insert(0, r'{repo}'); import scanner; "
            "scanner.gather_stats(r'{root}'); print('CYCLE-OK')").format(
        repo=repo, root=tmp_path)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=120)
    assert "CYCLE-OK" in r.stdout


# ---------------------------------------------------------------- §۱۱ privacy
def test_pot_window_title_never_plaintext(tmp_path):
    """(§۱۱) تایتلِ پنجرهٔ پلیر هرگز خام لاگ نمی‌شود — فقط هش."""
    from watchtime import _dbg_pot_window, set_debug_log
    log = tmp_path / "dbg.log"
    set_debug_log(log)
    try:
        _dbg_pot_window(4242, "Series.S01E04.PrivateTitle.mkv")
    finally:
        set_debug_log(None)
    text = log.read_text(encoding="utf-8")
    assert "PrivateTitle" not in text
    assert "title=#" in text
    assert "pid=4242" in text             # pid غیرحساس می‌ماند


# ---------------------------------------------------------------- §۱۲ merge
def _db_with_items(path: Path, rows: list[tuple[str, dict]]) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE items (path TEXT PRIMARY KEY, meta TEXT NOT NULL)")
    con.executemany("INSERT INTO items (path, meta) VALUES (?, ?)",
                    [(p, json.dumps(m)) for p, m in rows])
    con.commit()
    con.close()


def _mk_backup_zip(path: Path, rows: list[tuple[str, dict]]) -> None:
    tmp = path.with_suffix(".tmpdir")
    tmp.mkdir()
    _db_with_items(tmp / "data.db", rows)
    (tmp / "config.json").write_text("{}", encoding="utf-8")
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(tmp / "data.db", "data.db")
        zf.write(tmp / "config.json", "config.json")


def test_merge_backup_canonicalizes_keys(tmp_path):
    """(§۱۲) ردیفِ «C:/x/B/» پشتیبان با «C:/x/B» فعلی یکی است — تکراری ساخته نمی‌شود."""
    st = Store(tmp_path / "config.json")
    st.data["items"][_meta_key("C:/x/B")] = {"title": "old"}
    z = tmp_path / "b.mcbak"
    _mk_backup_zip(z, [("C:/x/B/", {"title": "from-backup"}),
                       ("C:/x/B", {"title": "dup-raw"})])
    res = backup.merge_backup(z, st)
    assert res["items_added"] == 0
    assert res["items_existing"] == 2     # هر دو ردیف به کلیدِ کانونی خوردند
    keys = [k for k in st.data["items"] if k.replace("\\", "/").endswith("x/B")]
    assert len(keys) == 1                 # یک ردیف، نه دو
    assert st.data["items"][keys[0]]["title"] == "old"   # فعلی برنده است
    st.close()


# ---------------------------------------------------------------- §۱۳ migration
def test_legacy_raw_keys_canonicalized_on_load(tmp_path):
    """(§۱۳) کلیدِ خام «a//b» / «c/d/» به کانونی می‌رود؛ برخورد = ادغامِ بدونِ له‌کردن.

    فرمِ کانونی روی هر میزبان همان `_meta_key` است — و `_meta_key` از
    round 12 عمداً «str(Path(key))» است: روی ویندوز جداکننده‌ها بک‌اسلش
    می‌شوند (تا رشته‌ی خام و Path به یک کلید برسند). پس انتظارها از خودِ
    _meta_key ساخته می‌شوند تا تست روی هر سه OSِ ماتریسِ CI معنای واحد
    داشته باشد — نسخهٔ قبلی «a/b» را هاردکد کرده بود و فقط روی لینوکس
    پاس می‌شد (تنِ تست را می‌شکست، نه کد را)."""
    db = tmp_path / "data.db"
    _db_with_items(db, [
        ("a//b", {"title": "A"}),
        ("c/d/", {"title": "C", "rating": 3}),
        ("dup//x", {"title": "raw", "extra": 1}),
        ("dup/x", {"title": "keep"}),
    ])
    st = Store(tmp_path / "config.json")
    items = st.data["items"]
    canon_ab = _meta_key("a/b")      # لینوکس: «a/b» — ویندوز: «a\b»
    canon_cd = _meta_key("c/d")
    canon_dup = _meta_key("dup/x")
    # هر دو شکلِ «خام» و «تمیز» به یک کلیدِ کانونی می‌رسند
    assert _meta_key("a//b") == canon_ab and _meta_key("c/d/") == canon_cd
    assert "a//b" not in items and "c/d/" not in items
    assert items[canon_ab]["title"] == "A"
    assert items[canon_cd] == {"title": "C", "rating": 3}
    # هر دو ردیفِ «dup» به یک کلید رسیدند — یک ردیف، ادغام، نه دوبله‌سازی
    dup_keys = [k for k in items if k.replace("\\", "/") == "dup/x"]
    assert dup_keys == [canon_dup]
    merged = items[canon_dup]
    # برندهٔ برخورد: اگر «dup/x» از قبل کانونی بود (لینوکس) مقدارِ موجود
    # برنده است؛ اگر هر دو خام بودند (ویندوز) اولی در حالتِ درج می‌نشیند
    # و دومی با setdefault ادغام می‌شود — در هر دو حالت هیچ مقداری له نمی‌شود
    clean_was_canonical = _meta_key("dup/x") == "dup/x"
    assert merged["title"] == ("keep" if clean_was_canonical else "raw")
    assert merged["extra"] == 1          # کلیدِ فقط-در-خام پر شد
    assert (tmp_path / "data.db.pre-canonical.bak").is_file()
    assert not st.save_failed
    # مهاجرت پایدار است — بارِ دوم دوباره rename نمی‌شود
    st.close()
    st2 = Store(tmp_path / "config.json")
    assert set(st2.data["items"]) == {canon_ab, canon_cd, canon_dup}
    st2.close()


# ---------------------------------------------------------------- §۱۴/§۱۵ manifests
def test_manifest_distinguishes_same_second(tmp_path):
    """(§۱۴) جایگزینی هم‌اندازه در همان ثانیه با ns متفاوت → مانیفست عوض می‌شود."""
    d = tmp_path / "item"
    d.mkdir()
    f = d / "a.mp4"
    f.write_bytes(b"0" * 100)
    sec = 1_600_000_000
    os.utime(f, ns=(sec * 10**9 + 123_456_789,) * 2)
    man1, _files = scanner.thumb_manifest(d)
    os.utime(f, ns=(sec * 10**9 + 987_654_321,) * 2)   # همان ثانیه، ns دیگر
    man2, _files = scanner.thumb_manifest(d)
    assert man1["a.mp4"] != man2["a.mp4"]
    assert man1["a.mp4"][0] == man2["a.mp4"][0] == 100
    os.utime(f, ns=(sec * 10**9 + 123_456_789,) * 2)
    man3, _files = scanner.thumb_manifest(d)
    assert man3 == man1                    # همان ns → برابر (idempotent)


def test_manifest_era_includes_cache_px(tmp_path):
    """(§۱۵) era = t2-<px مؤثر> — تغییرِ مقیاسِ نمایش مانیفست‌ها را بی‌اعتبار می‌کند."""
    assert prebuild._MANIFEST_ERA.startswith("t2")
    tc = ThumbnailCache(tmp_path / ".tc")
    era = f"{prebuild._MANIFEST_ERA}-{tc.size[0]}"
    assert era.startswith("t2-")
    old = dict({"m": {}, "r": 0, "e": "t1"})       # رکوردِ نسخهٔ قبل
    assert old["e"] != era                  # → بازبینیِ یک‌باره (قراردادِ prebuild)


# ---------------------------------------------------------------- §۲۰ cache dir
def test_backup_uses_effective_cache_dir(tmp_path, monkeypatch):
    """(§۲۰) MC_CACHE_DIR: export از مسیرِ مؤثر می‌خواند و restore به همان می‌نویسد."""
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(backup, "app_dir", lambda: app)
    cache = tmp_path / "custom-cache"
    cache.mkdir()
    (cache / "t.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setenv("MC_CACHE_DIR", str(cache))
    monkeypatch.setattr(covers, "app_dir", lambda: app)

    # app دادهٔ حداقلی دارد: data.db واقعی (اسنپ‌شات) + config
    con = sqlite3.connect(str(app / "data.db"))
    con.execute("CREATE TABLE items (path TEXT PRIMARY KEY, meta TEXT NOT NULL)")
    con.commit()
    con.close()
    (app / "config.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "b.mcbak"
    backup.export_backup(out, include_cache=True)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert ".thumb_cache/t.png" in names   # کشِ مسیرِ مؤثر داخلِ پشتیبان است

    # restore: pending با کش → مقصد، همان مسیرِ مؤثر
    (cache / "t.png").unlink()
    pending = app / ".mc_import_pending"
    (pending / ".thumb_cache").mkdir(parents=True)
    (pending / ".thumb_cache" / "restored.bin").write_bytes(b"r")
    doc = {"pending": str(pending), "files": 1, "include_cache": True,
           "applied": ["config.json", "data.db"], "attempts": 0}
    (app / "._mc_import").write_text(json.dumps(doc), encoding="utf-8")
    backup.apply_pending_import()
    assert (cache / "restored.bin").is_file()
    assert not (app / ".thumb_cache" / "restored.bin").exists()


# ---------------------------------------------------------------- §0 CI hardening
def test_c2_stress_encoding_is_hermetic():
    """زیرپروسهٔ stress همیشه UTF-8 می‌خواند (Windows/3.12 CI — defense in depth)."""
    src = (REPO_ROOT / "tests" / "test_cover_actions_fixes.py").read_text(
        encoding="utf-8")
    assert 'encoding="utf-8", errors="replace"' in src
    # and the workflow pins PYTHONUTF8 so the child never writes cp1252
    wf = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8")
    assert 'PYTHONUTF8: "1"' in wf
