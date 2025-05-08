import json
import pytest
from collections import deque

import market_sentinel.main as main

def test_save_and_load_sma_state(tmp_path, monkeypatch):
    # Prepare a temporary SMA state file
    state_file = tmp_path / "sma_state.json"
    monkeypatch.setattr(main, "SMA_STATE_FILE", state_file)
    # Monkeypatch price history file to ensure it's not used
    monkeypatch.setattr(main, "PRICE_HISTORY_FILE", tmp_path / "nonexistent.csv")

    # Create test windows dict with some data
    windows = {
        "SYM1": deque([10.0, 20.0, 30.0], maxlen=main.SMA_PERIOD),
        "SYM2": deque([5.0], maxlen=main.SMA_PERIOD)
    }

    # Save the SMA state
    main.save_sma_state(windows)
    # State file should exist
    assert state_file.exists(), "SMA state file was not created"

    # Load the file content
    data = json.loads(state_file.read_text())
    assert data.get("date") == main.datetime.now(main.ET).date().isoformat()
    assert "SYM1" in data.get("windows", {})
    assert data["windows"]["SYM1"] == [10.0, 20.0, 30.0]
    assert data["windows"]["SYM2"] == [5.0]

    # Now load via load_price_windows (should use state file)
    loaded = main.load_price_windows()
    # Ensure loaded windows match saved data
    assert isinstance(loaded, dict)
    assert "SYM1" in loaded and isinstance(loaded["SYM1"], deque)
    assert list(loaded["SYM1"]) == [10.0, 20.0, 30.0]
    assert list(loaded["SYM2"]) == [5.0]

    # Since loaded windows have fewer points than SMA_PERIOD, expect compute_sma to return None
    assert main.compute_sma(loaded["SYM1"]) is None
    assert main.compute_sma(loaded["SYM2"]) is None

