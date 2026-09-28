"""``rebuild-cache`` CLI subcommand — rebuild the resolve cache from existing info.json.

When a creator's videos were downloaded by an older code path (or on another
machine), the per-video ``info.json`` files already sit on disk next to each
``[BV...]`` subdirectory. Each ``info.json`` is the raw ``/x/web-interface/view``
response — exactly the input ``_parse_video_info`` consumes to produce a
:class:`VideoInfo`. So instead of re-resolving every bvid over the network
(which is slow and risks tripping B站 风控), this command walks the creator
directory, converts each ``info.json`` into a ``VideoInfo``, and merges them
into ``index.resolvecache.json``. A later ``download-index`` / TUI batch run
then hits the cache and resolves nothing.

Reuses :func:`bilibili_downloader.api.client._parse_video_info` and
:class:`bilibili_downloader.tui.resolve_cache.ResolveCache` unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from bilibili_downloader.api.client import _parse_video_info

logger = logging.getLogger(__name__)

# Filename of the raw /view response written next to each downloaded video.
_INFO_JSON = "info.json"


@dataclass
class RebuildReport:
    """Outcome of a rebuild run — surfaced to the user and asserted in tests."""

    cached: int = 0          # info.json files successfully turned into cache entries
    skipped: int = 0         # subdirs skipped (no info.json, unreadable, or no bvid)
    cache_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def rebuild_cache(creator_dir: Path) -> RebuildReport:
    """Rebuild ``<creator_dir>/index.resolvecache.json`` from existing info.json.

    Walks the **direct** subdirectories of ``creator_dir``, reads each
    ``info.json`` (raw /view response), feeds its ``data`` field to
    :func:`_parse_video_info`, and merges the resulting :class:`VideoInfo`
    entries into the resolve cache. Missing or corrupt ``info.json`` files are
    skipped and counted, never fatal.

    Raises :class:`SystemExit` if ``creator_dir`` is not a usable creator
    directory (no ``index.json``).
    """
    from bilibili_downloader.core.creator import (
        creator_directory_name,
        load_creator_index,
    )
    from bilibili_downloader.tui.resolve_cache import ResolveCache

    creator_dir = creator_dir.expanduser()
    if not creator_dir.is_dir():
        raise SystemExit(f"目录不存在：{creator_dir}")

    index_path = creator_dir / "index.json"
    if not index_path.is_file():
        raise SystemExit(
            f"未在 {creator_dir} 找到 index.json，请确认传入的是 UP 主目录"
            "（含 index.json 和 [BV...] 子目录）。"
        )

    # Load the index only to sanity-check that the directory name matches
    # <name>_<mid> — a mismatch means the TUI's for_creator() path math would
    # look elsewhere, so the cache we write here would never be found. Warn, but
    # still write the cache in place (the user may have renamed the dir on
    # purpose and placed index.json alongside it).
    try:
        index = load_creator_index(index_path)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"无法读取 index.json：{exc}") from exc

    expected_name = creator_directory_name(index.name, index.mid)
    if expected_name != creator_dir.name:
        logger.warning(
            "目录名 %r 与 index.json 中的 %r 不一致；若 TUI 的输出目录是该目录的"
            "父目录，缓存可能无法被命中",
            creator_dir.name, expected_name,
        )

    cache_path = creator_dir / "index.resolvecache.json"
    cache = ResolveCache(cache_path)
    report = RebuildReport(cache_path=cache_path)

    for child in sorted(creator_dir.iterdir()):
        if not child.is_dir():
            continue
        info_json = child / _INFO_JSON
        if not info_json.is_file():
            report.skipped += 1
            continue
        try:
            payload = json.loads(info_json.read_text(encoding="utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            bvid = (data or {}).get("bvid")
            if not data or not bvid:
                report.skipped += 1
                continue
            info = _parse_video_info(bvid, data)
            # Pass the bvid directly as the source so the cache key matches
            # exactly what BatchWorker looks up (extract_bvid(bvid) == bvid).
            cache.put(bvid, info)
            report.cached += 1
        except (OSError, ValueError) as exc:
            # Corrupt JSON, unreadable file, or a malformed /view envelope —
            # skip this one video but keep going.
            report.skipped += 1
            report.errors.append(f"{child.name}: {exc}")
            logger.debug("skipped %s: %s", child.name, exc)

    cache.save()
    return report


def cli_rebuild_cache(args) -> None:
    """Handle the ``rebuild-cache`` subcommand."""
    report = rebuild_cache(Path(args.creator_dir))
    print(f"已缓存 {report.cached} 个视频的解析结果")
    if report.skipped:
        print(f"跳过 {report.skipped} 个子目录（无 info.json 或损坏）")
    print(f"缓存文件：{report.cache_path}")
    print("之后导入 index.json 并加入下载队列时将命中缓存，无需重新解析。")
