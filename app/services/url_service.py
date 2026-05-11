import string
import random
from sqlalchemy.orm import Session
from app.db import models
from app.schemas import schemas
from fastapi import HTTPException

# A simple random string generator for M1
def generate_short_code(length: int = 6) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def create_short_url(db: Session, url_in: schemas.URLCreate) -> models.URL:
    # 1. Generate a unique short code
    # (In a real system we'd handle collisions better, but for M1 this simple loop is fine)
    while True:
        short_code = generate_short_code()
        existing = db.query(models.URL).filter(models.URL.short_code == short_code).first()
        if not existing:
            break

    # 2. Save to DB
    db_url = models.URL(
        short_code=short_code,
        long_url=str(url_in.long_url)
    )
    db.add(db_url)
    db.commit()
    db.refresh(db_url)
    return db_url

def get_original_url(db: Session, short_code: str) -> str:
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    # Track the click
    # Milestone 1 says "Eventual consistency for analytics". 
    # For now, we do it synchronously to keep it simple, but in later milestones, 
    # this should be moved to a background task or Kafka.
    click = models.Click(url_id=db_url.id)
    db.add(click)
    db.commit()

    return db_url.long_url

def get_analytics(db: Session, short_code: str) -> dict:
    db_url = db.query(models.URL).filter(models.URL.short_code == short_code).first()
    if not db_url:
        raise HTTPException(status_code=404, detail="URL not found")
    
    # We can query the related clicks
    click_count = db.query(models.Click).filter(models.Click.url_id == db_url.id).count()
    
    return {
        "short_code": short_code,
        "total_clicks": click_count
    }
