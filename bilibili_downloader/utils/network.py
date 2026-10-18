"""Network boundary helpers for URLs supplied by remote API responses."""

import re
from urllib.parse import urlparse, urlunparse

BILIBILI_WEB_HOSTS = ("bilibili.com", "b23.tv")
BILIBILI_RESOURCE_HOSTS = (
    "bilibili.com",
    "bilivideo.com",
    "bilivideo.cn",
    "hdslb.com",
    "edge.mountaintoys.cn",
)
MEDIA_HTTPS_PORTS = (None, 443, 4483)
# Bilibili's overseas mirrors on Akamai. Only Bilibili's own host names are
# trusted, never all of akamaized.net (any Akamai customer can serve there).
# Media requests carry no cookies, so this never widens where SESSDATA goes.
BILIBILI_MEDIA_HOST_PATTERNS = (re.compile(r"upos-[a-z]{2}-mirrorakam\.akamaized\.net"),)


def _domain_matches(hostname: str, allowed_domains: tuple[str, ...]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


def trusted_https_url(url: str, allowed_domains: tuple[str, ...]) -> str:
    """Return a normalized HTTPS URL or raise for an untrusted destination."""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("仅允许不含凭据的 HTTPS 地址")
    if not _domain_matches(hostname, allowed_domains):
        raise ValueError(f"不受信任的网络目标：{hostname}")
    return url


def trusted_media_url(
    url: str,
    allowed_domains: tuple[str, ...],
    host_patterns: tuple[re.Pattern, ...] = BILIBILI_MEDIA_HOST_PATTERNS,
) -> str:
    """Upgrade a trusted CDN HTTP URL to HTTPS, then validate it strictly.

    Only for cookie-less media/cover requests: ``host_patterns`` admits CDN
    hosts outside ``allowed_domains``.
    """
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    trusted_host = _domain_matches(hostname, allowed_domains) or any(
        pattern.fullmatch(hostname) for pattern in host_patterns
    )
    if trusted_host and not parsed.username and not parsed.password:
        if parsed.scheme == "https" and parsed.port in MEDIA_HTTPS_PORTS:
            return url
        if parsed.scheme == "http" and parsed.port in (None, 80):
            netloc = hostname if parsed.port is None else f"{hostname}:443"
            return urlunparse(parsed._replace(scheme="https", netloc=netloc))
    # Raises with the specific reason (scheme, credentials, port or host).
    return trusted_https_url(url, allowed_domains)
