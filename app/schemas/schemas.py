from pydantic import BaseModel, HttpUrl
from datetime import datetime

class URLCreate(BaseModel):
    long_url: HttpUrl

class URLResponse(BaseModel):
    short_code: str
    long_url: str
    
    class Config:
        from_attributes = True

class AnalyticsResponse(BaseModel):
    short_code: str
    total_clicks: int
