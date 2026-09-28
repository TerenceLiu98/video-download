"""File / directory browser screen — the TUI picker.

Textual ships no native file dialog, so this is a small modal browser reused
by Settings (output dir + ffmpeg path) and Creator (index.json import). It
walks the filesystem directly on the UI thread — directory listing is a
fast local op and never blocks the way network calls do.

Modes:
- ``select_file=True`` (default): choosing a file dismisses with its Path.
  Directories can be navigated into but not "selected" as the result. A
  ``name_filter`` predicate narrows which files are pickable (e.g. ffmpeg).
- ``select_file=False``: directory-select mode — choosing a directory (or
  pressing 选择当前目录) dismisses with that directory's Path.

Dismisses with a :class:`~pathlib.Path` on pick, or ``None`` on cancel.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

_HOME = Path.home()


def _ffmpeg_filter(name: str) -> bool:
    """Pickable predicate for the ffmpeg picker: match the executable name."""
    low = name.lower()
    if low == "ffmpeg" or low.startswith("ffmpeg"):
        return True
    # Windows: ffmpeg.exe
    stem, ext = os.path.splitext(low)
    return ext in (".exe", "") and stem.startswith("ffmpeg")


def _json_filter(name: str) -> bool:
    return name.lower().endswith(".json")


class FileBrowserScreen(ModalScreen):
    """Navigate directories and pick a file or directory.

    Args:
        start: Directory to open in (defaults to home, falling back to cwd).
        select_file: True → return a file; False → return a directory.
        name_filter: Optional predicate ``(name) -> bool``; in file mode only
            files passing it are pickable (Enter on them returns the path).
            Other files are still listed (greyed in hint) but not selectable.
        title: Modal heading.
    """

    DEFAULT_CSS = """
    FileBrowserScreen { align: center middle; }
    FileBrowserScreen > Vertical {
        width: 88; max-width: 94%; height: 32; max-height: 92%;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    FileBrowserScreen #fb-title { color: #ffffff; text-style: bold; }
    FileBrowserScreen #fb-path { color: #a0a0aa; }
    FileBrowserScreen #fb-hint { color: #a0a0aa; }
    FileBrowserScreen Input { background: #1b1b1f; border: solid #33333a; }
    FileBrowserScreen #fb-filter { height: 3; }
    FileBrowserScreen DataTable {
        height: 1fr; background: #1b1b1f; border: solid #26262b;
    }
    FileBrowserScreen #fb-select { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    FileBrowserScreen #fb-up { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    FileBrowserScreen #fb-cancel { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    FileBrowserScreen .fb-row { height: auto; padding: 0 0 1 0; }
    """

    BINDINGS = [
        Binding("up", "cursor_up", "上", show=False),
        Binding("down", "cursor_down", "下", show=False),
        Binding("enter", "activate", "进入/选择"),
        Binding("backspace", "go_parent", "上级"),
        Binding("escape", "cancel", "取消"),
    ]

    _DIR = "📁"
    _FILE = "📄"
    _PICKABLE = "✔"

    def __init__(
        self,
        start: Optional[Path] = None,
        *,
        select_file: bool = True,
        name_filter: Optional[Callable[[str], bool]] = None,
        title: str = "选择文件",
    ):
        super().__init__()
        self._select_file = select_file
        self._name_filter = name_filter
        self._title = title
        start = Path(start) if start else None
        for candidate in (start, _HOME, Path.cwd()):
            if candidate and candidate.is_dir():
                self._dir: Path = candidate
                break
        else:  # pragma: no cover - every reasonable host has a cwd
            self._dir = Path.cwd()
        # Sentinel prefix marks a directory row inside the DataTable; the rest
        # of the cell text is the entry name. Stored apart from the key because
        # DataTable row keys must be unique strings and names can repeat across
        # dirs — we key by the full path instead.
        self._is_dir: dict[str, bool] = {}
        self._pickable: dict[str, bool] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="fb-title")
            yield Static("", id="fb-path")
            yield Input(placeholder="筛选文件名…", id="fb-filter")
            with VerticalScroll():
                yield DataTable(id="fb-table", cursor_type="row", zebra_stripes=True)
            yield Static(self._hint_text(), id="fb-hint", classes="fb-row")
            with Horizontal(classes="fb-row"):
                yield Button("上级目录", id="fb-up")
                if not self._select_file:
                    yield Button("选择当前目录", id="fb-select")
                yield Button("取消", id="fb-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#fb-table", DataTable)
        table.add_columns("类型", "名称")
        self._reload()

    # --- view ---
    def _hint_text(self) -> str:
        if self._select_file:
            verb = "在文件上按 Enter 选择"
            if self._name_filter is not None:
                verb += "（仅 ✔ 标记的可选）"
        else:
            verb = "在目录上按 Enter 选择，或点「选择当前目录」"
        return f"Enter 进入目录 / {verb} · Backspace 上级 · Esc 取消"

    def _reload(self) -> None:
        self.query_one("#fb-path", Static).update(str(self._dir))
        filt = self.query_one("#fb-filter", Input).value.strip().lower()
        table = self.query_one("#fb-table", DataTable)
        table.clear()
        self._is_dir.clear()
        self._pickable.clear()
        try:
            entries = sorted(self._dir.iterdir(), key=_sort_key)
        except (PermissionError, OSError):
            self.query_one("#fb-hint", Static).update("无法读取该目录（权限不足）")
            return
        self.query_one("#fb-hint", Static).update(self._hint_text())
        for entry in entries:
            name = entry.name
            if filt and filt not in name.lower():
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            key = str(entry)
            self._is_dir[key] = is_dir
            if is_dir:
                icon, pickable = self._DIR, True  # dirs always navigable
            else:
                pickable = (
                    self._name_filter(name) if self._name_filter is not None else True
                )
                icon = (self._FILE + " " + self._PICKABLE) if pickable else self._FILE
            self._pickable[key] = pickable
            table.add_row(self._DIR if is_dir else icon, name, key=key)

    # --- actions ---
    def action_activate(self) -> None:
        self._activate_cursor()

    def action_go_parent(self) -> None:
        self._go_parent()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(DataTable.RowSelected, "#fb-table")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        self._activate(event.row_key.value)

    def _activate_cursor(self) -> None:
        table = self.query_one("#fb-table", DataTable)
        try:
            row_key, _ = table.coordinate_to_row_key(table.cursor_coordinate)
        except Exception:  # noqa: BLE001
            return
        if row_key is None or row_key.value is None:
            return
        self._activate(row_key.value)

    def _activate(self, key: str) -> None:
        path = Path(key)
        is_dir = self._is_dir.get(key, False)
        if is_dir:
            if not self._select_file:
                # Directory-select mode: selecting a directory returns it.
                self.dismiss(path)
                return
            # File-select mode: descend into it.
            if path.is_dir():
                self._dir = path
                self._reload()
            return
        # A file row.
        if self._select_file and self._pickable.get(key, False):
            self.dismiss(path)
        elif self._select_file:
            self.app.notify("该文件不可选", severity="warning")

    def _go_parent(self) -> None:
        parent = self._dir.parent
        if parent != self._dir and parent.is_dir():
            self._dir = parent
            self._reload()

    @on(Input.Changed, "#fb-filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        del event  # value read fresh in _reload
        self._reload()

    @on(Button.Pressed, "#fb-up")
    def _on_up(self) -> None:
        self._go_parent()

    @on(Button.Pressed, "#fb-select")
    def _on_select_dir(self) -> None:
        # Only rendered in directory-select mode.
        self.dismiss(self._dir)

    @on(Button.Pressed, "#fb-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)


def _sort_key(entry: Path):
    """List directories first, then files, each alphabetically (case-insensitive)."""
    try:
        is_dir = entry.is_dir()
    except OSError:
        is_dir = False
    try:
        name = entry.name.lower()
    except AttributeError:
        name = str(entry)
    return (0 if is_dir else 1, name)
