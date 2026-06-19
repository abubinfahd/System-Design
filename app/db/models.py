from sqlalchemy import Column, BigInteger, String, DateTime, Boolean, Index
from datetime import datetime, timezone
from app.db.database import Base


class URL(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    short_code = Column(String(10), unique=True, index=True, nullable=True)
    long_url = Column(String, nullable=False)
    custom_alias = Column(String, unique=True, index=True, nullable=True)
    click_count = Column(BigInteger, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ClickEvent(Base):
    """
    Separate write-heavy table for click tracking.
    No FK to urls — independent writes for zero lock contention.
    """
    __tablename__ = "click_events"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    short_code = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_click_events_short_code", "short_code"),
        Index("idx_click_events_created_at", "created_at"),
        Index("idx_click_events_processed", "processed"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key = Column(String, primary_key=True, index=True)
    response_body = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
