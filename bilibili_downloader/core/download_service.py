"""Shared end-to-end download and raw archive workflow."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.core.archive import (
    ArtifactPaths,
    CommentArchiveDownloader,
    download_cover,
)
from bilibili_downloader.core.creator import atomic_write_json
from bilibili_downloader.core.danmaku import DanmakuDownloader
from bilibili_downloader.core.downloader import StreamDownloader
from bilibili_downloader.core.models import DownloadItem, DownloadOutcome, SubtitleInfo
from bilibili_downloader.core.subtitle import SubtitleDownloader
from bilibili_downloader.utils.cancellation import (
    OperationCancelled,
    RequestCancellation,
)

logger = logging.getLogger(__name__)

# Sidecars of one video are written by one task at a time; different videos
# archive concurrently.
_VIDEO_LOCKS: dict[Path, list] = {}
_VIDEO_LOCKS_GUARD = threading.Lock()
# Comment archives page through a rate-limited API for minutes, so only one runs
# at a time process-wide (keeps the global comment request rate unchanged).
_COMMENT_ARCHIVE_SLOT = threading.Semaphore(1)


class DownloadService:
    """Download media and requested original/converted companion files."""

    def __init__(
        self,
        api_client: BilibiliAPIClient,
        output_dir: str,
        ffmpeg_path: Optional[str] = None,
    ):
        self._api_client = api_client
        self._output_dir = Path(output_dir)
        self._downloader = StreamDownloader(
            api_client, output_dir, ffmpeg_path=ffmpeg_path
        )
        self._cancelled = False
        self._requests = RequestCancellation()

    @property
    def transferred_bytes(self):
        return self._downloader.transferred_bytes

    @property
    def transfer_metrics(self):
        return self._downloader.speed_bps, self._downloader.eta_seconds

    def cancel(self) -> None:
        self._cancelled = True
        self._requests.cancel()
        self._downloader.cancel()

    def download(
        self,
        item: DownloadItem,
        progress_callback: Callable[[float, str], None],
        media_ready: Optional[Callable[[], None]] = None,
    ) -> DownloadOutcome:
        """Download media, then archive its companion files.

        ``media_ready`` fires once the MP4 is in place, before the (possibly
        slow) sidecar archiving, so schedulers can hand the bandwidth slot to
        the next task.
        """
        with self._requests.bind():
            return self._download(item, progress_callback, media_ready)

    def _download(
        self,
        item: DownloadItem,
        progress_callback: Callable[[float, str], None],
        media_ready: Optional[Callable[[], None]] = None,
    ) -> DownloadOutcome:
        self._cancelled = False
        paths = ArtifactPaths(self._output_dir, item)
        preparation_warnings: list[str] = []

        cover_for_merge = self._prepare_cover(item, paths, preparation_warnings)
        media_metadata = _media_metadata(item) if item.embed_metadata else None

        def video_progress(progress: float, text: str) -> None:
            self._raise_if_cancelled()
            progress_callback(0.04 + progress * 0.78, text)

        download_kwargs = {}
        if media_metadata:
            download_kwargs["media_metadata"] = media_metadata
        if item.embed_cover and cover_for_merge is not None:
            download_kwargs["cover_path"] = cover_for_merge
        video_path = self._downloader.download(item, video_progress, **download_kwargs)
        self._raise_if_cancelled()
        if media_ready is not None:
            media_ready()
        video_file = Path(video_path)
        sidecars = paths.sidecars(video_file)
        outcome = DownloadOutcome(
            video_path=video_path,
            actual_quality=(
                self._downloader.last_video_stream.id
                if self._downloader.last_video_stream else None
            ),
            actual_video_codec=(
                self._downloader.last_video_stream.codecid
                if self._downloader.last_video_stream else None
            ),
            skipped_media=self._downloader.last_download_skipped,
            warnings=preparation_warnings + self._downloader.last_warnings,
        )

        if outcome.actual_quality is not None and outcome.actual_quality != item.selected_quality.value:
            outcome.warnings.append(
                f"请求画质 {item.selected_quality.label} 不可用，已回退到代码 {outcome.actual_quality}"
            )
        if outcome.actual_video_codec is not None and outcome.actual_video_codec != item.selected_video_codec:
            outcome.warnings.append(
                f"请求的视频编码不可用，实际使用编码代码 {outcome.actual_video_codec}"
            )

        self._persist_video_sidecars(
            item,
            paths,
            sidecars,
            cover_for_merge,
            outcome,
            progress_callback,
        )
        self._raise_if_cancelled()
        status = "下载完成（有警告）" if outcome.is_partial else "下载完成"
        progress_callback(1.0, status)
        return outcome

    def _prepare_cover(
        self,
        item: DownloadItem,
        paths: ArtifactPaths,
        warnings: list[str],
    ) -> Optional[Path]:
        if not (item.download_cover or item.embed_cover) or not item.video_info.cover_url:
            return None
        if paths.media_path is not None:
            cover_path = paths.sidecars(paths.media_path)["cover"]
        else:
            cover_path = (
                self._output_dir / ".biliflow-parts" / "artifacts"
                / f"{item.video_info.bvid}.jpg"
            )
        if not _needs_write(cover_path, item.refresh_sidecars):
            return cover_path
        try:
            download_cover(item.video_info.cover_url, cover_path)
            return cover_path
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._raise_if_cancelled()
            logger.warning("Cover download failed for %s: %s", item.video_info.bvid, exc)
            warnings.append(f"封面下载失败：{exc}")
            return None

    def _persist_video_sidecars(
        self,
        item: DownloadItem,
        paths: ArtifactPaths,
        sidecars: dict[str, Path],
        cover_for_merge: Optional[Path],
        outcome: DownloadOutcome,
        progress_callback: Callable[[float, str], None],
    ) -> None:
        with self._video_lock(sidecars["metadata"], progress_callback):
            if item.download_metadata:
                self._download_metadata(item, sidecars["metadata"], outcome, progress_callback)

            if item.download_cover and cover_for_merge is not None:
                try:
                    if cover_for_merge != sidecars["cover"] and _needs_write(
                        sidecars["cover"], item.refresh_sidecars
                    ):
                        sidecars["cover"].parent.mkdir(parents=True, exist_ok=True)
                        temporary = sidecars["cover"].with_name(
                            f".{sidecars['cover'].name}.{threading.get_ident()}.tmp"
                        )
                        shutil.copy2(cover_for_merge, temporary)
                        temporary.replace(sidecars["cover"])
                    outcome.cover_path = str(sidecars["cover"])
                except Exception as exc:  # noqa: BLE001
                    outcome.warnings.append(f"封面保存失败：{exc}")

            if item.download_comments:
                self._download_comments(item, sidecars["comments"], outcome, progress_callback)

            if item.download_danmaku:
                self._download_danmaku(item, sidecars, outcome, progress_callback)

            if item.download_subtitle:
                self._download_subtitle(item, sidecars["subtitle"], outcome, progress_callback)

    def _download_metadata(self, item, path, outcome, progress_callback) -> None:
        # Only hit the API when the archive is missing, so re-running a
        # finished batch costs no metadata requests.
        if not _needs_write(path, item.refresh_sidecars):
            outcome.metadata_path = str(path)
            return
        self._raise_if_cancelled()
        progress_callback(0.84, "正在获取原始元信息...")
        try:
            raw_metadata = self._api_client.get_video_info_raw(item.video_info.bvid)
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._raise_if_cancelled()
            logger.warning("Metadata archive failed for %s: %s", item.video_info.bvid, exc)
            outcome.warnings.append(f"元信息获取失败：{exc}")
            return
        try:
            atomic_write_json(path, raw_metadata)
            outcome.metadata_path = str(path)
        except Exception as exc:  # noqa: BLE001
            outcome.warnings.append(f"元信息保存失败：{exc}")

    def _download_comments(self, item, path, outcome, progress_callback) -> None:
        if not item.refresh_sidecars and _comments_complete(path):
            outcome.comments_path = str(path)
            return
        self._raise_if_cancelled()
        progress_callback(0.86, "等待评论归档队列...")
        try:
            with self._acquire(_COMMENT_ARCHIVE_SLOT):
                progress_callback(0.86, "正在归档评论和回复...")
                CommentArchiveDownloader(self._api_client).download(
                    item.video_info.aid,
                    item.video_info.bvid,
                    path,
                    cancel_checker=lambda: self._cancelled,
                )
            outcome.comments_path = str(path)
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._raise_if_cancelled()
            logger.warning("Comment archive failed for %s: %s", item.video_info.bvid, exc)
            outcome.warnings.append(f"评论归档失败：{exc}")

    def _download_danmaku(self, item, sidecars, outcome, progress_callback) -> None:
        xml_path = sidecars["danmaku_xml"]
        ass_path = sidecars["danmaku_ass"]
        xml_ready = not _needs_write(xml_path, False)
        ass_ready = not _needs_write(ass_path, False)
        if not item.refresh_sidecars and xml_ready and ass_ready:
            outcome.danmaku_xml_path = str(xml_path)
            outcome.danmaku_path = str(ass_path)
            return
        self._raise_if_cancelled()
        progress_callback(0.92, "正在下载弹幕原文并生成 ASS...")
        try:
            if xml_ready and not item.refresh_sidecars:
                DanmakuDownloader.xml_to_ass(xml_path.read_bytes(), ass_path)
            else:
                DanmakuDownloader.download_and_convert(
                    item.video_info.cid, ass_path, xml_path
                )
            outcome.danmaku_xml_path = str(xml_path)
            outcome.danmaku_path = str(ass_path)
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._raise_if_cancelled()
            logger.warning("Danmaku download failed for %s: %s", item.video_info.bvid, exc)
            outcome.warnings.append(f"弹幕下载失败：{exc}")

    def _download_subtitle(self, item, path, outcome, progress_callback) -> None:
        if not _needs_write(path, item.refresh_sidecars):
            outcome.subtitle_paths.append(str(path))
            return
        self._raise_if_cancelled()
        progress_callback(0.96, "正在下载字幕...")
        subtitle = self._select_subtitle(item, outcome)
        if subtitle is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            SubtitleDownloader.download_and_convert(subtitle.url, path)
            outcome.subtitle_paths.append(str(path))
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._raise_if_cancelled()
            logger.warning("Subtitle download failed for %s: %s", item.video_info.bvid, exc)
            outcome.warnings.append(f"字幕下载失败：{exc}")

    def _select_subtitle(
        self, item: DownloadItem, outcome: DownloadOutcome
    ) -> Optional[SubtitleInfo]:
        try:
            tracks = self._api_client.get_subtitle_tracks(
                item.video_info.bvid, item.video_info.cid
            )
        except Exception as exc:  # noqa: BLE001
            tracks = item.video_info.subtitle_list
            if not tracks:
                outcome.warnings.append(f"字幕轨道查询失败：{exc}")
                return None
        if not tracks:
            outcome.warnings.append("该分 P 没有可下载的字幕")
            return None
        for track in tracks:
            if track.lan == item.selected_subtitle_lan:
                return track
        selected = tracks[0]
        if item.selected_subtitle_lan:
            outcome.warnings.append(
                f"未找到 {item.selected_subtitle_lan} 字幕，已使用 {selected.lan_doc or selected.lan}"
            )
        return selected

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise OperationCancelled("Download cancelled by user")

    @contextmanager
    def _acquire(self, lock):
        """Wait for ``lock`` while staying responsive to cancellation."""
        while not lock.acquire(timeout=0.1):
            self._raise_if_cancelled()
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def _video_lock(self, sidecar: Path, progress_callback):
        key = sidecar.resolve()
        with _VIDEO_LOCKS_GUARD:
            entry = _VIDEO_LOCKS.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            if entry[0].locked():
                progress_callback(0.84, "同一视频的归档文件正在写入，等待中...")
            with self._acquire(entry[0]):
                yield
        finally:
            with _VIDEO_LOCKS_GUARD:
                entry[1] -= 1
                if entry[1] == 0:
                    _VIDEO_LOCKS.pop(key, None)


def _needs_write(path: Path, refresh: bool) -> bool:
    return refresh or not path.is_file() or path.stat().st_size == 0


def _comments_complete(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("complete") is True
    except (OSError, ValueError, AttributeError):
        return False


def _media_metadata(item: DownloadItem) -> dict[str, str]:
    info = item.video_info
    published = ""
    if info.pubdate:
        published = datetime.fromtimestamp(info.pubdate, timezone.utc).date().isoformat()
    return {
        "title": info.title,
        "artist": info.owner_name or info.author,
        "comment": info.desc,
        "date": published,
        "purl": f"https://www.bilibili.com/video/{info.bvid}",
    }
