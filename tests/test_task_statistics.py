"""Statistics distinguish recovery, current imports, and actual media bytes."""

import asyncio
import time

from textual.widgets import Select, Static

from bilibili_downloader.core.models import DownloadItem, DownloadOutcome, VideoInfo
from bilibili_downloader.tui import messages
from bilibili_downloader.tui.app import BiliFlowTUI
from bilibili_downloader.tui.state import BatchJob
from bilibili_downloader.tui.widgets.download_queue import (
    DownloadQueue,
    DownloadQueueModel,
    format_speed,
)


def test_interrupted_records_are_not_resolution_failures(tmp_path):
    model = DownloadQueueModel()
    for state, status in (("awaiting", "待解析"), ("resolving", "解析中"),
                          ("resolve_failed", "解析中断，可重试"), ("resolve_failed", "解析失败")):
        did = model.add_source("BV1", "视频", str(tmp_path), {})
        model.get(did).state, model.get(did).status = state, status
    model.save(tmp_path / "tasks.json")
    restored = DownloadQueueModel()
    restored.restore(tmp_path / "tasks.json")
    assert [r.state for r in restored.rows()] == ["resolve_interrupted"] * 3 + ["resolve_failed"]


def test_speed_units_and_stale_samples():
    assert format_speed(2048) == "2.0 KiB/s"
    assert format_speed(12) == "12 B/s"
    model = DownloadQueueModel()
    did = model.add(None)
    model.set_progress(did, 0.3, "下载中", 2048, 60)
    row = model.get(did)
    assert DownloadQueue._metrics(row) == ("2.0 KiB/s", "01:00")
    row.speed_updated_at -= 3
    assert DownloadQueue._metrics(row) == ("--", "--")


def test_aggregate_bytes_survive_merge_and_completed_rows_hide(tmp_path, monkeypatch):
    monkeypatch.setattr("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr("bilibili_downloader.utils.config._load_sessdata_from_keyring", lambda: "")

    async def run():
        app = BiliFlowTUI()
        async with app.run_test(size=(140, 44)) as pilot:
            ids = [app._model.add(DownloadItem(video_info=VideoInfo(title=f"视频 {i}"))) for i in range(3)]
            app._refresh_queue()
            app._show_workspace("tasks")
            app._batch_jobs[1] = BatchJob(1, 10892, str(tmp_path), done=8592)
            app._on_download_progress(messages.DownloadProgress(ids[0], 0.5, "视频流", transferred_bytes=1024))
            app._on_download_progress(messages.DownloadProgress(ids[0], 0.82, "合并", transferred_bytes=2048))
            app._on_download_progress(messages.DownloadProgress(ids[0], 1.0, "完成", transferred_bytes=2048))
            app._on_download_finished(messages.DownloadFinished(ids[0], DownloadOutcome(video_path="/tmp/a.mp4")))
            app._model.mark_done(ids[1], DownloadOutcome(video_path="/tmp/b.mp4", warnings=["字幕失败"]))
            app._refresh_queue()
            app._transfer_at = time.monotonic() - 1
            app._refresh_transfer_display()
            assert 2000 < app._transfer_speed <= 2048
            assert app.query_one(DownloadQueue)._visible_ids == [ids[2]]
            summary = str(app.query_one("#task-summary", Static).render())
            assert "全部记录" in summary and "2.0 KiB/s" in summary
            assert "部分完成 1" in summary and "失败 0" in summary
            imports = str(app.query_one("#import-status", Static).render())
            assert "本次导入" in imports and "8592/10892" in imports
            app._transfer_at = time.monotonic() - 1
            app._refresh_transfer_display()
            assert app._transfer_speed == 0
            app.query_one("#task-filter", Select).value = "partial"
            await pilot.pause()
            assert app.query_one(DownloadQueue)._visible_ids == [ids[1]]
            assert "字幕失败" in str(app.query_one("#task-details", Static).render())

    asyncio.run(run())
