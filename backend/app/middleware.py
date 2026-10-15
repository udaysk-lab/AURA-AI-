"""HTTP middleware: request IDs, rate limiting, security headers.

Rate limiting matters more here than in a typical CRUD app for two reasons: the
inbound channel endpoints authenticate with a static token rather than a session,
and every request can trigger paid model calls. A leaked token without a limiter
is an open tap on someone's credit card.

The limiter uses Redis when REDIS_URL is set and an in-process dictionary
otherwise. The in-process version is honest about its weakness: with N workers
the effective limit is N times what you configured, which preflight warns about.
"""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.security import decode_access_token

log = logging.getLogger("aura.http")

# Endpoints that must stay reachable even when a caller is being limited,
# otherwise a rate-limited client can't discover why.
EXEMPT_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc"}


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------


class MemoryBuckets:
    """Sliding-window counters held in this process."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        now = time.monotonic()
        window_start = now - window
        hits = self._hits[key]
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window - (now - hits[0])))
            return False, retry_after
        hits.append(now)

        # Opportunistic cleanup so idle keys don't accumulate forever.
        if len(self._hits) > 10_000:
            for stale in [k for k, v in self._hits.items() if not v]:
                self._hits.pop(stale, None)
        return True, 0


class RedisBuckets:
    """Shared counters, so the limit means the same thing across workers."""

    def __init__(self, url: str) -> None:
        import redis  # imported lazily so the dependency stays optional

        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.client.ping()

    def hit(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        redis_key = f"aura:rl:{key}"
        pipe = self.client.pipeline()
        pipe.incr(redis_key, 1)
        pipe.expire(redis_key, int(window), nx=True)
        count, _ = pipe.execute()
        if int(count) > limit:
            return False, max(1, int(self.client.ttl(redis_key) or window))
        return True, 0


def _make_buckets():
    if settings.redis_url:
        try:
            buckets = RedisBuckets(settings.redis_url)
            log.info("Rate limiter: Redis at %s", settings.redis_url)
            return buckets
        except Exception as exc:
            log.warning("Redis unavailable (%s) — falling back to in-memory limits.", exc)
    log.info("Rate limiter: in-memory (per-process)")
    return MemoryBuckets()


_buckets = None


def buckets():
    global _buckets
    if _buckets is None:
        _buckets = _make_buckets()
    return _buckets


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID and log slow or failing requests.

    Without this, a production bug report is "it broke sometimes" with no way to
    tie a user's screenshot to a log line.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.exception(
                "[%s] %s %s failed after %.0fms",
                request_id, request.method, request.url.path, elapsed,
            )
            raise

        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed:.0f}"

        if response.status_code >= 500:
            log.error(
                "[%s] %s %s → %s (%.0fms)",
                request_id, request.method, request.url.path,
                response.status_code, elapsed,
            )
        elif elapsed > 3000:
            log.warning(
                "[%s] slow: %s %s took %.0fms",
                request_id, request.method, request.url.path, elapsed,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # HSTS only outside local dev — sending it over plain HTTP on localhost
        # pins the browser to https and breaks development.
        if settings.environment != "local":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user for authenticated calls, per-IP otherwise, tighter for webhooks."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        # The desktop app serves its own web pages from this process. Loading
        # one fetches a dozen script files without a session, which would
        # exhaust the anonymous allowance before the sign-in screen appeared.
        # Static files cost nothing to serve; the limit is for the API.
        if settings.aura_web_dir and is_web_asset(request):
            return await call_next(request)

        key, limit = self._classify(request)
        allowed, retry_after = buckets().hit(key, limit)
        if not allowed:
            log.warning("Rate limited %s on %s", key, request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Slow down.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        # Trust the leftmost X-Forwarded-For entry only behind a proxy you control.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _classify(self, request: Request) -> tuple[str, int]:
        path = request.url.path

        # Inbound webhooks get the tightest limit — they're the only endpoints
        # reachable without a session. Keyed on the token when it arrives as a
        # query parameter (Telegram-style), otherwise on IP: middleware can't
        # read the JSON body without consuming the stream the handler needs.
        if path.startswith("/api/channels/inbound/"):
            token = request.query_params.get("token", "")
            fingerprint = token[-12:] if token else self._client_ip(request)
            return f"inbound:{fingerprint}", settings.rate_limit_inbound_per_minute

        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            payload = decode_access_token(authorization[7:])
            if payload and payload.get("sub"):
                return f"user:{payload['sub']}", settings.rate_limit_per_minute

        return f"ip:{self._client_ip(request)}", settings.rate_limit_anon_per_minute


# ---------------------------------------------------------------------------
# Local installs: answer to this machine, and to paired devices only
# ---------------------------------------------------------------------------

LOOPBACK_NAMES = {"localhost", "::1", "127.0.0.1", "::ffff:127.0.0.1"}


def is_loopback(host: str | None) -> bool:
    """Is this peer address this computer?"""
    if not host:
        return False
    if host in LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return addr.is_loopback or bool(mapped and mapped.is_loopback)


def host_name(header: str | None) -> str:
    """The name part of a Host header: '127.0.0.1:8765' → '127.0.0.1', '[::1]:8765' → '::1'."""
    value = (header or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def is_web_asset(request: Request) -> bool:
    """A request for the web app's own files rather than the API."""
    if request.method not in ("GET", "HEAD"):
        return False
    first = request.url.path.strip("/").split("/", 1)[0]
    return first not in ("api", "health", "docs", "redoc", "openapi.json")


# What a device that hasn't paired yet may reach: the app's pages (so it can
# show the pairing form), the sign-in config (so the page knows to show it),
# and the pairing endpoint itself. Everything else needs a paired token.
REMOTE_OPEN_PATHS = {"/health", "/api/auth/config", "/api/remote/pair"}


def remote_token_claims(request: Request) -> dict | None:
    """The claims of a paired-device token on this request, if it carries one."""
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    payload = decode_access_token(authorization[7:])
    if payload and payload.get("sub") and payload.get("typ") == "remote":
        return payload
    return None


def _refuse(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


class LocalAccessMiddleware(BaseHTTPMiddleware):
    """Who may talk to AURA when it operates the computer it runs on.

    Does nothing on a hosted deployment. On a local install it enforces two
    rules no route can forget:

    * A request from this computer must also *name* this computer. A web page
      can rebind its own domain to 127.0.0.1 (DNS rebinding) and then reach
      the API as "same origin" — but its requests still say Host: its-domain,
      and those are refused.
    * A request from anywhere else is refused unless remote access is on, and
      then it may only reach the pairing screen until it presents a token from
      pairing. A normal session token is not enough from the network.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.local_mode:
            return await call_next(request)

        peer = request.client.host if request.client else ""
        if is_loopback(peer):
            name = host_name(request.headers.get("host"))
            # No Host at all names nothing, so it doesn't name this computer.
            if name not in settings.local_allowed_host_set:
                log.warning("Refused a local request for host %r (possible DNS rebinding)", name)
                return _refuse(
                    421,
                    "AURA on this computer only answers to its own address "
                    "(127.0.0.1 or localhost).",
                )
            return await call_next(request)

        # From another device.
        if not settings.remote_access_enabled:
            return _refuse(
                403,
                "AURA on this computer only answers to this computer. To use it from "
                "another device, turn on remote access in its Settings.",
            )
        if request.method == "OPTIONS" or is_web_asset(request):
            return await call_next(request)
        if request.url.path in REMOTE_OPEN_PATHS:
            return await call_next(request)
        if remote_token_claims(request) is None:
            return _refuse(
                401,
                "This device isn't paired with AURA yet. Pair it from the sign-in screen.",
            )
        return await call_next(request)
