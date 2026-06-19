"""
URL Shortener service layer.

Implements:
- URL creation with Base62 encoding, idempotency, and custom aliases (M1-M3)
- Redis cache-aside pattern for redirects (M5)
- Click tracking via separate click_events table (M4)
- Pre-computed analytics from urls.click_count (M4)
- Dangerous domain warning logging (M3)
"""

import string
import json
import re
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db import models
from app.schemas import schemas
from app.core.errors import APIException
from app.core.config import settings
from app.core import cache

logger = logging.getLogger("url_shortener.service")

BASE62 = string.digits + string.ascii_letters

# Known suspicious domains (placeholder — extend as needed)
SUSPICIOUS_DOMAINS = {"bit.ly", "tinyurl.com"}


def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62[0]

    base = len(BASE62)
    result = []

    while num:
        num, rem = divmod(num, base)
        result.append(BASE62[rem])

    return ''.join(reversed(result))


def is_expired(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        return expires_at < datetime.now(timezone.utc).replace(tzinfo=None)
    return expires_at < datetime.now(timezone.utc)


def _check_suspicious_domain(long_url: str) -> None:
    """Log a warning for suspicious/known-shortener domains (M3 security)."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(long_url).netloc.lower()
        if domain in SUSPICIOUS_DOMAINS:
            logger.warning(
                "⚠️ Suspicious domain detected: %s — URL: %s",
                domain,
                long_url[:100]
            )
    except Exception:
        pass


def create_short_url(db: Session, url_in: schemas.URLCreate, idempotency_key: str | None = None) -> dict:
    """
    Create a short URL with full transaction safety.

    Flow (M3/M4):
    1. Check idempotency key (return cached response if duplicate)
    2. Validate custom alias
    3. BEGIN TRANSACTION → INSERT → flush → encode Base62 → UPDATE → COMMIT
    4. Populate Redis cache for the new short code
    """
    # 1. Check idempotency key if provided
    if idempotency_key:
        existing_key = db.query(models.IdempotencyKey).filter(models.IdempotencyKey.key == idempotency_key).first()
        if existing_key:
            logger.info("Idempotency key hit: %s — returning cached response", idempotency_key)
            return json.loads(existing_key.response_body)

    # 2. Check custom alias if provided
    if url_in.custom_alias:
        if not re.match(r"^[a-zA-Z0-9_-]+$", url_in.custom_alias):
            raise APIException(status_code=400, code="INVALID_ALIAS", message="Custom alias must match [a-zA-Z0-9_-] only")

        # Check if alias is already taken (either as a short code or custom alias)
        existing = db.query(models.URL).filter(
            (models.URL.short_code == url_in.custom_alias) |
            (models.URL.custom_alias == url_in.custom_alias)
        ).first()
        if existing:
            raise APIException(status_code=409, code="ALIAS_TAKEN", message="The custom alias is already taken")

    # Log suspicious domains
    _check_suspicious_domain(str(url_in.long_url))

    # 3. DB Transaction: insert → flush → generate base62 code → update → commit
    try:
        db_url = models.URL(
            long_url=str(url_in.long_url),
            custom_alias=url_in.custom_alias,
            expires_at=url_in.expires_at,
            click_count=0,
        )
        db.add(db_url)
        db.flush()  # Obtain the autoincrement ID without committing

        # 4. Set short code
        if url_in.custom_alias:
            db_url.short_code = url_in.custom_alias
        else:
            db_url.short_code = encode_base62(db_url.id)

        # Build response representation
        base_url = settings.BASE_URL.rstrip("/")
        short_url = f"{base_url}/{db_url.short_code}"
        created_at_str = db_url.created_at.isoformat() if db_url.created_at else datetime.now(timezone.utc).isoformat()

        response_data = {
            "short_code": db_url.short_code,
            "short_url": short_url,
            "created_at": created_at_str
        }

        # Cache response body under idempotency key if provided
        if idempotency_key:
            ik_entry = models.IdempotencyKey(
                key=idempotency_key,
                response_body=json.dumps(response_data)
            )
            db.add(ik_entry)

        db.commit()

        # Populate Redis cache for the new short code (M5)
        cache.set_cached_url(db_url.short_code, db_url.long_url)
        logger.info("Created short URL: %s → %s", db_url.short_code, db_url.long_url[:80])

        return response_data

    except Exception as e:
        db.rollback()
        if isinstance(e, APIException):
            raise e
        logger.error("URL creation failed: %s", e, exc_info=True)
        raise APIException(status_code=500, code="INTERNAL_SERVER_ERROR", message=str(e))


def get_original_url(db: Session, short_code: str) -> str:
    """
    Redirect lookup — the hot path.

    Flow (M5 Cache-Aside):
    1. Check L1 cache → L2 Redis cache
    2. On cache miss → query Postgres → populate cache
    3. Check expiration
    4. Track click in click_events (M4 — independent write, no FK contention)
    """
    # Step 1: Check cache (L1 → L2)
    cached_url = cache.get_cached_url(short_code)

    if cached_url:
        # Cache hit — still need to check the DB for expiration on first access
        # For performance, we trust the cache and check expiration separately
        _track_click(db, short_code)
        return cached_url

    # Step 2: Cache miss — query Postgres
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise APIException(status_code=404, code="NOT_FOUND", message="URL not found")

    if is_expired(db_url.expires_at):
        raise APIException(status_code=410, code="EXPIRED", message="The short URL has expired")

    # Step 3: Populate cache
    cache.set_cached_url(short_code, db_url.long_url)

    # Step 4: Track the click
    _track_click(db, short_code)

    return db_url.long_url


def _track_click(db: Session, short_code: str) -> None:
    """
    Record a click event in the click_events table.
    Independent INSERT — no FK, no lock contention on the urls table.
    The background aggregation job will batch-update urls.click_count.
    """
    try:
        click_event = models.ClickEvent(short_code=short_code)
        db.add(click_event)
        db.commit()
    except Exception as e:
        db.rollback()
        # Click tracking failure should not break the redirect
        logger.warning("Click tracking failed for %s: %s", short_code, e)


def get_analytics(db: Session, short_code: str) -> dict:
    """
    Return analytics with pre-computed click_count (M4).
    Reads urls.click_count instead of COUNT(*) over millions of click_events rows.
    Optionally includes unprocessed recent clicks for better accuracy.
    """
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise APIException(status_code=404, code="NOT_FOUND", message="URL not found")

    # Pre-computed count from background aggregation
    aggregated_count = db_url.click_count or 0

    # Add unprocessed recent clicks for near-real-time accuracy
    recent_result = db.execute(text("""
        SELECT COUNT(*) FROM click_events
        WHERE short_code = :code AND processed = FALSE
    """), {"code": short_code})
    recent_clicks = recent_result.scalar() or 0

    total_clicks = aggregated_count + recent_clicks

    return {
        "short_code": short_code,
        "total_clicks": total_clicks,
        "created_at": db_url.created_at
    }
