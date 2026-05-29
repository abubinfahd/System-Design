from fastapi import APIRouter, Depends, status, Header
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas import schemas
from app.services import url_service
from app.core.rate_limiter import rate_limit_create_url

router = APIRouter()

# --- Legacy Endpoints for Backwards Compatibility ---

@router.post("/shorten", response_model=schemas.URLResponse, status_code=status.HTTP_201_CREATED)
def shorten_url_legacy(url_in: schemas.URLCreate, db: Session = Depends(get_db)):
    """
    Legacy endpoint to create a short URL.
    """
    response_data = url_service.create_short_url(db, url_in)
    return response_data

@router.get("/analytics/{short_code}", response_model=schemas.AnalyticsResponse)
def get_analytics_legacy(short_code: str, db: Session = Depends(get_db)):
    """
    Legacy endpoint to get the click analytics.
    """
    analytics_data = url_service.get_analytics(db, short_code)
    return analytics_data


# --- Milestone 3 Versioned and Standard Endpoints ---

@router.post(
    "/v1/urls",
    response_model=schemas.URLResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_create_url)]
)
def create_url(
    url_in: schemas.URLCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """
    Production-grade endpoint to create a short URL with rate limiting and idempotency key support.
    """
    response_data = url_service.create_short_url(db, url_in, idempotency_key=idempotency_key)
    return response_data

@router.get(
    "/v1/urls/{short_code}/analytics",
    response_model=schemas.AnalyticsResponse
)
def get_analytics_v1(short_code: str, db: Session = Depends(get_db)):
    """
    Retrieve click analytics for a short code.
    """
    analytics_data = url_service.get_analytics(db, short_code)
    return analytics_data


# --- Root Redirect Endpoint (Hot Path) ---

@router.get("/{short_code}", response_class=RedirectResponse)
def redirect_to_url(short_code: str, db: Session = Depends(get_db)):
    """
    Hot path redirect endpoint (no rate limiting).
    """
    long_url = url_service.get_original_url(db, short_code)
    return RedirectResponse(url=long_url, status_code=status.HTTP_302_FOUND)
