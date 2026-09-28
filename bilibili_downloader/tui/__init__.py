"""BiliFlow terminal UI (Textual).

Launch via ``bilibili-downloader tui`` or ``python -m bilibili_downloader.tui``.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback


def launch() -> None:
    """Create and run the BiliFlowTUI app."""
    _install_excepthooks()
    from bilibili_downloader.tui.app import BiliFlowTUI

    BiliFlowTUI().run()


def _install_excepthooks() -> None:
    """Route uncaught exceptions to the logger instead of stderr.

    The TUI owns the full terminal, so Python's default stderr traceback is
    invisible — and swallowed entirely once the console handler is dropped.
    These hooks make every uncaught exception (on the main thread *and* on
    background worker threads) land in ``biliflow.log`` so crashes are
    diagnosable after the fact.
    """
    logger = logging.getLogger("bilibili_downloader.tui")

    def _log_threading(args) -> None:  # threading.ExcepthookInfo
        tb = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        logger.critical("Uncaught exception in thread %r:\n%s", args.thread.name, tb)

    def _log_sys(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical("Uncaught exception on main thread:\n%s", tb)

    sys.excepthook = _log_sys
    threading.excepthook = _log_threading


__all__ = ["launch"]
