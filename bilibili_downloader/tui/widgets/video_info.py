"""Video info panel — title, author, duration, BV (no cover image).

Terminal raster images are out of scope, so the cover is represented by a
placeholder note (the cover is still archived to the download directory).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class VideoInfoPanel(Vertical):
    DEFAULT_CSS = """
    VideoInfoPanel {
        background: #161619;
        border: solid #26262b;
        padding: 1 2;
        height: auto;
        min-height: 7;
        width: 1fr;
    }
    VideoInfoPanel #vi-title { color: #ffffff; text-style: bold; }
    VideoInfoPanel .vi-chip { color: #a0a0aa; }
    VideoInfoPanel #vi-state { color: #3fb950; }
    VideoInfoPanel #vi-hint { color: #6b6b75; }
    """

    def compose(self) -> ComposeResult:
        yield Static("视频信息  [待解析]", id="vi-state")
        yield Static("粘贴链接并解析以显示视频信息", id="vi-title")
        yield Static("解析后可在下方选择画质与编码", id="vi-hint", classes="vi-chip")
        yield Static("UP 主：—    时长：—    BV号：—", id="vi-meta", classes="vi-chip")

    def show_video(self, info) -> None:
        self.query_one("#vi-state", Static).update("视频信息  [已解析]")
        self.query_one("#vi-title", Static).update(info.title or "无标题")
        self.query_one("#vi-meta", Static).update(
            f"UP 主：{info.author or '未知'}    时长：{info.duration_str}    BV号：{info.bvid}"
        )

    def show_error(self, message: str) -> None:
        self.query_one("#vi-state", Static).update("视频信息  [解析失败]")
        self.query_one("#vi-title", Static).update(message)
