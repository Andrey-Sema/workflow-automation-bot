"""Lazy, process-wide Gemini client factory.

Previously every agent module created its own `genai.Client(...)` at import
time, which crashed the whole process (including `pytest` collection) the
moment `GEMINI_API_KEY` was missing. The client is now built once, on first
actual use, and cached.
"""

from __future__ import annotations

from functools import lru_cache

from google import genai

from src.settings import get_settings, require_gemini_api_key


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    settings = get_settings()
    api_key = require_gemini_api_key(settings)
    return genai.Client(api_key=api_key.get_secret_value())
