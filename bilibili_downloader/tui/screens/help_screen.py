"""Help screen — keyboard shortcut reference."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from bilibili_downloader import __version__

HELP = """\
全局快捷键
  Ctrl+L   登录 / 退出登录
  Ctrl+B   批量导入链接
  Ctrl+U   UP 主投稿索引
  Ctrl+,   下载设置
  Ctrl+T   切换明 / 暗主题
  Ctrl+Q   退出（有任务在跑会确认）
  F1       本帮助

下载队列（光标在队列上时）
  c        取消当前任务
  r        重试失败任务
  Delete   删除一行
  C        取消全部任务

关于
  哔哩哔哩视频下载器 v{}

一款桌面端B站视频下载工具。
支持4K、HDR、杜比视界、弹幕和字幕下载。
""".format(__version__)


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical {
        width: 64; max-width: 90%; height: auto;
        background: #161619; border: solid #33333a; padding: 1 2;
    }
    HelpScreen #hp-text { color: #e8e8ec; }
    HelpScreen #hp-close { background: #1b1b1f; color: #e8e8ec; border: solid #33333a; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(HELP, id="hp-text")
            yield Button("关闭", id="hp-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
