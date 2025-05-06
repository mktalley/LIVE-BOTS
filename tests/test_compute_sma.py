import pytest
from collections import deque

from market_sentinel.main import compute_sma, SMA_PERIOD


def test_compute_sma_insufficient_data():
    prices = list(range(SMA_PERIOD - 1))
    assert compute_sma(prices) is None


def test_compute_sma_exact_data():
    prices = [float(i) for i in range(1, SMA_PERIOD + 1)]
    expected = sum(prices) / SMA_PERIOD
    assert compute_sma(prices) == pytest.approx(expected)


def test_compute_sma_with_deque_overflow():
    # Use deque with maxlen SMA_PERIOD to simulate sliding window
    values = list(range(1, SMA_PERIOD + 5))
    window = deque(values, maxlen=SMA_PERIOD)
    # Expect average of last SMA_PERIOD values
    expected = sum(values[-SMA_PERIOD:]) / SMA_PERIOD
    assert compute_sma(window) == pytest.approx(expected)
