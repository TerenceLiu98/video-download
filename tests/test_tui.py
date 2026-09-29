"""Tests for the TUI: queue model lifecycle, concurrency, and integration.

The queue model is pure-Python and unit-tested directly. Pilot tests drive the
Textual app with the network layer monkeypatched (wrapped in asyncio.run so no
pytest-asyncio plugin is needed). A guard test ensures the bare invocation
(no subcommand) still routes to the GUI launcher, not the TUI.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

from bilibili_downloader.core.models import (
    DownloadItem,
    DownloadOutcome,
    VideoInfo,
    VideoQuality,
)
from bilibili_downloader.tui.widgets.download_queue import DownloadQueueModel


def _item(bvid="BV1TEST00001", title="测试视频"):
    return DownloadItem(
        video_info=VideoInfo(bvid=bvid, cid=1, title=title, duration=60),
        selected_quality=VideoQuality.Q1080P,
        selected_video_codec=7,
    )


def _outcome():
    return DownloadOutcome(video_path="/tmp/x.mp4")


# --- queue model lifecycle ---

def test_queue_add_returns_stable_ids():
    model = DownloadQueueModel()
    a = model.add(_item("BV1", "A"))
    b = model.add(_item("BV2", "B"))
    assert a == 0 and b == 1
    assert [r.title for r in model.rows()] == ["A", "B"]


def test_queue_recovery_preserves_options_and_directory(tmp_path):
    model = DownloadQueueModel()
    did = model.add(_item(), "/original/output")
    model.set_progress(did, 0.42, "下载视频流", 1024, 20)
    completed = model.add(_item("BV2"))
    model.mark_done(completed, _outcome())
    path = tmp_path / "tasks.json"
    model.save(path)
    recovered = DownloadQueueModel()
    recovered.restore(path)
    row = recovered.get(did)
    assert (row.state, row.pct, row.output_dir) == ("paused", 42, "/original/output")
    assert row.speed_bps == 0 and row.eta_seconds is None
    assert row.item.selected_video_codec == 7
    assert recovered.get(completed).state == "done"
    assert recovered.add(_item()) > completed


def test_active_task_cannot_be_deleted_and_pause_survives_progress():
    model = DownloadQueueModel()
    did = model.add(_item())
    worker = MagicMock()
    model.register_worker(did, worker)
    model.delete(did)
    assert model.get(did) is not None
    model.cancel(did)
    worker.cancel.assert_called_once()
    model.set_progress(did, 0.5, "下载中")
    assert model.get(did).state == "pausing"
    model.mark_cancelled(did)
    assert model.get(did).state == "paused"
    assert not model.has_active


def test_queue_cursor_actions_and_metrics_at_terminal_sizes(tmp_path):
    from bilibili_downloader.tui.app import BiliFlowTUI
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueue

    async def run():
        for size in ((80, 24), (140, 44)):
            p1, p2, p3 = _config_patches(tmp_path)
            with p1, p2, p3:
                app = BiliFlowTUI()
                async with app.run_test(size=size) as pilot:
                    did = app._model.add(_item())
                    worker = MagicMock()
                    app._model.register_worker(did, worker)
                    app._model.set_progress(did, 0.4, "正在下载视频流", 2097152, 65)
                    app._refresh_queue()
                    app._show_workspace("tasks")
                    table = app.query_one(DownloadQueue)
                    await pilot.pause()
                    table.move_cursor(row=table.get_row_index(str(did)))
                    table.focus()
                    await pilot.pause()
                    assert table._cursor_download_id() == did
                    assert table._metrics(app._model.get(did)) == ("2.0 MiB/s", "01:05")
                    await pilot.press("c")
                    await pilot.pause()
                    worker.cancel.assert_called_once()
                    assert app._model.get(did).state == "pausing"
                    assert table.region.width <= size[0]
                    app.save_screenshot(str(tmp_path / f"queue-{size[0]}.svg"))

    asyncio.run(run())


def test_queue_mark_done_partial_when_warnings():
    model = DownloadQueueModel()
    did = model.add(_item())
    model.mark_done(did, DownloadOutcome(video_path="/x", warnings=["缺字幕"]))
    row = model.get(did)
    assert row.state == "partial"
    assert row.status == "部分完成"
    assert row.pct == 100


def test_queue_delete_preserves_other_ids():
    model = DownloadQueueModel()
    a = model.add(_item("BV1", "A"))
    b = model.add(_item("BV2", "B"))
    c = model.add(_item("BV3", "C"))
    model.delete(b)
    ids = [r.download_id for r in model.rows()]
    assert ids == [a, c]
    assert model.get(b) is None
    assert model.get_item(a) is not None


def test_queue_retry_then_mark_failed():
    model = DownloadQueueModel()
    did = model.add(_item())
    model.mark_failed(did, "boom")
    model.mark_retry(did)
    row = model.get(did)
    assert row.state == "retry"
    assert row.pct == 0
    model.mark_failed(did, "boom2")
    assert model.get(did).state == "failed"


def test_queue_add_error_row_has_no_worker():
    model = DownloadQueueModel()
    did = model.add_error("无法解析 BVxxx")
    row = model.get(did)
    assert row.state == "error"
    assert row.status == "未加入队列"
    assert model.get_worker(did) is None
    assert model.has_active is False


# --- app integration (Pilot, network monkeypatched) ---

def _config_patches(tmp_path):
    """Patch config + keyring so tests never touch real credentials/files."""
    return (
        patch("bilibili_downloader.utils.config.DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        patch(
            "bilibili_downloader.utils.config._load_sessdata_from_keyring", return_value=""
        ),
        patch(
            "bilibili_downloader.utils.config._save_sessdata_to_keyring", return_value=True
        ),
    )


def test_app_launches_and_mounts_three_regions(tmp_path):
    from bilibili_downloader.tui.app import BiliFlowTUI
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueue
    from bilibili_downloader.tui.widgets.sidebar import NavSidebar

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3:
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 44)) as pilot:
                await pilot.pause()
                assert app.query_one(NavSidebar) is not None
                assert app.query_one(DownloadQueue) is not None
                assert app.state is not None
                assert app.state.login.label == "未登录"

    asyncio.run(run())


def test_login_status_result_updates_sidebar_from_client_nav_shape(tmp_path):
    """get_nav_info returns the inner data mapping, not an API envelope."""
    from bilibili_downloader.tui import messages
    from bilibili_downloader.tui.app import BiliFlowTUI

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3:
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 44)) as pilot:
                await pilot.pause()
                app.state.login_request_id = 1
                app._on_login_status(
                    messages.LoginStatusResult(
                        1,
                        {"isLogin": True, "uname": "扫码用户", "mid": 123456},
                    )
                )
                assert app.state.login.is_login is True
                assert app.state.login.label == "已登录：扫码用户 (123456)"
                assert str(app._sidebar().query_one("#login-btn").label) == (
                    "已登录：扫码用户 (123456)"
                )

    asyncio.run(run())


def test_resolve_to_download_lifecycle(tmp_path):
    """Resolve → enqueue → download → queue shows 完成."""
    from bilibili_downloader.tui.app import BiliFlowTUI
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueue

    fake_info = VideoInfo(bvid="BV1GJ411x7h7", cid=1, title="TUI 视频", duration=120, author="UP")
    svc = MagicMock()
    svc.download.return_value = _outcome()

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3, patch(
            "bilibili_downloader.core.batch.BatchResolver.resolve_one", return_value=fake_info
        ), patch(
            "bilibili_downloader.api.client.BilibiliAPIClient.get_play_url",
            side_effect=RuntimeError("no net"),
        ), patch(
            "bilibili_downloader.core.download_service.DownloadService", return_value=svc
        ):
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 44)) as pilot:
                await pilot.pause()
                app.query_one("#url-input").value = "BV1GJ411x7h7"
                app._start_resolve()
                await pilot.pause(delay=0.3)
                app._start_download()
                await pilot.pause(delay=0.4)
                rows = app.query_one(DownloadQueue).model.rows()
                assert len(rows) == 1
                assert rows[0].state == "done"
                assert rows[0].status == "完成"

    asyncio.run(run())


def test_concurrency_semaphore_caps_inflight(tmp_path):
    """With max=2, enqueueing 5 never runs more than 2 concurrently."""
    from bilibili_downloader.tui.app import BiliFlowTUI

    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_download(item, cb):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1
        return _outcome()

    svc = MagicMock()
    svc.download.side_effect = slow_download

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3, patch(
            "bilibili_downloader.core.batch.BatchResolver.resolve_one",
            return_value=VideoInfo(bvid="BV", cid=1, title="t", duration=1),
        ), patch(
            "bilibili_downloader.api.client.BilibiliAPIClient.get_play_url", side_effect=RuntimeError
        ), patch(
            "bilibili_downloader.core.download_service.DownloadService", return_value=svc
        ):
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 44)) as pilot:
                await pilot.pause()
                app.state.settings.max_concurrent_downloads = 2
                for i in range(5):
                    app.enqueue_download(_item(f"BV{i}", f"v{i}"))
                await pilot.pause(delay=1.0)

    asyncio.run(run())
    assert peak <= 2, f"concurrency exceeded cap (peak={peak})"


def test_progress_ticks_update_row_in_place_without_rebuild(tmp_path):
    """Per-tick DownloadProgress repaints one row, never rebuilds the table.

    Guards the incremental ``refresh_row_by_id`` path: after several progress
    ticks for the same download the row count must stay 1 (no spurious
    add/clear churn) while the 进度 / 状态 cells reflect the latest state.
    """
    from bilibili_downloader.tui import messages
    from bilibili_downloader.tui.app import BiliFlowTUI
    from bilibili_downloader.tui.widgets.download_queue import DownloadQueue

    async def run():
        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3:
            app = BiliFlowTUI()
            async with app.run_test(size=(120, 44)) as pilot:
                await pilot.pause()
                table = app.query_one(DownloadQueue)
                # Add a row to the model directly and render it, WITHOUT
                # spawning a real worker (which would race these ticks with its
                # own lifecycle messages). We are exercising the App's
                # progress-handler → incremental-render path only.
                did = app.state.queue.add(_item("BV1", "进度测试"))
                table.refresh_model()
                await pilot.pause()
                assert table.row_count == 1

                # Drive several progress ticks for the same download.
                for pct in (0.10, 0.25, 0.50, 0.80):
                    app.post_message(messages.DownloadProgress(did, pct, "下载中"))
                    await pilot.pause()

                # Row count unchanged — no full-rebuild churn.
                assert table.row_count == 1
                row = table.get_row_at(0)
                assert "下载中" in str(row[table._COL_STATUS])
                assert "80" in row[table._COL_PROGRESS]

    asyncio.run(run())


# --- file browser screen ---

def test_file_browser_picks_file(tmp_path):
    """File mode: descending into a dir and selecting a pickable file dismisses
    with that Path; non-matching names stay listed but aren't selectable."""
    import asyncio

    from bilibili_downloader.tui.screens import FileBrowserScreen, _json_filter

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "index.json").write_text("{}", encoding="utf-8")
    (sub / "ignore.txt").write_text("x", encoding="utf-8")

    async def run():
        result = {}

        def on_done(path):
            result["path"] = path

        from bilibili_downloader.tui.app import BiliFlowTUI

        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3:
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 40)) as pilot:
                app.push_screen(
                    FileBrowserScreen(
                        start=tmp_path, select_file=True, name_filter=_json_filter,
                        title="选 json",
                    ),
                    on_done,
                )
                await pilot.pause()
                browser = app.screen
                assert browser is not None
                # Open the subdir.
                browser._dir = sub
                browser._reload()
                await pilot.pause()
                # Cursor lands on first row (index.json, sorted before ignore.txt
                # because the filter still lists it but it's not pickable).
                browser._activate(str(sub / "index.json"))
                await pilot.pause()
        assert result.get("path") == sub / "index.json"

    asyncio.run(run())


def test_file_browser_directory_mode_dismisses_dir(tmp_path):
    """Directory mode: 选择当前目录 dismisses with the current directory Path."""
    import asyncio

    from bilibili_downloader.tui.screens import FileBrowserScreen

    async def run():
        result = {}

        def on_done(path):
            result["path"] = path

        from bilibili_downloader.tui.app import BiliFlowTUI

        p1, p2, p3 = _config_patches(tmp_path)
        with p1, p2, p3:
            app = BiliFlowTUI()
            async with app.run_test(size=(110, 40)) as pilot:
                app.push_screen(
                    FileBrowserScreen(start=tmp_path, select_file=False, title="选目录"),
                    on_done,
                )
                await pilot.pause()
                app.screen.query_one("#fb-select").press()
                await pilot.pause()
        assert result.get("path") == tmp_path

    asyncio.run(run())


# --- default-behavior guard ---

def test_bare_invocation_launches_gui_not_tui():
    """`main([])` must call _launch_gui, never _launch_tui (plan requirement)."""
    from bilibili_downloader import __main__ as cli

    called = {}
    with patch.object(cli, "_launch_gui", lambda: called.__setitem__("gui", True)), patch.object(
        cli, "_launch_tui", lambda: called.__setitem__("tui", True)
    ), patch("sys.argv", ["bilibili-downloader"]):
        cli.main()
    assert called.get("gui") is True
    assert "tui" not in called
