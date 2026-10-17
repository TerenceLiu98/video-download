"""Failure classification shared by the GUI, TUI and CLI schedulers."""

from __future__ import annotations

import socket

import httpx

from bilibili_downloader.api.client import BilibiliAPIError
from bilibili_downloader.utils.cancellation import OperationCancelled

# HTTP statuses that clear up on their own: timeouts, risk control and CDN errors.
_TRANSIENT_STATUS = {408, 412, 429, 500, 502, 503, 504}
# Bilibili risk-control / rate-limit envelope codes.
_TRANSIENT_API_CODES = {-352, -412, -509, -799}


class TransientDownloadError(RuntimeError):
    """A download failed for a reason that a later retry may resolve."""


def is_transient_error(exc: BaseException | None) -> bool:
    """Return whether ``exc`` (or the error it wraps) is worth retrying later."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OperationCancelled):
            return False
        if isinstance(exc, (TransientDownloadError, httpx.TransportError,
                            ConnectionError, TimeoutError, socket.gaierror)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _TRANSIENT_STATUS
        if isinstance(exc, BilibiliAPIError):
            return exc.code in _TRANSIENT_API_CODES
        exc = exc.__cause__
    return False
