# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for the cover-action fixes (round 16: C1, C2, C5).

- C1: changing/removing a cover on the details page must invalidate the
  library card of THAT item (cache + in-flight worker result), not just the
  details page;
- C2: importing a cover must never hit
  `QThread: Destroyed while thread is still running` (qFatal → whole app
  aborts) — deleteLater is wired to QThread.finished, the official pattern;
  the stress loop runs in a SUBPROCESS because the crash is an abort, not a
  Python exception;
- C5: a *meta* save failure (e.g. locked SQLite) must show its own message,
  not «this file is not a valid image».
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import covers  # noqa: E402
import main as app_main  # noqa: E402
import portable  # noqa: E402
from scanner import scan_roots  # noqa: E402
from store import Store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _demo_root(tmp: Path) -> Path:
    demo = tmp / "demo"
    (demo / "Movie A").mkdir(parents=True)
    (demo / "Movie B").mkdir()
    icon = REPO_ROOT / "assets" / "icon.png"
    if icon.is_file():
        shutil.copy(str(icon), str(demo / "Movie A" / "poster.png"))
    (demo / "Movie B" / "readme.txt").write_text("demo", encoding="utf-8")
    return demo


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    """MainWindow با یک ریشه‌ی دمو — برای مسیرهای UI."""
    monkeypatch.setattr(app_main, "CACHE_DIR", tmp_path / ".thumb_cache")
    demo = _demo_root(tmp_path)
    store = Store(tmp_path / "config.json")
    store.set_roots([{"path": str(demo), "category": ""}])
    w = app_main.MainWindow(store)
    items = scan_roots(store.roots(),
                       include_image_fallback=store.include_image_fallback)
    assert items, "demo scan produced no items"
    w.on_scan_finished((items, None))
    qapp.processEvents()
    yield w, items[0]
    w.close()
    qapp.processEvents()
    store.close()
    shutil.rmtree(str(tmp_path / ".thumb_cache"), ignore_errors=True)


# ------------------------------------------------------------------ C1
def test_cover_change_invalidates_library_card(qapp, window):
    """تعویض کاور از جزئیات → کشِ پوسترِ *همان* عنوان بی‌اعتبار و کتابخانه dirty (C1).

    قبلاً _after_cover_change هیچ سیگنالی نمی‌داد؛ کارت تا refresh/rescan
    کاورِ قدیمی نشان می‌داد."""
    w, item = window
    w.details.set_item(item)
    w.stack.setCurrentWidget(w.details)
    qapp.processEvents()
    # کشِ کارت را مثل یک کارتِ بارگذاری‌شده پر می‌کنیم
    key = ("old-cache.png", 123)
    w.library._item_png[str(item.path)] = key
    w.library._poster_started.add(str(item.path))
    seen = []
    w.details.coverChanged.connect(seen.append)

    w.details._after_cover_change(True)
    qapp.processEvents()

    assert seen and seen[0] is item            # سیگنال با خودِ item آمد
    assert str(item.path) not in w.library._item_png
    assert str(item.path) not in w.library._poster_started
    assert str(item.path) in w.library._stale_sources
    assert w._lib_dirty is True                # در برگشت بازسازی می‌شود

    w._back_to_library()
    qapp.processEvents()
    assert w.library._stale_sources == set()   # نسلِ تازه — نگهبان برطرف


def test_cover_clear_also_invalidates(qapp, window):
    """حذفِ کاور از جزئیات هم همان مسیر است (C1) — بازگشت به آیکونِ پوشه."""
    w, item = window
    w.details.set_item(item)
    w.stack.setCurrentWidget(w.details)
    qapp.processEvents()
    w.library._item_png[str(item.path)] = ("old.png", 1)

    w.details._after_cover_clear(True)
    qapp.processEvents()

    assert str(item.path) not in w.library._item_png
    assert str(item.path) in w.library._stale_sources


def test_stale_worker_result_is_dropped(qapp, window):
    """نتیجه‌ی درِپروازِ کارگر برای عنوانِ عوض‌کرده‌ی کاور پوسترِ قدیمی برنمی‌گرداند (C1)."""
    w, item = window
    path = str(item.path)
    w.library._stale_sources.add(path)
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(0xFF0000FF)
    gen = w.library._cards_gen

    w.library._on_poster_loaded(gen, path, "old.png", 5, img)
    assert path not in w.library._item_png     # نتیجه‌ی قدیمی رد شد

    # نتیجه‌ی بعدی (منبعِ تازه — بعد از هر رد، نگهبان یک‌باره است) پذیرفته می‌شود
    w.library._on_poster_loaded(gen, path, "new.png", 7, img)
    assert w.library._item_png.get(path) == ("new.png", 7)


# ------------------------------------------------------------------ C2
_C2_STRESS_SRC = r'''
import os, sys, time, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, __REPO__)
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
app = QApplication([])

import covers, portable
tmp = Path(tempfile.mkdtemp(prefix="c2stress-"))
portable.app_dir = lambda: tmp          # مقصدِ کاورها → پوشه‌ی قلابی
covers.app_dir = lambda: tmp

from ui import cover_actions
cover_actions.app_dir = lambda: tmp     # (round 17) ازِ سومی — وگرنه storm
                                        # داخلِ پوشه‌ی *واقعیِ* برنامه می‌نوشت

# (round 17) هیچ دیالوگِ مودالی در زیرپروسه نباید باز شود — یک خطایِ گذرای
# ویندوزی (قفلِ os.replace) کارگر را به QMessageBox.warning می‌برد که در
# زیرپروسه هیچ‌وقت بسته نمی‌شود → on_done کامل نمی‌شود → SystemExit. خطاها
# ثبت و در پایان گزارش می‌شوند.
_ui_msgs: list = []
def _record_msg(*a, **k):
    _ui_msgs.append(str(a[2]) if len(a) > 2 else "?")
QMessageBox = cover_actions.QMessageBox
QMessageBox.warning = staticmethod(_record_msg)
QMessageBox.information = staticmethod(_record_msg)

class FakeStore:
    def set_custom_poster(self, *a, **k):
        pass

from PIL import Image
src = tmp / "tiny.png"
Image.new("RGB", (64, 64), (12, 34, 56)).save(src)

parent = QObject()
done = {"n": 0}
store = FakeStore()
item = SimpleNamespace(path=Path("C:/x/y"))

t0 = time.monotonic()
for round_ in range(8):
    # انفجاری: ۵۰ ایمپورت پشت‌سرهم بدونِ انتظار — دقیقاً الگویِ بازتولیدِ
    # ممیزی (کارگرهای چندگانه در پرواز؛ حذفِ deleteLater در slot باید با
    # نخِ هنوز-در-حال-اجرای کارگر مسابقه دهد)
    for i in range(50):
        cover_actions._run_import(parent, store, item, src,
                                  lambda changed: done.__setitem__("n", done["n"] + 1))
        app.processEvents()
    deadline = time.monotonic() + 60
    while done["n"] < (round_ + 1) * 50 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    app.processEvents()
    if done["n"] != (round_ + 1) * 50:
        raise SystemExit(f"on_done count mismatch in round {round_}")
if _ui_msgs:
    raise SystemExit("cover import showed UI dialogs (locked replace?): "
                     + " | ".join(_ui_msgs[:3]))
print("C2-OK", done["n"], round(time.monotonic() - t0, 1))
sys.stdout.flush()
# (round 22) خروجِ سخت بعد از نتیجهٔ نهایی — باگِ upstreamِ shiboken6 6.12.0
# روی پایتون 3.11: رساندنِ payloadِ dict از Signal(object) به slotِ پایتونی
# refcountِ True/False را خراب می‌کند که فقط در پایانِ عمرِ مفسر (GCِ نهایی)
# به «Fatal Python error: bool_dealloc» و SIGABRT می‌رسد. زمانِ اجرا کاملاً
# سالم است (هر ۴۰۰ callback رسید)؛ روی پایتون 3.12+ و shiboken6های قدیمی‌تر
# هم نیست. os._exit فقط جلوی رساندنِ آن باگِ teardown به این تست را
# می‌گیرد — flush صریح لازم است چون stdout به pipe بلوک‌بافر است و
# os._exit از بافرها می‌گذرد. همهٔ گره‌های زمانِ اجرا مسلح‌اند: عدمِ
# تطابقِ شمارنده یا بازشدنِ دیالوگِ مودال همان‌جا SystemExit غیرصفر
# می‌دهد و «QThread: Destroyed» هم qFatal است — هیچ مسیرِ خطایی از این
# خروجِ سخت رد نمی‌شود.
os._exit(0)
'''


def test_import_worker_stress_no_qthread_crash(tmp_path):
    """۴۰۰ ایمپورتِ انفجاری (۸×۵۰): بدونِ «QThread: Destroyed…» و بدونِ از‌دست‌رفتنِ نتیجه (C2).

    کدِ قدیمی deleteLater را داخلِ slotهای ok/fail می‌زد؛ ممیزی در ۳/۳ اجرا
    قبل از تکرارِ ۵۰ کرش گرفت. این تست در زیرپروسه اجراست چون qFatal کدِ
    خروجِ غیرصفر می‌دهد نه استثنای پایتون.

    (round 17) زیرپروسه کاملاً ایزوله شد: cover_actions.app_dir هم قلابی می‌شود
    (وگرنه storm داخلِ پوشه‌ی واقعیِ برنامه می‌نوشت)، QMessageBox به ثبتِ
    پیام تبدیل می‌شود (دیالوگِ مودالی در زیرپروسه هیچ‌وقت بسته نمی‌شود و
    گزارشِ کاربرِ ویندوزی دقیقاً همین را rc=1 می‌کرد) و خواندنِ خروجی با
    encoding="utf-8" است — قبلاً decodeِ cp1252 در نخِ خواننده می‌مرد و
    علتِ واقعیِ شکستِ زیرپروسه گم می‌شد.

    (round 22) زیرپروسه بعد از چاپِ نتیجه با os._exit(0) خارج می‌شود:
    shiboken6 6.12.0 روی پایتون 3.11 در پایانِ عمرِ مفسر، refcountِ
    True/False را خراب می‌بیند (bool_dealloc → SIGABRT) و تستِ سالمِ
    زمانِ اجرا را قرمز می‌کرد — باگِ upstream است نه برنامه؛ روی
    پایتون 3.12+ و shiboken6های قدیمی‌تر نیست. خروجِ سخت فقط از مسیرِ
    موفق می‌گذرد؛ مسیرهایِ خطا با SystemExit/qFatal همان‌جا غیرصفر
    خارج می‌شوند و در خروجیِ تست می‌مانند."""
    script = tmp_path / "c2stress.py"
    script.write_text(_C2_STRESS_SRC
                      .replace("__REPO__", repr(str(REPO_ROOT)))
                      .replace("__ITERS__", "400"),
                      encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, timeout=300, env=env, cwd=str(REPO_ROOT),
        encoding="utf-8", errors="replace")   # خروجیِ UTF-8 زیرپروسه روی cp1252 نمی‌میرد
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, \
        f"stress subprocess failed rc={r.returncode}\n{combined[-2000:]}"
    assert "QThread: Destroyed" not in combined
    assert "C2-OK 400" in r.stdout


# ------------------------------------------------------------------ C5
def test_meta_save_failure_gets_dedicated_message(qapp, tmp_path, monkeypatch):
    """شکستِ ذخیره‌ی متا (SQLite قفل…) پیامِ «عکسِ خراب» نمی‌گیرد (C5)."""
    from ui import cover_actions
    tmp = tmp_path / "app"
    monkeypatch.setattr(portable, "app_dir", lambda: tmp)
    monkeypatch.setattr(covers, "app_dir", lambda: tmp)

    warnings = []
    infos = []

    def _warn(parent, title, text, *a, **k):
        warnings.append(text)

    def _info(parent, title, text, *a, **k):
        infos.append(text)

    monkeypatch.setattr(cover_actions.QMessageBox, "warning", _warn)
    monkeypatch.setattr(cover_actions.QMessageBox, "information", _info)

    class BoomStore:
        def set_custom_poster(self, *a, **k):
            raise RuntimeError("database is locked")

    from PIL import Image
    src = tmp_path / "tiny.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(src)

    parent = QObject()
    results = []
    cover_actions._run_import(parent, BoomStore(), SimpleNamespace(path=Path("x")),
                              src, results.append)
    deadline = time.monotonic() + 30
    while parent._cover_worker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.001)
    QApplication.processEvents()

    assert results == [False]                  # تغییر ثبت نشد
    assert warnings == [covers.T("ذخیره‌ی اطلاعاتِ کاور ممکن نشد.")]
    bad = covers.T("این فایل تصویرِ معتبری نیست.")
    assert bad not in warnings                 # پیامِ غلطِ قبلی نیست


# ------------------------------------------------------------------ C4-b (پیامِ degraded)
def test_degraded_notice_text_is_translatable():
    """کلیدِ پیامِ «انیمیشن حذف شد» معادلِ انگلیسی دارد (قراردادِ T)."""
    from i18n_en import EN
    assert covers.DEGRADED_MSG_TEXT in EN
    assert EN[covers.DEGRADED_MSG_TEXT].strip()


# --------------------------------- round 17 §۲ — لایه‌ی زیرِ انیمیشن (نه placeholderِ بنفش)
def test_movie_underlay_is_not_letter_placeholder(qapp, window, monkeypatch,
                                                  tmp_path):
    """پشتِ کاورِ متحرک placeholderِ حروفی نمی‌ماند — فریمِ ساکن/لایه‌ی شفاف (round 17 §۲).

    گیفِ مربعی فقط بخشِ میانیِ قاب را می‌پوشاند؛ قبلاً نوارِ خالیِ اطرافش
    بنفشِ رنگِ دسته بود (گزارش کاربر: تمِ کاغذ + گیف → بنفشِ ناهماهنگ).
    حالا _set_poster_media فریمِ ساکن (letterboxِ شفاف) یا لایه‌ی شفاف زیرِ
    فیلم می‌نشیند و _on_hero_ready هم placeholder را زیرِ فیلمِ فعال
    نصب نمی‌کند."""
    from PIL import Image
    from ui.widgets import placeholder_poster

    w, item = window
    fake = tmp_path / "appfake"
    fake.mkdir()
    monkeypatch.setattr(portable, "app_dir", lambda: fake)
    monkeypatch.setattr(covers, "app_dir", lambda: fake)

    frames = [Image.new("RGB", (64, 64), c) for c in ((180, 40, 40), (40, 180, 40))]
    src = fake / "square.gif"
    frames[0].save(src, save_all=True, append_images=frames[1:],
                   duration=80, loop=0)
    info = covers.import_cover(src, fake)
    assert info["animated"] is True
    w.store.set_custom_poster(str(item.path), info)

    w.details.set_item(item)
    qapp.processEvents()

    assert w.details._movie is not None             # انیمیشن واقعاً فعال است
    img = w.details.poster.pixmap().toImage()
    # نقطه‌ی نوارِ بالایی: بیرونِ تصویرِ مربعی، داخلِ قابِ گردشده
    assert img.pixelColor(126, 20).alpha() == 0     # شفاف — رنگِ تم از پشت دیده می‌شود
    assert img.pixelColor(126, 168).alpha() == 255  # مرکز: خودِ فریمِ ساکن زیرِ فیلم
    # رفتارِ قدیمی برای مقایسه — placeholder همان‌جا ماتِ بنفش بود:
    ph = placeholder_poster("م", "#a78bfa", 252, 336, 16)
    assert ph.toImage().pixelColor(126, 20).alpha() == 255

    # hero با شکست برگردد (png خالی) — placeholder زیرِ فیلمِ فعال برنمی‌گردد
    w.details._on_hero_ready(str(item.path), "", "")
    img2 = w.details.poster.pixmap().toImage()
    assert img2.pixelColor(126, 20).alpha() == 0

    # hero با فریمِ ساکنِ واقعی برگردد — زیرِ فیلم نصب می‌شود، نوار همچنان شفاف
    w.details._on_hero_ready(str(item.path), str(fake / info["poster_static"]), "")
    img3 = w.details.poster.pixmap().toImage()
    assert img3.pixelColor(126, 20).alpha() == 0
    assert img3.pixelColor(126, 168).alpha() == 255


def test_static_cover_still_gets_placeholder(qapp, window, monkeypatch,
                                             tmp_path):
    """بدونِ کاورِ متحرک، رفتارِ قبلی سرِ جایش است — placeholder تا رسیدنِ hero."""
    w, item = window
    w.details.set_item(item)
    qapp.processEvents()
    assert w.details._movie is None
    assert w.details.poster.pixmap() is not None


def test_apply_theme_rebuilds_card_pixmap_cache(qapp, window):
    """تعویضِ تم → کشِ پیکسمپِ کارت‌ها پاک می‌شود (تحلیلِ هوشِ ۱).

    رنگِ letterboxِ کارت‌ها موقعِ ساختِ پیکسمپ در _pm_cache پخته می‌شود؛
    بدونِ پاک‌کردنِ کش، کارت‌ها بعد از تعویضِ تم نوارِ رنگِ قدیمی نگه
    می‌داشتند تا بازسازیِ بعدی."""
    w, _item = window
    w.library._item_png["whatever"] = ("old.png", 4)
    w.apply_theme("paper")
    qapp.processEvents()
    assert w.library._item_png == {}                # بازسازی با تمِ تازه
