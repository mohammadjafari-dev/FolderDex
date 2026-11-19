# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Mohammad Jafari

"""Background thumbnail prebuilding — the "Prebuild after scan" settings option.

Contract: when enabled, adding a new path builds all of its thumbnails
right away. After a scan finishes, low-pressure parallel threads run:
  Phase 1) poster for every title, using the full fallback chain (icon →
           image → video → PDF → archive content) — quick visual impact on
           the library page;
  Phase 2) real thumbnails for every single file of each title (the details
           grid size, 132px) with the same rules as the details page (the
           title poster/icon is removed from the file list).

Design notes:
- Both phases run *in parallel* (previously single-threaded, where the
  GIL plus subprocesses serialized everything) — with the fresh WebP
  encoding, build time drops several-fold;
- Before phase 2 starts, the prebuilder *lists* the files so the "x of y"
  reported to the progress bar is exact and real (a progress
  bar with numbers).

Queue design:
- The file queue is split into **light/heavy** queues: images and documents
  (fast, in-process) get the bigger pool and go *first*; archives and videos
  (each a multi-second subprocess) get a separate pool and go *after* — a few
  archives can no longer hold the whole queue hostage, and light thumbnails
  land in the cache much sooner (faster visual impact);
- The serial phase-2a listing (174 consecutive get_file_tree calls) now runs
  through a small parallel pool;
- Thread counts rose a bit — the work mostly waits on subprocesses/disk, not
  CPU.
Both phases share the same on-disk cache, so duplicated work is zero:
anything already built (say, while browsing) is not built again. Gentle
pressure: a few milliseconds of pause after every few files so the disk/CPU
stays free for the UI.

The **thumbnail
manifest** — the same pattern as the durations manifest, but for prebuild
(it re-checks only titles whose files actually changed, so restoring a
backup does not spend minutes re-checking untouched thumbnails).
A new phase 0 builds a "fingerprint" of every title's files (relative name +
size + mtime, via the cheap Windows os.scandir) and compares it with the
manifest stored in the database:
- Equal → unchanged title: no poster, no files, no archive retry — fully
  skipped;
- Different/new → only that title is inspected (e.g. when an image was added
  to the folder). The file list comes from this same phase-0 walk;
  get_file_tree no longer runs again.
The manifest lives in data.db, so it also travels inside the .mcbak backup
file and comes back "warm" after a restore. Clearing/trimming the cache also
clears manifests, so a missing thumbnail can never stay hidden. If an archive
hits a "soft" failure, its title is retried at most 2 times on later runs and
then skipped like everything else (a capped retry — not minutes of wasted
work on every run).
_MANIFEST_ERA: when the thumbnail logic changes, this token is bumped so old
manifests are invalidated automatically and everything is re-checked once.
"""
from __future__ import annotations

from i18n import T

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hwinfo
from filethumbs import FileThumbs, group_of, poster_png_fallback
from scanner import LOOSE_DOC_EXTS, fa, fa_mmss, thumb_manifest
from watchtime import dbg_log


def cover_ready(item, st, cache_get, ft=None, px: int = 256) -> bool:
    """Does this title's card have a real cover?

    A "skip as unchanged" is only allowed when a cover actually exists on
    disk: a custom poster / folder icon / generated thumbnail for a chain
    candidate. Previously only the content fingerprint was measured — a title
    whose poster was never built (e.g. a video soft-fail without ffmpeg)
    stayed a placeholder forever after its retry budget ran out, and no later
    run ever touched it.

    the preview_video/pdf/archive candidates are now checked in the
    cache they are ACTUALLY built into. The poster chain
    (poster_png_fallback → FileThumbs.video/pdf/archive_thumb_only) caches
    under the FileThumbs key (path|mtime|size|group|px|v5); the old check ran
    ThumbnailCache.get — a different key layout AND a Pillow decode of a
    video/PDF/archive (always fails) — so the answer was always False and the
    smart skip never fired for such titles. Pass ft=FileThumbs + px from the
    prebuilder; the legacy cache_get path stays for compatibility."""
    try:
        if st.custom_poster(item.path)[0]:
            return True
    except Exception:
        pass
    if getattr(item, "icon_path", None):
        return True
    for cand in (getattr(item, "preview_video", None),
                 getattr(item, "preview_pdf", None),
                 getattr(item, "preview_archive", None)):
        if not cand:
            continue
        if ft is not None:
            try:
                # The exact key the poster chain wrote (same mtime/size guard:
                # a changed source file = a miss = re-inspect).
                p = ft._cache_path(Path(cand), group_of(Path(cand)), int(px))
                if p.exists():
                    return True
            except Exception as _exc_dbg:
                dbg_log(f"cover_ready failed: {type(_exc_dbg).__name__}")
        try:
            png = cache_get(cand)
        except Exception:
            png = None
        if png and Path(png).exists():
            return True
    return False

# Light/heavy queue split — lights need no subprocess and are fast; heavies
# (archives/videos) each run their own multi-second process and must not
# block the light queue (two queues: fast first, slow afterwards).
_HEAVY_GROUPS = {"archive", "video"}

# Thumbnail-manifest era — bump by one whenever the thumbnail logic changes,
# so manifests from previous generations are invalidated automatically and
# every title is re-checked once.
_MANIFEST_ERA = "t2"     # ns-mtime manifests — old "t1" records are invalidated once
# Retry budget on later runs for a title whose archive soft-failed:
_RETRY_BUDGET = 2


class Prebuilder:
    """Prebuild thread manager — safe start/stop; a new scan cancels the old thread."""

    # Light parallelism — thumbnailing is more I/O and subprocess than raw CPU
    # math. Lights (images/docs) get more threads; heavies get a separate pool.
    # Heavies split in two — archives (a heavy 7z subprocess → moderate) and
    # videos, which via the Windows-centric chain (Shell cache/render) are
    # mostly I/O and want more threads.
    # Measured: ~3 seconds per heavy file — video/image thumbnails spend
    # most of their time in subprocesses/decoding, not Python CPU, so more
    # threads than cores costs nothing. On 8 cores: 16 video/file threads,
    # 8 archive threads.
    FILE_WORKERS = max(8, min(16, (os.cpu_count() or 4) * 2))
    HEAVY_WORKERS = max(3, min(6, (os.cpu_count() or 4) // 2 + 1))  # backwards compatibility
    # The "cache-limit... cache-size error" report, plus the wish that
    # "everything should be cached but fast" — the real bottleneck is I/O and
    # subprocesses, not Python. So a core-increase move: archives ×2 cores
    # (6→12 threads) and videos ×3 cores (12→24 threads) so the heavy prebuild
    # phase finishes roughly twice as fast.
    # ("Parallelize based on hardware specs"): thread counts are
    # no longer a function of cores alone — hwinfo also reads RAM size (little
    # RAM → fewer threads so the system does not choke); the caps rose a bit
    # (archives 12→14, videos 24→28) because the real bottleneck is drive I/O,
    # not CPU; MC_VIDEO_WORKERS / MC_ARCHIVE_WORKERS can override for debugging.
    ARCHIVE_WORKERS = hwinfo.archive_workers()
    VIDEO_WORKERS = hwinfo.video_workers()
    # Earlier these two phases were locked at fixed caps of 4 and 3
    # threads, and disk-type detection was computed only *after*
    # them — so the poster phase (which for icon-less folders is the very
    # same ffmpeg extraction) got only 3 threads on a 10-core machine. Both
    # are now decided inside _run
    # from hwinfo + disk type; these class values are just the pre-computation
    # fallback. MC_POSTER_WORKERS / MC_LIST_WORKERS can override for debugging
    # (hwinfo.poster_workers / list_workers).
    LIST_WORKERS = hwinfo.list_workers()
    POSTER_WORKERS = hwinfo.poster_workers()

    def __init__(self, store, cache, progress=None, counts=None):
        self.store = store
        self.cache = cache
        # Progress notification to the UI — callbacks that must reach the main
        # thread thread-safely themselves (main.py does it via a queued Qt signal).
        self.on_progress = progress     # descriptive text → status bar
        self.on_counts = counts         # (phase, done, total) → numeric progress bar
        self._lock = threading.Lock()
        self._gen = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # While DurationWorker is reading the headers of thousands of videos
        # from the same drive, the heavy prebuild phases wait — two
        # overlapping disk-read waves on one external drive multiply the
        # I/O bottleneck.
        self._durations_busy = False
        # True when heavy files were detected on a spinning disk — heavy
        # workers turn conservative in _run; visible in the summary/log.
        self._heavy_rotational = False
        # Real poster/list phase thread counts — filled in _run from hwinfo
        # and disk type (default = class values).
        self._poster_workers = self.POSTER_WORKERS
        self._list_workers = self.LIST_WORKERS

    def set_durations_busy(self, flag: bool):
        """Called from main.py: duration computation is in progress / done."""
        self._durations_busy = bool(flag)

    def _wait_durations(self, gen: int, ev: threading.Event):
        """Wait for the "computing durations" wave to finish (the heavy-phase decision point)."""
        while self._alive(gen, ev) and getattr(self, "_durations_busy", False):
            time.sleep(0.40)

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def start(self, items):
        """Start prebuilding for a list of titles (any previous thread is cancelled)."""
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._stop.set()                    # the previous thread stops at its first opportunity
            ev = threading.Event()
            self._stop = ev
            t = threading.Thread(
                target=self._run, args=(gen, list(items or []), ev),
                daemon=True, name="media-thumb-prebuild",
)
            self._thread = t
            t.start()

    def stop(self):
        """Soft cancel — the thread exits at the first safe point."""
        with self._lock:
            self._gen += 1
            self._stop.set()

    def wait(self, timeout: float = 8.0) -> bool:
        """Bounded wait until the prebuild thread (and its pools) finished.

        closeEvent used to stop() without waiting and close the
        SQLite connections right away while pool threads could still write
        ("database is closed" errors / lost writes)."""
        with self._lock:
            t = self._thread
        if t is None or not t.is_alive():
            return True
        t.join(timeout=max(0.0, float(timeout)))
        return not t.is_alive()

    def _alive(self, gen: int, ev: threading.Event) -> bool:
        return gen == self._gen and not ev.is_set()

    def _progress(self, msg: str):
        cb = self.on_progress
        if cb is None:
            return
        try:
            cb(msg)
        except Exception as _exc_dbg:
            dbg_log(f"_progress failed: {type(_exc_dbg).__name__}")
            pass

    def _counts(self, phase: str, done: int, total: int):
        cb = self.on_counts
        if cb is None:
            return
        try:
            cb(phase, done, total)
        except Exception as _exc_dbg:
            dbg_log(f"_counts failed: {type(_exc_dbg).__name__}")
            pass

    # ------------------------------------------------------------ main execution
    def _run(self, gen: int, items: list, ev: threading.Event):
        try:
            from watchtime import _dbg
        except Exception as _exc_dbg:  # pragma: no cover
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            def _dbg(_m):
                pass
        ft = FileThumbs(self.cache.cache_dir)
        try:
            ft.archive_enabled = bool(self.store.archive_thumbs)
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        # Video-path counters are reset "for this run" so the final summary
        # reports the real numbers of this prebuild
        try:
            import winthumbs as _wst
            _wst.reset_thumbnail_stats()
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        FileThumbs.ffmpeg_video_uses = 0
        FileThumbs._archive_soft_fails = 0     # fresh window for soft-fail reporting
        FileThumbs._archive_soft_files = set()  # this run's soft-fail paths
        FileThumbs._probe_counts = {}          # fresh window for reprobe reporting
        with FileThumbs._probe_inflight_lock:  # clear orphaned probe events
            FileThumbs._probe_inflight = {}
        st = self.store
        # Rotational-disk detection happens *at the very start*
        # of the run (sample: the first existing path of each title —
        # video/archive/icon/the folder itself) so the poster and list pools
        # also become disk-aware, not just the heavy phases; a finer check
        # later re-measures from the heavy-file sample and makes the
        # heavy phases conservative too if needed.
        self._poster_workers = self.POSTER_WORKERS
        self._list_workers = self.LIST_WORKERS
        try:
            _step0 = max(1, len(items) // 120 + 1)
            _smp0 = []
            for _it0 in items[::_step0][:120]:
                for _p0 in (getattr(_it0, "preview_video", None),
                            getattr(_it0, "preview_archive", None),
                            getattr(_it0, "icon_path", None),
                            getattr(_it0, "path", None)):
                    if _p0:
                        _smp0.append(str(_p0))
                        break
            _rot0 = hwinfo.any_rotational(_smp0) if _smp0 else False
            if _rot0:
                self._heavy_rotational = True
                self._poster_workers = hwinfo.poster_workers(True)
                self._list_workers = hwinfo.list_workers(True)
            elif items:
                self._poster_workers = hwinfo.poster_workers(False)
                self._list_workers = hwinfo.list_workers(False)
        except Exception as _exc_dbg:
            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
            pass
        state = {"n": 0, "t": 0.0}
        count_lock = threading.Lock()
        # Seconds are counted per group (image/doc/…) so the summary and the
        # debug log say exactly where the time went. A dict value means "worker-thread
        # seconds"; dividing by the thread count ≈ that group's real share of
        # wall-clock time.
        gsecs: dict = {}

        def _tick(kind: str, total: int, text_tmpl: str):
            with count_lock:
                state["n"] += 1
                n = state["n"]
            now = time.monotonic()
            if n % 5 == 0 or now - state["t"] > 1.2:
                state["t"] = now
                self._counts(kind, n, total)
                self._progress(text_tmpl.format(fa(n), fa(total)))
            if n % 12 == 0:
                time.sleep(0.004)              # gentle pacing — resources freed for the UI

        n_posters = n_files = 0
        t0_run = time.monotonic()          # total time feeds the final summary
        _t_lap = t0_run                    # each phase timed separately → split in the summary
        sec_p = sec_l = sec_a = sec_v = 0.0

        def _lap_s() -> float:
            nonlocal _t_lap
            now = time.monotonic()
            s = now - _t_lap
            _t_lap = now
            return s
        try:
            # ── Phase 0: content fingerprint — smart skipping of unchanged titles ──
            # Contract: "when the backup file is complete and nothing
            # changed, it must not spend minutes checking again". For each
            # title, the fingerprint of all files (relative name+size+mtime) is
            # compared with the manifest stored in the database; equal = fully
            # skipped (no poster, no files, no archive retry). The file list is
            # also kept from this same walk so the old "listing" phase (a
            # second get_file_tree) never runs at all.
            mans: dict = {}            # i → current manifest
            listed: dict = {}          # i → flat file list (from the phase-0 walk)
            retry_old: dict = {}       # i → previous retry budget (soft fail)
            to_do: list = []           # indices of titles that need re-inspection
            n_skip = 0
            era = _MANIFEST_ERA
            # The cache pixel size joins the era — after a display-scale change
            # (100%→150%) the effective cache size (256/384/512) changes;
            # without this, the prebuilder would skip unchanged titles against
            # an old-generation manifest and the new generation's thumbnails
            # would never be prebuilt.
            try:
                _px = int(getattr(self.cache, "size", (256, 256))[0])
            except Exception:
                _px = 256
            era = f"{era}-{_px}"
            total0 = len(items)
            state["n"] = 0; state["t"] = 0.0
            self._counts("check", 0, total0)
            self._progress(T("🔎 بررسی هوشمندِ تغییرها (۰/{0})…").format(fa(total0)))

            def _probe0(arg):
                i, it = arg
                try:
                    man, files = thumb_manifest(
                        Path(it.path), loose=getattr(it, "loose_group", False))
                except Exception as _exc_dbg:
                    dbg_log(f"_run._probe0 failed: {type(_exc_dbg).__name__}")
                    man, files = None, []   # cautious: it gets processed, but no manifest
                return i, man, files

            with ThreadPoolExecutor(self._list_workers) as pool0:
                for i, man, files in pool0.map(_probe0, enumerate(items)):
                    if not self._alive(gen, ev):
                        return
                    mans[i] = man
                    listed[i] = files or []
                    old_r = 0
                    rec = None
                    try:
                        rec = st.thumb_state_of(items[i])
                    except Exception as _exc_dbg:
                        dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                        rec = None
                    if isinstance(rec, dict):
                        try:
                            old_r = int(rec.get("r") or 0)
                        except Exception as _exc_dbg:
                            dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                            old_r = 0
                    if rec is not None and rec.get("e") == era \
                            and rec.get("m") == man and old_r <= 0 \
                            and cover_ready(items[i], st, self.cache.get,
                                            ft=ft,
                                            px=getattr(self.cache, "size",
                                                       (256, 256))[0]):
                        n_skip += 1                     # unchanged → fully skipped
                    else:
                        # A coverless title (its poster was never built) is
                        # never skipped as "unchanged" — re-inspect/rebuild
                        to_do.append(i)                 # new / changed / retry
                        retry_old[i] = old_r
                    _tick("check", total0, T("🔎 بررسی هوشمندِ تغییرها… {0}/{1}"))
            if not self._alive(gen, ev):
                return
            self._counts("check", total0, total0)
            sec0 = _lap_s()                             # fingerprint phase share
            if n_skip:
                _msg0 = (T("⏭ {0} عنوان بدون تغییر — تامبنیل‌هایشان سالم است و رد شدند").format(fa(n_skip)))
                if to_do:
                    _msg0 += T(" · 🔎 {0} عنوان بازرسی می‌شود").format(fa(len(to_do)))
                self._progress(_msg0)
            _dbg(f"prebuild manifest: {n_skip} unchanged skipped, {len(to_do)} "
                 f"to check (retry>0: {sum(1 for r in retry_old.values() if r > 0)}) "
                 f"in {sec0:.1f}s")

            # Each title's selected files to build (same old rules: without the
            # title icon/poster, loose-doc filter, direct files in loose mode)
            sel_files: dict = {}
            for i in to_do:
                it = items[i]
                files_i = listed.get(i) or []
                if getattr(it, "loose_docs_only", False):
                    files_i = [f for f in files_i
                               if f.suffix.lower() in LOOSE_DOC_EXTS]
                _ex = {str(p) for p in (getattr(it, "icon_path", None),
                                        st.poster_of(it, static=True)) if p}   # static frame
                if _ex:
                    files_i = [f for f in files_i if str(f) not in _ex]
                sel_files[i] = files_i

            # ── Phase 1: title posters (light parallelism) — only changed/new titles ──
            total_p = len(to_do)
            state["n"] = 0; state["t"] = 0.0
            self._counts("posters", 0, total_p)
            if to_do:
                self._progress(T("⚡ پیش‌ساخت تامبنیل شروع شد — پوستر پوشه‌ها (۰/{0})").format(fa(total_p)))

            def _poster(it):
                if not self._alive(gen, ev):
                    return
                try:
                    # The cache's effective pixel size (display-scale-aware
                    # size) — the prebuilder builds exactly the size that the
                    # display will read later
                    poster_png_fallback(
                        self.cache.get, ft,
                        st.poster_of(it, static=True),   # static frame
                        getattr(it, "preview_video", None),
                        getattr(it, "preview_pdf", None),
                        getattr(it, "preview_archive", None),
                        getattr(self.cache, "size", (256, 256))[0])
                except Exception as e:
                    # a single-file crash never writes a path — only the exception type
                    _dbg(T("⚠ خطا در پیش‌ساخت پوستر: {0}").format(type(e).__name__))
                _tick("posters", total_p,
                      T("⚡ پیش‌ساخت پوستر پوشه‌ها… {0}/{1}"))

            if to_do:
                with ThreadPoolExecutor(self._poster_workers) as pool:
                    list(pool.map(_poster, (items[i] for i in to_do)))
            if not self._alive(gen, ev):
                return
            n_posters = state["n"]
            self._counts("posters", n_posters, total_p)
            sec_p = _lap_s()                         # poster phase share
            _dbg(f"prebuild: posters phase done ({n_posters}) in {sec_p:.1f}s")

            # A separate "listing phase" is no longer needed — the file list
            # came from the phase-0 walk (fingerprint) and only needs splitting
            # into the queues.
            plan_light: list = []          # images/docs/misc — fast
            plan_archive: list = []        # archives — 7z process (own phase)
            plan_video: list = []          # videos — Shell/winthumb → ffmpeg
            arc_item_of: dict = {}         # archive path → title index (soft fail)
            for i in to_do:
                for f in (sel_files.get(i) or []):
                    # light/heavy queue; heavies split into two
                    g = group_of(f)
                    if g == "archive":
                        plan_archive.append(f)
                        arc_item_of[str(f)] = i
                    elif g == "video":
                        plan_video.append(f)
                    else:
                        plan_light.append(f)
            del listed
            total_l = len(plan_light)
            total_a = len(plan_archive)
            total_v = len(plan_video)

            # Disk-aware rule: if heavy files sit on a spinning disk
            # (HDD), dozens of parallel readers thrash the disk head and
            # everything slows down → heavy workers automatically turn
            # conservative. Detection comes from this very machine
            # (IOCTL/sysfs — nothing is hardcoded; on SSD/NVMe or unknown the
            # previous fast caps stay).
            try:
                _smp = (plan_archive + plan_video)[:: max(1, len(plan_archive + plan_video) // 120 + 1)][:120]
                _rot = hwinfo.any_rotational(_smp)
                self._heavy_rotational = bool(_rot)
                if _rot:
                    self.ARCHIVE_WORKERS = hwinfo.archive_workers(True)
                    self.VIDEO_WORKERS = hwinfo.video_workers(True)
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                self._heavy_rotational = False

            # ── Phase 2b: parallel thumbnail build of "light" files ───────────────────
            state["n"] = 0; state["t"] = 0.0
            self._counts("files", 0, total_l)
            self._progress(T("⚡ پیش‌ساخت تامبنیل فایل‌ها (۰/{0})…").format(fa(total_l)))

            def _file(f, kind="files", total=0, tmpl="", on_none=None):
                if not self._alive(gen, ev):
                    return
                # The part of the library currently on screen has priority;
                # the app must stay usable — if the UI is
                # requesting thumbnails right now (browsing the details grid or
                # cards), the prebuild phases pause so disk/CPU stays free for
                # the page in front of the user. Work resumes ~2.5 seconds
                # after the UI goes quiet — "minutes of waiting" no longer
                # makes sense for the user.
                while self._alive(gen, ev) and FileThumbs.ui_active():
                    time.sleep(0.30)
                _ta = time.monotonic()     # this file's share of its group's time
                try:
                    _got = ft.get(f, 132)  # details grid size — same shared cache
                    # An archive failure is attributed to its owning title
                    # (capped retry). On failure ft.get returns the "file-type
                    # card", not None — so filethumbs' soft-fail path registry
                    # is the source of truth.
                    if on_none is not None and (
                            _got is None
                            or str(f) in FileThumbs._archive_soft_files):
                        try:
                            on_none(f)
                        except Exception as _exc_dbg:
                            dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                            pass
                except Exception as _exc_dbg:
                    dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                    pass
                try:
                    dt = time.monotonic() - _ta
                    with count_lock:
                        gsecs[group_of(f)] = gsecs.get(group_of(f), 0.0) + dt
                except Exception as _exc_dbg:
                    dbg_log(f"_run._file failed: {type(_exc_dbg).__name__}")
                    pass
                _tick(kind, total, tmpl)

            if total_l:
                with ThreadPoolExecutor(self.FILE_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "files", total_l,
                        T("⚡ پیش‌ساخت تامبنیل فایل‌ها… {0}/{1}")), plan_light,
                        chunksize=8)
            if not self._alive(gen, ev):
                return
            n_files = state["n"]
            sec_l = _lap_s()                         # listing + light files

            # ── Phase 2c: archives — list/extract subprocesses (own phase) ──
            # Measured on a large library — heavies are by default
            # not prebuilt at all — on a large video library, eagerly building
            # every video/archive beats Explorer's capacity no matter the
            # per-file optimizations; like Explorer: placeholder/cache now, and
            # a real thumbnail once the file is actually seen (visible-first on
            # the details page). The explicit "prebuild heavies too" option in
            # Settings restores the old behavior.
            # Rationale: the whole point
            # of prebuild is that every file's thumbnail is already built so
            # the app stays fast — so with prebuild enabled, the archive/video
            # phases always run (in the current model: in the background, pausing
            # while the UI is being browsed).
            # Titles whose archives soft-failed this run (capped retry)
            soft_items: set = set()

            def _arc_none(f):
                try:
                    i0 = arc_item_of.get(str(f))
                    if i0 is not None:
                        with count_lock:
                            soft_items.add(i0)
                except Exception as _exc_dbg:
                    dbg_log(f"_run._arc_none failed: {type(_exc_dbg).__name__}")
                    pass

            heavy_on = True
            if total_a and heavy_on:
                self._wait_durations(gen, ev)     # durations first, then heavies
                state["n"] = 0; state["t"] = 0.0
                self._counts("archives", 0, total_a)
                self._progress(
                    T("📦 پیش‌ساخت تامبنیل آرشیوها (۰/{0})…").format(fa(total_a)))
                with ThreadPoolExecutor(self.ARCHIVE_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "archives", total_a,
                        T("📦 پیش‌ساخت تامبنیل آرشیوها… {0}/{1}"),
                        on_none=_arc_none), plan_archive,
                        chunksize=2)
                if not self._alive(gen, ev):
                    return
                n_files += state["n"]
                sec_a = _lap_s()                     # archive phase share

            # ── Phase 2d: videos — Windows fast path first / lazy fallback ──
            if total_v and heavy_on:
                self._wait_durations(gen, ev)
                state["n"] = 0; state["t"] = 0.0
                self._counts("videos", 0, total_v)
                self._progress(
                    T("🎬 پیش‌ساخت تامبنیل ویدیوها (۰/{0})…").format(fa(total_v)))
                with ThreadPoolExecutor(self.VIDEO_WORKERS) as pool:
                    pool.map(lambda f: _file(
                        f, "videos", total_v,
                        T("🎬 پیش‌ساخت تامبنیل ویدیوها… {0}/{1}")), plan_video,
                        chunksize=4)
                if not self._alive(gen, ev):
                    return
                n_files += state["n"]
                sec_v = _lap_s()                     # video phase share
            if (total_a or total_v) and not heavy_on:
                self._progress(
                    T("🏎 {0} فایل سنگین (ویدیو/فشرده) — موقعِ دیده‌شدن ساخته می‌شوند (پیش‌ساخت تنبل)").format(fa(total_a + total_v)))

            total_f = total_l + total_a + total_v
            self._counts("done", n_files, total_f)
            # The exact total time in the final summary — the
            # user learns how long it took and sees the exact number while testing.
            el_run = time.monotonic() - t0_run
            _dbg(f"prebuild done: {n_posters} posters + {n_files} file thumbs "
                 f"({n_skip} unchanged skipped) "
                 f"in {el_run:.1f}s (heavy={'on' if heavy_on else 'lazy'}, "
                 f"workers: poster={self._poster_workers} list={self._list_workers} "
                 f"video={self.VIDEO_WORKERS} archive={self.ARCHIVE_WORKERS}"
                 f"{' · دیسک چرخان→محافظه‌کار' if self._heavy_rotational else ''}"
                 f" · عملکرد={hwinfo.perf_mode()}, "                    # mode transparency
                 f"hw: {hwinfo.hw_summary()})")
            # Splitting the video paths in the summary — the user sees exactly
            # how much "Explorer speed" helped (cache) and that ffmpeg only ran
            # for a few files. In lazy mode the summary says explicitly that
            # heavies are built when seen (so zero cache/render numbers raise
            # no questions).
            vinfo = ""
            if total_v and heavy_on:
                try:
                    import winthumbs as _wst
                    ss = _wst.shell_thumbnail_stats()
                    vinfo = (T(" · ویدیوها: کش‌ویندوز {0} · رندر {1} · ffmpeg {2}").format(fa(ss['cache_hit']), fa(ss['render_hit']), fa(FileThumbs.ffmpeg_video_uses)))
                except Exception as _exc_dbg:
                    dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                    vinfo = ""
            elif (total_a or total_v) and not heavy_on:
                vinfo = (T(" · 🏎 {0} موردِ سنگین (موقعِ دیدنشان ساخته می‌شوند)").format(fa(total_a + total_v)))
            # The "ffmpeg 31320 times" anomaly — if a file was probed more
            # than once in this run, the repeats show up here (normal = none),
            # so the root of cache rework is visible in the user's next report.
            try:
                _rt = FileThumbs.reprobe_total()
                if _rt > 0:
                    vinfo += f" · ⚠ reprobe {fa(_rt)}"
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
            # The "one archive got no thumbnail" case — archive soft-fails
            # are surfaced explicitly; these get no negative cache entry.
            # The retry is capped (at most the next 2 runs, managed via
            # the manifest) — not forever on every run.
            try:
                if FileThumbs._archive_soft_fails > 0:
                    vinfo += (T(" · ⚠ {0} آرشیو تلاش‌ناموفق (حداکثر {1} اجرای بعدی بازتلاش)").format(fa(FileThumbs._archive_soft_fails), fa(_RETRY_BUDGET)))
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
            # Save the thumbnail manifest — only when nothing was cancelled.
            # A "half-done" manifest is dangerous (wrong skipping on the next
            # run), so it is either saved complete or the previous manifest
            # stays. Archive soft-fail → retry budget: fresh failures get 2
            # tries and consumed budgets decay to zero, so the futile
            # every-boot retry loop ends
            # for good; on success the budget resets to zero.
            try:
                _states = {}
                for i in to_do:
                    _man = mans.get(i)
                    if not _man:
                        continue
                    _or = int(retry_old.get(i, 0) or 0)
                    if i in soft_items:
                        _nr = max(0, _or - 1) if _or > 0 else _RETRY_BUDGET
                    else:
                        _nr = 0
                    _states[str(items[i].path)] = {"m": _man, "r": _nr, "e": era}
                if _states:
                    st.set_thumb_states(_states)
                    _dbg(f"prebuild manifest: saved {len(_states)} thumb manifests "
                         f"(soft-retry: {sum(1 for v in _states.values() if v['r'] > 0)})")
            except Exception as e:
                _dbg(f"thumb manifest save failed: {type(e).__name__}")
            # The final summary splits the time phase by phase and shows
            # exactly how much each part took; the light groups'
            # (image/doc/…) share is also
            # logged in detail in the debug log.
            segs = T("پوستر {0} · سبک {1}").format(fa_mmss(sec_p), fa_mmss(sec_l))
            if sec_a > 0:
                segs += f" · 📦 {fa_mmss(sec_a)}"
            if sec_v > 0:
                segs += f" · 🎬 {fa_mmss(sec_v)}"
            # The summary states explicitly how many titles were unchanged
            # and skipped.
            _skip_txt = (T("⏭ {0} عنوان بدون‌تغییر · ").format(fa(n_skip)) if n_skip else "")
            self._progress(
                T("✅ پیش‌ساخت تامبنیل کامل شد — {0}{1} پوستر · {2} تامبنیل فایل · ⏱ {3} ({4}){5}").format(_skip_txt, fa(n_posters), fa(n_files), fa_mmss(el_run), segs, vinfo))
            try:
                if gsecs:
                    _top = sorted(gsecs.items(), key=lambda kv: -kv[1])[:5]
                    _dbg("prebuild group secs: " + ", ".join(
                        f"{k}={v:.1f}s" for k, v in _top))
            except Exception as _exc_dbg:
                dbg_log(f"_run failed: {type(_exc_dbg).__name__}")
                pass
        except Exception as e:
            _dbg(f"prebuild aborted: {e}")
