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
            assert table._visible_ids == [complete, partial, active]
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
            await pilot.pause(0.2)
            assert table.row_count == 0
            assert app.query_one("#task-delete", Button).disabled
            app.query_one("#task-search", Input).value = ""
            app.query_one("#task-filter", Select).value = "all"
            await pilot.pause(0.2)
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


def test_index_rows_appear_before_resolution_and_stay_in_order(tmp_path, monkeypatch):
    from bilibili_downloader.core.models import (
        CreatorVideoEntry,
        CreatorVideoIndex,
        VideoPage,
    )
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueueModel

    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")
    monkeypatch.setattr("bilibili_downloader.tui.workers.batch.BatchWorker._cancellable_wait", lambda *_: None)
    release = threading.Event()
    sources = ["BVfirst", "BVsecond", "BVthird"]
    index = CreatorVideoIndex(mid=123, name="UP", source="test", fetched_at="today", total=3,
                              videos=[CreatorVideoEntry(bvid=s, title=f"标题 {s}") for s in sources])

    def resolve(source):
        assert release.wait(5)
        if source == "BVthird":
            raise ValueError("视频已删除")
        return VideoInfo(bvid=source, cid=1, title=f"标题 {source}", pages=(
            [VideoPage(cid=1, page=1, part="上"), VideoPage(cid=2, page=2, part="下")]
            if source == "BVfirst" else []
        ))

    async def run():
        with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", side_effect=resolve):
            app = BiliFlowTUI()
            async with app.run_test(size=(140, 44)) as pilot:
                try:
                    app.state.settings.output_dir = str(tmp_path)
                    with patch.object(app, "_launch_download") as launch:
                        app._start_batch(sources, creator_index=index)
                        await pilot.pause()
                        rows = app._model.rows()
                        original_ids = [r.download_id for r in rows]
                        assert len(rows) == 3
                        assert [r.title for r in rows] == [f"标题 {s}" for s in sources]
                        assert all(r.item is None for r in rows)
                        assert rows[1].state == "awaiting"
                        launch.assert_not_called()
                        release.set()
                        for _ in range(40):
                            await pilot.pause(0.05)
                            if not app._batch_jobs[1].running:
                                break
                        assert not app._batch_jobs[1].running
                        rows = app._model.rows()
                        assert len(rows) == 4
                        assert [r.download_id for r in rows][::2] == original_ids[:2]
                        assert rows[-1].download_id == original_ids[-1]
                        assert rows[-1].state == "resolve_failed"
                        assert "视频已删除" in rows[-1].error
                        assert [r.item.video_info.cid for r in rows[:2]] == [1, 2]
                        assert launch.call_count == 3
                        order = [r.download_id for r in rows]
                        app._model.mark_done(order[0], DownloadOutcome(video_path="/tmp/a.mp4"))
                        app._model.mark_done(order[1], DownloadOutcome(video_path="/tmp/b.mp4", warnings=["无字幕"]))
                        app._refresh_queue()
                        table = app.query_one(DownloadQueue)
                        assert table._visible_ids == order
                        assert app._batch_jobs[1].done == 3
                        summary = str(app.query_one("#task-summary", Static).render())
                        assert "完成 1/3" in summary and "部分完成 1" in summary
                        imports = str(app.query_one("#import-status", Static).render())
                        assert "解析 3/3" in imports and "失败 1" in imports
                        restored = DownloadQueueModel()
                        app._save_queue()
                        await asyncio.wrap_future(app._save_future)
                        restored.restore(app._task_path)
                        assert [r.download_id for r in restored.rows()] == order
                        assert restored.rows()[-1].source == "BVthird"
                        app.save_screenshot(str(tmp_path / "index-progress.svg"))
                        with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one",
                                   return_value=VideoInfo(bvid="BVthird", cid=3, title="重试成功")):
                            app._retry_download(order[-1])
                            for _ in range(40):
                                await pilot.pause(0.05)
                                if not app._batch_jobs[2].running:
                                    break
                            assert app._model.get(order[-1]).item.video_info.cid == 3
                            assert table._visible_ids == order
                            assert launch.call_count == 4
                finally:
                    release.set()

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
