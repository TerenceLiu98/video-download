"""Persistent home workspace, hidden while the task workspace is visible."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from bilibili_downloader.tui.widgets.control_panel import ControlPanel
from bilibili_downloader.tui.widgets.url_bar import UrlBar
from bilibili_downloader.tui.widgets.video_info import VideoInfoPanel


class MainScreen(Vertical):
    DEFAULT_CSS = """
    MainScreen { background: #121214; }
    MainScreen > VerticalScroll {
        padding: 1 2;
        width: 1fr;
        height: 1fr;
    }
    MainScreen #ms-page-title { color: #ffffff; text-style: bold; }
    MainScreen #ms-page-caption { color: #a0a0aa; }
    MainScreen .ms-gap { height: 1; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("下载", id="ms-page-title")
            yield Static("解析链接，选择规格，加入下载队列", id="ms-page-caption")
            yield Static(classes="ms-gap")
            yield UrlBar()
            yield Static(classes="ms-gap")
            yield VideoInfoPanel()
            yield Static(classes="ms-gap")
            yield ControlPanel()
