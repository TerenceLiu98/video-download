"""Settings screen — edit all AppSettings fields, validate, save.

Modal Screen. Fields map 1:1 to ``AppSettings`` (mirrors
``gui/dialogs/settings_dialog.py``). On save, the App persists via
``ConfigManager``, rebuilds the download semaphore if concurrency changed, and
refreshes home checkbox defaults.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from bilibili_downloader.core.models import VideoQuality

QUALITY_OPTIONS = [(q.label, q.value) for q in VideoQuality]
CODEC_OPTIONS = [("H.265 / HEVC", 12), ("H.264 / AVC", 7), ("AV1", 13)]


class SettingsScreen(ModalScreen):
    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical {
        width: 90; max-width: 92%; height: 34; max-height: 90%;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    SettingsScreen #ss-title { color: #ffffff; text-style: bold; }
    SettingsScreen .ss-label { color: #a0a0aa; }
    SettingsScreen Input, SettingsScreen Select { background: #1b1b1f; border: solid #33333a; }
    SettingsScreen #ss-save { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    SettingsScreen #ss-cancel { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    SettingsScreen #ss-check-ffmpeg { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    SettingsScreen .ss-row { height: auto; padding: 0 0 1 0; }
    """

    def __init__(self, settings):
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        s = self._settings
        with Vertical():
            yield Static("下载设置", id="ss-title")
            with VerticalScroll():
                yield Static("输出目录", classes="ss-label")
                yield Horizontal(
                    Input(value=s.output_dir, id="ss-output"),
                    Button("浏览", id="ss-browse-output"),
                    classes="ss-row",
                )

                yield Static("默认画质", classes="ss-label")
                yield Select(QUALITY_OPTIONS, value=s.default_quality.value, id="ss-quality", allow_blank=False)

                yield Static("默认编码", classes="ss-label")
                yield Select(CODEC_OPTIONS, value=s.default_video_codec, id="ss-codec", allow_blank=False)

                yield Static("并发下载数（1-8）", classes="ss-label")
                yield Horizontal(
                    Button("−", id="ss-dec"),
                    Input(value=str(s.max_concurrent_downloads), id="ss-jobs", type="integer"),
                    Button("+", id="ss-inc"),
                    classes="ss-row",
                )

                yield Static("FFmpeg 路径（留空自动检测）", classes="ss-label")
                yield Horizontal(
                    Input(value=s.ffmpeg_path, id="ss-ffmpeg", placeholder="留空自动检测"),
                    Button("浏览", id="ss-browse-ffmpeg"),
                    Button("检查", id="ss-check-ffmpeg"),
                    classes="ss-row",
                )

                yield Horizontal(
                    Checkbox("默认下载弹幕", value=s.download_danmaku, id="ss-danmaku"),
                    Checkbox("默认下载字幕", value=s.download_subtitle, id="ss-subtitle"),
                    classes="ss-row",
                )
                yield Horizontal(
                    Checkbox("默认保存元信息", value=s.download_metadata, id="ss-metadata"),
                    Checkbox("默认保存封面", value=s.download_cover, id="ss-cover"),
                    classes="ss-row",
                )
                yield Horizontal(
                    Checkbox("默认归档评论", value=s.download_comments, id="ss-comments"),
                    classes="ss-row",
                )
                yield Horizontal(
                    Checkbox("写入 MP4 元信息", value=s.embed_metadata, id="ss-embed-meta"),
                    Checkbox("写入 MP4 封面", value=s.embed_cover, id="ss-embed-cover"),
                    classes="ss-row",
                )
            yield Horizontal(
                Button("保存", id="ss-save"),
                Button("取消", id="ss-cancel"),
            )

    @on(Button.Pressed, "#ss-inc")
    def _inc(self) -> None:
        self._adjust_jobs(+1)

    @on(Button.Pressed, "#ss-dec")
    def _dec(self) -> None:
        self._adjust_jobs(-1)

    def _adjust_jobs(self, delta: int) -> None:
        inp = self.query_one("#ss-jobs", Input)
        try:
            val = int(inp.value or "1") + delta
        except ValueError:
            val = 1
        val = max(1, min(8, val))
        inp.value = str(val)
        self.query_one("#ss-inc", Button).disabled = val >= 8
        self.query_one("#ss-dec", Button).disabled = val <= 1

    @on(Button.Pressed, "#ss-check-ffmpeg")
    def _check_ffmpeg(self) -> None:
        path = self.query_one("#ss-ffmpeg", Input).value.strip() or None
        self.app.start_ffmpeg_check(path)

    @on(Button.Pressed, "#ss-browse-output")
    def _browse_output(self) -> None:
        from bilibili_downloader.tui.screens import FileBrowserScreen

        start = self.query_one("#ss-output", Input).value.strip() or None
        self.app.push_screen(
            FileBrowserScreen(start or None, select_file=False, title="选择保存目录"),
            self._apply_browsed("#ss-output"),
        )

    @on(Button.Pressed, "#ss-browse-ffmpeg")
    def _browse_ffmpeg(self) -> None:
        from bilibili_downloader.tui.screens import FileBrowserScreen, _ffmpeg_filter

        start = self.query_one("#ss-ffmpeg", Input).value.strip() or None
        self.app.push_screen(
            FileBrowserScreen(
                start or None,
                select_file=True,
                name_filter=_ffmpeg_filter,
                title="选择 FFmpeg",
            ),
            self._apply_browsed("#ss-ffmpeg"),
        )

    def _apply_browsed(self, input_id: str):
        """Build the dismiss callback that writes a picked Path into an Input."""
        def _write(picked) -> None:
            if picked is not None:
                self.query_one(input_id, Input).value = str(picked)
        return _write

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ss-cancel":
            self.dismiss(None)
        elif event.button.id == "ss-save":
            self._save()

    def _save(self) -> None:
        output = self.query_one("#ss-output", Input).value.strip()
        if not output:
            self.app.notify("输出目录不能为空", severity="error")
            return
        ffmpeg = self.query_one("#ss-ffmpeg", Input).value.strip()
        if ffmpeg and not Path(ffmpeg).is_file():
            self.app.notify("FFmpeg 路径不是有效文件", severity="error")
            return
        try:
            jobs = max(1, min(8, int(self.query_one("#ss-jobs", Input).value or "1")))
        except ValueError:
            jobs = 1

        new = self._settings.model_copy(update={
            "output_dir": output,
            "default_quality": VideoQuality(self.query_one("#ss-quality", Select).value),
            "default_video_codec": self.query_one("#ss-codec", Select).value,
            "max_concurrent_downloads": jobs,
            "ffmpeg_path": ffmpeg,
            "download_danmaku": self.query_one("#ss-danmaku", Checkbox).value,
            "download_subtitle": self.query_one("#ss-subtitle", Checkbox).value,
            "download_metadata": self.query_one("#ss-metadata", Checkbox).value,
            "download_cover": self.query_one("#ss-cover", Checkbox).value,
            "download_comments": self.query_one("#ss-comments", Checkbox).value,
            "embed_metadata": self.query_one("#ss-embed-meta", Checkbox).value,
            "embed_cover": self.query_one("#ss-embed-cover", Checkbox).value,
        })
        self.dismiss(new)
