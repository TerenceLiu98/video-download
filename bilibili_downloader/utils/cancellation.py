"""Interrupt synchronous HTTP reads without closing clients from another thread."""

import socket
import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("request_cancellation", default=None)


def attach_current_request(request):
    scope = _current.get()
    if scope is not None:
        scope.attach(request)


class RequestCancellation:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._sockets = weakref.WeakSet()

    def attach(self, request):
        if self.cancelled.is_set():
            raise RuntimeError("Request cancelled")
        request.extensions["trace"] = self.trace

    @contextmanager
    def bind(self):
        token = _current.set(self)
        try:
            yield
        finally:
            _current.reset(token)

    def trace(self, event, info):
        if event in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            stream = info.get("return_value")
            sock = stream.get_extra_info("socket") if stream else None
            if sock is not None:
                with self._lock:
                    self._sockets.add(sock)
                if self.cancelled.is_set():
                    self._interrupt(sock)

    @staticmethod
    def _interrupt(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            sockets = list(self._sockets)
        for sock in sockets:
            self._interrupt(sock)
