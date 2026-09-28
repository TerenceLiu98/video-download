"""Creator screen — fetch a UP-creator's index, filter, select, enqueue.

Modal Screen. Flow: enter UID/space-URL → 抓取索引 (resumes from
``index.partial.json`` if present) → a DataTable of submissions with a
checkbox column (Space toggles) + a filter Input + 全选/全不选/反选 →
加入队列 enqueues checked BVIDs as a batch.

The App owns the fetch worker + enqueue; this screen only gathers the selection.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

_CHECKED = "☑"
_UNCHECKED = "☐"


class CreatorScreen(ModalScreen):
    DEFAULT_CSS = """
    CreatorScreen { align: center middle; }
    CreatorScreen > Vertical {
        width: 96; max-width: 94%; height: 36; max-height: 92%;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    CreatorScreen #cs-title { color: #ffffff; text-style: bold; }
    CreatorScreen .cs-muted { color: #a0a0aa; }
    CreatorScreen #cs-status { color: #a0a0aa; }
    CreatorScreen Input { background: #1b1b1f; border: solid #33333a; }
    CreatorScreen #cs-source { height: 3; }
    CreatorScreen #cs-filter { height: 3; }
    CreatorScreen #cs-import-path { height: 3; }
    CreatorScreen #cs-fetch { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    CreatorScreen #cs-browse { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    CreatorScreen #cs-import { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    CreatorScreen #cs-enqueue { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    CreatorScreen #cs-cancel { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    CreatorScreen DataTable {
        height: 1fr; background: #1b1b1f; border: solid #26262b;
    }
    """

    def __init__(self, api_client, output_dir: str):
        super().__init__()
        self._client = api_client
        self._output_dir = output_dir
        self._index = None          # CreatorVideoIndex once fetched
        self._checked: set[str] = set()
        self._filter = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("UP 主投稿索引", id="cs-title")
            yield Static(
                "① 输入 UID/空间链接后点「抓取索引」拉取全部投稿；"
                "② 已有 index.json 可填路径点「导入」直接载入，无需联网。",
                classes="cs-muted",
            )
            yield Horizontal(
                Input(placeholder="UID 或空间链接（回车抓取）", id="cs-source"),
                Button("抓取索引", id="cs-fetch"),
            )
            yield Horizontal(
                Input(
                    placeholder="已有 index.json 路径（导入，可选）",
                    id="cs-import-path",
                ),
                Button("浏览", id="cs-browse"),
                Button("导入", id="cs-import"),
            )
            yield Static("", id="cs-status")
            yield Input(placeholder="筛选标题或 BV…", id="cs-filter")
            yield DataTable(id="cs-table", cursor_type="row", zebra_stripes=True)
            yield Horizontal(
                Button("全选", id="cs-all"),
                Button("全不选", id="cs-none"),
                Button("反选", id="cs-invert"),
            )
            yield Horizontal(
                Button("加入队列 (0)", id="cs-enqueue"),
                Button("取消", id="cs-cancel"),
            )

    def on_mount(self) -> None:
        self.query_one("#cs-table", DataTable).add_columns("选", "标题", "BV", "时长")

    # --- fetch ---
    @on(Input.Submitted, "#cs-source")
    def _on_source_submitted(self, event: Input.Submitted) -> None:
        # 回车即可触发抓取，无需先点按钮。
        if event.value.strip():
            self._on_fetch()

    @on(Button.Pressed, "#cs-fetch")
    def _on_fetch(self) -> None:
        source = self.query_one("#cs-source", Input).value.strip()
        if not source:
            self.app.notify("请输入 UID 或空间链接", severity="warning")
            return
        self.query_one("#cs-status", Static).update("正在抓取索引…")
        self.app.start_creator_fetch(source, self._output_dir)

    @on(Button.Pressed, "#cs-browse")
    def _browse_import(self) -> None:
        from bilibili_downloader.tui.screens import FileBrowserScreen, _json_filter

        current_path = self.query_one("#cs-import-path", Input).value.strip()
        start = Path(current_path).expanduser() if current_path else None
        # If the current path is a file, start from its parent directory
        if start and start.is_file():
            start = start.parent
        # Fall back to output directory if neither is valid
        if not start or not start.is_dir():
            start = Path(self._output_dir).expanduser()

        def _write(picked) -> None:
            if picked is not None:
                self.query_one("#cs-import-path", Input).value = str(picked)

        self.app.push_screen(
            FileBrowserScreen(
                start,
                select_file=True,
                name_filter=_json_filter,
                title="选择 index.json"
            ),
            _write,
        )

    # --- import ---
    @on(Input.Submitted, "#cs-import-path")
    def _on_import_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self._on_import()

    @on(Button.Pressed, "#cs-import")
    def _on_import(self) -> None:
        from bilibili_downloader.core.creator import load_creator_index

        raw = self.query_one("#cs-import-path", Input).value.strip()
        if not raw:
            self.app.notify("请填写 index.json 的路径", severity="warning")
            return
        path = Path(raw).expanduser()
        if not path.is_file():
            self.app.notify(f"文件不存在：{path}", severity="error")
            return
        try:
            index = load_creator_index(path)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"无法读取索引：{exc}", severity="error")
            return
        self._set_index(index)
        self.query_one("#cs-status", Static).update(
            f"已导入 {len(index.videos)} 个投稿（{path.name}）"
        )

    def _set_index(self, index) -> None:
        self._index = index
        self._checked = {v.bvid for v in index.videos}
        self._render_table()

    # Called by the App (which receives the worker messages on its pump).
    def on_creator_progress(self, message):
        self.query_one("#cs-status", Static).update(f"已索引 {message.done}/{message.total or '?'}")

    def on_creator_status(self, message):
        self.query_one("#cs-status", Static).update(message.text)

    def on_creator_finished(self, message):
        # Persist final index + drop partial checkpoint (mirrors Qt creator dialog).
        self.app.persist_creator_index(message.index)
        self._set_index(message.index)
        self.query_one("#cs-status", Static).update(
            f"索引完成：{len(message.index.videos)} 个投稿"
        )

    def on_creator_failed(self, message):
        self.query_one("#cs-status", Static).update(f"抓取失败：{message.error}")

    # --- filter / selection ---
    @on(Input.Changed, "#cs-filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._filter = event.value.strip().lower()
        self._render_table()

    @on(Button.Pressed, "#cs-all")
    def _select_visible(self) -> None:
        for v in self._visible_videos():
            self._checked.add(v.bvid)
        self._render_table()

    @on(Button.Pressed, "#cs-none")
    def _select_none(self) -> None:
        for v in self._visible_videos():
            self._checked.discard(v.bvid)
        self._render_table()

    @on(Button.Pressed, "#cs-invert")
    def _invert_visible(self) -> None:
        for v in self._visible_videos():
            if v.bvid in self._checked:
                self._checked.discard(v.bvid)
            else:
                self._checked.add(v.bvid)
        self._render_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Space-equivalent: clicking/toggling a row flips its checkbox.
        if self._index is None or event.row_key is None or event.row_key.value is None:
            return
        try:
            row_idx = int(event.row_key.value)
        except (TypeError, ValueError):
            return
        videos = list(self._visible_videos())
        if 0 <= row_idx < len(videos):
            bvid = videos[row_idx].bvid
            if bvid in self._checked:
                self._checked.discard(bvid)
            else:
                self._checked.add(bvid)
            self._render_table()

    # --- enqueue / cancel ---
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cs-cancel":
            self.app.cancel_creator_fetch()
            self.dismiss(None)
        elif event.button.id == "cs-enqueue":
            if self._index is None:
                self.app.notify("请先抓取索引", severity="warning")
                return
            selected = [v.bvid for v in self._index.videos if v.bvid in self._checked]
            if not selected:
                self.app.notify("未选择任何投稿", severity="warning")
                return
            self.dismiss((selected, self._index))

    # --- helpers ---
    def _visible_videos(self):
        if self._index is None:
            return []
        if not self._filter:
            return self._index.videos
        return [
            v for v in self._index.videos
            if self._filter in v.title.lower() or self._filter in v.bvid.lower()
        ]

    def _render_table(self) -> None:
        table = self.query_one("#cs-table", DataTable)
        table.clear()
        for i, v in enumerate(self._visible_videos()):
            mark = _CHECKED if v.bvid in self._checked else _UNCHECKED
            table.add_row(mark, v.title[:40], v.bvid, _fmt_duration(v.duration), key=str(i))
        self.query_one("#cs-enqueue", Button).label = f"加入队列 ({len(self._checked)})"


def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return "--"
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"
