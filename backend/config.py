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
    # Use a supported Vision model (gemini-2.0-flash is stable & cost-efficient)
    gemini_model: str = "gemini-2.0-flash"
    
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
    max_extraction_workers: int = 2  # Number of parallel workers for page processing

    # NEW: Use optimized pipeline (CV preprocessing + token optimization)
    use_optimized_pipeline: bool = True  # Set to False to use legacy pipeline

    # Output format
    minimal_output: bool = True  # Generate minimal JSON output (answers + identifiers only)

    # Prompt caching (to reduce repeated format analysis)
    cache_page_prompts: bool = True
    page_hash_size: int = 16

    # DigitalOcean Spaces
    spaces_endpoint: Optional[str] = None
    spaces_bucket: Optional[str] = None
    spaces_region: Optional[str] = None
    spaces_key: Optional[str] = None
    spaces_secret: Optional[str] = None
    archive_images_to_spaces: bool = False
    spaces_image_archive_folder: str = "exams-extraction-pics"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
