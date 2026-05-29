from pydantic import BaseModel, field_validator
from datetime import datetime
from urllib.parse import urlparse
import re

class URLCreate(BaseModel):
    long_url: str
    custom_alias: str | None = None
    expires_at: datetime | None = None

    @field_validator("long_url")
    @classmethod
    def validate_long_url(cls, v: str) -> str:
        if len(v) > 2048:
            raise ValueError("URL must not exceed 2048 characters")
        try:
            parsed = urlparse(v)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("URL must have a valid scheme (http or https)")
            if not parsed.netloc:
                raise ValueError("URL must have a valid domain")
        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            raise ValueError("Invalid URL format")
        return v

    @field_validator("custom_alias")
    @classmethod
    def validate_custom_alias(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Custom alias must match [a-zA-Z0-9_-] only")
        return v

class URLResponse(BaseModel):
    short_code: str
    short_url: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class AnalyticsResponse(BaseModel):
    short_code: str
    total_clicks: int
    created_at: datetime

    class Config:
        from_attributes = True
