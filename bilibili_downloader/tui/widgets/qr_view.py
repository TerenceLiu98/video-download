"""QR code view — renders the login QR as monospace block characters.

Reuses the ``qrcode`` library (already a dependency) to render the URL into a
``StringIO`` via ``QRCode.print_ascii(invert=True)``, then displays it. The QR
art is stored as explicit lines and rendered via a Rich ``Text`` built with
``Text.append`` per line so Textual never wraps it (a naive ``Static.update``
wraps lines whose display width exceeds the container, mangling the QR).
"""

from __future__ import annotations

import io

from rich.text import Text
from textual.widgets import Static


class QRView(Static):
    """A Static that displays a QR code rendered as ASCII block art."""

    DEFAULT_CSS = """
    QRView {
        height: auto;
        width: 1fr;
        padding: 0;
        /* No background/border: blend into the modal so the QR reads as art
           on the dialog surface, not a pasted-on patch. */
        background: transparent;
        border: none;
        content-align: center middle;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines: list[str] = []

    def render_qr(self, url: str) -> None:
        import qrcode

        buf = io.StringIO()
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.print_ascii(invert=True, out=buf)
        # Drop the trailing newline; keep each module row as an explicit line.
        self._lines = buf.getvalue().rstrip("\n").split("\n")
        self.refresh()

    def render(self) -> Text:
        if not self._lines:
            return Text("（等待生成二维码）", style="#a0a0aa")
        # Foreground only (no background) so the QR sits on the modal surface.
        text = Text(no_wrap=True)
        text.append("\n".join(self._lines), style="#e8e8ec")
        return text
