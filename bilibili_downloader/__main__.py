"""Entry point for the Bilibili downloader application."""

import argparse
import logging
import logging.handlers
import sys

from bilibili_downloader.core.models import VideoQuality

logger = logging.getLogger(__name__)


def _configure_logging(*, console: bool = True) -> None:
    """Configure stderr (INFO) + rotating file (DEBUG) logging.

    The file handler lets long-running background jobs (nohup/tmux/systemd)
    keep a durable record even after the terminal closes.

    ``console=False`` is used by the TUI: any stderr write corrupts the
    full-screen terminal, so the TUI logs to file only and surfaces status
    through its own UI instead.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Silence chatty HTTP libraries (httpx logs every request at INFO).
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if console:
        stream = logging.StreamHandler()  # stderr by default
        stream.setLevel(logging.INFO)
        stream.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(stream)

    try:
        log_dir = _default_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "biliflow.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
    except OSError as exc:
        # Logging to a file is best-effort; never block the app on it.
        root.warning("Could not open log file: %s", exc)


def _default_log_dir():
    from pathlib import Path

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "BiliFlow"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "BiliFlow" / "Logs"
    return Path.home() / ".local" / "share" / "biliflow" / "logs"


def main(argv=None):
    """Main entry point. Supports CLI, TUI and (default) GUI modes."""
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        _launch_gui()
    else:
        _dispatch(args)


def cli_main(argv=None):
    """Pure CLI entry point: never launches the GUI or TUI.

    Only needs the headless dependencies (no PySide6 / Textual), so it works
    over SSH, on servers and in the console-only release binary.
    """
    _configure_logging()
    parser = build_parser(prog="biliflow-cli", include_tui=False)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        raise SystemExit(2)
    _dispatch(args)


def build_parser(
    *, prog: str = "bilibili-downloader", include_tui: bool = True
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Bilibili video downloader - GUI, TUI, and CLI modes."
            if include_tui
            else "Bilibili video downloader - command-line mode."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- test subcommand ---
    test_parser = subparsers.add_parser(
        "test",
        help="Fetch video metadata for a BV/AV number or URL",
    )
    test_parser.add_argument(
        "source",
        help="BV/AV number, Bilibili URL, or b23.tv short link",
    )

    # --- download subcommand ---
    download_parser = subparsers.add_parser(
        "download",
        help="Download a video by BV/AV number or URL",
    )
    download_parser.add_argument(
        "source",
        help="BV/AV number, Bilibili URL, or b23.tv short link",
    )
    download_parser.add_argument(
        "--quality", "-q",
        type=int,
        default=VideoQuality.Q1080P,
        choices=[q.value for q in VideoQuality],
        help=f"Video quality code (default: {VideoQuality.Q1080P})",
    )
    download_parser.add_argument(
        "--output", "-o",
        help="Output directory (default: from settings or ./downloads)",
    )
    download_parser.add_argument(
        "--danmaku", "-d",
        action="store_true",
        help="Download danmaku (ASS format)",
    )
    download_parser.add_argument(
        "--subtitle", "-s",
        action="store_true",
        help="Download subtitles (SRT format)",
    )
    download_parser.add_argument(
        "--codec", "-c",
        type=int,
        choices=[7, 12, 13],
        help="Video codec code: 7=AVC, 12=HEVC, 13=AV1 (default: settings)",
    )
    download_parser.add_argument(
        "--page", "-p",
        default="1",
        help="Multi-part page number, or 'all' (default: 1)",
    )
    download_parser.add_argument(
        "--subtitle-language",
        default="zh-Hans",
        help="Preferred Bilibili subtitle language code (default: zh-Hans)",
    )
    _add_archive_options(download_parser, include_media=False)

    creator_parser = subparsers.add_parser(
        "creator",
        help="Fetch an UP creator's complete submission index",
    )
    creator_parser.add_argument("source", help="Creator UID or space.bilibili.com URL")
    creator_parser.add_argument("--index", help="Manifest path (default: creator directory/index.json)")
    creator_selection = creator_parser.add_mutually_exclusive_group()
    creator_selection.add_argument("--download-all", action="store_true")
    creator_selection.add_argument("--bvid", action="append", default=[])
    _add_download_options(creator_parser)
    _add_archive_options(creator_parser)

    batch_parser = subparsers.add_parser(
        "batch",
        help="Download many videos from links given as arguments, a file, or stdin",
    )
    batch_parser.add_argument(
        "sources",
        nargs="*",
        help="BV/AV numbers, Bilibili URLs or b23.tv short links",
    )
    batch_parser.add_argument(
        "--file", "-f",
        action="append",
        default=[],
        help="Text file with one link per line ('-' reads stdin); repeatable",
    )
    batch_parser.add_argument(
        "--first-page",
        action="store_true",
        help="Only download P1 of multi-part videos (default: all parts)",
    )
    _add_download_options(batch_parser)
    _add_archive_options(batch_parser)

    index_parser = subparsers.add_parser(
        "download-index",
        help="Download selected videos from an existing creator index",
    )
    index_parser.add_argument("manifest", help="Path to index.json")
    index_selection = index_parser.add_mutually_exclusive_group(required=True)
    index_selection.add_argument("--all", action="store_true")
    index_selection.add_argument("--bvid", action="append", default=[])
    _add_download_options(index_parser)
    _add_archive_options(index_parser)

    # --- login subcommand (headless QR / SESSDATA login) ---
    login_parser = subparsers.add_parser(
        "login",
        help="Login via QR code (rendered in terminal) or a pasted SESSDATA, "
        "saving credentials for CLI use without the GUI",
    )
    login_mode = login_parser.add_mutually_exclusive_group()
    login_mode.add_argument(
        "--sessdata",
        help="Skip the QR flow and login with a SESSDATA cookie value directly",
    )
    login_mode.add_argument(
        "--status",
        action="store_true",
        help="Show the saved login state without logging in again",
    )
    subparsers.add_parser("logout", help="Remove saved Bilibili credentials")

    # --- rebuild-cache subcommand (rebuild resolve cache from existing info.json) ---
    rebuild_parser = subparsers.add_parser(
        "rebuild-cache",
        help="从已下载的 info.json 重建解析缓存，避免重新解析（离线）",
    )
    rebuild_parser.add_argument(
        "creator_dir",
        help="UP 主目录（含 index.json 和 [BV...] 子目录）",
    )

    # --- tui subcommand (full-screen terminal UI) ---
    if include_tui:
        subparsers.add_parser(
            "tui",
            help="Launch the full-screen terminal UI (Textual)",
        )

    return parser


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "test":
        _cli_test(args.source)
    elif args.command == "download":
        _cli_download(args)
    elif args.command == "creator":
        _cli_creator(args)
    elif args.command == "batch":
        _cli_batch(args)
    elif args.command == "download-index":
        _cli_download_index(args)
    elif args.command == "login":
        _cli_login(args)
    elif args.command == "logout":
        _cli_logout()
    elif args.command == "rebuild-cache":
        _cli_rebuild_cache(args)
    elif args.command == "tui":
        _launch_tui()


def _launch_tui() -> None:
    """Launch the Textual TUI (lazy import, friendly fallback if missing)."""
    try:
        from bilibili_downloader.tui import launch
    except ImportError:
        print('Textual not installed. Install with: pip install "bilibili-downloader[tui]"')
        print("Or use the command line: biliflow-cli --help")
        sys.exit(1)
    # The TUI owns the whole terminal; any stderr log line would corrupt it.
    # Keep the file handler, drop the console handler.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.handlers.RotatingFileHandler
        ):
            root.removeHandler(handler)
    try:
        launch()
    except KeyboardInterrupt:
        pass


def _cli_test(source: str):
    """CLI test: fetch video info for a given input."""

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.batch import BatchResolver
    from bilibili_downloader.core.ffmpeg import FFmpegManager

    print(f"Fetching info for {source}...")

    # Check FFmpeg
    available, msg = FFmpegManager.check_available()
    print(f"FFmpeg: {'OK' if available else 'NOT FOUND'} - {msg}")

    # Fetch video info
    client = BilibiliAPIClient()
    try:
        info = BatchResolver(client).resolve_one(source)
        print(f"\nTitle:     {info.title}")
        print(f"Author:    {info.author}")
        print(f"Duration:  {info.duration_str}")
        print(f"BVID:      {info.bvid}")
        print(f"PID:       {info.cid}")
        print(f"Pages:     {len(info.pages)}")
        print(f"Subtitles: {len(info.subtitle_list)}")
        if info.subtitle_list:
            for s in info.subtitle_list:
                print(f"  - {s.lan}: {s.lan_doc}")
        print("\nSuccess!")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        client.close()


def _cli_download(args: argparse.Namespace):
    """CLI download: download a video by BV/AV number or URL."""
    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.batch import BatchResolver
    from bilibili_downloader.core.download_service import DownloadService
    from bilibili_downloader.core.models import DownloadItem
    from bilibili_downloader.utils.config import ConfigManager

    quality = VideoQuality(args.quality)

    # Load settings for default output dir and ffmpeg path
    config = ConfigManager()
    settings = config.load()
    output_dir = args.output or settings.output_dir

    print(f"Downloading {args.source} at {quality.label}...")

    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    service = None
    try:
        info = BatchResolver(client).resolve_one(args.source)
        print(f"Title: {info.title}")

        if str(args.page).lower() == "all":
            page_infos = [info.for_page(page) for page in info.pages] or [info]
        else:
            try:
                page_number = int(args.page)
            except ValueError as exc:
                raise ValueError("--page must be a positive page number or 'all'") from exc
            if page_number < 1 or page_number > max(1, len(info.pages)):
                raise ValueError(f"--page must be between 1 and {max(1, len(info.pages))}")
            page_infos = [info.for_page(info.pages[page_number - 1])] if info.pages else [info]

        def progress(pct, text):
            bar_len = 30
            filled = int(bar_len * pct)
            bar = "=" * filled + "-" * (bar_len - filled)
            print(f"\r[{bar}] {pct * 100:5.1f}%  {text}", end="", flush=True)

        service = DownloadService(
            client, output_dir, ffmpeg_path=settings.ffmpeg_path or None,
        )
        codec = args.codec or settings.default_video_codec
        for index, page_info in enumerate(page_infos, start=1):
            if len(page_infos) > 1:
                print(f"\n[{index}/{len(page_infos)}] CID {page_info.cid}")
            item = DownloadItem(
                video_info=page_info,
                selected_quality=quality,
                selected_video_codec=codec,
                output_path=output_dir,
                download_danmaku=args.danmaku,
                download_subtitle=args.subtitle,
                selected_subtitle_lan=args.subtitle_language,
                download_metadata=_option(args, "metadata", settings.download_metadata),
                download_cover=_option(args, "cover", settings.download_cover),
                download_comments=_option(args, "comments", settings.download_comments),
                embed_metadata=_option(args, "embed_metadata", settings.embed_metadata),
                embed_cover=_option(args, "embed_cover", settings.embed_cover),
                refresh_sidecars=getattr(args, "refresh_sidecars", False),
            )
            outcome = service.download(item, progress)
            print(f"\nSaved to: {outcome.video_path}")
            for warning in outcome.warnings:
                print(f"Warning: {warning}")
    except KeyboardInterrupt:
        if service is not None:
            service.cancel()
        print("\nDownload cancelled")
        raise SystemExit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        client.close()


def _add_download_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--quality", "-q", type=int, default=VideoQuality.Q1080P,
        choices=[q.value for q in VideoQuality],
    )
    parser.add_argument("--output", "-o")
    parser.add_argument("--codec", "-c", type=int, choices=[7, 12, 13])
    parser.add_argument("--subtitle-language", default="zh-Hans")
    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=None,
        help="Concurrent downloads for batch/creator (default: from settings, 1-8)",
    )


def _add_archive_options(
    parser: argparse.ArgumentParser, *, include_media: bool = True
) -> None:
    if include_media:
        parser.add_argument("--danmaku", action=argparse.BooleanOptionalAction, default=None)
        parser.add_argument("--subtitle", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--metadata", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--cover", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--comments", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--embed-metadata", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--embed-cover", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--refresh-sidecars",
        action="store_true",
        help="Overwrite existing metadata, comments, cover, danmaku and subtitles",
    )


def _option(args: argparse.Namespace, name: str, default: bool) -> bool:
    value = getattr(args, name, None)
    return default if value is None else bool(value)


def _resolve_jobs(args: argparse.Namespace, settings) -> int:
    """Pick concurrency: explicit --jobs, else settings, clamped to 1-8."""
    jobs = getattr(args, "jobs", None)
    if jobs is None:
        jobs = getattr(settings, "max_concurrent_downloads", 1) or 1
    return max(1, min(8, int(jobs)))


def _cli_login(args: argparse.Namespace) -> None:
    from bilibili_downloader.cli.login import cli_login

    try:
        cli_login(args)
    except KeyboardInterrupt:
        print("\n已取消")
        raise SystemExit(130)


def _cli_logout() -> None:
    from bilibili_downloader.cli.login import cli_logout

    cli_logout()


def _cli_rebuild_cache(args: argparse.Namespace) -> None:
    from bilibili_downloader.cli.rebuild_cache import cli_rebuild_cache

    try:
        cli_rebuild_cache(args)
    except KeyboardInterrupt:
        print("\n已取消")
        raise SystemExit(130)


def _cli_creator(args: argparse.Namespace) -> None:
    from pathlib import Path

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.creator import (
        CreatorIndexService,
        creator_directory_name,
        load_creator_index,
        parse_creator_mid,
        save_creator_index,
    )
    from bilibili_downloader.utils.config import ConfigManager

    settings = ConfigManager().load()
    output_dir = Path(args.output or settings.output_dir)
    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    try:
        manifest = (
            Path(args.index)
            if args.index
            else None
        )

        # Resolve a resume checkpoint: an index.partial.json left by a previous
        # interrupted run. Only relevant when we're re-fetching (no explicit
        # --index pointing elsewhere). core.fetch() accepts a resume_index and
        # continues from the saved cursor.
        resume_index = None
        if manifest is None:
            mid = parse_creator_mid(args.source)
            # name is unknown before fetching, so glob by the _<mid> suffix.
            candidates = list(output_dir.glob(f"*_{mid}/index.partial.json"))
            if candidates:
                try:
                    resume_index = load_creator_index(candidates[0])
                    print(f"发现检查点，尝试从 {len(resume_index.videos)} 个投稿处继续…")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("检查点读取失败，将重新抓取：%s", exc)
                    resume_index = None

        def _checkpoint(idx):
            # Persist progress so an interrupted run can resume later.
            ckpt_dir = output_dir / creator_directory_name(idx.name, idx.mid)
            save_creator_index(idx, ckpt_dir / "index.partial.json")

        print(f"Fetching creator index for {args.source}...")
        index = CreatorIndexService(client).fetch(
            args.source,
            progress_callback=lambda done, total: print(
                f"\rIndexed {done}/{total or '?'} videos", end="", flush=True
            ),
            status_callback=lambda status: print(f"\n{status}", flush=True),
            resume_index=resume_index,
            checkpoint_callback=_checkpoint,
        )
        final_manifest = manifest or (
            output_dir / creator_directory_name(index.name, index.mid) / "index.json"
        )
        save_creator_index(index, final_manifest)
        # Index complete — drop the partial checkpoint.
        (final_manifest.parent / "index.partial.json").unlink(missing_ok=True)
        print(f"\nIndex saved to: {final_manifest}")
        print(f"Creator: {index.name} ({index.mid}), videos: {len(index.videos)}")
        if args.download_all or args.bvid:
            selected = index.videos if args.download_all else _select_entries(index, args.bvid)
            _download_creator_entries(client, settings, output_dir, index, selected, args)
    except KeyboardInterrupt:
        print("\nCancelled")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nError: {exc}")
        raise SystemExit(1) from exc
    finally:
        client.close()


def _cli_download_index(args: argparse.Namespace) -> None:
    from pathlib import Path

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.creator import load_creator_index
    from bilibili_downloader.utils.config import ConfigManager

    settings = ConfigManager().load()
    output_dir = Path(args.output or settings.output_dir)
    index = load_creator_index(Path(args.manifest))
    selected = index.videos if args.all else _select_entries(index, args.bvid)
    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    try:
        _download_creator_entries(client, settings, output_dir, index, selected, args)
    except KeyboardInterrupt:
        print("\nCancelled")
        raise SystemExit(130)
    finally:
        client.close()


def _cli_batch(args: argparse.Namespace) -> None:
    from pathlib import Path

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.batch import BatchResolver, classify_batch_inputs
    from bilibili_downloader.utils.config import ConfigManager

    lines = list(args.sources)
    for name in args.file:
        try:
            text = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"无法读取 {name}：{exc}")
            raise SystemExit(1) from exc
        lines.extend(text.splitlines())
    if not args.sources and not args.file and not sys.stdin.isatty():
        lines.extend(sys.stdin.read().splitlines())

    sources, invalid = classify_batch_inputs("\n".join(lines))
    for line in invalid:
        print(f"跳过无法识别的输入：{line}")
    if not sources:
        print("没有可下载的链接。用法：biliflow-cli batch <链接...> 或 --file links.txt")
        raise SystemExit(1)

    settings = ConfigManager().load()
    output_dir = Path(args.output or settings.output_dir)
    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    resolver = BatchResolver(client)
    tasks = [(source, source, lambda source=source: resolver.resolve_one(source)) for source in sources]
    try:
        _run_download_tasks(
            client, settings, output_dir, tasks, args, all_pages=not args.first_page
        )
    except KeyboardInterrupt:
        print("\nCancelled")
        raise SystemExit(130)
    finally:
        client.close()


def _select_entries(index, bvids: list[str]):
    wanted = {value.upper() for value in bvids}
    selected = [entry for entry in index.videos if entry.bvid.upper() in wanted]
    missing = wanted - {entry.bvid.upper() for entry in selected}
    if missing:
        raise ValueError(f"index.json 中不存在：{', '.join(sorted(missing))}")
    return selected


def _download_creator_entries(client, settings, output_dir, index, entries, args) -> None:
    import threading
    from pathlib import Path

    from bilibili_downloader.core.creator import creator_directory_name
    from bilibili_downloader.tui.resolve_cache import ResolveCache

    # Resolve from the local archive first (the resolve cache shared with the
    # TUI, then each video's info.json) so re-running a finished index costs
    # no API requests; only unseen videos hit the network.
    creator_dir = Path(output_dir) / creator_directory_name(index.name, index.mid)
    cache = ResolveCache.for_creator(output_dir, index)
    cache_lock = threading.Lock()

    def resolve(bvid):
        with cache_lock:
            info = cache.get(bvid)
        if info is not None:
            return info
        info = _archived_video_info(creator_dir, bvid) or client.get_video_info(bvid)
        with cache_lock:
            cache.put(bvid, info)
        return info

    tasks = [
        (entry.bvid, entry.title, lambda bvid=entry.bvid: resolve(bvid))
        for entry in entries
    ]
    try:
        _run_download_tasks(
            client, settings, output_dir, tasks, args,
            creator_mid=index.mid, creator_name=index.name,
        )
    finally:
        with cache_lock:
            cache.save()


def _archived_video_info(creator_dir, bvid):
    """Parse ``VideoInfo`` from a previously archived ``[BV…] …/info.json``."""
    import json

    from bilibili_downloader.api.client import _parse_video_info

    if not creator_dir.is_dir():
        return None
    for child in creator_dir.iterdir():
        if not (child.is_dir() and child.name.startswith(f"[{bvid}] ")):
            continue
        try:
            payload = json.loads((child / "info.json").read_text(encoding="utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("bvid") == bvid:
                return _parse_video_info(bvid, data)
        except (OSError, ValueError) as exc:
            logger.debug("Ignoring archived info for %s: %s", bvid, exc)
    return None


def _run_download_tasks(
    client, settings, output_dir, tasks, args, *,
    all_pages=True, creator_mid=0, creator_name="",
) -> None:
    """Download ``(label, title, resolve)`` tasks concurrently.

    ``resolve`` runs inside the worker so metadata fetching overlaps with
    downloads. A failing task is reported and the others continue; any
    failure exits 1 after the summary.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from bilibili_downloader.core.download_service import DownloadService
    from bilibili_downloader.core.models import DownloadItem

    quality = VideoQuality(args.quality)
    codec = args.codec or settings.default_video_codec
    jobs = _resolve_jobs(args, settings)
    total = len(tasks)
    print(f"Downloading {total} video(s) with {jobs} concurrent job(s)...", flush=True)
    # Concurrent \r progress bars overwrite each other; only draw one when
    # downloads run one at a time.
    progress = _print_progress if jobs == 1 else (lambda pct, text: None)

    # Each task gets its own DownloadService because the service holds
    # per-download mutable state (self._cancelled, downloader.last_*).
    # They share the httpx client (thread-safe); sidecar writes are serialized
    # per video and comment archives run one at a time process-wide.
    def _process(position, label, resolve):
        service = DownloadService(
            client, str(output_dir), ffmpeg_path=settings.ffmpeg_path or None
        )
        info = resolve()
        if all_pages:
            pages = [info.for_page(page) for page in info.pages] or [info]
        else:
            pages = [info.for_page(info.pages[0])] if info.pages else [info]
        saved = []
        skipped = True
        for page_info in pages:
            item = DownloadItem(
                video_info=page_info,
                selected_quality=quality,
                selected_video_codec=codec,
                download_danmaku=_option(args, "danmaku", settings.download_danmaku),
                download_subtitle=_option(args, "subtitle", settings.download_subtitle),
                download_metadata=_option(args, "metadata", settings.download_metadata),
                download_cover=_option(args, "cover", settings.download_cover),
                download_comments=_option(args, "comments", settings.download_comments),
                embed_metadata=_option(args, "embed_metadata", settings.embed_metadata),
                embed_cover=_option(args, "embed_cover", settings.embed_cover),
                refresh_sidecars=args.refresh_sidecars,
                selected_subtitle_lan=args.subtitle_language,
                creator_mid=creator_mid,
                creator_name=creator_name,
            )
            outcome = service.download(item, progress)
            saved.append(outcome.video_path)
            skipped = skipped and outcome.skipped_media
            for warning in outcome.warnings:
                logger.warning("[%d/%d] %s: %s", position, total, label, warning)
        return info.title, saved, skipped

    failures = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        future_to_task = {
            pool.submit(_process, pos, label, resolve): (pos, label, title)
            for pos, (label, title, resolve) in enumerate(tasks, start=1)
        }
        for future in as_completed(future_to_task):
            position, label, title = future_to_task[future]
            if jobs == 1:
                print(flush=True)  # end the progress bar line
            try:
                resolved_title, saved, skipped = future.result()
                note = "（已存在，跳过）" if skipped else ""
                print(
                    f"[{position}/{total}] ✓ {label} — {resolved_title or title}{note}",
                    flush=True,
                )
                for path in saved:
                    logger.info("[%d/%d] saved: %s", position, total, path)
            except Exception as exc:  # noqa: BLE001
                failures.append((label, str(exc)))
                logger.error("下载失败 %s: %s", label, exc)
                print(f"✗ {label} — {exc}", flush=True)

    if failures:
        print(f"\n{len(failures)} 个任务失败：", flush=True)
        for label, err in failures:
            print(f"  {label}: {err}", flush=True)
        raise SystemExit(1)


def _print_progress(pct: float, text: str) -> None:
    bar_len = 30
    filled = int(bar_len * pct)
    bar = "=" * filled + "-" * (bar_len - filled)
    print(f"\r[{bar}] {pct * 100:5.1f}%  {text}", end="", flush=True)


def _launch_gui():
    """Launch the PySide6 GUI application."""
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from bilibili_downloader.gui.main_window import MainWindow
        from bilibili_downloader.gui.resources.paths import asset_path
        from bilibili_downloader.gui.resources.theme import ThemeManager
    except ImportError:
        print('PySide6 not installed. Install with: pip install "bilibili-downloader[gui]"')
        print("Or use the command line: biliflow-cli --help")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Bilibili Downloader")
    app.setOrganizationName("bilibili-downloader")
    app.setWindowIcon(QIcon(asset_path("app_icon.png")))
    theme_manager = ThemeManager(app)

    window = MainWindow()
    window.show()

    # Keep the controller alive for system theme change notifications.
    app._theme_manager = theme_manager
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
