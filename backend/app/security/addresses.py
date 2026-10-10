"""Refuse fetches to private, loopback, link-local, multicast and reserved addresses (SSRF).

Users add web pages by URL. Without this check a URL could reach the user's LAN, the app itself
(``localhost:8484``) or the SearXNG container. Every hop of a redirect and every request the
headless browser makes is checked.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]

PRIVATE_ADDRESS_MESSAGE = (
    "This address is on your local network; Trustbook only fetches public web pages."
)


class BlockedAddressError(ValueError):
    """The URL points somewhere Trustbook must not fetch. ``str(exc)`` is plain English."""


def system_resolver(host: str) -> list[str]:
    """All IP addresses ``host`` resolves to (system DNS)."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def is_public_ip(address: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            ip = mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver: Resolver = system_resolver) -> None:
    """Raise :class:`BlockedAddressError` unless ``url`` is http(s) to a public host.

    :raises OSError: when the host name cannot be resolved.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedAddressError("Only web addresses starting with http:// or https:// work.")
    host = parts.hostname
    if not host:
        raise BlockedAddressError("This web address has no host name.")
    if parts.username or parts.password:
        raise BlockedAddressError("Web addresses with a user name or password are not supported.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = [str(literal)] if literal is not None else resolver(host)
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise BlockedAddressError(PRIVATE_ADDRESS_MESSAGE)
