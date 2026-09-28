"""Control panel — page / quality / codec selectors + archival toggles.

Mirrors the Qt control panel. Selection state is read by the App to build
``DownloadItem``s (so this widget stays presentation-only). The subtitle
checkbox is disabled until a successful resolve (parity with Qt).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Select, Static

CODEC_LABELS = {12: "H.265 / HEVC", 7: "H.264 / AVC", 13: "AV1"}

# Full quality list (fallback when playurl discovery fails).
FULL_QUALITY = [
    (127, "8K"), (126, "杜比视界"), (125, "HDR"), (120, "4K"),
    (116, "1080P60"), (112, "1080P+ 高码率"), (80, "1080P"), (64, "720P"),
    (32, "480P"), (16, "360P"), (6, "240P"),
]
QUALITY_PRIORITY = [127, 126, 125, 120, 116, 112, 80, 64, 32, 16, 6]


class ControlPanel(Vertical):
    DEFAULT_CSS = """
    ControlPanel {
        background: #161619;
        border: solid #26262b;
        padding: 1 2;
        height: auto;
        width: 1fr;
    }
    ControlPanel #cp-title { color: #ffffff; text-style: bold; }
    ControlPanel .cp-hint { color: #6b6b75; }
    ControlPanel .cp-field-label { color: #a0a0aa; }
    ControlPanel .cp-row { height: auto; padding: 0 0 1 0; }
    ControlPanel Select { background: #161619; border: solid #33333a; width: 1fr; }
    ControlPanel #download-btn {
        background: #6366f1; color: #ffffff; border: solid #7c7ff5; text-style: bold;
    }
    ControlPanel #download-btn:hover { background: #7c7ff5; }
    ControlPanel #download-btn:disabled { background: #1b1b1f; color: #6b6b75; border: solid #26262b; }
    """

    def compose(self) -> ComposeResult:
        yield Static("输出规格", id="cp-title")
        yield Static("选择画质与编码", classes="cp-hint")

        yield Static("分 P 范围", classes="cp-field-label")
        yield Select([("单 P 视频", "current")], value="current", id="page-select", allow_blank=False)

        yield Static("画面质量", classes="cp-field-label")
        yield Select(
            [(lbl, qid) for qid, lbl in FULL_QUALITY],
            value=80, id="quality-select", allow_blank=False,
        )

        yield Static("视频编码", classes="cp-field-label")
        yield Select(
            [(CODEC_LABELS[c], c) for c in (12, 7, 13)],
            value=12, id="codec-select", allow_blank=False,
        )

        yield Horizontal(
            Checkbox("元信息", id="chk-metadata"),
            Checkbox("封面", id="chk-cover"),
            classes="cp-row",
        )
        yield Horizontal(
            Checkbox("评论", id="chk-comments"),
            Checkbox("弹幕", id="chk-danmaku"),
            classes="cp-row",
        )
        yield Checkbox("字幕", id="chk-subtitle", disabled=True)

        yield Button("加入下载队列", id="download-btn", classes="primary")
        yield Horizontal(
            Button("批量导入链接", id="batch-btn"),
            Button("UP 主投稿索引", id="creator-btn"),
        )

    def __init__(self):
        super().__init__()
        self._video_streams = []

    # --- population on resolve ---
    def populate_pages(self, info) -> None:
        select = self.query_one("#page-select", Select)
        if info.is_multi_part:
            opts = [(f"全部 {len(info.pages)} P", "all")] + [
                (f"P{p.page} · {p.part or '未命名'}", p) for p in info.pages
            ]
            select.set_options(opts)
            select.value = "all"
        else:
            select.set_options([("单 P 视频", "current")])
            select.value = "current"

    def populate_quality(self, video_streams, default_quality) -> None:
        select = self.query_one("#quality-select", Select)
        available_qids = {s.id for s in video_streams}
        if available_qids:
            ordered = [qid for qid in QUALITY_PRIORITY if qid in available_qids]
            select.set_options([(self._qlabel(qid), qid) for qid in ordered])
            select.value = default_quality.value if default_quality.value in ordered else ordered[0]
        else:
            select.set_options([(lbl, qid) for qid, lbl in FULL_QUALITY])
            select.value = default_quality.value if default_quality.value in dict(FULL_QUALITY) else 80

    def refresh_codecs(self, video_streams, quality_value, preferred_codec) -> None:
        select = self.query_one("#codec-select", Select)
        available = {s.codecid for s in video_streams if s.id == quality_value}
        codecs = [c for c in (12, 7, 13) if not available or c in available]
        select.set_options([(CODEC_LABELS[c], c) for c in codecs])
        select.value = preferred_codec if preferred_codec in codecs else (codecs[0] if codecs else 12)

    def set_video_streams(self, video_streams) -> None:
        """Store the current video streams for quality-change codec filtering."""
        self._video_streams = video_streams

    @on(Select.Changed, "#quality-select")
    def _on_quality_changed(self, event: Select.Changed) -> None:
        """When quality changes, re-filter available codecs (parity with GUI)."""
        if self._video_streams:
            current_codec = self.query_one("#codec-select", Select).value
            self.refresh_codecs(self._video_streams, event.value, current_codec)

    def enable_subtitle(self) -> None:
        self.query_one("#chk-subtitle", Checkbox).disabled = False

    @staticmethod
    def _qlabel(qid: int) -> str:
        return next((lbl for q, lbl in FULL_QUALITY if q == qid), str(qid))

    # --- selection accessors ---
    @property
    def page_selection(self):
        return self.query_one("#page-select", Select).value

    @property
    def quality_value(self):
        return self.query_one("#quality-select", Select).value

    @property
    def codec_value(self):
        return self.query_one("#codec-select", Select).value

    def checkbox_values(self):
        return {
            "metadata": self.query_one("#chk-metadata", Checkbox).value,
            "cover": self.query_one("#chk-cover", Checkbox).value,
            "comments": self.query_one("#chk-comments", Checkbox).value,
            "danmaku": self.query_one("#chk-danmaku", Checkbox).value,
            "subtitle": self.query_one("#chk-subtitle", Checkbox).value,
        }

    def sync_defaults_from_settings(self, settings) -> None:
        """Reflect default archival toggles from settings into checkboxes."""
        self.query_one("#chk-metadata", Checkbox).value = settings.download_metadata
        self.query_one("#chk-cover", Checkbox).value = settings.download_cover
        self.query_one("#chk-comments", Checkbox).value = settings.download_comments
        self.query_one("#chk-danmaku", Checkbox).value = settings.download_danmaku
        self.query_one("#chk-subtitle", Checkbox).value = settings.download_subtitle
