"""Visible, non-blocking shutdown progress."""

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ShutdownScreen(ModalScreen):
    DEFAULT_CSS = """
    ShutdownScreen { align: center middle; }
    ShutdownScreen > Vertical {
        width: 64; max-width: 90%; height: auto; padding: 1 2;
        border: solid #33333a; background: #161619;
    }
    ShutdownScreen Static { height: auto; }
    ShutdownScreen Button { display: none; }
    """

    def compose(self):
        with Vertical():
            yield Static("正在退出…", id="shutdown-status", markup=False)
            yield Button("重试保存", id="shutdown-retry")

    def set_status(self, text, retry=False):
        self.query_one(Static).update(text)
        self.query_one(Button).display = retry

    def on_mount(self):
        self.app._finish_shutdown()

    def on_button_pressed(self, event):
        event.stop()
        self.query_one(Button).display = False
        self.app._finish_shutdown()
