"""Tests for CLI page and companion-file options."""

import argparse
import threading
import time

import pytest

from bilibili_downloader import __main__ as cli
from bilibili_downloader.core.models import (
    AppSettings,
    CreatorVideoEntry,
    CreatorVideoIndex,
    DownloadOutcome,
    VideoInfo,
    VideoPage,
)


def test_cli_download_expands_all_pages_and_forwards_options(monkeypatch, tmp_path):
    info = VideoInfo(
        bvid="BV1GJ411x7h7",
        cid=11,
        title="Series",
        pages=[
            VideoPage(cid=11, page=1, part="One"),
            VideoPage(cid=22, page=2, part="Two"),
        ],
    )
    captured = []

    class FakeClient:
        def __init__(self, sessdata=None):
            self.sessdata = sessdata

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            assert output_dir == str(tmp_path)

        def download(self, item, callback):
            captured.append(item)
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.api.client.BilibiliAPIClient", FakeClient
    )
    monkeypatch.setattr(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one",
        lambda self, source: info,
    )
    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    monkeypatch.setattr(
        "bilibili_downloader.utils.config.ConfigManager.load",
        lambda self: AppSettings(output_dir=str(tmp_path)),
    )
    args = argparse.Namespace(
        source="BV1GJ411x7h7",
        quality=80,
        output=None,
        danmaku=True,
        subtitle=True,
        codec=7,
        page="all",
        subtitle_language="en-US",
    )

    cli._cli_download(args)

    assert [item.video_info.cid for item in captured] == [11, 22]
    assert all(item.selected_video_codec == 7 for item in captured)
    assert all(item.download_danmaku and item.download_subtitle for item in captured)
    assert all(item.selected_subtitle_lan == "en-US" for item in captured)


def _make_index(n):
    return CreatorVideoIndex(
        mid=1, name="UP", source="1", fetched_at="2026-07-23T00:00:00+00:00",
        total=n, reported_total=n,
        videos=[CreatorVideoEntry(bvid=f"BV{i:012d}", title=f"v{i}") for i in range(n)],
    )


def _batch_args(tmp_path, jobs=None, **extra):
    base = dict(
        quality=80, output=None, codec=7, subtitle_language="zh-Hans",
        jobs=jobs, refresh_sidecars=False,
    )
    base.update(extra)
    return argparse.Namespace(**base)


def test_creator_batch_downloads_concurrently(monkeypatch, tmp_path):
    """--jobs actually runs downloads in parallel (not sequentially)."""
    index = _make_index(4)
    active = 0
    active_lock = threading.Lock()
    peak = 0

    class FakeClient:
        def __init__(self, sessdata=None):
            pass

        def get_video_info(self, bvid):
            return VideoInfo(bvid=bvid, cid=1, title=bvid)

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            pass

        def download(self, item, callback):
            nonlocal active, peak
            with active_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)  # hold the slot so concurrency is observable
            with active_lock:
                active -= 1
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    args = _batch_args(tmp_path, jobs=4)
    cli._download_creator_entries(FakeClient(), AppSettings(output_dir=str(tmp_path)),
                                  tmp_path, index, index.videos, args)

    assert peak >= 2, f"downloads did not run concurrently (peak={peak})"


def test_creator_batch_isolates_failures_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    """One failing entry must not abort the others; failures exit 1."""
    index = _make_index(3)

    class FakeClient:
        def __init__(self, sessdata=None):
            pass

        def get_video_info(self, bvid):
            return VideoInfo(bvid=bvid, cid=1, title=bvid)

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            pass

        def download(self, item, callback):
            if item.video_info.bvid == "BV000000000001":
                raise RuntimeError("boom")
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    args = _batch_args(tmp_path, jobs=2)
    with pytest.raises(SystemExit) as exc_info:
        cli._download_creator_entries(FakeClient(), AppSettings(output_dir=str(tmp_path)),
                                      tmp_path, index, index.videos, args)
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    # Two succeeded, one failed and is listed in the failure summary.
    assert "✗ BV000000000001" in out
    assert "1 个任务失败" in out
