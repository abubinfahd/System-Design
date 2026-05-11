from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Mini URL Shortener"
    DATABASE_URL: str = "sqlite:///./shortener.db"
    
    class Config:
        env_file = ".env"

settings = Settings()
