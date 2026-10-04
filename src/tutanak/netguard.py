"""Keeps meeting content on this machine.

Tutanak talks to exactly one network service while it works: the local LLM server.
``assert_local`` refuses any endpoint that does not resolve to a loopback address,
so a typo or a pasted cloud URL cannot silently ship a transcript off the machine.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class RemoteEndpointBlocked(RuntimeError):
    """The configured endpoint is not on this machine."""


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def is_local_url(url: str) -> bool:
    """True when every address the URL's host resolves to is a loopback address."""
    host = urlsplit(url).hostname
    if not host:
        return False
    if _is_loopback(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(_is_loopback(a) for a in addresses)


def assert_local(url: str, *, allow_remote: bool = False) -> None:
    if allow_remote or is_local_url(url):
        return
    raise RemoteEndpointBlocked(
        f"{url!r} is not on this machine. Tutanak only sends meeting content to a local "
        "model server. Set allow_remote_llm=true if you really want to use this endpoint."
    )


def is_local_host_header(host: str | None) -> bool:
    """Validate an HTTP ``Host`` header (guards the local API against DNS rebinding)."""
    if not host:
        return False
    if host.startswith("["):  # [::1]:8765
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rsplit(":", 1)[0]
    name = name.lower()
    return name == "localhost" or _is_loopback(name)
