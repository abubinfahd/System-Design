import time
import threading
from fastapi import Request
from app.core.errors import APIException

class InMemoryRateLimiter:
    def __init__(self):
        self.requests = {}
        self.lock = threading.Lock()

    def is_rate_limited(self, ip: str, limit: int, window: int) -> bool:
        now = time.time()
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            
            # Filter requests in the current window (e.g. last 60 seconds)
            window_start = now - window
            self.requests[ip] = [t for t in self.requests[ip] if t > window_start]
            
            if len(self.requests[ip]) >= limit:
                return True
            
            self.requests[ip].append(now)
            return False

limiter = InMemoryRateLimiter()

def rate_limit_create_url(request: Request):
    # Retrieve client IP. Fallback to localhost if client info is missing.
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    # Policy: 10 requests/min per IP
    if limiter.is_rate_limited(client_ip, limit=10, window=60):
        raise APIException(
            status_code=429,
            code="TOO_MANY_REQUESTS",
            message="Rate limit exceeded. Limit is 10 requests per minute."
        )
