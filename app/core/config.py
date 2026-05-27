from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Mini URL Shortener"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/shortener"
    
    class Config:
        env_file = ".env"

settings = Settings()
