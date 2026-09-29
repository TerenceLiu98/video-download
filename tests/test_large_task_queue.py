"""Large queues must not rebuild every row or save on the event loop."""

import asyncio
import threading
from time import perf_counter
from unittest.mock import patch

from bilibili_downloader.core.models import DownloadItem, VideoInfo
from bilibili_downloader.tui.app import BiliFlowTUI
from bilibili_downloader.tui.widgets.download_queue import DownloadQueue


def test_ten_thousand_rows_update_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")

    async def run():
        app = BiliFlowTUI()
        async with app.run_test(size=(140, 44)) as pilot:
            for i in range(10000):
                app._model.add_source(f"BV{i}", f"视频 {i}", str(tmp_path), {})
            table = app.query_one(DownloadQueue)
            start = perf_counter()
            app._refresh_queue()
            print(f"10k initial table: {perf_counter() - start:.3f}s")
            app._show_workspace("tasks")
            await pilot.pause()
            table.move_cursor(row=5000)
            with patch.object(table, "clear", wraps=table.clear) as clear, patch.object(
                table, "_cells", wraps=table._cells
            ) as cells:
                app._model.get(0).state = "resolving"
                app._model.get(0).status = "解析中"
                start = perf_counter()
                app._refresh_queue()
                print(f"10k one-row refresh: {perf_counter() - start:.3f}s")
                clear.assert_not_called()
                assert cells.call_count == 1
                assert table._cursor_download_id() == 5000
                with patch.object(table, "visible_rows", side_effect=AssertionError("full scan on progress")):
                    app._model.set_progress(0, 0.5, "下载中", 1024, 10)
                    table.refresh_row_by_id(0)
                item = DownloadItem(video_info=VideoInfo(bvid="BV0", cid=2, title="第二P"))
                extra = app._model.resolve_source(0, item, after=0)
                app._refresh_queue()
                clear.assert_not_called()
                assert table._visible_ids[:3] == [0, extra, 1]
                assert table._cursor_download_id() == 5000
            assert table.row_count == 10001

    asyncio.run(run())


def test_slow_checkpoint_does_not_block_ui_and_keeps_latest_changes(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")
    started, release = threading.Event(), threading.Event()
    snapshots = []
    main_thread = threading.get_ident()

    def slow_save(path, snapshot):
        assert threading.get_ident() != main_thread
        snapshots.append(snapshot)
        started.set()
        assert release.wait(5)

    async def run():
        with patch("bilibili_downloader.core.creator.atomic_write_json", side_effect=slow_save):
            app = BiliFlowTUI()
            async with app.run_test(size=(120, 40)) as pilot:
                try:
                    did = app._model.add_source("BV1", "标题", str(tmp_path), {})
                    app._refresh_queue()
                    app._save_queue()
                    await pilot.pause()
                    assert started.is_set()
                    await pilot.click("#nav-batch")
                    app._model.get(did).status = "解析中"
                    app._refresh_queue()
                    app._save_queue()
                    assert len(snapshots) == 1
                    assert snapshots[0]["rows"][0]["status"] == "待解析"
                    release.set()
                    await asyncio.wrap_future(app._save_future)
                    app._save_queue()
                    await asyncio.wrap_future(app._save_future)
                    assert snapshots[-1]["rows"][0]["status"] == "解析中"
                    assert snapshots[0]["rows"][0]["status"] == "待解析"
                finally:
                    release.set()

    asyncio.run(run())
