"""Minimal circuit breaker.

Each Gemini agent already retries individually (2-3 attempts with
exponential backoff). That's fine for a transient blip, but during a real
Gemini outage every single order burns through the same doomed retries —
several seconds of backoff — before failing anyway. Once enough
consecutive failures have been seen, the breaker "opens" and callers can
fail fast instead, until a cooldown window passes and one trial call is
allowed through (half-open) to check whether the API has recovered.

Thread-safe (`threading.Lock`, not `asyncio.Lock`): Gemini calls run via
`asyncio.to_thread`, so this is read/written from worker threads, not
coroutines.
"""

from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """True if calls should be short-circuited right now. Automatically
        flips back to False once the cooldown elapses, letting calls through
        again without needing an explicit "half-open" call site.

        Note: this is a best-effort half-open, not a strict single-trial
        gate. If two callers race past the cooldown boundary before either
        has called record_success/record_failure, both get a real trial call
        instead of one — acceptable here since this bot processes orders
        from a handful of allowlisted operators, not high-volume traffic;
        the worst case is a couple of wasted retries, not incorrect data.
        """
        with self._lock:
            if self._opened_at is None:
                return False
            return time.time() - self._opened_at < self.cooldown_seconds

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = time.time()

    def reset(self) -> None:
        """Testing/admin escape hatch - forces the breaker fully closed."""
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None


# One shared breaker for all three Gemini agents: they hit the same
# underlying API/quota, so an outage affects all of them together.
gemini_circuit_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=60.0)
