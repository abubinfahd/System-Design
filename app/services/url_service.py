import string
import json
import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db import models
from app.schemas import schemas
from app.core.errors import APIException
from app.core.config import settings

BASE62 = string.digits + string.ascii_letters

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

def create_short_url(db: Session, url_in: schemas.URLCreate, idempotency_key: str | None = None) -> dict:
    # 1. Check idempotency key if provided
    if idempotency_key:
        existing_key = db.query(models.IdempotencyKey).filter(models.IdempotencyKey.key == idempotency_key).first()
        if existing_key:
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

    # 3. DB Transaction to perform insert, flush, generate base62 code, update, and commit
    try:
        db_url = models.URL(
            long_url=str(url_in.long_url),
            custom_alias=url_in.custom_alias,
            expires_at=url_in.expires_at
        )
        db.add(db_url)
        db.flush()  # Obtain the autoincrement ID

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
        return response_data

    except Exception as e:
        db.rollback()
        if isinstance(e, APIException):
            raise e
        raise APIException(status_code=500, code="INTERNAL_SERVER_ERROR", message=str(e))

def get_original_url(db: Session, short_code: str) -> str:
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise APIException(status_code=404, code="NOT_FOUND", message="URL not found")

    if is_expired(db_url.expires_at):
        raise APIException(status_code=410, code="EXPIRED", message="The short URL has expired")

    # Track the click
    click = models.Click(url_id=db_url.id)
    db.add(click)
    db.commit()

    return db_url.long_url

def get_analytics(db: Session, short_code: str) -> dict:
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise APIException(status_code=404, code="NOT_FOUND", message="URL not found")

    click_count = db.query(models.Click).filter(models.Click.url_id == db_url.id).count()

    return {
        "short_code": short_code,
        "total_clicks": click_count,
        "created_at": db_url.created_at
    }
