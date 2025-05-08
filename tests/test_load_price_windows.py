import csv
import pytest
from collections import deque

def test_load_price_windows(tmp_path, monkeypatch):
    # Import inside the test to allow monkeypatching module attributes
    import market_sentinel.main as main

    # Prepare a temporary price history file
    price_file = tmp_path / "price_history.csv"
    # Monkeypatch the PRICE_HISTORY_FILE path
    monkeypatch.setattr(main, "PRICE_HISTORY_FILE", price_file)

    # Generate timestamps: some for today, some for previous day
    now_et = main.datetime.now(main.ET)
    # Times for today's entries (more than SMA_PERIOD to test deque maxlen)
    times_today = [now_et.replace(minute=(i % 60), second=0, microsecond=0) for i in range(main.SMA_PERIOD + 2)]
    # A previous-day timestamp
    prev_day_ts = (now_et - main.timedelta(days=1)).replace(minute=0, second=0, microsecond=0)

    # Write header and rows to the CSV file
    with open(price_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "price", "baseline"])
        # Add previous-day row (should be filtered out)
        writer.writerow([prev_day_ts.isoformat(), "SYM", "100", "99"] )
        # Add today's rows
        for idx, ts in enumerate(times_today):
            # Use varying prices
            price = 200.0 + idx
            writer.writerow([ts.isoformat(), "SYM", str(price), "199"])

    # Call the loader
    windows = main.load_price_windows()
    # Verify that only today's entries are loaded
    assert "SYM" in windows
    loaded_prices = list(windows["SYM"])
    # Since deque maxlen is SMA_PERIOD, we expect the last SMA_PERIOD prices
    expected_prices = [200.0 + i for i in range(len(times_today))][-main.SMA_PERIOD:]
    assert loaded_prices == expected_prices

    # Verify compute_sma on the loaded window
    sma_value = main.compute_sma(windows["SYM"])
    assert sma_value == pytest.approx(sum(expected_prices) / main.SMA_PERIOD)
