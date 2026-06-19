"""
Mini URL Shortener — Application entrypoint.

Initializes:
- Database tables
- Redis connection (M5)
- Background click aggregation job (M4)
- Exception handlers (M3)
- API routes + health check (M6)
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import engine, Base
from app.api import routes
from app.api.health import health_router
from app.core.errors import register_exception_handlers
from app.core.cache import init_redis, close_redis
from app.services.aggregation import run_aggregation_loop

# ── Logging Configuration ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("url_shortener")

# ── Database Initialization ────────────────────────────────
Base.metadata.create_all(bind=engine)


# ── Application Lifespan ───────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages startup and shutdown lifecycle:
    - Startup: connect Redis, start aggregation job
    - Shutdown: cancel aggregation, close Redis
    """
    # Startup
    logger.info("🚀 Starting Mini URL Shortener...")

    # Initialize Redis (M5)
    init_redis()

    # Start background aggregation job (M4)
    aggregation_task = asyncio.create_task(run_aggregation_loop())
    logger.info("🔄 Background click aggregation job started")

    yield  # App is running

    # Shutdown
    logger.info("🛑 Shutting down Mini URL Shortener...")

    # Cancel aggregation job
    aggregation_task.cancel()
    try:
        await aggregation_task
    except asyncio.CancelledError:
        pass

    # Close Redis
    close_redis()
    logger.info("Shutdown complete")


# ── FastAPI Application ────────────────────────────────────
app = FastAPI(
    title="Mini URL Shortener",
    description="A production-grade URL shortener API with analytics, caching, and scaling support.",
    version="2.0.0",
    lifespan=lifespan,
)

# Register global exception handlers (M3)
register_exception_handlers(app)

# Include routers
app.include_router(health_router)
app.include_router(routes.router)


@app.get("/")
def root():
    return {"message": "Welcome to the Mini URL Shortener API. Visit /docs for Swagger documentation."}
