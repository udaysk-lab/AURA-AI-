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
import os
import platform
import re
import socket
import struct
import sys
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


# --- "is this host this computer?" (shared by the fetch floors and the shell's self-API floor, F12) --------


def parse_address(host: str):
    """An IP address from any spelling a client library accepts, or None.

    Dotted and IPv6 forms (including IPv4-mapped), and the legacy IPv4 forms `inet_aton` reads:
    127.1, 0x7f000001, 017700000001, 2130706433."""
    text = (host or "").strip().strip("[]").split("%")[0]
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    if _NUMERIC_HOST.fullmatch(text.lower()):
        try:
            return ipaddress.IPv4Address(struct.unpack("!I", socket.inet_aton(text))[0])
        except (OSError, ValueError, struct.error):
            return None
    return None


def is_this_computer(host: str) -> bool:
    """Does this host name or address mean this machine: localhost (any case, with or without the
    trailing dot), a loopback or unspecified address in any spelling (including IPv4-mapped IPv6), one of the
    names AURA answers to, or the computer's own host name?"""
    h = (host or "").strip().strip("[]").split("%")[0].rstrip(".").lower()
    if not h:
        return False
    if h == "localhost" or h.endswith(".localhost") or h in ("ip6-localhost", "ip6-loopback"):
        return True
    if h in settings.local_allowed_host_set or h in {x.lower() for x in settings.remote_hostname_list}:
        return True
    node = (platform.node() or "").lower().rstrip(".")
    if len(node) >= 3 and h == node:
        return True
    ip = parse_address(h)
    if ip is None:
        return False
    return any(c.is_loopback or c.is_unspecified for c in {ip, _unwrap(ip)})


# --- the port AURA is really on ------------------------------------------------------------------------------

_BOUND_PORTS: set[int] = set()


def note_bound_port(server) -> None:
    """Remember the port a request arrived on (the ASGI `server` pair): that is where AURA is."""
    try:
        port = int(server[1]) if server and server[1] else 0
    except (TypeError, ValueError, IndexError):
        return
    if 0 < port < 65536:
        _BOUND_PORTS.add(port)


def _argv_ports() -> set[int]:
    ports: set[int] = set()
    args = sys.argv[1:]
    for i, a in enumerate(args):
        value = ""
        if a in ("--port", "-p") and i + 1 < len(args):
            value = args[i + 1]
        elif a.startswith("--port="):
            value = a.split("=", 1)[1]
        if value.isdigit() and 0 < int(value) < 65536:
            ports.add(int(value))
    return ports


def bound_ports() -> set[int]:
    """Ports AURA is on: those requests arrived on, AURA_PORT, a `--port` on the command line. With
    nothing known yet, the two defaults (8765 for the app, 8000 for a source install)."""
    ports = set(_BOUND_PORTS) | _argv_ports()
    env = (os.environ.get("AURA_PORT") or "").strip()
    if env.isdigit() and 0 < int(env) < 65536:
        ports.add(int(env))
    return ports or {8765, 8000}


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


def _host_key(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _pinned_url(checked: Checked, original: str) -> str:
    parts = urlsplit(original)
    ip = f"[{checked.ip}]" if ":" in checked.ip else checked.ip
    default = 443 if checked.scheme == "https" else 80
    netloc = ip if checked.port == default else f"{ip}:{checked.port}"
    return urlunsplit((checked.scheme, netloc, parts.path or "/", parts.query, ""))


def safe_get(url: str, *, transport=None, on_redirect_host=None) -> dict:
    """GET one URL under the fetch floors. Raises FetchDenied; other errors are httpx's.

    `on_redirect_host(url, host)` is asked before following a redirect to a different host (F13);
    it returns None to go on or a sentence to refuse with. Returns {"url", "status", "content"}.
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
                    target = urljoin(current, response.headers["location"])
                    if on_redirect_host is not None:
                        old_host, new_host = _host_key(current), _host_key(target)
                        if new_host != old_host:
                            refusal = on_redirect_host(target, new_host)
                            if refusal:
                                raise FetchDenied(refusal, "FLOOR_REDIRECT_HOST")
                    current = target
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
