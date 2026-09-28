"""Textual Message types bridging background workers → UI.

Workers run on threads (``@work(thread=True)``) and post these messages via
``app.post_message(...)``. The Textual event loop routes them to widget
``on_*`` handlers on the main thread. Keeping them in one module avoids
circular imports between ``tui/workers/*`` and ``tui/screens/*``.
"""

from __future__ import annotations

from textual.message import Message


# --- Resolve ---------------------------------------------------------------
class ResolveFinished(Message):
    def __init__(self, info, video_streams, audio_streams, playurl_ok: bool):
        super().__init__()
        self.info = info
        self.video_streams = video_streams
        self.audio_streams = audio_streams
        self.playurl_ok = playurl_ok


class ResolveFailed(Message):
    def __init__(self, source: str, error: str):
        super().__init__()
        self.source = source
        self.error = error


# --- Download --------------------------------------------------------------
class DownloadProgress(Message):
    def __init__(self, download_id: int, pct: float, status_text: str):
        super().__init__()
        self.download_id = download_id
        self.pct = pct
        self.status_text = status_text


class DownloadFinished(Message):
    def __init__(self, download_id: int, outcome):
        super().__init__()
        self.download_id = download_id
        self.outcome = outcome


class DownloadFailed(Message):
    def __init__(self, download_id: int, error: str):
        super().__init__()
        self.download_id = download_id
        self.error = error


class DownloadCancelled(Message):
    def __init__(self, download_id: int):
        super().__init__()
        self.download_id = download_id


# --- Batch -----------------------------------------------------------------
class BatchItemReady(Message):
    def __init__(self, item):
        super().__init__()
        self.item = item


class BatchItemFailed(Message):
    def __init__(self, error: str):
        super().__init__()
        self.error = error


class BatchItemRetrying(Message):
    """A transient/风控 error hit during batch resolve; backing off + retrying."""

    def __init__(self, source: str, attempt: int, delay: float):
        super().__init__()
        self.source = source
        self.attempt = attempt
        self.delay = delay


class BatchProgress(Message):
    """How many of the batch inputs have been resolved so far."""

    def __init__(self, done: int, total: int):
        super().__init__()
        self.done = done
        self.total = total


class BatchDone(Message):
    pass


# --- Creator ---------------------------------------------------------------
class CreatorProgress(Message):
    def __init__(self, done: int, total: int):
        super().__init__()
        self.done = done
        self.total = total


class CreatorStatus(Message):
    def __init__(self, text: str):
        super().__init__()
        self.text = text


class CreatorFinished(Message):
    def __init__(self, index):
        super().__init__()
        self.index = index


class CreatorFailed(Message):
    def __init__(self, error: str):
        super().__init__()
        self.error = error


# --- Login -----------------------------------------------------------------
class LoginStatusResult(Message):
    def __init__(self, request_id: int, nav_info: dict):
        super().__init__()
        self.request_id = request_id
        self.nav_info = nav_info


class LoginStatusError(Message):
    def __init__(self, request_id: int, error: str):
        super().__init__()
        self.request_id = request_id
        self.error = error


class QrGenerated(Message):
    def __init__(self, url: str, qrcode_key: str):
        super().__init__()
        self.url = url
        self.qrcode_key = qrcode_key


class QrPollResult(Message):
    def __init__(self, status: int, sessdata: str | None = None):
        super().__init__()
        self.status = status
        self.sessdata = sessdata


class SessdataValidated(Message):
    def __init__(self, ok: bool, sessdata: str):
        super().__init__()
        self.ok = ok
        self.sessdata = sessdata


# --- FFmpeg ----------------------------------------------------------------
class FFmpegResult(Message):
    def __init__(self, available: bool, message: str):
        super().__init__()
        self.available = available
        self.message = message
