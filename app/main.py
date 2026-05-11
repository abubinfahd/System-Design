from fastapi import FastAPI
from app.db.database import engine, Base
from app.api import routes

# Create the database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Mini URL Shortener",
    description="A simple URL shortener API built for Milestone 1",
    version="1.0.0"
)

# Include our routes
app.include_router(routes.router)

@app.get("/")
def root():
    return {"message": "Welcome to the Mini URL Shortener API. Visit /docs for Swagger documentation."}
