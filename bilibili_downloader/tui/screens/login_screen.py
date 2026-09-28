"""Login screen — QR scan (terminal ASCII) + manual SESSDATA, with logout.

Modal Screen pushed from the sidebar/login button. Two columns: left renders
the QR in-terminal and polls; right pastes a SESSDATA cookie. On success the
sessdata is saved via the shared ``ConfigManager`` and the API client rebuilt.
"""

from __future__ import annotations

import logging

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.widgets.qr_view import QRView

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2.0


class LoginScreen(ModalScreen):
    DEFAULT_CSS = """
    LoginScreen { align: center middle; }
    LoginScreen > Vertical {
        width: 64; max-width: 96%; height: auto; max-height: 92%;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    LoginScreen #ls-title { color: #ffffff; text-style: bold; }
    /* Stacked: QR on top (full width), Cookie below — compact (no long hint,
       input + validate on one row) so the modal stays short and the QR has room. */
    LoginScreen .ls-qr-block { height: auto; width: 1fr; }
    LoginScreen .ls-cookie-block { height: auto; width: 1fr; margin-top: 1; }
    LoginScreen .ls-heading { color: #e8e8ec; text-style: bold; }
    LoginScreen .ls-muted { color: #a0a0aa; }
    LoginScreen Input { background: #1b1b1f; border: solid #33333a; }
    LoginScreen #sessdata-input { height: 3; width: 1fr; }
    LoginScreen .ls-row { height: auto; padding: 0 0 1 0; }
    LoginScreen .ls-tight { height: auto; padding: 0; }
    LoginScreen #ls-close { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; }
    LoginScreen #ls-regen { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    LoginScreen #ls-validate { background: #6366f1; color: #ffffff; border: solid #7c7ff5; }
    LoginScreen #ls-logout { background: #f85149 10%; color: #f85149; border: solid #f85149 40%; }
    LoginScreen QRView { height: auto; width: 1fr; }
    """

    QR_POLL = "qr_poll"  # set_interval name

    def __init__(self, has_session: bool):
        super().__init__()
        self._has_session = has_session
        self._qrcode_key: str | None = None
        self._pending_sessdata: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("登录 B 站账号", id="ls-title")
            # QR on top (full width so it never truncates).
            with Vertical(classes="ls-qr-block"):
                yield Static("扫码登录", classes="ls-heading")
                yield QRView(id="qr")
                yield Static("等待生成二维码…", id="qr-status", classes="ls-muted")
                with Horizontal(classes="ls-row"):
                    yield Button("重新生成二维码", id="ls-regen")
            # Cookie section below — compacted: short label, input + validate one row.
            with Vertical(classes="ls-cookie-block"):
                yield Static("手动输入 Cookie", classes="ls-heading")
                with Horizontal(classes="ls-tight"):
                    yield Input(placeholder="粘贴 SESSDATA", id="sessdata-input", password=True)
                    yield Button("验证并登录", id="ls-validate")
                if self._has_session:
                    with Horizontal(classes="ls-tight"):
                        yield Button("退出登录", id="ls-logout")
            with Horizontal(classes="ls-row"):
                yield Button("关闭", id="ls-close")

    def on_mount(self) -> None:
        self._regenerate_qr()
        self.set_interval(POLL_INTERVAL, self._poll_qr, name=self.QR_POLL)

    # --- QR flow ---
    def _regenerate_qr(self) -> None:
        self._qrcode_key = None
        self.query_one("#qr-status", Static).update("正在生成二维码…")
        self.app.start_qr_generate()

    def _poll_qr(self) -> None:
        if self._qrcode_key:
            self.app.start_qr_poll(self._qrcode_key)

    # Called by the App (it owns the @on handlers for app-posted messages;
    # Screen-level @on does NOT receive messages posted via app.post_message).
    def on_qr_generated(self, message: messages.QrGenerated) -> None:
        self._qrcode_key = message.qrcode_key
        self.query_one("#qr", QRView).render_qr(message.url)
        self.query_one("#qr-status", Static).update("请用手机 B 站 App 扫描上方二维码")

    def on_qr_poll_result(self, message: messages.QrPollResult) -> None:
        status = message.status
        status_label = self.query_one("#qr-status", Static)
        if status == 0 and message.sessdata:
            status_label.update("登录成功，正在保存…")
            self._finish_login(message.sessdata)
        elif status == 86090:
            status_label.update("已扫描，请在手机上确认登录…")
        elif status == 86038:
            status_label.update("二维码已过期，正在重新生成…")
            self._regenerate_qr()
        elif status == -1:
            status_label.update("网络异常，稍后重试…")

    def on_sessdata_validated(self, message: messages.SessdataValidated) -> None:
        if message.ok:
            self.app.notify("登录成功")
            self._finish_login(message.sessdata)
        else:
            self.app.notify("SESSDATA 无效或已过期", severity="error")

    # --- SESSDATA flow ---
    @on(Button.Pressed, "#ls-validate")
    def _on_validate(self) -> None:
        sessdata = self.query_one("#sessdata-input", Input).value.strip()
        if not sessdata:
            self.app.notify("请输入 SESSDATA", severity="warning")
            return
        self._pending_sessdata = sessdata
        self.app.start_sessdata_validate(sessdata)

    @on(Button.Pressed, "#ls-regen")
    def _on_regen(self) -> None:
        self._regenerate_qr()

    @on(Button.Pressed, "#ls-logout")
    def _on_logout(self) -> None:
        self.app.logout()
        self.app.notify("已退出登录")
        self.dismiss(True)

    @on(Button.Pressed, "#ls-close")
    def _on_close(self) -> None:
        self.dismiss(False)

    def _finish_login(self, sessdata: str) -> None:
        self.app.apply_login(sessdata)
        self.dismiss(True)
