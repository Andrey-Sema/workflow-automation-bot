import time

from src.circuit_breaker import CircuitBreaker


def test_starts_closed():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    assert breaker.is_open is False


def test_opens_after_threshold_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open is False
    breaker.record_failure()
    assert breaker.is_open is True


def test_success_resets_failure_count():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open is False  # only 2 consecutive since the reset


def test_success_closes_an_open_breaker():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure()
    assert breaker.is_open is True
    breaker.record_success()
    assert breaker.is_open is False


def test_reopens_after_a_success_then_new_failures():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.is_open is True


def test_half_opens_after_cooldown_elapses():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure()
    assert breaker.is_open is True
    time.sleep(0.1)
    assert breaker.is_open is False


def test_reset_forces_closed():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure()
    assert breaker.is_open is True
    breaker.reset()
    assert breaker.is_open is False
