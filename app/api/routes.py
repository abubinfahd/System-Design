from fastapi import APIRouter, Depends, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas import schemas
from app.services import url_service

router = APIRouter()

@router.post("/shorten", response_model=schemas.URLResponse, status_code=status.HTTP_201_CREATED)
def shorten_url(url_in: schemas.URLCreate, db: Session = Depends(get_db)):
    """
    Create a short URL from a long URL.
    """
    db_url = url_service.create_short_url(db, url_in)
    return db_url

@router.get("/{short_code}", response_class=RedirectResponse)
def redirect_to_url(short_code: str, db: Session = Depends(get_db)):
    """
    Redirect to the original URL and track the click.
    """
    long_url = url_service.get_original_url(db, short_code)
    return RedirectResponse(url=long_url, status_code=status.HTTP_302_FOUND)

@router.get("/analytics/{short_code}", response_model=schemas.AnalyticsResponse)
def get_analytics(short_code: str, db: Session = Depends(get_db)):
    """
    Get the total number of clicks for a short code.
    """
    analytics_data = url_service.get_analytics(db, short_code)
    return analytics_data
