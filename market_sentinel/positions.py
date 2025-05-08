import json
import os
import logging
from pathlib import Path
from datetime import datetime

# File for persisting open positions across restarts
BASE_DIR = Path(__file__).parent
POSITIONS_FILE = BASE_DIR / os.getenv("POSITIONS_FILE", "positions.json")


def load_positions():
    """Load open positions from the positions file. Returns dict mapping trade_id to position info."""
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        positions = {}
        for trade_id, pos in data.items():
            try:
                positions[trade_id] = {
                    "symbol": pos["symbol"],
                    "quantity": pos["quantity"],
                    "entry_price": pos["entry_price"],
                    "open_time": datetime.fromisoformat(pos["open_time"])
                }
            except Exception:
                logging.exception(f"Malformed position entry for trade_id {trade_id}")
        logging.info(f"Loaded positions: {list(positions.keys())}")
        return positions
    except FileNotFoundError:
        logging.info(f"No positions file found at {POSITIONS_FILE}, starting fresh")
        return {}
    except Exception as e:
        logging.error(f"Failed to load positions: {e}")
        return {}


def save_positions(positions):
    """Save open positions to the positions file."""
    try:
        data = {}
        for trade_id, pos in positions.items():
            data[trade_id] = {
                "symbol": pos["symbol"],
                "quantity": pos["quantity"],
                "entry_price": pos["entry_price"],
                "open_time": pos["open_time"].isoformat()
            }
        with open(POSITIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Saved positions to {POSITIONS_FILE}")
    except Exception as e:
        logging.error(f"Failed to save positions: {e}")
