"""
Redis caching layer implementing:
- Cache-aside pattern (lazy loading)
- TTL strategy (24h for stable URL data)
- Cache stampede protection (distributed lock)
- Graceful degradation (fallback to Postgres when Redis is down)
- L1 in-process cache (60s TTL for hottest codes)
- Cache invalidation (delete on update)
"""

import time
import logging
import redis
from app.core.config import settings

logger = logging.getLogger("url_shortener.cache")

# ── Redis Client ────────────────────────────────────────────
_redis_client: redis.Redis | None = None

CACHE_TTL = 86400       # 24 hours — URL data is stable
LOCK_TTL = 5            # 5 second distributed lock for stampede protection
L1_TTL = 60             # 60 second in-process cache
L1_MAX_SIZE = 1000      # Max entries in L1 cache


def init_redis() -> None:
    """Initialize Redis connection. Called on app startup."""
    global _redis_client
    try:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        _redis_client.ping()
        logger.info("✅ Redis connected successfully at %s", settings.REDIS_URL)
    except Exception as e:
        logger.warning("⚠️ Redis connection failed: %s — running without cache", e)
        _redis_client = None


def close_redis() -> None:
    """Close Redis connection. Called on app shutdown."""
    global _redis_client
    if _redis_client:
        try:
            _redis_client.close()
            logger.info("Redis connection closed")
        except Exception:
            pass
        _redis_client = None


# ── L1 In-Process Cache ────────────────────────────────────
_l1_cache: dict[str, dict] = {}


def _l1_get(short_code: str) -> str | None:
    """Check L1 in-process cache. Returns URL or None."""
    entry = _l1_cache.get(short_code)
    if entry and time.time() < entry["expires"]:
        logger.debug("L1 cache HIT for %s", short_code)
        return entry["url"]
    if entry:
        # Expired — remove it
        _l1_cache.pop(short_code, None)
    return None


def _l1_set(short_code: str, url: str) -> None:
    """Store in L1 cache with TTL. Evicts oldest if full."""
    if len(_l1_cache) >= L1_MAX_SIZE:
        # Simple eviction: remove the oldest entry
        oldest_key = next(iter(_l1_cache))
        _l1_cache.pop(oldest_key, None)
    _l1_cache[short_code] = {"url": url, "expires": time.time() + L1_TTL}


def _l1_delete(short_code: str) -> None:
    """Remove from L1 cache."""
    _l1_cache.pop(short_code, None)


# ── Cache-Aside with Stampede Protection ───────────────────

def get_cached_url(short_code: str) -> str | None:
    """
    Multi-layer cache lookup:
      L1 (in-process dict) → L2 (Redis) → return None (caller hits DB)

    Implements cache stampede protection with distributed lock.
    """
    # Layer 1: In-process cache (no network hop, ~0.01ms)
    l1_result = _l1_get(short_code)
    if l1_result:
        return l1_result

    # Layer 2: Redis (network hop, ~1ms)
    if not _redis_client:
        logger.debug("Redis unavailable — skipping L2 cache for %s", short_code)
        return None

    try:
        cached = _redis_client.get(f"url:{short_code}")
        if cached:
            logger.debug("Redis cache HIT for %s", short_code)
            _l1_set(short_code, cached)
            return cached

        logger.debug("Redis cache MISS for %s", short_code)

        # Stampede protection: try to acquire lock
        lock_key = f"lock:{short_code}"
        lock_acquired = _redis_client.set(lock_key, "1", nx=True, ex=LOCK_TTL)

        if not lock_acquired:
            # Another request is fetching from DB — wait briefly and retry Redis
            logger.debug("Stampede lock held for %s — waiting 50ms", short_code)
            time.sleep(0.05)
            cached = _redis_client.get(f"url:{short_code}")
            if cached:
                _l1_set(short_code, cached)
                return cached

        # Return None — caller will fetch from DB and call set_cached_url()
        return None

    except Exception as e:
        logger.warning("Redis error during GET for %s: %s", short_code, e)
        return None


def set_cached_url(short_code: str, long_url: str) -> None:
    """Store URL in Redis (L2) and L1 cache. Used after DB fetch."""
    # Always set L1
    _l1_set(short_code, long_url)

    if not _redis_client:
        return

    try:
        _redis_client.set(f"url:{short_code}", long_url, ex=CACHE_TTL)

        # Release stampede lock if we hold it
        _redis_client.delete(f"lock:{short_code}")

        logger.debug("Cached %s in Redis (TTL=%ds)", short_code, CACHE_TTL)
    except Exception as e:
        logger.warning("Redis error during SET for %s: %s", short_code, e)


def delete_cached_url(short_code: str) -> None:
    """
    Cache invalidation: delete from both L1 and L2.
    Called when a URL is updated or deleted.
    Always update DB first, then invalidate cache.
    """
    _l1_delete(short_code)

    if not _redis_client:
        return

    try:
        _redis_client.delete(f"url:{short_code}")
        logger.info("Cache invalidated for %s", short_code)
    except Exception as e:
        logger.warning("⚠️ Cache invalidation FAILED for %s: %s — stale data possible", short_code, e)


# ── Redis-based Rate Limiting ──────────────────────────────

def check_rate_limit(key: str, limit: int, window: int) -> bool:
    """
    Redis-based sliding window rate limiter.
    Returns True if rate limit is exceeded.
    Works across multiple workers/instances.
    Falls back to allowing the request if Redis is unavailable.
    """
    if not _redis_client:
        return False  # If Redis is down, don't block requests

    try:
        pipe = _redis_client.pipeline()
        now = time.time()
        window_start = now - window

        redis_key = f"ratelimit:{key}"

        # Remove old entries outside the window
        pipe.zremrangebyscore(redis_key, 0, window_start)
        # Add current request timestamp
        pipe.zadd(redis_key, {str(now): now})
        # Count requests in window
        pipe.zcard(redis_key)
        # Set expiry on the key
        pipe.expire(redis_key, window)

        results = pipe.execute()
        request_count = results[2]

        if request_count > limit:
            logger.info("Rate limit exceeded for %s: %d/%d in %ds", key, request_count, limit, window)
            return True

        return False

    except Exception as e:
        logger.warning("Redis rate limit check failed: %s — allowing request", e)
        return False


def is_redis_available() -> bool:
    """Health check for Redis connectivity."""
    if not _redis_client:
        return False
    try:
        _redis_client.ping()
        return True
    except Exception:
        return False
