"""Download worker — runs one download on a thread.

Each download gets its OWN ``DownloadService`` instance because the service
holds mutable per-download state (``self._cancelled``, ``downloader.last_*``);
they share the httpx client (thread-safe) and the module-level
``_SIDECAR_LOCK``. Mirrors ``gui/threads/download_worker.py`` semantics
without Qt.
"""

from __future__ import annotations

import logging
import time

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)

# Minimum wall-clock gap between two DownloadProgress emissions, and minimum
# integer-percent delta that always forces an emit. Together they bound
# message traffic from "one per download callback" (hundreds on a long video)
# to a handful per second, so the UI repaints smoothly instead of thrashing.
_PROGRESS_MIN_INTERVAL = 0.5
_PROGRESS_MIN_PCT_DELTA = 1


class DownloadWorker(CoreWorker):
    def __init__(self, app, client, item, output_dir: str, download_id: int,
                 ffmpeg_path: str | None = None):
        super().__init__(app)
        self._client = client
        self._item = item
        self._output_dir = output_dir
        self._download_id = download_id
        self._ffmpeg_path = ffmpeg_path
        self._service = None  # built in run()
        self._last_pct: int = -1
        self._last_text: str = ""
        self._last_emit_at: float = 0.0

    def run(self) -> None:
        import os

        from bilibili_downloader.core.download_service import DownloadService

        try:
            os.makedirs(self._output_dir, exist_ok=True)
            self._service = DownloadService(
                self._client, self._output_dir, ffmpeg_path=self._ffmpeg_path
            )

            def progress_cb(pct: float, text: str) -> None:
                if self._cancel.is_set():
                    self._service.cancel()
                    raise RuntimeError("cancel")
                self._emit_progress(pct, text)

            outcome = self._service.download(self._item, progress_cb)
            if self._cancel.is_set():
                self.emit(messages.DownloadCancelled(self._download_id))
            else:
                self.emit(messages.DownloadFinished(self._download_id, outcome))
        except Exception as exc:  # noqa: BLE001
            if self._cancel.is_set() or "cancel" in str(exc).lower():
                self.emit(messages.DownloadCancelled(self._download_id))
            else:
                logger.error("download %s failed: %s", self._download_id, exc)
                self.emit(messages.DownloadFailed(self._download_id, str(exc)))

    def cancel(self) -> None:  # type: ignore[override]
        super().cancel()
        if self._service is not None:
            self._service.cancel()

    def _emit_progress(self, pct: float, text: str) -> None:
        """Throttle DownloadProgress before posting it to the UI.

        Emits when the integer percent advanced by at least
        ``_PROGRESS_MIN_PCT_DELTA``, when the status text changed, or when
        ``_PROGRESS_MIN_INTERVAL`` seconds elapsed since the last emit — whichever
        fires first. This collapses bursts of identical-percentage callbacks
        (common while a large segment streams) into a single UI repaint.

        ``pct`` is a fraction in [0, 1] (the core download service reports it
        that way, and the queue model later does ``int(pct * 100)``); the
        throttle compares whole-percent steps, so it converts the same way.
        """
        now = time.monotonic()
        int_pct = int(pct * 100)
        pct_changed = abs(int_pct - self._last_pct) >= _PROGRESS_MIN_PCT_DELTA
        text_changed = text != self._last_text
        elapsed = (now - self._last_emit_at) >= _PROGRESS_MIN_INTERVAL
        if not (pct_changed or text_changed or elapsed):
            return
        self._last_pct = int_pct
        self._last_text = text
        self._last_emit_at = now
        self.emit(messages.DownloadProgress(self._download_id, pct, text))
