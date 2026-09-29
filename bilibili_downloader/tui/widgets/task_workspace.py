"""Task workspace; workers belong to the app and outlive navigation."""

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Select, Static

from bilibili_downloader.tui.widgets.download_queue import DownloadQueue


class TaskWorkspace(Vertical):
    DEFAULT_CSS = """
    TaskWorkspace { height: 1fr; padding: 0 1; }
    TaskWorkspace #task-summary { height: auto; max-height: 3; margin-bottom: 1; color: #e8e8ec; }
    TaskWorkspace #import-status { height: auto; max-height: 3; color: #7cd4bb; }
    TaskWorkspace #task-filters { height: 3; }
    TaskWorkspace Select { width: 20; }
    TaskWorkspace Input { width: 1fr; }
    TaskWorkspace #task-tools { height: 3; }
    TaskWorkspace Button { min-width: 8; width: auto; margin-right: 1; }
    TaskWorkspace #task-details-scroll {
        height: 7; max-height: 30%; border-top: solid #33333a;
    }
    TaskWorkspace #task-details { height: auto; }
    TaskWorkspace #task-empty { height: 2; color: #a0a0aa; }
    """

    def __init__(self, model):
        super().__init__(id="task-workspace")
        self.model = model

    def compose(self) -> ComposeResult:
        yield Static("下载任务", id="task-summary", markup=False)
        yield Static("", id="import-status", markup=False)
        with Horizontal(id="task-filters"):
            yield Select([
                ("全部", "all"), ("待解析/解析中", "resolving"), ("下载中", "active"), ("等待中", "pending"),
                ("已暂停", "paused"), ("失败", "failed"), ("部分完成", "partial"),
                ("已完成", "done"),
            ], value="all", allow_blank=False, id="task-filter")
            yield Input(placeholder="搜索标题 / BV 号", id="task-search")
        with HorizontalScroll(id="task-tools"):
            for label, action in (("导入链接", "import"), ("暂停", "cancel"),
                                  ("继续/重试", "retry"), ("打开目录", "open"),
                                  ("删除记录", "delete"), ("全部暂停", "cancel_all"),
                                  ("全部继续", "resume_all"), ("清理已完成", "clear_done")):
                yield Button(label, id=f"task-{action}")
        yield Static("暂无任务", id="task-empty")
        yield DownloadQueue(self.model)
        with VerticalScroll(id="task-details-scroll"):
            yield Static("", id="task-details", markup=False)

    @on(Select.Changed, "#task-filter")
    def filter_changed(self, event: Select.Changed) -> None:
        table = self.query_one(DownloadQueue)
        table.filter_state = str(event.value)
        table.refresh_model()
        self.update_details()

    @on(Input.Changed, "#task-search")
    def search_changed(self, event: Input.Changed) -> None:
        table = self.query_one(DownloadQueue)
        table.search = event.value.strip()
        table.refresh_model()
        self.update_details()

    @on(DataTable.RowHighlighted)
    def row_highlighted(self) -> None:
        self.update_details()

    @on(Button.Pressed)
    def task_action(self, event: Button.Pressed) -> None:
        if not event.button.id or not event.button.id.startswith("task-"):
            return
        event.stop()
        action = event.button.id.removeprefix("task-")
        if action == "import":
            self.app._open_batch()
            return
        did = self.query_one(DownloadQueue)._cursor_download_id()
        self.post_message(DownloadQueue.QueueAction(action, did if did is not None else -1))

    def update_details(self) -> None:
        table = self.query_one(DownloadQueue)
        row = self.model.get(table._cursor_download_id())
        self.query_one("#task-empty").display = table.row_count == 0
        self.query_one("#task-empty", Static).update(
            "暂无任务" if not len(self.model) else "没有匹配的任务"
        )
        active = row is not None and self.model.get_worker(row.download_id) is not None
        self.query_one("#task-cancel", Button).disabled = not active or row.state == "pausing"
        self.query_one("#task-retry", Button).disabled = (
            row is None or active or row.state not in ("paused", "cancelled", "failed", "resolve_failed")
            or (row.item is None and not row.source)
        )
        self.query_one("#task-delete", Button).disabled = row is None or active
        self.query_one("#task-open", Button).disabled = row is None or not (row.output_dir or row.video_path)
        if row is None:
            self.query_one("#task-details", Static).update("")
            return
        speed, eta = table._metrics(row)
        text = Text(row.title, style="bold")
        text.append(f"\n{row.status} · {row.spec} · {speed} · 流 ETA {eta}", style="")
        if row.video_path or row.output_dir:
            text.append(f"\n保存位置：{row.video_path or row.output_dir}", style="")
        if row.error:
            text.append(f"\n错误：{row.error}", style="red")
        for warning in row.warnings:
            text.append(f"\n警告：{warning}", style="yellow")
        self.query_one("#task-details", Static).update(text)

    def update_summary(self, summary: str, imports: str) -> None:
        self.query_one("#task-summary", Static).update(summary)
        self.query_one("#import-status", Static).update(imports)
        self.query_one("#import-status").display = bool(imports)
        self.update_details()
