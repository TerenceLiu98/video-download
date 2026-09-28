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
from textual.widgets import Button

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.tui import messages
from bilibili_downloader.tui.screens.batch_screen import BatchScreen
from bilibili_downloader.tui.screens.confirm_screen import ConfirmScreen
from bilibili_downloader.tui.screens.creator_screen import CreatorScreen
from bilibili_downloader.tui.screens.help_screen import HelpScreen
from bilibili_downloader.tui.screens.login_screen import LoginScreen
from bilibili_downloader.tui.screens.main_screen import MainScreen
from bilibili_downloader.tui.state import AppState, LoginState
from bilibili_downloader.tui.widgets.download_queue import (
    DownloadQueue,
    DownloadQueueModel,
)
from bilibili_downloader.tui.widgets.sidebar import NavSidebar
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
        yield NavSidebar()
        yield MainScreen()
        yield DownloadQueue(self._model)

    def on_mount(self) -> None:
        config = ConfigManager()
        settings = config.load()
        client = BilibiliAPIClient(sessdata=settings.sessdata or None)
        self.state = AppState(config=config, settings=settings, api_client=client, queue=self._model)
        self._semaphore = threading.Semaphore(max(1, settings.max_concurrent_downloads))

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
            pass
        elif btn_id in ("nav-batch", "batch-btn"):
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
        self.notify(f"正在批量解析 {n} 个链接…")
        self.start_batch(urls, flags, quality, codec,
                         creator_mid=(creator_index.mid if creator_index else 0),
                         creator_name=(creator_index.name if creator_index else ""),
                         creator_index=creator_index)

    @work(thread=True, group="batch")
    def start_batch(self, urls, flags, quality, codec, creator_mid=0, creator_name="", creator_index=None) -> None:
        assert self.state is not None
        from bilibili_downloader.core.models import VideoQuality
        from bilibili_downloader.tui.resolve_cache import ResolveCache

        q = VideoQuality(quality) if quality and not isinstance(quality, VideoQuality) else quality
        cache = ResolveCache.for_creator(self.state.settings.output_dir, creator_index) if creator_index else None
        BatchWorker(
            self, self.state.api_client, urls, flags,
            quality=q, codec=codec, creator_mid=creator_mid, creator_name=creator_name, cache=cache,
        ).run()

    @on(messages.BatchItemReady)
    def _on_batch_item_ready(self, message: messages.BatchItemReady) -> None:
        self.enqueue_download(message.item)

    @on(messages.BatchItemFailed)
    def _on_batch_item_failed(self, message: messages.BatchItemFailed) -> None:
        assert self.state is not None
        self.state.queue.add_error(message.error)
        self._refresh_queue()

    @on(messages.BatchProgress)
    def _on_batch_progress(self, message: messages.BatchProgress) -> None:
        """Only notify on completion to avoid spam for large indexes."""
        if message.done == message.total:
            self.notify(f"批量解析完成 {message.total} 条", severity="information")

    @on(messages.BatchItemRetrying)
    def _on_batch_item_retrying(self, message: messages.BatchItemRetrying) -> None:
        """User-visible backoff feedback for 风控 retries."""
        self.notify(f"{message.source} 触发风控，第{message.attempt}次重试（等待 {message.delay:g}s）", severity="warning")

    @on(messages.BatchDone)
    def _on_batch_done_msg(self, message: messages.BatchDone) -> None:
        # BatchProgress now handles completion notification, so we suppress this to avoid duplicate toasts.
        pass

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

    def enqueue_download(self, item) -> None:
        assert self.state is not None
        download_id = self.state.queue.add(item)
        self._refresh_queue()
        self.run_download_worker(download_id, item)

    @work(thread=True, group="download")
    def run_download_worker(self, download_id: int, item) -> None:
        assert self.state is not None and self._semaphore is not None
        worker = DownloadWorker(
            self,
            self.state.api_client,
            item,
            self.state.settings.output_dir,
            download_id,
            self.state.settings.ffmpeg_path or None,
        )
        self.state.queue.register_worker(download_id, worker)
        self._semaphore.acquire()
        try:
            worker.run()
        finally:
            self._semaphore.release()

    @on(messages.DownloadProgress)
    def _on_download_progress(self, message: messages.DownloadProgress) -> None:
        assert self.state is not None
        self.state.queue.set_progress(message.download_id, message.pct, message.status_text)
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
        try:
            self.query_one(DownloadQueue).refresh_model()
        except Exception:  # noqa: BLE001
            pass

    # --- queue key actions (cancel / retry / delete) ---
    @on(DownloadQueue.QueueAction)
    def _on_queue_action(self, message: DownloadQueue.QueueAction) -> None:
        assert self.state is not None
        action = message.action
        did = message.download_id
        if action == "cancel_all":
            self.state.queue.cancel_all()
            self.notify("已请求取消所有任务")
        elif action == "cancel":
            self.state.queue.cancel(did)
        elif action == "delete":
            self.state.queue.delete(did)
        elif action == "retry":
            self._retry_download(did)
        self._refresh_queue()

    def _retry_download(self, download_id: int) -> None:
        assert self.state is not None
        item = self.state.queue.get_item(download_id)
        if item is None:
            self.notify("无法重试该任务", severity="warning")
            return
        self.state.queue.mark_retry(download_id)
        self._refresh_queue()
        self.run_download_worker(download_id, item)

    # --- quit confirmation (parity with MainWindow.closeEvent) ---
    def action_quit(self) -> None:
        assert self.state is not None
        if self.state.queue.has_active:
            self.push_screen(
                ConfirmScreen("仍有下载任务正在运行。退出将保留断点数据，确认停止并退出吗？"),
                self._on_quit_confirm,
            )
        else:
            self._do_quit()

    def _on_quit_confirm(self, confirmed: bool) -> None:
        if confirmed:
            self._do_quit()

    def _do_quit(self) -> None:
        assert self.state is not None
        self.state.queue.cancel_all()
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
        nav = message.nav_info.get("data") or {}
        self.state.login.checking = False
        self.state.login.unknown = False
        if message.nav_info.get("data", {}).get("isLogin") if "data" in message.nav_info else nav.get("isLogin"):
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
        self._sidebar().set_login_label(self.state.login.label)
