"""Tests for the ``rebuild-cache`` CLI subcommand.

Covers: basic rebuild, skip of missing/corrupt info.json, merge semantics
across runs, and end-to-end cache-hit behaviour (resolve_one never called).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bilibili_downloader.cli.rebuild_cache import rebuild_cache
from bilibili_downloader.core.creator import creator_directory_name, save_creator_index
from bilibili_downloader.core.models import CreatorVideoEntry, CreatorVideoIndex
from bilibili_downloader.tui.resolve_cache import ResolveCache

NAME = "新华社"
MID = 473837611

# Valid-format bvids (extract_bvid accepts them).
BVIDS = ["BV1ab411c7d2", "BV1zZ4y1A7Kp", "BV1Qs411w7Hm"]


def _raw_view(bvid: str, aid: int, cid: int, title: str) -> dict:
    """A minimal but realistic raw /x/web-interface/view response envelope."""
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "bvid": bvid,
            "aid": aid,
            "cid": cid,
            "title": title,
            "duration": 39,
            "pic": "http://example.com/cover.jpg",
            "owner": {"mid": MID, "name": NAME},
            "pages": [{"cid": cid, "page": 1, "part": title, "duration": 39}],
            "subtitle": {"list": []},
            "pubdate": 1782366101,
        },
    }


def _make_creator_dir(tmp_path: Path, videos: list[CreatorVideoEntry]) -> Path:
    creator_dir = tmp_path / creator_directory_name(NAME, MID)
    creator_dir.mkdir()
    index = CreatorVideoIndex(
        mid=MID, name=NAME, source=str(MID),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        total=len(videos), videos=videos,
    )
    save_creator_index(index, creator_dir / "index.json")
    return creator_dir


def _write_info_json(creator_dir: Path, bvid: str, aid: int, cid: int, title: str) -> None:
    sub = creator_dir / f"[{bvid}] {title}"
    sub.mkdir()
    (sub / "info.json").write_text(
        json.dumps(_raw_view(bvid, aid, cid, title)), encoding="utf-8"
    )


def _entries() -> list[CreatorVideoEntry]:
    return [
        CreatorVideoEntry(bvid=BVIDS[i], aid=i + 1, title=f"v{i+1}", duration=39)
        for i in range(3)
    ]


def test_rebuild_cache_builds_cache_from_info_json(tmp_path):
    entries = _entries()
    creator_dir = _make_creator_dir(tmp_path, entries)
    for i, v in enumerate(entries, start=1):
        _write_info_json(creator_dir, v.bvid, i, 1000 + i, v.title)

    report = rebuild_cache(creator_dir)

    assert report.cached == 3
    assert report.skipped == 0
    assert report.cache_path == creator_dir / "index.resolvecache.json"
    assert report.cache_path.is_file()

    cache = ResolveCache(report.cache_path)
    for i, v in enumerate(entries, start=1):
        info = cache.get(v.bvid)
        assert info is not None, v.bvid
        assert info.cid == 1000 + i
        assert info.bvid == v.bvid
        assert len(info.pages) == 1


def test_rebuild_cache_skips_missing_and_corrupt_info_json(tmp_path):
    entries = _entries()
    creator_dir = _make_creator_dir(tmp_path, entries)
    # Two good subdirs.
    _write_info_json(creator_dir, entries[0].bvid, 1, 1001, entries[0].title)
    _write_info_json(creator_dir, entries[1].bvid, 2, 1002, entries[1].title)
    # A subdir with no info.json.
    (creator_dir / "[BV1NoInfo0000] empty").mkdir()
    # A subdir with a corrupt (non-JSON) info.json.
    bad = creator_dir / "[BV1BadJson000] broken"
    bad.mkdir()
    (bad / "info.json").write_text("{ this is not json", encoding="utf-8")

    report = rebuild_cache(creator_dir)

    assert report.cached == 2
    assert report.skipped == 2  # missing + corrupt
    # The good entries are still cached.
    cache = ResolveCache(report.cache_path)
    assert cache.get(entries[0].bvid).cid == 1001
    assert cache.get(entries[1].bvid).cid == 1002


def test_rebuild_cache_merges_without_clobbering_existing(tmp_path):
    entries = _entries()
    creator_dir = _make_creator_dir(tmp_path, entries)
    # First run: two videos.
    _write_info_json(creator_dir, entries[0].bvid, 1, 1001, entries[0].title)
    _write_info_json(creator_dir, entries[1].bvid, 2, 1002, entries[1].title)
    rebuild_cache(creator_dir)

    # Second run: add the third video's info.json and rebuild again.
    _write_info_json(creator_dir, entries[2].bvid, 3, 1003, entries[2].title)
    report = rebuild_cache(creator_dir)

    assert report.cached == 3  # all three present this run
    cache = ResolveCache(report.cache_path)
    # All three cached; original two keep their cids (merge, not wipe).
    assert cache.get(entries[0].bvid).cid == 1001
    assert cache.get(entries[1].bvid).cid == 1002
    assert cache.get(entries[2].bvid).cid == 1003


def test_rebuilt_cache_hits_zero_resolves_in_batch(tmp_path):
    """After rebuild, the TUI batch path resolves nothing — the whole point."""
    import bilibili_downloader.tui.workers.batch as batch_mod
    from bilibili_downloader.core.models import VideoQuality
    from bilibili_downloader.tui import messages
    from bilibili_downloader.tui.workers.batch import BatchWorker

    class FakeApp:
        def __init__(self):
            self.posted: list = []

        def post_message(self, message):
            self.posted.append(message)

    entries = _entries()
    creator_dir = _make_creator_dir(tmp_path, entries)
    for i, v in enumerate(entries, start=1):
        _write_info_json(creator_dir, v.bvid, i, 1000 + i, v.title)
    rebuild_cache(creator_dir)

    cache = ResolveCache(creator_dir / "index.resolvecache.json")
    app = FakeApp()
    resolve_calls = {"n": 0}

    def fake_resolve(_src):
        resolve_calls["n"] += 1
        raise AssertionError("resolve_one must not be called when cache is populated")

    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one", side_effect=fake_resolve
    ), patch.object(
        batch_mod.BatchWorker, "_cancellable_wait", lambda self, delay: None
    ), patch.object(
        batch_mod, "_INTER_RESOLVE_RANGE", (0.0, 0.0)
    ):
        BatchWorker(
            app, None, list(BVIDS), {},
            quality=VideoQuality.Q1080P, codec=7, cache=cache,
        ).run()

    assert resolve_calls["n"] == 0
    ready = sum(1 for m in app.posted if isinstance(m, messages.BatchItemReady))
    assert ready == 3, f"expected 3 items from cache, got {ready}"


def test_rebuild_cache_requires_index_json(tmp_path):
    import pytest

    bogus = tmp_path / "not_a_creator_dir"
    bogus.mkdir()
    with pytest.raises(SystemExit):
        rebuild_cache(bogus)
