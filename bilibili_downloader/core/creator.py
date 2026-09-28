"""Creator submission indexing and reusable manifest persistence."""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

from bilibili_downloader.api.client import BilibiliAPIClient, BilibiliAPIError
from bilibili_downloader.core.models import CreatorVideoEntry, CreatorVideoIndex
from bilibili_downloader.utils.validators import sanitize_filename

_MID_PATTERN = re.compile(
    r"(?:^\s*(\d+)\s*$|space\.bilibili\.com/(\d+)|[?&]mid=(\d+))",
    re.IGNORECASE,
)


def parse_creator_mid(source: str) -> int:
    """Parse a numeric MID or a Bilibili space URL."""
    match = _MID_PATTERN.search(source.strip())
    if not match:
        raise ValueError("无法识别 UP 主 UID 或空间链接")
    mid = int(next(value for value in match.groups() if value is not None))
    if mid <= 0:
        raise ValueError("UP 主 UID 必须是正整数")
    return mid


def creator_directory_name(name: str, mid: int) -> str:
    return sanitize_filename(f"{name}_{mid}")


def save_creator_index(index: CreatorVideoIndex, path: Path) -> None:
    atomic_write_json(path, index.model_dump(mode="json"))


def load_creator_index(path: Path) -> CreatorVideoIndex:
    return CreatorVideoIndex.model_validate_json(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON using fsync and an atomic same-directory replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CreatorIndexService:
    """Fetch every public submission through Bilibili's cursor-based media list."""

    def __init__(
        self,
        api_client: BilibiliAPIClient,
        page_size: int = 20,
        request_interval: float = 10.0,
        retry_delays: tuple[float, ...] = (10.0, 30.0, 60.0),
    ):
        self._api_client = api_client
        self._page_size = page_size
        self._request_interval = max(0.0, request_interval)
        self._retry_delays = retry_delays

    def fetch(
        self,
        source: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_checker: Optional[Callable[[], bool]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        resume_index: Optional[CreatorVideoIndex] = None,
        checkpoint_callback: Optional[Callable[[CreatorVideoIndex], None]] = None,
        checkpoint_every: int = 10,
    ) -> CreatorVideoIndex:
        mid = parse_creator_mid(source)
        profile = self._api_client.get_creator_profile(mid)
        name = str((profile.get("data") or {}).get("name") or f"UID_{mid}")
        can_resume = bool(
            resume_index
            and resume_index.mid == mid
            and not resume_index.complete
            and resume_index.next_cursor
        )
        raw_pages = list(resume_index.raw_pages) if can_resume else []
        videos = list(resume_index.videos) if can_resume else []
        seen = {entry.bvid for entry in videos}
        page = len(raw_pages) + 1
        cursor = resume_index.next_cursor if can_resume else 0
        expected_total = resume_index.reported_total if can_resume else 0
        last_request_at = 0.0

        if can_resume:
            if status_callback:
                status_callback(f"从检查点继续，已有 {len(videos)} 个投稿")
            if progress_callback:
                progress_callback(len(videos), expected_total)

        while True:
            if cancel_checker and cancel_checker():
                raise RuntimeError("Index fetch cancelled by user")
            if last_request_at:
                interval = random.uniform(0.0, self._request_interval)
                wait_time = interval - (time.monotonic() - last_request_at)
                if wait_time > 0:
                    _cancellable_wait(wait_time, cancel_checker)
            try:
                payload = self._fetch_page_with_retry(
                    mid,
                    page,
                    cursor,
                    len(videos),
                    expected_total,
                    cancel_checker,
                    status_callback,
                )
            except Exception:
                if checkpoint_callback and videos and cursor:
                    checkpoint_callback(
                        _build_creator_index(
                            mid,
                            name,
                            source,
                            videos,
                            raw_pages,
                            expected_total,
                            complete=False,
                            next_cursor=cursor,
                        )
                    )
                raise
            last_request_at = time.monotonic()
            raw_pages.append(payload)
            data = payload.get("data") or {}
            expected_total = int(data.get("total_count") or expected_total or 0)
            entries = data.get("media_list") or []
            if not entries:
                break

            for raw in entries:
                bvid = str(raw.get("bvid") or raw.get("bv_id") or "")
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                videos.append(_parse_creator_entry(raw))

            if progress_callback:
                progress_callback(len(videos), expected_total)
            complete = (
                not data.get("has_more", False)
                or len(videos) >= expected_total > 0
            )
            if complete:
                break
            next_cursor = _safe_int(entries[-1].get("id"))
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError(f"UP 主索引游标未推进，停止在第 {page} 批")
            cursor = next_cursor
            if (
                checkpoint_callback
                and checkpoint_every > 0
                and (page == 1 or page % checkpoint_every == 0)
            ):
                checkpoint_callback(
                    _build_creator_index(
                        mid,
                        name,
                        source,
                        videos,
                        raw_pages,
                        expected_total,
                        complete=False,
                        next_cursor=cursor,
                    )
                )
            page += 1

        return _build_creator_index(
            mid,
            name,
            source,
            videos,
            raw_pages,
            expected_total,
            complete=True,
            next_cursor=0,
        )

    def _fetch_page_with_retry(
        self,
        mid: int,
        page: int,
        cursor: int,
        fetched: int,
        expected_total: int,
        cancel_checker: Optional[Callable[[], bool]],
        status_callback: Optional[Callable[[str], None]],
    ) -> dict:
        for attempt in range(len(self._retry_delays) + 1):
            try:
                payload = self._api_client.get_creator_medialist_page(
                    mid, cursor, self._page_size
                )
                data = payload.get("data") or {}
                entries = data.get("media_list") or []
                if not entries and expected_total > fetched:
                    raise _PrematureEmptyPageError(
                        f"expected {expected_total} submissions, received {fetched}"
                    )
                return payload
            except Exception as exc:
                if not _retryable_creator_error(exc):
                    raise
                if attempt >= len(self._retry_delays):
                    if isinstance(exc, _PrematureEmptyPageError):
                        raise RuntimeError(
                            f"UP 主索引第 {page} 页连续返回空数据；服务端报告 "
                            f"{expected_total} 个投稿，当前仅获取 {fetched} 个。"
                            "已保存检查点，请稍后继续刷新索引"
                        ) from exc
                    raise RuntimeError(
                        f"B站在索引第 {page} 页触发访问风控；已获取 {fetched} 个投稿。"
                        "请保留登录状态，稍后重新刷新索引"
                    ) from exc
                delay = self._retry_delays[attempt]
                if status_callback:
                    status_callback(
                        f"第 {page} 页触发 B站风控，{delay:g} 秒后重试"
                    )
                _cancellable_wait(delay, cancel_checker)
        raise AssertionError("unreachable")


def _parse_creator_entry(raw: dict) -> CreatorVideoEntry:
    counters = raw.get("cnt_info") or {}
    return CreatorVideoEntry(
        bvid=str(raw.get("bvid") or raw.get("bv_id") or ""),
        aid=_safe_int(raw.get("aid") or raw.get("id")),
        title=str(raw.get("title") or ""),
        description=str(
            raw.get("description") or raw.get("desc") or raw.get("intro") or ""
        ),
        cover_url=str(raw.get("pic") or raw.get("cover") or ""),
        published_at=_safe_int(
            raw.get("created") or raw.get("pubdate") or raw.get("pubtime")
        ),
        duration=_duration_seconds(raw.get("length") or raw.get("duration") or 0),
        play_count=_safe_int(raw.get("play") or counters.get("play")),
        comment_count=_safe_int(raw.get("comment") or counters.get("reply")),
    )


def _build_creator_index(
    mid: int,
    name: str,
    source: str,
    videos: list[CreatorVideoEntry],
    raw_pages: list[dict],
    reported_total: int,
    *,
    complete: bool,
    next_cursor: int,
) -> CreatorVideoIndex:
    return CreatorVideoIndex(
        mid=mid,
        name=name,
        source=source,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        total=len(videos),
        reported_total=reported_total or len(videos),
        complete=complete,
        next_cursor=next_cursor,
        videos=list(videos),
        raw_pages=list(raw_pages),
    )


def _duration_seconds(value: object) -> int:
    if isinstance(value, int):
        return value
    parts = str(value).split(":")
    if not all(part.isdigit() for part in parts):
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _retryable_creator_error(exc: Exception) -> bool:
    if isinstance(exc, _PrematureEmptyPageError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (412, 429)
    if isinstance(exc, BilibiliAPIError):
        return exc.code in (-401, -352)
    return isinstance(exc, httpx.TransportError)


class _PrematureEmptyPageError(RuntimeError):
    """The cursor API returned an empty page before its reported total."""


def _cancellable_wait(
    delay: float,
    cancel_checker: Optional[Callable[[], bool]],
) -> None:
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if cancel_checker and cancel_checker():
            raise RuntimeError("Index fetch cancelled by user")
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
