# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""FolderDex — application entry point.

Run:   python main.py
"""
from __future__ import annotations

from i18n import T

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QStackedWidget, QWidget,
)

import watchtime
from appinfo import app_title
from media_info import DurationStore, fast_duration, format_duration, probe_many
from nav import NavHistory                  # navigation history (mouse side buttons)
from prebuild import Prebuilder
from scanner import fa, fa_mmss, item_videos, item_watchables, scan_roots
from store import Store
from thumbs import ThumbnailCache, cache_px_for_app
# HTML export was removed entirely (design decision: stay focused on the library itself)
from qss import build_theme, theme_bg, theme_label
from ui.details import DetailsPage, rel_of
from ui.dialogs import ItemEditDialog, SettingsDialog
from ui.library import LibraryPage
from ui.splash import SplashScreen
from ui.widgets import open_path

from scanner import VIDEO_EXTS

from portable import app_dir as _portable_app_dir
# BUILD/exe: under PyInstaller, data lives next to the exe and resources inside the bundle;
# app_dir() is correct in both cases (source: this folder; exe: the executable's folder).
APP_DIR = _portable_app_dir()

# Everything lives next to the app; nothing is stored in a global path.
# An earlier design (a Windows-wide global folder) was later reverted:
# on the first run after that change, data in the old global path — if still there — is *moved*
# (not copied) next to the app once, and the emptied global folder is removed.
# From then on, moving data between versions happens via the "Export/Import data"
# buttons in Settings (backup.py).
import paths as _paths                                  # noqa: E402
from watchtime import dbg_log
_moved = _paths.migrate_shared_to_app()
CONFIG_PATH = _paths.default_config_path()
CACHE_DIR = _paths.thumb_cache_dir()
if _moved:
    try:
        watchtime._dbg("shared→app migration: " + ", ".join(_moved))
    except Exception as _exc_dbg:
        dbg_log(f"main.<module> failed: {type(_exc_dbg).__name__}")
        pass


def _path_under_any(path, roots_norm) -> bool:
    """Is `path` inside any of the normalized root dirs?

    Used by on_scan_finished to keep recents that belong to roots this scan
    could not read (offline/failed drive) - their history is protected."""
    np_ = Store.norm_path(path)
    return any(np_ == r or np_.startswith(r + os.sep) for r in roots_norm)


class ScanWorker(QThread):
    progressed = Signal(str)
    counts = Signal(str, int, int)   # (phase, done, total) — real scan progress bar
    finished_items = Signal(object)

    def __init__(self, store: Store, cache: ThumbnailCache,
                 dstore: DurationStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        self.dstore = dstore   # same shared instance as the details page — the cache isn't wiped

    def run(self):
        # Report per-root scan result so the caller can
        # prune recents ONLY for successfully scanned roots - a temporarily
        # offline drive must not lose its history (partial multi-root scan).
        scan_status = {"ok": [], "failed": []}
        try:
            items = scan_roots(
                self.store.roots(),
                include_image_fallback=self.store.include_image_fallback,
                progress=self.progressed.emit,
                counts=lambda d, t: self.counts.emit("scan", d, t),
                status=scan_status,
)
            # "The app must always be usable" — video duration computation
            # is no longer part of the scan: the window opens right after the scan, with
            # posters, and durations are computed in the background by DurationWorker
            # (status bar: "computing durations…").
            durations = None
        except Exception as e:  # any error = a failed scan, not a hung splash
            dbg_log(f"run failed: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            self.progressed.emit(T("⚠ خطای اسکن: {0}").format(e))
            items, durations = [], None
        self.finished_items.emit((items, durations, scan_status))


class DurationWorker(QThread):
    """Compute video durations in the background — without delaying app startup.

    This used to run inside the scan thread *before* the window was shown ("wait
    a few minutes for the app to come up"). Now: the scan only gathers titles,
    the window and posters appear immediately, and this worker computes durations
    in the background using manifests (unchanged folders are skipped); its
    progress shows as "⏱ computing durations…" in the status bar and the cards
    update when it finishes.
    """

    progressed = Signal(str)
    counts = Signal(str, int, int)          # ("durations", done, total)
    totals_ready = Signal(object)           # (totals, manifests) or None

    def __init__(self, store: Store, dstore: DurationStore, items: list, parent=None):
        super().__init__(parent)
        self.store = store
        self.dstore = dstore
        self.items = list(items)
        self._abort = False

    def stop(self):
        self._abort = True

    def _item_manifest(self, videos) -> dict:
        """Fingerprint of an item's videos: {rel path: [size, mtime_ns]} — stable across runs.

        Full seconds used to be stored; replacing a file with an equal-size file in
        the *same second* looked "unchanged". Nanoseconds are now stored/compared —
        old second-granularity manifests mismatch exactly once and are then refilled
        from the durations cache (no new probe)."""
        man: dict[str, list] = {}
        for vf in videos:
            try:
                st = vf.stat()
                rel = str(vf.relative_to(self._manifest_base)).replace("\\", "/") \
                    if self._manifest_base else vf.name
                man[rel] = [st.st_size, int(st.st_mtime_ns)]
            except OSError:
                continue
        return man

    def _video_durations(self, items):
        """Per-item totals via manifests: an unchanged folder means no recomputation."""
        totals: dict[str, int] = {}
        manifests: dict[str, dict] = {}
        to_probe: dict[str, list] = {}       # only changed/new items
        skipped = 0
        for item in items:
            if self._abort:
                return None
            key = str(item.path)
            self._manifest_base = item.path if not item.path.is_file() else item.path.parent
            videos = item_videos(item)
            man = self._item_manifest(videos)
            old_man = self.store.manifest_of(item)
            old_total = self.store.total_duration(item)
            if old_man is not None and old_man == man and (
                    (videos and old_total > 0) or not videos):
                totals[key] = old_total
                manifests[key] = man
                skipped += 1
                continue
            totals[key] = 0
            manifests[key] = man
            to_probe[key] = [vf for vf in videos if self.dstore.get(vf) is None]
        if skipped:
            self.progressed.emit(T("⏱ {0} عنوان بدون تغییر — محاسبه‌ی مجدد ندارند").format(fa(skipped)))

        # Files needing a probe (only those missing from the cache) — parallel
        flat: list = []
        for key, vids in to_probe.items():
            flat.extend(vids)
        if flat:
            self.progressed.emit(T("⏱ خواندن زمان {0} ویدیوی جدید…").format(fa(len(flat))))
            last = [0.0]

            def _prog(d, t):
                # throttle: at most ~7 UI messages per second (matters with thousands of files)
                now = time.monotonic()
                if d == t or now - last[0] >= 0.15:
                    last[0] = now
                    # the explicit "computing durations" text
                    self.progressed.emit(T("⏱ در حال محاسبه‌ی زمان… {0} از {1}").format(fa(d), fa(t)))
                    self.counts.emit("durations", d, t)   # real progress bar

            # MKV/MP4/AVI/WMV headers are read directly and very fast (no subprocess);
            # ffprobe is used only for the remaining formats.
            # Workers are no longer a fixed count; conservative on HDD,
            # full power on SSD/NVMe (same logic as hwinfo).
            try:
                import hwinfo as _hw39
                _w39 = _hw39.video_workers(
                    _hw39.any_rotational([str(x) for x in flat[:150]]))
            except Exception as _exc_dbg:
                dbg_log(f"_video_durations failed: {type(_exc_dbg).__name__}")
                _w39 = 16
            probe_many(flat, self.dstore, workers=_w39, progress=_prog)
        # Final totals from the cache (fast — no probing)
        for item in items:
            key = str(item.path)
            if totals.get(key):
                continue
            total = 0
            for vf in item_videos(item):
                v = self.dstore.get(vf)
                if v and v > 0:
                    total += v
            totals[key] = int(total)
        if self._abort:
            return None
        self.dstore.save()
        return totals, manifests

    def run(self):
        res = None
        try:
            if self.store.compute_durations and self.items and not self._abort:
                res = self._video_durations(self.items)
        except Exception as e:  # a duration-computation error must not kill the app
            try:
                from watchtime import _dbg
                _dbg(f"duration-worker error: {type(e).__name__}")
            except Exception as _exc_dbg:
                dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                pass
            res = None
        self.totals_ready.emit(res)


class _ProgressChip(QWidget):
    """The "thin bar + separate label" pair for the status bar.

    Text inside the 12-pixel bar itself got half-clipped ("the
    progress bar isn't fully visible"). The bar is now only the visual percentage,
    and the full text — count + "elapsed … · remaining …" — appears in a separate
    QLabel with natural height. The internal direction stays left-to-right so the
    bar/text layout is always stable.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(8)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(170)
        self.bar.setMinimumHeight(10)
        self.bar.setMaximumHeight(10)
        self.lbl = QLabel()
        from i18n import is_rtl as _rtl45     # progress label direction follows the language
        self.lbl.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if _rtl45()
            else Qt.LayoutDirection.LeftToRight)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl)

    def reset(self, t_max: int, text: str):
        self.bar.setRange(0, max(1, t_max))
        self.bar.setValue(0)
        self.lbl.setText(text)

    def update(self, done: int, total: int, text: str):
        self.bar.setMaximum(max(1, int(total)))
        self.bar.setValue(max(0, min(int(done), int(total))))
        self.lbl.setText(text)


def _arm_exit_watchdog(seconds: float = 4.0):
    """Hard-exit watchdog — separate from closeEvent so tests can safely disable it.

    If releasing the old process (background threads / nested event loops left
    behind) still ends incompletely like before, this timer force-kills the process
    so two instances are never open at the same time (seen after importing a
    backup file)."""
    try:
        import os as _os
        _wt = threading.Timer(seconds, lambda: _os._exit(0))
        _wt.daemon = True
        _wt.start()
    except Exception as _exc_dbg:
        dbg_log(f"_arm_exit_watchdog failed: {type(_exc_dbg).__name__}")
        pass


class MainWindow(QMainWindow):
    # Cache pruning due to the size cap (LRU) happens on a background thread;
    # it reaches the UI thread via a signal so the thumbnail manifests are invalidated too.
    cache_pruned = Signal(int)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        # The cache always lives next to the app — nothing is stored in a
        # global path, so no setting for it is needed either.
        self.cache_dir = CACHE_DIR
        # Cache size aware of the Windows display scale (100%→256, 150%→384, 200%→512)
        # so posters don't look soft/stretched at high scale factors — the cache key
        # format embeds this size too, so a scale change only causes a natural cache rebuild.
        cache_px = cache_px_for_app(QApplication.instance())
        self.cache_px = cache_px
        self.cache = ThumbnailCache(self.cache_dir, size=(cache_px, cache_px))
        self.worker: ScanWorker | None = None
        self.items = []

        self.setWindowTitle(app_title())            # title follows the language
        self.setMinimumSize(1000, 680)
        self.resize(1240, 800)
        icon_file = APP_DIR / "assets" / "icon.png"
        if icon_file.exists():
            self.setWindowIcon(QIcon(str(icon_file)))

        self.dstore = DurationStore(self.cache_dir)   # shared instance — scanner and details
        # background thumbnail prebuilder (settings option)
        self.prebuilder = Prebuilder(self.store, self.cache)
        self.duration_worker: DurationWorker | None = None   # durations run in the background
        # After importing a backup the old app used to stay open — the relaunch
        # happens only via this flag and through the clean closeEvent path — never from
        # inside a modal dialog (nested event loops made closing incomplete).
        self._restart_after_import = False
        self.cache_pruned.connect(self._on_cache_pruned)

        self.stack = QStackedWidget()
        self.library = LibraryPage(store, self.cache, cache_px=self.cache_px)
        self.details = DetailsPage(store, self.cache, durations=self.dstore,
                                   cache_px=self.cache_px)
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.details)
        self.setCentralWidget(self.stack)

        self.status_lbl = QLabel(T("آماده ✨"))
        # one-time read-only-fallback message: the cache lives in %TEMP% and isn't permanent
        if getattr(self.cache, "read_only_fallback", False) or \
                getattr(self.dstore, "read_only_fallback", False):
            self.status_lbl.setText(
                T("پوشه‌ی برنامه فقط‌خواندنی است — کش موقتاً در پوشه‌ی Temp ویندوز ساخته شد"))
        self.statusBar().addWidget(self.status_lbl, 1)
        # Both bars became a "separate label + thin bar" pair — Persian text no longer gets
        # clipped inside the short bar; the scan also moved from indeterminate
        # mode to real counting, and both show "elapsed/remaining".
        self.scan_chip = _ProgressChip()
        self.scan_chip.hide()
        self.statusBar().addPermanentWidget(self.scan_chip)
        self.pb_chip = _ProgressChip()
        self.pb_chip.hide()
        self.statusBar().addPermanentWidget(self.pb_chip)
        self._scan_track = {"phase": "", "t0": 0.0}   # ETA timer resets on each phase change
        self._pb_track = {"phase": "", "t0": 0.0}
        self._scan_t0 = 0.0
        self.splash: SplashScreen | None = None

        # Safe relay of thumbnail-prebuilder progress from the background thread to the status
        # bar — the report "the prebuild option doesn't work" really meant it was never
        # visible when it ran; now the percentage/count is shown live.
        class _PbRelay(QObject):
            text = Signal(str)                     # free-form text → status bar
            counts = Signal(str, int, int)         # (phase, built, total) → progress bar
        self._pb_relay = _PbRelay(self)
        self._pb_relay.text.connect(self._on_relay_text)
        self._pb_relay.counts.connect(self._on_prebuild_counts)
        self.prebuilder.on_progress = self._pb_relay.text.emit
        self.prebuilder.on_counts = self._pb_relay.counts.emit

        # Signal wiring
        self.library.openDetails.connect(self.show_details)
        self.library.rescanClicked.connect(self.start_scan)
        self.library.pathsClicked.connect(self.manage_paths)
        self.library.editRequested.connect(self.edit_item)
        self.library.favChanged.connect(self.toggle_fav)
        self.library.themeChanged.connect(self.apply_theme)
        self.library.randomRequested.connect(self.random_pick)
        self.library.smartPlayRequested.connect(self.smart_play)
        self.details.backRequested.connect(self._back_to_library)
        self.details.changed.connect(self.refresh_library)
        # cover changed/removed from details → invalidate that card's poster cache
        self.details.coverChanged.connect(self._on_cover_changed)
        self.library.subcatChanged.connect(self.change_subcat)
        self.library.itemsChanged.connect(self.refresh_library)
        self.details.playRequested.connect(self.play_media)
        self._lib_dirty = False

        # central playback tracker — independent of pages; nothing is lost when a page/app closes
        self.tracker = watchtime.PlayTracker(self.store, rel_of)
        self.tracker.on_update = self._on_watch_tracked
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(700)   # faster tick — registering/switching episodes should feel immediate
        self._track_timer.timeout.connect(self._track_poll)
        self._last_lib_ping = 0.0

        # cache size limit (if the user set a cap) — after the UI is up, on a separate thread
        QTimer.singleShot(2500, self._enforce_cache_limit)

        # watch the database save-failure flag — SQLite failures no longer stay silent;
        # the user sees "could not save data" in the status bar
        self._save_warn_shown = False
        self._save_watch = QTimer(self)
        self._save_watch.setInterval(10000)
        self._save_watch.timeout.connect(self._check_save_failed)
        self._save_watch.start()

        # Navigation via mouse side buttons (◀ Back / ▶ Forward) — a browser-style
        # history of "library(category) ↔ details(title)"; the extra connection to sidebar
        # selection runs *after* the library's internal handler (connection order).
        self._nav = NavHistory()
        self._nav_replay = False
        self._nav.record(("lib", "all", "", ""))
        self.library.cats.currentItemChanged.connect(self._nav_on_side)
        _app47 = QApplication.instance()
        if _app47 is not None:
            _app47.installEventFilter(self)

        # Keyboard shortcuts — a complement to the mouse side buttons.
        # Auto-disabled in text fields (_sc_focus_guard) so typing isn't disrupted.
        try:
            from PySide6.QtGui import QKeySequence as _KS48, QShortcut as _SC48
            _SC48(_KS48("Alt+Left"), self, activated=lambda: self._sc_nav(True))
            _SC48(_KS48("Alt+Right"), self, activated=lambda: self._sc_nav(False))
            _SC48(_KS48(Qt.Key.Key_Space), self, activated=self._sc_space)
            _SC48(_KS48("W"), self, activated=self._sc_watched)
            _SC48(_KS48("F"), self, activated=self._sc_fav)
        except Exception as _exc_dbg:
            dbg_log(f"__init__ failed: {type(_exc_dbg).__name__}")
            pass

        # "Smart auto-backup" state — the file is rewritten only when something
        # actually changed (the store.data_rev/cfg_rev counters).
        self._ab_last_rev = -1
        self._ab_last_cfg_rev = -1
        self._ab_running = False
        self._ab_last_t = 0.0
        self._ab_pending = False          # enable pending until the prebuilder finishes
        self._pb_active = False           # is the thumbnail prebuilder running?

    # ------------------------------------------------------ Mouse navigation
    def _nav_push(self, state):
        if getattr(self, "_nav_replay", False):
            return
        try:
            self._nav.record(state)
        except Exception as _exc_dbg:
            dbg_log(f"_nav_push failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_on_side(self, *_a):
        """Category picked in the sidebar — only when the library is visible and not replaying."""
        if getattr(self, "_nav_replay", False):
            return
        try:
            if self.stack.currentWidget() is not self.library:
                return
            self._nav.record(("lib", self.library.filter_kind,
                              self.library.filter_cat, ""))
        except Exception as _exc_dbg:
            dbg_log(f"_nav_on_side failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_go(self, back: bool):
        try:
            state = self._nav.back() if back else self._nav.forward()
        except Exception as _exc_dbg:
            dbg_log(f"_nav_go failed: {type(_exc_dbg).__name__}")
            state = None
        if state:
            self._nav_apply(state)

    def _nav_apply(self, state):
        """Apply a history state — without recording it again (replay flag)."""
        page, kind, cat, item_path = state
        self._nav_replay = True
        try:
            if page == "det" and item_path:
                target = None
                for it in (self.items or []):
                    if str(getattr(it, "path", "")) == item_path:
                        target = it
                        break
                if target is not None:
                    # set_item tears down the previous item's queues itself
                    if self.stack.currentWidget() is not self.details \
                            or self.details.item is not target:
                        self.show_details(target)      # recording suppressed by the replay flag
                    return
                # item no longer in the current scan (deleted/moved) → library
                page, kind, cat = "lib", "all", ""
            if self.stack.currentWidget() is not self.library:
                self._back_to_library()                  # the deferred refresh is handled inside it too
            self._select_side_filter(kind, cat)
        finally:
            self._nav_replay = False

    def _select_side_filter(self, kind: str, cat: str):
        """Select the sidebar node matching (kind, cat); with no match → the "🏠 All" node."""
        try:
            tree = self.library.cats
            node = None
            it = self.library._tree_iter()
            while (li := it.value()) is not None:
                if li.data(0, Qt.ItemDataRole.UserRole) == (kind, cat):
                    node = li
                    break
                it += 1
            if node is None and tree.topLevelItemCount():
                node = tree.topLevelItem(0)
            if node is not None:
                tree.setCurrentItem(node)
        except Exception as _exc_dbg:
            dbg_log(f"_select_side_filter failed: {type(_exc_dbg).__name__}")
            pass

    def _nav_busy_context(self) -> bool:
        """Is a modal dialog open (backup/settings/prompts)? → the buttons must do nothing."""
        try:
            app = QApplication.instance()
            return bool(app is not None and app.activeModalWidget() is not None)
        except Exception as _exc_dbg:
            dbg_log(f"_nav_busy_context failed: {type(_exc_dbg).__name__}")
            return False

    def eventFilter(self, obj, event):
        """Global consumption of the mouse side buttons (XButton1/XButton2).

        Acts on Press; Release is consumed too so the widget under the cursor doesn't
        interpret the event again. No intervention while a modal dialog is open."""
        et = event.type()
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            xb = getattr(Qt.MouseButton, "BackButton", None)
            xf = getattr(Qt.MouseButton, "ForwardButton", None)
            try:
                b = event.button()
            except Exception as _exc_dbg:
                dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
                b = None
            if b is not None and xb is not None and b in (xb, xf):
                if et is QEvent.Type.MouseButtonRelease:
                    return True
                if not self._nav_busy_context():
                    if b is xb and self._nav.can_back():
                        self._nav_go(True)
                    elif b is xf and self._nav.can_forward():
                        self._nav_go(False)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------ Shortcuts
    def _sc_focus_guard(self) -> bool:
        """Shortcuts must not interfere while the user is typing."""
        try:
            from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit
            w = QApplication.focusWidget()
            return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))
        except Exception as _exc_dbg:
            dbg_log(f"_sc_focus_guard failed: {type(_exc_dbg).__name__}")
            return False

    def _sc_nav(self, back: bool):
        if self._nav_busy_context():
            return
        if back and self._nav.can_back():
            self._nav_go(True)
        elif not back and self._nav.can_forward():
            self._nav_go(False)

    def _sc_space(self):
        """Space on the details page = smart play (like the ▶ button)."""
        if self._sc_focus_guard():
            return
        if self.stack.currentWidget() is self.details:
            self.details.play_smart()

    def _sc_watched(self):
        """W on the details page = mark all episodes watched ↔ clear the state."""
        if self._sc_focus_guard() or self.stack.currentWidget() is not self.details:
            return
        item = self.details.item
        if item is None:
            return
        try:
            # Smart: videos if present — otherwise the other watchable files
            # (book/compressed manga/image/…); subtitles never count toward the total.
            vids = item_watchables(item)
        except Exception as _exc_dbg:
            dbg_log(f"_sc_watched failed: {type(_exc_dbg).__name__}")
            vids = []
        if not vids:
            return
        done, _part = self.store.watch_progress(item)   # returns (watched, in-progress)
        total = len(vids)                               # the real total = watchables on disk
        make = not (total > 0 and done >= total)
        for f in vids:
            self.store.watch_set(item, rel_of(item, f),
                                 Store.WATCH_DONE if make else 0)
        self.refresh_library()
        try:
            for f in vids:
                self.details.refresh_watch(rel_of(item, f))
        except Exception as _exc_dbg:
            dbg_log(f"_sc_watched failed: {type(_exc_dbg).__name__}")
            pass
        self.statusBar().showMessage(
            T("✓ همه‌ی قسمت‌های «{0}» دیده‌شده ثبت شد").format(self.store.title(item))
            if make else
            T("○ وضعیت تماشای «{0}» پاک شد").format(self.store.title(item)), 7000)

    def _sc_fav(self):
        """F on the details page = favorite ↔ unfavorite."""
        if self._sc_focus_guard() or self.stack.currentWidget() is not self.details:
            return
        if self.details.item is not None:
            self.details._toggle_fav()

    # ------------------------------------- Smart auto-backup
    def _maybe_auto_backup(self, trigger: str = "", force: bool = False):
        """The backup file is rewritten only when data/settings actually changed
        ("only touch the file when something changed"). Lightweight
        (no cache), atomic, on a background thread; it never locks the UI.
        force=True (option just enabled / a pending prebuild finished) skips the
        revision check and the per-minute cap so a fresh file is written immediately —
        even if nothing has changed since it was enabled."""
        try:
            cfg = self.store.auto_backup_cfg()
            path = str(cfg.get("path") or "")
            if not cfg.get("enabled") or not path or self._ab_running:
                return
            rev = (self.store.data_rev, self.store.cfg_rev)
            unchanged = rev == (self._ab_last_rev, self._ab_last_cfg_rev)
            # the exit trigger used to BYPASS the revision check —
            # despite the "only when something changed" promise, closing the
            # app rewrote the whole.mcbak every single time. Exit is now
            # exempt only when no backup ran yet this session (first-run
            # safety), otherwise the same revision check as the other
            # triggers applies.
            if not force and unchanged and self._ab_last_t != 0.0:
                return
            if not force and trigger != "exit" and unchanged:
                return
            now = time.monotonic()
            if not force and trigger != "exit" and now - self._ab_last_t < 60:
                return                                   # at most once per minute
            self._ab_last_t = now
            self._ab_last_rev, self._ab_last_cfg_rev = rev
            self._ab_running = True

            def _job48(_p=path, _inc=bool(cfg.get("include_cache"))):  # noqa: B008 — intentional snapshot at definition time
                try:
                    import backup as _bk48
                    # The user's own choice — simple (no cache) or full
                    # (with thumbnails); the customary default = simple/light.
                    _bk48.export_backup(_p, include_cache=_inc, atomic=True)
                    try:
                        self._pb_relay.text.emit(T("🗄 پشتیبانِ خودکار به‌روزرسانی شد ✔"))
                    except Exception as _exc_dbg:
                        dbg_log(f"_maybe_auto_backup._job48 failed: {type(_exc_dbg).__name__}")
                        pass
                except Exception as e:
                    try:
                        watchtime._dbg(f"auto-backup error: {type(e).__name__}")
                    except Exception as _exc_dbg:
                        dbg_log(f"_maybe_auto_backup._job48 failed: {type(_exc_dbg).__name__}")
                        pass
                finally:
                    self._ab_running = False

            threading.Thread(target=_job48, daemon=True).start()
        except Exception as _exc_dbg:
            dbg_log(f"_maybe_auto_backup failed: {type(_exc_dbg).__name__}")
            self._ab_running = False

    def _queue_auto_backup(self, force: bool = False):
        """Enable/re-path auto-backup from Settings. If the thumbnail prebuilder
        is running, the write waits for it to finish; otherwise the file is
        written now."""
        try:
            self._ab_pending = bool(self._ab_pending or force)
            if self._pb_active or not self._ab_pending:
                return
            self._ab_pending = False
            self._maybe_auto_backup("enable", force=True)
        except Exception as _exc_dbg:
            dbg_log(f"_queue_auto_backup failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------- Missing items under the roots
    def _check_missing_items(self):
        """Items that live under the current roots but whose files are gone — the
        "delete / new path" dialog. Only while the window is visible
        (never pop up in invisible tests) and at most once per run.

        three fixes — 1) the roots go through expandvars (scan_roots
        scans the expanded form, so the comparison must match); 2) a root whose
        base folder is currently not reachable (unplugged USB / offline drive)
        is skipped entirely — plugging out a drive used to declare hundreds of
        titles "missing"; 3) the per-item os.path.exists probe runs on a
        background thread instead of the UI thread (large libraries froze the
        window for seconds)."""
        try:
            if not self.isVisible():
                return
            if getattr(self, "_miss_shown_run", False):
                return
            self._miss_shown_run = True
            import os as _os48
            roots = []
            for r in self.store.roots():
                p = str(r.get("path") or "").strip()
                if p:
                    roots.append(_os48.path.expandvars(p))
            if not roots:
                return
            # Only roots that are readable right now participate — an offline
            # drive's items are "not scanned", not "missing".
            live_roots = [r for r in roots if _os48.path.isdir(r)]
            if not live_roots:
                return

            def _under48(p, r):
                p_ = _os48.path.normcase(str(p).replace("/", "\\"))
                r_ = _os48.path.normcase(str(r).replace("/", "\\")).rstrip("\\")
                return p_ == r_ or p_.startswith(r_ + "\\")

            def _probe():
                miss = []
                items = list((self.store.data.get("items") or {}).keys())
                for p in items:
                    try:
                        if any(_under48(p, r) for r in live_roots) \
                                and not _os48.path.exists(p):
                            miss.append(p)
                    except OSError:
                        continue
                # Back to the UI thread via the existing relay signal
                if miss:
                    self._pb_relay.text.emit("\x00missing-items\x00"
                                             + "\x00".join(miss))
            threading.Thread(target=_probe, daemon=True,
                             name="missing-items-probe").start()
        except Exception as _exc_dbg:
            dbg_log(f"_check_missing_items failed: {type(_exc_dbg).__name__}")
            self._miss_shown_run = False
            pass

    def _open_missing_dialog(self, miss: list):
        try:
            from ui.dialogs import MissingItemsDialog
            before = len((self.store.data.get("items") or {}))
            MissingItemsDialog(self.store, miss, self).exec()
            after = len((self.store.data.get("items") or {}))
            if after != before:
                self.statusBar().showMessage(T("تغییراتِ موارد گمشده اعمال شد ✔"), 6000)
                if self.worker is None or not self.worker.isRunning():
                    self.start_scan()                # resync the library with the deletions/moves
        except Exception as _exc_dbg:
            dbg_log(f"_open_missing_dialog failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ Tracked playback
    def play_media(self, item, f):
        """Play a video with the configured player + automatically record the watch result.

        Flow: resolve player ← saved position ← launch ← register with PlayTracker.
        Later results (live/final) arrive via _on_watch_tracked.
        """
        f = Path(f)
        if f.suffix.lower() not in VIDEO_EXTS:
            open_path(f)
            return
        if self.tracker.has_media(f):
            self.statusBar().showMessage(
                T("▶ «{0}» همین‌الان هم در حال پخش است").format(f.name), 6000)
            return
        self.store.add_recent(str(item.path))            # feeds 🕐 "Recent"
        player = watchtime.resolve_player(self.store.player_cfg)
        rel = rel_of(item, f)
        mode0, pos = self.store.watch_get(item, rel)
        if mode0 == Store.WATCH_DONE:
            pos = 0.0                    # already watched → start from the beginning
        # The duration must be "this file's" — never the whole series' total!
        # (Old bug: when the file's duration was unknown we fell back to the item's
        # total_duration, so e.g. an episode 100% watched logged "20 minutes watched".)
        dur = self.details._dur_known.get(str(f)) or 0
        if dur <= 0:
            v_dstore = self.dstore.get(f)         # None=not computed · -1=unknown
            if v_dstore and v_dstore > 0:
                dur = v_dstore
        if dur <= 0:
            # quick header probe of this very file (a few ms for MKV/MP4/AVI/WMV)
            v_probe = fast_duration(f)
            if v_probe and v_probe > 0:
                dur = v_probe
                try:
                    self.dstore.set(f, v_probe)
                    self.dstore.save()
                except Exception as _exc_dbg:
                    dbg_log(f"play_media failed: {type(_exc_dbg).__name__}")
                    pass
        if dur <= 0:
            dur = 0.0                              # unknown — percentage rules come from the player instead
        kind = player["kind"]
        note = (player.get("note") or "")
        prefix = (note + " · ") if note else ""
        name = Path(player["path"]).name if player.get("path") else ""

        wp = None
        track_label = ""
        if kind == "mpv" and player.get("path"):
            wp = watchtime.launch_mpv(player["path"], f, pos, self.cache.cache_dir)
            track_label = T("ردیابی زنده و دقیق فعال است — موقعیت هر چند ثانیه ذخیره می‌شود")
        elif kind == "potplayer" and player.get("path"):
            wp = watchtime.launch_potplayer(player["path"], f, pos)
            track_label = (T("ردیابی واقعی فعال است — موقعیت از خود پنجره‌ی PotPlayer "
                           "خوانده می‌شود (سیک هم لحاظ است)"))
        elif kind == "vlc" and player.get("path"):
            wp = watchtime.launch_vlc(player["path"], f, pos)
            track_label = T("ردیابی تخمینی فعال است (زمان پخش مداوم) — برای دقت کامل mpv/PotPlayer")
        elif kind == "elapsed" and player.get("path"):
            wp = watchtime.launch_elapsed(player["path"], f, pos)
            track_label = T("ردیابی تخمینی فعال است (زمان پخش مداوم)")

        if wp is not None:
            self.tracker.add(wp, item, rel, dur, kind)
            self._track_timer.start()
            resume_txt = (T(" · ادامه از {0}").format(format_duration(pos)) if pos > 5 else "")
            self.statusBar().showMessage(
                prefix + T("▶ پخش «{0}» با {1}{2} — {3}").format(f.name, name, resume_txt, track_label), 8000)
            return
        # ---- No tracking: the system default player ----
        self.statusBar().showMessage(
            prefix + T("▶ پخش با پخش‌کننده‌ی پیش‌فرض (بدون ردیابی) — برای ردیابی خودکار، "
            "mpv یا PotPlayer را در «⚙ تنظیمات» انتخاب کن"), 8000)
        open_path(f)

    def _track_poll(self):
        if not self.tracker.poll():
            self._track_timer.stop()

    def _on_watch_tracked(self, entry, kind: str, result):
        """Central-tracker feedback: refresh the details UI (if it's the same item) + the library."""
        if self.details.item is not None and self.details.item.path == entry.item.path:
            self.details.refresh_watch(entry.rel)
        if kind == "final":
            mode, pos = result
            if mode == Store.WATCH_DONE:
                msg = T("✓ «{0}» دیده‌شده ثبت شد").format(entry.proc.media.name)
            else:
                msg = (T("◐ «{0}» تا {1} ذخیره شد — پخش بعدی از همین‌جا ادامه می‌یابد").format(entry.proc.media.name, format_duration(pos)))
            self.statusBar().showMessage(msg, 8000)
            self.refresh_library()            # deferred if details is open (no hang)
        elif kind == "switch":
            self.statusBar().showMessage(
                T("⏭ پلیر رفت سراغ «{0}» — ردیابی روی قسمت جدید ادامه دارد").format(Path(result).name), 8000)
            if self.details.item is not None and self.details.item.path == entry.item.path:
                self.details.refresh_watch(entry.rel)
        else:
            # refresh library cards during live playback — at most every ~30 seconds
            now = time.monotonic()
            if now - self._last_lib_ping >= 30:
                self._last_lib_ping = now
                self.refresh_library()

    # ------------------------------------------------------------ Theme
    def apply_theme(self, name: str):
        self.store.set_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_theme(name))
        try:
            from ui.widgets import set_poster_bg     # poster background matches the theme
            set_poster_bg(theme_bg(name))
        except Exception as _exc_dbg:
            dbg_log(f"apply_theme failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # Card letterbox color is baked into the pixmap at *build* time in _pm_cache;
            # without clearing the cache, cards keep the old theme's color band after a
            # theme switch.
            # Rebuilding preserves the scroll position (ui/library.py).
            self.library.clear_pixmap_cache()
        except Exception as _exc_dbg:
            dbg_log(f"apply_theme cache-clear failed: {type(_exc_dbg).__name__}")
            pass
        combo = self.library.theme_combo          # sync the sidebar combo
        idx = combo.findData(name)
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self.status_lbl.setText(T("🎨 پوسته‌ی «{0}» اعمال شد").format(T(theme_label(name))))

    # ------------------------------------------------------------ Navigation
    def show_details(self, item):
        self.details.set_item(item)
        self.stack.setCurrentWidget(self.details)
        self.store.add_recent(str(item.path))             # feeds 🕐 "Recent"
        self._nav_push(("det", "", "", str(item.path)))   # navigation history

    def random_pick(self, item):
        """The "What to watch tonight?" button — details of that item."""
        self.show_details(item)

    def smart_play(self, item):
        """Play directly from the library page (best target: in-progress → unwatched → first)."""
        self.show_details(item)
        QApplication.processEvents()
        self.details.play_smart()

    # ------------------------------------------------------------ Navigation/refresh
    def _back_to_library(self):
        # Cancel the abandoned page's background work — "the app keeps chewing
        # on that folder even after I leave it". Without this cancel, the old folder's
        # thumbnail/duration queues kept eating CPU/disk inside the library.
        try:
            self.details.cancel_pending()
        except Exception as _exc_dbg:
            dbg_log(f"_back_to_library failed: {type(_exc_dbg).__name__}")
            pass
        self.stack.setCurrentWidget(self.library)
        if self._lib_dirty:               # changes made while in details are applied now
            self.refresh_library()
        # record in nav history (with the same category filter the user had)
        self._nav_push(("lib", self.library.filter_kind, self.library.filter_cat, ""))

    def refresh_library(self):
        """Rebuild the library — deferred while the details page is the visible one.

        If the details page is open (the user can't see the library), the rebuild is
        postponed until returning; that way saving live/final watch state or updating
        descriptions never stalls the UI (it used to freeze for a few
        seconds after closing the player").
        """
        if self.stack.currentWidget() is self.details:
            self._lib_dirty = True
            return
        self._lib_dirty = False
        self.library.set_items(self.items)

    def _on_cover_changed(self, item):
        """Cover changed/removed from the details page.

        Details refreshes itself; here only *that* item's poster cache is invalidated
        (previously no news reached the library and the card kept showing the old
        cover) and the rebuild is deferred until returning — same policy as
        refresh_library."""
        try:
            self.library.invalidate_item_poster(item.path)
        except Exception as _exc_dbg:
            dbg_log(f"_on_cover_changed failed: {type(_exc_dbg).__name__}")
            pass
        if self.stack.currentWidget() is self.details:
            self._lib_dirty = True             # rebuilt in _back_to_library
        else:
            self.refresh_library()

    def _check_save_failed(self):
        """Don't let a database save failure pass silently — the status bar announces it.

        store.save_data toggles the flag; this only shows a one-time message per
        failure episode (the read_only_fallback pattern)."""
        failed = bool(getattr(self.store, "save_failed", False))
        if failed and not self._save_warn_shown:
            self._save_warn_shown = True
            self.statusBar().showMessage(
                T("⚠ ذخیره‌ی اطلاعات ممکن نشد — تغییرات فعلاً فقط در حافظه‌اند"), 8000)
        elif not failed:
            self._save_warn_shown = False

    # ------------------------------------------------------------ Cache size limit
    def _on_cache_pruned(self, removed: int):
        """After the cache is shrunk by the size cap — invalidate the thumbnail
        manifests so the prebuilder rebuilds the removed files on the next run and no
        missing thumbnail hides behind "smart skipping"."""
        if not removed:
            return
        try:
            self.store.clear_thumb_manifests()
            watchtime._dbg("cache-limit: thumb manifests invalidated")
        except Exception as e:
            watchtime._dbg(f"cache-limit manifest clear error: {type(e).__name__}")

    def _enforce_cache_limit(self):
        """Enforce the cache size cap + prune the previous scale generation — on another thread."""
        limit = self.store.cache_max_mb()
        # A display-scale change alters the cache key, stranding the previous generation —
        # once per boot (marked with the previous boot's scale) the old files are pruned
        if self.cache_px:
            self._prune_scale_generation_async()
        if not limit:
            return
        from thumbs import enforce_cache_size_limit

        def job():
            try:
                freed, removed = enforce_cache_size_limit(self.cache.cache_dir, limit)
                if removed:
                    watchtime._dbg(
                        f"cache-limit: {removed} files removed, {freed} bytes freed (cap {limit}MB)")
                    try:
                        self.cache_pruned.emit(removed)      # → UI thread
                    except Exception as _exc_dbg:
                        dbg_log(f"_enforce_cache_limit.job failed: {type(_exc_dbg).__name__}")
                        pass
            except Exception as e:
                watchtime._dbg(f"cache-limit error: {type(e).__name__}")

        threading.Thread(target=job, daemon=True).start()

    def _prune_scale_generation_async(self) -> None:
        """prune_scale_generation on a background thread — prunes only when the scale changed."""
        def job():
            try:
                from thumbs import prune_scale_generation
                freed, removed = prune_scale_generation(self.cache.cache_dir,
                                                        self.cache_px)
                if removed:
                    watchtime._dbg(f"cache scale-gen prune: {removed} files, "
                                   f"{freed} bytes (px was different)")
                    try:
                        self.cache_pruned.emit(removed)
                    except Exception as _exc_dbg:
                        dbg_log(f"_prune_scale_generation_async.job failed: {type(_exc_dbg).__name__}")
                        pass
            except Exception as _exc_dbg:
                dbg_log(f"_prune_scale_generation_async.job failed: {type(_exc_dbg).__name__}")

        threading.Thread(target=job, daemon=True, name="cache-scale-prune").start()

    # ------------------------------------------------------------ Clean relaunch
    def request_restart(self):
        """A "clean" relaunch — exactly the behavior of a normal exit/full reset.

        Importing a backup once left the old app open and opened a second
        one. Cause: the relaunch ran from inside a modal dialog (nested event
        loops of the progress bar/settings) and app.quit didn't shut everything down.
        Now we only set a flag, close every window, and the real relaunch happens in
        closeEvent — *after* the threads are stopped and the databases are closed.
        """
        self._restart_after_import = True
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                for w in app.topLevelWidgets():
                    if w is not self and w.isVisible():
                        try:
                            w.close()
                        except Exception as _exc_dbg:
                            dbg_log(f"request_restart failed: {type(_exc_dbg).__name__}")
                            pass
        except Exception as _exc_dbg:
            dbg_log(f"request_restart failed: {type(_exc_dbg).__name__}")
            pass
        self.close()                # → closeEvent: cleanup + relaunch + watchdog

    # ------------------------------------------------------------ Settings
    def manage_paths(self):
        prev_theme = self.store.theme
        prev_ab48 = self.store.auto_backup_cfg()    # detect enable/path change
        dlg = SettingsDialog(self.store, self, self.cache.cache_dir, durations=self.dstore)
        if dlg.exec():
            _ab48 = self.store.auto_backup_cfg()
            if (_ab48.get("enabled")
                    and (not prev_ab48.get("enabled")
                         or str(_ab48.get("path") or "") != str(prev_ab48.get("path") or ""))):
                # Just enabled → build the file right now from all available data;
                # if the prebuilder is running, wait for it to finish.
                self._queue_auto_backup(force=True)
            watchtime.invalidate_player_cache()   # the player may have changed
            self.library.clear_pixmap_cache()     # and the image cache may have been wiped
            self._enforce_cache_limit()           # apply the new cache cap right away
            if self.store.theme != prev_theme:
                self.apply_theme(self.store.theme)

            # Hitting save used to stutter/freeze the UI for a few seconds — the heavy
            # post-save work (scan/library rebuild/prebuild) runs one event-loop turn later so
            # the settings dialog closes and the page repaints first — no more multi-second
            # pauses are visible on the UI thread.
            def _after_settings_save():
                # The language change is saved, and for the full application
                # (texts + RTL/LTR layout) a clean restart is offered — the same safe
                # restart mechanism.
                try:
                    import i18n as _i45
                    import hwinfo as _hw45
                    _hw45.set_perf_mode(self.store.perf_mode)
                    if self.store.language != _i45.lang():
                        from PySide6.QtWidgets import QMessageBox as _MB45
                        _a45 = _MB45.question(
                            self, T("تغییر زبان"),
                            T("برای اعمالِ کاملِ زبان جدید برنامه باید دوباره راه‌اندازی شود.")
                            + "\n\n" + T("الان راه‌اندازی مجدد شود؟"))
                        if _a45 == _MB45.StandardButton.Yes:
                            self.request_restart()
                            return
                except Exception as _exc_dbg:
                    dbg_log(f"manage_paths._after_settings_save failed: {type(_exc_dbg).__name__}")
                    pass
                if dlg.paths_changed:
                    # Scan only if no scan is already running (the app's first startup path scans
                    # right after manage_paths itself — no double scan)
                    if self.worker is None or not self.worker.isRunning():
                        self.start_scan()         # paths/fallback/durations changed → needed
                else:
                    # general changes (player/theme/categories) apply without a scan
                    if dlg.cats_changed:
                        self.refresh_library()
                    # Turning on "prebuild thumbnails" should take effect now — the user expects
                    # it to start even without a rescan (report: "the option doesn't work"; before
                    # this, it only started after a fresh scan).
                    if self.store.prebuild_thumbs and self.items \
                            and not self.prebuilder.running:
                        self.status_lbl.setText(T("⚙ تنظیمات ذخیره شد ✓ · ⚡ پیش‌ساخت تامبنیل شروع شد…"))
                        self.prebuilder.start(self.items)
                    else:
                        self.status_lbl.setText(T("⚙ تنظیمات ذخیره شد — بدون اسکن مجدد ✓"))

            from PySide6.QtCore import QTimer as _QT43
            _QT43.singleShot(0, _after_settings_save)

    # ------------------------------------------------------------ Scan
    def start_scan(self, first: bool = False):
        if not self.store.roots():
            QMessageBox.information(
                self, T("شروع کار 🎬"),
                T("هنوز مسیری اضافه نشده.\nحالا پنجره‌ی مسیرها باز می‌شود — پوشه‌هایت را اضافه کن."),
)
            self.manage_paths()
            if not self.store.roots():
                return
        # On first run, show only the splash until the scan ends (no half-built window)
        if first and not self.isVisible():
            self.splash = SplashScreen()
            self.splash.setStatus(T("در حال اسکن کتابخانه…"))
            self.splash.show()
        try:
            self.prebuilder.stop()          # stop the stale scan's prebuilder
        except Exception as _exc_dbg:
            dbg_log(f"start_scan failed: {type(_exc_dbg).__name__}")
            pass
        try:
            if self.duration_worker is not None:   # cancel the stale scan's duration worker
                self.duration_worker.stop()
                self.duration_worker = None
        except Exception as _exc_dbg:
            dbg_log(f"start_scan failed: {type(_exc_dbg).__name__}")
            pass
        self.pb_chip.hide()                 # hide the abandoned prebuilder bar
        self.library.set_busy(True)
        self._scan_t0 = time.monotonic()
        self._scan_track.update(phase="", t0=0.0)
        self.scan_chip.reset(100, T("🔍 در حال شمارش عنوان‌ها…"))
        self.scan_chip.show()
        self.status_lbl.setText(T("در حال اسکن… ⏳"))
        self.worker = ScanWorker(self.store, self.cache, self.dstore, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.counts.connect(self._on_scan_counts)
        self.worker.finished_items.connect(self.on_scan_finished)
        self.worker.start()

    def _on_relay_text(self, msg: str):
        """Relay-slot router : prebuild progress goes to the status
        bar; the missing-items probe (a background thread that must not touch
        widgets) uses a NUL-marker prefix to request the dialog."""
        try:
            if msg.startswith("\x00missing-items\x00"):
                miss = [p for p in msg.split("\x00missing-items\x00", 1)[1]
.split("\x00") if p]
                QTimer.singleShot(1500, lambda: self._open_missing_dialog(miss))
                return
        except Exception as _exc_dbg:
            dbg_log(f"_on_relay_text failed: {type(_exc_dbg).__name__}")
        self.status_lbl.setText(msg)

    def _on_progress(self, msg: str):
        self.status_lbl.setText(msg)
        if self.splash:
            self.splash.setStatus(msg)

    # ------------------------------------------------------------ Elapsed/ETA text
    @staticmethod
    def _eta_parts(track: dict, phase: str, done: int, total: int):
        """(elapsed, estimated remaining) — the timer resets automatically on phase change.

        Show "how long it took and how long is left" on the
        progress bar; remaining is estimated from the phase's average rate
        (elapsed/done).
        """
        now = time.monotonic()
        if track.get("phase") != phase or not track.get("t0"):
            track["phase"] = phase
            track["t0"] = now
        elapsed = now - track["t0"]
        remaining = None
        if done > 0 and total > done:
            remaining = elapsed / done * (total - done)
        return elapsed, remaining

    def _prog_text(self, label: str, track: dict, phase: str,
                   done: int, total: int) -> str:
        el, rem = self._eta_parts(track, phase, done, total)
        text = T("{0} {1} از {2} · گذشته {3}").format(label, fa(done), fa(total), fa_mmss(el))
        if rem is not None:
            text += T(" · مانده ~{0}").format(fa_mmss(rem))
        return text

    def _on_scan_counts(self, phase: str, done: int, total: int):
        """The scan's real counting bar — "scanning n of m items" / "video durations"."""
        if total <= 0:
            return
        # The explicit "computing durations" text on the status bar
        label = T("🔍 اسکن") if phase == "scan" else T("⏱ در حال محاسبه‌ی زمان")
        text = self._prog_text(label, self._scan_track, phase, done, total)
        self.scan_chip.update(done, total, text)
        if self.scan_chip.isHidden():
            self.scan_chip.show()

    def _on_prebuild_counts(self, phase: str, done: int, total: int):
        """The prebuilder's numeric progress bar — "n of m · elapsed · remaining" in the status bar.

        The "done" phase hides the bar (the final summary with the total time stays on
        the status bar itself); the other phases show the exact live count.
        """
        ui = self.pb_chip
        self._pb_active = not (phase == "done" or total <= 0)   # prebuilder state
        if phase == "done" or total <= 0:
            ui.hide()

            # Thumbnails finished → auto-backup; if a fresh enable was pending on the prebuilder's
            # completion, the forced write happens here.
            def _pb_backup48():
                if self._ab_pending:
                    self._ab_pending = False
                    self._maybe_auto_backup("prebuild", force=True)
                else:
                    self._maybe_auto_backup("prebuild")

            QTimer.singleShot(1200, _pb_backup48)
            return
        label = {"check": T("🔎 بررسی"),                              # fingerprint phase
                 "posters": T("⚡ پوستر"),
                 "archives": T("📦 آرشیو"),
                 "videos": T("🎬 ویدیو"),
                 "heavy": T("⚡ سنگین‌ها")}.get(phase, T("⚡ تامبنیل"))   # distinct phases
        text = self._prog_text(label, self._pb_track, phase, done, total)
        ui.update(done, total, text)
        if ui.isHidden():
            ui.show()

    def on_scan_finished(self, payload):
        items, durations, scan_status = payload
        self.items = list(items)
        self.worker = None
        if durations is not None:
            totals, manifests = durations
            self.store.set_total_durations(totals, manifests)
        # Orphaned recents (moved/deleted paths) are pruned after every full scan so the
        # count next to 🕐 "Recent" matches reality.
        # Runs before set_items so the fresh sidebar shows the pruned count. If the library
        # is empty (offline drive/failed scan) nothing is pruned — a temporary failure must
        # not destroy history.
        # Recents orphan cleanup after each full scan, with the offline-root
        # guard: prune only against the roots THIS scan
        # actually read (scan_status["ok"]); recents of failed/missing roots
        # are protected so a temporarily offline drive keeps its history.
        ok_roots = [str(p) for p in (scan_status or {}).get("ok", [])]
        if self.items and ok_roots:
            try:
                okn = [Store.norm_path(r) for r in ok_roots]
                prot = [p for p in self.store.recents()
                        if not _path_under_any(p, okn)]
                self.store.prune_recents([str(i.path) for i in self.items],
                                         protected=prot)
            except Exception as _exc_dbg:
                dbg_log(f"prune_recents failed: {type(_exc_dbg).__name__}")
        self.library.set_items(self.items)
        self.library.set_busy(False)
        self.scan_chip.hide()
        if self.splash:
            self.splash.close()
            self.splash = None
            self.show()
            self.raise_()
            self.activateWindow()
        # "Posters/app first, then durations in the background" — the window is
        # shown *before* duration computation starts; durations are computed by a dedicated
        # worker (skipping unchanged folders) while the status bar shows "computing
        # durations…".
        self._start_duration_worker()
        dur_note = (T(" · ⏱ محاسبه‌ی زمان خاموش است")
                    if not self.store.compute_durations
                    else (T(" · ⏱ زمان‌ها در پس‌زمینه…") if self.items else ""))
        # the exact scan time stays on the status bar so the user sees how long it took
        scan_el = time.monotonic() - self._scan_t0 if self._scan_t0 else 0.0
        scan_el_txt = f" · ⏱ {fa_mmss(scan_el)}" if scan_el >= 0.2 else ""
        self.status_lbl.setText(
            T("✅ {0} عنوان یافت شد{1}{2}").format(fa(len(self.items)), scan_el_txt, dur_note))
        # User option: "after adding a path/scanning, prebuild all thumbnails" — a dedicated
        # low-pressure thread; it does no duplicate work (same shared disk cache).
        if self.store.prebuild_thumbs and self.items:
            self.prebuilder.start(self.items)
        # items under the roots whose files are gone → the "delete/new path" dialog
        self._check_missing_items()
        # Orphaned covers in .covers/ (deleted items / leftovers of replaced covers from
        # older versions) are collected after every full scan — not on every cover change,
        # since correct collection needs a full meta pass.
        self._cover_gc_async()

    def _cover_gc_async(self) -> None:
        """collect_garbage on a background thread — files with no meta references are removed.

        References come from the database itself (store.cover_refs), not from the
        current scan's items — a temporarily offline root (separate drive/network down)
        isn't in the scan, so its custom covers would look "orphaned" and get deleted
        (data loss).
        """
        try:
            refs: set[str] = self.store.cover_refs()
        except Exception as _exc_dbg:
            dbg_log(f"_cover_gc_async refs failed: {type(_exc_dbg).__name__}")
            return

        def job():
            try:
                from covers import collect_garbage
                # The store is passed in so references are re-read under the Store lock before each
                # unlink — the outer snapshot may be stale by the time the thread finishes, and a
                # re-referenced cover would be deleted
                removed = collect_garbage(refs, store=self.store)
                if removed:
                    watchtime._dbg(
                        f"cover-gc: {removed} orphan cover file(s) removed")
            except Exception as _exc_dbg:
                dbg_log(f"_cover_gc_async.job failed: {type(_exc_dbg).__name__}")

        threading.Thread(target=job, daemon=True, name="cover-gc").start()

    def _start_duration_worker(self):
        """The background durations worker — a still-running old one is cancelled."""
        try:
            if self.duration_worker is not None:
                self.duration_worker.stop()
                self.duration_worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass
        self.duration_worker = None
        try:
            self.prebuilder.set_durations_busy(False)   # always at the decision point
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass
        if not self.store.compute_durations or not self.items:
            return
        self.duration_worker = DurationWorker(self.store, self.dstore, self.items, self)
        self.duration_worker.progressed.connect(self._on_progress)
        self.duration_worker.counts.connect(self._on_scan_counts)
        self.duration_worker.totals_ready.connect(self._on_durations_ready)
        self.duration_worker.start()
        # The prebuilder's heavy phases wait for the duration wave to finish (on an
        # external drive two overlapping disk-read waves multiply the I/O bottleneck)
        try:
            self.prebuilder.set_durations_busy(True)
        except Exception as _exc_dbg:
            dbg_log(f"_start_duration_worker failed: {type(_exc_dbg).__name__}")
            pass

    def _on_durations_ready(self, payload):
        self.duration_worker = None
        try:
            self.prebuilder.set_durations_busy(False)   # heavy phases are free
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            pass
        if not payload:
            return
        totals, manifests = payload
        try:
            self.store.set_total_durations(totals, manifests)
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            return
        try:
            self.library.refresh_durations()     # card text refreshes in place
        except Exception as _exc_dbg:
            dbg_log(f"_on_durations_ready failed: {type(_exc_dbg).__name__}")
            pass
        self.scan_chip.hide()
        # if the prebuilder is running it writes its own text — don't override it
        if not self.prebuilder.running:
            self.status_lbl.setText(
                T("✅ {0} عنوان · ⏱ محاسبه‌ی زمان تمام شد").format(fa(len(self.items))))

    # ------------------------------------------------------------ Edit / favorites
    def edit_item(self, item):
        dlg = ItemEditDialog(item, self.store, self)
        # Cover changes inside the dialog apply immediately; if the user then hits Cancel,
        # the library/details must not show the old cover — the coverChanged path notifies
        # independently of accept/reject.
        try:
            dlg.coverTouched.connect(self._on_cover_changed)
        except Exception as _exc_dbg:
            dbg_log(f"edit_item coverTouched failed: {type(_exc_dbg).__name__}")
        if dlg.exec():
            self.library.clear_pixmap_cache()   # the custom poster may have changed
            self.refresh_library()
            if self.stack.currentWidget() is self.details:
                self.details.set_item(item)

    def toggle_fav(self, item):
        self.store.set_meta(item.path, favorite=not self.store.fav(item.path))
        fav = self.store.fav(item.path)
        # update that card in place — no grid rebuild and no scroll jump
        if self.stack.currentWidget() is self.library and self.library.update_item_card(item):
            self.library.rebuild_sidebar()
            self.library._update_stats()
        else:
            self.refresh_library()
        self.status_lbl.setText(
            (T("⭐ «{}» به علاقه‌مندی‌ها رفت") if fav else T("☆ «{}» از علاقه‌مندی حذف شد"))
            .format(self.store.title(item)))

    def change_subcat(self, item, cat: str):
        """Card/header quick box: switch between the main category and subcategories, no dialog."""
        self.store.set_category(item.path, cat)
        # first try an in-place card update (no jump/rebuild — fast)
        if self.stack.currentWidget() is self.library and self.library.update_item_card(item):
            self.library.rebuild_sidebar()
            self.library._update_stats()
        else:
            sb = self.library.scroll.verticalScrollBar()
            pos = sb.value()
            self.refresh_library()
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(0, lambda: sb.setValue(pos))   # after the re-layout
        if self.stack.currentWidget() is self.details and self.details.item is item:
            self.details.set_item(item)
        self.status_lbl.setText(
            f"📁 «{self.store.title(item)}» → {cat.replace('/', ' › ')}")

    def closeEvent(self, event):
        """App close: save still-open playback positions, stop the scan safely, flush caches."""
        try:
            _app47x = QApplication.instance()
            if _app47x is not None:
                _app47x.removeEventFilter(self)   # don't let the filter call into a dead object
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._track_timer.stop()
            self.tracker.shutdown()
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # last chance for auto-backup — only if something actually changed
            self._maybe_auto_backup("exit")
            _tab48 = time.monotonic()
            while self._ab_running and time.monotonic() - _tab48 < 10:
                # A pure sleep wait froze the window for up to 10 seconds (Windows wrote "Not
                # responding" after 5 seconds and users hit End task) — the event loop stays alive
                # while waiting.
                try:
                    _app48 = QApplication.instance()
                    if _app48 is not None:
                        _app48.processEvents()
                except Exception as _exc_dbg:
                    dbg_log(f"closeEvent pump failed: {type(_exc_dbg).__name__}")
                    pass
                time.sleep(0.05)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            if self.duration_worker is not None and self.duration_worker.isRunning():
                self.duration_worker.stop()      # duration worker exits cleanly
                self.duration_worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # If a scan is running we don't wait for a safe finish (it can take a while) — forced
            # termination only at app-exit time, with no lasting side effects.
            # a bounded graceful wait comes FIRST — terminate() on a
            # thread holding a SQLite connection used to be the very first
            # resort and could corrupt the write in progress.
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(2500)
                if self.worker.isRunning():
                    self.worker.terminate()
                    self.worker.wait(1500)
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.prebuilder.stop()       # the thumbnail prebuilder thread stops at the first safe point
            self.prebuilder.wait(8.0)    # pools must be drained BEFORE the DBs close
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.library.shutdown()      # stop the poster loader thread cleanly
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            # Disconnect signals + cancel thumbnail/header/duration queues — fixes the exit error
            # "RuntimeError: Signal source has been deleted" (exit-time error)
            self.details.shutdown()
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.dstore.close()          # flush dirty SQLite state + close the connection
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self.store.close()           # final flush of the data database
        except Exception as _exc_dbg:
            dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
            pass
        super().closeEvent(event)
        # "Shut it down properly, like a full reset" — the backup-import relaunch
        # happens only through this clean path — i.e. *after* all threads are stopped and the
        # databases are closed; the fresh process sees healthy data.
        if event.isAccepted() and getattr(self, "_restart_after_import", False):
            try:
                import sys as _sys
                from portable import app_dir as _appd
                from ui.dialogs import _relaunch_app as _rl
                if getattr(_sys, "frozen", False):
                    _rl([_sys.executable])
                else:
                    _rl([_sys.executable, str(_appd() / "main.py")])
            except Exception as _exc_dbg:
                dbg_log(f"closeEvent failed: {type(_exc_dbg).__name__}")
                pass
            # Hard-exit watchdog: if lingering background threads or nested loops still make the
            # exit incomplete, the old process is force-killed within a few seconds so two open
            # instances are never seen.
            _arm_exit_watchdog()


def load_font(app: QApplication):
    font_file = APP_DIR / "assets" / "Vazirmatn-Regular.ttf"
    if not font_file.exists():                    # exe mode — inside the _MEIPASS bundle
        from portable import resource_path as _rpf45
        font_file = _rpf45("assets/Vazirmatn-Regular.ttf")
    if font_file.exists():
        QFontDatabase.addApplicationFont(str(font_file))
    font = QFont()
    font.setFamilies(["Vazirmatn", "Segoe UI", "Tahoma"])
    font.setPointSize(10)
    app.setFont(font)


def _write_log(line: str):
    """One line in the app's log (next to main.py)."""
    try:
        with open(APP_DIR / "folderdex-error.log", "a", encoding="utf-8") as f:
            import datetime as _dt
            f.write(f"\n[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
    except Exception as _exc_dbg:
        dbg_log(f"_write_log failed: {type(_exc_dbg).__name__}")
        pass


def _disable_window_ghosting():
    """Windows: turn off "ghost/white windows" (Window Ghosting).

    When an app doesn't reach its message loop for a few seconds, Windows replaces
    its window with a white "Not Responding" window with the same title — exactly
    the "white screens with the app's title" that had been reported. With this
    call, a momentary heavy spot no longer produces a ghost window.
    (The root fix is in place too: heavy work was moved off the UI thread.)
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.DisableProcessWindowsGhosting()
    except Exception as _exc_dbg:
        dbg_log(f"_disable_window_ghosting failed: {type(_exc_dbg).__name__}")
        pass


def _writable_error_log_path() -> Path:
    """Error-log path — goes to %TEMP% when the app folder is read-only.

    Previously the logging itself raised on the unwritable folder and excepthook
    fell through to sys.__excepthook__ — in --windowed mode the user saw nothing
    (the root of "the app won't open").
    """
    probe = APP_DIR / f".wprobe-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return APP_DIR / "folderdex-error.log"
    except OSError as _exc_dbg:
        dbg_log(f"error log in APP_DIR not writable: {type(_exc_dbg).__name__}")
    import tempfile
    try:
        return Path(tempfile.gettempdir()) / "folderdex-error.log"
    except Exception as _exc_dbg:
        dbg_log(f"error log temp failed: {type(_exc_dbg).__name__}")
        return APP_DIR / "folderdex-error.log"


def _install_excepthook():
    """Log unexpected errors to the log file (crucial for console-less pythonw mode)."""
    import traceback

    def hook(exc_type, exc, tb):
        try:
            with open(_writable_error_log_path(), "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 60 + "\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception as _exc_dbg:
            dbg_log(f"_install_excepthook.hook failed: {type(_exc_dbg).__name__}")
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


class _PhantomWindowGuard(QObject):
    """Unknown-window guard — hunts the mysterious "white screens" that were reported.

    Every top-level widget that opens and is not one of the app's known windows
    (main window, dialogs, menus/tooltips/popups) is logged (class, size, title,
    time) and gets a proper title/icon; this both diagnoses the issue and keeps
    the worst case presentable.
    """

    _OK_TYPES = (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                 Qt.WindowType.SplashScreen, Qt.WindowType.Tool)

    def __init__(self):
        super().__init__()
        self.seen: list[str] = []           # for in-app tests/troubleshooting

    def eventFilter(self, obj, event):
        try:
            if (event.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and obj.isWindow()):
                if isinstance(obj, (QMainWindow, QDialog, QMenu)):
                    return False
                # compare only the window "type" (the WindowType bit mask, not bit-by-bit)
                if (obj.windowFlags() & Qt.WindowType.WindowType_Mask) in self._OK_TYPES:
                    return False
                desc = (f"phantom-window: {type(obj).__name__} "
                        f"title={obj.windowTitle()!r} "
                        f"size={obj.width()}x{obj.height()}")
                self.seen.append(desc)
                _write_log(desc)
                if not obj.windowTitle():
                    obj.setWindowTitle(T("FolderDex"))
                ic = QApplication.instance().windowIcon()
                if not ic.isNull():
                    obj.setWindowIcon(ic)
        except Exception as _exc_dbg:
            dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
            pass
        return False


def _hide_all_consoles():
    """Windows: no console/white-flash window from any subprocess.

    All of our direct calls already pass CREATE_NO_WINDOW; this global patch
    guarantees that any future subprocess (or third-party library) stays silent
    too. GUI players (mpv/PotPlayer/…) naturally show their own windows.
    """
    if sys.platform != "win32":
        return
    import subprocess as _sp
    _orig_popen = _sp.Popen

    def _guarded(*args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
        return _orig_popen(*args, **kwargs)

    _sp.Popen = _guarded


def _install_qt_msg_filter():
    """Filter cosmetic Qt-internal warnings — "errors show up when closing/running".

    Two known, harmless warnings whose origin is inside Qt itself (not our code —
    all of our code paths were normalized earlier): "QFont::setPointSize: Point
    size <= 0" (when a px-based QSS font is overridden somewhere as point-based)
    and "libpng iCCP" (a broken color profile in some PNGs of the user's own
    library, read directly by Qt — e.g. in the cover-pick preview). These no
    longer print to the console but remain in the debug file so the trail
    isn't lost.
    """
    from PySide6.QtCore import qInstallMessageHandler

    def _handler(mode, ctx, msg):
        try:
            if ("setPointSize: Point size <= 0" in msg
                    or ("libpng warning" in msg and "iCCP" in msg)):
                try:
                    watchtime._dbg(f"qt-note suppressed: {msg}")
                except Exception as _exc_dbg:
                    dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
                    pass
                return
        except Exception as _exc_dbg:
            dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
            pass
        try:
            sys.stderr.write(msg + "\n")
        except Exception as _exc_dbg:
            dbg_log(f"_install_qt_msg_filter._handler failed: {type(_exc_dbg).__name__}")
            pass

    qInstallMessageHandler(_handler)


def main() -> int:
    _install_excepthook()
    _hide_all_consoles()
    _disable_window_ghosting()
    watchtime.set_debug_log(APP_DIR / "folderdex-debug.log")
    _install_qt_msg_filter()
    # The "full reset" button: if a reset was requested in the previous
    # run, it happens here — before any database/config is opened.
    try:
        _fr37 = _paths.factory_reset_if_pending()
        if _fr37:
            watchtime._dbg(T("factory reset: پاک‌سازی کامل انجام شد ({0} مقصد)").format(len(_fr37)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    # Export/import: if a backup restore was requested in the previous
    # run, the staged files are rewritten over the originals here — before any database
    # is opened.
    try:
        import backup as _bk39
        _im39 = _bk39.apply_pending_import()
        if _im39:
            watchtime._dbg(T("import backup: بازیابی انجام شد ({0} مورد)").format(len(_im39)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    config_path = CONFIG_PATH
    argv = sys.argv[1:]
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            config_path = Path(argv[i + 1]).resolve()

    # Bilingual UI: the language is resolved **before any widget is built**
    # so the layout (RTL/LTR) and texts are right from the start.
    # The migration rule lives in exactly one place: i18n.lang_from_config.
    import i18n as _i18n
    try:
        import json as _json45
        _raw45 = _json45.loads(Path(config_path).read_text(encoding="utf-8"))
        _i18n.set_lang(_i18n.lang_from_config(_raw45))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        _i18n.set_lang("en")

    app = QApplication(sys.argv)
    app.setApplicationName("FolderDex")
    # any untitled window's title/taskbar should show the app name instead of "Python"
    app.setApplicationDisplayName(app_title())
    # app-wide icon (not just the main window) so no stray window gets the Python icon
    from portable import resource_path as _rpath45
    _icon = _rpath45("assets/icon.png")
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if _i18n.is_rtl()
        else Qt.LayoutDirection.LeftToRight)
    load_font(app)

    # unknown-window guard (root-causing the reported "white screens")
    guard = _PhantomWindowGuard()
    app.installEventFilter(guard)
    app._phantom_guard = guard          # keep it alive (prevent garbage collection)

    # "Even yes/no should be Persian": localize the standard Qt buttons
    # (QMessageBox/QInputDialog) — all boxes, including static ones.
    _i18n.install_std_button_localizer(app)

    # Safety (public release): if the imported config.json was not applied because the
    # safe wipe (fail-closed) failed, tell the user explicitly — silence here would mean
    # an unnoticed partial import. The language is now definitive (store.language).
    try:
        _skipped_imports = _bk39.skipped_imports()
        if _skipped_imports:
            from PySide6.QtWidgets import QMessageBox
            if "config.json" in _skipped_imports:
                # The whole imported config.json was rejected (fail-closed —
                # it could not be sanitized safely)
                QMessageBox.warning(
                    None,
                    _i18n.T("ورود اطلاعات"),
                    _i18n.T("این بخش از پشتیبان وارد نشد:\n{0}\n\nفایلِ config.jsonِ داخلِ پشتیبان سالم/امن نبود؛ تنظیماتِ فعلیِ شما دست‌نخورده ماند.").format(", ".join(_skipped_imports)))
            else:
                # Honesty fix: partial skips (player / auto_backup /
                # network roots / colors) are NOT "config untouched" — say
                # exactly which sensitive parts were dropped for security.
                QMessageBox.information(
                    None,
                    _i18n.T("ورود اطلاعات"),
                    _i18n.T("این بخش‌های حساس از پشتیبان وارد نشدند (دلایلِ امنیتی):\n{0}\n\nبقیه‌ی اطلاعات (دیتابیس، وضعیت تماشا، کاورها) به‌طور کامل وارد شد؛ بعداً می‌توانی این موارد را خودت در تنظیمات دوباره تعریف کنی.").format(", ".join(_skipped_imports)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass

    store = None
    try:
        store = Store(config_path)
    except Exception as e:
        # The Store construction sat OUTSIDE the startup error
        # handling — a read-only app folder crashed the process silently and
        # the helpful "why" dialog never appeared.
        dbg_log(f"Store init failed: {type(e).__name__}")
        try:
            QMessageBox.critical(
                None, T("FolderDex"),
                T("برنامه نتوانست شروع کند — احتمالاً پوشه‌ی برنامه قابلِ نوشتن نیست.\n\nبرنامه را به یک پوشه‌ی قابلِ نوشتن (مثلاً Desktop یا Documents) منتقل و دوباره اجرا کنید.\nجزئیات در folderdex-error.log (اگر پوشه فقط‌خواندنی باشد در پوشه‌ی Temp ویندوز)."))
        except Exception as _exc_dbg:
            dbg_log(f"fatal-error dialog failed: {type(_exc_dbg).__name__}")
        return 1
    # a quarantined corrupt config.json gets the same honest notice
    # the database quarantine already had.
    try:
        _qc49 = getattr(store, "quarantined_config", None)
        if _qc49:
            QMessageBox.warning(
                None, T("تنظیمات بازیابی شد"),
                T("فایلِ تنظیمات (config.json) قابلِ خواندن نبود و کنارِ برنامه به\n\"{0}\" منتقل شد.\n\nبرنامه با تنظیماتِ پیش‌فرض بالا آمد؛ فهرستِ پوشه‌ها و دسته‌ها را دوباره اضافه کنید،\nیا مسیرِ فایلِ منتقل‌شده را برای بازیابیِ دستی بررسی کنید.").format(_qc49[0]))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    # Definitive language from the Store (with old-config migration) + final direction —
    # with no widgets built yet, fixing the direction here is free and complete.
    _i18n.set_lang(store.language)
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if _i18n.is_rtl()
        else Qt.LayoutDirection.LeftToRight)
    # Performance mode + saved disk-type detection results (stable worker counts
    # across runs)
    try:
        import hwinfo as _hw45
        _hw45.set_perf_mode(store.perf_mode)
        _hw45.load_kinds(store.disk_kinds)
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    # After importing a backup the database was once malformed and the app would not start — if a
    # corrupt database was quarantined and recreated in _open_db, tell the user once what
    # happened (file names only — no paths) and how to recover.
    try:
        _q41 = getattr(store, "db_quarantined", None)
        if _q41:
            QMessageBox.information(
                None, T("بازیابی پس از خرابی دیتابیس"),
                T("دیتابیسِ قبلی آسیب دیده بود (خطای malformed که دیدی) و خودکار قرنطینه شد:\n{0}\n\nبرنامه با دیتابیسِ تازه بالا آمد؛ تنظیمات و فهرستِ پوشه‌ها از config.json حفظ شده‌اند. وضعیتِ تماشا و دسته‌ها را می‌توانی از تنظیمات با «📥 واردکردن اطلاعات» و فایلِ پشتیبانِ ‎.mcbak‎ برگردانی — نسخه‌ی جدید پیش از اعمال، سلامتِ دیتابیس را آزمون می‌کند (quick_check) و اگر فایل سالم نباشد هیچ تغییری نمی‌دهد.").format(T('، ').join(_q41)))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    app.setStyleSheet(build_theme(store.theme))
    try:
        from ui.widgets import set_poster_bg       # in sync with the startup theme
        set_poster_bg(theme_bg(store.theme))
    except Exception as _exc_dbg:
        dbg_log(f"main failed: {type(_exc_dbg).__name__}")
        pass
    try:
        w = MainWindow(store)
    except Exception as e:
        # Silent startup death — the most common worst case is a read-only app folder
        # (Program Files / a locked USB stick). The user must know "why"; the log also goes
        # to %TEMP% when the folder is read-only.
        dbg_log(f"MainWindow init failed: {type(e).__name__}")
        try:
            QMessageBox.critical(
                None, T("FolderDex"),
                T("برنامه نتوانست شروع کند — احتمالاً پوشه‌ی برنامه قابلِ نوشتن نیست.\n\nبرنامه را به یک پوشه‌ی قابلِ نوشتن (مثلاً Desktop یا Documents) منتقل و دوباره اجرا کنید.\nجزئیات در folderdex-error.log (اگر پوشه فقط‌خواندنی باشد در پوشه‌ی Temp ویندوز)."))
        except Exception as _exc_dbg:
            dbg_log(f"fatal-error dialog failed: {type(_exc_dbg).__name__}")
        return 1
    if store.roots():
        # scan in the background + splash; the window is shown after loading
        w.start_scan(first=True)
    else:
        w.show()
        w.start_scan()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
