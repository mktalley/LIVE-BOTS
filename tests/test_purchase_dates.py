import json
from datetime import date, timedelta
import pytest

import market_sentinel.main as ms


def test_save_purchase_dates_writes_correct_structure(tmp_path, monkeypatch):
    # Setup a temporary purchase dates file
    file_path = tmp_path / "purchase_dates.json"
    monkeypatch.setattr(ms, "PURCHASE_DATES_FILE", file_path)

    # Define a sample mapping with dates
    sample_dates = {
        "AAA": date(2020, 1, 1),
        "BBB": date.today(),
    }

    # Call save_purchase_dates
    ms.save_purchase_dates(sample_dates)

    # Read and parse the file
    assert file_path.exists(), "purchase_dates.json was not created"
    data = json.loads(file_path.read_text())

    # Verify top-level 'date' is today's date
    today_str = date.today().isoformat()
    assert data.get("date") == today_str

    # Verify 'purchase_dates' mapping has correct ISO date strings
    expected_pd = {sym: d.isoformat() for sym, d in sample_dates.items()}
    assert data.get("purchase_dates") == expected_pd


def test_load_purchase_dates_with_current_date(tmp_path, monkeypatch):
    # Prepare a file with today's date and sample entries
    file_path = tmp_path / "purchase_dates.json"
    monkeypatch.setattr(ms, "PURCHASE_DATES_FILE", file_path)

    today_str = date.today().isoformat()
    content = {
        "date": today_str,
        "purchase_dates": {
            "SYM1": today_str,
            "SYM2": today_str,
        },
    }
    file_path.write_text(json.dumps(content))

    # Load purchase dates
    loaded = ms.load_purchase_dates()

    # Expect date objects for each symbol
    expected = {"SYM1": date.fromisoformat(today_str), "SYM2": date.fromisoformat(today_str)}
    assert loaded == expected


def test_load_purchase_dates_ignores_stale_file(tmp_path, monkeypatch):
    # Prepare a file with yesterday's date
    file_path = tmp_path / "purchase_dates.json"
    monkeypatch.setattr(ms, "PURCHASE_DATES_FILE", file_path)

    yesterday = date.today() - timedelta(days=1)
    content = {
        "date": yesterday.isoformat(),
        "purchase_dates": {
            "OLD": yesterday.isoformat(),
        },
    }
    file_path.write_text(json.dumps(content))

    # Load purchase dates should ignore stale data
    loaded = ms.load_purchase_dates()
    assert loaded == {}


def test_load_purchase_dates_no_file(tmp_path, monkeypatch):
    # No file exists
    file_path = tmp_path / "nonexistent.json"
    monkeypatch.setattr(ms, "PURCHASE_DATES_FILE", file_path)

    loaded = ms.load_purchase_dates()
    assert loaded == {}
