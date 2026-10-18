"""Tests for CLI page and companion-file options."""

import argparse
import threading
import time

import pytest

from bilibili_downloader import __main__ as cli
from bilibili_downloader.core.models import (
    AppSettings,
    CreatorVideoEntry,
    CreatorVideoIndex,
    DownloadOutcome,
    VideoInfo,
    VideoPage,
)


def test_cli_download_expands_all_pages_and_forwards_options(monkeypatch, tmp_path):
    info = VideoInfo(
        bvid="BV1GJ411x7h7",
        cid=11,
        title="Series",
        pages=[
            VideoPage(cid=11, page=1, part="One"),
            VideoPage(cid=22, page=2, part="Two"),
        ],
    )
    captured = []

    class FakeClient:
        def __init__(self, sessdata=None):
            self.sessdata = sessdata

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            assert output_dir == str(tmp_path)

        def download(self, item, callback):
            captured.append(item)
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.api.client.BilibiliAPIClient", FakeClient
    )
    monkeypatch.setattr(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one",
        lambda self, source: info,
    )
    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    monkeypatch.setattr(
        "bilibili_downloader.utils.config.ConfigManager.load",
        lambda self: AppSettings(output_dir=str(tmp_path)),
    )
    args = argparse.Namespace(
        source="BV1GJ411x7h7",
        quality=80,
        output=None,
        danmaku=True,
        subtitle=True,
        codec=7,
        page="all",
        subtitle_language="en-US",
    )

    cli._cli_download(args)

    assert [item.video_info.cid for item in captured] == [11, 22]
    assert all(item.selected_video_codec == 7 for item in captured)
    assert all(item.download_danmaku and item.download_subtitle for item in captured)
    assert all(item.selected_subtitle_lan == "en-US" for item in captured)


def _make_index(n):
    return CreatorVideoIndex(
        mid=1, name="UP", source="1", fetched_at="2026-07-23T00:00:00+00:00",
        total=n, reported_total=n,
        videos=[CreatorVideoEntry(bvid=f"BV{i:012d}", title=f"v{i}") for i in range(n)],
    )


def _batch_args(tmp_path, jobs=None, **extra):
    base = dict(
        quality=80, output=None, codec=7, subtitle_language="zh-Hans",
        jobs=jobs, refresh_sidecars=False,
    )
    base.update(extra)
    return argparse.Namespace(**base)


def test_creator_batch_downloads_concurrently(monkeypatch, tmp_path):
    """--jobs actually runs downloads in parallel (not sequentially)."""
    index = _make_index(4)
    active = 0
    active_lock = threading.Lock()
    peak = 0

    class FakeClient:
        def __init__(self, sessdata=None):
            pass

        def get_video_info(self, bvid):
            return VideoInfo(bvid=bvid, cid=1, title=bvid)

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            pass

        def download(self, item, callback):
            nonlocal active, peak
            with active_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)  # hold the slot so concurrency is observable
            with active_lock:
                active -= 1
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    args = _batch_args(tmp_path, jobs=4)
    cli._download_creator_entries(FakeClient(), AppSettings(output_dir=str(tmp_path)),
                                  tmp_path, index, index.videos, args)

    assert peak >= 2, f"downloads did not run concurrently (peak={peak})"


def test_creator_batch_isolates_failures_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    """One failing entry must not abort the others; failures exit 1."""
    index = _make_index(3)

    class FakeClient:
        def __init__(self, sessdata=None):
            pass

        def get_video_info(self, bvid):
            return VideoInfo(bvid=bvid, cid=1, title=bvid)

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            pass

        def download(self, item, callback):
            if item.video_info.bvid == "BV000000000001":
                raise RuntimeError("boom")
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    args = _batch_args(tmp_path, jobs=2)
    with pytest.raises(SystemExit) as exc_info:
        cli._download_creator_entries(FakeClient(), AppSettings(output_dir=str(tmp_path)),
                                      tmp_path, index, index.videos, args)
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    # Two succeeded, one failed and is listed in the failure summary.
    assert "✗ BV000000000001" in out
    assert "1 个任务失败" in out


def test_cli_main_without_command_prints_help_and_never_launches_gui(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_configure_logging", lambda **kw: None)
    monkeypatch.setattr(cli, "_launch_gui", lambda: pytest.fail("GUI launched"))
    with pytest.raises(SystemExit) as exc_info:
        cli.cli_main([])
    assert exc_info.value.code == 2
    out = capsys.readouterr().out
    assert "batch" in out and "login" in out and "logout" in out


def test_cli_main_has_no_tui_subcommand(monkeypatch):
    monkeypatch.setattr(cli, "_configure_logging", lambda **kw: None)
    with pytest.raises(SystemExit) as exc_info:
        cli.cli_main(["tui"])
    assert exc_info.value.code == 2


def _install_batch_fakes(monkeypatch, tmp_path, captured):
    class FakeClient:
        def __init__(self, sessdata=None):
            self.sessdata = sessdata

        def get_video_info(self, bvid):
            pages = [VideoPage(cid=1, page=1, part="A"), VideoPage(cid=2, page=2, part="B")]
            return VideoInfo(bvid=bvid, cid=1, title=f"title-{bvid}", pages=pages)

        def close(self):
            pass

    class FakeService:
        def __init__(self, client, output_dir, ffmpeg_path=None):
            assert output_dir == str(tmp_path)

        def download(self, item, callback):
            captured.append(item)
            return DownloadOutcome(video_path=str(tmp_path / item.filename))

    monkeypatch.setattr("bilibili_downloader.api.client.BilibiliAPIClient", FakeClient)
    monkeypatch.setattr(
        "bilibili_downloader.core.download_service.DownloadService", FakeService
    )
    monkeypatch.setattr(
        "bilibili_downloader.utils.config.ConfigManager.load",
        lambda self: AppSettings(output_dir=str(tmp_path), sessdata="token"),
    )
    monkeypatch.setattr(cli, "_configure_logging", lambda **kw: None)


def test_cli_batch_reads_args_and_files_dedupes_and_skips_invalid(
    monkeypatch, tmp_path, capsys
):
    captured = []
    _install_batch_fakes(monkeypatch, tmp_path, captured)
    links = tmp_path / "links.txt"
    links.write_text(
        "https://www.bilibili.com/video/BV1GJ411x7h7\nnot a link\n\nBV1xx411c7mD\n",
        encoding="utf-8",
    )

    cli.cli_main(["batch", "BV1GJ411x7h7", "--file", str(links), "-j", "1"])

    out = capsys.readouterr().out
    assert "跳过无法识别的输入：not a link" in out
    bvids = [item.video_info.bvid for item in captured]
    # BV1GJ411x7h7 appears twice in the input but downloads once (both parts).
    assert sorted(set(bvids)) == ["BV1GJ411x7h7", "BV1xx411c7mD"]
    assert len(captured) == 4
    assert "[2/2] ✓" in out


def test_cli_batch_first_page_only(monkeypatch, tmp_path):
    captured = []
    _install_batch_fakes(monkeypatch, tmp_path, captured)
    cli.cli_main(["batch", "BV1GJ411x7h7", "--first-page"])
    assert [item.video_info.cid for item in captured] == [1]


def test_cli_batch_without_inputs_exits_nonzero(monkeypatch, tmp_path, capsys):
    _install_batch_fakes(monkeypatch, tmp_path, [])
    monkeypatch.setattr("sys.stdin", type("TTY", (), {"isatty": lambda self: True})())
    with pytest.raises(SystemExit) as exc_info:
        cli.cli_main(["batch"])
    assert exc_info.value.code == 1
    assert "没有可下载的链接" in capsys.readouterr().out


def test_logout_clears_saved_sessdata(monkeypatch, tmp_path, capsys):
    from bilibili_downloader.cli import login as cli_login_module
    from bilibili_downloader.utils.config import ConfigManager

    saved = []
    monkeypatch.setattr(
        ConfigManager, "load", lambda self: AppSettings(sessdata="token")
    )
    monkeypatch.setattr(ConfigManager, "save", lambda self, settings: saved.append(settings))
    cli_login_module.cli_logout()
    assert saved and saved[0].sessdata == ""
    assert "已退出登录" in capsys.readouterr().out


def test_login_status_reports_logged_in_user(monkeypatch, capsys):
    from bilibili_downloader.cli import login as cli_login_module
    from bilibili_downloader.utils.config import ConfigManager

    class FakeClient:
        def __init__(self, sessdata=None):
            assert sessdata == "token"

        def get_nav_info(self):
            return {"isLogin": True, "uname": "测试用户", "mid": 42, "vipStatus": 1}

        def close(self):
            pass

    monkeypatch.setattr(ConfigManager, "load", lambda self: AppSettings(sessdata="token"))
    monkeypatch.setattr(cli_login_module, "BilibiliAPIClient", FakeClient)
    cli_login_module.cli_login(argparse.Namespace(status=True, sessdata=None))
    assert "已登录：测试用户（UID 42，大会员）" in capsys.readouterr().out
    # The success message after a fresh login reads the same unwrapped mapping.
    assert cli_login_module._whoami("token") == ("测试用户", "42")


def test_login_status_without_credentials_exits_nonzero(monkeypatch, capsys):
    from bilibili_downloader.cli import login as cli_login_module
    from bilibili_downloader.utils.config import ConfigManager

    monkeypatch.setattr(ConfigManager, "load", lambda self: AppSettings(sessdata=""))
    with pytest.raises(SystemExit) as exc_info:
        cli_login_module.cli_login(argparse.Namespace(status=True, sessdata=None))
    assert exc_info.value.code == 1
    assert "当前未登录" in capsys.readouterr().out
