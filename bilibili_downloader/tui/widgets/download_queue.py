"""Download queue — model + live DataTable widget.

A direct port of ``gui/widgets/download_list.py`` semantics onto Textual's
``DataTable``. The model (``DownloadQueueModel``) owns the stable download-id →
row mapping and all state transitions, so it is unit-testable without the
widget. The widget renders the model and drives it via keybindings.

The task workspace filters and sorts rows without changing model identity.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable

from bilibili_downloader.core.models import VIDEO_CODEC_MAP, DownloadItem, VideoQuality

_BAR_LEN = 10


def format_speed(speed: float) -> str:
    if speed >= 1048576:
        return f"{speed / 1048576:.1f} MiB/s"
    if speed >= 1024:
        return f"{speed / 1024:.1f} KiB/s"
    return f"{max(0, speed):.0f} B/s"


class TaskTitle(Text):
    def __init__(self, row):
        super().__init__(row.title, no_wrap=True, overflow="ellipsis")
        self.download_id = row.download_id


def _progress_bar(pct: int, state: str) -> str:
    """Render a Unicode progress bar string for a DataTable cell."""
    if state == "failed" or state == "error":
        return f"✗ {pct}%"
    if state in ("done", "partial"):
        return "█" * _BAR_LEN + " 100%"
    filled = int(_BAR_LEN * max(0, min(100, pct)) / 100)
    return "█" * filled + "░" * (_BAR_LEN - filled) + f" {pct}%"


@dataclass
class Row:
    download_id: int
    item: Optional[object] = None       # DownloadItem; None for resolution-error rows
    title: str = ""
    spec: str = ""
    pct: int = 0
    status: str = "等待中"
    state: str = "pending"              # pending|downloading|done|partial|failed|cancelled|retry|error
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    output_dir: str = ""
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    speed_updated_at: float = 0.0
    video_path: str = ""
    source: str = ""
    resolve_options: dict = field(default_factory=dict)


class DownloadQueueModel:
    """Stable-id download queue state (no UI dependency)."""

    def __init__(self):
        self._rows: dict[int, Row] = {}
        self._order: list[int] = []
        self._next_id = 0
        self._workers: dict[int, object] = {}
        self._snapshot_cache = {}

    # --- iteration / inspection ---
    def rows(self) -> list[Row]:
        return [self._rows[did] for did in self._order]

    def get(self, download_id: int) -> Optional[Row]:
        return self._rows.get(download_id)

    def __len__(self) -> int:
        return len(self._order)

    @property
    def has_active(self) -> bool:
        return bool(self._workers)

    def get_worker(self, download_id: int):
        return self._workers.get(download_id)

    def get_item(self, download_id: int):
        row = self._rows.get(download_id)
        return row.item if row else None

    def save(self, path: Path) -> None:
        from bilibili_downloader.core.creator import atomic_write_json

        atomic_write_json(path, self.snapshot())

    def snapshot(self) -> dict:
        """Detach changed rows on the UI thread; reuse immutable saved records."""
        from copy import deepcopy

        records = []
        for row in self.rows():
            values = vars(row).copy()
            values["warnings"] = list(row.warnings)
            cached = self._snapshot_cache.get(row.download_id)
            if cached is not None and cached[0] == values:
                records.append(cached[1])
                continue
            values["resolve_options"] = deepcopy(row.resolve_options)
            record = values.copy()
            record["item"] = row.item.model_dump(mode="json") if row.item else None
            if record["item"]:
                # Signed stream URLs expire; only persist the video identity/options.
                info = record["item"]["video_info"]
                info["video_streams"] = []
                info["audio_streams"] = []
                info["subtitle_list"] = []
            records.append(record)
            self._snapshot_cache[row.download_id] = (values, record)
        return {"version": 1, "rows": records}

    def restore(self, path: Path) -> None:
        self._snapshot_cache.clear()
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("不支持的任务记录版本")
        restored = {}
        for record in payload["rows"]:
            record = dict(record)
            record["item"] = DownloadItem.model_validate(record["item"]) if record["item"] else None
            row = Row(**record)
            if row.item and not row.title:
                row.title = row.item.video_info.title
            if (row.state in ("awaiting", "resolving")
                    or (row.state == "resolve_failed" and row.status == "解析中断，可重试")):
                row.state, row.status = "resolve_interrupted", "解析中断，可继续"
            row.speed_bps, row.eta_seconds = 0.0, None
            row.speed_updated_at = 0.0
            if row.state in ("pending", "downloading", "retry", "pausing"):
                row.state, row.status = "paused", "已恢复，等待继续"
            restored[row.download_id] = row
        self._rows = restored
        self._order = list(restored)
        self._next_id = max(self._order, default=-1) + 1

    # --- mutation ---
    def add_source(self, source: str, title: str, output_dir: str, options: dict) -> int:
        did = self.add(None, output_dir)
        row = self._rows[did]
        row.title = title or source
        row.source = source
        row.resolve_options = dict(options)
        row.state, row.status = "awaiting", "待解析"
        return did

    def resolve_source(self, did: int, item, after: int | None = None) -> int:
        if after is not None:
            output_dir = self._rows[did].output_dir
            did = self.add(item, output_dir)
            self._order.remove(did)
            self._order.insert(self._order.index(after) + 1, did)
        row = self._rows[did]
        row.item = item
        row.title = item.video_info.title
        if item.video_info.is_multi_part:
            page = next((p for p in item.video_info.pages if p.cid == item.video_info.cid), None)
            if page:
                row.title += f" · P{page.page} {page.part}"
        row.spec = _spec_label(item)
        row.state, row.status, row.error = "pending", "等待下载", None
        return did

    def add(self, item, output_dir: str = "") -> int:
        """Add a download item. Returns a stable download id."""
        download_id = self._next_id
        self._next_id += 1
        self._rows[download_id] = Row(
            download_id=download_id,
            item=item,
            title=(item.video_info.title if item and item.video_info else ""),
            spec=_spec_label(item),
            output_dir=output_dir,
        )
        self._order.append(download_id)
        return download_id

    def add_error(self, error: str) -> int:
        """Add a persistent resolution-error row (no worker)."""
        download_id = self._next_id
        self._next_id += 1
        self._rows[download_id] = Row(
            download_id=download_id,
            item=None,
            title=error[:80],
            spec="解析失败",
            status="未加入队列",
            state="error",
            error=error,
        )
        self._order.append(download_id)
        return download_id

    def register_worker(self, download_id: int, worker) -> None:
        self._workers[download_id] = worker

    def unregister_worker(self, download_id: int) -> None:
        self._workers.pop(download_id, None)

    def set_progress(self, download_id: int, pct: float, status_text: str,
                     speed_bps: float = 0.0, eta_seconds: float | None = None) -> None:
        row = self._rows.get(download_id)
        if row is None:
            return
        row.pct = int(pct * 100)
        row.speed_bps, row.eta_seconds = speed_bps, eta_seconds
        row.speed_updated_at = time.monotonic()
        if row.state == "pausing":
            return
        row.status = status_text or row.status
        row.state = "downloading"

    def mark_done(self, download_id: int, outcome) -> None:
        self.unregister_worker(download_id)
        row = self._rows.get(download_id)
        if row is None:
            return
        row.pct = 100
        warnings = list(getattr(outcome, "warnings", []) or [])
        row.warnings = warnings
        row.error = None
        row.video_path = getattr(outcome, "video_path", "") or ""
        row.status = "部分完成" if warnings else "完成"
        row.state = "partial" if warnings else "done"
        # Update spec from actual quality/codec if available.
        if outcome is not None and getattr(outcome, "actual_quality", None) is not None:
            quality = next(
                (entry.label for entry in VideoQuality if entry.value == outcome.actual_quality),
                str(outcome.actual_quality),
            )
            codec = VIDEO_CODEC_MAP.get(
                outcome.actual_video_codec, str(outcome.actual_video_codec or "")
            )
            row.spec = f"{quality} · {codec}"

    def mark_failed(self, download_id: int, error: str) -> None:
        self.unregister_worker(download_id)
        row = self._rows.get(download_id)
        if row is None:
            return
        row.status = f"失败：{error[:24]}"
        row.state = "failed"
        row.error = error

    def mark_cancelled(self, download_id: int) -> None:
        self.unregister_worker(download_id)
        row = self._rows.get(download_id)
        if row is None:
            return
        row.status = "已暂停"
        row.state = "paused"

    def mark_cancel_in_flight(self, download_id: int) -> None:
        row = self._rows.get(download_id)
        if row is None:
            return
        row.status = "暂停中…"
        row.state = "pausing"

    def mark_retry(self, download_id: int) -> None:
        row = self._rows.get(download_id)
        if row is None:
            return
        row.pct = 0
        row.status = "重试中…"
        row.state = "retry"

    def cancel(self, download_id: int) -> None:
        """Signal an active worker to cancel."""
        worker = self._workers.get(download_id)
        if worker is not None:
            self.mark_cancel_in_flight(download_id)
            worker.cancel()
        elif (row := self.get(download_id)) is not None and row.state in ("pending", "retry"):
            self.mark_cancelled(download_id)

    def delete(self, download_id: int) -> None:
        if download_id in self._workers:
            return
        self._workers.pop(download_id, None)
        self._rows.pop(download_id, None)
        self._snapshot_cache.pop(download_id, None)
        if download_id in self._order:
            self._order.remove(download_id)

    def cancel_all(self) -> None:
        for row in self.rows():
            self.cancel(row.download_id)


def _spec_label(item) -> str:
    if item is None:
        return ""
    q = getattr(item, "selected_quality", None)
    label = getattr(q, "label", None) or str(q or "")
    codec = getattr(item, "selected_video_codec", None)
    codec_name = VIDEO_CODEC_MAP.get(codec, "") if codec else ""
    return f"{label} · {codec_name}".strip(" ·")


class DownloadQueue(DataTable):
    """Live download table with cancel/retry/delete keybindings."""

    cursor_type = "row"
    zebra_stripes = True

    BINDINGS = [
        ("c", "cancel_cursor", "暂停"),
        ("r", "retry_cursor", "继续/重试"),
        ("delete", "delete_cursor", "删除"),
        ("C", "cancel_all", "全部暂停"),
    ]

    class QueueAction(Message):
        """Posted when a key-driven action targets a row; the App handles it."""

        def __init__(self, action: str, download_id: int):
            super().__init__()
            self.action = action          # "cancel" | "retry" | "delete" | "cancel_all"
            self.download_id = download_id

    def __init__(self, model: DownloadQueueModel):
        # Set model BEFORE super().__init__: DataTable's cursor_type reactive
        # triggers watchers during init that reach refresh.
        self.model = model
        self.filter_state = "unfinished"
        self.search = ""
        self._visible_ids = []
        self._rendered = {}
        self._layout_width = 0
        super().__init__()

    def on_mount(self) -> None:
        self._build_columns(max(40, self.size.width))
        self._full_refresh()

    def _build_columns(self, width: int) -> None:
        self.clear(columns=True)
        self._layout_width = width
        self._compact = width < 85
        self._COL_STATUS = 2 if self._compact else 4
        widths = (("视频", max(12, width - 28)), ("进度", 8), ("状态", 12)) if self._compact else (
            ("视频", max(18, width - 63)), ("进度", 16), ("速度", 12), ("流 ETA", 8), ("状态", 15),
        )
        for label, col_width in widths:
            self.add_column(label, width=col_width)

    def on_resize(self) -> None:
        width = self.size.width
        if width > 0 and width != self._layout_width:
            selected = self._cursor_download_id()
            self._build_columns(width)
            self._full_refresh()
            if selected in self._visible_ids:
                self.move_cursor(row=self._visible_ids.index(selected))

    def visible_rows(self) -> list[Row]:
        return [row for row in self.model.rows() if self._matches(row)]

    def _matches(self, row: Row) -> bool:
        states = {
            "active": {"downloading", "pausing"}, "pending": {"pending", "retry"},
            "paused": {"paused", "cancelled", "resolve_interrupted"}, "failed": {"failed", "error", "resolve_failed"},
            "partial": {"partial"}, "done": {"done"},
            "resolving": {"awaiting", "resolving"},
        }
        return ((self.filter_state == "all"
                 or (self.filter_state == "unfinished" and row.state not in ("done", "partial"))
                 or row.state in states.get(self.filter_state, set()))
                and (not self.search or self.search.casefold() in
                     (row.title + " " + row.source + " " + (row.item.video_info.bvid if row.item else "")).casefold()))

    @staticmethod
    def _signature(row):
        return row.title, row.pct, row.state, row.status, row.speed_bps, row.eta_seconds

    def _cells(self, row: Row):
        color = {"failed": "red", "error": "red", "resolve_failed": "red", "partial": "yellow",
                 "done": "green", "downloading": "cyan"}.get(row.state, "white")
        status = Text(row.status, style=color, no_wrap=True, overflow="ellipsis")
        title = TaskTitle(row)
        if self._compact:
            return title, f"{row.pct}%", status
        return title, _progress_bar(row.pct, row.state), *self._metrics(row), status

    def _cursor_download_id(self) -> int | None:
        """Return the download_id of the row under the cursor, or None."""
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:  # noqa: BLE001
            return None
        try:
            return int(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return None

    # --- key actions ---
    def action_cancel_cursor(self) -> None:
        did = self._cursor_download_id()
        if did is not None:
            self.post_message(self.QueueAction("cancel", did))

    def action_retry_cursor(self) -> None:
        did = self._cursor_download_id()
        if did is not None:
            self.post_message(self.QueueAction("retry", did))

    def action_delete_cursor(self) -> None:
        did = self._cursor_download_id()
        if did is not None:
            self.post_message(self.QueueAction("delete", did))

    def action_cancel_all(self) -> None:
        self.post_message(self.QueueAction("cancel_all", -1))

    def _full_refresh(self) -> None:
        cursor = self.cursor_row
        selected = self._cursor_download_id()
        self.clear()
        self._rendered.clear()
        rows = self.visible_rows()
        self._visible_ids = [r.download_id for r in rows]
        for row in rows:
            self.add_row(*self._cells(row), key=str(row.download_id))
            self._rendered[row.download_id] = self._signature(row)
        if self.row_count:
            if selected in self._visible_ids:
                cursor = self._visible_ids.index(selected)
            self.move_cursor(row=min(cursor, self.row_count - 1))
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        active = sum(r.state == "downloading" for r in self.model.rows())
        done = sum(r.state in ("done", "partial") for r in self.model.rows())
        self.border_title = f"下载任务  {len(self.model)} · 下载中 {active} · 完成 {done}"

    @staticmethod
    def _metrics(row: Row) -> tuple[str, str]:
        if (row.state != "downloading" or row.speed_bps <= 0
                or time.monotonic() - row.speed_updated_at > 2):
            return "--", "--"
        speed = row.speed_bps
        label = format_speed(speed)
        seconds = int(row.eta_seconds) if row.eta_seconds is not None else None
        eta = f"{seconds // 60:02d}:{seconds % 60:02d}" if seconds is not None else "--"
        return label, eta

    # --- called by message handlers (main thread) ---
    def refresh_model(self) -> None:
        rows = self.visible_rows()
        ids = [row.download_id for row in rows]
        wanted = set(ids)
        removed = self._rendered.keys() - wanted
        if len(removed) > 100:
            self._full_refresh()
            return
        selected = self._cursor_download_id()
        for did in removed:
            self.remove_row(str(did))
            self._rendered.pop(did)
        for row in rows:
            signature = self._signature(row)
            if row.download_id not in self._rendered:
                self.add_row(*self._cells(row), key=str(row.download_id))
            elif self._rendered[row.download_id] != signature:
                row_index = self.get_row_index(str(row.download_id))
                for col, value in enumerate(self._cells(row)):
                    self.update_cell_at(Coordinate(row_index, col), value)
            self._rendered[row.download_id] = signature
        if ids != self._visible_ids:
            rank = {did: i for i, did in enumerate(ids)}
            self.sort(key=lambda cells: rank[cells[0].download_id])
            self._visible_ids = ids
            if selected in wanted:
                self.move_cursor(row=rank[selected])
        self._refresh_summary()

    # Positional columns are shared with on_mount and _full_refresh.
    _COL_PROGRESS = 1
    _COL_STATUS = 4

    def refresh_row_by_id(self, download_id: int) -> None:
        """Re-render a single row in place, without rebuilding the table.

        Progress ticks fire on every download callback, so a full
        ``_full_refresh()`` here would clear and rebuild the whole DataTable
        each tick — flickering the table, resetting the cursor, and dropping
        focus on long downloads. Instead, update only the cells that can change
        (进度 / 状态 / 操作) on the existing row. Falls back to a full refresh
        only when the row key is not yet rendered (e.g. a just-added row that
        hasn't been laid out, or a key miss after a delete).
        """
        row = self.model.get(download_id)
        if row is None:
            return
        visible = download_id in self._rendered
        if self._matches(row) != visible:
            self.refresh_model()
            return
        if not visible:
            return
        try:
            row_index = self.get_row_index(str(download_id))
        except (KeyError, ValueError):
            self._full_refresh()
            return
        for col, value in enumerate(self._cells(row)):
            self.update_cell_at(Coordinate(row_index, col), value)
        self._rendered[download_id] = self._signature(row)
