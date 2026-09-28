"""Creator indexing and raw archive regression tests."""

import json
from pathlib import Path

import pytest

from bilibili_downloader.core.archive import (
    ArtifactPaths,
    CommentArchiveDownloader,
    download_cover,
)
from bilibili_downloader.core.creator import (
    CreatorIndexService,
    load_creator_index,
    parse_creator_mid,
    save_creator_index,
)
from bilibili_downloader.core.download_service import DownloadService
from bilibili_downloader.core.downloader import StreamDownloader
from bilibili_downloader.core.models import DownloadItem, VideoInfo, VideoPage


class FakeCreatorAPI:
    def get_creator_profile(self, mid):
        return {"code": 0, "data": {"mid": mid, "name": "测试 UP"}}

    def get_creator_medialist_page(self, mid, cursor, page_size):
        entries = {
            0: [
                {"bv_id": "BV0000000001", "id": 1, "title": "One", "duration": 62},
                {"bv_id": "BV0000000002", "id": 2, "title": "Two", "duration": 123},
            ],
            2: [{"bv_id": "BV0000000003", "id": 3, "title": "Three"}],
        }[cursor]
        return {
            "code": 0,
            "data": {
                "total_count": 3,
                "has_more": cursor == 0,
                "media_list": entries,
            },
        }


def test_creator_index_fetches_every_page_and_round_trips(tmp_path):
    index = CreatorIndexService(
        FakeCreatorAPI(), page_size=2, request_interval=0
    ).fetch("123")

    assert index.mid == 123
    assert index.name == "测试 UP"
    assert [video.bvid for video in index.videos] == [
        "BV0000000001", "BV0000000002", "BV0000000003"
    ]
    assert index.videos[0].duration == 62
    assert len(index.raw_pages) == 2

    path = tmp_path / "index.json"
    save_creator_index(index, path)
    assert load_creator_index(path) == index


def test_creator_index_randomizes_each_page_interval(monkeypatch):
    samples = []

    def sample_interval(lower, upper):
        samples.append((lower, upper))
        return 0

    monkeypatch.setattr(
        "bilibili_downloader.core.creator.random.uniform", sample_interval
    )
    CreatorIndexService(FakeCreatorAPI(), page_size=2).fetch("123")

    assert samples == [(0.0, 10.0)]


def test_creator_index_checkpoints_and_resumes_from_cursor():
    checkpoints = []
    first_api = FakeCreatorAPI()
    CreatorIndexService(
        first_api, page_size=2, request_interval=0
    ).fetch(
        "123",
        checkpoint_callback=checkpoints.append,
        checkpoint_every=1,
    )
    partial = checkpoints[0]

    assert partial.complete is False
    assert partial.next_cursor == 2
    assert [entry.bvid for entry in partial.videos] == [
        "BV0000000001",
        "BV0000000002",
    ]

    class ResumeAPI(FakeCreatorAPI):
        def __init__(self):
            self.cursors = []

        def get_creator_medialist_page(self, mid, cursor, page_size):
            self.cursors.append(cursor)
            return super().get_creator_medialist_page(mid, cursor, page_size)

    resume_api = ResumeAPI()
    completed = CreatorIndexService(
        resume_api, page_size=2, request_interval=0
    ).fetch("123", resume_index=partial)

    assert resume_api.cursors == [2]
    assert completed.complete is True
    assert completed.next_cursor == 0
    assert len(completed.videos) == 3


def test_creator_index_retries_http_412(monkeypatch):
    import httpx

    api = FakeCreatorAPI()
    original = api.get_creator_medialist_page
    attempts = 0

    def flaky_page(mid, cursor, page_size):
        nonlocal attempts
        if cursor == 2 and attempts == 0:
            attempts += 1
            request = httpx.Request("GET", "https://api.bilibili.com/test")
            response = httpx.Response(412, request=request)
            raise httpx.HTTPStatusError(
                "blocked", request=request, response=response
            )
        return original(mid, cursor, page_size)

    monkeypatch.setattr(api, "get_creator_medialist_page", flaky_page)
    statuses = []
    index = CreatorIndexService(
        api,
        page_size=2,
        request_interval=0,
        retry_delays=(0,),
    ).fetch("123", status_callback=statuses.append)

    assert len(index.videos) == 3
    assert attempts == 1
    assert statuses == ["第 2 页触发 B站风控，0 秒后重试"]


def test_creator_index_retries_premature_empty_page_and_saves_latest_checkpoint():
    class PrematureEmptyAPI(FakeCreatorAPI):
        def __init__(self):
            self.page_two_attempts = 0

        def get_creator_medialist_page(self, mid, cursor, page_size):
            if cursor == 2:
                self.page_two_attempts += 1
                return {
                    "code": 0,
                    "data": {
                        "total_count": 3,
                        "has_more": False,
                        "media_list": [],
                    },
                }
            return super().get_creator_medialist_page(mid, cursor, page_size)

    api = PrematureEmptyAPI()
    checkpoints = []
    statuses = []

    with pytest.raises(RuntimeError, match="连续返回空数据"):
        CreatorIndexService(
            api,
            page_size=2,
            request_interval=0,
            retry_delays=(0, 0),
        ).fetch(
            "123",
            checkpoint_callback=checkpoints.append,
            status_callback=statuses.append,
        )

    assert api.page_two_attempts == 3
    assert len(statuses) == 2
    assert checkpoints[-1].complete is False
    assert checkpoints[-1].next_cursor == 2
    assert [video.bvid for video in checkpoints[-1].videos] == [
        "BV0000000001",
        "BV0000000002",
    ]


def test_cover_download_upgrades_trusted_http_url(monkeypatch, tmp_path):
    from io import BytesIO

    import httpx
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    requested_urls = []

    def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=buffer.getvalue(), request=request)

    real_client = httpx.Client
    monkeypatch.setattr(
        "bilibili_downloader.core.archive.httpx.Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    output = tmp_path / "cover.jpg"
    download_cover("http://i0.hdslb.com/cover.png", output)

    assert requested_urls == ["https://i0.hdslb.com/cover.png"]
    with Image.open(output) as image:
        assert image.format == "JPEG"


def test_parse_creator_mid_accepts_uid_and_space_url():
    assert parse_creator_mid("42") == 42
    assert parse_creator_mid("https://space.bilibili.com/98765/video") == 98765


class FakeCommentAPI:
    def __init__(self):
        self.reply_calls = []

    def get_comment_page(self, aid, page, page_size):
        replies = (
            [{"rpid": 10, "rcount": 3}, {"rpid": 20, "rcount": 0}]
            if page == 1 else [{"rpid": 30, "rcount": 0}]
        )
        return {
            "code": 0,
            "data": {
                "page": {"count": 3},
                "replies": replies,
                "top": {"upper": {"rpid": 40, "root": 0, "rcount": 1}},
            },
        }

    def get_comment_replies(self, aid, root, page, page_size):
        self.reply_calls.append((root, page))
        total = 1 if root == 40 else 3
        replies = [
            {"rpid": page * 100 + offset}
            for offset in range(1 if root == 40 else (2 if page == 1 else 1))
        ]
        return {"code": 0, "data": {"page": {"count": total}, "replies": replies}}


def test_comment_archive_preserves_all_raw_root_and_nested_pages(tmp_path):
    api = FakeCommentAPI()
    path = tmp_path / "comments.json"

    CommentArchiveDownloader(api, request_interval=0, page_size=2).download(
        99, "BV0000000001", path
    )

    archive = json.loads(path.read_text(encoding="utf-8"))
    assert archive["complete"] is True
    assert len(archive["root_pages"]) == 2
    assert len(archive["reply_pages"]["10"]) == 2
    assert len(archive["reply_pages"]["40"]) == 1
    assert api.reply_calls == [(10, 1), (10, 2), (40, 1)]


def _creator_item(**updates):
    info = VideoInfo(
        bvid="BV0000000001",
        aid=99,
        cid=456,
        title="A title",
        pages=[VideoPage(cid=456, page=2, part="Second")],
    )
    values = {
        "video_info": info,
        "creator_mid": 123,
        "creator_name": "测试 UP",
    }
    values.update(updates)
    return DownloadItem(**values)


def test_creator_paths_are_stable_by_bvid_and_cid(tmp_path):
    paths = ArtifactPaths(tmp_path, _creator_item())

    assert paths.media_path == (
        tmp_path / "测试 UP_123" / "[BV0000000001] A title"
        / "P02 [456] Second.mp4"
    )


def test_creator_paths_reuse_identifiers_after_titles_change(tmp_path):
    old_dir = tmp_path / "测试 UP_123" / "[BV0000000001] Old title"
    old_dir.mkdir(parents=True)
    old_media = old_dir / "P02 [456] Old part.mp4"
    old_media.write_bytes(b"complete")

    assert ArtifactPaths(tmp_path, _creator_item()).media_path == old_media


def test_sidecar_names_keep_dots_in_video_title(tmp_path):
    item = _creator_item(creator_mid=0, creator_name="")
    paths = ArtifactPaths(tmp_path, item).sidecars(tmp_path / "release.v2.mp4")

    assert paths["metadata"].name == "release.v2.info.json"
    assert paths["danmaku_xml"].name == "release.v2.danmaku.xml"


def test_existing_creator_media_is_skipped_without_api_request(tmp_path):
    item = _creator_item()
    target = ArtifactPaths(tmp_path, item).media_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"complete")

    downloader = StreamDownloader(object(), str(tmp_path))
    result = downloader.download(item, lambda *_args: None)

    assert result == str(target)
    assert downloader.last_download_skipped is True


def test_rerun_skips_media_but_fills_missing_metadata(tmp_path):
    item = _creator_item(download_metadata=True)
    target = ArtifactPaths(tmp_path, item).media_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"complete")

    class API:
        def get_video_info_raw(self, bvid):
            return {"code": 0, "data": {"bvid": bvid, "title": "A title"}}

    outcome = DownloadService(API(), str(tmp_path)).download(
        item, lambda *_args: None
    )

    assert outcome.skipped_media is True
    assert Path(outcome.metadata_path).is_file()


def test_incomplete_comment_checkpoint_is_refetched(tmp_path):
    item = _creator_item(download_comments=True)
    target = ArtifactPaths(tmp_path, item).media_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"complete")
    comments = ArtifactPaths(tmp_path, item).sidecars(target)["comments"]
    comments.write_text('{"complete": false}', encoding="utf-8")

    class API:
        calls = 0

        def get_comment_page(self, aid, page, page_size):
            self.calls += 1
            return {"code": 0, "data": {"page": {"count": 0}, "replies": []}}

    api = API()
    outcome = DownloadService(api, str(tmp_path)).download(item, lambda *_args: None)

    assert api.calls == 1
    assert outcome.comments_path == str(comments)
    assert json.loads(comments.read_text(encoding="utf-8"))["complete"] is True
