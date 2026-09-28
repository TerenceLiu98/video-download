"""URL input bar — paste a link and resolve.

The TUI analog of the Qt hero panel's URL row (now a compact top command bar
after the GUI redesign). Enter or the 解析 button triggers resolve.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input


class UrlBar(Horizontal):
    DEFAULT_CSS = """
    UrlBar { height: auto; padding: 0; width: 1fr; }
    UrlBar Input {
        background: #161619;
        border: solid #33333a;
        height: 3;
        width: 1fr;
    }
    UrlBar Input:focus { border: solid #6366f1; }
    UrlBar #resolve-btn {
        background: #6366f1;
        color: #ffffff;
        border: solid #7c7ff5;
        text-style: bold;
        height: 3;
        min-width: 10;
    }
    UrlBar #resolve-btn:hover { background: #7c7ff5; }
    UrlBar #resolve-btn:disabled { background: #1b1b1f; color: #6b6b75; border: solid #26262b; }
    """

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="粘贴 B 站链接、BV / AV 号或 b23.tv 短链",
            id="url-input",
        )
        yield Button("解析", id="resolve-btn", classes="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "url-input":
            self.query_one("#resolve-btn", Button).press()

    def on_key(self, event: events.Key) -> None:
        # Let Enter on the bar trigger resolve when focus is on the button.
        if event.key == "enter" and self.query_one("#resolve-btn", Button).has_focus:
            self.query_one("#resolve-btn", Button).press()
