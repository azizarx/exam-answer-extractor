"""
Gemini model selection helpers.
Provides safe model fallback selection with a single shared implementation.
"""
from typing import List, Optional, Tuple
import logging

import google.generativeai as genai

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _parse_models(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _candidate_models(preferred_model: Optional[str] = None) -> List[str]:
    settings = get_settings()
    candidates: List[str] = []

    if preferred_model and preferred_model.strip():
        candidates.append(preferred_model.strip())
    elif settings.gemini_model.strip():
        candidates.append(settings.gemini_model.strip())

    candidates.extend(_parse_models(settings.gemini_fallback_models))

    # De-duplicate while preserving order
    ordered: List[str] = []
    seen = set()
    for model in candidates:
        key = model.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(model)
    return ordered


def _resolve_available_model(candidates: List[str]) -> str:
    settings = get_settings()
    if not candidates:
        return "gemini-2.0-flash"

    if not settings.gemini_auto_fallback or len(candidates) == 1:
        return candidates[0]

    try:
        available = set()
        for model in genai.list_models():  # type: ignore[attr-defined]
            methods = set(getattr(model, "supported_generation_methods", []) or [])
            if "generateContent" not in methods:
                continue

            model_name = str(getattr(model, "name", "") or "")
            if not model_name:
                continue
            short_name = model_name.split("/")[-1]
            available.add(model_name.lower())
            available.add(short_name.lower())

        for candidate in candidates:
            if candidate.lower() in available:
                if candidate.lower() != candidates[0].lower():
                    logger.warning(
                        "Primary Gemini model '%s' unavailable, using fallback '%s'",
                        candidates[0],
                        candidate,
                    )
                return candidate

        logger.warning(
            "None of configured Gemini models were discovered by list_models; using '%s'",
            candidates[0],
        )
        return candidates[0]
    except Exception as exc:
        logger.warning("Could not list Gemini models for fallback resolution: %s", exc)
        return candidates[0]


def create_gemini_model(
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> Tuple[genai.GenerativeModel, str]:
    """
    Configure Gemini client and create a model with fallback-aware selection.
    Returns (model_instance, selected_model_name).
    """
    settings = get_settings()
    final_api_key = api_key if api_key is not None else settings.gemini_api_key
    genai.configure(api_key=final_api_key)  # type: ignore[attr-defined]

    selected = _resolve_available_model(_candidate_models(preferred_model=preferred_model))
    model = genai.GenerativeModel(selected)  # type: ignore[attr-defined]
    return model, selected
