"""
Application configuration management
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Local storage
    storage_root: str = "./storage"
    
    # Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    
    # Database
    database_url: str = ""
    
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
    
    # AI Extraction Performance
    use_parallel_extraction: bool = True  # Enable multi-threading for faster extraction
    max_extraction_workers: int = 4  # Number of parallel workers for page processing
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
