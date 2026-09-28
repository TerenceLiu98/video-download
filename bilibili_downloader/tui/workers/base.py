"""Base class for TUI background workers.

All core calls (api/core) are blocking + synchronous; the TUI runs them on
threads via ``@work(thread=True)``. This base owns two bridges back to the
Textual event loop:

* **cancel** — a ``threading.Event`` per long task. The ``cancel_checker``
  property is passed straight into core's ``cancel_checker=`` hook.
* **progress** — ``emit()`` posts a Textual ``Message`` via ``app.post_message``,
  which the event loop routes to widget ``on_*`` handlers on the main thread.

Worker subclasses are plain objects; a ``@work(thread=True)`` method on the
App or a Widget instantiates one and calls ``run()``. This keeps Textual's
worker lifecycle intact while letting the plain classes be unit-tested.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.message import Message

    from bilibili_downloader.tui.app import BiliFlowTUI


class CoreWorker:
    """Owns a cooperative-cancel event and a post_message bridge."""

    def __init__(self, app: "BiliFlowTUI"):
        self.app = app
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Signal the running task to stop (cooperative)."""
        self._cancel.set()

    @property
    def cancel_checker(self):
        """Callable suitable for core's ``cancel_checker=`` argument."""
        return self._cancel.is_set

    def emit(self, message: "Message") -> None:
        """Post a message from the worker thread onto the Textual event loop."""
        self.app.post_message(message)
