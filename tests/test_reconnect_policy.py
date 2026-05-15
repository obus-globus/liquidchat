"""Unit tests for the reconnect backoff policy."""

from __future__ import annotations

import random

import pytest

from liquidchat import ReconnectPolicy


def test_delay_grows_exponentially_then_caps() -> None:
    random.seed(0)
    p = ReconnectPolicy(base_delay=1.0, max_delay=10.0)
    delays = [p.delay(i) for i in range(10)]
    # First delay is around 1.0 ± 10%
    assert 0.85 <= delays[0] <= 1.15
    # Past the cap, delays should cluster around max_delay (within jitter).
    for d in delays[5:]:
        assert 8.5 <= d <= 11.5


def test_delay_jitter_introduces_variance() -> None:
    p = ReconnectPolicy(base_delay=2.0, max_delay=100.0)
    seen: set[float] = {p.delay(3) for _ in range(50)}
    assert len(seen) > 5, "expected jitter to vary the delay"


def test_default_policy_sane() -> None:
    p = ReconnectPolicy()
    assert p.base_delay > 0
    assert p.max_delay > p.base_delay
    assert p.max_attempts > 0


@pytest.mark.parametrize("base", [0.5, 1.0, 5.0])
def test_delay_is_positive(base: float) -> None:
    p = ReconnectPolicy(base_delay=base, max_delay=base * 10)
    for i in range(20):
        assert p.delay(i) > 0
