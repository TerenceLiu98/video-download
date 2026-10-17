"""TUI download scheduling: slot release, auto-retry and the network breaker."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import httpx

from bilibili_downloader.core.errors import TransientDownloadError
from tests.test_tui import _config_patches, _item, _outcome


def _run_app(tmp_path, service, scenario, max_concurrent=2):
    from bilibili_downloader.tui.app import BiliFlowTUI

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3, patch(
            "bilibili_downloader.core.download_service.DownloadService", return_value=service
        ):
            app = BiliFlowTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._task_path = None
                app.state.settings.output_dir = str(tmp_path)
                app.state.settings.max_concurrent_downloads = max_concurrent
                await scenario(app, pilot)

    asyncio.run(run())


def _offline(*_args, **_kwargs):
    raise TransientDownloadError("下载失败：offline") from httpx.ConnectError("offline")


async def _until(pilot, condition, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(delay=0.05)
    raise AssertionError("condition not reached")


def test_network_outage_trips_breaker_instead_of_failing_whole_queue(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.tui.app._AUTO_RETRY_DELAYS", (60.0,))
    service = MagicMock()
    service.download.side_effect = _offline

    async def scenario(app, pilot):
        for index in range(10):
            app.enqueue_download(_item(f"BV{index}", f"v{index}"))
        await _until(pilot, lambda: not app._active_downloads and app._dispatch_paused_until)
        rows = app._model.rows()
        started = service.download.call_count
        assert 3 <= started <= 4      # breaker opens after 3 consecutive network failures
        assert [r.state for r in rows].count("failed") == 0
        assert all(r.state == "retry" for r in rows[:started])
        assert "自动重试" in rows[0].status
        assert all(r.state == "pending" for r in rows[started:])
        assert "网络异常" in app.query_one("#task-summary").content

    _run_app(tmp_path, service, scenario)


def test_auto_retry_recovers_after_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.tui.app._AUTO_RETRY_DELAYS", (0.1,))
    service = MagicMock()
    service.download.side_effect = iter([TransientDownloadError("offline"), _outcome()])

    async def scenario(app, pilot):
        app.enqueue_download(_item())
        await _until(pilot, lambda: app._model.rows()[0].state == "done")
        assert service.download.call_count == 2

    _run_app(tmp_path, service, scenario)


def test_permanent_failures_are_not_auto_retried(tmp_path):
    service = MagicMock()
    service.download.side_effect = RuntimeError("No matching video stream found")

    async def scenario(app, pilot):
        app.enqueue_download(_item())
        await _until(pilot, lambda: app._model.rows()[0].state == "failed")
        await pilot.pause(delay=0.2)
        assert service.download.call_count == 1
        assert app._dispatch_paused_until == 0.0

    _run_app(tmp_path, service, scenario)


def test_archiving_task_releases_its_download_slot(tmp_path):
    release = threading.Event()
    service = MagicMock()

    def download(item, _callback, media_ready=None):
        if item.video_info.bvid == "BV_SLOW_ARCHIVE":
            media_ready()
            assert release.wait(5)   # e.g. a long comment archive
        return _outcome()

    service.download.side_effect = download

    async def scenario(app, pilot):
        app.enqueue_download(_item("BV_SLOW_ARCHIVE", "archives comments"))
        app.enqueue_download(_item("BV_NEXT", "next video"))
        try:
            await _until(pilot, lambda: app._model.rows()[1].state == "done")
            assert app._model.rows()[0].state != "done"
            assert app._archiving == {app._model.rows()[0].download_id}
        finally:
            release.set()
        await _until(pilot, lambda: app._model.rows()[0].state == "done")
        assert not app._archiving

    _run_app(tmp_path, service, scenario, max_concurrent=1)


def test_retry_failed_requeues_every_failed_row(tmp_path):
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueue

    service = MagicMock()
    service.download.side_effect = RuntimeError("HTTP 403")

    async def scenario(app, pilot):
        for index in range(3):
            app.enqueue_download(_item(f"BV{index}", f"v{index}"))
        await _until(pilot, lambda: all(r.state == "failed" for r in app._model.rows()))
        service.download.side_effect = None
        service.download.return_value = _outcome()
        app.post_message(DownloadQueue.QueueAction("retry_failed", -1))
        await _until(pilot, lambda: all(r.state == "done" for r in app._model.rows()))
        assert service.download.call_count == 6

    _run_app(tmp_path, service, scenario)


def test_breaker_trips_once_for_simultaneous_failures(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.tui.app._AUTO_RETRY_DELAYS", (60.0,))
    service = MagicMock()
    service.download.side_effect = _offline

    async def scenario(app, pilot):
        trips = []
        original = app.notify
        app.notify = lambda text, **kw: (trips.append(text) if "暂停启动" in text else None, original(text, **kw))
        for index in range(6):
            app.enqueue_download(_item(f"BV{index}", f"v{index}"))
        await _until(pilot, lambda: not app._active_downloads and app._dispatch_paused_until)
        await pilot.pause(delay=0.2)
        assert trips == ["网络连续出错，暂停启动新任务 30 秒"]

    _run_app(tmp_path, service, scenario, max_concurrent=5)
