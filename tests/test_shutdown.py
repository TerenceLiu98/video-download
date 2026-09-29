"""Shutdown remains responsive with queued work, stalled HTTP and slow disks."""

import asyncio
import json
import socket
import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bilibili_downloader.core.models import DownloadItem, VideoInfo
from bilibili_downloader.tui.app import BiliFlowTUI
from bilibili_downloader.tui.screens.shutdown_screen import ShutdownScreen
from bilibili_downloader.utils.cancellation import RequestCancellation


def test_cancel_interrupts_api_cooldown_and_waiting_requests():
    from time import monotonic

    from bilibili_downloader.api.client import BilibiliAPIClient

    client = BilibiliAPIClient()
    client._next_api_request_at = monotonic() + 120
    errors = []

    def request():
        try:
            client._request("/test")
        except RuntimeError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=request) for _ in range(4)]
    for thread in threads:
        thread.start()
    client.cancel_pending_requests()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(errors) == 4
    client.close()


@pytest.mark.parametrize("send_headers", [True, False])
def test_cancel_interrupts_stalled_http(send_headers):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    stalled, release = threading.Event(), threading.Event()
    errors = []
    scope = RequestCancellation()

    def serve():
        with listener.accept()[0] as conn:
            conn.recv(4096)
            if send_headers:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\npartial")
            stalled.set()
            release.wait(5)

    def request():
        try:
            with httpx.Client(event_hooks={"request": [scope.attach]}, trust_env=False) as client:
                client.get(f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=120)
        except (httpx.HTTPError, RuntimeError) as exc:
            errors.append(exc)

    server = threading.Thread(target=serve)
    download = threading.Thread(target=request)
    server.start()
    download.start()
    try:
        assert stalled.wait(3)
        scope.cancel()
        download.join(2)
        assert not download.is_alive(), "Cancellation waited for the 120s read timeout"
        assert errors
    finally:
        release.set()
        server.join(5)
        download.join(5)
        listener.close()


def test_large_pending_queue_exits_without_starting_pending_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")
    cancelled, saving, release_save = threading.Event(), threading.Event(), threading.Event()
    service = MagicMock()
    service.cancel.side_effect = cancelled.set

    def download(*_):
        assert cancelled.wait(5)
        raise RuntimeError("Download cancelled")

    service.download.side_effect = download
    from bilibili_downloader.core.creator import atomic_write_json

    def slow_save(path, payload):
        assert threading.current_thread() is not threading.main_thread()
        saving.set()
        assert release_save.wait(5)
        atomic_write_json(path, payload)

    async def run():
        with patch("bilibili_downloader.core.download_service.DownloadService", return_value=service), patch(
            "bilibili_downloader.core.creator.atomic_write_json", side_effect=slow_save
        ):
            app = BiliFlowTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                try:
                    app.state.settings.max_concurrent_downloads = 1
                    item = DownloadItem(video_info=VideoInfo(bvid="BV1", cid=1, title="下载"))
                    for _ in range(10000):
                        did = app._model.add(item, str(tmp_path))
                        app._launch_download(did, item)
                    await pilot.pause()
                    assert len(app._active_downloads) == 1
                    assert len(app._pending_downloads) == 9999
                    assert len([w for w in app.workers if w.group == "download"]) == 1
                    app._do_quit()
                    for _ in range(40):
                        await pilot.pause(0.05)
                        if saving.is_set():
                            break
                    assert cancelled.is_set() and saving.is_set()
                    assert isinstance(app.screen, ShutdownScreen)
                    ticks = []
                    app.set_timer(0.01, lambda: ticks.append(True))
                    await pilot.pause(0.05)
                    assert ticks, "UI event loop was blocked by final save"
                    assert not app._pending_downloads
                    service.download.assert_called_once()
                finally:
                    cancelled.set()
                    release_save.set()
                for _ in range(40):
                    await pilot.pause(0.05)
                    if not app.is_running:
                        break
                assert not app.is_running
            payload = json.loads((tmp_path / "tui-tasks.json").read_text())
            assert len(payload["rows"]) == 10000
            assert all(row["state"] == "paused" for row in payload["rows"])

    asyncio.run(run())
