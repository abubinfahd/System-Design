"""
Health check endpoint for load balancer integration.

Returns the health status of all dependencies (DB + Redis).
Load balancers use this to determine if a server instance should receive traffic.
Returns 200 if healthy, 503 if any critical dependency is down.
"""

import logging
from fastapi import APIRouter
from sqlalchemy import text
from app.db.database import SessionLocal
from app.core.cache import is_redis_available

logger = logging.getLogger("url_shortener.health")

health_router = APIRouter(tags=["Health"])


@health_router.get("/health")
def health_check():
    """
    Health check endpoint for load balancer and monitoring.

    Checks:
    - Database connectivity (critical — 503 if down)
    - Redis connectivity (non-critical — degraded if down)
    """
    db_healthy = False
    redis_healthy = False

    # Check database
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error("Health check: DB is DOWN — %s", e)

    # Check Redis
    redis_healthy = is_redis_available()
    if not redis_healthy:
        logger.warning("Health check: Redis is DOWN — running in degraded mode")

    # DB is critical, Redis is optional (graceful degradation)
    if db_healthy:
        status_code = 200
        status = "healthy" if redis_healthy else "degraded"
    else:
        status_code = 503
        status = "unhealthy"

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "db": "ok" if db_healthy else "down",
            "redis": "ok" if redis_healthy else "down"
        }
    )
