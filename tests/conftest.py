"""Session-wide fixtures.

`gemini_circuit_breaker` is a module-level singleton (src/circuit_breaker.py)
shared by every agent module — correct in production (one process, one
real Gemini quota), but a test-isolation trap otherwise: a handful of
tests deliberately simulate Gemini failures, and without a reset those
failures would accumulate *across* test files sharing the same pytest
session until the breaker actually trips, making an unrelated, later test
fail with GeminiUnavailableError for no reason visible in that test itself.
"""

import pytest

from src.circuit_breaker import gemini_circuit_breaker


@pytest.fixture(autouse=True)
def _reset_gemini_circuit_breaker():
    gemini_circuit_breaker.reset()
    yield
    gemini_circuit_breaker.reset()
