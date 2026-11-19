# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""The main library page: sidebar (search, sorting, category tree) + card grid.

Subcategories: categories can nest with "/" (e.g. "Anime/Watched").
Selecting a parent includes all of its subcategories.
"""
from __future__ import annotations

from i18n import T, fix_en_singular

import os
import time
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import (
    QMutex, QMutexLocker, QThread, Qt, QTimer, QWaitCondition, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from .dialogs import NoWheelComboBox

from media_info import format_duration, format_duration_short
from scanner import fa, fa_norm, natural_key, TYPE_ORDER

# Rank of the dominant content type, for the "content type" sort option
_TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}
from store import Store
from thumbs import ThumbnailCache
from qss import THEMES, theme_label
from filethumbs import (
    FileThumbs, load_cache_pixmap, load_cache_qimage, poster_png_fallback,
)
from .widgets import (
    CARD_H, CARD_W, CardWidget, FlowLayout, open_path,
    placeholder_poster, poster_letterbox_image, poster_needs_letterbox,
    poster_pixmap, POSTER_H, POSTER_W,
)
from watchtime import dbg_log

SORTS = ["نام", "نوع محتوا", "مدت زمان", "حجم", "تعداد قسمت‌ها", "تعداد فایل‌ها", "تاریخ تغییر", "امتیاز"]   # personal rating
ALL_KEY, FAV_KEY = "__all__", "__fav__"

POSTER_RADIUS = 12


def _poster_dbg(msg: str):
    """Log poster-build errors to the debug log — for tracing "thumbnail didn't show up" cases."""
    try:
        from watchtime import _dbg
        _dbg(msg)
    except Exception as _exc_dbg:
        dbg_log(f"_poster_dbg failed: {type(_exc_dbg).__name__}")
        pass


def safe_ref(p) -> str:
    """Anonymous reference for logs — watchtime index."""
    try:
        from watchtime import safe_ref as _sr
        return _sr(p)
    except Exception as _exc_dbg:
        dbg_log(f"safe_ref failed: {type(_exc_dbg).__name__}")
        return ".#?"


def _job_rank(job) -> int:
    """Light-to-heavy ordering for the initial poster queue: icon/image ← video ← PDF ← archive.
    (archive-only folders always ended up last in the queue.)"""
    _p, s, v, d, a = (list(job) + [""] * 5)[:5]
    if s:
        return 0
    if v:
        return 2
    if d:
        return 3
    if a:
        return 4
    return 1


def _rounded_image(img: QImage, w: int, h: int, radius: int) -> QImage:
    """Center-crop + rounded corners on a *QImage* — unlike QPixmap, allowed off the UI thread."""
    scaled = img.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
)
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(path)
    p.drawImage(-x, -y, scaled)
    p.end()
    return out


class PosterWorker(QThread):
    """Asynchronous loader for the library grid's posters.

    The heavy work (finding/generating the cache PNG, decoding the image from disk,
    cropping and rounding corners) all happens on this thread — the UI thread never
    waits on disk, so Windows never builds a "white/ghost" window for the app.
    QImage (unlike QPixmap) is safe for off-UI-thread processing; conversion to
    QPixmap and display happen on the main thread once the signal arrives.

    For items without an image the fallback chain continues: a real video thumbnail
    from inside the item (Windows shell or ffmpeg).
    """

    # note: mtime_ns overflows the Qt signal's int32 capacity — object keeps it intact
    loaded = Signal(int, str, str, object, object)   # generation, item path, png path, mtime_ns, QImage

    # Several concurrent workers inside this thread — posters used to be built
    # single-threaded, one by one, and a few-hundred-item library filled in slowly
    # Queue order matches display order, so visible cards are still
    # built first. The worker count scales with the CPU (4 to 10) — poster building
    # is more I/O and subprocess than raw computation, so multi-core machines can
    # use even more.
    WORKERS = max(4, min(10, (os.cpu_count() or 4)))
    # Slow lane: building a poster from inside a "cold" archive (no member cache) takes
    # seconds; several of these used to occupy the whole 8–10-thread pool and the plain
    # posters in view waited behind them (archive-only folders got no
    # thumbnail"). Cold archive jobs now go to a small separate lane so free threads
    # always remain for fast work.
    SLOW_MAX = max(1, min(3, WORKERS // 3))

    def __init__(self, store: Store, cache: ThumbnailCache, fthumbs, parent=None,
                 cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # effective cache size (from the display scale) — poster-chain calls use this px
        self.cache_px = int(cache_px)
        self.fthumbs = fthumbs          # FileThumbs — for the fallback video thumbnail
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._jobs: deque = deque()
        self._gen = 0
        self._stop = False

    def enqueue(self, jobs, gen: int):
        """Add new jobs + publish the current generation (older-generation jobs are dropped)."""
        with QMutexLocker(self._mutex):
            self._gen = gen
            self._jobs.extend(jobs)
            self._cond.wakeAll()

    def prioritize(self, paths, gen: int):
        """Cards in the user's view move to the front of the queue (relative order kept).

        Key to fixing "the bottom of the list gets no thumbnails until you scroll from
        the top": instead of a fixed top→bottom order, the queue is re-sorted on every
        scroll."""
        paths = set(paths or ())
        with QMutexLocker(self._mutex):
            if not self._jobs or gen != self._gen:
                return
            pri = [j for j in self._jobs if j and j[0] in paths]
            if not pri:
                return
            rest = [j for j in self._jobs if not (j and j[0] in paths)]
            self._jobs = deque(pri + rest)
            self._cond.wakeAll()

    def stop(self):
        """Stop at app exit — invalidate queued AND in-flight jobs immediately.

        Bumping the generation makes every already-submitted poster build go
        stale the moment _build_one looks at it, so no new heavy work (video
        frame extraction / archive listing) starts after the close click and
        the interpreter does not linger in the pool-exit join."""
        with QMutexLocker(self._mutex):
            self._stop = True
            self._gen += 1
            self._jobs.clear()
            self._cond.wakeAll()

    def _is_slow_job(self, job) -> bool:
        """Is this job a "cold archive" poster build? (No image cache and no member cache.)"""
        try:
            j = tuple(job) + ("",) * (5 - len(job))
            arc = j[4]
            if not arc:
                return False
            from pathlib import Path as _P
            ft = self.fthumbs
            p = _P(arc)
            if ft._cache_path(p, "archive", self.cache_px).exists():
                return False
            bad = ft._cache_path(p, "archive", self.cache_px).with_suffix(".bad")
            if bad.exists():
                return False            # negative marker ⇒ the probe fails instantly (fast)
            return ft._arc_pick_get(p) is None
        except Exception as _exc_dbg:
            dbg_log(f"_is_slow_job failed: {type(_exc_dbg).__name__}")
            return False

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        pool = ThreadPoolExecutor(max_workers=self.WORKERS,
                                  thread_name_prefix="poster")
        slow_pool = ThreadPoolExecutor(max_workers=self.SLOW_MAX,
                                       thread_name_prefix="poster-arc")
        futs: set = set()
        parked: list = []              # cold archive jobs waiting for a slow-lane slot
        try:
            while True:
                # Fill free slots immediately — previously the whole batch (WORKERS jobs) was waited
                # on at once, and one slow archive poster (a multi-second 7z/rar) held up the output
                # of the other 9 workers, stalling the whole list.
                self._mutex.lock()
                gen = self._gen
                slow_busy = sum(1 for f in futs if getattr(f, "_mc_slow", False))
                # parked slow-lane jobs run as soon as a slot opens; stale generations are dropped
                while parked and slow_busy < self.SLOW_MAX and not self._stop:
                    pg, pjob = parked.pop(0)
                    if pg != gen:
                        continue                        # stale rebuild — discard
                    fu = slow_pool.submit(self._build_one, pg, pjob)
                    fu._mc_slow = True
                    futs.add(fu)
                    slow_busy += 1
                fast_busy = len(futs) - slow_busy
                while self._jobs and fast_busy < self.WORKERS and not self._stop:
                    job = self._jobs.popleft()
                    slow = self._is_slow_job(job)
                    if slow:
                        # slow lane capped at its own limit — remaining cold archives park until
                        # later so they never delay the fast jobs in the user's view.
                        if slow_busy >= self.SLOW_MAX:
                            parked.append((gen, job))
                            continue
                        slow_busy += 1
                    else:
                        fast_busy += 1
                    fu = (slow_pool if slow else pool).submit(
                        self._build_one, gen, job)
                    fu._mc_slow = slow
                    futs.add(fu)
                stop = self._stop
                self._mutex.unlock()
                if stop:
                    return
                if not futs:
                    self._mutex.lock()
                    while not self._jobs and not parked and not self._stop:
                        self._cond.wait(self._mutex)
                    self._mutex.unlock()
                    continue
                done, _pending = wait(futs, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        f.result()
                    except Exception as _exc_dbg:
                        dbg_log(f"run failed: {type(_exc_dbg).__name__}")
                        pass
                futs -= done
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            slow_pool.shutdown(wait=False, cancel_futures=True)

    def _emit_fail(self, gen: int, item_path: str, why: str):
        """Officially report failure (no silence!) so the page can retry with backoff.

        Previously any stage failure (e.g. a temporary antivirus lock on the file or a
        stuck 7z worker) was swallowed silently and the card stayed on its letter
        placeholder forever, while the details page opened the same folder and built
        the poster fine."""
        if gen != self._gen:
            return
        _poster_dbg(f"poster fail [{why}]: {safe_ref(item_path)}")
        try:
            self.loaded.emit(gen, item_path, "", 0, None)
        except RuntimeError:
            pass                # app is closing — the signal source is gone

    def _build_one(self, gen: int, job):
        """Build one poster (inner pool threads) — guarded so one bad file can't kill the queue."""
        if gen != self._gen or self._stop:          # stale rebuild or app closing — drop it
            return
        job = tuple(job) + ("",) * (6 - len(job))   # tolerate older tuple layouts
        item_path, src, vid, pdf, arc, force_flag = job
        force = str(force_flag) == "1"      # scheduled retry: bypass negative caches
        try:
            # this is a UI request — prebuild phases respect it
            FileThumbs.ui_request_tick()
            # Full chain, moving past the failed stage: icon ← video ← PDF ← archive
            # (if the video is broken and yields no thumbnail, PDF and then the archive are tried)
            png = poster_png_fallback(self.cache.get, self.fthumbs,
                                      src, vid, pdf, arc, self.cache_px, force=force)
            if not png:
                self._emit_fail(gen, item_path, "chain empty")
                return
            try:
                mt = Path(png).stat().st_mtime_ns
            except OSError:
                mt = 0
            qimg = load_cache_qimage(png)       # WebP cache — native Qt decoder
            if qimg is None or qimg.isNull():
                self._emit_fail(gen, item_path, f"decode {png}")
                return
            # Squared icon (mismatched aspect) → letterbox with a blurred backdrop;
            # near-matching ratio → the traditional cover crop
            if poster_needs_letterbox(qimg.width(), qimg.height(), POSTER_W, POSTER_H):
                img = poster_letterbox_image(qimg, POSTER_W, POSTER_H, POSTER_RADIUS)
            else:
                img = _rounded_image(qimg, POSTER_W, POSTER_H, POSTER_RADIUS)
            if gen == self._gen:
                try:
                    self.loaded.emit(gen, item_path, str(png), mt, img)
                except RuntimeError:
                    pass        # app is closing
        except RuntimeError:
            pass                # dead signal while the app is closing
        except Exception as e:
            dbg_log(f"_build_one failed: {type(e).__name__}")
            try:
                self._emit_fail(gen, item_path, f"error {e!r:.120}")
            except Exception as _exc_dbg:
                dbg_log(f"_build_one failed: {type(_exc_dbg).__name__}")
                pass


class LibraryPage(QWidget):
    openDetails = Signal(object)
    rescanClicked = Signal()
    # HTML export removed — its button and signal went too
    pathsClicked = Signal()
    editRequested = Signal(object)
    favChanged = Signal(object)
    manageCatsClicked = Signal()
    themeChanged = Signal(str)
    randomRequested = Signal(object)
    smartPlayRequested = Signal(object)
    subcatChanged = Signal(object, str)     # (item, new category) — the card's quick subcategory box
    itemsChanged = Signal()                  # watch-state change from the right-click menu

    def __init__(self, store: Store, cache: ThumbnailCache, parent=None,
                 cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # effective cache size (from the Windows display scale) — all poster chains work with it
        self.cache_px = int(cache_px)
        self.items = []
        self.filter_kind = "all"   # all | fav | cat
        self.filter_cat = ""
        # LRU cache of poster pixmaps (key: cache png path + mtime) — category switches
        # avoid re-reading disk, and the cap keeps RAM from growing without bound.
        self._pm_cache: OrderedDict = OrderedDict()
        # item path → ready cache key (so card rebuilds don't need the worker)
        self._item_png: dict[str, tuple] = {}
        # in-flight worker results for an item whose cover changed —
        # rejected in _on_poster_loaded so the old poster can't come back
        self._stale_sources: set[str] = set()
        self._cards_gen = 0                    # card-rebuild generation (invalidates stale jobs)
        import weakref
        self._weakref = weakref
        self._waiting: dict[str, list] = {}    # item path → [weakrefs of cards waiting for a poster]
        self._poster_attempts: dict[str, int] = {}   # failed-attempt count per item (this generation)
        # file/video thumbnails — poster fallback for items without an image
        from filethumbs import FileThumbs
        self._fthumbs = FileThumbs(cache.cache_dir)
        # poster loader thread (lives for the app's lifetime; stopped in closeEvent)
        self._poster_thread = PosterWorker(store, cache, self._fthumbs, self,
                                           cache_px=self.cache_px)
        self._poster_thread.loaded.connect(self._on_poster_loaded)
        self._poster_thread.start()
        # Category meta-cache: build the family map *once* per rebuild, not per card
        # (fixes the O(N²) — the main cause of stalls when switching categories in a big library)
        self._cats_list: list[str] = []
        self._fam_map: dict[str, list[str]] = {}    # root → [root + direct children]
        self._expanded: set | None = None           # tree expansion state (None = default all open)
        # debounced search: each keystroke delays the grid re-render by 180 ms
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.rebuild_cards)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------- Sidebar ----------------
        side = QFrame()
        side.setObjectName("SideBar")
        side.setFixedWidth(260)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 14, 14, 10)
        sl.setSpacing(8)

        # Logo + app name
        brand = QHBoxLayout()
        brand.setSpacing(9)
        logo = QLabel()
        # transparent label background — the generic QWidget rule paints @bg@
        # behind the rounded logo and shows a rectangular halo on the sidebar
        logo.setObjectName("BrandLogo")
        from portable import resource_path as _rpath45    # BUILD/exe: resources come from _MEIPASS
        logo_pm = QPixmap(str(_rpath45("assets/icon.png")))
        if not logo_pm.isNull():
            logo.setPixmap(logo_pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        t1 = QLabel(T("FolderDex"))
        t1.setObjectName("AppTitle")
        from appinfo import APP_NAME, APP_NAME_EN, APP_VERSION
        from i18n import is_rtl as _rtl45
        t2 = QLabel(T("{0} · نسخه‌ی {1}").format(
            APP_NAME if _rtl45() else APP_NAME_EN, fa(APP_VERSION)))
        t2.setObjectName("BrandSub")
        brand_text.addWidget(t1)
        brand_text.addWidget(t2)
        brand.addWidget(logo)
        brand.addLayout(brand_text, 1)
        sl.addLayout(brand)

        self.search = QLineEdit()
        self.search.setPlaceholderText(T("🔍 جستجو در نام، مسیر، توضیحات…"))
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(T("با ی/ک عربی هم پیدا می‌کند (یعنی «سريال» = «سریال»)"))
        self.search.textChanged.connect(self._on_search)
        sl.addWidget(self.search)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(6)
        self.sort_combo = NoWheelComboBox()
        for _s45 in SORTS:                     # the config value stays Persian; display is bilingual
            self.sort_combo.addItem(T(_s45), _s45)
        self.btn_sort_dir = QPushButton("↑")
        self.btn_sort_dir.setObjectName("ModeBtn")
        self.btn_sort_dir.setFixedWidth(38)
        self.btn_sort_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        sort_row.addWidget(QLabel(T("مرتب‌سازی:")))
        sort_row.addWidget(self.sort_combo, 1)
        sort_row.addWidget(self.btn_sort_dir)
        sl.addLayout(sort_row)

        # Restore the saved preference
        sort_cfg = (store.config.get("sort") or {})
        mode = sort_cfg.get("mode", "نام")
        if mode in SORTS:
            _di45 = self.sort_combo.findData(mode)
            if _di45 >= 0:
                self.sort_combo.setCurrentIndex(_di45)
        self.sort_asc = bool(sort_cfg.get("asc", mode == "نام"))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_mode)
        self.btn_sort_dir.clicked.connect(self._toggle_sort_dir)
        self._refresh_sort_dir_btn()

        # The watch-state filter — smart and content-type agnostic
        # (books/manga/archives are covered too). The display label is neutral because
        # "watched" makes no sense for books; the old saved value is kept.
        wf_row = QHBoxLayout()
        wf_row.setSpacing(6)
        self.watch_filter = NoWheelComboBox()
        for _wlbl, _wval in ((T("همه"), "همه"),
                             (T("دیده‌نشده"), "ندیده"),
                             (T("نیمه‌تمام"), "نیمه‌تمام"),
                             (T("کامل دیده‌شده"), "دیده‌شده")):
            self.watch_filter.addItem(_wlbl, _wval)
        _wfc48 = str(store.config.get("watch_filter") or "همه")
        _wi48 = self.watch_filter.findData(_wfc48)
        self.watch_filter.setCurrentIndex(_wi48 if _wi48 >= 0 else 0)
        self.watch_filter.currentIndexChanged.connect(self._on_watch_filter)
        wf_row.addWidget(QLabel(T("نمایش:")))
        wf_row.addWidget(self.watch_filter, 1)
        sl.addLayout(wf_row)

        # Theme selection
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        self.theme_combo = NoWheelComboBox()
        for key in THEMES:
            self.theme_combo.addItem(T(theme_label(key)), key)   # was previously missing — sidebar
        idx = self.theme_combo.findData(store.theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_pick)
        theme_row.addWidget(QLabel(T("پوسته:")))
        theme_row.addWidget(self.theme_combo, 1)
        sl.addLayout(theme_row)

        sl.addWidget(QLabel(T("دسته‌بندی"), objectName="SideLabel"))
        self.cats = QTreeWidget()
        self.cats.setObjectName("CatTree")
        self.cats.setHeaderHidden(True)
        self.cats.setIndentation(18)
        self.cats.setAnimated(True)
        self.cats.currentItemChanged.connect(self._on_cat_item)
        self.cats.itemCollapsed.connect(self._on_tree_fold)
        self.cats.itemExpanded.connect(self._on_tree_fold)
        sl.addWidget(self.cats, 1)

        self.stats = QLabel("")
        self.stats.setObjectName("StatsLabel")
        self.stats.setWordWrap(True)
        sl.addWidget(self.stats)

        self.btn_rescan = QPushButton(T("🔄 اسکن مجدد"))
        self.btn_paths = QPushButton(T("⚙ تنظیمات"))
        self.btn_paths.setObjectName("PrimaryBtn")
        self.btn_paths.setToolTip(T("مسیرها، دسته‌ها، پوسته، پخش‌کننده و تنظیمات کش"))
        self.btn_rescan.clicked.connect(self.rescanClicked)
        self.btn_paths.clicked.connect(self.pathsClicked)
        sl.addWidget(self.btn_paths)
        sl.addWidget(self.btn_rescan)

        # ---------------- Content ----------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(20, 12, 20, 6)
        self.page_title = QLabel(T("همه"))          # the initial label is bilingual too
        self.page_title.setObjectName("PageTitle")
        self.page_sub = QLabel("")
        self.page_sub.setObjectName("PageSub")
        head.addWidget(self.page_title)
        head.addSpacing(10)
        head.addWidget(self.page_sub, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.btn_random = QPushButton(T("🎲 امشب چی ببینم؟"))
        self.btn_random.setObjectName("FlatBtn")
        self.btn_random.setToolTip(
            T("یک عنوان از فیلتر فعلی انتخاب می‌شود؛\n"
            "نیمه‌تمام‌ها شانس بیشتری دارند و کاملاً دیده‌شده‌ها کمتر."))
        self.btn_random.clicked.connect(self._random_dialog)
        head.addWidget(self.btn_random)
        rl.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container.setObjectName("ScrollContent")
        self.flow = FlowLayout(self.container, spacing=14)
        self.flow.setContentsMargins(18, 8, 18, 16)
        self.scroll.setWidget(self.container)
        rl.addWidget(self.scroll, 1)

        # Poster visibility policy ("you have to slowly scroll down from the top
        # for thumbnails to build") — in capped-cache mode only cards in view get jobs; in
        # unlimited mode the queue is re-prioritized on scroll:
        # item path → (job tuple, card weakref) for cards not yet queued
        self._poster_pending: dict[str, tuple] = {}
        # paths whose job was already queued (streaming or geometric) — don't build duplicates
        self._poster_started: set = set()
        # The last sorted/filtered list shown — enables geometric visibility of not-yet-built
        # cards (jumping straight to the bottom of the list must work)
        self._shown_items: list = []
        self._vis_timer = QTimer(self)
        self._vis_timer.setSingleShot(True)
        self._vis_timer.timeout.connect(self._apply_poster_visibility)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_poster_visibility())
        # active-scroll detection — to delay card building during scrolling
        self._scroll_paused = False
        self._scroll_last_val = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(200)
        self._scroll_timer.timeout.connect(self._on_scroll_idle)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_move)

        root.addWidget(side)
        root.addWidget(right, 1)

    # ------------------------------------------------------------ Data
    def set_items(self, items):
        """Fresh items (after a scan/path changes) — one pass: the sidebar rebuilds and it
        in turn triggers the card and stats rebuilds at the end (no duplicated work)."""
        self.items = list(items)
        self.rebuild_sidebar()

    def _counts(self):
        counts, favs = {}, 0
        for it in self.items:
            c = self.store.category_of(it)
            counts[c] = counts.get(c, 0) + 1
            favs += self.store.fav(it.path)
        return counts, favs

    def _total_for(self, prefix: str, counts: dict) -> int:
        return sum(n for c, n in counts.items() if c == prefix or c.startswith(prefix + "/"))

    def _on_tree_fold(self, _item):
        """The user folded/unfolded nodes — update the expanded set explicitly."""
        cur: set = set()
        self._collect_expanded(cur)
        self._expanded = cur

    def _collect_expanded(self, into: set):
        """Collect the tree's currently expanded categories (before rebuilding it)."""
        it = self._tree_iter()
        while (item := it.value()) is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "cat" and item.isExpanded():
                into.add(data[1])
            it += 1

    def rebuild_sidebar(self):
        self._refresh_cat_meta()              # family meta-cache — once for all cards
        counts, favs = self._counts()
        sel_kind, sel_cat = self.filter_kind, self.filter_cat
        self.cats.blockSignals(True)
        # Keep the tree's expansion state so the user doesn't see nodes re-collapsed on every
        # rebuild (and the expensive expandAll runs only on the first build, not every time)
        if self._expanded is None:
            expanded_prev = None
        else:
            expanded_prev = set()
            self._collect_expanded(expanded_prev)
        self.cats.clear()

        def make(text, kind, cat="", color=None, bold=False):
            li = QTreeWidgetItem([text])
            li.setData(0, Qt.ItemDataRole.UserRole, (kind, cat))
            if color:
                li.setForeground(0, QBrush(QColor(color)))
            if bold:
                # copy the tree's real font (not the default QFont with pointSize=-1)
                f = QFont(self.cats.font())
                # QSS-px fonts carry pointSize -1: assigning it as-is makes Qt emit a
                # "setPointSize: Point size <= 0" warning while painting — normalize to points
                if f.pointSizeF() <= 0:
                    f.setPointSizeF(10.0 if f.pixelSize() <= 0 else max(
                        8.0, round(f.pixelSize() * 0.75, 1)))
                f.setBold(True)
                li.setFont(0, f)
            return li

        self.cats.addTopLevelItem(make(T("🏠  همه ({0})").format(fa(len(self.items))), "all", bold=True))
        self.cats.addTopLevelItem(make(T("⭐  علاقه‌مندی‌ها ({0})").format(fa(favs)), "fav", bold=True))
        # 🕐 "Recent" — the most recently played/opened items
        # (naming review settled on "recent")
        # Compare via the normalized path key (normcase/normpath) — it used to be the raw
        # string, so a different spelling of the same path dropped out of the count next to
        # "Recent" (a stale count: it said 15 but there were 9).
        _paths48 = {self.store.norm_path(i.path) for i in self.items}
        _rec48 = [p for p in self.store.recents()
                  if self.store.norm_path(p) in _paths48]
        self.cats.addTopLevelItem(make(T("🕘  موارد اخیر ({0})").format(fa(len(_rec48))), "recent", bold=True))

        nodes: dict[str, QTreeWidgetItem] = {}
        cats = sorted(self.store.categories(), key=natural_key)
        for cat in cats:
            parts = [p for p in cat.split("/") if p.strip()]
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                if sub in nodes:
                    continue
                total = self._total_for(sub, counts)
                from i18n import disp_cat as _dcat45   # the segment's display name
                item = make(f"{_dcat45(parts[i-1])}  ({fa(total)})",
                            "cat", sub, self.store.color(sub),
                            bold=(i == 1))
                if i == 1:
                    self.cats.addTopLevelItem(item)
                else:
                    parent = nodes["/".join(parts[:i - 1])]
                    parent.addChild(item)
                nodes[sub] = item

        # Tree expansion: all open on the first build; later rebuilds preserve the user's state
        if expanded_prev is None:
            self.cats.expandAll()
            self._expanded = None           # means "all open" (the initial default)
        else:
            for sub, node in nodes.items():
                node.setExpanded(sub in expanded_prev)
            self._expanded = expanded_prev

        # Restore the previous selection
        target = None
        it = self._tree_iter()
        while (item := it.value()) is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data == (sel_kind, sel_cat):
                target = item
                break
            it += 1
        if target is None and self.cats.topLevelItemCount():
            target = self.cats.topLevelItem(0)
        if target:
            self.cats.setCurrentItem(target)
        self.cats.blockSignals(False)
        self._on_cat_item(self.cats.currentItem(), None)

    def _tree_iter(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        return QTreeWidgetItemIterator(self.cats)

    def _on_cat_item(self, item, _prev):
        if item:
            self.filter_kind, self.filter_cat = item.data(0, Qt.ItemDataRole.UserRole)
        titles = {"all": T("🏠 همه"), "fav": T("⭐ علاقه‌مندی‌ها"),
                  "recent": T("🕘 موارد اخیر")}
        from i18n import disp_cat as _dcat45
        self.page_title.setText(
            titles.get(self.filter_kind, _dcat45(self.filter_cat).replace("/", " › "))
)
        self.rebuild_cards()
        self._update_stats()          # sidebar stats scoped to the selected category
        self._update_luck_btn()       # the luck button's text/visibility follows the category type too

    # ------------------------------------------------------------ Poster cache (LRU)
    PM_LIMIT = 900

    def _pm_get(self, key):
        pm = self._pm_cache.get(key)
        if pm is not None:
            self._pm_cache.move_to_end(key)      # LRU: recent use → back of the queue
        return pm

    def _pm_put(self, key, pm: QPixmap):
        self._pm_cache[key] = pm
        self._pm_cache.move_to_end(key)
        while len(self._pm_cache) > self.PM_LIMIT:
            try:
                self._pm_cache.popitem(last=False)     # evict the oldest
            except KeyError:
                break

    def clear_pixmap_cache(self):
        """Clear the poster caches (e.g. after editing an item's custom poster).

        An earlier implementation only cleared the caches and bumped
        gen — cards that were waiting on a worker result at that moment (e.g. an
        archive-only folder being built) stayed on their placeholder forever, because
        the in-flight result was rejected by the new gen and the card was no longer in
        the waiting queue. Now a full safe rebuild happens and the scroll position is
        preserved."""
        self._pm_cache.clear()
        self._item_png.clear()
        self._stale_sources.clear()   # a fresh generation re-reads sources — the stale-source guard is no longer needed
        try:
            y = self.scroll.verticalScrollBar().value()
        except Exception as _exc_dbg:
            dbg_log(f"clear_pixmap_cache failed: {type(_exc_dbg).__name__}")
            y = 0
        self.rebuild_cards()
        if y:
            def _restore(v=y):
                try:
                    self.scroll.verticalScrollBar().setValue(v)
                except Exception as _exc_dbg:
                    dbg_log(f"clear_pixmap_cache._restore failed: {type(_exc_dbg).__name__}")
                    pass
            for d in (0, 150, 450, 1000):     # the layout settles in batches — restore over several ticks
                QTimer.singleShot(d, _restore)

    def invalidate_item_poster(self, path) -> None:
        """Invalidate the poster of *one* item — after a cover change/removal from details.

        Unlike clear_pixmap_cache (whole grid + scroll jump), only this item leaves the
        caches and queues: the next rebuild gives the card a fresh job with a fresh
        source, and all other cards stay untouched. The worker's in-flight result for
        this item is also invalidated (_stale_sources) so the old poster can't come
        back."""
        p = str(path)
        self._item_png.pop(p, None)
        self._poster_started.discard(p)     # so a fresh job with a fresh source gets queued
        self._poster_attempts.pop(p, None)
        self._poster_pending.pop(p, None)
        self._stale_sources.add(p)

    def shutdown(self):
        """Stop the poster loader thread cleanly when the app closes."""
        try:
            self._poster_thread.stop()
            self._poster_thread.wait(800)
        except Exception as _exc_dbg:
            dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ Placeholder poster / known
    def _ph_pixmap(self, item) -> QPixmap:
        """Placeholder poster (first letter + category color) — instant, no disk read."""
        cat = self.store.category_of(item)
        letter = self.store.title(item).strip()[:1] or T("؟")
        key = ("ph", letter, self.store.color(cat))
        pm = self._pm_get(key)
        if pm is None:
            pm = placeholder_poster(letter, self.store.color(cat),
                                    POSTER_W, POSTER_H, POSTER_RADIUS)
            self._pm_put(key, pm)
        return pm

    def _known_pm(self, item):
        """Return the item's real poster if already built, else None (the worker builds it)."""
        key = self._item_png.get(str(item.path))
        if key:
            pm = self._pm_get(key)
            if pm is not None:
                return pm
        return None

    def _poster_pixmap(self, item) -> QPixmap:
        """Synchronous poster read — only for one-off uses (the "What to watch tonight?" dialog).

        The library grid doesn't use this path; card posters are asynchronous.
        """
        FileThumbs.ui_request_tick()          # UI request (respected by the prebuilder)
        pm = self._known_pm(item)
        if pm is not None:
            return pm
        src = self.store.poster_of(item, static=True)  # the card grid reads a static frame
        png = poster_png_fallback(
            self.cache.get, self._fthumbs, src,
            getattr(item, "preview_video", None),
            getattr(item, "preview_pdf", None),
            getattr(item, "preview_archive", None), self.cache_px)
        if png:
            try:
                mt = Path(png).stat().st_mtime_ns
            except OSError:
                mt = 0
            key = (str(png), mt)
            pm = self._pm_get(key)
            if pm is None:
                raw = load_cache_pixmap(png)
                if raw is not None and not raw.isNull():
                    pm = poster_pixmap(raw, POSTER_W, POSTER_H, POSTER_RADIUS)
                    self._pm_put(key, pm)
            if pm is not None and not pm.isNull():
                self._item_png[str(item.path)] = key
                return pm
        return self._ph_pixmap(item)

    # ------------------------------------------------------------ Category meta-cache (fixes O(N²))
    def _refresh_cat_meta(self):
        """The category-family map is built once for all cards.

        Previously each card called Store.categories() (O(N) over all item metadata)
        plus child_categories (re-filtering the list) — i.e. O(N²) per rebuild; that
        was the main cause of the multi-second stall when switching categories in a
        large library.
        """
        cats = self.store.categories()
        self._cats_list = cats
        children: dict[str, list[str]] = {}
        for c in cats:
            p = Store.parent_of(c)
            if p is not None:
                children.setdefault(p, []).append(c)
        fam: dict[str, list[str]] = {}
        for base, kids in children.items():
            fam[base] = [base] + kids
        self._fam_map = fam

    def _subcat_view(self, item) -> dict | None:
        """Quick subcategory-change box — O(1) via the meta-cache."""
        cat = self.store.category_of(item)
        base = Store.parent_of(cat) or cat
        options = self._fam_map.get(base)
        if not options or len(options) < 2:
            return None
        labels = {f: (f if f == base else "↳ " + f[len(base) + 1:]) for f in options}
        return {
            "kind": "combo",
            "options": options,
            "labels": labels,
            "current": cat,
            "tip": T("تغییر سریع دسته‌ی این عنوان — دسته‌ی اصلی و همه‌ی زیردسته‌ها"),
        }

    def refresh_durations(self):
        """After the background duration pass, existing cards' info text refreshes
        — no card/poster rebuild and no scroll jump."""
        for card in self.findChildren(CardWidget):
            try:
                card.apply_view(self._view(card.item))
            except Exception as _exc_dbg:
                dbg_log(f"refresh_durations failed: {type(_exc_dbg).__name__}")
                pass

    def _view(self, item) -> dict:
        cat = self.store.category_of(item)
        info = item.info_short
        dur = self.store.total_duration(item)
        if dur:
            info += f" · ⏱ {format_duration_short(dur)}"
        # The card's progress badge follows the category's tracking profile too:
        # "no tracking" gets no ✓/◐ at all; books/comics use
        # 📖, video ✓, images 🖼 — the basket comes from watch_stats_by_kind so a deleted
        # file's record can't fake the badge on the card.
        _st48 = self.store.watch_stats_by_kind(item)
        _b48 = None
        for _k48 in ("video", "book", "image"):     # the displayable dominant kind
            if _st48[_k48][2]:
                if _b48 is None or _st48[_k48][2] > _st48[_b48][2]:
                    _b48 = _k48
        if _b48:
            _d48, _p48, _t48 = _st48[_b48]
            if _d48 or _p48:
                _g48 = {"video": "✓", "book": "📖", "image": "🖼"}[_b48]
                info += f" · {_g48}{fa(min(_d48, _t48))}/{fa(_t48)}"
                if _p48:
                    info += f" ◐{fa(_p48)}"
        _r48 = self.store.rating(item.path)          # personal rating on the card
        if _r48:
            # The star goes first on the line so no info length can clip it.
            info = f"★{fa(_r48)} · " + info
        return {
            "title": self.store.title(item),
            "category": cat,
            "color": self.store.color(cat),
            "info_short": info,
            "desc": self.store.desc(item.path),
            "fav": self.store.fav(item.path),
            # real poster if cached, else instant placeholder — the worker sends the final one
            "poster": self._known_pm(item) or self._ph_pixmap(item),
            "subcat": self._subcat_view(item),
        }

    def _on_theme_pick(self, _i):
        self.themeChanged.emit(self.theme_combo.currentData())

    def _on_watch_filter(self, _i):
        # watch-state filter — saved immediately (the preference is persistent)
        self.store.config["watch_filter"] = str(self.watch_filter.currentData() or "همه")
        try:
            self.store.save_config(_bump=False)
        except TypeError:
            self.store.save_config()
        self.rebuild_cards()
        self._update_stats()

    def _on_sort_mode(self, _i):
        # default direction: name and content type → ascending, the rest → descending
        self.sort_asc = (self.sort_combo.currentData() or self.sort_combo.currentText()) in ("نام", "نوع محتوا")
        self._refresh_sort_dir_btn()
        self._save_sort()
        self.rebuild_cards()

    def _toggle_sort_dir(self):
        self.sort_asc = not self.sort_asc
        self._refresh_sort_dir_btn()
        self._save_sort()
        self.rebuild_cards()

    def _refresh_sort_dir_btn(self):
        self.btn_sort_dir.setText("↑" if self.sort_asc else "↓")
        self.btn_sort_dir.setToolTip(T("صعودی") if self.sort_asc else T("نزولی"))

    def _save_sort(self):
        self.store.config["sort"] = {
            "mode": self.sort_combo.currentData() or self.sort_combo.currentText(),
            "asc": self.sort_asc,
        }
        self.store.save_config()

    def _filtered(self):
        # fa_norm: Arabic yeh/kaf, ZWNJ and diacritics don't matter — a real Persian search
        q = fa_norm(self.search.text().strip())
        # The "Recent" map (for filtering + reverse sorting) and the watch-state filter;
        # the map key is the normalized path — the same key is used in filter and sort
        _rmap48 = ({self.store.norm_path(p): i
                    for i, p in enumerate(self.store.recents())}
                   if self.filter_kind == "recent" else {})
        _wf48 = str(self.watch_filter.currentData() or "همه")
        out = []
        for it in self.items:
            cat = self.store.category_of(it)
            if self.filter_kind == "fav" and not self.store.fav(it.path):
                continue
            if self.filter_kind == "cat" and not Store.matches(cat, self.filter_cat):
                continue
            if self.filter_kind == "recent" and self.store.norm_path(it.path) not in _rmap48:
                continue
            if _wf48 != "همه":                     # watch-state filter
                _d48, _p48 = self.store.watch_progress(it)   # (watched, in-progress)
                _tot48 = self.store.watchable_total(it)       # smart — all category types
                _alldone48 = _tot48 > 0 and _d48 >= _tot48
                _seen48 = _d48 > 0 or _p48 > 0
                if _wf48 == "دیده‌شده" and not _alldone48:
                    continue
                if _wf48 == "نیمه‌تمام" and (_alldone48 or not _seen48):
                    continue
                if _wf48 == "ندیده" and _seen48:
                    continue
            if q:
                hay = fa_norm(
                    self.store.title(it) + " " + str(it.path) + " " + self.store.desc(it.path)
)
                if q not in hay:
                    continue
            out.append(it)

        if self.filter_kind == "recent":           # newest first, no manual sorting
            out.sort(key=lambda i: _rmap48.get(self.store.norm_path(i.path), 999))
            return out

        mode = self.sort_combo.currentData() or self.sort_combo.currentText()
        if mode == "نام":
            out.sort(key=lambda i: natural_key(self.store.title(i)),
                     reverse=not self.sort_asc)
        elif mode == "نوع محتوا":
            # Sort by "dominant content type" —
            # video-centric ← image-centric ← archive-centric ← document-centric ← audio ← other;
            # within each group, name sorting flips with the direction button. The dominant type
            # is computed at scan time from real file counts (not from the name).
            grouped: dict = {}
            for i in out:
                rank = _TYPE_RANK.get(getattr(i, "dom_type", "") or "other", 99)
                grouped.setdefault(rank, []).append(i)
            res = []
            for rank in sorted(grouped):
                res += sorted(grouped[rank],
                              key=lambda i: natural_key(self.store.title(i)),
                              reverse=not self.sort_asc)
            out = res
        elif mode == "مدت زمان":
            # Unknown durations (0) always stay at the end, in both directions.
            known = [i for i in out if self.store.total_duration(i) > 0]
            unknown = [i for i in out if self.store.total_duration(i) <= 0]
            known.sort(key=lambda i: self.store.total_duration(i),
                       reverse=not self.sort_asc)
            unknown.sort(key=lambda i: natural_key(self.store.title(i)))
            out = known + unknown
        elif mode == "امتیاز":                       # personal rating (unrated go last)
            rated = [i for i in out if self.store.rating(i.path) > 0]
            unrated = [i for i in out if self.store.rating(i.path) <= 0]
            rated.sort(key=lambda i: self.store.rating(i.path),
                       reverse=not self.sort_asc)
            unrated.sort(key=lambda i: natural_key(self.store.title(i)))
            out = rated + unrated
        else:
            out.sort(key=self._sort_key(mode), reverse=not self.sort_asc)
        return out

    def _sort_key(self, mode: str):
        if mode == "حجم":
            return lambda i: i.size_bytes
        if mode == "تعداد قسمت‌ها":
            return lambda i: i.video_count
        if mode == "تعداد فایل‌ها":
            return lambda i: i.file_count
        if mode == "تاریخ تغییر":
            return lambda i: i.modified
        return lambda i: 0

    # ------------------------------------------------------------ Card building (atomic, no flash)
    BUILD_BUDGET_MS = 0.028      # each build turn ~28 ms — the UI stays alive, with few hiccups

    def _on_search(self, _text):
        # debounce: rebuild the grid after a short typing pause (not on every keystroke)
        self._search_timer.start()

    def _swap_container(self, container: QWidget, flow: FlowLayout):
        """Atomically swap the scroll contents — the old page is hidden first, then deleted.

        This is the key to eliminating "white screens": if you detach a visible widget
        from its parent without hiding it first (what the classic clear_layout pattern
        did), Qt instantly turns it into an independent, visible white window (with the
        app's name) — and with 1000 cards that's 1000 transient windows!"""
        sb = self.scroll.verticalScrollBar()
        pos = sb.value()
        old = self.scroll.takeWidget()        # ownership returns to us so we can delete it safely
        self.scroll.setWidget(container)
        self.container, self.flow = container, flow
        if old is not None and old is not container:
            old.hide()                        # ← critical: hide first, then delete
            old.deleteLater()
        # preserve the scroll position (e.g. returning from details) — after the layout settles
        QTimer.singleShot(0, lambda: sb.setValue(min(pos, sb.maximum())))
        # after swapping the container, run the "user's view first" policy on the new cards
        QTimer.singleShot(20, self._schedule_poster_visibility)

    def rebuild_cards(self):
        self._cards_gen += 1
        gen = self._cards_gen
        # the "archive thumbnails" setting is applied to the fallback chain here (no scan)
        self._fthumbs.archive_enabled = self.store.archive_thumbs
        self._waiting.clear()
        self._poster_thread.enqueue([], gen)        # each rebuild invalidates stale jobs
        shown = self._filtered()
        self._shown_items = list(shown)     # for geometric visibility of unbuilt cards
        self._poster_started = set()        # fresh generation — dedup within this generation
        self._poster_attempts = {}          # fresh generation — retry counts reset to zero
        # the fresh generation re-reads poster sources from the store — clears the stale-source guard
        self._stale_sources.clear()
        self.page_sub.setText(T("{0} عنوان").format(fa(len(shown))))

        # The new container is built and filled *off-screen*; the current page stays until the
        # atomic swap — no empty white flicker and no window-cloned ghosting of old cards.
        container = QWidget()
        container.setObjectName("ScrollContent")
        flow = FlowLayout(container, spacing=14)
        flow.setContentsMargins(18, 8, 18, 16)

        if not shown:
            msg = QLabel(
                T("🗂\n\nهنوز عنوانی نیست!\nبا دکمه‌ی «⚙ تنظیمات» پوشه‌هایت را اضافه کن.")
                if not self.items else T("چیزی مطابق فیلتر/جستجو پیدا نشد.")
)
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setObjectName("MutedLabel")
            msg.setStyleSheet("font-size:15px; padding:60px;")
            msg.setMinimumWidth(CARD_W * 3)
            flow.addWidget(msg)
            self._swap_container(container, flow)
            return

        # Reserve the grid's final height up front (estimated, worst case with the subcategory row):
        # without this, every batch of newly added cards grows the ScrollArea a little; if the
        # user is scrolling at that moment, the scrollbar range keeps expanding and it looks
        # like "the posters are being rebuilt from scratch and the scroll position resets".
        # Reserving the final height at the start lets the ScrollArea see the real/final size
        # from the beginning.
        try:
            vpw = self.scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"rebuild_cards failed: {type(_exc_dbg).__name__}")
            vpw = CARD_W
        avail = max(CARD_W, vpw - 36)               # the flow's left/right margins, 18+18
        cols = max(1, int((avail + 14) // (CARD_W + 14)))
        row_h = CARD_H + 26 + 14                    # worst case (row with subcategory) + spacing
        rows = (len(shown) + cols - 1) // cols
        flow.reserve_height(rows * row_h + 8 + 16)  # + the FlowLayout's top/bottom contentsMargins

        queue = deque(shown)
        swapped = {"done": False}

        def step():
            if gen != self._cards_gen:              # a newer rebuild happened — this queue is abandoned
                if not swapped["done"]:
                    container.deleteLater()         # never became visible — safe to delete
                return
            t0 = time.monotonic()
            jobs = []
            waiters = []
            # Temporarily disable visual updates during batch adds → one layout recalculation
            # instead of one per card (fixes scroll lag/stutter)
            if swapped["done"]:
                container.setUpdatesEnabled(False)
            while queue and time.monotonic() - t0 < self.BUILD_BUDGET_MS:
                item = queue.popleft()
                card = CardWidget(item, self._view(item))
                card.clicked.connect(self.openDetails)
                card.menuRequested.connect(self._card_menu)
                card.subcatChanged.connect(self.subcatChanged)
                flow.addWidget(card)
                src = self.store.poster_of(item, static=True)  # the card grid reads a static frame
                # All three candidates go to the worker so that when any stage fails (e.g. a broken
                # video) the chain automatically moves on to PDF and then the archive's contents.
                vid = getattr(item, "preview_video", None)   # even with an icon, the
                pdf = getattr(item, "preview_pdf", None)     # fallback chain must push
                arc = getattr(item, "preview_archive", None) # past an icon failure
                if (src or vid or pdf or arc) and self._known_pm(item) is None:
                    pth = str(item.path)
                    waiters.append((pth, self._weakref.ref(card)))
                    # Geometric visibility (a jump to the list bottom) may have queued this item
                    # already — don't duplicate the work
                    if pth not in self._poster_started:
                        self._poster_started.add(pth)
                        jobs.append((pth, str(src) if src else "",
                                     str(vid) if vid else "", str(pdf) if pdf else "",
                                     str(arc) if arc else ""))
            if swapped["done"]:
                container.setUpdatesEnabled(True)
            unlimited = not self.store.cache_max_mb()
            if unlimited:
                # Unlimited mode: everything is built in the background (the user's view gets
                # priority via prioritize during scrolling)
                for pth, ref in waiters:
                    self._waiting.setdefault(pth, []).append(ref)
                if jobs:
                    # Light-to-heavy: icon/image ← video ← PDF ← archive — archive-only folders (the
                    # heaviest) no longer block the rest (report: "posters for archive folders are
                    # slow/don't appear"). The sort is stable; the visual order within each class is
                    # preserved.
                    jobs.sort(key=_job_rank)
                    self._poster_thread.enqueue(jobs, gen)
            else:
                # Capped mode: only cards in view get jobs; the rest queue when the scroll
                # reaches them (in _apply_poster_visibility)
                jobd = {j[0]: j for j in jobs}
                for pth, ref in waiters:
                    if pth in jobd:
                        self._poster_pending[pth] = (jobd[pth], ref)
                    elif pth in self._poster_started:
                        # geometric visibility already queued it — only register the card update
                        self._waiting.setdefault(pth, []).append(ref)
            self._schedule_poster_visibility()
            if not swapped["done"]:
                swapped["done"] = True
                self._swap_container(container, flow)   # first batch ready → atomic swap
            if queue:
                # a longer delay while scrolling — smoothness comes first
                delay = 80 if self._scroll_paused else 0
                QTimer.singleShot(delay, step)          # next turn on the next event-loop tick
            else:
                flow.reserve_height(0)              # build complete; replace with the real height

        step()

    # ------------------------------------------------------------ Scroll tracking
    def _on_scroll_move(self, *_):
        """Scrolling starts/continues → mark it active + delay card building."""
        self._scroll_paused = True
        self._scroll_timer.start()

    def _on_scroll_idle(self):
        """200 ms without scrolling → resume card building."""
        self._scroll_paused = False

    # ------------------------------------------------------------ Poster visibility policy
    def _schedule_poster_visibility(self):
        if not self._vis_timer.isActive():
            self._vis_timer.start(140)

    def _visible_paths(self) -> set:
        """Paths of items whose card is in the user's view (+ one page of margin above/below)."""
        out = set()
        try:
            sb = self.scroll.verticalScrollBar()
            y0 = sb.value()
            h = max(1, self.scroll.viewport().height())
        except Exception as _exc_dbg:
            dbg_log(f"_visible_paths failed: {type(_exc_dbg).__name__}")
            return out

        def _check(path, ref):
            w = ref()
            if w is None:
                return
            try:
                y = w.y()
                wh = w.height()
                if y + wh >= y0 - h and y <= y0 + 2 * h:
                    out.add(path)
            except Exception as _exc_dbg:
                dbg_log(f"_visible_paths._check failed: {type(_exc_dbg).__name__}")
                pass

        for path, (_j, ref) in self._poster_pending.items():
            _check(path, ref)
        for path, refs in self._waiting.items():
            for ref in refs:
                _check(path, ref)
        return out

    def _vis_shown_items(self) -> list:
        """Items in the scroll's visible area — geometric computation over the sorted/filtered
        list, *no built card required* (cards have a fixed size: CARD_W×CARD_H in a
        FlowLayout with spacing=14). Key to fixing "it builds top→bottom when I jump to
        the end of the list": unbuilt items are discovered and queued too."""
        items = self._shown_items or []
        if not items:
            return []
        try:
            sb = self.scroll.verticalScrollBar()
            h = max(1, self.scroll.viewport().height())
            y0 = sb.value()
            vpw = self.scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"_vis_shown_items failed: {type(_exc_dbg).__name__}")
            return []
        lo, hi = y0 - h, y0 + 2 * h
        avail = max(CARD_W, vpw - 36)                # the flow's left/right margins, 18+18
        cols = max(1, int((avail + 14) // (CARD_W + 14)))
        row_h = CARD_H + 26 + 14                     # worst case (row with subcategory)
        rows_total = (len(items) + cols - 1) // cols
        # two rows of margin on each side for shorter cards without a subcategory row
        r0 = max(0, int((lo - 8) // row_h) - 1)
        r1 = min(rows_total - 1, int((hi - 8) // row_h) + 2)
        out = []
        for r in range(r0, r1 + 1):
            out.extend(items[r * cols:(r + 1) * cols])
        return out

    def _apply_poster_visibility(self):
        """Enforces the visibility policy — called after cards are built and on every scroll."""
        gen = self._cards_gen
        vis = self._visible_paths()
        unlimited = not self.store.cache_max_mb()
        # capped mode: cards that entered view now get their jobs
        if self._poster_pending and vis:
            moved = []
            for path, (j, ref) in list(self._poster_pending.items()):
                if path in vis:
                    del self._poster_pending[path]
                    self._waiting.setdefault(path, []).append(ref)
                    moved.append(j)
            if moved:
                self._poster_thread.enqueue(moved, gen)
                self._poster_thread.prioritize(vis, gen)
        # unlimited mode: re-sort the background queue in favor of visible cards
        if unlimited and vis:
            self._poster_thread.prioritize(vis, gen)
        # Geometric visibility: unbuilt cards in the visible area are queued immediately too.
        # Motivating case: a card that hadn't been built yet couldn't get
        # priority when the user jumped straight to the end of a long folder;
        # now it's computed and queued from the scroll position.
        # The result is used once the card is in the cache, and _known_pm picks it up directly
        # at card build time.
        geo_jobs = []
        for it in self._vis_shown_items():
            pth = str(it.path)
            if pth in self._poster_started or self._known_pm(it) is not None:
                continue
            src = self.store.poster_of(it, static=True)  # the card grid reads a static frame
            vid = getattr(it, "preview_video", None)
            pdf = getattr(it, "preview_pdf", None)
            arc = getattr(it, "preview_archive", None)
            if not (src or vid or pdf or arc):
                continue
            self._poster_started.add(pth)
            self._poster_pending.pop(pth, None)
            geo_jobs.append((pth, str(src) if src else "",
                             str(vid) if vid else "", str(pdf) if pdf else "",
                             str(arc) if arc else ""))
        if geo_jobs:
            self._poster_thread.enqueue(geo_jobs, gen)
            self._poster_thread.prioritize({j[0] for j in geo_jobs}, gen)

    def _on_poster_loaded(self, gen: int, item_path: str, png: str, mt: int, img: QImage):
        """A ready poster arrives from the worker (main thread — Qt's automatic Queued connection)."""
        if gen != self._cards_gen:                  # stale rebuild — the result is useless
            return
        if item_path in self._stale_sources:
            # this item's cover was changed/removed from details — an in-flight result from
            # the old source must not fill the cache/card with the old poster; the next rebuild
            # (fresh gen) creates a new job with a fresh source
            self._stale_sources.discard(item_path)
            return
        if not png or img is None:                  # failure reported by the worker
            self._handle_poster_fail(gen, item_path)
            return
        self._poster_attempts.pop(item_path, None)
        self._poster_pending.pop(item_path, None)
        key = (png, mt)
        pm = self._pm_get(key)
        if pm is None:
            pm = QPixmap.fromImage(img)
            if pm.isNull():
                return
            self._pm_put(key, pm)
        self._item_png[item_path] = key
        refs = self._waiting.pop(item_path, [])
        for ref in refs:
            card = ref()
            if card is not None:
                card.set_poster(pm)

    # ------------------------------------------------- Failed-poster retry
    RETRY_DELAYS = (4000, 15000)                      # backoff: 4 s, 15 s
    MAX_POSTER_TRIES = 1 + len(RETRY_DELAYS)

    def _handle_poster_fail(self, gen: int, path: str):
        """Poster build failed — automatic delayed retry (not an eternal placeholder).

        An archive-only folder never got a poster, but it showed up in
        details. Many failures are transient (an antivirus/OneDrive lock on the file,
        7za's cold start, a busy disk); previously nothing came back from the worker and
        the card stayed posterless forever. Now we rebuild up to 3 times (immediate +
        4 s + 15 s) before giving up."""
        att = self._poster_attempts.get(path, 0)
        if att >= len(self.RETRY_DELAYS):
            refs = self._waiting.pop(path, [])
            self._poster_pending.pop(path, None)
            _poster_dbg(f"poster gave up after {att + 1} tries: {safe_ref(path)}")
            return
        # retrying is only worth it if a card is actually waiting (or in unlimited mode)
        refs = self._waiting.get(path) or []
        alive = any(ref() is not None for ref in refs)
        limited = bool(self.store.cache_max_mb())
        if not alive and limited:
            self._poster_pending.pop(path, None)
            return
        self._poster_attempts[path] = att + 1
        delay = self.RETRY_DELAYS[att]
        QTimer.singleShot(delay, lambda p=path, g=gen: self._retry_poster(p, g))

    def _retry_poster(self, path: str, gen: int):
        """One retry for a previously failed poster (if still needed)."""
        if gen != self._cards_gen:
            return
        if path in self._item_png:                # it was built by another route in the meantime
            return
        item = next((it for it in self.items if str(it.path) == path), None)
        if item is None:
            return
        src = self.store.poster_of(item, static=True)  # the card grid reads a static frame
        vid = getattr(item, "preview_video", None)
        pdf = getattr(item, "preview_pdf", None)
        arc = getattr(item, "preview_archive", None)
        if not (src or vid or pdf or arc):
            return
        self._poster_started.add(path)
        self._poster_pending.pop(path, None)
        # "1" means retry: negative stage caches are bypassed on retries so one transient
        # failure (an antivirus lock / 7za cold start) can't kill the thumbnail for good
        job = (path, str(src) if src else "", str(vid) if vid else "",
               str(pdf) if pdf else "", str(arc) if arc else "", "1")
        self._poster_thread.enqueue([job], gen)
        self._poster_thread.prioritize({path}, gen)

    def update_item_card(self, item) -> bool:
        """In-place update of one item's card (no grid rebuild, no scroll jump).

        For ⭐/category change/text edit — only that single card refreshes.
        False means the card isn't in the current scope or its structure must change →
        a rebuild is needed.
        """
        target = None
        for i in range(self.flow.count()):
            w = self.flow.itemAt(i).widget()
            if isinstance(w, CardWidget) and w.item.path == item.path:
                target = w
                break
        if target is None:
            return False
        view = self._view(item)
        if bool(target.view.get("subcat")) != bool(view.get("subcat")):
            return False                            # the subcategory row appears/disappears → rebuild
        target.apply_view(view)
        return True

    def _scope_items(self):
        """Items in the current stats scope: all / favorites / recent / one category (with subcategories)."""
        if self.filter_kind == "fav":
            return [i for i in self.items if self.store.fav(i.path)], T("⭐ علاقه‌مندی‌ها")
        if self.filter_kind == "recent":
            # the "recent" filter used to fall through to the
            # whole-library branch — the sidebar stats showed the full
            # library's numbers while the grid showed only the recents.
            return self._filtered(), T("🕘 موارد اخیر")
        if self.filter_kind == "cat":
            flt = self.filter_cat
            scope = [i for i in self.items
                     if Store.matches(self.store.category_of(i), flt)]
            from i18n import disp_cat as _dcat45
            return scope, "📂 " + _dcat45(flt).replace("/", " › ")
        return list(self.items), T("📊 آمار کلی کتابخانه")

    def _update_stats(self):
        """Sidebar stats — explicit and broken down by content type.

        A category with videos shows a "video watch percentage" and one
        with books a "book reading percentage", separately and in explicit words; a
        category can have neither (e.g. Games with the "no tracking" profile), in which
        case it gets no percentage line, or just one, or both. The mixed "N of M items
        completed" line (books + images + games over one denominator) was removed; a
        deleted file's record is no longer counted in any numerator/denominator.
        """
        from scanner import human_size
        scope, label = self._scope_items()
        total_size = sum(i.size_bytes for i in scope)
        total_eps = sum(i.video_count for i in scope)
        total_dur = sum(self.store.total_duration(i) for i in scope)
        prog = self.store.scope_progress_by_kind(scope)
        vid_d, vid_p, vid_t = prog["video"]
        book_d, book_p, book_t = prog["book"]
        img_d, img_p, img_t = prog["image"]
        lines = [label]
        if total_eps:
            lines.append(T("🎬 {0} عنوان · {1} قسمت").format(fa(len(scope)), fa(total_eps)))
        else:
            # The "item" count is independent of the tracking profile (Games should see "7 items"
            # too; only its percentage lines are absent)
            items_t = sum(self.store.watchable_total(i) for i in scope
                          if not (getattr(i, "video_count", 0) or 0))
            lines.append(T("🎬 {0} عنوان · {1} مورد").format(fa(len(scope)), fa(items_t)))
        lines.append(T("💾 حجم: {0}").format(human_size(total_size)))
        if total_dur:
            lines.append(T("⏱ زمان: {0}").format(format_duration(total_dur)))

        def _pct36(d: int, t: int) -> int:
            # Clamp: a stale record (an episode deleted after being marked) never produces a
            # percentage outside 0..100.
            return max(0, min(100, int(round(100 * d / t)))) if t else 0

        if vid_t:
            # Even 0% is displayed — previously the whole line disappeared when no
            # episode had been watched and only the completed count showed.
            s = T("👁 ویدیوها: {0} از {1} دیده‌شده ({2}٪)").format(
                fa(vid_d), fa(vid_t), fa(_pct36(vid_d, vid_t)))
            if vid_p:
                s += T(" · ◐ {0} در حال تماشا").format(fa(vid_p))
            lines.append(s)
        if book_t:
            s = T("📖 کتاب‌ها: {0} از {1} خوانده‌شده ({2}٪)").format(
                fa(book_d), fa(book_t), fa(_pct36(book_d, book_t)))
            if book_p:
                s += T(" · ◐ {0} در حال خواندن").format(fa(book_p))
            s += T(" · ✗ {0} خوانده‌نشده").format(
                fa(max(0, book_t - book_d - book_p)))
            lines.append(s)
        if img_t and (img_d or img_p):
            # The image badge only makes sense when images are actually marked — a video-only or
            # book-only category gets no extra line, and Games (whose files live in the "other"
            # basket) never gets a percentage line.
            s = T("🖼 تصاویر: {0} از {1} ({2}٪)").format(
                fa(img_d), fa(img_t), fa(_pct36(img_d, img_t)))
            if img_p:
                s += T(" · ◐ {0} نیمه‌تمام").format(fa(img_p))
            lines.append(s)
        self.stats.setText(fix_en_singular("\n".join(lines)))

    def _update_luck_btn(self):
        """The "What to watch tonight?" button adapts to the current scope's type:
        a bookish category → "What to read tonight?"; a "no tracking" category → hidden.

        Previously it showed on every category with the watch text — even Books and
        Comics."""
        bookish = untracked = False
        if self.filter_kind == "cat":
            st = self.store
            key = st.cat_track_profile_key(str(self.filter_cat or "").strip())
            if key is None:
                key = st.guess_profile_key(str(self.filter_cat or "").strip())
            bookish = (key == "book")
            untracked = (key == "none")
        self.btn_random.setVisible(not untracked)
        self.btn_random.setText(T("🎲 امشب چی بخونم؟") if bookish
                                else T("🎲 امشب چی ببینم؟"))

    # ------------------------------------------------------------ What to watch tonight?
    def _random_dialog(self):
        candidates = self._filtered()
        if not candidates:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "🎲", T("با فیلتر فعلی چیزی برای انتخاب نیست!"))
            return
        dlg = _LuckDialog(self, candidates, self)
        dlg.exec()

    # ------------------------------------------------------------ Card menu
    def _card_menu(self, item, gpos):
        fav = self.store.fav(item.path)
        menu = QMenu(self)
        a_open = menu.addAction(T("📄 صفحه‌ی جزئیات"))
        a_folder = menu.addAction(T("📂 باز کردن پوشه"))
        menu.addSeparator()
        a_edit = menu.addAction(T("✏ ویرایش اطلاعات"))
        # custom cover — can also be changed/removed from this menu
        a_cover = menu.addAction(T("🖼 انتخاب کاور…"))
        has_cover = bool(self.store.custom_poster(item.path)[0])
        a_clear = menu.addAction(T("↺ بازگشت به آیکون پوشه")) if has_cover else None
        a_fav = menu.addAction(T("☆ حذف از علاقه‌مندی") if fav else T("⭐ افزودن به علاقه‌مندی"))
        menu.addSeparator()
        # One toggle action — checks the current state first.
        # The "no tracking" category has no marking action at all.
        a_watch = None
        if self.store.progress_kind(item) != "none":
            done, part = self.store.watch_progress(item)
            total = self.store.watchable_total(item)
            all_done = total > 0 and done >= total
            if all_done:
                a_watch = menu.addAction(T("↺ پاک کردن وضعیت تماشا"))
            else:
                a_watch = menu.addAction(T("✓ همه دیده‌شده"))
        menu.addSeparator()
        a_copy = menu.addAction(T("📋 کپی مسیر"))
        act = menu.exec(gpos)
        menu.deleteLater()   # QObject buildup in long sessions — deterministic release
        if act is a_open:
            self.openDetails.emit(item)
        elif act is a_folder:
            open_path(item.path)
        elif act is a_edit:
            self.editRequested.emit(item)
        elif act is a_cover:
            from ui.cover_actions import pick_cover
            pick_cover(self, self.store, item, self._after_cover_change)
        elif a_clear is not None and act is a_clear:
            from ui.cover_actions import clear_cover
            clear_cover(self, self.store, item, self._after_cover_clear)
        elif act is a_fav:
            self.favChanged.emit(item)
        elif a_watch is not None and act is a_watch:
            self._card_watch_all(item, not all_done)
        elif act is a_copy:
            QGuiApplication.clipboard().setText(str(item.path))

    def _after_cover_change(self, changed: bool):
        """After a cover change: the pixmap cache and cards are rebuilt (no scroll jump)."""
        if not changed:
            return
        self.clear_pixmap_cache()
        self._set_status_hint(T("کاور به‌روز شد ✓"))

    def _after_cover_clear(self, changed: bool):
        """After removing the custom cover: back to the folder icon with a card rebuild."""
        if not changed:
            return
        self.clear_pixmap_cache()
        self._set_status_hint(T("کاور حذف شد"))

    def _set_status_hint(self, msg: str):
        """A short status-bar message — the same pattern as ui/details.py (no path shown)."""
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage(msg, 6000)

    def _card_watch_all(self, item, mark: bool):
        """Mark or clear every watchable file of an item.
        Also fixed the import bug + batched save + in-place update (no rebuild)."""
        try:
            from scanner import item_watchables
            from ui.details import rel_of
            files = item_watchables(item)
        except Exception as _exc_dbg:
            dbg_log(f"_card_watch_all failed: {type(_exc_dbg).__name__}")
            files = []
        if not files:
            return
        with self.store.defer_save():
            for f in files:
                r = rel_of(item, f)
                self.store.watch_set(item, r,
                                     Store.WATCH_DONE if mark else 0, 0)
        # in-place card update — no grid rebuild and no scroll jump
        if not self.update_item_card(item):
            self.itemsChanged.emit()
        # Refresh the sidebar stats the same moment — a reading/watching state change must
        # show up in the percentages immediately
        self._update_stats()

    def set_busy(self, busy: bool):
        for b in (self.btn_rescan, self.btn_paths, self.btn_random):
            b.setEnabled(not busy)


# ------------------------------------------------------------------ What to watch tonight?

class _LuckDialog(QDialog):
    """Weighted random pick: in-progress items get better odds, fully watched ones worse."""

    def __init__(self, page: LibraryPage, candidates: list, parent=None):
        super().__init__(parent)
        self.page = page
        self.store = page.store
        self.candidates = list(candidates)
        self._current = None
        self._seen_ids = set()
        self.setWindowTitle(T("امشب چی ببینم؟ 🎲"))
        self.setMinimumWidth(560)

        lay = QHBoxLayout(self)
        lay.setSpacing(18)
        lay.setContentsMargins(18, 16, 18, 16)

        self.poster = QLabel()
        self.poster.setFixedSize(POSTER_W, POSTER_H)
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("H1")
        self.title.setWordWrap(True)
        right.addWidget(self.title)

        self.meta = QLabel()
        self.meta.setObjectName("MutedLabel")
        self.meta.setWordWrap(True)
        right.addWidget(self.meta)

        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setMaximumHeight(90)
        right.addWidget(self.desc, 1)

        btns = QVBoxLayout()
        btns.setSpacing(8)
        self.btn_play = QPushButton(T("▶ همین را ببین"))
        self.btn_play.setObjectName("PrimaryBtn")
        self.btn_details = QPushButton(T("📄 جزئیات"))
        self.btn_reroll = QPushButton(T("🔄 نه، یکی دیگه"))
        self.btn_close = QPushButton(T("بستن"))
        self.btn_play.clicked.connect(self._play)
        self.btn_details.clicked.connect(self._details)
        self.btn_reroll.clicked.connect(self.pick)
        self.btn_close.clicked.connect(self.reject)
        for b in (self.btn_play, self.btn_details, self.btn_reroll):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        right.addLayout(btns)
        lay.addLayout(right, 1)

        self.pick()

    # Candidate weights: in-progress ⬆ / fully watched ⬇ / the rest balanced
    def _weight(self, item) -> float:
        total = self.store.watchable_total(item)     # smart — all category types
        if not total:
            return 1.0
        done, part = self.store.watch_progress(item)
        if part:
            return 5.0                      # half-finished — top priority
        if done >= total and total > 0:
            return 0.25                     # fully watched — almost never
        if done > 0:
            return 2.5                      # started
        return 2.0                          # unwatched

    def pick(self):
        import random
        pool = [(i, self._weight(i)) for i in self.candidates
                if str(i.path) not in self._seen_ids]
        if not pool:  # all shown — start over
            self._seen_ids.clear()
            pool = [(i, self._weight(i)) for i in self.candidates]
        items, weights = zip(*pool)
        item = random.choices(items, weights=weights, k=1)[0]
        self._seen_ids.add(str(item.path))
        self._show(item)

    def _show(self, item):
        self._current = item
        st = self.store
        self.title.setText("🎬 " + st.title(item))
        cat = st.category_of(item)
        from i18n import disp_cat as _dcat45
        parts = [f"📂 {_dcat45(cat)}", f"💾 {item.size_text}"]
        # the watched fraction is computed against the title's REAL
        # consumable total — for a book/comic with zero videos the old code
        # printed "watched 3/0" (the denominator was video_count).
        total = item.video_count if item.video_count else self.store.watchable_total(item)
        if item.video_count:
            parts.append(T("🎬 {0} قسمت").format(fa(item.video_count)))
        dur = st.total_duration(item)
        if dur:
            parts.append(f"⏱ {format_duration(dur)}")
        done, part = st.watch_progress(item)
        if done or part:
            parts.append(T("👁 {0}/{1} دیده شده").format(fa(done), fa(total))
                         + (T(" · ◐ {0} نیمه‌تمام").format(fa(part)) if part else ""))
        self.meta.setText(fix_en_singular("   ·   ".join(parts)))
        d = st.desc(item.path)
        self.desc.setText(d if d else T("توضیحاتی ثبت نشده."))
        self.poster.setPixmap(self.page._poster_pixmap(item))

    def _play(self):
        it = self._current
        self.accept()
        self.page.smartPlayRequested.emit(it)

    def _details(self):
        it = self._current
        self.accept()
        self.page.randomRequested.emit(it)
