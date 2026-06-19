"""
Rate limiting middleware.

Milestone 6: Uses Redis-based sliding window rate limiter that works
across multiple Gunicorn workers and horizontally scaled instances.

Falls back to in-memory rate limiting if Redis is unavailable.
"""

import time
import threading
import logging
from fastapi import Request
from app.core.errors import APIException
from app.core.cache import check_rate_limit

logger = logging.getLogger("url_shortener.rate_limiter")


# ── Fallback In-Memory Rate Limiter ────────────────────────
# Used when Redis is unavailable (per-worker only)

class InMemoryRateLimiter:
    def __init__(self):
        self.requests: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def is_rate_limited(self, ip: str, limit: int, window: int) -> bool:
        now = time.time()
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []

            # Filter requests in the current window
            window_start = now - window
            self.requests[ip] = [t for t in self.requests[ip] if t > window_start]

            if len(self.requests[ip]) >= limit:
                return True

            self.requests[ip].append(now)
            return False


_fallback_limiter = InMemoryRateLimiter()


def rate_limit_create_url(request: Request):
    """
    Rate limit dependency for POST /v1/urls.
    Policy: 10 requests/min per IP.

    Uses Redis for distributed rate limiting across workers.
    Falls back to in-memory if Redis is unavailable.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"

    # Try Redis-based rate limiting first (works across workers)
    is_limited = check_rate_limit(
        key=f"create_url:{client_ip}",
        limit=10,
        window=60
    )

    if is_limited:
        logger.info("Rate limit exceeded (Redis) for IP: %s", client_ip)
        raise APIException(
            status_code=429,
            code="TOO_MANY_REQUESTS",
            message="Rate limit exceeded. Limit is 10 requests per minute."
        )

    # If Redis returned False (not limited OR Redis is down), also check fallback
    # This provides per-worker protection even when Redis is unavailable
    if _fallback_limiter.is_rate_limited(client_ip, limit=10, window=60):
        logger.info("Rate limit exceeded (in-memory fallback) for IP: %s", client_ip)
        raise APIException(
            status_code=429,
            code="TOO_MANY_REQUESTS",
            message="Rate limit exceeded. Limit is 10 requests per minute."
        )
