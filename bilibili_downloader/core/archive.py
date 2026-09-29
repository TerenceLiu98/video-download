"""Raw sidecar collection and deterministic archive paths."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import httpx
from PIL import Image

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.api.endpoints import USER_AGENT
from bilibili_downloader.core.creator import atomic_write_json, creator_directory_name
from bilibili_downloader.core.models import DownloadItem
from bilibili_downloader.utils.network import BILIBILI_RESOURCE_HOSTS, trusted_media_url
from bilibili_downloader.utils.validators import sanitize_filename

MAX_COVER_BYTES = 20 * 1024 * 1024


class ArtifactPaths:
    """Resolve stable creator archive paths and standalone sidecar paths."""

    def __init__(self, output_dir: Path, item: DownloadItem):
        self.output_dir = output_dir
        self.item = item

    @property
    def is_creator_archive(self) -> bool:
        return bool(self.item.creator_mid and self.item.creator_name)

    @property
    def video_dir(self) -> Path:
        info = self.item.video_info
        root = self.output_dir / creator_directory_name(
            self.item.creator_name, self.item.creator_mid
        )
        if root.is_dir():
            existing = next(
                (
                    path
                    for path in root.iterdir()
                    if path.is_dir() and path.name.startswith(f"[{info.bvid}] ")
                ),
                None,
            )
            if existing is not None:
                return existing
        return root / sanitize_filename(f"[{info.bvid}] {info.title}")

    @property
    def page_number(self) -> int:
        for page in self.item.video_info.pages:
            if page.cid == self.item.video_info.cid:
                return page.page
        return 1

    @property
    def page_part(self) -> str:
        for page in self.item.video_info.pages:
            if page.cid == self.item.video_info.cid:
                return page.part
        return self.item.video_info.title

    @property
    def media_path(self) -> Optional[Path]:
        if not self.is_creator_archive:
            return None
        if self.video_dir.is_dir():
            prefix = f"P{self.page_number:02d} [{self.item.video_info.cid}] "
            existing = next(
                (
                    path
                    for path in self.video_dir.iterdir()
                    if path.is_file()
                    and path.suffix.lower() == ".mp4"
                    and path.name.startswith(prefix)
                ),
                None,
            )
            if existing is not None:
                return existing
        name = sanitize_filename(
            f"P{self.page_number:02d} [{self.item.video_info.cid}] {self.page_part}"
        )
        return self.video_dir / f"{name}.mp4"

    def sidecars(self, video_path: Path) -> dict[str, Path]:
        if self.is_creator_archive:
            return {
                "metadata": self.video_dir / "info.json",
                "comments": self.video_dir / "comments.json",
                "cover": self.video_dir / "cover.jpg",
                "danmaku_xml": _with_artifact_suffix(video_path, ".danmaku.xml"),
                "danmaku_ass": _with_artifact_suffix(video_path, ".danmaku.ass"),
                "subtitle": _with_artifact_suffix(
                    video_path,
                    f".{sanitize_filename(self.item.selected_subtitle_lan)}.srt",
                ),
            }
        return {
            "metadata": _with_artifact_suffix(video_path, ".info.json"),
            "comments": _with_artifact_suffix(video_path, ".comments.json"),
            "cover": _with_artifact_suffix(video_path, ".cover.jpg"),
            "danmaku_xml": _with_artifact_suffix(video_path, ".danmaku.xml"),
            "danmaku_ass": _with_artifact_suffix(video_path, ".danmaku.ass"),
            "subtitle": _with_artifact_suffix(video_path, ".srt"),
        }


class CommentArchiveDownloader:
    """Collect all top-level comments and all nested reply pages."""

    def __init__(
        self,
        api_client: BilibiliAPIClient,
        request_interval: float = 0.5,
        page_size: int = 20,
        max_retries: int = 5,
    ):
        self._api_client = api_client
        self._request_interval = max(0.0, request_interval)
        self._page_size = page_size
        self._max_retries = max(1, max_retries)
        self._last_request_at = 0.0

    def download(
        self,
        aid: int,
        bvid: str,
        output_path: Path,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> None:
        archive = {
            "schema_version": 1,
            "aid": aid,
            "bvid": bvid,
            "complete": False,
            "root_pages": [],
            "reply_pages": {},
        }
        page = 1
        archived_roots: set[int] = set()
        while True:
            self._check_cancel(cancel_checker)
            payload = self._request(
                lambda: self._api_client.get_comment_page(aid, page, self._page_size),
                cancel_checker,
            )
            archive["root_pages"].append(payload)
            data = payload.get("data") or {}
            replies = data.get("replies") or []
            atomic_write_json(output_path, archive)
            for root in _root_comments(data):
                root_id = int(root.get("rpid") or root.get("root") or 0)
                reply_count = int(root.get("rcount") or 0)
                if root_id and reply_count and root_id not in archived_roots:
                    archived_roots.add(root_id)
                    archive["reply_pages"][str(root_id)] = self._download_replies(
                        aid, root_id, cancel_checker
                    )
                    atomic_write_json(output_path, archive)

            if not replies:
                break

            page_info = data.get("page") or {}
            total = int(page_info.get("count") or 0)
            if page * self._page_size >= total or len(replies) < self._page_size:
                break
            page += 1

        archive["complete"] = True
        atomic_write_json(output_path, archive)

    def _download_replies(
        self,
        aid: int,
        root: int,
        cancel_checker: Optional[Callable[[], bool]],
    ) -> list[dict]:
        pages = []
        page = 1
        while True:
            self._check_cancel(cancel_checker)
            payload = self._request(
                lambda: self._api_client.get_comment_replies(
                    aid, root, page, self._page_size
                ),
                cancel_checker,
            )
            pages.append(payload)
            data = payload.get("data") or {}
            replies = data.get("replies") or []
            page_info = data.get("page") or {}
            total = int(page_info.get("count") or 0)
            if not replies or page * self._page_size >= total or len(replies) < self._page_size:
                break
            page += 1
        return pages

    def _request(self, action, cancel_checker):
        last_error = None
        for attempt in range(self._max_retries):
            self._check_cancel(cancel_checker)
            self._pace()
            try:
                return action()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < self._max_retries:
                    time.sleep(min(8.0, 2 ** attempt))
        raise RuntimeError(f"评论 API 连续失败 {self._max_retries} 次：{last_error}")

    def _pace(self) -> None:
        remaining = self._request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _check_cancel(cancel_checker: Optional[Callable[[], bool]]) -> None:
        if cancel_checker and cancel_checker():
            raise RuntimeError("Download cancelled by user")


def _with_artifact_suffix(video_path: Path, suffix: str) -> Path:
    return video_path.with_name(f"{video_path.stem}{suffix}")


def _root_comments(data: dict) -> list[dict]:
    roots = list(data.get("replies") or [])

    def collect(value):
        if isinstance(value, dict):
            if value.get("rpid") and int(value.get("root") or 0) == 0:
                roots.append(value)
                return
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(data.get("top") or {})
    collect(data.get("upper") or {})
    seen = set()
    unique = []
    for root in roots:
        rpid = int(root.get("rpid") or 0)
        if rpid and rpid not in seen:
            seen.add(rpid)
            unique.append(root)
    return unique


def download_cover(url: str, output_path: Path) -> None:
    """Download a trusted Bilibili cover and normalize it to JPEG atomically."""
    from bilibili_downloader.utils.cancellation import attach_current_request

    current_url = trusted_media_url(url, BILIBILI_RESOURCE_HOSTS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = bytearray()
    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
        timeout=httpx.Timeout(30.0, connect=5.0),
        event_hooks={"request": [attach_current_request]},
        follow_redirects=False,
    ) as client:
        for _ in range(8):
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current_url = trusted_media_url(
                        urljoin(current_url, location), BILIBILI_RESOURCE_HOSTS
                    )
                    continue
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_COVER_BYTES:
                        raise RuntimeError("封面文件过大")
                break
        else:
            raise RuntimeError("封面地址重定向次数过多")

    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".jpg",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        from io import BytesIO

        with Image.open(BytesIO(content)) as image:
            image.convert("RGB").save(temporary, "JPEG", quality=95)
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
