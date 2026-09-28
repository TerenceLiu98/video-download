"""Task navigation, filtering, details and app-owned background imports."""

import asyncio
import threading
from unittest.mock import MagicMock, patch

from textual.widgets import Button, Input, Select, Static

from bilibili_downloader.core.models import DownloadItem, DownloadOutcome, VideoInfo
from bilibili_downloader.tui.app import BiliFlowTUI
from bilibili_downloader.tui.screens.main_screen import MainScreen
from bilibili_downloader.tui.widgets.download_queue import DownloadQueue
from bilibili_downloader.tui.widgets.task_workspace import TaskWorkspace


def item(title):
    return DownloadItem(video_info=VideoInfo(bvid="BV1TEST00001", cid=1, title=title))


def test_task_filters_details_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")

    async def run():
        app = BiliFlowTUI()
        async with app.run_test(size=(140, 44)) as pilot:
            model = app._model
            complete = model.add(item("已完成视频"))
            model.mark_done(complete, DownloadOutcome(video_path="/tmp/complete.mp4"))
            partial = model.add(item("部分完成的长标题 [不是富文本]"))
            model.mark_done(partial, DownloadOutcome(video_path="/tmp/partial.mp4", warnings=["字幕接口返回 403"]))
            active = model.add(item("下载中的视频"))
            model.set_progress(active, 0.42, "视频流", 2097152, 120)
            model.register_worker(active, MagicMock())
            app._refresh_queue()
            await pilot.click("#nav-batch")
            await pilot.pause()
            table = app.query_one(DownloadQueue)
            assert not app.query_one(MainScreen).display
            assert app.query_one(TaskWorkspace).display
            assert table._visible_ids == [active, partial, complete]
            assert table.region.height > 20
            assert app.query_one(TaskWorkspace).region.x >= app.query_one("NavSidebar").region.right
            app.query_one("#task-filter", Select).value = "partial"
            await pilot.pause()
            assert table._visible_ids == [partial]
            detail = str(app.query_one("#task-details", Static).render())
            assert "字幕接口返回 403" in detail
            assert "/tmp/partial.mp4" in detail
            assert "[不是富文本]" in detail
            app.query_one("#task-search", Input).value = "不存在"
            await pilot.pause()
            assert table.row_count == 0
            assert app.query_one("#task-delete", Button).disabled
            app.query_one("#task-search", Input).value = ""
            app.query_one("#task-filter", Select).value = "all"
            await pilot.pause()
            app.query_one("#task-clear_done", Button).press()
            await pilot.pause()
            assert model.get(complete) is None
            assert model.get(partial) is not None
            assert model.get(active) is not None
            app.save_screenshot(str(tmp_path / "task-workspace-wide.svg"))
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert table._compact
            assert len(table.columns) == 3
            assert table.region.height >= 3
            assert table.virtual_size.width <= table.size.width
            app.save_screenshot(str(tmp_path / "task-workspace-narrow.svg"))

    asyncio.run(run())


def test_batch_import_and_download_continue_after_navigation(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")
    resolving = threading.Event()
    release_resolve = threading.Event()
    downloading = threading.Event()
    release_download = threading.Event()

    def resolve(_source):
        resolving.set()
        assert release_resolve.wait(5)
        return item("后台解析的视频").video_info

    def download(_item, _callback):
        downloading.set()
        assert release_download.wait(5)
        return DownloadOutcome(video_path=str(tmp_path / "original" / "video.mp4"))

    service = MagicMock()
    service.download.side_effect = download

    async def run():
        with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", side_effect=resolve), patch(
            "bilibili_downloader.core.download_service.DownloadService", return_value=service
        ) as constructor:
            app = BiliFlowTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                try:
                    app.state.settings.output_dir = str(tmp_path / "original")
                    app._open_batch()
                    await pilot.pause()
                    app.screen.query_one("#bs-text").load_text("BV1GJ411x7h7")
                    app.screen.query_one("#bs-enqueue", Button).press()
                    await pilot.pause()
                    assert resolving.is_set()
                    assert app._batch_jobs[1].running
                    await pilot.click("#nav-home")
                    assert app.query_one(MainScreen).display
                    app.state.settings.output_dir = str(tmp_path / "changed")
                    release_resolve.set()
                    for _ in range(30):
                        await pilot.pause(0.05)
                        if downloading.is_set():
                            break
                    assert downloading.is_set()
                    assert constructor.call_args.args[1] == str(tmp_path / "original")
                    assert app.query_one(MainScreen).display
                    assert not app._batch_jobs[1].running
                    # A modal does not own either the import or download worker.
                    app._open_settings()
                    await pilot.pause()
                    release_download.set()
                    for _ in range(30):
                        await pilot.pause(0.05)
                        if app._model.rows()[0].state == "done":
                            break
                    assert app._model.rows()[0].state == "done"
                    app.pop_screen()
                    await pilot.pause()
                    await pilot.click("#nav-batch")
                    assert app.query_one(DownloadQueue).row_count == 1
                finally:
                    release_resolve.set()
                    release_download.set()

    asyncio.run(run())
