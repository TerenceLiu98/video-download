"""Tests for stream downloader retry helpers."""

import json
from pathlib import Path

import httpx
import pytest

from bilibili_downloader.core.downloader import (
    StreamDownloader,
    _reserved_download_cache,
    _reserved_output_path,
    _response_total_size,
    _stream_urls,
)
from bilibili_downloader.core.models import DownloadItem, StreamInfo, VideoInfo


def test_stream_urls_deduplicates_base_and_backups():
    stream = StreamInfo(base_url="base", backup_url=["base", "backup", "backup"])

    assert _stream_urls(stream) == ["base", "backup"]


def test_transfer_metrics_exclude_resumed_bytes(monkeypatch, tmp_path):
    downloader = StreamDownloader(object(), str(tmp_path))
    dest = tmp_path / "video.m4s"
    dest.write_bytes(b"x" * 65536)
    ticks = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr("bilibili_downloader.core.downloader.time.monotonic", lambda: next(ticks))
    response = httpx.Response(
        206, headers={"content-range": "bytes 65536-196607/196608"},
        content=b"y" * 131072, request=httpx.Request("GET", "https://example.com"),
    )
    metrics = []
    downloader._write_response(
        response, dest, 65536,
        lambda p: metrics.append((p, downloader.speed_bps, downloader.eta_seconds)),
    )
    assert metrics[0][1:] == (65536, 1)
    assert dest.stat().st_size == 196608


def test_short_transfer_has_speed_and_counts_only_new_bytes(monkeypatch, tmp_path):
    downloader = StreamDownloader(object(), str(tmp_path))
    dest = tmp_path / "video.m4s"
    dest.write_bytes(b"a" * 1024)
    ticks = iter((0.0, 0.1))
    monkeypatch.setattr("bilibili_downloader.core.downloader.time.monotonic", lambda: next(ticks))
    response = httpx.Response(
        206, headers={"content-range": "bytes 1024-2047/2048"}, content=b"b" * 1024,
        request=httpx.Request("GET", "https://example.com"),
    )
    downloader._write_response(response, dest, 1024, lambda _: None)
    assert downloader.transferred_bytes == 1024
    assert downloader.speed_bps == 10240


def test_truncated_media_is_not_marked_complete(tmp_path):
    downloader = StreamDownloader(object(), str(tmp_path))
    response = httpx.Response(
        200, headers={"content-length": "10"}, content=b"short",
        request=httpx.Request("GET", "https://example.com"),
    )
    dest = tmp_path / "video.m4s"
    with pytest.raises(httpx.ProtocolError, match="不完整"):
        downloader._write_response(response, dest, 0, lambda p: None)
    assert dest.read_bytes() == b"short"


def test_download_stream_falls_back_to_backup_url(monkeypatch, tmp_path):
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path), max_retries=1)
    calls = []

    def fake_download_url(client, url, dest, progress_callback):
        calls.append(url)
        if url == "base":
            raise OSError("primary failed")
        progress_callback(1.0)

    monkeypatch.setattr(downloader, "_download_url", fake_download_url)

    downloader._download_stream(
        StreamInfo(base_url="base", backup_url=["backup"]),
        tmp_path / "video.m4s",
        lambda progress: None,
    )

    assert calls == ["base", "backup"]


def test_download_stream_falls_back_after_url_validation_error(
    monkeypatch, tmp_path
):
    downloader = StreamDownloader(
        api_client=object(), output_dir=str(tmp_path), max_retries=1
    )
    calls = []

    def fake_download_url(client, url, dest, progress_callback):
        calls.append(url)
        if url == "invalid-primary":
            raise ValueError("invalid media URL")
        progress_callback(1.0)

    monkeypatch.setattr(downloader, "_download_url", fake_download_url)
    downloader._download_stream(
        StreamInfo(
            base_url="invalid-primary",
            backup_url=["https://upos-sz.bilivideo.com/video.m4s"],
        ),
        tmp_path / "video.m4s",
        lambda _progress: None,
    )

    assert calls == [
        "invalid-primary",
        "https://upos-sz.bilivideo.com/video.m4s",
    ]


def test_download_stream_requires_at_least_one_url(tmp_path):
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path), max_retries=1)

    with pytest.raises(RuntimeError, match="流地址为空"):
        downloader._download_stream(StreamInfo(), tmp_path / "video.m4s", lambda progress: None)


def test_response_total_size_ignores_invalid_content_length():
    class Response:
        status_code = 200
        headers = {"content-length": "not-a-number"}

    assert _response_total_size(Response(), 0) == 0


def test_retry_wait_is_cancellable(tmp_path):
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))
    downloader.cancel()

    with pytest.raises(RuntimeError, match="cancelled"):
        downloader._wait_for_retry(1.0)


def test_output_path_reservation_avoids_existing_and_concurrent_files(tmp_path):
    requested = tmp_path / "episode.mp4"
    requested.write_bytes(b"existing")

    with _reserved_output_path(requested) as first:
        with _reserved_output_path(requested) as second:
            assert first.name == "episode (2).mp4"
            assert second.name == "episode (3).mp4"


def test_download_cache_is_stable_and_audio_specific(tmp_path):
    base = DownloadItem(video_info=VideoInfo(bvid="BV1xx", cid=123))
    alternate_audio = base.model_copy(update={"selected_audio_quality": 30216})

    with _reserved_download_cache(tmp_path, base) as first:
        first_path = first
    with _reserved_download_cache(tmp_path, base) as second:
        second_path = second
    with _reserved_download_cache(tmp_path, alternate_audio) as third:
        third_path = third

    assert first_path == second_path
    assert third_path != first_path


def test_download_url_rejects_redirect_to_untrusted_host(tmp_path):
    def handler(request):
        return httpx.Response(
            302,
            headers={"location": "https://example.org/private"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="不受信任"):
            downloader._download_url(
                client,
                "https://upos-sz.bilivideo.com/video.m4s",
                tmp_path / "video.m4s",
                lambda _progress: None,
            )
    finally:
        client.close()


def test_write_response_resumes_valid_partial_content(tmp_path):
    request = httpx.Request("GET", "https://upos-sz.bilivideo.com/video.m4s")
    response = httpx.Response(
        206,
        headers={"content-range": "bytes 3-5/6"},
        content=b"def",
        request=request,
    )
    dest = tmp_path / "video.m4s"
    dest.write_bytes(b"abc")
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))

    downloader._write_response(response, dest, 3, lambda _progress: None)

    assert dest.read_bytes() == b"abcdef"


def test_write_response_rejects_mismatched_partial_range(tmp_path):
    request = httpx.Request("GET", "https://upos-sz.bilivideo.com/video.m4s")
    response = httpx.Response(
        206,
        headers={"content-range": "bytes 2-4/6"},
        content=b"cde",
        request=request,
    )
    dest = tmp_path / "video.m4s"
    dest.write_bytes(b"abc")
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))

    with pytest.raises(httpx.ProtocolError, match="续传起点"):
        downloader._write_response(response, dest, 3, lambda _progress: None)

    assert dest.read_bytes() == b"abc"


def test_download_stream_reuses_only_matching_completion_marker(
    monkeypatch, tmp_path
):
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))
    dest = tmp_path / "video.m4s"
    marker = tmp_path / "video.m4s.complete"
    dest.write_bytes(b"complete")
    marker.write_text(str(dest.stat().st_size), encoding="ascii")
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_url",
        lambda *_args: calls.append("downloaded"),
    )

    downloader._download_stream(
        StreamInfo(base_url="https://upos-sz.bilivideo.com/video.m4s"),
        dest,
        lambda _progress: None,
    )
    assert calls == []

    dest.write_bytes(b"changed")
    downloader._download_stream(
        StreamInfo(base_url="https://upos-sz.bilivideo.com/video.m4s"),
        dest,
        lambda _progress: None,
    )
    assert calls == ["downloaded"]


MEDIA_URL = "https://upos-sz.bilivideo.com/video.m4s"


def _range_server(payload: bytes, *, honour_ranges=True, requests=None, status=None):
    """Mock CDN serving ``payload`` with optional byte-range support."""

    def handler(request):
        if requests is not None:
            requests.append(request.headers.get("range"))
        if status is not None:
            return httpx.Response(status, request=request)
        header = request.headers.get("range")
        if not header or not honour_ranges:
            return httpx.Response(200, content=payload, request=request)
        start, _, end = header.removeprefix("bytes=").partition("-")
        start, end = int(start), int(end) if end else len(payload) - 1
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {start}-{end}/{len(payload)}"},
            content=payload[start:end + 1],
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mismatched_stream_parts_are_discarded_before_resume(tmp_path):
    """A partial 480P fallback must not be continued with 1080P bytes."""
    from bilibili_downloader.core.downloader import _discard_mismatched_parts

    audio = StreamInfo(id=30280, codecid=0)
    _discard_mismatched_parts(tmp_path, StreamInfo(id=32, codecid=7), audio)
    (tmp_path / "video.m4s").write_bytes(b"480p-head")
    (tmp_path / "video.m4s.complete").write_text("9", encoding="ascii")

    _discard_mismatched_parts(tmp_path, StreamInfo(id=32, codecid=7), audio)
    assert (tmp_path / "video.m4s").read_bytes() == b"480p-head"

    _discard_mismatched_parts(tmp_path, StreamInfo(id=80, codecid=7), audio)
    assert not (tmp_path / "video.m4s").exists()
    assert not (tmp_path / "video.m4s.complete").exists()


def test_legacy_parts_without_manifest_are_discarded(tmp_path):
    from bilibili_downloader.core.downloader import _discard_mismatched_parts

    (tmp_path / "video.m4s").write_bytes(b"unknown origin")
    _discard_mismatched_parts(tmp_path, StreamInfo(id=80), StreamInfo(id=30280))

    assert not (tmp_path / "video.m4s").exists()


def test_resume_is_rejected_when_remote_size_changed(tmp_path):
    from bilibili_downloader.core.downloader import _total_marker

    dest = tmp_path / "video.m4s"
    dest.write_bytes(b"abc")
    _total_marker(dest).write_text("6", encoding="ascii")
    response = httpx.Response(
        206, headers={"content-range": "bytes 3-8/9"}, content=b"defghi",
        request=httpx.Request("GET", MEDIA_URL),
    )
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))

    with pytest.raises(httpx.ProtocolError, match="大小"):
        downloader._write_response(response, dest, 3, lambda _p: None)
    assert not dest.exists()


def test_large_stream_downloads_in_parallel_segments(monkeypatch, tmp_path):
    monkeypatch.setattr("bilibili_downloader.core.downloader.SEGMENT_MIN_SIZE", 10)
    payload = bytes(range(256)) * 40
    requests = []
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path), segments=4)
    dest = tmp_path / "video.m4s"

    with _range_server(payload, requests=requests) as client:
        downloader._download_stream(StreamInfo(base_url=MEDIA_URL), dest, lambda _p: None, client=client)

    assert dest.read_bytes() == payload
    assert downloader.transferred_bytes == len(payload)
    assert sorted(requests)[0] == "bytes=0-0"  # range probe
    assert len([r for r in requests if r != "bytes=0-0"]) == 4
    assert list(tmp_path.glob("video.m4s.part*")) == []
    assert not (tmp_path / "video.m4s.segments.json").exists()


def test_interrupted_segments_resume_only_missing_bytes(monkeypatch, tmp_path):
    from bilibili_downloader.core.downloader import _new_segment_plan, _segment_part

    payload = bytes(range(200))
    dest = tmp_path / "video.m4s"
    plan = _new_segment_plan(dest, len(payload), 2)
    _segment_part(dest, 0).write_bytes(payload[:100])        # finished
    _segment_part(dest, 1).write_bytes(payload[100:130])     # interrupted
    requests = []
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))

    with _range_server(payload, requests=requests) as client:
        downloader._download_url(client, MEDIA_URL, dest, lambda _p: None)

    assert plan["ranges"] == [[0, 99], [100, 199]]
    assert requests == ["bytes=130-199"]
    assert dest.read_bytes() == payload


def test_server_without_range_support_uses_single_connection(monkeypatch, tmp_path):
    monkeypatch.setattr("bilibili_downloader.core.downloader.SEGMENT_MIN_SIZE", 10)
    payload = b"z" * 500
    requests = []
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path))
    dest = tmp_path / "video.m4s"

    with _range_server(payload, honour_ranges=False, requests=requests) as client:
        downloader._download_url(client, MEDIA_URL, dest, lambda _p: None)

    assert dest.read_bytes() == payload
    assert requests == ["bytes=0-0", None]


def test_expired_stream_urls_are_resigned_without_spending_retries(tmp_path):
    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path), max_retries=1)
    fresh = "https://upos-sz.bilivideo.com/fresh.m4s"
    refreshed = []

    def handler(request):
        if request.url.path == "/video.m4s":
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=b"media", request=request)

    def refresh():
        refreshed.append(True)
        return StreamInfo(base_url=fresh)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader._download_stream(
            StreamInfo(base_url=MEDIA_URL), tmp_path / "video.m4s", lambda _p: None,
            client=client, refresh=refresh,
        )

    assert refreshed == [True]
    assert (tmp_path / "video.m4s").read_bytes() == b"media"


def test_exhausted_network_retries_raise_transient_error(monkeypatch, tmp_path):
    from bilibili_downloader.core.errors import (
        TransientDownloadError,
        is_transient_error,
    )

    downloader = StreamDownloader(api_client=object(), output_dir=str(tmp_path), max_retries=1)

    def offline(*_args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(downloader, "_download_url", offline)
    with pytest.raises(TransientDownloadError) as caught:
        downloader._download_stream(StreamInfo(base_url=MEDIA_URL), tmp_path / "v.m4s", lambda _p: None)
    assert is_transient_error(caught.value)


def test_permanent_http_errors_are_not_transient():
    from bilibili_downloader.core.errors import is_transient_error
    from bilibili_downloader.utils.cancellation import OperationCancelled

    request = httpx.Request("GET", MEDIA_URL)
    forbidden = httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))
    throttled = httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

    assert not is_transient_error(forbidden)
    assert is_transient_error(throttled)
    assert not is_transient_error(OperationCancelled("stop"))
    assert not is_transient_error(OSError(28, "No space left on device"))


def _fallback_api(stream_ids):
    """Fake API whose playurl serves the next quality from ``stream_ids`` per call."""

    class API:
        def get_play_url(self, **_kwargs):
            qid = stream_ids.pop(0)
            return {
                "video_streams": [StreamInfo(id=qid, codecid=7,
                                             base_url=f"https://upos-sz.bilivideo.com/{qid}.m4s")],
                "audio_streams": [StreamInfo(id=30280, codecid=0,
                                             base_url="https://upos-sz.bilivideo.com/audio.m4s")],
            }

    return API()


def _run_fallback_scenario(monkeypatch, tmp_path):
    from bilibili_downloader.core import downloader as module
    from bilibili_downloader.core.models import VideoQuality

    media = {"/32.m4s": b"L" * 40, "/80.m4s": b"H" * 100, "/audio.m4s": b"A" * 10}
    fail_audio = {"on": True}

    def handler(request):
        body = media[request.url.path]
        if request.url.path == "/audio.m4s" and fail_audio["on"]:
            raise httpx.ConnectError("offline", request=request)
        header = request.headers.get("range")
        if header:
            start = int(header.removeprefix("bytes=").split("-")[0])
            return httpx.Response(206, content=body[start:], request=request,
                                  headers={"content-range": f"bytes {start}-{len(body) - 1}/{len(body)}"})
        return httpx.Response(200, content=body, request=request)

    def merge(video, audio, merged, **_kwargs):
        merged.write_bytes(video.read_bytes() + audio.read_bytes())
        return True, ""

    monkeypatch.setattr(module.StreamDownloader, "_media_client",
                        lambda self: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module.FFmpegManager, "merge_streams", merge)
    item = DownloadItem(video_info=VideoInfo(bvid="BV1xx", cid=1, title="clip"),
                        selected_quality=VideoQuality.Q1080P, selected_video_codec=7)
    downloader = StreamDownloader(_fallback_api([32, 80]), str(tmp_path), max_retries=1, segments=1)

    with pytest.raises(RuntimeError):
        downloader.download(item, lambda *_a: None)      # logged out: 480P video done, audio fails
    fail_audio["on"] = False
    return Path(downloader.download(item, lambda *_a: None))   # logged in: 1080P served


def test_resume_after_quality_change_never_merges_stale_video(monkeypatch, tmp_path):
    output = _run_fallback_scenario(monkeypatch, tmp_path)

    assert output.read_bytes() == b"H" * 100 + b"A" * 10



class _NoNetworkAPI:
    def get_play_url(self, *args, **kwargs):
        raise AssertionError("finished download must not fetch stream URLs")


def _standalone_item():
    return DownloadItem(video_info=VideoInfo(bvid="BV1GJ411x7h7", cid=7, title="Song"))


def test_finished_standalone_download_is_skipped_via_ledger(tmp_path):
    from bilibili_downloader.core.completed import record_completed

    item = _standalone_item()
    finished = tmp_path / "Song (2).mp4"  # collision-renamed on its first run
    finished.write_bytes(b"mp4")
    record_completed(tmp_path, item, finished)

    downloader = StreamDownloader(_NoNetworkAPI(), str(tmp_path))
    result = downloader.download(item, lambda _p, _t: None)

    assert Path(result) == finished
    assert downloader.last_download_skipped


def test_legacy_standalone_download_is_skipped_when_sidecar_matches(tmp_path):
    from bilibili_downloader.core.completed import LEDGER_NAME

    item = _standalone_item()
    (tmp_path / "Song.mp4").write_bytes(b"mp4")
    (tmp_path / "Song.info.json").write_text(
        json.dumps({"data": {"bvid": "BV1GJ411x7h7"}}), encoding="utf-8"
    )

    downloader = StreamDownloader(_NoNetworkAPI(), str(tmp_path))
    assert Path(downloader.download(item, lambda _p, _t: None)) == tmp_path / "Song.mp4"
    assert "BV1GJ411x7h7:7" in json.loads((tmp_path / LEDGER_NAME).read_text("utf-8"))


def test_same_title_from_another_video_is_downloaded_again(tmp_path):
    item = _standalone_item()
    (tmp_path / "Song.mp4").write_bytes(b"mp4")
    (tmp_path / "Song.info.json").write_text(
        json.dumps({"data": {"bvid": "BV1other"}}), encoding="utf-8"
    )

    downloader = StreamDownloader(_NoNetworkAPI(), str(tmp_path))
    with pytest.raises(AssertionError, match="must not fetch"):
        downloader.download(item, lambda _p, _t: None)
