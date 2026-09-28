"""Batch worker — resolve many URLs, emit per-video items.

Mirrors ``gui/threads/batch_worker.py`` (``BatchResolver.resolve_one`` per URL,
multi-P expansion) without Qt. Each resolved video becomes a ``BatchItemReady``
message the App enqueues; each failed URL becomes a ``BatchItemFailed``.

Resilience: B站批量解析容易触发风控（412/429 / -352）。每个条目按
``_RETRY_DELAYS`` 做长退避重试（宁可多等也不要太快，对齐 core/creator.py 的
``CreatorIndexService``），成功后插入一个随机间隔避免连发。风控重试期间发
``BatchItemRetrying`` 反馈；每解析一条发 ``BatchProgress``。
"""

from __future__ import annotations

import logging
import random
import time

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)

# Backoff (seconds) before each retry of a rate-limited / transient resolve
# failure. Mirrors CreatorIndexService.retry_delays — when B站 returns 412/429
# we wait long rather than hammering and getting fully blocked.
_RETRY_DELAYS: tuple[float, ...] = (10.0, 30.0, 60.0)

# A small random pause between successful resolves so a long creator index
# doesn't fire hundreds of /view requests back-to-back.
_INTER_RESOLVE_RANGE = (0.5, 1.5)

# Seam for tests: real workers sleep via time.sleep/time.monotonic, but tests
# monkeypatch this to a no-op so the suite doesn't wait minutes.
_sleep = time.sleep
_monotonic = time.monotonic


class BatchWorker(CoreWorker):
    def __init__(self, app, client, urls, flags: dict,
                 quality=None, codec=None,
                 creator_mid: int = 0, creator_name: str = "",
                 cache=None):
        super().__init__(app)
        self._client = client
        self._urls = urls
        self._flags = flags
        self._quality = quality
        self._codec = codec
        self._creator_mid = creator_mid
        self._creator_name = creator_name
        self._cache = cache

    def run(self) -> None:
        from bilibili_downloader.core.batch import BatchResolver
        from bilibili_downloader.core.creator import _retryable_creator_error
        from bilibili_downloader.core.models import DownloadItem

        resolver = BatchResolver(self._client)
        total = len(self._urls)
        for index, url in enumerate(self._urls, start=1):
            if self._cancel.is_set():
                break

            # Check cache before resolving (bypass network if cached).
            info = None
            if self._cache:
                info = self._cache.get(url)

            # Cache miss or no cache: resolve with retry.
            if info is None:
                info = self._resolve_with_retry(resolver, url, _retryable_creator_error)
                # Cache the successfully resolved info.
                if info is not None and self._cache:
                    self._cache.put(url, info)

            if info is not None:
                pages = [info.for_page(p) for p in info.pages] if info.is_multi_part else [info]
                for pi in pages:
                    item = DownloadItem(
                        video_info=pi,
                        selected_quality=self._quality,
                        selected_video_codec=self._codec,
                        download_danmaku=self._flags.get("danmaku", False),
                        download_subtitle=self._flags.get("subtitle", False),
                        download_metadata=self._flags.get("metadata", False),
                        download_cover=self._flags.get("cover", False),
                        download_comments=self._flags.get("comments", False),
                        embed_metadata=self._flags.get("embed_metadata", False),
                        embed_cover=self._flags.get("embed_cover", False),
                        creator_mid=self._creator_mid,
                        creator_name=self._creator_name,
                    )
                    self.emit(messages.BatchItemReady(item))
            # Pace between items so we don't trip rate limits on long indexes.
            if index < total and not self._cancel.is_set():
                _sleep(random.uniform(*_INTER_RESOLVE_RANGE))
            self.emit(messages.BatchProgress(index, total))

        # Persist cache if it was updated.
        if self._cache:
            self._cache.save()

        self.emit(messages.BatchDone())

    def _resolve_with_retry(self, resolver, source, is_retryable) -> object | None:
        """Resolve one source, retrying transient/风控 errors with long backoff.

        Returns the resolved ``VideoInfo`` on success, or ``None`` if every
        attempt failed (the failure is already reported via BatchItemFailed).
        Non-retryable errors (e.g. an unrecognised input) fail immediately.
        """
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            if self._cancel.is_set():
                return None
            try:
                return resolver.resolve_one(source)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not is_retryable(exc) or attempt >= len(_RETRY_DELAYS):
                    logger.warning("batch resolve failed for %s: %s", source, exc)
                    self.emit(messages.BatchItemFailed(f"无法解析 {source}: {exc}"))
                    return None
                delay = _RETRY_DELAYS[attempt]
                logger.info(
                    "batch resolve %s hit transient error (%s); retry %d in %gs",
                    source, exc, attempt + 1, delay,
                )
                self.emit(messages.BatchItemRetrying(source, attempt + 1, delay))
                self._cancellable_wait(delay)
        # Unreachable: the loop returns or reports failure inside.
        if last_exc is not None:  # pragma: no cover - defensive
            self.emit(messages.BatchItemFailed(f"无法解析 {source}: {last_exc}"))
        return None

    def _cancellable_wait(self, delay: float) -> None:
        """Sleep for ``delay`` but wake promptly if the user cancels."""
        deadline = _monotonic() + delay
        while _monotonic() < deadline:
            if self._cancel.is_set():
                return
            _sleep(min(0.1, max(0.0, deadline - _monotonic())))
