"""DASH stream downloader with FFmpeg merge support."""

import hashlib
import json
import logging
import shutil
import ssl
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import httpx

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.api.endpoints import USER_AGENT
from bilibili_downloader.core.archive import ArtifactPaths
from bilibili_downloader.core.completed import find_completed, record_completed
from bilibili_downloader.core.errors import TransientDownloadError, is_transient_error
from bilibili_downloader.core.ffmpeg import FFmpegManager
from bilibili_downloader.core.models import DownloadItem, StreamInfo, VideoQuality
from bilibili_downloader.utils.cancellation import (
    OperationCancelled,
    RequestCancellation,
    cancellable_sleep,
)
from bilibili_downloader.utils.network import (
    BILIBILI_RESOURCE_HOSTS,
    trusted_media_url,
)

logger = logging.getLogger(__name__)

# Standard headers for stream download
STREAM_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com/",
}

CHUNK_SIZE = 8192
MAX_RETRIES = 5
# Bilibili's CDN throttles each connection (~1 MiB/s measured), so large
# streams are split into ranges fetched in parallel.
SEGMENT_CONNECTIONS = 4
SEGMENT_MIN_SIZE = 8 * 1024 * 1024
# Signed stream URLs expire; re-sign at most this many times per stream.
MAX_URL_REFRESHES = 2

# Create an SSL context that works around CDN issues
SSL_CONTEXT = ssl.create_default_context()
_OUTPUT_PATH_LOCK = threading.Lock()
_RESERVED_OUTPUT_PATHS: set[Path] = set()
_CACHE_PATH_LOCK = threading.Lock()
_RESERVED_CACHE_PATHS: set[Path] = set()


class StreamDownloader:
    """Downloads DASH video/audio streams and merges with FFmpeg."""

    def __init__(
        self,
        api_client: BilibiliAPIClient,
        output_dir: str,
        max_retries: int = MAX_RETRIES,
        ffmpeg_path: Optional[str] = None,
        segments: int = SEGMENT_CONNECTIONS,
    ):
        self._api_client = api_client
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._max_retries = max(1, max_retries)
        self._ffmpeg_path = ffmpeg_path
        self._segments = max(1, segments)
        self._cancelled = False
        self._requests = RequestCancellation()
        self._metrics_lock = threading.Lock()
        self.speed_bps = 0.0
        self.transferred_bytes = 0
        self.eta_seconds = None
        self.last_video_stream: Optional[StreamInfo] = None
        self.last_audio_stream: Optional[StreamInfo] = None
        self.last_download_skipped = False
        self.last_warnings: list[str] = []

    def cancel(self):
        """Signal the downloader to cancel current operation."""
        self._cancelled = True
        self._requests.cancel()

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise OperationCancelled("Download cancelled by user")

    def download(
        self,
        item: DownloadItem,
        progress_callback: Callable[[float, str], None],
        media_metadata: Optional[dict[str, str]] = None,
        cover_path: Optional[Path] = None,
    ) -> str:
        """Download a video to disk.

        Flow:
            1. Fetch playurl for fresh stream URLs
            2. Download video .m4s
            3. Download audio .m4s
            4. Merge with FFmpeg
            5. Clean up temp files

        Args:
            item: DownloadItem with video info and quality selection.
            progress_callback: Called with (progress 0-1, status_text).

        Returns:
            Path to the merged output file.
        """
        self._cancelled = False
        self.last_download_skipped = False
        self.last_warnings = []
        self.last_video_stream = None
        self.last_audio_stream = None

        stable_output = ArtifactPaths(self._output_dir, item).media_path
        if stable_output is not None:
            existing = (
                stable_output
                if stable_output.is_file() and stable_output.stat().st_size > 0
                else None
            )
        else:
            existing = find_completed(self._output_dir, item)
        if existing is not None:
            self.last_download_skipped = True
            progress_callback(1.0, "视频已存在，正在补齐归档文件...")
            return str(existing)

        progress_callback(0.0, "正在获取流地址...")
        playurl_data = self._fetch_play_url(item)

        # Select video stream
        video_stream = self._select_video_stream(
            playurl_data["video_streams"],
            item.selected_quality,
            item.selected_video_codec,
        )
        if not video_stream:
            raise RuntimeError("No matching video stream found")
        self.last_video_stream = video_stream

        # Select audio stream
        audio_stream = self._select_audio_stream(
            playurl_data["audio_streams"],
            item.selected_audio_quality,
        )
        if not audio_stream:
            raise RuntimeError("No matching audio stream found")
        self.last_audio_stream = audio_stream

        requested_output = stable_output or (self._output_dir / item.filename)
        requested_output.parent.mkdir(parents=True, exist_ok=True)
        output_context = (
            _fixed_output_path(requested_output)
            if stable_output is not None
            else _reserved_output_path(requested_output)
        )
        with _reserved_download_cache(self._output_dir, item) as tmp, output_context as output_path:
            # The cache key is the *requested* spec; the streams actually served
            # can differ (quality fallback, login change), so never resume parts
            # that belong to another stream.
            _discard_mismatched_parts(tmp, video_stream, audio_stream)
            video_path = tmp / "video.m4s"
            audio_path = tmp / "audio.m4s"

            with self._media_client() as client:
                # Download video stream
                progress_callback(0.0, "正在下载视频流...")
                self._download_stream(
                    video_stream,
                    video_path,
                    lambda p: progress_callback(p * 0.6, "正在下载视频流..."),
                    client=client,
                    refresh=lambda: self._refresh_stream(item, video_stream, "video_streams"),
                )
                self._raise_if_cancelled()

                # Download audio stream
                progress_callback(0.6, "正在下载音频流...")
                self._download_stream(
                    audio_stream,
                    audio_path,
                    lambda p: progress_callback(0.6 + p * 0.2, "正在下载音频流..."),
                    client=client,
                    refresh=lambda: self._refresh_stream(item, audio_stream, "audio_streams"),
                )
                self._raise_if_cancelled()

            # Merge with FFmpeg
            self.speed_bps = 0.0
            self.eta_seconds = None
            progress_callback(0.82, "正在合并视频...")
            merged_path = tmp / "merged.mp4"
            merge_kwargs = {}
            if media_metadata:
                merge_kwargs["metadata"] = media_metadata
            if cover_path is not None:
                merge_kwargs["cover_path"] = cover_path
            success, msg = FFmpegManager.merge_streams(
                video_path,
                audio_path,
                merged_path,
                custom_path=self._ffmpeg_path,
                cancel_checker=lambda: self._cancelled,
                **merge_kwargs,
            )
            self._raise_if_cancelled()
            if not success and cover_path is not None:
                self.last_warnings.append(f"封面嵌入失败，已保留独立封面：{msg}")
                merge_kwargs.pop("cover_path", None)
                success, msg = FFmpegManager.merge_streams(
                    video_path,
                    audio_path,
                    merged_path,
                    custom_path=self._ffmpeg_path,
                    cancel_checker=lambda: self._cancelled,
                    **merge_kwargs,
                )
                self._raise_if_cancelled()
            if not success:
                raise RuntimeError(f"FFmpeg merge failed: {msg}")

            merged_path.replace(output_path)
            if stable_output is None:
                record_completed(self._output_dir, item, output_path)
            shutil.rmtree(tmp, ignore_errors=True)
            _remove_empty_cache_root(tmp.parent)

            progress_callback(1.0, "视频下载完成")
            return str(output_path)

    def _fetch_play_url(self, item: DownloadItem) -> dict:
        info = item.video_info
        return self._api_client.get_play_url(
            bvid=info.bvid,
            cid=info.cid,
            quality=item.selected_quality,
            need_hdr=item.selected_quality in (VideoQuality.QHDR,),
            need_dolby=item.selected_quality in (VideoQuality.Q_DOLBY,),
            preferred_codec=item.selected_video_codec,
        )

    def _refresh_stream(self, item: DownloadItem, current: StreamInfo, key: str) -> StreamInfo:
        """Fetch fresh signed URLs for exactly the stream being downloaded."""
        self._raise_if_cancelled()
        streams = self._fetch_play_url(item)[key]
        for stream in streams:
            if stream.id == current.id and stream.codecid == current.codecid:
                return stream
        raise RuntimeError("流地址已过期，重新获取后找不到相同规格的流")

    def _media_client(self) -> httpx.Client:
        """One pooled client per download, shared by both streams and segments."""
        transport = httpx.HTTPTransport(
            http2=False,
            retries=1,
            verify=SSL_CONTEXT,
        )
        return httpx.Client(
            transport=transport,
            event_hooks={"request": [self._requests.attach]},
        )

    def _select_video_stream(
        self,
        streams: list[StreamInfo],
        quality: VideoQuality,
        preferred_codec: int = 12,  # HEVC
    ) -> Optional[StreamInfo]:
        """Find best matching video stream.

        Falls back to best available quality if requested quality not available.
        """
        # Try exact quality match first
        candidates = [s for s in streams if s.id == quality.value]
        if not candidates:
            # Fallback: group by quality, prefer requested codec within each group
            if not streams:
                return None
            # Find the closest available quality (prefer higher)
            available_ids = sorted({s.id for s in streams}, reverse=True)
            closest = None
            for qid in available_ids:
                if qid <= quality.value:
                    closest = qid
                    break
            if closest is None:
                closest = available_ids[-1]  # Fallback to lowest available
            candidates = [s for s in streams if s.id == closest]

        # Prefer requested codec
        for s in candidates:
            if s.codecid == preferred_codec:
                return s

        # Return highest bandwidth
        return max(candidates, key=lambda s: s.bandwidth)

    def _select_audio_stream(
        self,
        streams: list[StreamInfo],
        preferred_id: int = 30280,
    ) -> Optional[StreamInfo]:
        """Find best matching audio stream."""
        for s in streams:
            if s.id == preferred_id:
                return s
        # Fallback to highest quality audio
        if streams:
            return max(streams, key=lambda s: s.bandwidth)
        return None

    def _download_stream(
        self,
        stream: StreamInfo,
        dest: Path,
        progress_callback: Callable[[float], None],
        client: Optional[httpx.Client] = None,
        refresh: Optional[Callable[[], StreamInfo]] = None,
    ) -> None:
        """Download a single .m4s stream with progress tracking and retry.

        ``refresh`` re-signs the stream URLs when every mirror rejects the
        current ones as expired (403/404/410); it does not consume a retry.
        """
        self.speed_bps = 0.0
        self.eta_seconds = None
        complete_marker = dest.with_suffix(f"{dest.suffix}.complete")
        if _has_valid_completion_marker(dest, complete_marker):
            progress_callback(1.0)
            return
        urls = _stream_urls(stream)
        if not urls:
            raise RuntimeError("下载失败：流地址为空")

        if client is None:
            with self._media_client() as own_client:
                self._download_stream(stream, dest, progress_callback, own_client, refresh)
            return

        last_error = None
        refreshes = 0
        attempt = 0
        while attempt < self._max_retries:
            self._raise_if_cancelled()
            expired = True
            for url in urls:
                try:
                    self._download_url(client, url, dest, progress_callback)
                    try:
                        complete_marker.write_text(
                            str(dest.stat().st_size), encoding="ascii"
                        )
                    except OSError:
                        # The media is complete even if the optional resume marker
                        # cannot be persisted (for example on a read-only mount).
                        logger.debug(
                            "Unable to write completion marker %s",
                            complete_marker,
                            exc_info=True,
                        )
                    return
                except (
                    httpx.HTTPError,
                    ConnectionError,
                    OSError,
                    ValueError,
                ) as e:
                    if self._cancelled:
                        raise OperationCancelled("Download cancelled") from e
                    last_error = e
                    expired = expired and _is_expired_url_error(e)
                    logger.debug(
                        "Download attempt %d failed for %s: %s",
                        attempt + 1, url, e,
                    )

            if expired and refresh is not None and refreshes < MAX_URL_REFRESHES:
                refreshes += 1
                logger.info("Stream URLs expired; requesting fresh signed URLs")
                urls = _stream_urls(refresh()) or urls
                continue
            attempt += 1
            if attempt < self._max_retries:
                delay = _retry_delay(attempt - 1, last_error)
                self._wait_for_retry(delay)

        error_type = TransientDownloadError if is_transient_error(last_error) else RuntimeError
        raise error_type(f"下载失败：{last_error}") from last_error

    @contextmanager
    def _open_media(self, client: httpx.Client, url: str, headers: dict):
        """Open a media response, following only redirects to trusted CDN hosts."""
        current_url = trusted_media_url(url, BILIBILI_RESOURCE_HOSTS)
        for _ in range(8):
            with client.stream(
                "GET",
                current_url,
                headers=headers,
                timeout=httpx.Timeout(120.0, connect=5.0),
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current_url = trusted_media_url(
                        urljoin(current_url, location),
                        BILIBILI_RESOURCE_HOSTS,
                    )
                    continue
                yield response
                return
        raise RuntimeError("下载失败：媒体地址重定向次数过多")

    def _download_url(
        self,
        client: httpx.Client,
        url: str,
        dest: Path,
        progress_callback: Callable[[float], None],
    ) -> None:
        """Download one URL, resuming from existing partial data when possible.

        Large files on servers that honour ranges are fetched over several
        connections, because the CDN throttles each connection individually.
        """
        plan = _load_segment_plan(dest)
        if plan is None and self._segments > 1 and not dest.exists():
            total = self._probe_range_support(client, url)
            if total is not None and total >= SEGMENT_MIN_SIZE:
                plan = _new_segment_plan(dest, total, self._segments)
        if plan is not None:
            self._download_segments(client, url, dest, plan, progress_callback)
            return

        resume_from = dest.stat().st_size if dest.exists() else 0
        headers = dict(STREAM_HEADERS)
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
        with self._open_media(client, url, headers) as response:
            self._write_response(response, dest, resume_from, progress_callback)

    def _probe_range_support(self, client: httpx.Client, url: str) -> Optional[int]:
        """Return the media size when the server serves byte ranges, else None."""
        headers = dict(STREAM_HEADERS, Range="bytes=0-0")
        with self._open_media(client, url, headers) as response:
            if response.status_code != 206 or _content_range_start(response) != 0:
                return None
            total = _content_range_total(response)
            return total if total > 0 else None

    def _write_response(
        self,
        response: httpx.Response,
        dest: Path,
        resume_from: int,
        progress_callback: Callable[[float], None],
    ) -> None:
        """Validate and persist a terminal media response."""
        if response.status_code == 416 and _range_already_complete(response, dest):
            progress_callback(1.0)
            return
        response.raise_for_status()

        append = resume_from > 0 and response.status_code == 206
        if response.status_code == 206:
            range_start = _content_range_start(response)
            if range_start != resume_from:
                raise httpx.ProtocolError(
                    "服务器返回的续传起点与本地文件大小不一致",
                    request=response.request,
                )
        downloaded = resume_from if append else 0
        total = _response_total_size(response, downloaded)
        _check_expected_total(dest, total, append, response.request)
        mode = "ab" if append else "wb"

        last_reported = -1.0
        sampled_at = time.monotonic()
        sampled_bytes = downloaded
        self.speed_bps = 0.0
        self.eta_seconds = None
        with open(dest, mode) as f:
            for chunk in response.iter_bytes(chunk_size=65536):
                self._raise_if_cancelled()
                f.write(chunk)
                downloaded += len(chunk)
                self.transferred_bytes += len(chunk)
                now = time.monotonic()
                elapsed = now - sampled_at
                if elapsed > 0 and (elapsed >= 0.5 or (total > 0 and downloaded >= total)):
                    self.speed_bps = (downloaded - sampled_bytes) / elapsed
                    self.eta_seconds = (
                        max(0, total - downloaded) / self.speed_bps
                        if total > 0 and self.speed_bps > 0 else None
                    )
                if total > 0:
                    pct = min(downloaded / total, 1.0)
                    if elapsed >= 0.5 or pct - last_reported >= 0.05 or pct >= 1.0:
                        progress_callback(pct)
                        last_reported = pct
                elif elapsed >= 0.5:
                    progress_callback(0.0)
                if elapsed >= 0.5:
                    sampled_at, sampled_bytes = now, downloaded

        if total > 0 and downloaded != total:
            raise httpx.ProtocolError("媒体流不完整，保留分片等待续传", request=response.request)
        progress_callback(1.0)

    def _download_segments(
        self,
        client: httpx.Client,
        url: str,
        dest: Path,
        plan: dict,
        progress_callback: Callable[[float], None],
    ) -> None:
        """Fetch every unfinished byte range in parallel, then join the parts."""
        total = plan["total"]
        ranges = plan["ranges"]
        parts = [_segment_part(dest, index) for index in range(len(ranges))]
        done = [_segment_done(part, start, end) for part, (start, end) in zip(parts, ranges)]
        progress = {"bytes": sum(done)}
        stop = threading.Event()

        pending = [index for index, (start, end) in enumerate(ranges)
                   if done[index] < end - start + 1]
        sampled_at = time.monotonic()
        sampled_bytes = progress["bytes"]
        self.speed_bps = 0.0
        self.eta_seconds = None
        if pending:
            pool = ThreadPoolExecutor(max_workers=len(pending), thread_name_prefix="media-segment")
            futures = [
                pool.submit(
                    self._fetch_segment, client, url, parts[index],
                    ranges[index][0] + done[index], ranges[index][1], total, dest, progress, stop,
                )
                for index in pending
            ]
            try:
                while True:
                    finished, running = wait(futures, timeout=0.25, return_when=FIRST_EXCEPTION)
                    failed = next((f for f in finished if f.exception() is not None), None)
                    if failed is not None:
                        raise failed.exception()
                    self._raise_if_cancelled()
                    now = time.monotonic()
                    with self._metrics_lock:
                        current = progress["bytes"]
                    if now - sampled_at >= 0.5:
                        self.speed_bps = (current - sampled_bytes) / (now - sampled_at)
                        self.eta_seconds = (
                            max(0, total - current) / self.speed_bps if self.speed_bps > 0 else None
                        )
                        sampled_at, sampled_bytes = now, current
                    progress_callback(min(current / total, 1.0))
                    if not running:
                        break
            finally:
                stop.set()
                pool.shutdown(wait=True)

        for part, (start, end) in zip(parts, ranges):
            if _segment_done(part, start, end) != end - start + 1:
                raise httpx.ProtocolError("分段数据不完整，保留分段等待续传", request=None)
        # Grow the first part in place so the join needs at most one segment of
        # extra disk space. The plan survives until the end, so a crash here only
        # re-fetches the parts already consumed.
        parts[0].replace(dest)
        with open(dest, "ab") as output:
            for part in parts[1:]:
                with open(part, "rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                part.unlink()
        if dest.stat().st_size != total:
            dest.unlink(missing_ok=True)
            raise httpx.ProtocolError("分段合并后的大小不正确", request=None)
        _segment_plan_path(dest).unlink(missing_ok=True)
        progress_callback(1.0)

    def _fetch_segment(
        self,
        client: httpx.Client,
        url: str,
        part: Path,
        start: int,
        end: int,
        total: int,
        dest: Path,
        progress: dict,
        stop: threading.Event,
    ) -> None:
        headers = dict(STREAM_HEADERS, Range=f"bytes={start}-{end}")
        with self._open_media(client, url, headers) as response:
            response.raise_for_status()
            if (response.status_code != 206 or _content_range_start(response) != start
                    or _content_range_total(response) != total):
                # The server stopped honouring ranges or the file changed; drop
                # the plan so the next attempt starts over on one connection.
                _discard_segments(dest)
                raise httpx.ProtocolError(
                    "服务器分段响应与本地分段不一致，已丢弃分段重新下载",
                    request=response.request,
                )
            written = start
            with open(part, "ab") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    if self._cancelled or stop.is_set():
                        raise OperationCancelled("Download cancelled")
                    f.write(chunk[: end + 1 - written])
                    count = min(len(chunk), end + 1 - written)
                    written += count
                    with self._metrics_lock:
                        progress["bytes"] += count
                        self.transferred_bytes += count
                    if written > end:
                        return
        if written <= end:
            raise httpx.ProtocolError("媒体分段不完整，保留分段等待续传", request=None)

    def _wait_for_retry(self, delay: float) -> None:
        """Wait between retries while remaining responsive to cancellation."""
        cancellable_sleep(delay, lambda: self._cancelled, "Download cancelled")


def _stream_urls(stream: StreamInfo) -> list[str]:
    """Return base and backup URLs without duplicates."""
    urls = []
    for url in [stream.base_url, *stream.backup_url]:
        if url and url not in urls:
            urls.append(url)
    return urls


def _response_total_size(response: httpx.Response, downloaded: int) -> int:
    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)

    try:
        content_length = int(response.headers.get("content-length", 0))
    except (TypeError, ValueError):
        content_length = 0
    if response.status_code == 206:
        return downloaded + content_length
    return content_length


def _content_range_start(response: httpx.Response) -> Optional[int]:
    """Parse the first byte offset from a 206 Content-Range header."""
    value = response.headers.get("content-range", "")
    unit, separator, byte_range = value.partition(" ")
    start, dash, _remainder = byte_range.partition("-")
    if unit.lower() != "bytes" or not separator or not dash or not start.isdigit():
        return None
    return int(start)


def _retry_delay(attempt: int, error: Optional[BaseException]) -> float:
    error_msg = str(error or "")
    if "SSL" in error_msg or "UNEXPECTED_EOF" in error_msg:
        return 3 * (attempt + 1)
    return 2 ** attempt


def _content_range_total(response: httpx.Response) -> int:
    total = response.headers.get("content-range", "").rsplit("/", 1)[-1]
    return int(total) if total.isdigit() else 0


def _is_expired_url_error(error: BaseException) -> bool:
    return (isinstance(error, httpx.HTTPStatusError)
            and error.response.status_code in (403, 404, 410))


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _total_marker(dest: Path) -> Path:
    return dest.with_name(f"{dest.name}.total")


def _check_expected_total(dest: Path, total: int, append: bool, request) -> None:
    """Refuse to append to a partial file that belongs to a different file size."""
    if total <= 0:
        return
    marker = _total_marker(dest)
    expected = _read_int(marker)
    if append and expected is not None and expected != total:
        dest.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        raise httpx.ProtocolError(
            "服务器文件大小与本地分片不一致，已丢弃分片重新下载", request=request
        )
    if expected != total:
        try:
            marker.write_text(str(total), encoding="ascii")
        except OSError:
            logger.debug("Unable to write size marker %s", marker, exc_info=True)


def _stream_identity(stream: StreamInfo) -> list[int]:
    return [stream.id, stream.codecid]


def _discard_mismatched_parts(tmp: Path, video: StreamInfo, audio: StreamInfo) -> None:
    """Drop cached parts unless they were fetched for exactly these streams."""
    manifest = tmp / "streams.json"
    expected = {"version": 1, "video": _stream_identity(video), "audio": _stream_identity(audio)}
    try:
        current = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if current == expected:
        return
    for child in tmp.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    manifest.write_text(json.dumps(expected), encoding="utf-8")


def _segment_plan_path(dest: Path) -> Path:
    return dest.with_name(f"{dest.name}.segments.json")


def _segment_part(dest: Path, index: int) -> Path:
    return dest.with_name(f"{dest.name}.part{index}")


def _discard_segments(dest: Path) -> None:
    for path in [_segment_plan_path(dest), *dest.parent.glob(f"{dest.name}.part*")]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows cannot delete a part another segment still has open; a
            # new plan clears it once every segment has stopped.
            logger.debug("Unable to remove segment file %s", path, exc_info=True)


def _new_segment_plan(dest: Path, total: int, segments: int) -> dict:
    _discard_segments(dest)
    size = -(-total // segments)
    ranges = [[start, min(total, start + size) - 1] for start in range(0, total, size)]
    plan = {"version": 1, "total": total, "ranges": ranges}
    _segment_plan_path(dest).write_text(json.dumps(plan), encoding="utf-8")
    return plan


def _load_segment_plan(dest: Path) -> Optional[dict]:
    path = _segment_plan_path(dest)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        total, ranges = int(plan["total"]), [[int(s), int(e)] for s, e in plan["ranges"]]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        _discard_segments(dest)
        return None
    contiguous = all(prev[1] + 1 == cur[0] for prev, cur in zip(ranges, ranges[1:]))
    if not ranges or ranges[0][0] != 0 or ranges[-1][1] != total - 1 or not contiguous:
        _discard_segments(dest)
        return None
    return {"version": 1, "total": total, "ranges": ranges}


def _segment_done(part: Path, start: int, end: int) -> int:
    """Bytes already fetched for a range; oversized parts are corrupt and reset."""
    try:
        size = part.stat().st_size
    except FileNotFoundError:
        return 0
    if size > end - start + 1:
        part.unlink(missing_ok=True)
        return 0
    return size


def _range_already_complete(response: httpx.Response, dest: Path) -> bool:
    """Treat a 416 response as success when the partial file is already complete."""
    content_range = response.headers.get("content-range", "")
    if "/" not in content_range or not dest.is_file():
        return False
    total = content_range.rsplit("/", 1)[-1]
    return total.isdigit() and dest.stat().st_size == int(total)


def _has_valid_completion_marker(dest: Path, marker: Path) -> bool:
    if not dest.is_file() or not marker.is_file():
        return False
    try:
        expected_size = int(marker.read_text(encoding="ascii").strip())
        return expected_size > 0 and dest.stat().st_size == expected_size
    except (OSError, ValueError):
        return False


@contextmanager
def _reserved_output_path(requested: Path):
    """Reserve a non-existing output path across concurrent downloads."""
    requested = requested.resolve()
    with _OUTPUT_PATH_LOCK:
        candidate = requested
        counter = 2
        while candidate.exists() or candidate in _RESERVED_OUTPUT_PATHS:
            candidate = requested.with_name(
                f"{requested.stem} ({counter}){requested.suffix}"
            )
            counter += 1
        _RESERVED_OUTPUT_PATHS.add(candidate)
    try:
        yield candidate
    finally:
        with _OUTPUT_PATH_LOCK:
            _RESERVED_OUTPUT_PATHS.discard(candidate)


@contextmanager
def _fixed_output_path(requested: Path):
    """Reserve the exact stable archive path without collision renaming."""
    requested = requested.resolve()
    with _OUTPUT_PATH_LOCK:
        if requested in _RESERVED_OUTPUT_PATHS:
            raise RuntimeError(f"同一视频分 P 已在下载：{requested.name}")
        _RESERVED_OUTPUT_PATHS.add(requested)
    try:
        yield requested
    finally:
        with _OUTPUT_PATH_LOCK:
            _RESERVED_OUTPUT_PATHS.discard(requested)


@contextmanager
def _reserved_download_cache(output_dir: Path, item: DownloadItem):
    """Reserve a stable cache directory so failed downloads can resume later."""
    identity = (
        f"{item.video_info.bvid}:{item.video_info.cid}:"
        f"{item.selected_quality.value}:{item.selected_video_codec}:"
        f"{item.selected_audio_quality}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    root = (output_dir / ".biliflow-parts").resolve()
    requested = root / digest
    with _CACHE_PATH_LOCK:
        candidate = requested
        counter = 2
        while candidate in _RESERVED_CACHE_PATHS:
            candidate = root / f"{digest}-{counter}"
            counter += 1
        candidate.mkdir(parents=True, exist_ok=True)
        _RESERVED_CACHE_PATHS.add(candidate)
    try:
        yield candidate
    finally:
        with _CACHE_PATH_LOCK:
            _RESERVED_CACHE_PATHS.discard(candidate)


def _remove_empty_cache_root(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass
