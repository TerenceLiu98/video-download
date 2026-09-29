"""BiliFlow Textual TUI application — the root App.

Owns the shared ``AppState`` (settings/api_client/queue/login), the navigation
sidebar, the persistent download queue, and all background-worker launchers.
Every blocking core call runs in a thread via ``@work(thread=True)`` and reports
back via Textual Messages (see ``tui/messages.py``).

Concurrency mirrors the Qt GUI's ``QThreadPool.maxThreadCount``: a
``threading.Semaphore`` sized to ``settings.max_concurrent_downloads`` caps
in-flight downloads. Each task gets its own ``DownloadService`` (mutable
per-download state) but shares the httpx client + module ``_SIDECAR_LOCK``.
"""

from __future__ import annotations

import logging
import threading

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.tui import messages
from bilibili_downloader.tui.screens.batch_screen import BatchScreen
from bilibili_downloader.tui.screens.confirm_screen import ConfirmScreen
from bilibili_downloader.tui.screens.creator_screen import CreatorScreen
from bilibili_downloader.tui.screens.help_screen import HelpScreen
from bilibili_downloader.tui.screens.login_screen import LoginScreen
from bilibili_downloader.tui.screens.main_screen import MainScreen
from bilibili_downloader.tui.state import AppState, BatchJob, LoginState
from bilibili_downloader.tui.widgets.download_queue import (
    DownloadQueue,
    DownloadQueueModel,
)
from bilibili_downloader.tui.widgets.sidebar import NavSidebar
from bilibili_downloader.tui.widgets.task_workspace import TaskWorkspace
from bilibili_downloader.tui.workers.batch import BatchWorker
from bilibili_downloader.tui.workers.creator import CreatorIndexWorker
from bilibili_downloader.tui.workers.download import DownloadWorker
from bilibili_downloader.tui.workers.ffmpeg import FFmpegCheckWorker
from bilibili_downloader.tui.workers.login import (
    LoginStatusWorker,
    QrGenerateWorker,
    QrPollWorker,
    SessdataValidateWorker,
)
from bilibili_downloader.tui.workers.resolve import ResolveWorker
from bilibili_downloader.utils.config import ConfigManager
from bilibili_downloader.utils.validators import is_bilibili_url

logger = logging.getLogger(__name__)


class BiliFlowTUI(App):
    CSS_PATH = "design.tcss"
    TITLE = "BiliFlow"
    SUB_TITLE = "Bilibili 下载器"

    BINDINGS = [
        ("ctrl+l", "login", "登录"),
        ("ctrl+b", "batch", "批量"),
        ("ctrl+u", "creator", "UP主"),
        ("ctrl+comma", "settings", "设置"),
        ("ctrl+t", "toggle_theme", "主题"),
        ("f1", "help", "帮助"),
        ("ctrl+q", "quit", "退出"),
    ]

    def action_login(self) -> None:
        self._open_login()

    def action_batch(self) -> None:
        self._open_batch()

    def action_creator(self) -> None:
        self._open_creator()

    def action_settings(self) -> None:
        self._open_settings()

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if self.theme != "textual-light" else "textual-dark"
        self.notify(f"主题：{'浅色' if self.theme == 'textual-light' else '深色'}")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # --- lifecycle ---
    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace-shell"):
            yield NavSidebar()
            with Vertical(id="workspace-main"):
                yield MainScreen()
                yield TaskWorkspace(self._model)
                yield Button("下载任务 0", id="background-status")

    def on_mount(self) -> None:
        config = ConfigManager()
        settings = config.load()
        client = BilibiliAPIClient(sessdata=settings.sessdata or None)
        self.state = AppState(config=config, settings=settings, api_client=client, queue=self._model)
        self._semaphore = threading.Semaphore(max(1, settings.max_concurrent_downloads))
        self._task_path = config.task_path
        try:
            self._model.restore(self._task_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._task_path = None
            self.notify(f"任务记录读取失败，保留原文件：{exc}", severity="error")
        self._refresh_queue()
        self._show_workspace("home")
        self.set_interval(2, self._save_queue)

        # Sidebar control panel defaults from settings.
        self._control_panel().sync_defaults_from_settings(settings)

        if settings.sessdata:
            self._refresh_login_status()

    def __init__(self):
        super().__init__()
        self.state: AppState | None = None
        self._semaphore: threading.Semaphore | None = None
        self._model = DownloadQueueModel()
        self._creator_worker = None
        self._task_path = None
        self._batch_jobs: dict[int, BatchJob] = {}
        self._quitting = False

    # --- helpers to reach widgets ---
    def _sidebar(self) -> NavSidebar:
        return self.query_one(NavSidebar)

    def _control_panel(self):
        return self.query_one(MainScreen).query_one("ControlPanel")

    def _video_info(self):
        return self.query_one(MainScreen).query_one("VideoInfoPanel")

    def _url_input(self):
        return self.query_one(MainScreen).query_one("#url-input")

    def _resolve_btn(self):
        return self.query_one(MainScreen).query_one("#resolve-btn")

    # --- navigation actions ---
    def _open_login(self) -> None:
        has_session = bool(self.state and self.state.settings.sessdata)
        self.push_screen(LoginScreen(has_session))

    @on(Button.Pressed)
    def _on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "login-btn":
            self._open_login()
        elif btn_id == "nav-home":
            self._show_workspace("home")
        elif btn_id in ("nav-batch", "background-status"):
            self._show_workspace("tasks")
        elif btn_id == "batch-btn":
            self._open_batch()
        elif btn_id in ("nav-creator", "creator-btn"):
            self._open_creator()
        elif btn_id == "nav-settings":
            self._open_settings()
        elif btn_id == "resolve-btn":
            self._start_resolve()
        elif btn_id == "download-btn":
            self._start_download()

    # --- batch ---
    def _show_workspace(self, page: str) -> None:
        tasks = page == "tasks"
        self.query_one(MainScreen).display = not tasks
        self.query_one(TaskWorkspace).display = tasks
        self.query_one("#background-status").display = not tasks
        self.query_one("#nav-home").set_class(not tasks, "--active")
        self.query_one("#nav-batch").set_class(tasks, "--active")
        if tasks:
            self.query_one(DownloadQueue).focus()
        else:
            self._url_input().focus()

    def _open_batch(self) -> None:
        self.push_screen(BatchScreen(), self._handle_batch_result)

    def _handle_batch_result(self, urls) -> None:
        # NOTE: do NOT name this _on_batch_done — Textual would treat it as the
        # auto-handler for the BatchDone message and call it with the message
        # object instead of the screen's dismiss result.
        if urls:
            self._start_batch(urls)

    def _start_batch(self, urls, creator_index=None) -> None:
        assert self.state is not None
        cp = self._control_panel()
        quality = cp.quality_value
        codec = cp.codec_value
        flags = cp.checkbox_values()
        flags["embed_metadata"] = self.state.settings.embed_metadata
        flags["embed_cover"] = self.state.settings.embed_cover
        n = len(urls)
        batch_id = max(self._batch_jobs, default=0) + 1
        job = BatchJob(batch_id, n, self.state.settings.output_dir)
        self._batch_jobs[batch_id] = job
        titles = {v.bvid: v.title for v in creator_index.videos} if creator_index else {}
        options = dict(flags=flags, quality=quality, codec=codec,
                       creator_mid=creator_index.mid if creator_index else 0,
                       creator_name=creator_index.name if creator_index else "")
        job.row_ids = [self._model.add_source(url, titles.get(url, url), job.output_dir, options)
                       for url in urls]
        self._refresh_queue()
        self._show_workspace("tasks")
        self._refresh_task_summary()
        self.notify(f"已提交 {n} 个链接，正在后台解析")
        self.start_batch(urls, flags, quality, codec,
                         creator_mid=(creator_index.mid if creator_index else 0),
                         creator_name=(creator_index.name if creator_index else ""),
                         creator_index=creator_index, batch_id=batch_id)

    @work(thread=True, group="batch")
    def start_batch(self, urls, flags, quality, codec, creator_mid=0, creator_name="", creator_index=None, batch_id=0) -> None:
        assert self.state is not None
        from bilibili_downloader.core.models import VideoQuality
        from bilibili_downloader.tui.resolve_cache import ResolveCache

        q = VideoQuality(quality) if quality and not isinstance(quality, VideoQuality) else quality
        job = self._batch_jobs.get(batch_id)
        output_dir = job.output_dir if job else self.state.settings.output_dir
        try:
            cache = ResolveCache.for_creator(output_dir, creator_index) if creator_index else None
            worker = BatchWorker(
                self, self.state.api_client, urls, flags,
                quality=q, codec=codec, creator_mid=creator_mid, creator_name=creator_name,
                cache=cache, batch_id=batch_id,
            )
            if job:
                job.worker = worker
            if self._quitting:
                worker.cancel()
            worker.run()
        except Exception as exc:  # noqa: BLE001
            message = messages.BatchDone(str(exc))
            message.batch_id = batch_id
            self.post_message(message)

    @on(messages.BatchItemReady)
    def _on_batch_item_ready(self, message: messages.BatchItemReady) -> None:
        if self._quitting:
            return
        job = self._batch_jobs.get(message.batch_id)
        if job and 0 <= message.source_index < len(job.row_ids):
            did = job.row_ids[message.source_index]
            if self._model.get(did) is None:
                return
            parts = job.part_rows.setdefault(message.source_index, [])
            if message.part_index < len(parts):
                return
            after = next((p for p in reversed(parts) if self._model.get(p)), did) if parts else None
            did = self._model.resolve_source(did, message.item, after=after)
            parts.append(did)
            self._launch_download(did, message.item)
            self._refresh_queue()
        else:
            self.enqueue_download(message.item, job.output_dir if job else None)

    def _batch_source_row(self, message):
        job = self._batch_jobs.get(message.batch_id)
        if job and 0 <= message.source_index < len(job.row_ids):
            return self._model.get(job.row_ids[message.source_index])
        return None

    @on(messages.BatchItemResolving)
    def _on_batch_item_resolving(self, message: messages.BatchItemResolving) -> None:
        row = self._batch_source_row(message)
        if row:
            row.state, row.status = "resolving", "解析中"
            self._refresh_queue()

    @on(messages.BatchItemFailed)
    def _on_batch_item_failed(self, message: messages.BatchItemFailed) -> None:
        assert self.state is not None
        row = self._batch_source_row(message)
        if row:
            row.state, row.status, row.error = "resolve_failed", "解析失败", message.error
        elif message.batch_id not in self._batch_jobs:
            self.state.queue.add_error(message.error)
        job = self._batch_jobs.get(message.batch_id)
        if job:
            job.failed += 1
        self._refresh_queue()

    @on(messages.BatchProgress)
    def _on_batch_progress(self, message: messages.BatchProgress) -> None:
        job = self._batch_jobs.get(message.batch_id)
        if job:
            job.done = message.done
            job.status = "解析中"
        self._refresh_task_summary()

    @on(messages.BatchItemRetrying)
    def _on_batch_item_retrying(self, message: messages.BatchItemRetrying) -> None:
        """User-visible backoff feedback for 风控 retries."""
        job = self._batch_jobs.get(message.batch_id)
        if job:
            job.status = f"风控重试 {message.attempt} · 等待 {message.delay:g}s"
        row = self._batch_source_row(message)
        if row:
            row.status = f"解析重试 · 等待 {message.delay:g}s"
        self._refresh_queue()

    @on(messages.BatchDone)
    def _on_batch_done_msg(self, message: messages.BatchDone) -> None:
        job = self._batch_jobs.get(message.batch_id)
        if job:
            job.running = False
            job.worker = None
            job.status = f"中断：{message.error}" if message.error else "解析完成"
            for did in job.row_ids:
                row = self._model.get(did)
                if row and row.state in ("awaiting", "resolving"):
                    row.state, row.status = "resolve_failed", "解析中断，可重试"
                    row.error = message.error or "解析未完成"
        self._refresh_queue()
        if message.error:
            self.notify(f"批量导入中断：{message.error}", severity="error")
        elif job:
            self.notify(f"批量解析完成 {job.done}/{job.total} · 失败 {job.failed}")

    # --- creator ---
    def _open_creator(self) -> None:
        assert self.state is not None
        self.push_screen(
            CreatorScreen(self.state.api_client, self.state.settings.output_dir),
            self._on_creator_done,
        )

    def _on_creator_done(self, result) -> None:
        if not result:
            return
        bvids, index = result
        self._start_batch(bvids, creator_index=index)

    def start_creator_fetch(self, source: str, output_dir: str) -> None:
        """Launched by the creator screen's 抓取 button."""
        assert self.state is not None
        from pathlib import Path

        from bilibili_downloader.core.creator import (
            load_creator_index,
            parse_creator_mid,
        )

        resume_index = None
        try:
            mid = parse_creator_mid(source)
            candidates = list(Path(output_dir).glob(f"*_{mid}/index.partial.json"))
            if candidates:
                # Sort candidates by mtime descending (most recent first)
                latest = max(candidates, key=lambda p: p.stat().st_mtime)
                loaded = load_creator_index(latest)
                # Only resume if the index is incomplete and has a next_cursor
                if not getattr(loaded, 'complete', False) and getattr(loaded, 'next_cursor', None):
                    resume_index = loaded
                    self.notify(f"从检查点继续（已有 {len(resume_index.videos)} 个投稿）")
        except Exception as exc:  # noqa: BLE001
            logger.debug("creator resume detection failed: %s", exc)

        self.run_creator_fetch(source, output_dir, resume_index)

    @work(thread=True, group="creator")
    def run_creator_fetch(self, source, output_dir, resume_index) -> None:
        assert self.state is not None
        worker = CreatorIndexWorker(
            self, self.state.api_client, source, output_dir, resume_index
        )
        self._creator_worker = worker
        worker.run()
        self._creator_worker = None

    def cancel_creator_fetch(self) -> None:
        """Cancel an in-flight creator index fetch."""
        if hasattr(self, '_creator_worker') and self._creator_worker is not None:
            self._creator_worker.cancel()

    def persist_creator_index(self, index) -> None:
        """Write the final index.json and drop the partial checkpoint."""
        from pathlib import Path

        from bilibili_downloader.core.creator import (
            creator_directory_name,
            save_creator_index,
        )

        assert self.state is not None
        out = Path(self.state.settings.output_dir)
        path = out / creator_directory_name(index.name, index.mid) / "index.json"
        save_creator_index(index, path)
        (path.parent / "index.partial.json").unlink(missing_ok=True)

    # Creator worker messages are posted to the App pump; route them to the
    # active CreatorScreen so the UI updates (the screen owns the selection UI).
    @on(messages.CreatorProgress)
    def _on_creator_progress(self, message: messages.CreatorProgress) -> None:
        self._creator_screen_set(lambda s: s.on_creator_progress(message))

    @on(messages.CreatorStatus)
    def _on_creator_status(self, message: messages.CreatorStatus) -> None:
        self._creator_screen_set(lambda s: s.on_creator_status(message))

    @on(messages.CreatorFinished)
    def _on_creator_finished(self, message: messages.CreatorFinished) -> None:
        self._creator_screen_set(lambda s: s.on_creator_finished(message))

    @on(messages.CreatorFailed)
    def _on_creator_failed(self, message: messages.CreatorFailed) -> None:
        self._creator_screen_set(lambda s: s.on_creator_failed(message))

    def _creator_screen_set(self, apply) -> None:
        from bilibili_downloader.tui.screens.creator_screen import CreatorScreen

        if isinstance(self.screen, CreatorScreen):
            apply(self.screen)

    # --- settings ---
    def _open_settings(self) -> None:
        from bilibili_downloader.tui.screens.settings_screen import SettingsScreen

        assert self.state is not None
        self.push_screen(SettingsScreen(self.state.settings), self._on_settings_done)

    def _on_settings_done(self, new_settings) -> None:
        assert self.state is not None
        if new_settings is None:
            return
        try:
            self.state.config.save(new_settings)
        except OSError as exc:
            self.notify(f"设置保存失败：{exc}", severity="error")
            return
        old_max = self.state.settings.max_concurrent_downloads
        self.state.settings = new_settings
        if new_settings.max_concurrent_downloads != old_max:
            self._semaphore = threading.Semaphore(max(1, new_settings.max_concurrent_downloads))
        self._control_panel().sync_defaults_from_settings(new_settings)
        self.notify("设置已保存")

    # --- ffmpeg ---
    def start_ffmpeg_check(self, custom_path: str | None = None) -> None:
        self.run_ffmpeg_check(custom_path)

    @work(thread=True, group="ffmpeg")
    def run_ffmpeg_check(self, custom_path) -> None:
        FFmpegCheckWorker(self, custom_path).run()

    @on(messages.FFmpegResult)
    def _on_ffmpeg_result(self, message: messages.FFmpegResult) -> None:
        sev = "information" if message.available else "warning"
        self.notify(
            f"FFmpeg {'可用' if message.available else '不可用'}：{message.message}", severity=sev
        )

    # --- URL resolve ---
    def _start_resolve(self) -> None:
        assert self.state is not None
        url = self._url_input().value.strip()
        if not url:
            self.notify("请输入B站视频链接或BV号", severity="warning")
            return
        if not is_bilibili_url(url):
            self.notify("无效的B站视频链接格式", severity="error")
            return
        self._resolve_btn().disabled = True
        self._resolve_btn().label = "解析中…"
        self.start_resolve(url)

    @work(thread=True, exclusive=True, group="resolve")
    def start_resolve(self, source: str) -> None:
        assert self.state is not None
        ResolveWorker(self, self.state.api_client, source).run()

    @on(messages.ResolveFinished)
    def _on_resolve_finished(self, message: messages.ResolveFinished) -> None:
        assert self.state is not None
        info = message.info
        info.video_streams = message.video_streams
        info.audio_streams = message.audio_streams
        self.state.current_video = info
        self.state.current_video_streams = message.video_streams
        self.state.current_playurl_ok = message.playurl_ok

        # Store video_streams in control panel for quality-change codec filtering
        cp = self._control_panel()
        cp.set_video_streams(message.video_streams)

        self._resolve_btn().disabled = False
        self._resolve_btn().label = "解析"
        self._video_info().show_video(info)

        cp.populate_pages(info)
        cp.populate_quality(message.video_streams, self.state.settings.default_quality)
        cp.refresh_codecs(
            message.video_streams,
            cp.quality_value,
            self.state.settings.default_video_codec,
        )
        cp.enable_subtitle()

    @on(messages.ResolveFailed)
    def _on_resolve_failed(self, message: messages.ResolveFailed) -> None:
        self._resolve_btn().disabled = False
        self._resolve_btn().label = "解析"
        self._video_info().show_error(message.error)
        self.notify(f"解析失败：{message.error}", severity="error")

    # --- download ---
    def _start_download(self) -> None:
        from bilibili_downloader.core.models import DownloadItem, VideoQuality

        assert self.state is not None
        if self.state.current_video is None:
            self.notify("请先解析一个视频链接", severity="warning")
            return
        cp = self._control_panel()
        quality = cp.quality_value
        codec = cp.codec_value
        if quality is None:
            self.notify("当前视频没有可用画质", severity="warning")
            return
        flags = cp.checkbox_values()
        page_sel = cp.page_selection
        video = self.state.current_video

        if page_sel == "all":
            infos = [video.for_page(p) for p in video.pages]
        elif hasattr(page_sel, "cid"):
            infos = [video.for_page(page_sel)]
        else:
            infos = [video]

        for vi in infos:
            item = DownloadItem(
                video_info=vi,
                selected_quality=VideoQuality(quality) if not isinstance(quality, VideoQuality) else quality,
                selected_video_codec=codec,
                download_danmaku=flags["danmaku"],
                download_subtitle=flags["subtitle"],
                download_metadata=flags["metadata"],
                download_cover=flags["cover"],
                download_comments=flags["comments"],
                embed_metadata=self.state.settings.embed_metadata,
                embed_cover=self.state.settings.embed_cover,
            )
            self.enqueue_download(item)
        self.notify(f"已加入 {len(infos)} 个下载任务")

    def enqueue_download(self, item, output_dir: str | None = None) -> None:
        assert self.state is not None
        download_id = self.state.queue.add(item, output_dir or self.state.settings.output_dir)
        self._refresh_queue()
        self._launch_download(download_id, item)
        self._refresh_task_summary()

    def _launch_download(self, download_id: int, item) -> None:
        assert self.state is not None and self._semaphore is not None
        worker = DownloadWorker(
            self,
            self.state.api_client,
            item,
            self.state.queue.get(download_id).output_dir or self.state.settings.output_dir,
            download_id,
            self.state.settings.ffmpeg_path or None,
        )
        self.state.queue.register_worker(download_id, worker)
        self.run_download_worker(worker, self._semaphore)

    @work(thread=True, group="download")
    def run_download_worker(self, worker, semaphore) -> None:
        while not semaphore.acquire(timeout=0.1):
            if worker.cancel_checker():
                worker.run()
                return
        try:
            worker.run()
        finally:
            semaphore.release()

    @on(messages.DownloadProgress)
    def _on_download_progress(self, message: messages.DownloadProgress) -> None:
        assert self.state is not None
        self.state.queue.set_progress(message.download_id, message.pct, message.status_text,
                                      message.speed_bps, message.eta_seconds)
        # Per-tick progress repaints only the changed row; structural events
        # (add/done/failed/cancel) still use the full refresh in _refresh_queue.
        # If the incremental update can't locate the row (e.g. it was added this
        # tick and not yet laid out), fall back to a full refresh so the cell is
        # never left stale — but log it so a real regression is diagnosable.
        try:
            self.query_one(DownloadQueue).refresh_row_by_id(message.download_id)
        except Exception:  # noqa: BLE001
            logger.debug("incremental row refresh failed, falling back", exc_info=True)
            self._refresh_queue()
        self._refresh_task_summary()

    @on(messages.DownloadFinished)
    def _on_download_finished(self, message: messages.DownloadFinished) -> None:
        assert self.state is not None
        self.state.queue.mark_done(message.download_id, message.outcome)
        self._refresh_queue()
        warnings = getattr(message.outcome, "warnings", []) or []
        if warnings:
            self.notify(f"下载完成，但有 {len(warnings)} 项警告", severity="warning")
        else:
            self.notify("下载完成", severity="information")

    @on(messages.DownloadFailed)
    def _on_download_failed(self, message: messages.DownloadFailed) -> None:
        assert self.state is not None
        self.state.queue.mark_failed(message.download_id, message.error)
        self._refresh_queue()
        self.notify(f"下载失败：{message.error}", severity="error")

    @on(messages.DownloadCancelled)
    def _on_download_cancelled(self, message: messages.DownloadCancelled) -> None:
        assert self.state is not None
        self.state.queue.mark_cancelled(message.download_id)
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        self._save_queue()
        try:
            self.query_one(DownloadQueue).refresh_model()
            self._refresh_task_summary()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_task_summary(self) -> None:
        from collections import Counter

        rows = self._model.rows()
        counts = Counter(r.state for r in rows)
        speed = sum(r.speed_bps for r in rows if r.state == "downloading")
        download_total = sum(r.item is not None for r in rows)
        summary = (f"解析：待解析 {counts['awaiting']} · 解析中 {counts['resolving']} · 失败 {counts['resolve_failed']}\n"
                   f"下载：完成 {counts['done']}/{download_total} 分P · 部分完成 {counts['partial']} · 下载中 {counts['downloading']} · "
                   f"等待 {counts['pending'] + counts['retry']} · "
                   f"异常 {counts['failed'] + counts['error'] + counts['partial']} · "
                   f"{speed / 1048576:.1f} MiB/s")
        jobs = list(self._batch_jobs.values())
        running = [j for j in jobs if j.running]
        shown = running or jobs[-1:]
        imports = "\n".join(
            f"导入 #{j.batch_id}：{j.total} 条 · 解析 {j.done}/{j.total} · 失败 {j.failed} · {j.status}"
            for j in shown
        )
        self.query_one(TaskWorkspace).update_summary(summary, imports)
        self.query_one("#background-status", Button).label = (
            f"后台导入 {len(running)} · 下载 {counts['downloading']} · "
            f"等待 {counts['pending'] + counts['retry']} · 查看任务"
        )

    def _save_queue(self) -> None:
        if self._task_path is not None:
            try:
                self._model.save(self._task_path)
            except OSError as exc:
                self.notify(f"任务记录保存失败：{exc}", severity="error")
                self._task_path = None

    # --- queue key actions (cancel / retry / delete) ---
    @on(DownloadQueue.QueueAction)
    def _on_queue_action(self, message: DownloadQueue.QueueAction) -> None:
        assert self.state is not None
        action = message.action
        did = message.download_id
        if action == "cancel_all":
            self.state.queue.cancel_all()
            self.notify("已请求暂停所有任务")
        elif action == "resume_all":
            for row in self._model.rows():
                if row.state in ("paused", "cancelled"):
                    self._retry_download(row.download_id)
        elif action == "clear_done":
            for row in self._model.rows():
                if row.state == "done":
                    self._model.delete(row.download_id)
        elif action == "open":
            self._open_task_directory(did)
        elif action == "cancel":
            self.state.queue.cancel(did)
        elif action == "delete":
            if self.state.queue.get_worker(did) is not None:
                self.notify("请先暂停任务再删除", severity="warning")
                return
            self.state.queue.delete(did)
        elif action == "retry":
            self._retry_download(did)
        self._refresh_queue()

    def _open_task_directory(self, download_id: int) -> None:
        import os
        import subprocess
        import sys
        from pathlib import Path

        row = self._model.get(download_id)
        if row is None:
            return
        directory = Path(row.video_path).parent if row.video_path else Path(row.output_dir)
        if not directory.is_dir():
            self.notify("保存目录不存在", severity="warning")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(directory))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(directory)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.notify(f"无法打开目录：{exc}", severity="error")

    def _retry_download(self, download_id: int) -> None:
        assert self.state is not None
        row = self.state.queue.get(download_id)
        if row and row.state == "resolve_failed" and row.source:
            batch_id = max(self._batch_jobs, default=0) + 1
            job = BatchJob(batch_id, 1, row.output_dir, row_ids=[download_id])
            self._batch_jobs[batch_id] = job
            row.state, row.status, row.error = "awaiting", "待解析", None
            self._refresh_queue()
            self.start_batch([row.source], batch_id=batch_id, **row.resolve_options)
            return
        if row is None or row.state not in ("failed", "paused", "cancelled"):
            return
        if self.state.queue.get_worker(download_id) is not None:
            return
        item = self.state.queue.get_item(download_id)
        if item is None:
            self.notify("无法重试该任务", severity="warning")
            return
        self.state.queue.mark_retry(download_id)
        self._refresh_queue()
        self._launch_download(download_id, item)

    # --- quit confirmation (parity with MainWindow.closeEvent) ---
    def action_quit(self) -> None:
        assert self.state is not None
        if self.state.queue.has_active or any(j.running for j in self._batch_jobs.values()):
            self.push_screen(
                ConfirmScreen("仍有导入或下载正在运行。退出将停止未完成的解析，保留已入队任务和下载断点，确认退出吗？"),
                self._on_quit_confirm,
            )
        else:
            self._do_quit()

    def _on_quit_confirm(self, confirmed: bool) -> None:
        if confirmed:
            self._do_quit()

    def _do_quit(self) -> None:
        assert self.state is not None
        self._quitting = True
        for job in self._batch_jobs.values():
            if job.worker is not None:
                job.worker.cancel()
        self.state.queue.cancel_all()
        self._save_queue()
        try:
            self.state.api_client.close()
        except Exception:  # noqa: BLE001
            logger.debug("failed to close api client on quit", exc_info=True)
        self.exit()

    # --- login ---
    @work(thread=True, group="qr-gen")
    def start_qr_generate(self) -> None:
        QrGenerateWorker(self).run()

    @work(thread=True, group="qr-poll")
    def start_qr_poll(self, qrcode_key: str) -> None:
        QrPollWorker(self, qrcode_key).run()

    @work(thread=True, group="sessdata")
    def start_sessdata_validate(self, sessdata: str) -> None:
        SessdataValidateWorker(self, sessdata).run()

    # QR/SESSDATA worker messages are posted to the App pump; route them to the
    # active LoginScreen (Screen-level @on does not receive app-posted messages).
    @on(messages.QrGenerated)
    def _on_qr_generated(self, message: messages.QrGenerated) -> None:
        self._login_screen_call(lambda s: s.on_qr_generated(message))

    @on(messages.QrPollResult)
    def _on_qr_poll_result(self, message: messages.QrPollResult) -> None:
        self._login_screen_call(lambda s: s.on_qr_poll_result(message))

    @on(messages.SessdataValidated)
    def _on_sessdata_validated(self, message: messages.SessdataValidated) -> None:
        self._login_screen_call(lambda s: s.on_sessdata_validated(message))

    def _login_screen_call(self, apply) -> None:
        from bilibili_downloader.tui.screens.login_screen import LoginScreen

        if isinstance(self.screen, LoginScreen):
            apply(self.screen)

    def apply_login(self, sessdata: str) -> None:
        """Persist sessdata, rebuild the API client, refresh login status."""
        assert self.state is not None
        new_settings = self.state.settings.model_copy(update={"sessdata": sessdata})
        self.state.config.save(new_settings)
        self.state.settings = new_settings
        self._replace_api_client()
        self._refresh_login_status()

    def logout(self) -> None:
        assert self.state is not None
        new_settings = self.state.settings.model_copy(
            update={"sessdata": "", "last_login_at": None}, deep=True
        )
        self.state.config.save(new_settings)
        self.state.settings = new_settings
        self._replace_api_client()
        self.state.login = LoginState()
        self._sidebar().set_login_label(self.state.login.label)

    def _replace_api_client(self) -> None:
        assert self.state is not None
        previous = self.state.api_client
        self.state.api_client = BilibiliAPIClient(sessdata=self.state.settings.sessdata or None)
        try:
            previous.close()
        except Exception:  # noqa: BLE001
            logger.debug("failed to close retired api client", exc_info=True)

    def _refresh_login_status(self) -> None:
        assert self.state is not None
        self.state.login_request_id += 1
        request_id = self.state.login_request_id
        self.state.login.checking = True
        self._sidebar().set_login_label(self.state.login.label)
        self._run_login_status(request_id)

    @work(thread=True, group="login-status")
    def _run_login_status(self, request_id: int) -> None:
        assert self.state is not None
        LoginStatusWorker(self, self.state.api_client, request_id).run()

    @on(messages.LoginStatusResult)
    def _on_login_status(self, message: messages.LoginStatusResult) -> None:
        assert self.state is not None
        if message.request_id != self.state.login_request_id:
            return
        nav = message.nav_info
        self.state.login.checking = False
        self.state.login.unknown = False
        if nav.get("isLogin"):
            self.state.login.is_login = True
            self.state.login.uname = str(nav.get("uname", ""))
            self.state.login.mid = str(nav.get("mid", ""))
        else:
            self.state.login = LoginState()
        self._sidebar().set_login_label(self.state.login.label)

    @on(messages.LoginStatusError)
    def _on_login_status_error(self, message: messages.LoginStatusError) -> None:
        assert self.state is not None
        if message.request_id != self.state.login_request_id:
            return
        self.state.login.checking = False
        self.state.login.unknown = True
        self._sidebar().set_login_label(self.state.login.label)
