import hashlib
import logging
import time
from typing import Dict, List, Optional, Tuple
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from app.config.settings import settings
from app.utils.response import api_error

logger = logging.getLogger("rate_limit")

WINDOW_SECONDS = 60.0
# Paths that must stay reachable for platform health checks and API docs.
EXEMPT_PATHS = {"/health", "/", "/docs", "/redoc", "/openapi.json"}
# Prune stale keys once the table grows past this, so the map cannot grow
# without bound from one-shot IPs.
_PRUNE_THRESHOLD = 10_000

_client_requests: Dict[str, List[float]] = {}


def _client_ip(conn: HTTPConnection) -> str:
    """
    Resolve the caller's IP. Behind a reverse proxy, conn.client.host is the
    proxy itself, which would make every user share a single bucket, so the
    forwarded header is used when the deployment opts in.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = conn.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = conn.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return conn.client.host if conn.client else "unknown"


def _bucket_key(conn: HTTPConnection) -> str:
    """
    Prefer the bearer token so a single credential cannot fan out across many
    source addresses; fall back to IP for anonymous traffic. The token is
    hashed so raw credentials never reach the log or the in-memory table.
    """
    auth = conn.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return "ip:" + _client_ip(conn)


def _prune(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    for key in [k for k, v in _client_requests.items() if not v or v[-1] < cutoff]:
        _client_requests.pop(key, None)


def check_rate_limit(conn: HTTPConnection) -> Tuple[bool, int, float]:
    """
    Sliding-window check. Returns (allowed, remaining, retry_after_seconds)
    and records the request when allowed.
    """
    limit = settings.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return True, 0, 0.0

    now = time.time()
    if len(_client_requests) > _PRUNE_THRESHOLD:
        _prune(now)

    key = _bucket_key(conn)
    cutoff = now - WINDOW_SECONDS
    hits = [t for t in _client_requests.get(key, []) if t > cutoff]

    if len(hits) >= limit:
        _client_requests[key] = hits
        retry_after = max(1.0, WINDOW_SECONDS - (now - hits[0]))
        return False, 0, retry_after

    hits.append(now)
    _client_requests[key] = hits
    return True, limit - len(hits), 0.0


class RateLimitMiddleware:
    """
    Enforces RATE_LIMIT_PER_MINUTE requests per minute per credential (or per IP
    when unauthenticated) across every HTTP route.

    Written as pure ASGI rather than a route dependency for two reasons: a
    dependency attached at app level is also resolved for WebSocket routes, where
    there is no Request to inject and no HTTP response to return; and middleware
    runs before routing, so unmatched paths are covered too.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        conn = HTTPConnection(scope)
        if conn.url.path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        allowed, _remaining, retry_after = check_rate_limit(conn)
        if allowed:
            await self.app(scope, receive, send)
            return

        logger.warning(
            f"Rate limit exceeded for {_bucket_key(conn)[:12]}... on {conn.url.path}"
        )
        response = api_error(
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests. Please slow down.",
            status_code=429,
            details={"retry_after_seconds": int(retry_after)},
            headers={"Retry-After": str(int(retry_after))},
        )
        await response(scope, receive, send)


def reset_rate_limits() -> None:
    """Clear all buckets. Used by tests."""
    _client_requests.clear()
