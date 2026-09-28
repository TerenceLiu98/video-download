"""Left navigation sidebar — brand, nav buttons, login pill/action.

Docked on the left of the main screen. The first nav ("首页") is the home
view; the rest open modal screens. The login button shows the current login
state (mirrors the Qt sidebar + header ghost button).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Static


class NavSidebar(Vertical):
    DEFAULT_CSS = """
    NavSidebar {
        dock: left;
        width: 26;
        background: #0a0a0c;
        border-right: solid #26262b;
        padding: 1 1;
        layer: base;
    }
    NavSidebar Static { background: transparent; }
    NavSidebar #brand-title { color: #ffffff; text-style: bold; }
    NavSidebar #brand-caption { color: #6b6b75; }
    NavSidebar #nav-section { color: #6b6b75; padding: 1 1 0 1; }
    NavSidebar .nav-btn {
        background: transparent;
        color: #a0a0aa;
        border-left: tall transparent;
        text-align: left;
        height: 3;
        width: 1fr;
    }
    NavSidebar .nav-btn:hover { color: #e8e8ec; background: #161619; }
    NavSidebar .nav-btn.--active {
        color: #e8e8ec;
        background: #1e1e3a;
        border-left: tall #6366f1;
    }
    NavSidebar #login-btn {
        background: #161619;
        color: #e8e8ec;
        border: solid #26262b;
        margin-top: 1;
        width: 1fr;
    }
    NavSidebar #login-btn:hover { border: solid #33333a; }
    """

    def compose(self) -> ComposeResult:
        yield Static("BiliFlow", id="brand-title")
        yield Static("Bilibili 下载器", id="brand-caption")
        yield Static("工作区", id="nav-section")
        yield Button("首页", id="nav-home", classes="nav-btn --active")
        yield Button("批量任务", id="nav-batch", classes="nav-btn")
        yield Button("UP 主投稿", id="nav-creator", classes="nav-btn")
        yield Button("下载设置", id="nav-settings", classes="nav-btn")
        yield Static("", id="nav-spacer", classes="nav-spacer")
        yield Button("登录 B 站账号", id="login-btn")

    def set_login_label(self, label: str) -> None:
        self.query_one("#login-btn", Button).label = label
