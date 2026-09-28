"""Resolve worker — URL → VideoInfo + available streams.

Mirrors ``gui/threads/resolve_worker.py`` but without Qt. The playurl call is
non-fatal (mirrors Qt: a failure still yields the video info, just without
quality/codec discovery).
"""

from __future__ import annotations

import logging

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)


class ResolveWorker(CoreWorker):
    def __init__(self, app, client, source: str):
        super().__init__(app)
        self._client = client
        self._source = source

    def run(self) -> None:
        from bilibili_downloader.core.batch import BatchResolver
        from bilibili_downloader.core.models import VideoQuality

        try:
            info = BatchResolver(self._client).resolve_one(self._source)
        except Exception as exc:  # noqa: BLE001
            self.emit(messages.ResolveFailed(self._source, str(exc)))
            return

        video_streams: list = []
        audio_streams: list = []
        playurl_ok = False
        try:
            data = self._client.get_play_url(
                bvid=info.bvid,
                cid=info.cid,
                quality=VideoQuality.Q8K,
                discover_all=True,
            )
            video_streams = data.get("video_streams", [])
            audio_streams = data.get("audio_streams", [])
            playurl_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("playurl discovery failed for %s: %s", info.bvid, exc)

        self.emit(messages.ResolveFinished(info, video_streams, audio_streams, playurl_ok))
