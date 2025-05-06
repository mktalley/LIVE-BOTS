import os, sys, json, time, logging, csv, pytz, smtplib, math
from pathlib import Path
from datetime import datetime, date, timedelta, time as dt_time
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
import signal
import random
from alpaca_trade_api.rest import REST, TimeFrame, APIError
from collections import deque, defaultdict
from typing import Sequence, Optional
# Load environment variables from .env file in repository root, if present
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, val = line.partition('=')
        if not sep:
            continue
        key = key.strip()
        # Remove inline comments
        val = val.split('#', 1)[0].strip()
        os.environ.setdefault(key, val)




def compute_sma(prices: Sequence[float]) -> Optional[float]:
    """Compute Simple Moving Average if enough data points are available."""
    if len(prices) < SMA_PERIOD:
        return None
    return sum(prices) / len(prices)


# ─── CONFIG ─────────────────────────────────────────────────────────────────────
# Load credentials and settings from environment (support legacy .env keys)
# API key/secret: allow APCA_* or (legacy) APCA_*_ID, APCA_*_KEY
API_KEY = os.getenv("APCA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY")
# Base URL: allow APCA_BASE_URL or (legacy) ALPACA_BASE_URL, default to production
BASE_URL = os.getenv("APCA_BASE_URL") or os.getenv("ALPACA_BASE_URL") or "https://api.alpaca.markets"

# Directory for local files
BASE_DIR = Path(__file__).parent

# File paths (can override filenames via env vars; resolves relative to BASE_DIR)
BOT_A_SYMBOLS_FILE = BASE_DIR / os.getenv("BOT_A_SYMBOLS_FILE", "botA_symbols.txt")
BOT_B_SYMBOLS_FILE = BASE_DIR / os.getenv("BOT_B_SYMBOLS_FILE", "botB_symbols.txt")
BASELINE_FILE = BASE_DIR / os.getenv("BASELINE_FILE", "baselines.json")
TRADE_LOG_FILE = BASE_DIR / os.getenv("TRADE_LOG_FILE", "trade_log.csv")
LOG_FILE_PATH = BASE_DIR / os.getenv("LOG_FILE_PATH", "sentinel.log")
PRICE_HISTORY_FILE = BASE_DIR / os.getenv("PRICE_HISTORY_FILE", "price_history.csv")

# Trading triggers
BUY_TRIGGER_A = float(os.getenv("BUY_TRIGGER_A", 0.995))
SELL_TRIGGER_A = float(os.getenv("SELL_TRIGGER_A", 1.09))
STOP_MULTIPLIER_A = float(os.getenv("STOP_MULTIPLIER_A", 0.3))
BUY_TRIGGER_B = float(os.getenv("BUY_TRIGGER_B", 0.98))
SELL_TRIGGER_B = float(os.getenv("SELL_TRIGGER_B", 1.03))
STOP_MULTIPLIER_B = float(os.getenv("STOP_MULTIPLIER_B", 0.5))

# Risk and baseline parameters
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
RISK_PCT = float(os.getenv("RISK_PCT", 0.015))
RESET_HOURS = int(os.getenv("RESET_HOURS", 6))
BASELINE_DRIFT = float(os.getenv("BASELINE_DRIFT", 0.05))
VOLATILITY_FILTER = float(os.getenv("VOLATILITY_FILTER", 0.02))
# Simple moving average period for trend filter
SMA_PERIOD = int(os.getenv("SMA_PERIOD", 20))

# Circuit breaker settings
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", 5))
CIRCUIT_BREAKER_COOLDOWN = int(os.getenv("CIRCUIT_BREAKER_COOLDOWN", 60))
# Track consecutive API call failures
error_streak = 0

# Email settings
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL", EMAIL_ADDRESS)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))

# Timezone and market hours (Eastern)
ET = pytz.timezone(os.getenv("ET_TIMEZONE", "US/Eastern"))
LUNCH_START = dt_time(int(os.getenv("LUNCH_START_HOUR", 11)), int(os.getenv("LUNCH_START_MIN", 30)))
LUNCH_END = dt_time(int(os.getenv("LUNCH_END_HOUR", 13)), int(os.getenv("LUNCH_END_MIN", 0)))
MARKET_CLOSE = dt_time(int(os.getenv("MARKET_CLOSE_HOUR", 16)), int(os.getenv("MARKET_CLOSE_MIN", 0)))
PT = pytz.timezone(os.getenv("PT_TIMEZONE", "US/Pacific"))  # Pacific Time


# Validate required environment variables
required_env = {
    "APCA_API_KEY": API_KEY,
    "APCA_API_SECRET": API_SECRET,
    "EMAIL_ADDRESS": EMAIL_ADDRESS,
    "EMAIL_PASSWORD": EMAIL_PASSWORD,
}
missing = [name for name, val in required_env.items() if not val]
if missing:
    sys.stderr.write(f"Missing required environment variables: {', '.join(missing)}\n")
    if __name__ == "__main__":
        sys.exit(1)

def is_lunch_time():
    now_et = datetime.now(ET).time()
    return LUNCH_START <= now_et < LUNCH_END

def is_market_close():
    now_et = datetime.now(ET).time()
    return now_et >= MARKET_CLOSE

# Configure logging in Pacific Time
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
formatter.converter = lambda *args: datetime.now(PT).timetuple()
file_handler = RotatingFileHandler(str(LOG_FILE_PATH), maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(stream_handler)
api = None
if __name__ == "__main__":
    api = REST(API_KEY, API_SECRET, BASE_URL)

# ─── EXPONENTIAL BACKOFF ────────────────────────────────────────────────────────
def retry_api_call(func, *args, retries=5, base_delay=5, **kwargs):
    global error_streak
    for i in range(retries):
        try:
            result = func(*args, **kwargs)
            # reset error streak on success
            error_streak = 0
            return result
        except Exception as e:
            # Return None for missing positions or symbols
            if any(msg in str(e) for msg in ("position does not exist", "symbol not found")):
                return None
            # increment error streak
            error_streak += 1
            logging.exception(f"Error calling {func.__name__} — full traceback")
            # Circuit breaker: cooldown on too many consecutive errors
            if error_streak >= CIRCUIT_BREAKER_THRESHOLD:
                logging.error(f"Circuit breaker tripped after {error_streak} errors. Cooling down for {CIRCUIT_BREAKER_COOLDOWN}s.")
                time.sleep(CIRCUIT_BREAKER_COOLDOWN)
                error_streak = 0
                return None
            # Jittered exponential backoff
            jitter = random.uniform(0.5, 1.5)
            wait = base_delay * (2 ** i) * jitter
            logging.warning(f"API call failed: {e}. Retrying in {wait:.1f}s...")
            time.sleep(wait)
    raise RuntimeError(f"API call failed after {retries} retries: {func.__name__}")

# ─── BASELINE MANAGEMENT ───────────────────────────────────────────────────────
def load_baselines():
    if not os.path.exists(BASELINE_FILE):
        return {}
    with open(BASELINE_FILE) as f:
        raw = json.load(f)
    out = {}
    now = datetime.now(PT)
    for sym, data in raw.items():
        if isinstance(data, dict) and "price" in data:
            ts = datetime.fromisoformat(data.get("ts", now.isoformat()))
            out[sym] = {"price": data["price"], "ts": ts}
    return out

def save_baselines(baselines):
    dump = {
        sym: {"price": v["price"], "ts": v["ts"].isoformat()}
        for sym, v in baselines.items()
    }
    with open(BASELINE_FILE, "w") as f:
        json.dump(dump, f, indent=2)

def record_price_history(symbol, price, baseline):
    try:
        with open(PRICE_HISTORY_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(["timestamp", "symbol", "price", "baseline"])
            writer.writerow([datetime.now(PT).isoformat(), symbol, price, baseline])
    except Exception as e:
        logging.error(f"Failed to record price history: {e}")

baselines = load_baselines()


# Sliding windows for SMA trend filter
price_windows = defaultdict(lambda: deque(maxlen=SMA_PERIOD))

# ─── POSITION & PRICE HELPERS ──────────────────────────────────────────────────
def get_position_info(symbol):
    p = retry_api_call(api.get_position, symbol)
    if not p:
        return 0.0, 0.0
    return float(p.qty), float(p.avg_entry_price)

def get_current_price(symbol):
    t = retry_api_call(api.get_latest_trade, symbol)
    if t: return t.price
    bars = retry_api_call(api.get_bars, symbol, TimeFrame.Minute, limit=1)
    return bars[-1].c if bars else None

def calculate_atr(symbol):
    bars = retry_api_call(api.get_bars, symbol, TimeFrame.Day, limit=ATR_PERIOD+1)
    if not bars:
        return None
    trs = []
    prev_close = bars[0].c
    for bar in bars[1:]:
        tr = max(bar.h - bar.l, abs(bar.h - prev_close), abs(bar.l - prev_close))
        trs.append(tr)
        prev_close = bar.c
    return sum(trs)/len(trs) if trs else None

def log_trade(action, symbol, qty, price):
    fieldnames = ["timestamp", "action", "symbol", "quantity", "price"]
    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, mode="a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(PT).isoformat(),
            "action": action,
            "symbol": symbol,
            "quantity": qty,
            "price": price
        })


# ─── GRACEFUL SHUTDOWN HANDLER ──────────────────────────────────────────
def graceful_shutdown(signum, frame):
    logging.info(f"Received signal {signum}, shutting down.")
    try:
        if summary and not sent_closing_email:
            send_email("Early Exit Market Summary", "\n".join(summary))
    except Exception:
        logging.exception("Error sending summary on shutdown")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)

logging.info("Market Sentinel bot started.")
summary = []
sent_closing_email = False
last_trading_date = None

while api is not None:
    try:
        clock = retry_api_call(api.get_clock)
        is_open, next_open = clock.is_open, clock.next_open

        # Reset summary and email flag at start of new trading day
        today_et = datetime.now(ET).date()
        if is_open and last_trading_date != today_et:
            summary.clear()
            sent_closing_email = False
            last_trading_date = today_et
        if not is_open:
            # Market closed: wait until open, waking up at most every minute
            now = datetime.now(next_open.tzinfo)
            seconds_until_open = (next_open - now).total_seconds()
            if seconds_until_open <= 0:
                # Open time reached; recheck immediately
                continue
            sleep_sec = min(seconds_until_open, 60)
            logging.info(f"Market closed. Sleeping {sleep_sec:.0f}s until next open at {next_open}")
            time.sleep(sleep_sec)
            continue

        cash = float(retry_api_call(api.get_account).cash)
        logging.info(f"Buying power (cash): ${cash:.2f}")

        for bot_name, file_path, bt, st, sm in [
            ("Bot A", BOT_A_SYMBOLS_FILE, BUY_TRIGGER_A, SELL_TRIGGER_A, STOP_MULTIPLIER_A),
            ("Bot B", BOT_B_SYMBOLS_FILE, BUY_TRIGGER_B, SELL_TRIGGER_B, STOP_MULTIPLIER_B),
        ]:
            try:
                with open(file_path) as f:
                    symbols = [line.strip() for line in f if line.strip()]
            except Exception as e:
                logging.error(f"Failed to read symbols from {file_path}: {e}")
                continue
            for sym in symbols:
                price = get_current_price(sym)
                if not price:
                    continue
                qty, avg_entry = get_position_info(sym)

                now = datetime.now(PT)
                bl = baselines.get(sym)
                atr = calculate_atr(sym) or 0

                reset_req = False
                if bl is None:
                    reset_req = True
                elif (now - bl["ts"]) > timedelta(hours=RESET_HOURS):
                    reset_req = True
                elif abs(price - bl["price"]) / bl["price"] > BASELINE_DRIFT:
                    if atr > 0 and (atr / price) > VOLATILITY_FILTER:
                        reset_req = True

                if reset_req:
                    baselines[sym] = {"price": price, "ts": now}
                    save_baselines(baselines)
                    logging.info(f"[{bot_name}][{sym}] Reset baseline → ${price:.2f}")

                base_price = baselines[sym]["price"]
                record_price_history(sym, price, base_price)
                # SMA trend filter
                price_windows[sym].append(price)
                sma = compute_sma(price_windows[sym])
                if sma is None:
                    logging.info(f"[{bot_name}][{sym}] Waiting for SMA warm-up ({len(price_windows[sym])}/{SMA_PERIOD})")
                    continue
                trend_ok = price > sma
                logging.info(f"[{bot_name}][{sym}] SMA:{sma:.2f}, Trend:{'PASS' if trend_ok else 'FAIL'}")


                buy_price = base_price * bt
                sell_price = base_price * st
                stop_price = max(base_price - atr * sm, 0)

                logging.info(
                    f"[{bot_name}][{sym}] Base:${base_price:.2f}, Curr:${price:.2f}, Buy@${buy_price:.2f}, Sell@${sell_price:.2f}, Stop@${stop_price:.2f}, Owned={qty:.4f}"
                )

                if qty == 0 and price <= buy_price and trend_ok and not is_lunch_time():
                    qty_to_buy = round((cash * RISK_PCT) / price, 6)
                    retry_api_call(api.submit_order, symbol=sym, qty=qty_to_buy, side="buy", type="market", time_in_force="day")
                    log_trade("buy", sym, qty_to_buy, price)
                    summary.append(f"[{bot_name}] BUY {qty_to_buy:.6f} of {sym} @ ${price:.2f}")
                elif qty > 0:
                    if price >= sell_price:
                        retry_api_call(api.submit_order, symbol=sym, qty=qty, side="sell", type="market", time_in_force="day")
                        log_trade("sell", sym, qty, price)
                        summary.append(f"[{bot_name}] SELL (target) {qty:.6f} of {sym} @ ${price:.2f}")
                    elif price <= stop_price:
                        retry_api_call(api.submit_order, symbol=sym, qty=qty, side="sell", type="market", time_in_force="day")
                        log_trade("sell", sym, qty, price)
                        summary.append(f"[{bot_name}] SELL (stop) {qty:.6f} of {sym} @ ${price:.2f}")

        if is_market_close() and not sent_closing_email:
            equity = float(retry_api_call(api.get_account).equity)
            positions = retry_api_call(api.list_positions) or []
            unrealized = sum(float(p.unrealized_pl) for p in positions)
            summary.append("")
            summary.append(f"EOD Equity: ${equity:.2f}")
            summary.append(f"Unrealized P/L: ${unrealized:.2f}")
            send_email("Daily Market Summary", "\n".join(summary or ["No trades today."]))
            sent_closing_email = True

        time.sleep(60)
    except Exception:
        logging.exception("Main loop error — full traceback")
        time.sleep(60)