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
    # Primary model to use for extraction.
    gemini_model: str = "gemini-2.5-pro"
    # Ordered fallback list used when the primary model is unavailable.
    gemini_fallback_models: str = "gemini-2.0-flash,gemini-2.0-flash-lite"
    gemini_auto_fallback: bool = True
    
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
    
    # Per-page parallelism inside one submission. Pure I/O parallelism (each
    # worker holds the GIL while waiting on Gemini), but each worker also
    # carries a PIL image, so don't crank this up too high — 10 workers per
    # backend × 5 parallel backends OOM'd a 27GB host in the May 2026 bench.
    # 6 is the sweet spot for one backend: roughly halves wall-clock vs 3,
    # peak working set ~150MB.
    max_extraction_workers: int = 6

    # Output format
    minimal_output: bool = False  # Keep full candidate metadata in generated JSON by default

    # Image preprocessing (improves contrast/clarity before extraction)
    enable_image_preprocessing: bool = True
    preprocessing_mode: str = "balanced"  # balanced | aggressive

    # Mathpix /v3/pdf — required when a chosen template flags any question as
    # type=diagram. We submit the PDF once, poll, then regex CDN URLs out of
    # the returned .mmd and assign them by question label.
    mathpix_app_id: str = ""
    mathpix_app_key: str = ""
    mathpix_poll_interval_seconds: float = 3.0
    mathpix_max_wait_seconds: float = 600.0

    # NOTE: Gemini calls intentionally do NOT pass max_output_tokens. With
    # gemini-2.5-pro, reasoning tokens are billed from the same budget; a
    # 1024 cap (the old default) caused finish_reason=MAX_TOKENS on every
    # call and starved the visible JSON. The model's default cap (~64k) is
    # what we want.

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
