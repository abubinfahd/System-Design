from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Mini URL Shortener"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/shortener"
    BASE_URL: str = "http://localhost:8000"
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"

settings = Settings()
