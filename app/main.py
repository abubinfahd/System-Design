from fastapi import FastAPI
from app.db.database import engine, Base
from app.api import routes
from app.core.errors import register_exception_handlers

# Create the database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Mini URL Shortener",
    description="A production-grade URL shortener API with versioning, analytics, custom aliases, and expiration support.",
    version="1.1.0"
)

# Register global exception handlers
register_exception_handlers(app)

# Include our routes
app.include_router(routes.router)

@app.get("/")
def root():
    return {"message": "Welcome to the Mini URL Shortener API. Visit /docs for Swagger documentation."}
