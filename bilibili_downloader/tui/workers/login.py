"""Login workers — QR generate/poll, SESSDATA validate, login-status check.

Thin thread wrappers over ``api/login.py::LoginManager`` and
``BilibiliAPIClient.get_nav_info``. Mirrors the QR/cookie workers that lived
inside ``gui/dialogs/login_dialog.py``.
"""

from __future__ import annotations

import logging

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)


class QrGenerateWorker(CoreWorker):
    def __init__(self, app):
        super().__init__(app)

    def run(self) -> None:
        from bilibili_downloader.api.login import LoginManager

        manager = LoginManager()
        try:
            url, qrcode_key, _img = manager.generate_qr()
            self.emit(messages.QrGenerated(url, qrcode_key))
        except Exception as exc:  # noqa: BLE001
            logger.error("QR generate failed: %s", exc)
            self.emit(messages.QrPollResult(status=-1))  # signal error to caller
        finally:
            manager.close()


class QrPollWorker(CoreWorker):
    def __init__(self, app, qrcode_key: str):
        super().__init__(app)
        self._qrcode_key = qrcode_key

    def run(self) -> None:
        from bilibili_downloader.api.login import LoginManager

        manager = LoginManager()
        try:
            result = manager.check_qr_status(self._qrcode_key)
            status = result.get("status", -1)
            sessdata = None
            if status == 0:
                sessdata = (result.get("cookies") or {}).get("SESSDATA")
            self.emit(messages.QrPollResult(status=status, sessdata=sessdata))
        except Exception as exc:  # noqa: BLE001
            logger.warning("QR poll failed: %s", exc)
            self.emit(messages.QrPollResult(status=-1))
        finally:
            manager.close()


class SessdataValidateWorker(CoreWorker):
    def __init__(self, app, sessdata: str):
        super().__init__(app)
        self._sessdata = sessdata

    def run(self) -> None:
        from bilibili_downloader.api.login import LoginManager

        manager = LoginManager()
        try:
            ok = manager.validate_sessdata(self._sessdata)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sessdata validate failed: %s", exc)
            ok = False
        finally:
            manager.close()
        self.emit(messages.SessdataValidated(ok=ok, sessdata=self._sessdata))


class LoginStatusWorker(CoreWorker):
    def __init__(self, app, client, request_id: int):
        super().__init__(app)
        self._client = client
        self._request_id = request_id

    def run(self) -> None:
        try:
            nav = self._client.get_nav_info()
            self.emit(messages.LoginStatusResult(self._request_id, nav))
        except Exception as exc:  # noqa: BLE001
            logger.warning("login status check failed: %s", exc)
            self.emit(messages.LoginStatusError(self._request_id, str(exc)))
