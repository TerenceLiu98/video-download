"""``login`` CLI subcommand — QR-code or manual SESSDATA login, headless.

Lets a user obtain and persist Bilibili credentials from the terminal so the
rest of the CLI (``creator``, ``download``, …) can run without ever opening the
Qt GUI. Credentials are saved through the same :class:`ConfigManager` the GUI
uses (keyring + obfuscated JSON fallback), so a login here is immediately
visible to the GUI and vice-versa.

Reuses :class:`bilibili_downloader.api.login.LoginManager` unchanged — that
class is pure httpx and already exposes QR generation, status polling, and
SESSDATA validation.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from bilibili_downloader.api.client import BilibiliAPIClient
from bilibili_downloader.api.login import LoginManager

logger = logging.getLogger(__name__)

# QR poll cadence and ceiling. Bilibili QR codes live ~180s.
_POLL_INTERVAL = 2.0
_POLL_TIMEOUT = 200.0


def cli_login(args: argparse.Namespace) -> None:
    """Handle the ``login`` subcommand."""
    sessdata = getattr(args, "sessdata", None)
    if sessdata:
        _login_with_sessdata(sessdata.strip())
    else:
        _login_with_qr()


def _print_qr_terminal(url: str) -> None:
    """Render a QR code as block-character ASCII art to the terminal.

    Uses the already-depended-on ``qrcode`` library's built-in ASCII renderer.
    ``invert=True`` paints the QR modules as solid blocks, which scans more
    reliably on dark terminal backgrounds than the default.
    """
    import qrcode  # hard dependency in pyproject.toml

    print("\n请使用哔哩哔哩手机 App 扫描下方二维码登录：\n", flush=True)
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.print_ascii(invert=True, out=sys.stdout)
    print(flush=True)


def _login_with_qr() -> None:
    manager = LoginManager()
    try:
        deadline = time.monotonic() + _POLL_TIMEOUT
        while time.monotonic() < deadline:
            try:
                _url, qrcode_key, _img = manager.generate_qr()
            except Exception as exc:  # noqa: BLE001
                print(f"\n生成二维码失败：{exc}", flush=True)
                raise SystemExit(1)

            _print_qr_terminal(_url)
            print("等待扫码…（二维码约 3 分钟后过期，过期会自动重新生成）", flush=True)

            sessdata = _poll_qr(manager, qrcode_key, deadline)
            if sessdata:
                _finalize(sessdata)
                return
            # QR expired (86038) — loop to regenerate.
            print("\n二维码已过期，正在重新生成…", flush=True)

        print("\n登录超时，请重新运行 login。", flush=True)
        raise SystemExit(1)
    finally:
        manager.close()


def _poll_qr(manager: LoginManager, qrcode_key: str, deadline: float) -> str | None:
    """Poll QR status until success/expiry/timeout. Returns SESSDATA or None."""
    while time.monotonic() < deadline:
        try:
            result = manager.check_qr_status(qrcode_key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("QR status poll failed: %s", exc)
            time.sleep(_POLL_INTERVAL)
            continue

        status = result.get("status")
        if status == 0:
            cookies = result.get("cookies") or {}
            sessdata = cookies.get("SESSDATA")
            if sessdata:
                return sessdata
            logger.warning("登录成功但未取到 SESSDATA")
            return None
        if status == 86090:
            print("\r已扫描，请在手机上确认登录…     ", end="", flush=True)
        elif status == 86038:
            return None  # expired — caller regenerates
        # 86101 = still waiting; stay quiet to avoid spamming.
        time.sleep(_POLL_INTERVAL)
    return None


def _login_with_sessdata(sessdata: str) -> None:
    if not sessdata:
        print("SESSDATA 为空。", flush=True)
        raise SystemExit(1)
    _finalize(sessdata)


def _finalize(sessdata: str) -> None:
    """Validate, persist, and confirm the obtained SESSDATA."""
    manager = LoginManager()
    try:
        if not manager.validate_sessdata(sessdata):
            print("SESSDATA 无效或已过期，请重新登录。", flush=True)
            raise SystemExit(1)
    finally:
        manager.close()

    from bilibili_downloader.utils.config import ConfigManager

    config = ConfigManager()
    settings = config.load()
    config.save(settings.model_copy(update={"sessdata": sessdata}))

    uname, mid = _whoami(sessdata)
    if uname:
        print(f"\n登录成功：{uname}（UID {mid}）", flush=True)
    else:
        print("\n登录成功。", flush=True)
    print("凭据已保存，现在可直接使用 CLI，无需打开 GUI：", flush=True)
    print("  bilibili-downloader creator <UP主UID> --download-all", flush=True)


def _whoami(sessdata: str) -> tuple[str, str]:
    """Return (uname, mid) for a freshly logged-in session, or ("", "")."""
    client = BilibiliAPIClient(sessdata=sessdata)
    try:
        nav = client.get_nav_info()
    except Exception as exc:  # noqa: BLE001
        logger.debug("whoami nav fetch failed: %s", exc)
        return "", ""
    finally:
        client.close()
    data = nav.get("data") or {}
    return str(data.get("uname") or ""), str(data.get("mid") or "")
