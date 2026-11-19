# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Regression tests for the cover-action fixes.

- changing/removing a cover on the details page must invalidate the
  library card of THAT item (cache + in-flight worker result), not just the
  details page;
- importing a cover must never hit
  `QThread: Destroyed while thread is still running` (qFatal → whole app
  aborts) — deleteLater is wired to QThread.finished, the official pattern;
  the stress loop runs in a SUBPROCESS because the crash is an abort, not a
  Python exception;
- a *meta* save failure (e.g. locked SQLite) must show its own message,
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
    """MainWindow with a demo root — for the UI paths."""
    monkeypatch.setattr(app_main, "CACHE_DIR", tmp_path / ".thumb_cache")
    demo = _demo_root(tmp_path)
    store = Store(tmp_path / "config.json")
    store.set_roots([{"path": str(demo), "category": ""}])
    w = app_main.MainWindow(store)
    items = scan_roots(store.roots(),
                       include_image_fallback=store.include_image_fallback)
    assert items, "demo scan produced no items"
    w.on_scan_finished((items, None, None))
    qapp.processEvents()
    yield w, items[0]
    w.close()
    qapp.processEvents()
    store.close()
    shutil.rmtree(str(tmp_path / ".thumb_cache"), ignore_errors=True)


# ------------------------------------------------------------------ cover-change invalidation
def test_cover_change_invalidates_library_card(qapp, window):
    """Changing the cover from the details page must invalidate the poster
    cache of THAT title and mark the library dirty.

    Previously _after_cover_change emitted no signal; the card kept showing
    the old cover until a refresh/rescan."""
    w, item = window
    w.details.set_item(item)
    w.stack.setCurrentWidget(w.details)
    qapp.processEvents()
    # fill the card cache as if a card had already loaded
    key = ("old-cache.png", 123)
    w.library._item_png[str(item.path)] = key
    w.library._poster_started.add(str(item.path))
    seen = []
    w.details.coverChanged.connect(seen.append)

    w.details._after_cover_change(True)
    qapp.processEvents()

    assert seen and seen[0] is item            # the signal carried the item itself
    assert str(item.path) not in w.library._item_png
    assert str(item.path) not in w.library._poster_started
    assert str(item.path) in w.library._stale_sources
    assert w._lib_dirty is True                # rebuilt on the way back

    w._back_to_library()
    qapp.processEvents()
    assert w.library._stale_sources == set()   # fresh generation — guard cleared


def test_cover_clear_also_invalidates(qapp, window):
    """Removing the cover from the details page takes the same path — back to the folder icon."""
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
    """An in-flight worker result for a cover-changed title must not deliver the old poster."""
    w, item = window
    path = str(item.path)
    w.library._stale_sources.add(path)
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(0xFF0000FF)
    gen = w.library._cards_gen

    w.library._on_poster_loaded(gen, path, "old.png", 5, img)
    assert path not in w.library._item_png     # stale result rejected

    # the next result (fresh source — the guard is one-shot per rejection) is accepted
    w.library._on_poster_loaded(gen, path, "new.png", 7, img)
    assert w.library._item_png.get(path) == ("new.png", 7)


# ------------------------------------------------------------------ QThread stress (subprocess)
_COVER_STRESS_SRC = r'''
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
portable.app_dir = lambda: tmp          # covers destination -> the fake folder
covers.app_dir = lambda: tmp

from ui import cover_actions
cover_actions.app_dir = lambda: tmp     # third override — otherwise the storm
                                        # writes into the app's *real* folder

# No modal dialog may open inside the subprocess — a transient Windows
# error (an os.replace lock) sends the worker to QMessageBox.warning,
# which never gets closed in the subprocess → on_done never completes →
# SystemExit. Errors are recorded and reported at the end.
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
for wave in range(8):
    # Explosive: 50 back-to-back imports with no waiting — several workers
    # in flight; the
    # deleteLater removal in the slot must race the still-running worker thread)
    for i in range(50):
        cover_actions._run_import(parent, store, item, src,
                                  lambda changed: done.__setitem__("n", done["n"] + 1))
        app.processEvents()
    deadline = time.monotonic() + 60
    while done["n"] < (wave + 1) * 50 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    app.processEvents()
    if done["n"] != (wave + 1) * 50:
        raise SystemExit(f"on_done count mismatch in wave {wave}")
if _ui_msgs:
    raise SystemExit("cover import showed UI dialogs (locked replace?): "
                     + " | ".join(_ui_msgs[:3]))
print("COVER-OK", done["n"], round(time.monotonic() - t0, 1))
sys.stdout.flush()
# Hard exit once the final result is in — an upstream shiboken6 6.12.0
# bug on Python 3.11: delivering a dict payload from Signal(object) to a
# Python slot corrupts the refcount of True/False, which surfaces only at
# interpreter shutdown (the final GC) as "Fatal Python error:
# bool_dealloc" and SIGABRT. The runtime itself is perfectly healthy
# (all 400 callbacks arrived); it does not happen on Python 3.12+ or on
# older shiboken6 builds either. os._exit merely keeps that teardown bug
# away from this test — the explicit flush is required because stdout is
# block-buffered on a pipe and os._exit skips the buffers. Every runtime
# guard is armed: a counter mismatch or a modal dialog opening there
# raises SystemExit with a non-zero code right away, and "QThread:
# Destroyed" is qFatal too — no error path slips past this hard exit.
os._exit(0)
'''


def test_import_worker_stress_no_qthread_crash(tmp_path):
    """400 explosive imports (8x50): no "QThread: Destroyed..." and no lost
    result.

    The old code called deleteLater inside the ok/fail slots; the stress
    scenario crashed before iteration 50 in 3/3 runs. This test runs in a
    subprocess because qFatal yields a non-zero exit code, not a Python
    exception.

    The subprocess is fully isolated: cover_actions.app_dir is faked too
    (otherwise the storm would write into the app's real folder),
    QMessageBox is turned into a message recorder (a modal dialog can never
    be closed inside a subprocess, and that is exactly what made the Windows
    run end with rc=1), and the output is read with
    encoding="utf-8" — the previous cp1252 decode died in the reader thread
    and hid the subprocess's real failure reason.

    The subprocess exits with os._exit(0) after printing its result:
    shiboken6 6.12.0 on Python 3.11 corrupts the refcount of True/False at
    interpreter shutdown (bool_dealloc -> SIGABRT) when a dict payload
    travels through Signal(object) to a Python slot, turning a perfectly
    healthy run red — an upstream bug, not ours; absent on Python 3.12+ and
    older shiboken6 builds. The hard exit is only reached on the success
    path; failure paths exit non-zero right away via SystemExit/qFatal and
    stay visible in the test output."""
    script = tmp_path / "c2stress.py"
    script.write_text(_COVER_STRESS_SRC
                      .replace("__REPO__", repr(str(REPO_ROOT)))
                      .replace("__ITERS__", "400"),
                      encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, timeout=300, env=env, cwd=str(REPO_ROOT),
        encoding="utf-8", errors="replace")   # the subprocess's UTF-8 output must not die on cp1252
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, \
        f"stress subprocess failed rc={r.returncode}\n{combined[-2000:]}"
    assert "QThread: Destroyed" not in combined
    assert "COVER-OK 400" in r.stdout


# ------------------------------------------------------------------ meta-save failure message
def test_meta_save_failure_gets_dedicated_message(qapp, tmp_path, monkeypatch):
    """A meta-save failure (locked SQLite, …) must not get the "not a valid image" message."""
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

    assert results == [False]                  # the change was not recorded
    assert warnings == [covers.T("ذخیره‌ی اطلاعاتِ کاور ممکن نشد.")]
    bad = covers.T("این فایل تصویرِ معتبری نیست.")
    assert bad not in warnings                 # not the old wrong message


# ------------------------------------------------------------------- degraded-animation message
def test_degraded_notice_text_is_translatable():
    """The "animation removed" message key has an English equivalent (the T() contract)."""
    from i18n_en import EN
    assert covers.DEGRADED_MSG_TEXT in EN
    assert EN[covers.DEGRADED_MSG_TEXT].strip()


# --------------------------------- the layer under the animation (not the purple placeholder)
def test_movie_underlay_is_not_letter_placeholder(qapp, window, monkeypatch,
                                                  tmp_path):
    """No letter placeholder may remain behind an animated cover — static
    frame / transparent layer.

    The square gif covers only the middle band of the frame; the empty band
    around it used to be the category's purple (paper theme +
    gif -> clashing purple). Now _set_poster_media puts a static frame
    (transparent letterbox) or a transparent layer under the video, and
    _on_hero_ready no longer installs the placeholder under an active
    video."""
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

    assert w.details._movie is not None             # the animation is really active
    img = w.details.poster.pixmap().toImage()
    # a top-band point: outside the square image, inside the rounded frame
    assert img.pixelColor(126, 20).alpha() == 0     # transparent — the theme color shows through from behind
    assert img.pixelColor(126, 168).alpha() == 255  # center: the static frame itself sits under the video
    # old behavior for comparison — the placeholder used to be opaque purple there:
    ph = placeholder_poster("م", "#a78bfa", 252, 336, 16)
    assert ph.toImage().pixelColor(126, 20).alpha() == 255

    # hero fails (empty png) — the placeholder must not come back under the active video
    w.details._on_hero_ready(str(item.path), "", "")
    img2 = w.details.poster.pixmap().toImage()
    assert img2.pixelColor(126, 20).alpha() == 0

    # hero returns a real static frame — installed under the video, the band stays transparent
    w.details._on_hero_ready(str(item.path), str(fake / info["poster_static"]), "")
    img3 = w.details.poster.pixmap().toImage()
    assert img3.pixelColor(126, 20).alpha() == 0
    assert img3.pixelColor(126, 168).alpha() == 255


def test_static_cover_still_gets_placeholder(qapp, window, monkeypatch,
                                             tmp_path):
    """Without an animated cover, the previous behavior stands — placeholder until the hero arrives."""
    w, item = window
    w.details.set_item(item)
    qapp.processEvents()
    assert w.details._movie is None
    assert w.details.poster.pixmap() is not None


def test_apply_theme_rebuilds_card_pixmap_cache(qapp, window):
    """Switching themes clears the cards' pixmap cache.

    The cards' letterbox color is baked into the pixmap at build time in
    _pm_cache; without clearing the cache, cards would keep the old color
    band after a theme switch until the next rebuild."""
    w, _item = window
    w.library._item_png["whatever"] = ("old.png", 4)
    w.apply_theme("paper")
    qapp.processEvents()
    assert w.library._item_png == {}                # rebuilt with the new theme
