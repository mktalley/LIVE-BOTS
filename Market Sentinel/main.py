import os, json, time, logging, csv, pytz, smtplib, math
from datetime import datetime, date, timedelta, time as dt_time
from email.mime.text import MIMEText
from alpaca_trade_api.rest import REST, TimeFrame, APIError

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
API_KEY    = "AK8XASA88WMTSRKFMRN3"
API_SECRET = "96cnaUhRf4OaGM98QbDZJbCLCuWRmwAKEvreIzEu"
BASE_URL   = "https://api.alpaca.markets"

BOT_A_SYMBOLS_FILE = "botA_symbols.txt"
BOT_B_SYMBOLS_FILE = "botB_symbols.txt"
BASELINE_FILE      = "baselines.json"
TRADE_LOG_FILE     = "trade_log.csv"
LOG_FILE_PATH      = "sentinel.log"
PRICE_HISTORY_FILE = "price_history.csv"

BUY_TRIGGER_A      = 0.995
SELL_TRIGGER_A     = 1.09
STOP_MULTIPLIER_A  = 0.3

BUY_TRIGGER_B      = 0.98
SELL_TRIGGER_B     = 1.03
STOP_MULTIPLIER_B  = 0.5

ATR_PERIOD     = 14
RISK_PCT       = 0.015
RESET_HOURS    = 6
BASELINE_DRIFT = 0.05
VOLATILITY_FILTER = 0.02  # don’t reset baseline if volatility too low

EMAIL_ADDRESS  = "mktalley@gmail.com"
EMAIL_PASSWORD = "eooncglziamrtcyw"
TO_EMAIL       = "mktalley@icloud.com"
EMAIL_HOST     = "smtp.gmail.com"
EMAIL_PORT     = 587

ET           = pytz.timezone("US/Eastern")
LUNCH_START  = dt_time(11, 30)
LUNCH_END    = dt_time(13, 0)
MARKET_CLOSE = dt_time(16, 0)

def is_lunch_time():
    now_et = datetime.now(ET).time()
    return LUNCH_START <= now_et < LUNCH_END

def is_market_close():
    now_et = datetime.now(ET).time()
    return now_et >= MARKET_CLOSE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler()
    ]
)
api = REST(API_KEY, API_SECRET, BASE_URL)

# ─── EXPONENTIAL BACKOFF ────────────────────────────────────────────────────────
def retry_api_call(func, *args, retries=5, base_delay=5, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "position does not exist" in str(e) or "symbol not found" in str(e):
                return None
            wait = base_delay * (2 ** i)
            logging.warning(f"API call failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"API call failed after {retries} retries: {func.__name__}")

# ─── BASELINE MANAGEMENT ───────────────────────────────────────────────────────
def load_baselines():
    if not os.path.exists(BASELINE_FILE):
        return {}
    with open(BASELINE_FILE) as f:
        raw = json.load(f)
    out = {}
    now = datetime.utcnow()
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
    with open(PRICE_HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["timestamp", "symbol", "price", "baseline"])
        writer.writerow([datetime.utcnow().isoformat(), symbol, price, baseline])

baselines = load_baselines()

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
            "timestamp": datetime.utcnow().isoformat(),
            "action": action,
            "symbol": symbol,
            "quantity": qty,
            "price": price
        })

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

while True:
    try:
        clock = retry_api_call(api.get_clock)
        is_open, next_open = clock.is_open, clock.next_open
        if not is_open:
            logging.info(f"Market closed. Sleeping until {next_open}…")
            time.sleep(600)
            continue

        cash = float(retry_api_call(api.get_account).cash)
        logging.info(f"Buying power (cash): ${cash:.2f}")

        for bot_name, file_path, bt, st, sm in [
            ("Bot A", BOT_A_SYMBOLS_FILE, BUY_TRIGGER_A, SELL_TRIGGER_A, STOP_MULTIPLIER_A),
            ("Bot B", BOT_B_SYMBOLS_FILE, BUY_TRIGGER_B, SELL_TRIGGER_B, STOP_MULTIPLIER_B),
        ]:
            symbols = retry_api_call(open, file_path).read().split()
            for sym in symbols:
                price = get_current_price(sym)
                if not price:
                    continue
                qty, avg_entry = get_position_info(sym)

                now = datetime.utcnow()
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

                buy_price = base_price * bt
                sell_price = base_price * st
                stop_price = max(base_price - atr * sm, 0)

                logging.info(
                    f"[{bot_name}][{sym}] Base:${base_price:.2f}, Curr:${price:.2f}, Buy@${buy_price:.2f}, Sell@${sell_price:.2f}, Stop@${stop_price:.2f}, Owned={qty:.4f}"
                )

                if qty == 0 and price <= buy_price and not is_lunch_time():
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

    except Exception as e:
        logging.error(f"Main loop error: {e}")
        time.sleep(60)
