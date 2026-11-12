"""Fetching a URL without ever reaching this computer or its network.

Spec 10-permissions.md section 8.3. `fetch_url` used to be `httpx.get(url,
follow_redirects=True)`, which would happily read AURA's own API on 127.0.0.1,
a router's admin page, or a cloud metadata address, and follow a redirect from a
public page to any of them. `safe_get` closes that:

* the host name and every address it resolves to are checked before connecting;
* the connection goes to the address that was checked (pinned), so a name that
  answers "public" to the check and "private" to the connection (DNS rebinding)
  gets nothing;
* redirects are followed by hand, and every hop is checked again;
* the system proxy is not used, because a proxy would resolve the name itself.

`check_url` runs the same host checks without connecting; the gate uses it so a
fetch that can never succeed is refused instead of asked about.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.config import settings

MAX_HOPS = 6  # the first request and five redirects
MAX_CHARS = 20_000
_MAX_BYTES = 400_000  # read at most this much of a body before decoding
_TIMEOUT = 20
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERIC_HOST = re.compile(r"[0-9a-fx.]+")


class FetchDenied(Exception):
    """The URL points somewhere fetch_url must never go. `code` is the floor code."""

    def __init__(self, reason: str, code: str = "FLOOR_PRIVATE_NETWORK"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


# --- seams for tests (no network in tests) ----------------------------------


def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to. Replaced in tests."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _make_client(transport=None):
    import httpx

    return httpx.Client(timeout=_TIMEOUT, trust_env=False, follow_redirects=False, transport=transport)


# --- address checks ----------------------------------------------------------


def _unwrap(ip):
    """The IPv4 address hidden inside a mapped / 6to4 / NAT64 / Teredo IPv6 address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip.ipv4_mapped
        if ip.sixtofour:
            return ip.sixtofour
        if ip.teredo:
            return ip.teredo[1]
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def is_public(address: str) -> bool:
    """True only for an ordinary, globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address.strip("[]").split("%")[0])
    except ValueError:
        return False
    inner = _unwrap(ip)
    for candidate in {ip, inner}:
        if (
            not candidate.is_global
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_unspecified
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_private
        ):
            return False
        if isinstance(candidate, ipaddress.IPv4Address) and candidate in _CGNAT:
            return False
    return True


def _host_of(parts) -> str:
    host = parts.hostname
    if not host:
        raise FetchDenied("The URL has no host.", "FLOOR_PRIVATE_NETWORK")
    host = host.strip("[]").rstrip(".").lower()
    try:
        ipaddress.ip_address(host.split("%")[0])
        return host
    except ValueError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise FetchDenied(f"The host name is not valid: {exc}") from exc


def _refuse_names(host: str) -> None:
    if (
        host == "localhost"
        or host.endswith(_LOCAL_SUFFIXES)
        or host in settings.local_allowed_host_set
        or host in {h.lower() for h in settings.remote_hostname_list}
    ):
        raise FetchDenied(
            "That address is on this computer or its network. fetch_url only reads public web pages."
        )


@dataclass(frozen=True)
class Checked:
    url: str
    scheme: str
    host: str  # as it should appear in the Host header (without port)
    port: int
    ip: str  # the address that passed the checks and will be connected to
    host_header: str


def check_url(url: str) -> Checked:
    """Vet one URL: scheme, host name, and every address the name resolves to."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise FetchDenied(f"The URL could not be read: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise FetchDenied("Only http and https URLs are supported.", "FLOOR_BAD_URL")
    host = _host_of(parts)
    port = port or (443 if scheme == "https" else 80)
    literal = None
    try:
        literal = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        _refuse_names(host)
    if literal is not None:
        ips = [str(literal)]
    else:
        try:
            ips = resolve(host, port)
        except OSError as exc:
            if _NUMERIC_HOST.fullmatch(host):
                # 127.1, 0x7f.1, 017700000001: an address spelled oddly. Never let one through.
                raise FetchDenied("That address is on this computer or its network.") from exc
            raise FetchDenied(f"Couldn't look up {host}: {exc}", "FLOOR_UNRESOLVED") from exc
    if not ips:
        raise FetchDenied(f"Couldn't look up {host}.", "FLOOR_UNRESOLVED")
    for address in ips:
        if not is_public(address):
            raise FetchDenied(
                "That address is on this computer or its network. fetch_url only reads public web pages."
            )
    ip = sorted(ips)[0]
    default = 443 if scheme == "https" else 80
    shown = f"[{host}]" if ":" in host else host
    return Checked(
        url=url, scheme=scheme, host=host, port=port, ip=ip,
        host_header=shown if port == default else f"{shown}:{port}",
    )


def _pinned_url(checked: Checked, original: str) -> str:
    parts = urlsplit(original)
    ip = f"[{checked.ip}]" if ":" in checked.ip else checked.ip
    default = 443 if checked.scheme == "https" else 80
    netloc = ip if checked.port == default else f"{ip}:{checked.port}"
    return urlunsplit((checked.scheme, netloc, parts.path or "/", parts.query, ""))


def safe_get(url: str, *, transport=None) -> dict:
    """GET one URL under the fetch floors. Raises FetchDenied; other errors are httpx's.

    Returns {"url", "status", "content"} like the old tool did.
    """
    import httpx

    current = url
    with _make_client(transport) as client:
        for _hop in range(MAX_HOPS):
            checked = check_url(current)
            request = httpx.Request(
                "GET",
                _pinned_url(checked, current),
                headers={"Host": checked.host_header, "User-Agent": "AURA/2 fetch_url", "Accept": "*/*"},
                extensions={"sni_hostname": checked.host},
            )
            response = client.send(request, stream=True)
            try:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_BYTES:
                        break
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                if len(body) > MAX_CHARS:
                    body = body[:MAX_CHARS] + "\n…[truncated]…"
                return {"url": current, "status": response.status_code, "content": body}
            finally:
                response.close()
    raise FetchDenied("Too many redirects.", "FLOOR_REDIRECTS")
