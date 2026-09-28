"""Resolve cache: disk-backed bvid → VideoInfo cache for batch operations.

Reduces redundant API calls when re-processing creator indexes by persisting
resolved VideoInfo objects to disk. Cache files live alongside the creator
index as index.resolvecache.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from bilibili_downloader.core.creator import atomic_write_json
from bilibili_downloader.core.models import CreatorVideoIndex, VideoInfo
from bilibili_downloader.utils.validators import extract_bvid

logger = logging.getLogger(__name__)


class ResolveCache:
    """Disk-backed bvid → VideoInfo cache for batch resolution.

    Loads on first access (lazy), marks dirty on writes, and atomically saves
    on demand. Missing/corrupted cache files are treated as empty caches rather
    than failing the batch.
    """

    def __init__(self, path: Path) -> None:
        """Initialize a cache at ``path`` (load is deferred until first get/save)."""
        self._path = path
        self._data: dict[str, dict] = {}
        self._dirty = False
        self._loaded = False

    @classmethod
    def for_creator(cls, output_dir: Path | str, index: CreatorVideoIndex | None) -> Optional[ResolveCache]:
        """Build a ResolveCache for a creator index, or None if no index provided.

        The cache file is placed next to index.json as index.resolvecache.json.
        If ``index`` is None (plain batch paste), this returns None (no caching).
        """
        if index is None:
            return None

        from bilibili_downloader.core.creator import creator_directory_name

        out_dir = Path(output_dir) if isinstance(output_dir, str) else output_dir
        creator_dir = out_dir / creator_directory_name(index.name, index.mid)
        cache_path = creator_dir / "index.resolvecache.json"

        return cls(cache_path)

    def get(self, source: str) -> Optional[VideoInfo]:
        """Look up a cached VideoInfo by bvid extracted from ``source``.

        Returns:
            VideoInfo if cached and valid, None otherwise (cache miss or no bvid).
        """
        bvid = extract_bvid(source)
        if not bvid:
            return None

        if not self._loaded:
            self._load()

        key = bvid.lower()
        payload = self._data.get(key)
        if not payload:
            return None

        try:
            return VideoInfo.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache hit for %s but payload invalid: %s", key, exc)
            return None

    def put(self, source: str, info: VideoInfo) -> None:
        """Store a VideoInfo in the cache keyed by its bvid.

        If ``source`` yields no bvid, this is a no-op.
        """
        bvid = extract_bvid(source)
        if not bvid:
            return

        if not self._loaded:
            self._load()

        key = bvid.lower()
        self._data[key] = info.model_dump(mode="json")
        self._dirty = True

    def save(self) -> None:
        """Atomically persist cache to disk if dirty (no-op otherwise)."""
        if not self._dirty:
            return

        try:
            atomic_write_json(self._path, self._data)
            self._dirty = False
            logger.debug("resolve cache saved: %s", self._path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to save resolve cache: %s", exc)

    def _load(self) -> None:
        """Load cache from disk if it exists; tolerate corruption as empty cache."""
        if self._loaded:
            return

        self._loaded = True
        if not self._path.is_file():
            return

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._data = payload
                logger.debug("resolve cache loaded: %s", self._path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load resolve cache (will treat as empty): %s", exc)
            self._data = {}
