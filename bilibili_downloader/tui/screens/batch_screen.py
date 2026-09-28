"""Batch screen — paste many URLs, preview validity, enqueue.

Modal Screen. A multi-line TextArea for pasting links; a debounced live preview
runs ``classify_batch_inputs`` (pure + fast, inline) and shows valid/invalid
counts. "加入队列" enqueues the valid list and dismisses.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from bilibili_downloader.core.batch import classify_batch_inputs


class BatchScreen(ModalScreen):
    DEFAULT_CSS = """
    BatchScreen { align: center middle; }
    BatchScreen > Vertical {
        width: 92; max-width: 92%; height: auto; max-height: 88%;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    BatchScreen #bs-title { color: #ffffff; text-style: bold; }
    BatchScreen .bs-muted { color: #a0a0aa; }
    BatchScreen TextArea {
        height: 12; background: #1b1b1f; border: solid #33333a; color: #e8e8ec;
    }
    BatchScreen #bs-preview { color: #a0a0aa; }
    BatchScreen #bs-enqueue { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    BatchScreen #bs-cancel { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("批量导入链接", id="bs-title")
            yield Static("每行一个 B 站链接、BV / AV 号或 b23.tv 短链", classes="bs-muted")
            yield TextArea(id="bs-text")
            yield Static("有效 0 · 无效 0", id="bs-preview")
            yield Horizontal(
                Button("加入队列", id="bs-enqueue"),
                Button("取消", id="bs-cancel"),
            )

    @on(TextArea.Changed)
    def _on_text_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "bs-text":
            return
        valid, invalid = classify_batch_inputs(event.text_area.text)
        self.query_one("#bs-preview", Static).update(
            f"有效 {len(valid)} · 无效 {len(invalid)}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "bs-cancel":
            self.dismiss([])
        elif event.button.id == "bs-enqueue":
            valid, _invalid = classify_batch_inputs(self.query_one("#bs-text", TextArea).text)
            if not valid:
                self.app.notify("没有有效的链接", severity="warning")
                return
            self.dismiss(valid)
