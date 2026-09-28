"""Creator index worker — fetch a UP-creator's full submission index.

Mirrors ``gui/threads/creator_worker.py``: drives
``CreatorIndexService.fetch`` with progress/status/cancel callbacks and a
checkpoint callback that persists ``index.partial.json`` for resume.
"""

from __future__ import annotations

import logging

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)


class CreatorIndexWorker(CoreWorker):
    def __init__(self, app, client, source: str, output_dir: str, resume_index=None):
        super().__init__(app)
        self._client = client
        self._source = source
        self._output_dir = output_dir
        self._resume_index = resume_index

    def run(self) -> None:
        from pathlib import Path

        from bilibili_downloader.core.creator import (
            CreatorIndexService,
            creator_directory_name,
            save_creator_index,
        )

        out = Path(self._output_dir)

        def save_checkpoint(index) -> None:
            ckpt = out / creator_directory_name(index.name, index.mid) / "index.partial.json"
            save_creator_index(index, ckpt)
            self.emit(messages.CreatorStatus(f"已自动保存检查点：{len(index.videos)} 个投稿"))

        try:
            index = CreatorIndexService(self._client).fetch(
                self._source,
                progress_callback=lambda d, t: self.emit(messages.CreatorProgress(d, t)),
                cancel_checker=self.cancel_checker,
                status_callback=lambda s: self.emit(messages.CreatorStatus(s)),
                resume_index=self._resume_index,
                checkpoint_callback=save_checkpoint,
            )
            self.emit(messages.CreatorFinished(index))
        except Exception as exc:  # noqa: BLE001
            if "cancelled" in str(exc).lower():
                return
            logger.error("creator index failed: %s", exc)
            self.emit(messages.CreatorFailed(str(exc)))
