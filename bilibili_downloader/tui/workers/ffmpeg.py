"""FFmpeg check worker — wraps ``FFmpegManager.check_available``."""

from __future__ import annotations

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker


class FFmpegCheckWorker(CoreWorker):
    def __init__(self, app, custom_path: str | None = None):
        super().__init__(app)
        self._custom_path = custom_path

    def run(self) -> None:
        from bilibili_downloader.core.ffmpeg import FFmpegManager

        try:
            available, msg = FFmpegManager.check_available(self._custom_path)
            self.emit(messages.FFmpegResult(available, msg))
        except Exception as exc:  # noqa: BLE001
            self.emit(messages.FFmpegResult(False, str(exc)))
