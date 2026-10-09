"""SSRF-safe download of user-supplied URLs (voice audio_url).

Only HTTPS URLs whose host is on an allowlist are fetched, every resolved
address must be public, redirects are followed manually so each hop is
re-checked, and the body is streamed with a hard size cap.
"""

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlsplit

import httpx

# Twilio serves recording/media URLs from api.twilio.com, which redirects to
# its media CDN. Override with a comma-separated list; "*.example.com"
# matches subdomains only.
DEFAULT_ALLOWED_HOSTS = "api.twilio.com,*.twiliocdn.com"
MAX_REDIRECTS = 3


class UnsafeURLError(ValueError):
    """The URL is not allowed to be fetched."""


def allowed_hosts() -> list[str]:
    raw = os.getenv("AUDIO_URL_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def host_is_allowed(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def _address_is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def validate_url(url: str) -> None:
    """Raise UnsafeURLError unless ``url`` is HTTPS, allowlisted and public."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURLError("only https URLs are allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    # Literal IPs are never on the allowlist, but check them explicitly so
    # the error is clear even if someone allowlists one.
    try:
        ipaddress.ip_address(host)
        raise UnsafeURLError("IP-literal hosts are not allowed")
    except ValueError:
        pass
    if not host_is_allowed(host, allowed_hosts()):
        raise UnsafeURLError(f"host {host!r} is not on the allowlist")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURLError(f"cannot resolve {host!r}") from e
    for info in infos:
        addr = info[4][0]
        if not _address_is_public(addr):
            raise UnsafeURLError(f"{host!r} resolves to non-public address {addr}")


async def fetch_bytes(
    url: str,
    max_bytes: int,
    timeout: httpx.Timeout | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    """Download ``url`` after SSRF checks; raise UnsafeURLError or httpx errors."""
    timeout = timeout or httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=transport
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await validate_url(url)
            async with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeURLError("redirect without Location")
                    url = urljoin(url, location)
                    continue
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UnsafeURLError("response exceeds size limit")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise UnsafeURLError("response exceeds size limit")
                return bytes(body)
    raise UnsafeURLError("too many redirects")
