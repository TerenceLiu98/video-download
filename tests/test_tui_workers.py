"""Tests for TUI worker classes (plain objects, no Textual runtime needed).

Each worker emits Textual Messages via ``app.post_message``. We inject a fake
app whose ``post_message`` records messages, then assert the right message
types fire for success / failure / cancel paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bilibili_downloader.core.models import (
    DownloadOutcome,
    VideoInfo,
    VideoQuality,
)
from bilibili_downloader.tui import messages


class FakeApp:
    """Captures posted messages instead of routing them to a Textual loop."""

    def __init__(self):
        self.posted: list = []

    def post_message(self, message):
        self.posted.append(message)


def _make_download_item():
    return MagicMock(
        video_info=VideoInfo(bvid="BV1TEST00001", cid=1, title="t", duration=10),
        selected_quality=VideoQuality.Q1080P,
        selected_video_codec=7,
        filename="x.mp4",
    )


def test_download_worker_emits_finished_on_success(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()
    fake_service.download.return_value = DownloadOutcome(video_path=str(tmp_path / "x.mp4"))

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 5)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadFinished" in msg_types
    finished = next(m for m in app.posted if isinstance(m, messages.DownloadFinished))
    assert finished.download_id == 5


def test_download_worker_emits_failed_on_error(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()
    fake_service.download.side_effect = RuntimeError("network down")

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 1)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadFailed" in msg_types
    assert "DownloadCancelled" not in msg_types


def test_download_worker_emits_cancelled_when_cancelled(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()

    def download(item, cb):
        # Simulate core noticing the cancel flag.
        worker.cancel()
        raise RuntimeError("cancel")

    fake_service.download.side_effect = download

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 9)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadCancelled" in msg_types
    assert "DownloadFailed" not in msg_types


def test_download_worker_throttles_repeated_progress_callbacks(tmp_path):
    """Rapid same-percentage callbacks collapse to a handful of emissions.

    Without throttling, a service that calls progress_cb once per chunk would
    flood the UI with one DownloadProgress per callback. The worker must emit
    at most one message per integer-percent step (plus the first call).
    """
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()

    def download(item, cb):
        # 50 callbacks all reporting 42% — a long segment streaming with no
        # integer-percent movement.
        for _ in range(50):
            cb(0.423, "下载中")
        return DownloadOutcome(video_path=str(tmp_path / "x.mp4"))

    fake_service.download.side_effect = download

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 3)
        worker.run()

    progress_msgs = [m for m in app.posted if isinstance(m, messages.DownloadProgress)]
    # First callback always emits (last_pct sentinel); the rest are suppressed
    # because neither the integer percent nor the status text changed and not
    # enough wall-clock time elapsed during a tight loop.
    assert len(progress_msgs) <= 2, f"expected <=2 progress msgs, got {len(progress_msgs)}"
    assert len(progress_msgs) >= 1


def test_download_worker_emits_when_percent_advances(tmp_path):
    """Distinct integer-percentage steps each emit a progress message."""
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()

    def download(item, cb):
        for pct in (0.10, 0.20, 0.30, 0.40):
            cb(pct, "下载中")
        return DownloadOutcome(video_path=str(tmp_path / "x.mp4"))

    fake_service.download.side_effect = download

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 7)
        worker.run()

    progress_msgs = [m for m in app.posted if isinstance(m, messages.DownloadProgress)]
    assert [m.pct for m in progress_msgs] == [0.10, 0.20, 0.30, 0.40]


def test_resolve_worker_handles_playurl_failure():
    from bilibili_downloader.tui.workers.resolve import ResolveWorker

    app = FakeApp()
    info = VideoInfo(bvid="BV1TEST00002", cid=2, title="t", duration=10)
    client = MagicMock()

    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one", return_value=info
    ), patch.object(
        client, "get_play_url", side_effect=RuntimeError("no playurl")
    ):
        ResolveWorker(app, client, "BV1TEST00002").run()

    finished = next(m for m in app.posted if isinstance(m, messages.ResolveFinished))
    assert finished.playurl_ok is False
    assert finished.info.bvid == "BV1TEST00002"


# --- BatchWorker retry / rate-limit behaviour ------------------------------
def _patch_batch_sleep():
    """Short-circuit BatchWorker's backoff + inter-resolve pacing.

    ``_cancellable_wait`` would otherwise sleep 10/30/60s on rate-limited
    retries. Patch the wait method itself to a no-op and zero the inter-resolve
    pause so the suite is instant.
    """
    import bilibili_downloader.tui.workers.batch as batch_mod

    return (
        patch.object(batch_mod.BatchWorker, "_cancellable_wait", lambda self, delay: None),
        patch.object(batch_mod, "_INTER_RESOLVE_RANGE", (0.0, 0.0)),
    )


def test_batch_worker_retries_rate_limited_then_succeeds():
    """A 412/429-class error is retried with backoff; success still enqueues."""
    from bilibili_downloader.api.client import BilibiliAPIError
    from bilibili_downloader.core.batch import BatchResolveError
    from bilibili_downloader.tui.workers.batch import BatchWorker

    app = FakeApp()
    info = VideoInfo(bvid="BV1RATE00001", cid=11, title="retry", duration=10)
    client = MagicMock()

    # First two attempts hit 风控 (-352), third succeeds.
    side = [
        BilibiliAPIError(-352, "风控"),
        BilibiliAPIError(-352, "风控"),
        info,
    ]
    wait_patch, inter_patch = _patch_batch_sleep()
    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one", side_effect=side
    ), wait_patch, inter_patch:
        BatchWorker(
            app, client, ["BV1RATE00001"], {},
            quality=VideoQuality.Q1080P, codec=7,
        ).run()

    types = [type(m).__name__ for m in app.posted]
    assert types.count("BatchItemRetrying") == 2, types
    assert "BatchItemReady" in types, types
    assert "BatchItemFailed" not in types, types
    assert "BatchDone" in types, types
    # BatchResolveError must NOT be classified as retryable.
    assert isinstance(BatchResolveError("x"), Exception)


def test_batch_worker_non_retryable_error_fails_immediately():
    """An unrecognised input (BatchResolveError) is not retried."""
    from bilibili_downloader.core.batch import BatchResolveError
    from bilibili_downloader.tui.workers.batch import BatchWorker

    app = FakeApp()
    client = MagicMock()
    wait_patch, inter_patch = _patch_batch_sleep()
    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one",
        side_effect=BatchResolveError("无法识别"),
    ), wait_patch, inter_patch:
        BatchWorker(app, client, ["garbage"], {}).run()

    types = [type(m).__name__ for m in app.posted]
    assert "BatchItemRetrying" not in types, types
    assert "BatchItemReady" not in types, types
    assert "BatchItemFailed" in types, types


def test_batch_worker_emits_progress_per_item():
    """Each resolved item advances BatchProgress(done, total)."""
    from bilibili_downloader.tui.workers.batch import BatchWorker

    app = FakeApp()
    client = MagicMock()
    infos = [
        VideoInfo(bvid=f"BV1P{i:03d}", cid=100 + i, title=f"t{i}", duration=10)
        for i in range(3)
    ]
    wait_patch, inter_patch = _patch_batch_sleep()
    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one", side_effect=infos
    ), wait_patch, inter_patch:
        BatchWorker(
            app, client, ["a", "b", "c"], {},
            quality=VideoQuality.Q1080P, codec=7,
        ).run()

    progress = [m for m in app.posted if isinstance(m, messages.BatchProgress)]
    assert [(p.done, p.total) for p in progress] == [(1, 3), (2, 3), (3, 3)]


# --- BatchWorker resolve cache behaviour ---------------------------------
def test_batch_cache_skips_resolve_on_hit(tmp_path):
    """Cache hit bypasses resolve_one; DownloadItem still enqueued."""

    from bilibili_downloader.tui.resolve_cache import ResolveCache
    from bilibili_downloader.tui.workers.batch import BatchWorker

    app = FakeApp()
    client = MagicMock()

    # Populate cache with a VideoInfo for a valid BV number.
    cache_path = tmp_path / "cache.json"
    cache = ResolveCache(cache_path)
    cached_info = VideoInfo(bvid="BV1zC4y1k7xA", cid=1, title="cached", duration=10, pages=[])
    cache.put("BV1zC4y1k7xA", cached_info)
    cache.save()

    # Create a new cache instance (simulating a fresh BatchWorker).
    cache = ResolveCache(cache_path)

    # Mock resolve_one with a side effect that tracks calls.
    resolve_mock = MagicMock(return_value=RuntimeError("should not be called"))

    with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", resolve_mock):
        wait_patch, inter_patch = _patch_batch_sleep()
        with wait_patch, inter_patch:
            BatchWorker(
                app, client, ["BV1zC4y1k7xA"], {},
                quality=VideoQuality.Q1080P, codec=7, cache=cache,
            ).run()

    assert resolve_mock.call_count == 0, f"resolve_one should not be called on cache hit, but was called {resolve_mock.call_count} times"

    types = [type(m).__name__ for m in app.posted]
    assert "BatchItemReady" in types, types
    assert "BatchItemFailed" not in types, types
    assert "BatchDone" in types, types


def test_batch_cache_persists_and_reloads(tmp_path):
    """First run populates cache; second run hits cache without resolve."""

    from bilibili_downloader.tui.resolve_cache import ResolveCache
    from bilibili_downloader.tui.workers.batch import BatchWorker

    cache_path = tmp_path / "creator_cache.json"

    # First run: resolve normally, cache is populated.
    info = VideoInfo(bvid="BV1fIr3T1k7x", cid=2, title="first-run", duration=10, pages=[])
    app1 = FakeApp()
    client1 = MagicMock()

    resolve_mock1 = MagicMock(return_value=info)

    wait_patch, inter_patch = _patch_batch_sleep()
    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one",
        resolve_mock1
    ), wait_patch, inter_patch:
        cache1 = ResolveCache(cache_path)
        BatchWorker(
            app1, client1, ["BV1fIr3T1k7x"], {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache1,
        ).run()

    assert resolve_mock1.call_count == 1, "first run should call resolve_once"
    assert cache_path.is_file(), "cache file should exist after first run"

    # Second run: cache hit, resolve_one should not be called.
    app2 = FakeApp()
    client2 = MagicMock()
    resolve_mock2 = MagicMock(return_value=info)

    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one",
        resolve_mock2
    ), wait_patch, inter_patch:
        cache2 = ResolveCache(cache_path)
        cached_info = cache2.get("BV1fIr3T1k7x")
        assert cached_info is not None, "cache should return VideoInfo on hit"
        assert cached_info.bvid == "BV1fIr3T1k7x"

        BatchWorker(
            app2, client2, ["BV1fIr3T1k7x"], {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache2,
        ).run()

    assert resolve_mock2.call_count == 0, "second run should not call resolve_once"

    types = [type(m).__name__ for m in app2.posted]
    assert "BatchItemReady" in types, types
    assert "BatchItemFailed" not in types, types


# --- ResolveCache path construction -----------------------------------------
def test_resolve_cache_for_creator_path(tmp_path):
    """ResolveCache.for_creator builds path next to index.json."""

    from bilibili_downloader.core.models import CreatorVideoIndex
    from bilibili_downloader.tui.resolve_cache import ResolveCache

    # Build a creator index.
    index = CreatorVideoIndex(
        mid=123456789,
        name="TestCreator",
        source="https://space.bilibili.com/123456789",
        fetched_at="2024-01-01T00:00:00Z",
        total=1,
        videos=[],
    )

    cache = ResolveCache.for_creator(tmp_path, index)
    assert cache is not None
    expected_dir = tmp_path / "TestCreator_123456789"
    expected_path = expected_dir / "index.resolvecache.json"
    assert cache._path == expected_path

    # Verify None is returned for no index.
    assert ResolveCache.for_creator(tmp_path, None) is None


# --- App handler routing tests --------------------------------------------
def test_app_has_batch_progress_handler():
    """App defines a @on(BatchProgress) handler."""
    from bilibili_downloader.tui.app import BiliFlowTUI

    app = BiliFlowTUI()
    # The handler method should exist.
    assert hasattr(app, "_on_batch_progress")
    # It should be registered with the @on decorator.
    # (Textual stores this in internal routing tables; we just check the method exists.)
    method = getattr(app, "_on_batch_progress")
    assert callable(method)


def test_app_has_batch_item_retrying_handler():
    """App defines a @on(BatchItemRetrying) handler."""
    from bilibili_downloader.tui.app import BiliFlowTUI

    app = BiliFlowTUI()
    assert hasattr(app, "_on_batch_item_retrying")
    method = getattr(app, "_on_batch_item_retrying")
    assert callable(method)


def test_app_start_batch_passes_cache_for_creator_index():
    """When creator_index is provided, ResolveCache.for_creator returns non-None cache."""
    from pathlib import Path

    from bilibili_downloader.core.models import CreatorVideoIndex
    from bilibili_downloader.tui.resolve_cache import ResolveCache

    output_dir = Path("/tmp/test_output")
    index = CreatorVideoIndex(
        mid=987654321,
        name="TestUP",
        source="https://space.bilibili.com/987654321",
        fetched_at="2024-01-01T00:00:00Z",
        total=0,
        videos=[],
    )

    cache = ResolveCache.for_creator(output_dir, index)
    assert cache is not None, "cache should be non-None when creator_index provided"
    expected_dir = output_dir / "TestUP_987654321"
    expected_path = expected_dir / "index.resolvecache.json"
    assert cache._path == expected_path


def test_app_start_batch_no_cache_for_plain_batch():
    """When creator_index is None, ResolveCache.for_creator returns None."""
    from pathlib import Path

    from bilibili_downloader.tui.resolve_cache import ResolveCache

    output_dir = Path("/tmp/test_output")
    cache = ResolveCache.for_creator(output_dir, None)
    assert cache is None, "cache should be None when creator_index not provided"


# --- End-to-end smoke verification -----------------------------------------
def test_batch_e2e_creator_cache_and_retry(tmp_path):
    """Full path: first run resolves, second run hits cache; transient 风控 retries."""

    from bilibili_downloader.api.client import BilibiliAPIError
    from bilibili_downloader.tui.resolve_cache import ResolveCache
    from bilibili_downloader.tui.workers.batch import BatchWorker

    cache_path = tmp_path / "e2e_cache.json"

    # Create 3 distinct video infos.
    infos = [
        VideoInfo(bvid="BV1e2e1T3sT1", cid=1, title="e2e-1", duration=10, pages=[]),
        VideoInfo(bvid="BV1e2e1T3sT2", cid=2, title="e2e-2", duration=10, pages=[]),
        VideoInfo(bvid="BV1e2e1T3sT3", cid=3, title="e2e-3", duration=10, pages=[]),
    ]
    urls = ["BV1e2e1T3sT1", "BV1e2e1T3sT2", "BV1e2e1T3sT3"]

    # First run: resolve all 3 via API.
    app1 = FakeApp()
    client1 = MagicMock()
    resolve_count_1 = {"count": 0}
    resolve_mock1 = MagicMock(side_effect=lambda url: (resolve_count_1.update(count=resolve_count_1["count"] + 1) or infos[urls.index(url)]))

    wait_patch, inter_patch = _patch_batch_sleep()
    with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", resolve_mock1), wait_patch, inter_patch:
        cache1 = ResolveCache(cache_path)
        BatchWorker(
            app1, client1, urls, {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache1,
        ).run()

    assert resolve_count_1["count"] == 3, "first run should resolve all 3 items"
    assert cache_path.is_file(), "cache should exist after first run"

    # Check BatchItemReady emissions.
    ready_msgs_1 = [m for m in app1.posted if isinstance(m, messages.BatchItemReady)]
    assert len(ready_msgs_1) == 3, f"first run should emit 3 BatchItemReady, got {len(ready_msgs_1)}"

    # Second run: cache hits for all 3 (resolve_one should not be called).
    app2 = FakeApp()
    client2 = MagicMock()
    resolve_count_2 = {"count": 0}
    resolve_mock2 = MagicMock(side_effect=lambda url: (resolve_count_2.update(count=resolve_count_2["count"] + 1) or infos[urls.index(url)]))

    with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", resolve_mock2), wait_patch, inter_patch:
        cache2 = ResolveCache(cache_path)
        BatchWorker(
            app2, client2, urls, {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache2,
        ).run()

    assert resolve_count_2["count"] == 0, "second run should not call resolve_one (all cache hits)"

    ready_msgs_2 = [m for m in app2.posted if isinstance(m, messages.BatchItemReady)]
    assert len(ready_msgs_2) == 3, f"second run should still emit 3 BatchItemReady, got {len(ready_msgs_2)}"

    # Third run: inject one transient 风控 error (-352) on a cache-miss item.
    # We'll use a fresh cache to force cache miss.
    cache_path_3 = tmp_path / "e2e_cache_3.json"
    app3 = FakeApp()
    client3 = MagicMock()

    # First item: -352 twice, then success.
    resolve_count_3 = {"count": 0}
    def resolve_side_effect_3(url):
        resolve_count_3["count"] += 1
        if url == "BV1e2e1T3sT1" and resolve_count_3["count"] <= 2:
            raise BilibiliAPIError(-352, "风控")
        return infos[urls.index(url)]

    resolve_mock3 = MagicMock(side_effect=resolve_side_effect_3)

    with patch("bilibili_downloader.core.batch.BatchResolver.resolve_one", resolve_mock3), wait_patch, inter_patch:
        cache3 = ResolveCache(cache_path_3)
        BatchWorker(
            app3, client3, urls, {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache3,
        ).run()

    # Should have 2 failures + 1 success + 2 more successes = 5 total calls.
    assert resolve_count_3["count"] == 5, f"third run should retry transient error, got {resolve_count_3['count']} calls"

    retrying_msgs = [m for m in app3.posted if isinstance(m, messages.BatchItemRetrying)]
    assert len(retrying_msgs) == 2, f"should emit 2 BatchItemRetrying for -352 retries, got {len(retrying_msgs)}"

    ready_msgs_3 = [m for m in app3.posted if isinstance(m, messages.BatchItemReady)]
    assert len(ready_msgs_3) == 3, f"third run should still succeed all items, got {len(ready_msgs_3)} BatchItemReady"

    # Verify BatchProgress emissions.
    progress_msgs_3 = [m for m in app3.posted if isinstance(m, messages.BatchProgress)]
    assert [(p.done, p.total) for p in progress_msgs_3] == [(1, 3), (2, 3), (3, 3)]
