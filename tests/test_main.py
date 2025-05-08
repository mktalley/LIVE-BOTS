import os
import csv
import json
import logging
import shutil
import pytest
from pathlib import Path
from datetime import datetime

import market_sentinel.main as main
import market_sentinel.positions as posmod

# Ensure a clean environment for each test by removing test files if present
def setup_function(function):
    # Reset any environment-altered file paths
    pass


def test_migrate_trade_log_creates_new_and_renames_legacy(tmp_path, monkeypatch):
    # Setup a legacy trade log missing trade_id header
    legacy_file = tmp_path / "trade_log.csv"
    legacy_file.write_text(
        "timestamp,action,symbol,quantity,entry_price,price,profit\n"
        "2025-01-01T00:00:00, buy, XYZ,1,100,100,\n"
    )
    monkeypatch.setattr(main, 'TRADE_LOG_FILE', legacy_file)
    # Run migration
    main.migrate_trade_log()
    # Check legacy file was renamed
    legacy_variants = [p.name for p in tmp_path.iterdir() if p.suffix == '.csv' and 'legacy_' in p.name]
    assert any('trade_log.legacy_' in name for name in legacy_variants)
    # New trade_log.csv should exist
    assert legacy_file.exists()
    header = legacy_file.read_text().splitlines()[0]
    assert 'trade_id' in header


def test_migrate_trade_log_initializes_empty(tmp_path, monkeypatch):
    trade_file = tmp_path / 'trade_log.csv'
    monkeypatch.setattr(main, 'TRADE_LOG_FILE', trade_file)
    # No file initially
    if trade_file.exists():
        trade_file.unlink()
    main.migrate_trade_log()
    assert trade_file.exists()
    header = trade_file.read_text().splitlines()[0]
    assert header == 'timestamp,action,trade_id,symbol,quantity,entry_price,price,profit'


def test_validate_trades_logs_warnings(tmp_path, monkeypatch, caplog):
    trade_file = tmp_path / 'trade_log.csv'
    rows = [
        {'timestamp': '2025-01-01T00:00:00', 'action': 'sell', 'trade_id': '1', 'symbol': 'A', 'quantity': '1', 'entry_price': '', 'price': '', 'profit': ''},
        {'timestamp': '2025-01-01T00:00:00', 'action': 'buy',  'trade_id': '2', 'symbol': 'B', 'quantity': '1', 'entry_price': '1', 'price': '1', 'profit': ''},
        {'timestamp': '2025-01-01T00:00:00', 'action': 'sell', 'trade_id': '2', 'symbol': 'B', 'quantity': '1', 'entry_price': '', 'price': '', 'profit': ''},
        {'timestamp': '2025-01-01T00:00:00', 'action': 'sell', 'trade_id': '2', 'symbol': 'B', 'quantity': '1', 'entry_price': '', 'price': '', 'profit': ''},
    ]
    with trade_file.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    monkeypatch.setattr(main, 'TRADE_LOG_FILE', trade_file)
    caplog.set_level(logging.WARNING)
    main.validate_trades()
    messages = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING']
    assert any('sell without matching buy' in m for m in messages)
    assert any('duplicate close for trade_id 2' in m for m in messages)


def test_log_trade_and_header(tmp_path, monkeypatch):
    trade_file = tmp_path / 'trade_log.csv'
    monkeypatch.setattr(main, 'TRADE_LOG_FILE', trade_file)
    if trade_file.exists():
        trade_file.unlink()
    # Log a buy without profit
    main.log_trade('buy', 'TST', 2, 10, trade_id='abc', entry_price=5)
    content = trade_file.read_text().splitlines()
    header = content[0].split(',')
    assert header == ['timestamp','action','trade_id','symbol','quantity','entry_price','price','profit']
    row = content[1].split(',')
    # Validate fields
    assert row[1] == 'buy'
    assert row[2] == 'abc'
    assert row[3] == 'TST'
    assert row[4] == '2'
    assert row[5] == '5'
    assert row[6] == '10'
    assert row[7] == ''


def test_positions_roundtrip(tmp_path, monkeypatch):
    pfile = tmp_path / 'positions.json'
    monkeypatch.setattr(posmod, 'POSITIONS_FILE', pfile)
    # Prepare a sample position
    now = datetime.now()
    positions = {'x': {'symbol': 'X', 'quantity': 1, 'entry_price': 100, 'open_time': now}}
    posmod.save_positions(positions)
    loaded = posmod.load_positions()
    assert set(loaded.keys()) == {'x'}
    assert loaded['x']['symbol'] == 'X'
    assert loaded['x']['quantity'] == 1


def test_execute_buy_and_sell(tmp_path, monkeypatch):
    trade_file = tmp_path / 'trade_log.csv'
    pos_file = tmp_path / 'positions.json'
    monkeypatch.setattr(main, 'TRADE_LOG_FILE', trade_file)
    monkeypatch.setattr(posmod, 'POSITIONS_FILE', pos_file)
    # Empty positions
    open_positions = {}
    # Buy
    trade_id = main.execute_buy('ABC', 3, 10, open_positions)
    assert trade_id in open_positions
    assert open_positions[trade_id]['symbol'] == 'ABC'
    # Sell
    sold_id, profit = main.execute_sell('ABC', 3, 12, open_positions)
    assert sold_id == trade_id
    assert profit == pytest.approx((12-10)*3)
    # Positions file should now be empty
    assert json.loads(pos_file.read_text()) == {}
