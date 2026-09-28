"""Download queue — model + live DataTable widget.

A direct port of ``gui/widgets/download_list.py`` semantics onto Textual's
``DataTable``. The model (``DownloadQueueModel``) owns the stable download-id →
row mapping and all state transitions, so it is unit-testable without the
widget. The widget renders the model and drives it via keybindings.

Status strings mirror the Qt GUI exactly: 等待中 / 下载中 / 完成 / 部分完成 /
失败：… / 已取消 / 重试中… / 取消中… / 解析失败 / 未加入队列.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable

from bilibili_downloader.core.models import VIDEO_CODEC_MAP, VideoQuality

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

    # --- mutation ---
    def add(self, item) -> int:
        """Add a download item. Returns a stable download id."""
        download_id = self._next_id
        self._next_id += 1
        self._rows[download_id] = Row(
            download_id=download_id,
            item=item,
            title=(item.video_info.title[:48] if item and item.video_info else ""),
            spec=_spec_label(item),
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

    def set_progress(self, download_id: int, pct: float, status_text: str) -> None:
        row = self._rows.get(download_id)
        if row is None:
            return
        row.pct = int(pct * 100)
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
        row.status = "已取消"
        row.state = "cancelled"

    def mark_cancel_in_flight(self, download_id: int) -> None:
        row = self._rows.get(download_id)
        if row is None:
            return
        row.status = "取消中…"
        row.state = "downloading"

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
        self._workers.pop(download_id, None)
        self._rows.pop(download_id, None)
        if download_id in self._order:
            self._order.remove(download_id)

    def cancel_all(self) -> None:
        for worker in list(self._workers.values()):
            if worker is not None:
                worker.cancel()


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
        ("c", "cancel_cursor", "取消"),
        ("r", "retry_cursor", "重试"),
        ("delete", "delete_cursor", "删除"),
        ("C", "cancel_all", "全部取消"),
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
        super().__init__()

    def on_mount(self) -> None:
        self.add_columns("视频", "规格", "进度", "状态", "操作")
        self._full_refresh()

    def _cursor_download_id(self) -> int | None:
        """Return the download_id of the row under the cursor, or None."""
        try:
            row_key, _ = self.coordinate_to_row_key(self.cursor_coordinate)
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
        self.clear()
        for row in self.model.rows():
            self.add_row(
                row.title,
                row.spec,
                _progress_bar(row.pct, row.state),
                row.status,
                self._action_hint(row),
                key=str(row.download_id),
            )

    @staticmethod
    def _action_hint(row: Row) -> str:
        if row.state in ("pending", "downloading", "retry"):
            return "c 取消"
        if row.state == "failed":
            return "r 重试 · ⌫ 删除"
        return "⌫ 删除"

    # --- called by message handlers (main thread) ---
    def refresh_model(self) -> None:
        self._full_refresh()

    # Column layout is fixed by on_mount's add_columns: 0 视频, 1 规格,
    # 2 进度, 3 状态, 4 操作. add_columns with bare labels yields integer
    # (None-named) column keys, so a name lookup misses — use the stable
    # positional indices for the cells that change on a progress tick.
    _COL_PROGRESS = 2
    _COL_STATUS = 3
    _COL_ACTION = 4

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
        try:
            row_index = self.get_row_index(str(download_id))
        except (KeyError, ValueError):
            self._full_refresh()
            return
        progress = _progress_bar(row.pct, row.state)
        self.update_cell_at(Coordinate(row_index, self._COL_PROGRESS), progress)
        self.update_cell_at(Coordinate(row_index, self._COL_STATUS), row.status)
        self.update_cell_at(Coordinate(row_index, self._COL_ACTION), self._action_hint(row))
