"""
Application configuration management
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # DigitalOcean Spaces
    spaces_region: str = "sgp1"
    spaces_endpoint: str = "https://sgp1.digitaloceanspaces.com"
    spaces_key: str
    spaces_secret: str
    spaces_bucket: str = "exam-answer-sheets"
    
    # Google Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-1.5-flash"
    
    # Database
    database_url: str
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Application
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    
    # File Upload
    max_file_size_mb: int = 50
    allowed_extensions: str = ".pdf"
    
    # PDF Processing
    poppler_path: Optional[str] = None  # Optional: Path to Poppler binaries
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
