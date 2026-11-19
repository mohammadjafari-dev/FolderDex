# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Details page for one title: blurred hero header, large poster, specs, an
editable description, watch tracking (✓ seen / ◐ partial with resume), and files
in two modes: a collapsible nested tree or large thumbnails."""
from __future__ import annotations

from i18n import T

import os


def _widget_dead(w) -> bool:
    """Has this Qt widget already been deleted? (No sip — touching a dead object
    raises RuntimeError.)

    Only meaningful for real widgets (ThumbCard). RuntimeError means the C++
    object is gone; AttributeError (a non-widget object, e.g. QTreeWidgetItem)
    carries no signal here → treat as alive."""
    try:
        w.isVisible()
        return False
    except RuntimeError:
        return True
    except AttributeError:
        return False


def _dir_fingerprint(path: Path) -> tuple:
    """Lightweight directory fingerprint for the file-structure cache.

    Only names and directory mtimes — no per-file stat calls, so a 2000-file
    folder takes a few milliseconds — no more "leave the page, come back,
    wait all over again"). Adding/removing/renaming any file or folder changes
    its parent directory's mtime and invalidates the cache; editing content
    inside a file (which doesn't rotate the listing) leaves it untouched —
    exactly what we want.
    """
    p = Path(path)
    try:
        if p.is_file():
            st = p.lstat()
            return ("file", p.name, st.st_size, int(st.st_mtime_ns))
        if not p.exists():
            return ("missing", 0)
    except OSError:
        return ("missing", 0)
    n_files = 0
    n_dirs = 0
    mt = 0
    try:
        for root, dirs, files_ in os.walk(p):
            n_files += len(files_)
            n_dirs += 1
            try:
                m = os.lstat(root).st_mtime_ns
                if m > mt:
                    mt = m
            except OSError:
                pass
            files_.sort()
            dirs.sort()
    except OSError:
        return ("dir", p.name, -1)
    return ("dir", p.name, n_files, n_dirs, int(mt))
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QPoint, QRunnable, QThreadPool, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QBrush, QMovie, QImageReader
from PySide6.QtWidgets import (
    QButtonGroup, QApplication, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QStackedWidget, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QHeaderView, QSizePolicy,
)

from scanner import (
    LOOSE_DOC_EXTS, get_file_hierarchy, get_file_tree, fa, human_size,
    item_watchables,
    natural_key, file_type_group, TYPE_ORDER, VIDEO_EXTS,
)

# Type-group ranks for "file type" sorting — built once as a lookup map
_TYPE_RANK = {t: i for i, t in enumerate(TYPE_ORDER)}
from store import Store
from thumbs import ThumbnailCache
from filethumbs import (
    FileThumbs, ThumbJob, ThumbSignals, group_of, load_cache_pixmap,
    poster_png_fallback,
)


class _HeroSignals(QObject):
    """Hero poster + blurred backdrop built in the background (delivered to the UI thread)."""
    ready = Signal(str, str, str)    # title key, poster png path, backdrop png path


class _HeroJob(QRunnable):
    """Builds the hero poster and backdrop on a background thread.

    Previously the same poster fallback chain (icon ← video(shell/ffmpeg) ←
    PDF(render) ← archive) ran *synchronously* on the UI thread, so opening
    titles with a heavy chain (e.g. a large PDF or a video on a slow drive)
    froze the whole app for a few seconds.
    """

    def __init__(self, page, src, item):
        super().__init__()
        self._page = page
        self._src = src
        self._item = item

    def run(self):
        p, item = self._page, self._item
        png = hero = None
        try:
            # The details poster is 252×336 logical points; for sharpness at 150–200%
            # scaling it is cached at 384px minimum (max via the scale-aware cache).
            px = max(getattr(p, "cache_px", 256), 384)
            png = poster_png_fallback(
                p.cache.get, p.fthumbs, self._src,
                getattr(item, "preview_video", None),
                getattr(item, "preview_pdf", None),
                getattr(item, "preview_archive", None), px)
            hero = p.fthumbs.hero_backdrop(png) if png else None
        except Exception as _exc_dbg:
            dbg_log(f"run failed: {type(_exc_dbg).__name__}")
            png = hero = None
        try:
            p._hero_signals.ready.emit(
                str(item.path), str(png or ""), str(hero or ""))
        except RuntimeError:
            pass          # app shutting down — signal source already deleted
from media_info import (
    DurationJob, DurationSignals, DurationStore, format_duration, format_duration_short,
)
from .dialogs import ItemEditDialog, NoWheelComboBox
from .widgets import (
    FlowLayout, clear_layout, open_path,
    placeholder_poster, poster_pixmap, rounded_pixmap,
)
from watchtime import dbg_log


DONE_COLOR = QColor("#4ade80")   # green — watched
PART_COLOR = QColor("#fbbf24")   # amber — partially watched


def rel_of(item, f: Path) -> str:
    """Relative path of a file within the title folder (uses / for cross-platform stability)."""
    try:
        if Path(item.path).is_file():
            return Path(f).name
        return str(Path(f).relative_to(item.path)).replace("\\", "/")
    except (ValueError, OSError):
        return Path(f).name


class _Hero(QFrame):
    """Page-top header with the poster blurred as its background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        self.setMinimumHeight(158)
        self._bg: QPixmap | None = None

    def set_background(self, pm: QPixmap | None):
        self._bg = pm
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111927"))
        p.drawRoundedRect(r, 14, 14)
        if self._bg and not self._bg.isNull():
            scaled = self._bg.scaled(
                r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
)
            x = (scaled.width() - r.width()) // 2
            y = (scaled.height() - r.height()) // 2
            p.setClipRect(r.adjusted(0, 0, -1, -1))
            path_rect = r.adjusted(0, 0, 0, 0)
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(path_rect, 14, 14)
            p.setClipPath(path)
            p.drawPixmap(-x, -y, scaled)
            p.fillRect(r, QColor(12, 17, 26, 175))
        p.end()
        super().paintEvent(event)


class _FlowContainer(QWidget):
    """FlowLayout host that adjusts its height to the current width
    (so it displays correctly inside a vertical scroll area)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing=10)
        self.flow.setContentsMargins(4, 8, 4, 8)
        self._uniform: tuple | None = None   # (card width, card height) when cards are uniform

    def set_uniform_card(self, cw: int, ch: int):
        """Declare uniform-size cards — enables the analytic height-computation path."""
        self._uniform = (cw, ch)

    def refresh_height(self, real: bool = False):
        """Refresh the height after cards have been added.

        Cards are added in batches (via QTimer) but resizeEvent only
        fired on width changes, so the container height stayed at its initial empty
        value and only the first rows (≈36 cards) were visible. This method runs
        after every insertion batch so that *all* files actually become visible.

        For a 2000+ file folder the page once stalled: in uniform-card mode
        (details grid — every ThumbCard is a fixed 152×186) the height is computed
        with an O(1) formula instead of heightForWidth, which walked every item;
        previously each build batch triggered a full O(n) layout walk whose
        product froze large folders for tens of seconds. The formula yields exactly
        the same result as FlowLayout._do_layout for uniform items. With real=True
        a real (decaying) walk runs (end of the batched build — final assurance).
        """
        w = self.width()
        if w <= 0:
            return
        if self._uniform and not real:
            cw, ch = self._uniform
            n = self.flow.count()
            if n <= 0:
                h = 16
            else:
                avail = max(1, w - 8)                 # left/right margins 4+4
                sp = 10                                # layout spacing
                cols = max(1, int((avail + sp) // (cw + sp)))
                rows = (n + cols - 1) // cols
                h = rows * (ch + sp) - sp + 16         # top/bottom margins 8+8
            if h != self.height():
                self.setFixedHeight(h)
            return
        h = self.flow.heightForWidth(w)
        if h > 0 and h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        self.refresh_height()
        super().resizeEvent(event)


class _VSection(QWidget):
    """Virtual grid section — cards are only materialized for rows near the viewport.

    ("entering a file-heavy folder wants patience; leaving
    and coming back starts the wait over"): the real entry bottleneck was
    building thousands of ThumbCard widgets — not layout, not thumbnails.
    Because cards have a fixed size and the layout is uniform, every file's
    coordinates are fully analytic: the section knows its height immediately and
    materializes only the "viewport ± two pages" window; as you move away from
    the window, the cards on that side are pruned and freed so memory stays
    bounded too. All files remain reachable (scrolling materializes them
    instantly) — the build cost is simply confined to "what is on screen". A
    pruned card is rebuilt instantly because its thumbnail comes from the disk
    cache (peek).
    """

    CARD_W, CARD_H, SP = 152, 186, 10   # matches the fixed-size ThumbCard and the old spacing
    M_H, M_V = 4, 8                     # left/right and top/bottom margins — as in the old FlowLayout

    def __init__(self, files, make_card, parent=None, notify=None, width_hint=0):
        super().__init__(parent)
        self.files = list(files)
        self._make_card = make_card      # card factory callback (a healthy, live ThumbCard)
        self._notify = notify            # "geometry changed — sync now" callback (DetailsPage)
        self._width_hint = int(width_hint or 0)   # initial width guess — until the layout settles
        self._cards: dict = {}           # index -> ThumbCard
        self._cols = 0
        self._eff_w = 0                  # effective (viewport) width for the RTL anchor
        self._last_w = 0                 # last width the cards were laid out with
        self._win = None                 # currently materialized (start row, end row)
        # The section must not impose an unreliable minimum width on the
        # layout — fixed height, width chases the viewport (the Ignored
        # policy keeps its minimum width unlocked).
        _sp = self.sizePolicy()
        _sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.setSizePolicy(_sp)
        self._rebuild_layout()

    def resizeEvent(self, ev):
        """Re-flow the cards in place on width changes — never any horizontal scrolling.

        Re-layout once ran only when the *column count* changed; now also
        ("for some folders the cards stick out of the app
        until I fullscreen it"): the root cause was that when the *vertical
        scrollbar* appeared (file-heavy folders) the viewport width shrank by
        ~12px; the column count stayed the same, but the last column's x
        (computed from self.width() in right-to-left mode) was still cached from
        the old, wider width, so the last column's cards landed outside the
        frame. Now re-layout runs on *every* width change.
        """
        old = self._cols
        self._rebuild_layout()
        w_changed = self.width() != self._last_w
        if self._cols and (self._cols != old or w_changed):
            for i, card in list(self._cards.items()):
                try:
                    card.move(*self._cell_rect(i))
                except RuntimeError:
                    pass
        self._last_w = self.width()
        super().resizeEvent(ev)

    # ---------------------------------------------------------------- analytic geometry
    def _row_count(self) -> int:
        return max(1, (len(self.files) + max(1, self._cols) - 1) // max(1, self._cols))

    def _effective_width(self) -> int:
        """A trustworthy width for layout — the scroll viewport's actual width.

        In a folder with a long section title the grid locked onto a fixed
        icon count per row; shrinking the window did not reduce it and icons
        of each row stuck out — cause: the section title button's long text kept the whole
        container's minimum width up (the button's minimumSizeHint ≈ its text)
        and the viewport could not effectively become narrower; consequently
        self.width() of each section stayed bloated and the layout was computed
        from an irrelevant, wide width. Now layout and horizontal positioning
        reference the actual viewport width of the nearest parent QScrollArea —
        exactly what the user sees.
        """
        w = self.width()
        try:
            p = self.parentWidget()
            while p is not None:
                if isinstance(p, QScrollArea) and p.viewport() is not None:
                    vw = p.viewport().width()
                    if vw > 50 and (w < 50 or vw < w):
                        w = vw
                    break
                p = p.parentWidget()
        except Exception as _exc_dbg:
            dbg_log(f"_effective_width failed: {type(_exc_dbg).__name__}")
            pass
        return w

    def _rebuild_layout(self):
        """Columns from the current width — exactly equivalent to the
        FlowLayout formula for uniform cards."""
        w = self._effective_width()
        if w < 120:
            w = max(120, self._width_hint)   # fall back to the width guess before the layout settles
        self._eff_w = w                      # RTL anchor reference used by _cell_rect
        avail = max(1, w - 2 * self.M_H)
        cols = max(1, int((avail + self.SP) // (self.CARD_W + self.SP)))
        if cols != self._cols:
            self._cols = cols
            self._win = None               # new width — the window must be recomputed
        rows = self._row_count()
        h = rows * (self.CARD_H + self.SP) - self.SP + 2 * self.M_V
        if h != self.height():
            self.setFixedHeight(h)

    def _cell_rect(self, idx: int):
        """Absolute coordinates inside the section — with right-to-left
        layout support (like FlowLayout)."""
        r, c = divmod(idx, max(1, self._cols))
        x_ltr = self.M_H + c * (self.CARD_W + self.SP)
        if QApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            w = float(self._eff_w or self.width())   # anchor = the viewport's real width
            x = w - self.M_H - self.CARD_W - c * (self.CARD_W + self.SP)
        else:
            x = x_ltr
        y = self.M_V + r * (self.CARD_H + self.SP)
        return x, y

    # ---------------------------------------------------------------- window materialization
    def sync_window(self, y0: float, y1: float):
        """Materialize the rows intersecting [y0, y1] (section-local coordinates); prune the rest."""
        self._rebuild_layout()
        rows = self._row_count()
        rh = self.CARD_H + self.SP
        r0 = max(0, int((y0 - self.M_V) // rh))
        r1 = min(rows - 1, int((y1 - self.M_V) // rh) + 1)
        win = (r0, r1)
        if win == self._win:
            return
        self._win = win
        i0 = r0 * self._cols
        i1 = min(len(self.files), (r1 + 1) * self._cols) if r1 >= r0 else i0
        keep = set(range(i0, i1))
        # Prune cards outside the window — free them (lingering cards cost memory and speed)
        for i in list(self._cards):
            if i not in keep:
                wdg = self._cards.pop(i)
                wdg.hide()
                wdg.deleteLater()
        # Build the new cards for the window
        for i in range(i0, i1):
            if i in self._cards:
                continue
            try:
                card = self._make_card(self.files[i])
            except Exception as _exc_dbg:
                dbg_log(f"sync_window failed: {type(_exc_dbg).__name__}")
                continue                    # file vanished between scan and display — skip it
            try:
                card.setParent(self)
                x, y = self._cell_rect(i)
                card.move(x, y)
                card.show()
                self._cards[i] = card
            except RuntimeError:
                pass

    def card_count(self) -> int:
        return len(self._cards)

    # resizeEvent is defined at the top of the class (in-place re-flow
    # instead of clearing — this old "wipe everything" version was removed so the
    # flicker/delay never returns and cards never fall off the page).


class ThumbCard(QFrame):

    watchMenuRequested = Signal(object, object)   # (ThumbCard, global pos)
    playRequested = Signal(object)                # double-click → same as _play (configured player)

    def __init__(self, path: Path, thumb: QPixmap, size_text: str, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.rel = ""
        self.setObjectName("ThumbCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(152, 186)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(4)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(132, 132)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setPixmap(thumb)
        lay.addWidget(self._thumb_label)

        name = QLabel(self.path.name)
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        name.setFixedHeight(30)
        name.setStyleSheet("font-size:11px;")
        name.setToolTip(self.path.name)
        self._name_label = name
        lay.addWidget(name)

        self._size_text = size_text
        self._size_label = QLabel(size_text)
        self._size_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._size_label.setObjectName("MutedLabel")
        self._size_label.setStyleSheet("font-size:10.5px;")
        lay.addWidget(self._size_label)

        # watch-status badge (✓ / ◐) — top corner of the card
        self._watch_badge = QLabel(self)
        self._watch_badge.hide()

        self.setToolTip(f"{self.path.name}\n{size_text}\n{self.path}")

    def set_thumb(self, pm: QPixmap):
        self._thumb_label.setPixmap(pm)

    def set_duration(self, text: str):
        if not text:
            return
        base = self._size_text
        self._size_label.setText(f"{base} · {text}" if base else text)

    def set_tag(self, tag: str):
        """Metadata tag on the card (e.g. "Cover") — the cover file is no longer
        removed from the file listing, so its card must be identifiable."""
        if not tag:
            return
        base = self._size_text
        self._size_label.setText(f"{tag} · {base}" if base else tag)

    def set_watch(self, mode: int, pos: float = 0.0, page_based: bool = False):
        if mode == Store.WATCH_DONE:
            self._watch_badge.setText("✓")
            self._watch_badge.setStyleSheet(
                "background:#16a34a; color:#fff; border-radius:9px;"
                "padding:1px 6px; font-size:12px; font-weight:bold;")
            self._watch_badge.adjustSize()
            self._watch_badge.move(8, 8)
            self._watch_badge.show()
            self._name_label.setStyleSheet("font-size:11px; color:#4ade80;")
        elif mode == Store.WATCH_PART:
            # The unit used to be wrong for books — '5 seconds' instead of 'page 5' —
            # the progress unit comes from the file type: video/audio → resume time;
            # document/book/archive → "page N". pos=0 → bare ◐ (half-read).
            if pos and page_based:
                badge_txt = "◐ " + T("صفحه‌ی {0}").format(fa(int(pos)))
            elif pos:
                badge_txt = "◐ " + format_duration_short(pos)
            else:
                badge_txt = "◐"
            self._watch_badge.setText(badge_txt)
            self._watch_badge.setStyleSheet(
                "background:#b45309; color:#fff; border-radius:9px;"
                "padding:1px 6px; font-size:10.5px; font-weight:bold;")
            self._watch_badge.adjustSize()
            self._watch_badge.move(8, 8)
            self._watch_badge.show()
            self._name_label.setStyleSheet("font-size:11px; color:#fbbf24;")
        else:
            self._watch_badge.hide()
            self._name_label.setStyleSheet("font-size:11px;")

    def mouseDoubleClickEvent(self, event):
        self.playRequested.emit(self.path)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.watchMenuRequested.emit(self, event.globalPosition().toPoint())
        super().mousePressEvent(event)


class DetailsPage(QWidget):
    backRequested = Signal()
    changed = Signal()
    # the cover was replaced/removed — emitted with the item itself so the
    # library invalidates *that card only*, not the whole grid; receiver:
    # main._on_cover_changed
    coverChanged = Signal(object)
    playRequested = Signal(object, object)      # (item, file path) — tracked play from the main window

    def __init__(self, store: Store, cache: ThumbnailCache, parent=None,
                 durations: DurationStore | None = None, cache_px: int = 256):
        super().__init__(parent)
        self.store = store
        self.cache = cache
        # effective cache size (Windows display scale) — details poster gets at least 384
        self.cache_px = int(cache_px)
        self.fthumbs = FileThumbs(cache.cache_dir)
        self.item = None
        self._sections = None
        self._vsections = []               # virtual grid sections
        self._tree_cache: dict = {}        # file-structure cache: (fingerprint, sections)
        self._hier = None

        # Asynchronous thumbnail loading
        self._thumb_signals = ThumbSignals()
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        self._thumb_pool = QThreadPool(self)
        # Thread cap raised (3 → 6). Two reasons: 1) the new cache is WebP, which
        # encodes several times faster than JPEG XL, so each thread saturates far
        # less; 2) thumbnail building felt slow. At least 2
        # cores still stay free for UI/disk/playback so the interface doesn't lag.
        self._thumb_pool.setMaxThreadCount(
            max(3, min(6, max(2, (os.cpu_count() or 4) - 2))))
        self._thumb_pending: dict[tuple, list] = {}   # (path,px) -> [owners]
        self._thumb_queued: set[tuple] = set()

        # Single-thread pool for the hero poster — never queued behind hundreds of thumbnails
        self._hero_signals = _HeroSignals()
        self._hero_signals.ready.connect(self._on_hero_ready)
        self._hero_pool = QThreadPool(self)
        self._hero_pool.setMaxThreadCount(1)
        self._hero_key = ""

        # Visibility policy for file thumbnails: jobs are registered
        # as "wanted" but only start once their card is in view; with an unlimited
        # cache the rest are built slowly in the background at lower priority
        self._thumb_wanted: set[tuple] = set()   # (path, px) — waiting to start
        # Ranked background-feed buckets — inserted at enqueue time (instead of
        # sorting the whole list on every 80 ms tick, which with thousands of files
        # meant O(n log n) per tick and tens of seconds of cumulative freeze —
        # a 2130-file folder once hung the app).
        # A stale entry (already out of wanted) is skipped on consumption at a glance.
        self._bg_buckets: dict[int, deque] = {r: deque() for r in range(4)}
        self._thumb_prio: dict[tuple, int] = {}  # (path, px) → priority of the started job
        self._thumb_bg: set[tuple] = set()       # running background jobs (priority 0)
        self._limited = False                    # is the cache size cap active?
        self._folder_only = False                # "folder thumbnails only"
        self._thumb_sched = QTimer(self)
        self._thumb_sched.setSingleShot(True)
        self._thumb_sched.timeout.connect(self._start_visible_thumb_jobs)

        # Video durations (async + persistent cache — instance shared with the scanner)
        self.dstore = durations if durations is not None else DurationStore(cache.cache_dir)
        self._dur_signals = DurationSignals()
        # Dedicated pool for duration probes: the thumbnail queue must not hold
        # durations back (otherwise playback would briefly use a wrong/unknown value)
        self._dur_pool = QThreadPool(self)
        self._dur_pool.setMaxThreadCount(2)
        self._dur_signals.ready.connect(self._on_dur_ready)
        self._dur_paths: list = []
        self._dur_known: dict[str, float] = {}
        self._dur_waits: dict[str, list] = {}
        self._dur_queued: set[str] = set()

        # Watch tracking — process monitoring happens in the main window
        # (PlayTracker) so nothing is lost when this page or the app closes; this
        # page only refreshes the UI.
        self._watch_rows: dict[str, list] = {}     # rel -> [owners (tree/cards)]
        self._track_on = True                      # does the current category want watched/read state?
        self._profile = self.store.track_profile("")  # the current category's state pattern (auto is the default)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.btn_back = QPushButton(T("→ بازگشت به کتابخانه"))
        self.btn_back.setObjectName("FlatBtn")
        self.btn_back.setFixedWidth(190)
        self.btn_back.clicked.connect(self.backRequested)
        top.addWidget(self.btn_back)
        top.addStretch(1)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(20)

        # ---------------- poster column ----------------
        poster_col = QVBoxLayout()
        poster_col.setSpacing(10)
        self.poster = QLabel()
        self.poster.setFixedSize(252, 336)
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # No frame/bars: the poster floats on the page color; the
        # static letterbox bars are transparent too so the theme color shows through.
        self.poster.setStyleSheet("background: transparent; border: none;")
        # Animated cover: a child label with exactly the image's aspect ratio,
        # inside the 252×336 frame. A QLabel with a QMovie draws frames at natural
        # size and got cropped by the fixed frame (a wide GIF appeared
        # half-cut); setScaledSize on this path is not reliable. With
        # setScaledContents every frame is drawn to this same-ratio label
        # (confirmed by an offscreen test). Created before btn_cover so the corner
        # button always stays on top of the animation.
        self.poster_movie = QLabel(self.poster)
        self.poster_movie.setStyleSheet("background: transparent; border: none;")
        self.poster_movie.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_movie.setScaledContents(True)   # every frame scales to this label
        self.poster_movie.hide()
        # Custom cover: right-click on the poster plus a small corner button so
        # the feature is discoverable without right-click — the same three actions
        # on both paths.
        self.poster.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.poster.customContextMenuRequested.connect(self._poster_menu)
        self.btn_cover = QPushButton("🖼", self.poster)
        self.btn_cover.setObjectName("FlatBtn")
        self.btn_cover.setFixedSize(38, 32)
        self.btn_cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cover.setToolTip(T("انتخاب کاور سفارشی برای این عنوان"))
        self.btn_cover.move(252 - 38 - 8, 336 - 32 - 8)
        # clicked(bool) passes False — forwarded to _poster_menu it landed in
        # mapToGlobal(False) and crashed with TypeError. The lambda
        # swallows the checked flag so the menu uses its own center position.
        self.btn_cover.clicked.connect(lambda _checked=False: self._poster_menu())
        poster_col.addWidget(self.poster)
        self._movie: QMovie | None = None      # playing the animated cover (details page only)
        self._poster_media_src = None          # animated-cover source — replayed by showEvent

        self.btn_play = QPushButton(T("▶ پخش هوشمند"))
        self.btn_play.setObjectName("PrimaryBtn")
        self.btn_play.setToolTip(
            T("اولین قسمت نیمه‌تمام/ندیده پخش می‌شود و از همان‌جایی که مانده بودی ادامه می‌یابد.\n"
            "ردیابی دقیق با mpv یا PotPlayer — از «⚙ تنظیمات» انتخاب‌شده است."))
        self.btn_open_folder = QPushButton(T("📂 باز کردن پوشه"))
        self.btn_edit = QPushButton(T("✏ ویرایش اطلاعات"))
        self.btn_fav = QPushButton()
        self.btn_copy = QPushButton(T("📋 کپی مسیر"))
        self.btn_play.clicked.connect(self.play_smart)
        self.btn_open_folder.clicked.connect(self._open_folder)
        self.btn_edit.clicked.connect(self._edit_dialog)
        self.btn_fav.clicked.connect(self._toggle_fav)
        self.btn_copy.clicked.connect(self._copy_path)
        for b in (self.btn_play, self.btn_open_folder, self.btn_edit, self.btn_fav, self.btn_copy):
            poster_col.addWidget(b)
        # The category box and the "mark all seen" button moved to the poster column
        self.cat_combo = NoWheelComboBox()
        self.cat_combo.setSizeAdjustPolicy(
            NoWheelComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cat_combo.setMinimumContentsLength(8)
        self.cat_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cat_combo.setToolTip(
            T("دسته‌ی این عنوان — کلیک برای تعویض بین دسته‌ی اصلی و زیردسته‌ها"))
        self.cat_combo.activated.connect(self._on_hero_cat)
        poster_col.addWidget(self.cat_combo)
        self.btn_watch_all = QPushButton()
        self.btn_watch_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_watch_all.clicked.connect(self._toggle_watch_all)
        poster_col.addWidget(self.btn_watch_all)
        # a personal ⭐ 1–5 rating per title — persisted in the
        # database, shown on its card, and available for sorting/filtering.
        self.rate_combo = NoWheelComboBox()
        self.rate_combo.addItem(T("⭐ امتیاز: —"), 0)
        for _n48 in range(1, 6):
            self.rate_combo.addItem("⭐ " + "★" * _n48, _n48)
        self.rate_combo.currentIndexChanged.connect(self._on_rate_change)
        poster_col.addWidget(self.rate_combo)
        poster_col.addStretch(1)
        body.addLayout(poster_col)

        # ---------------- content column ----------------
        info = QVBoxLayout()
        info.setSpacing(10)

        # Hero header: title + chips over the blurred backdrop
        self.hero = _Hero()
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("HeroTitle")
        self.title.setWordWrap(True)
        hl.addWidget(self.title)
        # Chips wrap to the next row in the floating layout; they no longer
        # land on the category name.
        chips = FlowLayout(spacing=8)
        # The category box moved to the poster column — only the category name here
        self.cat_name_lbl = QLabel()
        self.cat_name_lbl.setStyleSheet(
            "color:#e7edf6; background:#0c111a88; border:1px solid #ffffff30;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
)
        chips.addWidget(self.cat_name_lbl)
        self.chips_extra = []
        for _ in range(4):
            c = QLabel()
            c.setStyleSheet(
                "color:#e7edf6; background:#0c111a88; border:1px solid #ffffff30;"
                "border-radius:9px; padding:2px 10px; font-size:11.5px;"
)
            self.chips_extra.append(c)
            chips.addWidget(c)
        self.chip_dur = QLabel()
        self.chip_dur.setStyleSheet(
            "color:#ffd9a0; background:#0c111a88; border:1px solid #fbbf2466;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
)
        self.chip_dur.hide()
        chips.addWidget(self.chip_dur)
        self.chip_watch = QLabel()
        self.chip_watch.setStyleSheet(
            "color:#b7f7cd; background:#0c111a88; border:1px solid #4ade8055;"
            "border-radius:9px; padding:2px 10px; font-size:11.5px;"
)
        self.chip_watch.hide()
        chips.addWidget(self.chip_watch)
        hl.addLayout(chips)

        # Watch progress bar (thin, under the chips)
        self.watch_bar = QProgressBar()
        self.watch_bar.setTextVisible(False)
        self.watch_bar.setFixedHeight(6)
        self.watch_bar.hide()
        hl.addWidget(self.watch_bar)
        info.addWidget(self.hero)

        # Description
        desc_head = QHBoxLayout()
        dl = QLabel(T("📝 توضیحات"))
        dl.setObjectName("SectionHeader")
        self.btn_desc_edit = QPushButton(T("✏ ویرایش"))
        self.btn_desc_edit.setObjectName("MiniBtn")
        self.btn_desc_edit.clicked.connect(self._edit_desc)
        desc_head.addWidget(dl)
        desc_head.addStretch(1)
        desc_head.addWidget(self.btn_desc_edit)
        info.addLayout(desc_head)

        self.desc_label = QLabel()
        self.desc_label.setObjectName("DescBox")
        self.desc_label.setWordWrap(True)
        info.addWidget(self.desc_label)
        self.desc_editor = QPlainTextEdit()
        self.desc_editor.setMaximumHeight(100)
        self.desc_editor.hide()
        info.addWidget(self.desc_editor)
        self.desc_btns = QWidget()
        db = QHBoxLayout(self.desc_btns)
        db.setContentsMargins(0, 0, 0, 0)
        b_save = QPushButton(T("ذخیره"))
        b_save.setObjectName("PrimaryBtn")
        b_cancel = QPushButton(T("انصراف"))
        b_save.clicked.connect(self._save_desc)
        b_cancel.clicked.connect(self._end_desc_edit)
        db.addStretch(1)
        db.addWidget(b_cancel)
        db.addWidget(b_save)
        self.desc_btns.hide()
        info.addWidget(self.desc_btns)

        # ---------------- files ----------------
        files_head = QHBoxLayout()
        fl = QLabel(T("📂 فایل‌ها"))
        fl.setObjectName("SectionHeader")
        self.files_count = QLabel("")
        self.files_count.setObjectName("MutedLabel")
        files_head.addWidget(fl)
        files_head.addWidget(self.files_count)
        files_head.addStretch(1)
        # ---------------- file sorting (per section/folder — tree order preserved) ----------------
        self.sort_combo = NoWheelComboBox()
        self.sort_combo.setMinimumWidth(120)
        for lbl, key in ((T("نام"), "name"), (T("نوع فایل"), "type"), (T("حجم"), "size"),
                         (T("تاریخ تغییر"), "modified"), (T("مدت‌زمان"), "duration")):   # previously missing
            self.sort_combo.addItem(lbl, key)
        self.sort_combo.setToolTip(
            T("مرتب‌سازی فایل‌های این عنوان — داخل هر بخش/پوشه جدا اعمال می‌شود\n"
            "(ساختار پوشه‌های تودرتو دست‌نخورده می‌ماند؛ فقط ترتیب فایل‌ها عوض می‌شود)"))
        self._file_sort = store.config.get("file_sort", "name")
        if self.sort_combo.findData(self._file_sort) < 0:
            self._file_sort = "name"
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(self._file_sort))
        self._file_sort_desc = bool(store.config.get("file_sort_desc", False))
        self.sort_dir_btn = QPushButton()
        self.sort_dir_btn.setObjectName("FlatBtn")
        self.sort_dir_btn.setMinimumWidth(110)
        self.sort_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sync_sort_dir_btn()
        self.sort_combo.currentIndexChanged.connect(self._on_file_sort)
        self.sort_dir_btn.clicked.connect(self._on_file_sort_dir)
        files_head.addWidget(QLabel("↕"))
        files_head.addWidget(self.sort_combo)
        files_head.addWidget(self.sort_dir_btn)
        self.btn_mode_tree = QPushButton(T("☰  درختی"))
        self.btn_mode_grid = QPushButton(T("▦  بندانگشتی"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for b in (self.btn_mode_tree, self.btn_mode_grid):
            b.setCheckable(True)
            b.setObjectName("ModeBtn")
            self.mode_group.addButton(b)
            files_head.addWidget(b)
        self.btn_mode_tree.clicked.connect(lambda: self._set_mode("tree"))
        self.btn_mode_grid.clicked.connect(lambda: self._set_mode("grid"))
        info.addLayout(files_head)

        self.stack = QStackedWidget()

        # Tree mode
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([T("نام"), T("حجم"), T("مدت")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setObjectName("FileTree")
        self.tree.setIndentation(20)
        self.tree.setAnimated(True)
        self.tree.expandToDepth(0)
        self.tree.itemDoubleClicked.connect(self._tree_dblclick)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_ctx_menu)
        self.stack.addWidget(self.tree)

        # Thumbnail mode
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        # The icons used to run off the page and an extra horizontal scroll
        # showed up — the grid's horizontal scrollbar is always off — the layout
        # is obliged to re-flow at any width (resizeEvent in _VSection rearranges
        # the cards).
        self.grid_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_container = QWidget()
        self.grid_container.setObjectName("ScrollContent")
        # The container must not impose a minimum width on the viewport (unlocks the columns)
        self.grid_container.setMinimumWidth(0)
        self.grid_layout = QVBoxLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 8, 4)
        self.grid_layout.setSpacing(6)
        self.grid_scroll.setWidget(self.grid_container)
        self.stack.addWidget(self.grid_scroll)

        # Scrolling ⇒ reschedule the thumbnail visibility policy (grid and tree alike)
        self.grid_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_thumb_jobs())
        self.tree.verticalScrollBar().valueChanged.connect(
            lambda *_: self._schedule_thumb_jobs())
        # Scroll/resize ⇒ materialize the visibility window of the virtual sections
        self.grid_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._grid_sync_visible())
        self.grid_scroll.viewport().installEventFilter(self)

        info.addWidget(self.stack, 1)
        body.addLayout(info, 1)
        root.addLayout(body, 1)

        self._mode = store.config.get("file_view", "grid")
        if self._mode not in ("tree", "grid"):
            self._mode = "grid"

    def eventFilter(self, obj, event):
        # Grid viewport resize → re-materialize the visibility window
        try:
            if (obj is self.grid_scroll.viewport()
                    and event.type() == QEvent.Type.Resize):
                QTimer.singleShot(0, self._grid_sync_visible)
        except Exception as _exc_dbg:
            dbg_log(f"eventFilter failed: {type(_exc_dbg).__name__}")
            pass
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ display
    def set_item(self, item):
        self.item = item
        st = self.store
        self._clear_thumb_pending()
        self._thumb_pool.clear()            # queued jobs of the previous title are cancelled —
        self._thumb_queued.clear()          # otherwise the new title's first files would stay
        self._triage_y = None               # stuck behind the stale queue
        self.fthumbs.archive_enabled = st.archive_thumbs   # freshly applied user setting
        # Cache policy: capped = only in-view thumbnails; "folders only" = no file thumbnails
        self._limited = bool(st.cache_max_mb())
        self._folder_only = bool(st.folder_thumbs_only)

        # Poster — show the letter placeholder immediately; the fallback chain
        # (icon/image ← video ← PDF ← archive) is built on a background thread so
        # the UI doesn't lock up (_HeroJob)
        src = st.poster_of(item)
        src_static = st.poster_of(item, static=True)
        cat = st.category_of(item)
        self._hero_key = str(item.path)
        self._stop_poster_movie()
        self.poster.setPixmap(placeholder_poster(
            st.title(item).strip()[:1] or T("؟"), st.color(cat), 252, 336, 16))
        self.hero.set_background(None)
        # Animated cover? It starts right away; hero/backdrop are built from the static frame
        self._set_poster_media(src, src_static)
        self._hero_pool.start(_HeroJob(self, src_static or src, item))
        self.title.setText(st.title(item))

        self._fill_hero_cat_combo()             # the colored category badge = the category box itself
        texts = []
        if item.video_count:
            texts.append(T("🎬 {0} قسمت").format(fa(item.video_count)))
        texts.append(f"💾 {item.size_text}")
        texts.append(T("📦 {0} فایل").format(fa(item.file_count)))
        if item.modified:
            texts.append("🕓 " + datetime.fromtimestamp(item.modified).strftime("%Y/%m/%d"))
        for lbl, txt in zip(self.chips_extra, texts + [""] * (4 - len(texts))):
            lbl.setText(txt)
            lbl.setVisible(bool(txt))

        self.btn_open_folder.setText(T("📂 باز کردن پوشه") if not item.path.is_file() else T("📂 باز کردن پوشه‌ی محتوی"))
        self._refresh_fav_btn()
        self._refresh_rate_combo()              # sync with the new title's rating
        self._refresh_desc()

        # File data
        # The cover image used to be hidden; it must show every file
        # inside the folder — the cover/icon image is no longer removed from the
        # details file listing: the same file is both the page-top cover and a
        # real collection item; its card gets a "cover" tag.
        # System files (desktop.ini/thumbs.db) and dotfiles are still filtered by
        # get_file_tree itself — one rule shared with gather_stats.
        exclude: set = set()
        self._cover_paths = {str(p) for p in (getattr(item, "icon_path", None),
                                              st.poster_of(item)) if p}
        loose = getattr(item, "loose_group", False)
        # The root documents group shows only archives/PDFs (videos get their own card)
        only = LOOSE_DOC_EXTS if getattr(item, "loose_docs_only", False) else None
        # File-structure cache with the lightweight fingerprint — returning from
        # the library to the same unchanged title no longer re-runs get_file_tree
        # (leaving and coming back used to restart the wait).
        ck = (str(item.path), frozenset(exclude), bool(loose), bool(only))
        fp = _dir_fingerprint(item.path)
        cached = self._tree_cache.get(ck)
        if cached is not None and cached[0] == fp:
            self._sections = cached[1]
        else:
            self._sections = get_file_tree(item.path, exclude=exclude, loose=loose,
                                           only_exts=only)
            self._tree_cache[ck] = (fp, self._sections)
            while len(self._tree_cache) > 12:     # memory bound — evict the oldest title
                self._tree_cache.pop(next(iter(self._tree_cache)))
        # The tree hierarchy is only needed in "tree" mode — building it
        # synchronously in the (default) grid was pure added latency for 2000+ file
        # folders; it is now built lazily (entering a busy folder used to
        # hang the app).
        self._hier_params = (item.path, frozenset(exclude), loose, only)
        self._hier = None
        # This category's tracking profile (movie/book/custom/no tracking) — the
        # "seen"/"read"/"done" labels are built from this profile
        self._profile = st.track_profile(st.category_of(item))
        self._track_on = bool(self._profile.get("enabled", True))
        self._resort_files()          # the user's sort criterion (name/size/date/duration)
        total = sum(len(s["files"]) for s in self._sections)
        self.files_count.setText(T("({0} فایل)").format(fa(total)))

        # Video durations: use the cache + queue the rest for computation
        self._dur_paths = [
            f for s in self._sections for f in s["files"]
            if f.suffix.lower() in VIDEO_EXTS
        ]
        self._dur_known = {}
        self._dur_waits = {}
        self._dur_queued = set()
        for p in self._dur_paths:
            v = self.dstore.get(p)
            if v is not None:
                self._dur_known[str(p)] = v
        self._update_dur_chip()

        self._watch_rows = {}
        self._refresh_watch_ui()
        self._refresh_watch_all_btn()         # keep the "mark all seen" button in sync
        self._apply_mode(build=True)
        # The scroll used to open in the middle/at the end
        # instead of the top — on every folder open, both modes' scrollbars
        # return to the top — immediately and once more after the layout settles
        # (a late layout may restore the value to a stale valid spot, i.e.
        # middle/end).
        self._reset_scroll_top()
        QTimer.singleShot(0, self._reset_scroll_top)

    def _reset_scroll_top(self):
        """Grid and tree scrollbars, vertical and horizontal, back to the top."""
        for sb in (self.grid_scroll.verticalScrollBar(),
                   self.grid_scroll.horizontalScrollBar(),
                   self.tree.verticalScrollBar(),
                   self.tree.horizontalScrollBar()):
            try:
                sb.setValue(0)
            except Exception as _exc_dbg:
                dbg_log(f"_reset_scroll_top failed: {type(_exc_dbg).__name__}")
                pass

    # ---------------- keeping the user's place across cover changes ----------------
    def _view_state_snapshot(self) -> dict:
        """Grid/tree scroll positions + expanded tree nodes — restored after a rebuild."""
        snap: dict = {}
        for key, sb in (("gv", self.grid_scroll.verticalScrollBar()),
                        ("gh", self.grid_scroll.horizontalScrollBar()),
                        ("tv", self.tree.verticalScrollBar()),
                        ("th", self.tree.horizontalScrollBar())):
            try:
                snap[key] = int(sb.value())
            except Exception:
                snap[key] = 0
        try:
            snap["expanded"] = self._tree_expanded_paths()
        except Exception:
            snap["expanded"] = set()
        return snap

    def _tree_expanded_paths(self) -> set:
        out: set = set()

        def walk(it, prefix):
            for i in range(it.childCount()):
                ch = it.child(i)
                try:
                    path = prefix + "/" + str(ch.text(0))
                    if ch.isExpanded():
                        out.add(path)
                except RuntimeError:
                    continue          # C++ object is gone
                walk(ch, path)

        walk(self.tree.invisibleRootItem(), "")
        return out

    def _restore_view_state(self, snap: dict) -> None:
        """Restore the user's place — via singleShot(0) so it runs after set_item's
        own singleShot(0) (which scrolls back to the top)."""
        if not snap:
            return

        def _do():
            try:
                self.grid_scroll.verticalScrollBar().setValue(snap.get("gv", 0))
                self.grid_scroll.horizontalScrollBar().setValue(snap.get("gh", 0))
                self.tree.verticalScrollBar().setValue(snap.get("tv", 0))
                self.tree.horizontalScrollBar().setValue(snap.get("th", 0))
            except Exception as _exc_dbg:
                dbg_log(f"_restore_view_state scroll failed: {type(_exc_dbg).__name__}")
            exp = snap.get("expanded") or set()
            if exp:
                def walk(it, prefix):
                    for i in range(it.childCount()):
                        ch = it.child(i)
                        try:
                            path = prefix + "/" + str(ch.text(0))
                            ch.setExpanded(path in exp)
                        except RuntimeError:
                            continue
                        walk(ch, path)
                walk(self.tree.invisibleRootItem(), "")

        QTimer.singleShot(0, _do)

    # ------------------------------------------------------------ view mode
    def _apply_mode(self, build=False):
        if self._mode == "tree":
            self.btn_mode_tree.setChecked(True)
            self.stack.setCurrentWidget(self.tree)
            if build:
                self._build_tree()
        else:
            self.btn_mode_grid.setChecked(True)
            self.stack.setCurrentWidget(self.grid_scroll)
            if build:
                self._build_grid()

    def _set_mode(self, mode):
        self._mode = mode
        self.store.config["file_view"] = mode
        self.store.save_config()
        self._apply_mode(build=True)

    # ------------------------------------------------------------ file sorting
    def _sync_sort_dir_btn(self):
        self.sort_dir_btn.setText(T("↓ نزولی") if self._file_sort_desc else T("↑ صعودی"))
        self.sort_dir_btn.setToolTip(T("کلیک برای تغییر جهت مرتب‌سازی"))

    def _save_file_sort(self):
        self.store.config["file_sort"] = self._file_sort
        self.store.config["file_sort_desc"] = bool(self._file_sort_desc)
        self.store.save_config()

    def _on_file_sort(self, _i):
        self._file_sort = self.sort_combo.currentData()
        # Natural direction per criterion: name and file type default ascending (readable grouping)
        self._file_sort_desc = self._file_sort not in ("name", "type")
        self._sync_sort_dir_btn()
        self._save_file_sort()
        self._resort_and_rebuild()

    def _on_file_sort_dir(self):
        self._file_sort_desc = not self._file_sort_desc
        self._sync_sort_dir_btn()
        self._save_file_sort()
        self._resort_and_rebuild()

    def _resort_and_rebuild(self):
        self._resort_files()
        self._apply_mode(build=True)

    def _dur_for(self, f: Path) -> float:
        d = self._dur_known.get(str(f))
        if d:
            return d
        if f.suffix.lower() in VIDEO_EXTS:
            try:
                v = self.dstore.get(f)
            except Exception as _exc_dbg:
                dbg_log(f"_dur_for failed: {type(_exc_dbg).__name__}")
                v = None
            if v and v > 0:
                return float(v)
        return 0.0

    def _file_sort_key(self, f: Path):
        name_key = natural_key(f.name.lower())
        mode = self._file_sort
        if mode == "type":
            # Sorting by "file type" — group first
            # (video ← image ← archive ← document ← audio ← other), natural name
            # order within the group.
            # Sorting only works on the extension; file names can be in any
            # language and pose no limitation.
            return (_TYPE_RANK.get(file_type_group(f), 9), name_key)
        if mode == "size":
            try:
                return (f.lstat().st_size, name_key)
            except OSError:
                return (0, name_key)
        if mode == "modified":
            try:
                return (f.lstat().st_mtime, name_key)
            except OSError:
                return (0.0, name_key)
        return name_key

    def _sorted(self, files: list) -> list:
        """Sort by the current criterion/direction — unknown durations always go
        to the end in "duration" mode."""
        rev = self._file_sort_desc
        if self._file_sort == "duration":
            keyed = [(f, self._dur_for(f)) for f in files]
            known = [(f, d) for f, d in keyed if d > 0]
            unknown = [f for f, d in keyed if d <= 0]
            known.sort(key=lambda t: (t[1], natural_key(t[0].name.lower())), reverse=rev)
            unknown.sort(key=lambda f: natural_key(f.name.lower()))
            return [f for f, _ in known] + unknown
        return sorted(files, key=self._file_sort_key, reverse=rev)

    def _resort_files(self):
        if self._sections:
            for s in self._sections:
                s["files"] = self._sorted(s["files"])

    # ------------------------------------------------------------ async thumbnails
    def _thumb_px_or_queue(self, f: Path, px: int, owner) -> QPixmap | None:
        """Return the cached thumbnail; otherwise keep the placeholder and queue
        real generation.

        Previously, on a cache miss, the "file-type card" was built synchronously
        right here on the UI thread (~3 ms per file — with 700 files that meant
        ~2 seconds of UI freeze). Now a placeholder returns instantly and both the
        type card and the real thumbnail arrive on background threads.
        Jobs no longer start immediately: they are registered as "wanted" and the
        visibility policy (_start_visible_thumb_jobs) starts them — so even the
        bottom of the list gets thumbnails without a top→bottom scroll sweep."""
        cached = self.fthumbs.peek(f, px)
        if cached:
            pm = load_cache_pixmap(cached)
            if pm is not None and not pm.isNull():
                return pm
        if self._folder_only:
            # "folder thumbnails only": files get just the file-type card (cheap and cached)
            cp = self.fthumbs.card(f, px)
            if cp:
                pm = load_cache_pixmap(cp)
                if pm is not None and not pm.isNull():
                    return pm
            return None
        key = (str(f), px)
        self._thumb_pending.setdefault(key, []).append(owner)
        if key not in self._thumb_queued:
            self._thumb_wanted.add(key)
            self._bg_buckets[self._bg_rank(key)].append(key)   # light-to-heavy bucket
            self._schedule_thumb_jobs()
        return None

    # ------------------------------------------------------------ thumbnail visibility policy
    def _schedule_thumb_jobs(self):
        if not self._thumb_sched.isActive():
            self._thumb_sched.start(90)

    def _owner_visible(self, owner) -> bool:
        """Is the owner of this request (grid card or tree row) inside the user's view?"""
        try:
            if isinstance(owner, ThumbCard):
                h = max(1, self.grid_scroll.viewport().height())
                # Fixed bug: y() used to be taken relative to the *section
                # container*, not the viewport — for every section after the
                # first (and even the first section's header offset) the scroll
                # comparison was wrong and the visibility policy misfired. mapTo
                # converts straight to viewport coordinates, independent of how
                # deeply the containers are nested.
                y = owner.mapTo(self.grid_scroll.viewport(), QPoint(0, 0)).y()
                wh = owner.height()
                return y + wh >= -h and y <= 2 * h            # viewport + one page of margin
            if isinstance(owner, QTreeWidgetItem):
                r = self.tree.visualItemRect(owner)
                vh = max(1, self.tree.viewport().height())
                return r.bottom() >= -vh and r.top() <= 2 * vh
        except Exception as _exc_dbg:
            dbg_log(f"_owner_visible failed: {type(_exc_dbg).__name__}")
            pass
        return True   # unknown → build to be safe

    # ------------------------------------------------------------ geometric grid visibility
    def _grid_metrics(self) -> tuple[int, int, int]:
        """(column count, row height, section header height) — cards are a fixed
        152×186 in the FlowLayout with spacing=10, so every file's position is
        computable *before its card is built* (the key to fixing "the bottom of
        the list never gets thumbnails")."""
        try:
            vpw = self.grid_scroll.viewport().width()
        except Exception as _exc_dbg:
            dbg_log(f"_grid_metrics failed: {type(_exc_dbg).__name__}")
            vpw = 800
        cont_w = max(120, vpw - 10)        # grid_layout left/right margins = 2+8
        avail = cont_w - 8                 # FlowLayout left/right margins = 4+4
        cols = max(1, int((avail + 10) // (152 + 10)))
        hdr = getattr(self, "_grid_hdr_h", 34) or 34
        return cols, 186 + 10, hdr

    def _visible_grid_files(self) -> list:
        """Files whose card (even if it does not exist yet!) intersects the
        scroll's visible area — computed geometrically with one row of margin on
        each side."""
        if self._mode != "grid" or not self._sections or self._folder_only:
            return []
        try:
            sb = self.grid_scroll.verticalScrollBar()
            h = max(1, self.grid_scroll.viewport().height())
            y0 = sb.value()
        except Exception as _exc_dbg:
            dbg_log(f"_visible_grid_files failed: {type(_exc_dbg).__name__}")
            return []
        lo, hi = y0 - h, y0 + 2 * h         # viewport + one page above/below (like the cards)
        cols, row_h, hdr = self._grid_metrics()
        out = []
        y = 2                                # grid_layout top margin
        for section in self._sections:
            n = len(section["files"])
            y += hdr + 6                     # section button + layout spacing
            rows = max(1, (n + cols - 1) // cols)
            cont_h = rows * row_h - 10 + 16  # -last spacing + flow top/bottom margins
            c0, c1 = y, y + cont_h
            if c1 >= lo and c0 <= hi and n:
                r0 = max(0, int((lo - c0 - 8) // row_h))
                r1 = min(rows - 1, int((hi - c0 - 8) // row_h) + 1)
                for r in range(r0, r1 + 1):
                    out.extend(section["files"][r * cols:(r + 1) * cols])
            y = c1 + 6
        return out

    def _thumb_start(self, key: tuple, prio: int):
        """Start a thumbnail job with its priority recorded — paired with _on_thumb_ready."""
        self._thumb_queued.add(key)
        self._thumb_prio[key] = prio
        if prio == 0:
            self._thumb_bg.add(key)
        self._thumb_pool.start(
            ThumbJob(self.fthumbs, key[0], key[1], self._thumb_signals), prio)

    def _retriage_thumb_queue(self):
        """Cancel queued-but-not-started jobs and return them to the "wanted" set.

        QThreadPool cannot re-prioritize a queued job; it can only clear. So when
        the user makes a large scroll jump, stale (far-from-view) jobs are
        cancelled and the full prioritization is rebuilt from the fresh viewport —
        the key to fixing "it still makes the old mess". Running
        jobs are untouched; their results are discarded harmlessly.
        """
        self._thumb_pool.clear()
        back = list(self._thumb_queued)
        self._thumb_queued.clear()
        for k in back:
            self._thumb_bg.discard(k)
            self._thumb_prio.pop(k, None)
            self._thumb_wanted.add(k)
            self._bg_buckets[self._bg_rank(k)].append(k)

    def _start_visible_thumb_jobs(self):
        # Re-triage on a large scroll jump (more than ~2 pages): rebuild the stale queue
        try:
            if self._mode == "grid":
                _sb = self.grid_scroll.verticalScrollBar()
                _h = max(1, self.grid_scroll.viewport().height())
            else:
                _sb = self.tree.verticalScrollBar()
                _h = max(1, self.tree.viewport().height())
            _y = _sb.value()
            _last = getattr(self, "_triage_y", None)
            if _last is None:
                self._triage_y = _y
            elif abs(_y - _last) > 2 * _h and self._thumb_queued:
                self._triage_y = _y
                self._retriage_thumb_queue()
        except Exception as _exc_dbg:
            dbg_log(f"_start_visible_thumb_jobs failed: {type(_exc_dbg).__name__}")
            pass
        # Phase 0 (geometric): files in the visible area — *even if their cards
        # do not exist yet*. Until now only existing cards
        # could claim priority and bottom-of-list cards did not exist during a
        # fast jump; the grid's pixel-exact geometric computation now finds the
        # visible files directly and starts them at top priority (10). When the
        # card is built later in the flow, peek() hits the fresh cache and shows
        # it instantly.
        if not self._folder_only:
            for f in self._visible_grid_files():
                key = (str(f), 132)
                self._thumb_wanted.discard(key)
                if key not in self._thumb_queued:
                    self._thumb_start(key, 10)
        # Phase 1: existing cards in view — *tree mode only*; in the grid, phase
        # 0 (pixel-exact geometry) covers the same area directly and cheaply with
        # an identical margin. Previously, for every key in the wanted list (a
        # 2000+ file folder means thousands of entries), a geometric mapTo was
        # punched on every 90 ms tick: O(n) per tick = tens of seconds of wasted
        # cycles and a frozen entry into very file-heavy folders (the
        # only way out was closing and reopening the app).
        if self._mode != "grid":
            for key in list(self._thumb_wanted):
                owners = self._thumb_pending.get(key) or []
                if any(self._owner_visible(o) for o in owners):
                    self._thumb_wanted.discard(key)
                    if key not in self._thumb_queued:
                        self._thumb_start(key, 10)
        # Phase 2 (unlimited cache only): the rest also trickle in the background
        # — but with a concurrency cap, so threads always stay free for visible
        # work (phases 0/1); previously the flood of background jobs saturated the
        # queue and the user sat waiting at the bottom of the list.
        if not self._limited:
            self._pump_bg_thumbs()
        # Phase 3: durations of "in-view" videos — only these, not the folder's
        # thousands of files (fixes the hangs on entering a 2000+ file
        # folder)
        self._dur_queue_visible()

    # Light-to-heavy rank for background feeding — cached per *extension* (not
    # per file!) because group_of builds a full Path (~14 µs) and sorting on
    # every 80 ms tick over thousands of keys meant millions of Paths and tens
    # of seconds of freeze (a 2130-file folder once hung the app).
    # Extensions are few, so a cache suffices.
    _BG_ORDER = {"image": 0, "doc": 1, "code": 1, "other": 1, "exe": 1,
                 "audio": 1, "archive": 2, "video": 3}

    def _bg_rank(self, key) -> int:
        cache = getattr(self, "_ext_rank_cache", None)
        if cache is None:
            cache = self._ext_rank_cache = {}
        name = key[0]
        i = name.rfind(".")
        ext = name[i:].lower() if i >= 0 else ""
        r = cache.get(ext)
        if r is None:
            try:
                r = self._BG_ORDER.get(group_of("x" + ext), 1)
            except Exception as _exc_dbg:
                dbg_log(f"_bg_rank failed: {type(_exc_dbg).__name__}")
                r = 1
            cache[ext] = r
        return r

    def _pump_bg_thumbs(self):
        """Gradually feed background jobs (priority 0) up to half the thread cap.

        ("build thumbnails for several video files in under
        a second"): the background pump no longer eagerly builds video/archive
        thumbs — they are built only through the visibility policy when their
        card is actually near the viewport (exactly Explorer's behavior: viewed
        ones immediately, the rest never or when viewed). The pump only advances
        the light buckets (image/document/misc), which take a fraction of a
        second.
        """
        cap = max(2, self._thumb_pool.maxThreadCount() // 2)
        n = 0
        for _round in range(2):
            # Consume from the light buckets only (r=0 image, r=1 doc/misc); every
            # insert/pop is O(1) (instead of a full list sort per tick — that
            # built millions of keys with thousands of files and hung the app).
            # Stale entries are skipped.
            for r in range(2):
                bucket = self._bg_buckets[r]
                while bucket and n < 24 and len(self._thumb_bg) < cap:
                    key = bucket.popleft()
                    if key not in self._thumb_wanted:
                        continue            # stale — already consumed/cancelled
                    self._thumb_wanted.discard(key)
                    if key in self._thumb_queued:
                        continue            # the user-visibility phase already started it
                    self._thumb_start(key, 0)
                    n += 1
                if n >= 24 or len(self._thumb_bg) >= cap:
                    break
            if n or not self._thumb_wanted:
                break
            # Self-healing: re-enqueue the remaining light keys (heavy ones stay
            # untouched in wanted until the visibility policy builds them when
            # near).
            for key in list(self._thumb_wanted):
                if self._bg_rank(key) < 2:
                    self._bg_buckets[self._bg_rank(key)].append(key)
        light_left = bool(self._bg_buckets[0] or self._bg_buckets[1])
        if light_left and not self._limited:
            QTimer.singleShot(80, self._pump_bg_thumbs)

    def _on_hero_ready(self, key: str, png: str, hero: str):
        """Hero poster + backdrop arrived from the background thread — apply
        only if it is still the current title."""
        if key != self._hero_key or self.item is None:
            return                     # the user opened another title in the meantime
        st, item = self.store, self.item
        cat = st.category_of(item)
        pm = load_cache_pixmap(png) if png else None
        if pm is None or pm.isNull():
            if getattr(self, "_movie", None) is None:
                pm = placeholder_poster(
                    st.title(item).strip()[:1] or T("؟"), st.color(cat), 252, 336, 16)
            else:
                pm = None   # animation playing — the layer beneath it is left untouched

        else:
            pm = poster_pixmap(pm, 252, 336, 16, bg="none")   # no frame — the image's own shape
            # The real still frame — still: direct; animated: below the video
            # (empty bars keep their theme color, not the purple placeholder;
            # which clashed with light themes). The video label is a child of the poster and gets
            # drawn over this pixmap — the old "don't touch" note was
            # unnecessary; updating it is safe.
        if pm is not None:
            self.poster.setPixmap(pm)
        hero_pm = load_cache_pixmap(hero) if hero else None
        self.hero.set_background(hero_pm)

    # ------------------------------------------------------------ custom cover
    def hideEvent(self, event):
        """Returning to the library only *hides* the page — a QMovie keeps
        decoding frames until stop() (pointless CPU/battery). On hide, playback
        stops; on return, showEvent restarts the same source."""
        super().hideEvent(event)
        try:
            self._stop_poster_movie()
        except Exception as _exc_dbg:
            dbg_log(f"DetailsPage.hideEvent failed: {type(_exc_dbg).__name__}")

    def showEvent(self, event):
        """Back to the details page — the title's animated cover plays again."""
        super().showEvent(event)
        try:
            if self.isVisible() and self.item is not None \
                    and getattr(self, "_poster_media_src", None) \
                    and getattr(self, "_movie", None) is None:
                # Single-source guard: if set_item already started the movie
                # before the page became visible (initial-entry scenario —
                # show_details calls set_item before setCurrentWidget), a second
                # instance is not created/started; the old instance is never left
                # behind without a stop (CPU/file handle).
                self._set_poster_media(*self._poster_media_src)
        except Exception as _exc_dbg:
            dbg_log(f"DetailsPage.showEvent failed: {type(_exc_dbg).__name__}")

    def _stop_poster_movie(self):
        """Stop the previous animated cover's playback (if any) — before any
        set_item/cover removal.

        The stopped object is freed with deleteLater — previously only the
        Python reference was dropped, and replaced instances, being children of
        the page, stayed alive until the page itself was destroyed (QObject
        accumulation in long sessions). stop() before deleteLater() is
        mandatory and the order is preserved."""
        mv = getattr(self, "_movie", None)
        if mv is not None:
            self._movie = None
            try:
                mv.stop()
            except RuntimeError:
                pass                       # C++ object is gone
            try:
                mv.deleteLater()
            except RuntimeError:
                pass
        self._hide_poster_movie()          # poster is static again — setMovie(None) not needed

    def _poster_slot_rect(self, sw: int, sh: int) -> tuple[int, int, int, int]:
        """Rect fitting the image's aspect ratio inside the 252×336 frame (x, y, w, h)."""
        if sw <= 0 or sh <= 0:
            return 0, 0, 252, 336
        k = min(252 / sw, 336 / sh)
        w, h = max(1, round(sw * k)), max(1, round(sh * k))
        return (252 - w) // 2, (336 - h) // 2, w, h

    def _install_movie_underlay(self) -> None:
        """Install the cover's static frame beneath the animation.

        A square/wide GIF covers only the middle part of the 252×336 frame;
        previously the letter placeholder stayed behind the movie — its
        background is the category color (the default category is purple —
        #a78bfa) — so the empty bars around the movie stayed purple, clashing
        with the paper theme). Now the cover's own static frame (a
        transparent letterbox) sits beneath the movie and the theme color shows
        through the bars; the movie label is a *child* of the poster and draws
        over this layer, so installing it is safe. If the static frame can't be
        read, a transparent layer is placed so the purple placeholder never
        appears behind the movie."""
        src = (getattr(self, "_poster_media_src", None) or (None, None))[1]
        try:
            pm = load_cache_pixmap(src) if src else None
            if pm is not None and not pm.isNull():
                self.poster.setPixmap(poster_pixmap(pm, 252, 336, 16, bg="none"))
                return
            clear = QPixmap(252, 336)
            clear.fill(Qt.GlobalColor.transparent)
            self.poster.setPixmap(clear)
        except Exception as _exc_dbg:
            dbg_log(f"_install_movie_underlay failed: {type(_exc_dbg).__name__}")

    def _set_poster_media(self, src, src_static) -> None:
        """Animated cover → QMovie on the same-ratio child label; static → the
        hero path.

        self.poster itself with a QMovie would crop frames into the fixed frame
        — the same-ratio child label (poster_movie) is the fix for wide/square
        covers. No raise_() so the corner cover button always stays above the
        movie (clickable)."""
        try:
            # Single-source lifecycle: every set/replace first fully stops and
            # detaches the previous instance — previously the second instance
            # simply overwrote the first, leaving it (a child of the page, still
            # running) without a stop: pointless CPU/RAM + an open file handle
            # on Windows.
            self._stop_poster_movie()
            if src is None or src_static is None or Path(src) == Path(src_static):
                self._poster_media_src = None
                self._hide_poster_movie()      # static: the poster arrives via _on_hero_ready
                return
            self._poster_media_src = (src, src_static)
            mv = QMovie(str(src), parent=self)
            mv.setCacheMode(QMovie.CacheMode.CacheAll)
            sz = QImageReader(str(src)).size()   # first-frame size (QMovie.size fills after start)
            sw, sh = (sz.width(), sz.height()) if sz.isValid() else (252, 336)
            self.poster_movie.setGeometry(*self._poster_slot_rect(sw, sh))
            self._install_movie_underlay()   # static frame beneath the movie — not the purple placeholder
            self.poster_movie.setMovie(mv)
            self.poster_movie.show()
            mv.start()
            self._movie = mv
        except Exception as _exc_dbg:
            dbg_log(f"_set_poster_media failed: {type(_exc_dbg).__name__}")
            self._hide_poster_movie()
            self._movie = None

    def _hide_poster_movie(self) -> None:
        """Clear/hide the movie label (without touching the static poster)."""
        try:
            self.poster_movie.setMovie(None)
            self.poster_movie.hide()
        except Exception as _exc_dbg:
            dbg_log(f"_hide_poster_movie failed: {type(_exc_dbg).__name__}")

    def _poster_menu(self, pos=None):
        """Custom-cover menu on the poster (right-click or the corner button)."""
        item = self.item
        if item is None:
            return
        menu = QMenu(self)
        a_pick = menu.addAction(T("🖼 انتخاب کاور…"))
        has_cover = bool(self.store.custom_poster(item.path)[0])
        a_clear = menu.addAction(T("↺ بازگشت به آیکون پوشه")) if has_cover else None
        menu.addSeparator()
        a_open = menu.addAction(T("📂 باز کردن پوشه‌ی کاورها"))
        if pos is None:
            pos = self.poster.rect().center()
        act = menu.exec(self.poster.mapToGlobal(pos))
        menu.deleteLater()   # QObject accumulation in long sessions — deterministic release
        if act is a_pick:
            from ui.cover_actions import pick_cover
            pick_cover(self, self.store, item, self._after_cover_change)
        elif a_clear is not None and act is a_clear:
            from ui.cover_actions import clear_cover
            clear_cover(self, self.store, item, self._after_cover_clear)
        elif act is a_open:
            from ui.cover_actions import open_covers_folder
            open_covers_folder(self)

    def _after_cover_change(self, changed: bool):
        """After a cover change: poster + hero are rebuilt (the same reload
        path).

        previously only this page refreshed and the library heard nothing
        — the card showed the old cover until a refresh/rescan. coverChanged is
        now emitted so main invalidates the poster cache of *that* title.
        The user's place (scroll/expanded tree nodes) is preserved too — a full
        set_item used to reset everything to the top."""
        if not changed:
            return
        self._stop_poster_movie()
        self._tree_cache.clear()      # the exclude-set includes the poster path — everything is stale
        if self.item is not None:
            snap = self._view_state_snapshot()
            self.set_item(self.item)
            self._restore_view_state(snap)
            self.coverChanged.emit(self.item)   # refresh the library card too
        self._set_status_hint(T("کاور به‌روز شد ✓"))

    def _after_cover_clear(self, changed: bool):
        """After clearing the custom cover: back to the folder icon with a
        poster rebuild.

        the same path as replacement — the library card must return to the
        folder icon. The user's place is preserved — same mechanism as
        replacement."""
        if not changed:
            return
        self._stop_poster_movie()
        self._tree_cache.clear()
        if self.item is not None:
            snap = self._view_state_snapshot()
            self.set_item(self.item)
            self._restore_view_state(snap)
            self.coverChanged.emit(self.item)   # refresh the library card too
        self._set_status_hint(T("کاور حذف شد"))

    def _on_thumb_ready(self, path_str: str, png: str, px: int):
        key = (path_str, px)
        self._thumb_queued.discard(key)
        prio = self._thumb_prio.pop(key, None)
        if prio == 0:
            self._thumb_bg.discard(key)
        owners = self._thumb_pending.pop(key, [])
        pm = load_cache_pixmap(png) if png else None
        if pm is None or pm.isNull():
            if png:
                return
            # Generation failed (broken video, encrypted/unknown archive…):
            # fall back to the file-type card so the card never stays a
            # placeholder forever. (ThumbJob now signals on failure too —
            # otherwise the key got stuck in the queue.)
            try:
                cp = self.fthumbs.card(path_str, px)
            except Exception as _exc_dbg:
                dbg_log(f"_on_thumb_ready failed: {type(_exc_dbg).__name__}")
                cp = None
            pm = load_cache_pixmap(cp) if cp else None
            if pm is None or pm.isNull():
                return
        for owner in owners:
            try:
                if isinstance(owner, ThumbCard):
                    owner.set_thumb(rounded_pixmap(pm, 132, 132, 10))
                elif isinstance(owner, QTreeWidgetItem):
                    owner.setIcon(0, QIcon(pm.scaled(
                        26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)))
            except RuntimeError:
                pass   # widget deleted
        if not self._limited:
            self._pump_bg_thumbs()       # refill the free background slots

    def _clear_thumb_pending(self):
        self._thumb_pending.clear()
        self._thumb_wanted.clear()
        self._thumb_prio.clear()
        self._thumb_bg.clear()
        for b in self._bg_buckets.values():
            b.clear()

    def cancel_pending(self):
        """Immediately cancel all of the page's background work — called when
        leaving a folder.

        Even after leaving that folder the app stayed
        slow. Cause: the queue of thousands of thumbnails + the background-feed
        chain + the ffprobe probes of the abandoned title kept draining to the
        end, eating CPU/disk inside the library too. Without disconnecting (the
        page stays alive for the next title), this method empties the queues and
        drops the queued QRunnables; running jobs have their results discarded
        via the RuntimeError guard and the wanted/queued checks.
        """
        try:
            self._clear_thumb_pending()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        for pool in (self._thumb_pool, self._hero_pool, self._dur_pool):
            try:
                pool.clear()            # queued jobs are cancelled
            except Exception as _exc_dbg:
                dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
                pass
        try:
            self._thumb_queued.clear()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        try:
            self._dur_queued.clear()
        except Exception as _exc_dbg:
            dbg_log(f"cancel_pending failed: {type(_exc_dbg).__name__}")
            pass
        self._triage_y = None           # view triage position of the previous title

    def shutdown(self):
        """Clean page shutdown — disconnect signals and cancel the thread
        queues.

        Fixes "RuntimeError: Signal source has been deleted" on exit (user
        report): a running QRunnable emitted signals after its QObject was
        deleted. disconnect + clear + the RuntimeError guard inside the jobs
        themselves (the second line of defense) silence it.
        """
        for sig in (self._thumb_signals, self._hero_signals, self._dur_signals):
            try:
                sig.ready.disconnect()
            except (TypeError, RuntimeError):
                pass
        try:
            self._thumb_sched.stop()
        except Exception as _exc_dbg:
            dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
            pass
        for pool in (self._thumb_pool, self._hero_pool, self._dur_pool):
            try:
                pool.clear()
            except Exception as _exc_dbg:
                dbg_log(f"shutdown failed: {type(_exc_dbg).__name__}")
                pass

    # ------------------------------------------------------------ video durations
    def _dur_apply_or_wait(self, f: Path, owner):
        """Like _dur_lookup but without immediate queueing — computed later for in-view files."""
        key = str(f)
        v = self._dur_known.get(key)
        if v is not None:
            if v > 0:
                self._apply_dur(owner, format_duration(v))
            return
        if not self.store.compute_durations:
            return   # duration computation is off in settings — use the cache only
        self._dur_waits.setdefault(key, []).append(owner)

    def _dur_queue_visible(self):
        """Queue duration computation only for videos within the scroll's visible area."""
        if not self.store.compute_durations or not self._dur_waits:
            return
        try:
            if self._mode == "grid":
                files = self._visible_grid_files()
            else:
                files = None            # tree: the previous behavior (all rows get queued)
        except Exception as _exc_dbg:
            dbg_log(f"_dur_queue_visible failed: {type(_exc_dbg).__name__}")
            return
        if files is None:
            return
        for f in files:
            try:
                if f.suffix.lower() not in VIDEO_EXTS:
                    continue
            except Exception as _exc_dbg:
                dbg_log(f"_dur_queue_visible failed: {type(_exc_dbg).__name__}")
                continue
            key = str(f)
            if key in self._dur_waits and key not in self._dur_queued \
                    and key not in self._dur_known:
                self._dur_queued.add(key)
                self._dur_pool.start(DurationJob(self.dstore, key, self._dur_signals))

    def _dur_lookup(self, f: Path, owner):
        """Apply the cached duration or queue its computation."""
        key = str(f)
        v = self._dur_known.get(key)
        if v is not None:
            if v > 0:
                self._apply_dur(owner, format_duration(v))
            return
        if not self.store.compute_durations:
            return   # duration computation is off in settings — use the cache only
        self._dur_waits.setdefault(key, []).append(owner)
        if key not in self._dur_queued:
            self._dur_queued.add(key)
            self._dur_pool.start(DurationJob(self.dstore, key, self._dur_signals))
        if isinstance(owner, QTreeWidgetItem):
            owner.setText(2, "…")

    def _apply_dur(self, owner, text: str):
        try:
            if isinstance(owner, ThumbCard):
                owner.set_duration(text)
            elif isinstance(owner, QTreeWidgetItem):
                owner.setText(2, text)
        except RuntimeError:
            pass

    def _on_dur_ready(self, path_str: str, val: float):
        self._dur_queued.discard(path_str)
        self._dur_known[path_str] = val
        owners = self._dur_waits.pop(path_str, [])
        text = format_duration(val) if val > 0 else ""
        for owner in owners:
            self._apply_dur(owner, text)
        self._update_dur_chip()

    def _update_dur_chip(self):
        need = len(self._dur_paths)
        probing = self.store.compute_durations
        # When disabled there is nothing to wait for → treat everything as "done"
        done = (sum(1 for p in self._dur_paths if str(p) in self._dur_known)
                if probing else need)
        total = sum(
            self._dur_known.get(str(p), 0)
            for p in self._dur_paths
            if self._dur_known.get(str(p), -1) > 0
)
        # If computation yielded nothing, use the value stored at scan time
        meta_total = 0
        if total <= 0 and self.item is not None:
            meta_total = self.store.total_duration(self.item)
        if not need and not meta_total:
            self.chip_dur.hide()
            return
        if done < need:
            text = (f"⏱ {format_duration(total)}…" if total > 0 else T("⏱ محاسبه…"))
            text += f" {fa(done)}/{fa(need)}"
            self.chip_dur.setText(text)
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها (در حال محاسبه)"))
            self.chip_dur.show()
        elif total > 0:
            self.chip_dur.setText(T("⏱ مجموع: {0}").format(format_duration(total)))
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها"))
            self.chip_dur.show()
        elif meta_total > 0:
            self.chip_dur.setText(T("⏱ مجموع: {0}").format(format_duration(meta_total)))
            self.chip_dur.setToolTip(T("مجموع مدت‌زمان ویدیوها"))
            self.chip_dur.show()
        else:
            self.chip_dur.hide()

    # ------------------------------------------------------------ watch/read status (UI)
    def _status_labels(self, f: Path | None = None) -> tuple[str, str, str, bool]:
        """(done label, partial label, activity name, resume-position?) from
        the current category's profile.

        The auto profile derives the label from the file type (video→watch,
        document→read); a pinned profile (movie/book/custom) imposes its own
        labels.
        All labels pass through T() for full translation.
        """
        p = getattr(self, "_profile", None) or self.store.track_profile("")
        is_vid = bool(f) and Path(f).suffix.lower() in VIDEO_EXTS
        if p.get("id") == "auto":
            b = p["vid"] if is_vid else p["doc"]
            return T(b["done"]), T(b["part"]), T(b["act"]), is_vid
        # Labels pass through T() (translated for built-ins, fallback for custom ones)
        return (T(p.get("done") or "تمام شد"), T(p.get("part") or "نیمه"),
                T(p.get("act") or "پیشرفت"), bool(p.get("id") == "video" and is_vid))

    def _pool_labels(self, has_vids: bool) -> tuple[str, str, str]:
        """Chip labels based on the title's content and profile (no specific
        file).
        All labels pass through T()."""
        p = getattr(self, "_profile", None) or self.store.track_profile("")
        if p.get("id") == "auto":
            b = p["vid"] if has_vids else p["doc"]
            return T(b["done"]), T(b["part"]), T(b["act"])
        return (T(p.get("done") or "تمام شد"), T(p.get("part") or "نیمه"),
                T(p.get("act") or "پیشرفت"))

    def _refresh_watch_ui(self):
        """Chip + progress bar in the header — label from the category's
        tracking profile (watch/read/custom)."""
        if self.item is None or not self._track_on:
            self.chip_watch.hide()
            self.watch_bar.hide()
            return
        # Progress denominator: videos if any, otherwise all of the title's files (manga/book/archive)
        allf = [f for s in (self._sections or []) for f in s["files"]]
        vids = [f for f in allf if f.suffix.lower() in VIDEO_EXTS]
        pool = vids if vids else allf
        total = len(pool)
        done = part = 0
        for f in pool:
            m, _p = self.store.watch_get(self.item, rel_of(self.item, f))
            if m == Store.WATCH_DONE:
                done += 1
            elif m == Store.WATCH_PART:
                part += 1
        if not total or (not done and not part):
            self.chip_watch.hide()
            self.watch_bar.hide()
            return
        done_lbl, part_lbl, act_lbl = self._pool_labels(bool(vids))
        txt = f"👁 {fa(done)}/{fa(total)} {done_lbl}"
        if part:
            txt += f" · ◐ {fa(part)} {part_lbl}"
        self.chip_watch.setText(txt)
        self.chip_watch.setToolTip(
            T("پیشرفت {0} — با راست‌کلیک روی هر فایل علامت بزن").format(act_lbl))
        self.chip_watch.show()
        pct = int(round(100 * (done + 0.5 * part) / max(1, total)))
        self.watch_bar.setRange(0, 100)
        self.watch_bar.setValue(pct)
        self.watch_bar.setToolTip(T("{0}٪ {1}").format(fa(pct), done_lbl))
        self.watch_bar.show()

    def _apply_watch(self, owner, rel: str, mode: int, pos: float):
        """Apply the status visuals to a tree item or card."""
        try:
            if isinstance(owner, ThumbCard):
                # The ◐ badge's unit comes from the file type — video "time",
                # book/document "page N" (it used to say "'5 seconds' instead of
                # 'page 5'").
                try:
                    _resume_ok = self._status_labels(owner.path)[3]
                except Exception as _exc_dbg:
                    dbg_log(f"_apply_watch labels failed: {type(_exc_dbg).__name__}")
                    _resume_ok = owner.path.suffix.lower() in VIDEO_EXTS
                owner.set_watch(mode, pos, page_based=not _resume_ok)
            elif isinstance(owner, QTreeWidgetItem):
                name_txt = owner.data(0, Qt.ItemDataRole.UserRole + 1) or owner.text(0)
                if mode == Store.WATCH_DONE:
                    owner.setForeground(0, QBrush(DONE_COLOR))
                    if "✓" not in owner.text(0):
                        owner.setText(0, "✓ " + str(name_txt))
                elif mode == Store.WATCH_PART:
                    owner.setForeground(0, QBrush(PART_COLOR))
                    if "◐" not in owner.text(0):
                        owner.setText(0, "◐ " + str(name_txt))
                else:
                    owner.setForeground(0, QBrush())
                    owner.setText(0, str(name_txt))
                data = owner.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == "file":
                    fpath = Path(str(data[1]))
                    done_lbl, part_lbl, _act_lbl, resume_ok = self._status_labels(fpath)
                    if mode == Store.WATCH_PART and pos and resume_ok:
                        tip = (T("\n◐ تا {0} {1} — رزومه: {2}").format(format_duration(pos), part_lbl, format_duration_short(pos)))
                    elif mode == Store.WATCH_PART and pos and not resume_ok:
                        # Page number for non-video files (book/manga)
                        tip = T("\n◐ {0} — تا صفحه‌ی {1}").format(part_lbl, fa(int(pos)))
                    elif mode == Store.WATCH_PART:
                        tip = f"\n◐ {part_lbl}"
                    elif mode == Store.WATCH_DONE:
                        tip = f"\n✓ {done_lbl}"
                    else:
                        tip = ""
                    owner.setToolTip(0, str(data[1]) + tip)
        except RuntimeError:
            pass

    def _refresh_watch_rows(self, rel: str):
        if self.item is None:
            return
        mode, pos = self.store.watch_get(self.item, rel)
        for owner in self._watch_rows.get(rel, []):
            self._apply_watch(owner, rel, mode, pos)

    def _after_watch_change(self, rels):
        for rel in rels:
            self._refresh_watch_rows(rel)
        self._refresh_watch_ui()
        self.changed.emit()

    # ------------------------------------------------------------ watch context menu
    def _watch_menu(self, f: Path, gpos, owners_rels: list[str] | None = None):
        rel = rel_of(self.item, f)
        mode, pos = self.store.watch_get(self.item, rel)
        done_lbl, part_lbl, _act_lbl, resume_ok = self._status_labels(f)
        menu = QMenu(self)
        a_play = menu.addAction(T("▶ پخش") + (T(" (ادامه از ") + format_duration_short(pos) + ")"
                                           if mode == Store.WATCH_PART and pos else ""))
        menu.addSeparator()
        a_done = menu.addAction(T("✓ علامت «{0}»").format(done_lbl) if mode != Store.WATCH_DONE
                                else T("✓ {0} — برداشتن علامت").format(done_lbl))
        if resume_ok:
            a_part = menu.addAction(T("◐ {0} — ثبت موقعیت…").format(part_lbl))
        else:
            a_part = menu.addAction(T("◐ علامت «{0}»").format(part_lbl) if mode != Store.WATCH_PART
                                    else T("◐ {0} — برداشتن علامت").format(part_lbl))
        if mode or pos:
            a_clear = menu.addAction(T("↺ پاک کردن وضعیت"))
        else:
            a_clear = None
        act = menu.exec(gpos)
        menu.deleteLater()   # QObject accumulation in long sessions — deterministic release
        if act is a_play:
            self._play(f)
        elif act is a_done:
            to_clear = mode == Store.WATCH_DONE
            if owners_rels:
                for r in owners_rels:
                    self.store.watch_set(self.item, r, 0 if to_clear else Store.WATCH_DONE, 0)
                self._after_watch_change(owners_rels)
            else:
                self.store.watch_set(self.item, rel, 0 if to_clear else Store.WATCH_DONE, 0)
                self._after_watch_change([rel])
        elif act is a_part:
            if not resume_ok:
                self.store.watch_set(self.item, rel,
                                     0 if mode == Store.WATCH_PART else Store.WATCH_PART, 0)
                self._after_watch_change([rel])
                return
            val, ok = QInputDialog.getInt(
                self, T("موقعیت تماشا"),
                T("تا کجای «{0}» دیده‌ای؟ (به دقیقه)").format(f.name),
                value=int(pos // 60) if pos else 0, minValue=0, maxValue=10000)
            if ok:
                self.store.watch_set(self.item, rel, Store.WATCH_PART, val * 60.0)
                self._after_watch_change([rel])
        elif a_clear is not None and act is a_clear:
            self.store.watch_set(self.item, rel, 0, 0)
            self._after_watch_change([rel])

    def _doc_status_actions(self, f: Path):
        """Build the non-video file menu → (menu, actions) — separate from exec
        so the choices (text/option order) are checkable without actually
        opening the menu. Labels come from the category's tracking profile
        (read/done/…)."""
        rel = rel_of(self.item, f)
        mode, _pos = self.store.watch_get(self.item, rel)
        done_lbl, part_lbl, _act_lbl, _resume = self._status_labels(f)
        menu = QMenu(self)
        acts = {
            "open": menu.addAction(T("▶ باز کردن")),
            "folder": menu.addAction(T("📂 نمایش در پوشه")),
        }
        menu.addSeparator()
        acts["done"] = menu.addAction(
            T("✓ علامت «{0}»").format(done_lbl) if mode != Store.WATCH_DONE
            else T("✓ {0} — برداشتن علامت").format(done_lbl))
        acts["part"] = menu.addAction(
            T("◐ {0} — تا صفحه‌ی {1}").format(part_lbl, fa(int(_pos)))
            if mode == Store.WATCH_PART and _pos else
            (T("◐ {0} — برداشتن علامت").format(part_lbl) if mode == Store.WATCH_PART
             else T("◐ {0}…").format(part_lbl)))
        return menu, acts

    def _doc_status_choose(self, f: Path, acts: dict, act):
        """Apply a status-menu choice (read/partial/open) to the file.
        Partial → a dialog to record the page number."""
        if act is None:
            return
        rel = rel_of(self.item, f)
        mode, _pos = self.store.watch_get(self.item, rel)
        if act is acts["open"]:
            self._play(f)  # use the category's opener
            return
        elif act is acts["folder"]:
            open_path(f.parent)
        elif act is acts["done"]:
            self.store.watch_set(self.item, rel,
                                 0 if mode == Store.WATCH_DONE else Store.WATCH_DONE, 0)
            self._after_watch_change([rel])
        elif act is acts["part"]:
            if mode == Store.WATCH_PART:
                # Clear the mark
                self.store.watch_set(self.item, rel, 0, 0)
                self._after_watch_change([rel])
            else:
                # Record the page number for a partial read
                from PySide6.QtWidgets import QInputDialog
                page, ok = QInputDialog.getInt(
                    self, T("ثبت پیشرفت مطالعه"),
                    T("تا چه صفحه‌ای خوانده‌اید؟ (۰ = فقط علامت نیمه‌تمام)"),
                    value=int(_pos) if _pos else 0, minValue=0, maxValue=99999)
                if ok:
                    self.store.watch_set(self.item, rel, Store.WATCH_PART, float(page))
                    self._after_watch_change([rel])

    def _doc_status_menu(self, f: Path, gpos):
        """Context menu for non-video files (PDF/archive/image…): open + the
        "✓ read / ◐ partially read" mark — added for manga and
        books."""
        menu, acts = self._doc_status_actions(f)
        self._doc_status_choose(f, acts, menu.exec(gpos))
        menu.deleteLater()

    def _dir_watch_menu(self, dir_path: Path, gpos):
        """Folder menu: group marking — videos if any, otherwise all files.
        The label comes from the category's tracking profile ("✓ all seen/read"
        or a custom label)."""
        base = Path(dir_path)
        videos, others = [], []
        if base.is_dir():
            for p in base.rglob("*"):
                if not p.is_file() or p.name.startswith("."):
                    continue
                (videos if p.suffix.lower() in VIDEO_EXTS else others).append(p)
        targets = videos if videos else others
        done_lbl, _part_lbl, _act_lbl = self._pool_labels(bool(videos))
        menu = QMenu(self)
        a_open = menu.addAction(T("📂 باز کردن پوشه"))
        a_done = menu.addAction(T("✓ همه‌ی این بخش {0}").format(done_lbl))
        a_clear = menu.addAction(T("↺ پاک کردن وضعیت این بخش"))
        act = menu.exec(gpos)
        menu.deleteLater()   # QObject accumulation in long sessions — deterministic release
        if act is a_open:
            open_path(dir_path)
            return
        if act not in (a_done, a_clear):
            return
        rels = [rel_of(self.item, v) for v in targets]
        for r in rels:
            self.store.watch_set(self.item, r, Store.WATCH_DONE if act is a_done else 0, 0)
        self._after_watch_change(rels)

    # ------------------------------------------------------------ tracked playback
    @staticmethod
    def _opener_is_safe(opener: str) -> bool:
        """Validate the opener path (public-release security).

        The path stored in config.json must be an *existing absolute file* with
        a Windows executable extension (.exe/.com); never shell=True, and raw
        names resolvable through PATH (like cmd or powershell) are rejected so
        a tampered/shared config.json cannot end up executing interpreters.
        Real openers always come from the file-picker dialog with an absolute
        path, so this check breaks no legitimate setting.

        BatBadBut: .bat/.cmd are NOT accepted anymore — Popen runs
        them through cmd.exe /c, and a media file name containing cmd
        metacharacters (& | ^ % ") then executes injected commands. Only
        binary executables are allowed as library openers."""
        try:
            from pathlib import Path as _P
            p = _P(str(opener or "").strip())
            if not p.is_absolute() or not p.is_file():
                return False
            if p.suffix.lower() not in (".exe", ".com"):
                return False
            return True
        except Exception as _exc_dbg:
            dbg_log(f"_opener_is_safe failed: {type(_exc_dbg).__name__}")
            return False

    def _play(self, f: Path):
        """Play a file — monitoring and result recording happen in the main
        window (PlayTracker).
        Non-video files open with the category's configured opener."""
        if f.suffix.lower() not in VIDEO_EXTS:
            # If a category opener is configured for this extension, open with it
            if self.item is not None:
                cat = self.store.category_of(self.item)
                opener = self.store.cat_opener_for(cat, f.suffix)
                if opener:
                    if not self._opener_is_safe(opener):
                        # Security: invalid opener — don't run it; log and
                        # fall through to the Windows default opener (the
                        # fallback must not stay silent)
                        try:
                            from watchtime import _dbg, safe_ref
                            _dbg(f"cat opener rejected by validation: "
                                 f"{safe_ref(opener)} — falling back to shell open")
                        except Exception as _exc_dbg:
                            dbg_log(f"_play failed: {type(_exc_dbg).__name__}")
                            pass
                    else:
                        import subprocess as _sp49
                        try:
                            _sp49.Popen([opener, str(f)])
                            return
                        except Exception as e:
                            try:
                                from watchtime import _dbg, safe_ref
                                _dbg(f"cat opener launch failed: {safe_ref(opener)}: "
                                     f"{type(e).__name__}")
                            except Exception as _exc_dbg:
                                dbg_log(f"_play failed: {type(_exc_dbg).__name__}")
                                pass
            open_path(f)
            return
        self.playRequested.emit(self.item, f)

    def refresh_watch(self, rel: str):
        """Refresh the UI after a file's watch status changes (from the central tracker)."""
        self._refresh_watch_rows(rel)
        self._refresh_watch_ui()

    def _set_status_hint(self, msg: str):
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage(msg, 6000)

    # ------------------------------------------------------------ smart play
    def _best_play_target(self) -> Path | None:
        """Play target: the partial with the furthest position, else the first
        unseen, else the first video.
        For video-less folders (book/manga/image) the first unseen
        watchable file — or simply the first watchable — is opened."""
        videos = [f for s in (self._sections or []) for f in s["files"]
                  if f.suffix.lower() in VIDEO_EXTS]
        # No videos → try the watchable files (book/manga/archive/image)
        if not videos:
            from scanner import item_watchables
            watchable = item_watchables(self.item)
            if not watchable:
                p = Path(self.item.path)
                return p if p.is_file() else None
            # Partials first (furthest position)
            partials = []
            for f in watchable:
                mode, pos = self.store.watch_get(self.item, rel_of(self.item, f))
                if mode == Store.WATCH_PART:
                    partials.append((pos, f))
            if partials:
                return max(partials, key=lambda t: t[0])[1]
            # First unseen
            for f in watchable:
                mode, _ = self.store.watch_get(self.item, rel_of(self.item, f))
                if mode != Store.WATCH_DONE:
                    return f
            # First file
            return watchable[0]
        partials = []
        for f in videos:
            mode, pos = self.store.watch_get(self.item, rel_of(self.item, f))
            if mode == Store.WATCH_PART:
                partials.append((pos, f))
        if partials:
            return max(partials, key=lambda t: t[0])[1]
        for f in videos:
            mode, _ = self.store.watch_get(self.item, rel_of(self.item, f))
            if mode != Store.WATCH_DONE:
                return f
        return videos[0]

    def play_smart(self):
        if self.item is None:
            return
        target = self._best_play_target()
        if target is not None:
            self._play(target)

    # ------------------------------------------------------------ tree
    def _build_tree(self):
        self.tree.clear()
        if self._hier is None and self.item is not None:
            # Lazily build the hierarchy — only when tree mode is actually needed
            try:
                _p, _ex, _lo, _only = getattr(self, "_hier_params", None) or (
                    self.item.path, frozenset(), getattr(self.item, "loose_group", False), None)
                self._hier = get_file_hierarchy(_p, exclude=set(_ex), loose=_lo,
                                                only_exts=_only)
            except Exception as _exc_dbg:
                dbg_log(f"_build_tree failed: {type(_exc_dbg).__name__}")
                self._hier = None
        style = QApplication.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_fallback = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        def add_children(parent_item, node):
            for d in node["dirs"]:
                it = QTreeWidgetItem([d["path"].name, ""])
                it.setIcon(0, dir_icon)
                it.setData(0, Qt.ItemDataRole.UserRole, ("dir", str(d["path"])))
                parent_item.addChild(it)
                add_children(it, d)
            for f in self._sorted(node["files"]):   # same criterion as the grid — the tree stays sorted too
                try:
                    size = human_size(f.lstat().st_size)
                except OSError:
                    size = ""
                it = QTreeWidgetItem([f.name, size, ""])
                it.setData(0, Qt.ItemDataRole.UserRole + 1, f.name)  # raw name for status resets
                pm = self._thumb_px_or_queue(f, 26, it)
                it.setIcon(0, QIcon(pm.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                           if pm else file_fallback)
                it.setData(0, Qt.ItemDataRole.UserRole, ("file", str(f)))
                it.setToolTip(0, str(f))
                if f.suffix.lower() in VIDEO_EXTS:
                    self._dur_lookup(f, it)
                if self._track_on:
                    # Status for every file type — video "seen", PDF/archive "read"
                    rel = rel_of(self.item, f)
                    self._watch_rows.setdefault(rel, []).append(it)
                    mode, pos = self.store.watch_get(self.item, rel)
                    if mode:
                        self._apply_watch(it, rel, mode, pos)
                parent_item.addChild(it)

        if self._hier:
            root_item = self.tree.invisibleRootItem()
            if self.item.path.is_file():
                node = {"path": self.item.path.parent, "dirs": [], "files": [self.item.path]}
            else:
                node = self._hier
            add_children(root_item, node)
            self.tree.expandToDepth(0)

    def _tree_dblclick(self, it: QTreeWidgetItem, _col):
        kind, p = it.data(0, Qt.ItemDataRole.UserRole) or (None, None)
        if kind == "dir":
            it.setExpanded(not it.isExpanded())
        elif kind == "file":
            self._play(Path(p))

    def _tree_ctx_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        kind, p = it.data(0, Qt.ItemDataRole.UserRole) or (None, None)
        gpos = self.tree.viewport().mapToGlobal(pos)
        if kind == "file":
            f = Path(p)
            ext = f.suffix.lower()
            if not self._track_on:
                self._generic_file_menu(f, gpos)
            elif ext in VIDEO_EXTS:
                self._watch_menu(f, gpos)
            else:
                self._doc_status_menu(f, gpos)
        elif kind == "dir":
            if self._track_on:
                self._dir_watch_menu(Path(p), gpos)
            else:
                menu = QMenu(self)
                a_open = menu.addAction(T("📂 باز کردن پوشه"))
                _act = menu.exec(gpos)
                menu.deleteLater()
                if _act is a_open:
                    open_path(p)

    # ------------------------------------------------------------ thumbnails
    def _build_grid(self):
        clear_layout(self.grid_layout)
        self._grid_gen = getattr(self, "_grid_gen", 0) + 1
        if not self._sections:
            empty = QLabel(T("فایلی در این پوشه نیست."))
            empty.setObjectName("MutedLabel")
            empty.setStyleSheet("padding:20px;")
            self.grid_layout.addWidget(empty)
            return
        # Cards had to wait for the whole batch to build and then they
        # popped in at once; leaving and coming back restarted the wait —
        # batch card building is gone! Each section is a "virtual" container
        # that knows its height analytically from the start; only rows near the
        # viewport (± two pages) actually become cards and are pruned/freed as
        # you move away. Entering a 2000-file folder now means a few headers + a
        # few dozen cards — a fraction of a second.
        self._vsections = []
        for section in self._sections:
            btn, container = self._add_grid_section(section["title"], section["files"])
            # real section-header height for geometric visibility (fallback inside _grid_metrics)
            _h = btn.sizeHint().height()
            if _h > 4:
                self._grid_hdr_h = _h
            self._vsections.append(container)
        self.grid_layout.addStretch(1)
        # First materialization: immediately (no waiting for a paint event) and
        # once more after the layout settles — both are cheap since only the view
        # window is materialized.
        self._grid_sync_visible()
        QTimer.singleShot(0, self._grid_sync_visible)

    def _grid_sync_visible(self):
        """Materialize the view window for every virtual section.

        Called on scroll, resize and after sections are built/collapsed; for
        each section it translates the "viewport ± two pages" intersection into
        local coordinates and hands it to the section (_VSection.sync_window
        does the prune/build).
        """
        sects = getattr(self, "_vsections", None) or []
        if getattr(self, "_mode", "grid") != "grid" or not sects:
            return
        try:
            vh = max(1, self.grid_scroll.viewport().height())
            y0 = self.grid_scroll.verticalScrollBar().value()
        except Exception as _exc_dbg:
            dbg_log(f"_grid_sync_visible failed: {type(_exc_dbg).__name__}")
            return
        lo, hi = y0 - vh, y0 + 2 * vh          # viewport + two pages below + one page above
        for sec in sects:
            try:
                if not sec.isVisible():
                    continue                       # noqa: B008 — intentional snapshot
                top = sec.mapTo(self.grid_container, QPoint(0, 0)).y()
            except RuntimeError:
                continue                           # widget deleted
            if top + sec.height() < lo or top > hi:
                sec.sync_window(-(10 ** 9), -(10 ** 9) + 1)   # section fully out of view → prune
                continue
            sec.sync_window(lo - top, hi - top)

    def _make_grid_card(self, f: Path) -> ThumbCard:
        try:
            size_text = human_size(f.lstat().st_size)
        except OSError:
            size_text = ""
        card = ThumbCard(f, placeholder_poster(f.suffix.lstrip(".")[:1] or T("؟"),
                                               "#33507c", 132, 132, 10), size_text)
        card.rel = rel_of(self.item, f)
        # The cover/icon file is no longer removed from the listing — its card gets
        # a "cover/folder icon" tag so the user knows this is the title's image.
        try:
            if str(f) in (getattr(self, "_cover_paths", None) or set()):
                card.set_tag(T("آیکون پوشه") if f.suffix.lower() in (".ico", ".icl")
                             else T("کاور"))
        except RuntimeError as _exc_dbg:
            dbg_log(f"cover tag failed: {type(_exc_dbg).__name__}")

        def _menu(c, gp):
            ext = c.path.suffix.lower()
            if not self._track_on:
                self._generic_file_menu(c.path, gp)
            elif ext in VIDEO_EXTS:
                self._watch_menu(c.path, gp)
            else:
                self._doc_status_menu(c.path, gp)

        card.watchMenuRequested.connect(_menu)
        card.playRequested.connect(self._play)
        pm = self._thumb_px_or_queue(f, 132, card)
        if pm is not None:
            card.set_thumb(rounded_pixmap(pm, 132, 132, 10))
        if f.suffix.lower() in VIDEO_EXTS:
            # The cached value is applied; computation is queued only for
            # "in-view" files — previously every video card build immediately
            # threw an ffprobe into the queue, which for a multi-thousand-file
            # folder meant thousands of subprocesses (entering a
            # busy folder slowed/hung the app").
            self._dur_apply_or_wait(f, card)
        if self._track_on:
            row = self._watch_rows.setdefault(card.rel, [])
            # Pruned copies of this card (virtualization) are dropped alongside
            row[:] = [w for w in row if not _widget_dead(w)]
            row.append(card)
            mode, pos = self.store.watch_get(self.item, card.rel)
            if mode:
                self._apply_watch(card, card.rel, mode, pos)
        return card

    def _add_grid_section(self, title: str, files: list):
        btn = QPushButton(T("▼  {0}   ·   {1} فایل").format(title, fa(len(files))))
        btn.setObjectName("SectionToggle")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Root of the fixed icon-count bug: the long
        # title text inflated the button's minimum width, which through the
        # layout pinned the whole container's minimum width so it could never
        # get narrower — the Ignored policy means the button's width no longer
        # locks a minimum (no matter how long the text).
        btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        btn.setMinimumWidth(0)
        self.grid_layout.addWidget(btn)

        # Virtual section — no more batch card building; the section knows its
        # height analytically and materializes only the near-viewport window
        try:
            hint_w = max(120, self.grid_scroll.viewport().width() - 10)
        except Exception as _exc_dbg:
            dbg_log(f"_add_grid_section failed: {type(_exc_dbg).__name__}")
            hint_w = 0
        container = _VSection(files, self._make_grid_card,
                              notify=self._grid_sync_visible, width_hint=hint_w)
        self.grid_layout.addWidget(container)

        btn.clicked.connect(lambda _c=False, b=btn, w=container, t=title, count=len(files):  # noqa: B008 — deliberate snapshot
                            self._toggle_section(b, w, t, count))
        return btn, container

    def _toggle_section(self, btn, container, title, count):
        vis = container.isVisible()
        container.setVisible(not vis)
        btn.setText(T("{0}  {1}   ·   {2} فایل").format('▼' if not vis else '▶', title, fa(count)))
        QTimer.singleShot(0, self._grid_sync_visible)   # the reopened section gets its window built

    def _generic_file_menu(self, f: Path, gpos):
        menu = QMenu(self)
        a_open = menu.addAction(T("▶ باز کردن"))
        a_folder = menu.addAction(T("📂 نمایش در پوشه"))
        act = menu.exec(gpos)
        menu.deleteLater()   # QObject accumulation in long sessions — freeing is guaranteed
        if act is a_open:
            open_path(f)
        elif act is a_folder:
            open_path(f.parent)

    # ------------------------------------------------------------ actions
    def _open_folder(self):
        if self.item:
            p = self.item.path if self.item.path.is_dir() else self.item.path.parent
            open_path(p)

    # ------------------------------------------------------------ category box in the header
    def _fill_hero_cat_combo(self):
        """The header's category badge = the category box itself (user
        review: "make it one with the category name"). Its text is the current
        category's name and its color comes from the category; both change on a
        switch. If there is no subcategory to switch to, it shows the same name
        as a single option so the header layout stays stable."""
        st = self.store
        cur = st.category_of(self.item) if self.item else ""
        view = st.cat_switch_view(self.item) if self.item else None
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        if view:
            labels = view.get("labels") or {}
            for full in view["options"]:
                self.cat_combo.addItem(labels.get(full, full), full)
            idx = self.cat_combo.findData(view["current"])
            self.cat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        elif cur:
            self.cat_combo.addItem(cur, cur)       # display only — nothing to switch
        self.cat_combo.blockSignals(False)
        if cur:
            from i18n import disp_cat as _dcat45
            self.cat_combo.setToolTip(
                T("دسته: {0} — کلیک برای تعویض").format(_dcat45(cur).replace("/", " › "))
                if view else T("دسته: {0}").format(_dcat45(cur).replace("/", " › ")))
            self.cat_combo.show()
            # Category name in the header (display only)
            self.cat_name_lbl.setText(_dcat45(cur).split("/")[-1])
            self.cat_name_lbl.show()
        else:
            self.cat_combo.hide()
            self.cat_name_lbl.hide()

    def _on_hero_cat(self, i: int):
        if not self.item:
            return
        data = self.cat_combo.itemData(i)
        if not data or data == self.store.category_of(self.item):
            return
        self.store.set_category(self.item.path, data)
        # The page itself must sync too — the tracking profile/row labels/the
        # "mark all seen" button come from the new category's profile; previously
        # only Library was notified and the header stayed stale until the next
        # visit.
        try:
            self._profile = self.store.track_profile(data)
            self._track_on = bool(self._profile.get("enabled", True))
            self._fill_hero_cat_combo()
            self._refresh_watch_ui()
            self._refresh_watch_all_btn()
        except Exception as _exc_dbg:
            dbg_log(f"_on_hero_cat refresh failed: {type(_exc_dbg).__name__}")
        self.changed.emit()                      # the library rebuilds
        from i18n import disp_cat as _dcat45
        self._set_status_hint(T("📁 دسته → {0}").format(_dcat45(data).replace('/', ' › ')))

    # ------------------------------------------------ the "mark whole title seen" button
    def _refresh_watch_all_btn(self):
        """Sync the "✓ mark all ↔ clear" button with the title's current status."""
        btn = getattr(self, "btn_watch_all", None)
        if btn is None:
            return
        if self.item is None or not getattr(self, "_track_on", True):
            btn.hide()
            return
        try:
            total = self.store.watchable_total(self.item)
        except Exception as _exc_dbg:
            dbg_log(f"_refresh_watch_all_btn failed: {type(_exc_dbg).__name__}")
            total = len(item_watchables(self.item))
        if not total:
            btn.hide()
            return
        done, _part = self.store.watch_progress(self.item)
        all_done = done >= total > 0
        done_lbl, _pl, _al = self._pool_labels(bool(self.item.video_count))
        if all_done:
            btn.setText(T("↺ برداشتن علامت «همه {0}»").format(done_lbl))
            btn.setToolTip(
                T("پاک‌کردن علامتِ تماشا از هر {0} فایلِ قابل‌مصرفِ این عنوان").format(fa(total)))
        else:
            btn.setText(T("✓ همه {0}").format(done_lbl))
            btn.setToolTip(
                T("علامت‌گذاری یک‌جای هر {0} فایلِ قابل‌مصرف به‌عنوان «{1}» — "
                  "برای مواردی که از قبل دیده‌ای؛ دیگر لازم نیست دونه‌دونه بزنی")
                .format(fa(total), done_lbl))
        btn.show()

    def _toggle_watch_all(self):
        """Button click: mark the whole title seen ↔ clear the marks — all at
        once, fast ("mark movies I've already seen as
        watched faster")."""
        if self.item is None:
            return
        files = item_watchables(self.item)
        if not files:
            self._set_status_hint(T("فایلِ قابل‌مصرفی (ویدیو/کتاب/فشرده/…) در این عنوان نیست"))
            return
        done, _part = self.store.watch_progress(self.item)
        make = not (len(files) > 0 and done >= len(files))
        rels = []
        for f in files:
            r = rel_of(self.item, f)
            self.store.watch_set(self.item, r, Store.WATCH_DONE if make else 0, 0)
            rels.append(r)
        self._after_watch_change(rels)           # rows + progress chip + library signal
        self._refresh_watch_all_btn()
        done_lbl, _pl, _al = self._pool_labels(bool(self.item.video_count))
        self._set_status_hint(
            T("✓ هر {0} فایلِ «{1}» {2} علامت خورد").format(
                fa(len(files)), self.store.title(self.item), done_lbl)
            if make else
            T("○ علامتِ تماشای «{0}» پاک شد").format(self.store.title(self.item)))

    def _refresh_fav_btn(self):
        fav = self.store.fav(self.item.path)
        self.btn_fav.setText(T("★ حذف از علاقه‌مندی‌ها") if fav else T("☆ افزودن به علاقه‌مندی‌ها"))

    def _refresh_desc(self):
        desc = self.store.desc(self.item.path)
        if desc:
            self.desc_label.setText(desc)
            self.desc_label.setObjectName("DescBox")
            self.desc_label.setStyleSheet("")
        else:
            self.desc_label.setText(T("توضیحاتی ثبت نشده — از «ویرایش اطلاعات» یا دکمه‌ی ✏ اضافه کن."))
            self.desc_label.setObjectName("DescEmpty")
            self.desc_label.setStyleSheet(
                "background:rgba(128,128,128,0.07); border:1px dashed rgba(128,128,128,0.35);"
                "border-radius:12px; padding:10px 12px;"
)

    def _toggle_fav(self):
        self.store.set_meta(self.item.path, favorite=not self.store.fav(self.item.path))
        self._refresh_fav_btn()
        self.changed.emit()

    # Personal ⭐ rating
    def _refresh_rate_combo(self):
        if getattr(self, "rate_combo", None) is None or self.item is None:
            return
        _v48 = self.store.rating(self.item.path)
        _i48 = self.rate_combo.findData(_v48)
        self.rate_combo.blockSignals(True)
        self.rate_combo.setCurrentIndex(_i48 if _i48 >= 0 else 0)
        self.rate_combo.blockSignals(False)

    def _on_rate_change(self, _i):
        if self.item is None:
            return
        _v48 = int(self.rate_combo.currentData() or 0)
        self.store.set_meta(self.item.path, rating=_v48 if _v48 else None)  # None ← clears the key
        self.changed.emit()                     # cards refresh their star badge

    def _copy_path(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(str(self.item.path))

    def _edit_dialog(self):
        dlg = ItemEditDialog(self.item, self.store, self)
        # The cover applies immediately from the edit dialog; Cancel must
        # refresh the page too — without this, the meta is new but the
        # cover/header stay stale.
        try:
            dlg.coverTouched.connect(self._on_edit_cover_touch)
        except Exception as _exc_dbg:
            dbg_log(f"_edit_dialog coverTouched failed: {type(_exc_dbg).__name__}")
        if dlg.exec():
            self.set_item(self.item)
            self.changed.emit()

    def _on_edit_cover_touch(self, item):
        """The cover changed from the edit dialog (Save/Cancel alike) — the same path as replacement."""
        if item is None or self.item is None \
                or str(item.path) != str(self.item.path):
            return
        self._stop_poster_movie()
        self._tree_cache.clear()
        snap = self._view_state_snapshot()
        self.set_item(self.item)
        self._restore_view_state(snap)
        self.coverChanged.emit(self.item)      # refresh the library card too

    def _edit_desc(self):
        self.desc_editor.setPlainText(self.store.desc(self.item.path))
        self.desc_label.hide()
        self.btn_desc_edit.hide()
        self.desc_editor.show()
        self.desc_btns.show()
        self.desc_editor.setFocus()

    def _save_desc(self):
        self.store.set_meta(self.item.path, description=self.desc_editor.toPlainText().strip() or None)
        self._end_desc_edit()
        self.changed.emit()

    def _end_desc_edit(self):
        self.desc_editor.hide()
        self.desc_btns.hide()
        self.desc_label.show()
        self.btn_desc_edit.show()
        self._refresh_desc()
