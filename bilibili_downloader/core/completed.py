"""Ledger of finished standalone downloads, so re-runs skip them.

Creator archives use stable ``[BV…] …/P01 [cid] ….mp4`` paths and are
recognised on disk directly. Standalone downloads are named after the title
and renamed on collision, so a file name alone cannot tell "this video" from
"another video with the same title". The ledger records which file holds
which ``bvid:cid``.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from bilibili_downloader.core.archive import ArtifactPaths
from bilibili_downloader.core.creator import atomic_write_json
from bilibili_downloader.core.models import DownloadItem

logger = logging.getLogger(__name__)

LEDGER_NAME = ".biliflow-downloads.json"
_LOCK = threading.Lock()


def find_completed(output_dir: Path, item: DownloadItem) -> Optional[Path]:
    """Return the finished media file for ``item``, or None."""
    with _LOCK:
        name = _load(output_dir).get(_key(item))
    if name:
        path = output_dir / name
        if _non_empty(path):
            return path

    # Files downloaded before the ledger existed: trust the default name only
    # when its info.json sidecar names the same video.
    legacy = output_dir / item.filename
    if _non_empty(legacy) and _sidecar_matches(output_dir, item, legacy):
        record_completed(output_dir, item, legacy)
        return legacy
    return None


def record_completed(output_dir: Path, item: DownloadItem, path: Path) -> None:
    """Remember that ``path`` holds the finished media for ``item``."""
    try:
        relative = Path(path).resolve().relative_to(Path(output_dir).resolve())
    except ValueError:
        return
    try:
        with _LOCK:
            # Re-read before writing so entries from other runs survive.
            data = _load(output_dir)
            data[_key(item)] = relative.as_posix()
            atomic_write_json(output_dir / LEDGER_NAME, data)
    except OSError as exc:
        logger.warning("Could not update download ledger: %s", exc)


def _key(item: DownloadItem) -> str:
    return f"{item.video_info.bvid}:{item.video_info.cid}"


def _load(output_dir: Path) -> dict[str, str]:
    path = output_dir / LEDGER_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable download ledger %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _non_empty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _sidecar_matches(output_dir: Path, item: DownloadItem, media: Path) -> bool:
    sidecar = ArtifactPaths(output_dir, item).sidecars(media)["metadata"]
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    return isinstance(data, dict) and data.get("bvid") == item.video_info.bvid
