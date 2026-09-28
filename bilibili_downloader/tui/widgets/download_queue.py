"""Download queue — model + live DataTable widget.

A direct port of ``gui/widgets/download_list.py`` semantics onto Textual's
``DataTable``. The model (``DownloadQueueModel``) owns the stable download-id →
row mapping and all state transitions, so it is unit-testable without the
widget. The widget renders the model and drives it via keybindings.

The task workspace filters and sorts rows without changing model identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable

from bilibili_downloader.core.models import VIDEO_CODEC_MAP, DownloadItem, VideoQuality

_BAR_LEN = 10


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
    video_path: str = ""


class DownloadQueueModel:
    """Stable-id download queue state (no UI dependency)."""

    def __init__(self):
        self._rows: dict[int, Row] = {}
        self._order: list[int] = []
        self._next_id = 0
        self._workers: dict[int, object] = {}

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
        from dataclasses import asdict

        from bilibili_downloader.core.creator import atomic_write_json

        records = []
        for row in self.rows():
            record = asdict(row)
            record["item"] = row.item.model_dump(mode="json") if row.item else None
            if record["item"]:
                # Signed stream URLs expire; only persist the video identity/options.
                info = record["item"]["video_info"]
                info["video_streams"] = []
                info["audio_streams"] = []
                info["subtitle_list"] = []
            records.append(record)
        atomic_write_json(path, {"version": 1, "rows": records})

    def restore(self, path: Path) -> None:
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
            if row.item:
                row.title = row.item.video_info.title
            row.speed_bps, row.eta_seconds = 0.0, None
            if row.state in ("pending", "downloading", "retry", "pausing"):
                row.state, row.status = "paused", "已恢复，等待继续"
            restored[row.download_id] = row
        self._rows = restored
        self._order = list(restored)
        self._next_id = max(self._order, default=-1) + 1

    # --- mutation ---
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

    def delete(self, download_id: int) -> None:
        if download_id in self._workers:
            return
        self._workers.pop(download_id, None)
        self._rows.pop(download_id, None)
        if download_id in self._order:
            self._order.remove(download_id)

    def cancel_all(self) -> None:
        for did in list(self._workers):
            self.cancel(did)


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
        self.filter_state = "all"
        self.search = ""
        self._visible_ids = []
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
        states = {
            "active": {"downloading", "pausing"}, "pending": {"pending", "retry"},
            "paused": {"paused", "cancelled"}, "failed": {"failed", "error"},
            "partial": {"partial"}, "done": {"done"},
        }
        priority = {"downloading": 0, "pausing": 0, "failed": 1, "error": 1,
                    "partial": 2, "pending": 3, "retry": 3, "paused": 4, "cancelled": 4, "done": 5}
        rows = [r for r in self.model.rows()
                if (self.filter_state == "all" or r.state in states.get(self.filter_state, set()))
                and self.search.casefold() in (r.title + " " + (r.item.video_info.bvid if r.item else "")).casefold()]
        return sorted(rows, key=lambda r: (priority.get(r.state, 6), r.download_id))

    def _cells(self, row: Row):
        color = {"failed": "red", "error": "red", "partial": "yellow",
                 "done": "green", "downloading": "cyan"}.get(row.state, "white")
        status = Text(row.status, style=color, no_wrap=True, overflow="ellipsis")
        title = Text(row.title, no_wrap=True, overflow="ellipsis")
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
        rows = self.visible_rows()
        self._visible_ids = [r.download_id for r in rows]
        for row in rows:
            self.add_row(*self._cells(row), key=str(row.download_id))
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
        if row.state != "downloading" or row.speed_bps <= 0:
            return "--", "--"
        speed = row.speed_bps
        label = f"{speed / 1048576:.1f} MiB/s" if speed >= 1048576 else f"{speed / 1024:.1f} KiB/s"
        seconds = int(row.eta_seconds) if row.eta_seconds is not None else None
        eta = f"{seconds // 60:02d}:{seconds % 60:02d}" if seconds is not None else "--"
        return label, eta

    # --- called by message handlers (main thread) ---
    def refresh_model(self) -> None:
        self._full_refresh()

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
        if [r.download_id for r in self.visible_rows()] != self._visible_ids:
            self._full_refresh()
            return
        if download_id not in self._visible_ids:
            return
        try:
            row_index = self.get_row_index(str(download_id))
        except (KeyError, ValueError):
            self._full_refresh()
            return
        for col, value in enumerate(self._cells(row)):
            self.update_cell_at(Coordinate(row_index, col), value)
        self._refresh_summary()
