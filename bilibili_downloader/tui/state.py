"""Shared application state for the TUI.

A plain container (not reactive) — Textual workers post messages for UI
updates, so state mutations flow back through ``Message``s rather than
reactive reactivity. The App holds the single ``AppState``; workers receive
``api_client`` + ``settings`` snapshots; UI reads ``app.state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.models import AppSettings
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueueModel
    from bilibili_downloader.utils.config import ConfigManager


@dataclass
class LoginState:
    uname: str = ""
    mid: str = ""
    is_login: bool = False
    checking: bool = False
    unknown: bool = False

    @property
    def label(self) -> str:
        if self.unknown:
            return "登录状态未知"
        if self.checking:
            return "正在检查账号…"
        if self.is_login:
            label = f"已登录：{self.uname}" if self.uname else "已登录"
            if self.mid:
                label += f" ({self.mid})"
            return label
        return "未登录"


@dataclass
class BatchJob:
    batch_id: int
    total: int
    output_dir: str
    done: int = 0
    failed: int = 0
    running: bool = True
    status: str = "解析中"
    worker: Optional[object] = None
    row_ids: list[int] = field(default_factory=list)
    part_rows: dict[int, list[int]] = field(default_factory=dict)


@dataclass
class AppState:
    config: "ConfigManager"
    settings: "AppSettings"
    api_client: "BilibiliAPIClient"
    queue: "DownloadQueueModel"
    login: LoginState = field(default_factory=LoginState)
    # Monotonic counter to discard stale login-status results.
    login_request_id: int = 0
    # The resolved video + its stream info, held for the control panel.
    current_video: Optional[object] = None
    current_video_streams: list = field(default_factory=list)
    current_playurl_ok: bool = False
