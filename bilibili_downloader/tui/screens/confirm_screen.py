"""Generic confirmation modal screen.

Used by the quit-with-active-tasks prompt (parity with the Qt
``MainWindow.closeEvent`` confirmation). ``dismiss(True)`` on confirm,
``dismiss(False)`` otherwise.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 72; max-width: 88%; height: auto;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    ConfirmScreen #cf-msg { color: #e8e8ec; margin-bottom: 1; }
    ConfirmScreen #cf-confirm { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    ConfirmScreen #cf-cancel { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    """

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._message, id="cf-msg")
            yield Button("确认", id="cf-confirm")
            yield Button("取消", id="cf-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cf-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)
