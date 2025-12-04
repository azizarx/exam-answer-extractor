"""
Local file storage utilities for saving and retrieving uploads/results.
"""
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, BinaryIO, Dict
from uuid import uuid4

from backend.config import get_settings

logger = logging.getLogger(__name__)


class LocalStorage:
    """Simple local filesystem storage with upload/result directories."""

    def __init__(self):
        settings = get_settings()
        self.base_path = Path(settings.storage_root).expanduser().resolve()
        self.uploads_path = self.base_path / "uploads"
        self.results_path = self.base_path / "results"
        for path in (self.base_path, self.uploads_path, self.results_path):
            path.mkdir(parents=True, exist_ok=True)
        logger.info("Initialized LocalStorage at %s", self.base_path)

    def _safe_name(self, filename: str) -> str:
        """Return a filesystem-safe version of the provided filename."""
        name = Path(filename).name
        return name.replace(" ", "_")

    def _unique_name(self, filename: str, suffix: Optional[str] = None) -> str:
        safe_name = self._safe_name(filename)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        unique = uuid4().hex[:8]
        if suffix:
            safe_name = f"{Path(safe_name).stem}.{suffix}"
        return f"{timestamp}_{unique}_{safe_name}"

    def _build_result(self, path: Path) -> Dict[str, str]:
        relative = path.relative_to(self.base_path)
        return {
            "status": "success",
            "filename": path.name,
            "relative_path": str(relative).replace(os.sep, "/"),
            "absolute_path": str(path),
        }

    def save_pdf(self, file_obj: BinaryIO, filename: str) -> Dict[str, str]:
        """Persist a PDF upload to disk."""
        final_name = self._unique_name(filename)
        destination = self.uploads_path / final_name
        file_obj.seek(0)
        with open(destination, "wb") as dest:
            shutil.copyfileobj(file_obj, dest)
        logger.info("Saved PDF %s to %s", filename, destination)
        return self._build_result(destination)

    def save_json(self, json_data: str, filename: str) -> Dict[str, str]:
        """Persist JSON results to disk."""
        final_name = self._unique_name(filename, suffix="json")
        destination = self.results_path / final_name
        destination.write_text(json_data, encoding="utf-8")
        logger.info("Saved JSON %s to %s", filename, destination)
        return self._build_result(destination)

    def read_json(self, relative_path: str) -> Optional[str]:
        """Read stored JSON results; returns None if missing."""
        path = self.get_absolute_path(relative_path)
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning("JSON path %s not found", relative_path)
        return None

    def delete_file(self, relative_path: Optional[str]) -> bool:
        """Delete a stored file if it exists."""
        if not relative_path:
            return False
        path = self.get_absolute_path(relative_path)
        if path and path.exists():
            try:
                path.unlink()
                logger.info("Deleted stored file %s", path)
                return True
            except OSError as exc:
                logger.error("Failed to delete %s: %s", path, exc)
        return False

    def get_absolute_path(self, relative_path: Optional[str]) -> Optional[Path]:
        if not relative_path:
            return None
        candidate = self.base_path / relative_path
        try:
            candidate = candidate.resolve()
        except FileNotFoundError:
            return candidate
        return candidate


_local_storage: Optional[LocalStorage] = None


def get_local_storage() -> LocalStorage:
    global _local_storage
    if _local_storage is None:
        _local_storage = LocalStorage()
    return _local_storage
