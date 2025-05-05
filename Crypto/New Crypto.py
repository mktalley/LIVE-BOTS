#!/usr/bin/env python3
import time
import json
import requests
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
import pytz
from collections import deque
import os
from pathlib import Path

# Load environment variables from .env file
# The .env file should be located at the repository root
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key, value)


# CONFIGURATION
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")  # can override via env
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
if not API_KEY or not API_SECRET:
    raise RuntimeError("Missing Alpaca API credentials: APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set in .env")
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type": "application/json"
}

# EMAIL SETTINGS
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, TO_EMAIL]):
    raise RuntimeError("Missing email credentials: EMAIL_ADDRESS, EMAIL_PASSWORD, TO_EMAIL")

# STRATEGY PARAMETERS
TOP_N_SYMBOLS = 5   # select top N cryptos by 24h volume

# Dynamic symbol universe (top N by 24h volume)
def fetch_top_symbols(n):
    r = requests.get("https://data.alpaca.markets/v1beta2/crypto/us/tickers", headers=HEADERS)
    r.raise_for_status()
    tickers = r.json().get("tickers", [])
    sorted_by_vol = sorted(tickers, key=lambda t: t.get("day", {}).get("v", 0), reverse=True)
    syms = []
    for t in sorted_by_vol[:n]:
        s = t.get("symbol", "")
        if s.endswith("USD"):
            syms.append(f"{s[:-3]}/USD")
        else:
            syms.append(s)
    return syms

try:
    SYMBOLS = fetch_top_symbols(TOP_N_SYMBOLS)
except Exception:
    # fallback to manual list if fetch fails
    SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "AVAX/USD"]
SHORT_EMA_PERIOD = 15   # minutes
LONG_EMA_PERIOD = 60    # minutes
ATR_PERIOD = 14         # lookback for ATR calculation
STOP_ATR_MULT = 1.0     # exit on -1x ATR
TAKE_ATR_MULT = 2.0     # exit on +2x ATR
BUY_DIP = -1.0          # % below short EMA to buy
MIN_DEPTH_USD = 20.0    # min USD at bid for liquidity
MIN_DAILY_VOL = 100.0   # min 24h volume
RISK_PER_TRADE_USD = 10.0 # USD risk per trade
NOTIONAL_USD = 10.0     # fallback notional

# Derived constants
ALPHA_SHORT = 2 / (SHORT_EMA_PERIOD + 1)
ALPHA_LONG = 2 / (LONG_EMA_PERIOD + 1)

# STATE
STATE_FILE = "state.json"
state = {
    "pnl":        {s: 0.0 for s in SYMBOLS},
    "overall_pnl": 0.0,
    "ema_short":  {s: None for s in SYMBOLS},
    "ema_long":   {s: None for s in SYMBOLS},
    "entry_price":{s: None for s in SYMBOLS},
    "last_email": None
}
history = {s: deque(maxlen=ATR_PERIOD+1) for s in SYMBOLS}


def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_orderbook(sym):
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/orderbooks"
    r = requests.get(url, headers=HEADERS, params={"symbols": sym})
    r.raise_for_status()
    ob = r.json()["orderbooks"][sym]
    price = (ob["b"][0]["p"] + ob["a"][0]["p"]) / 2.0
    depth = ob["b"][0]["s"] * price
    return price, depth


def get_daily_volume(sym):
    # Alpaca data API expects symbols without slash, e.g., 'BTCUSD'
    ticker = sym.replace("/", "")
    url = "https://data.alpaca.markets/v1beta2/crypto/us/bars"
    params = {"symbols": ticker, "timeframe": "1Day", "limit": 1}
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    data = r.json().get("bars", {})
    bars = data.get(ticker, [])
    return float(bars[0].get("v", 0.0)) if bars else 0.0


def get_position(sym):
    r = requests.get(f"{BASE_URL}/v2/positions/{sym.replace('/','')}", headers=HEADERS)
    if r.status_code == 200:
        d = r.json()
        return float(d.get("qty", 0.0)), float(d.get("avg_entry_price", 0.0))
    return 0.0, None


def place_order(sym, side, otype, val):
    payload = {"symbol": sym, "side": side, "type": "market", "time_in_force": "gtc"}
    if otype == "notional": payload["notional"] = str(val)
    else: payload["qty"] = str(val)
    r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload)
    ts = now_ts()
    if r.status_code in (200,201): print(f"{ts} ORDER OK: {sym} {side} {otype} {val}")
    else: print(f"{ts} ORDER FAIL: {sym} {side} {otype} {val} -> {r.status_code}")
    return r


def send_daily_email():
    today = datetime.now(pytz.timezone("US/Pacific")).date()
    if state["last_email"] == str(today): return
    body = f"📈 Daily Crypto P&L – {today}\n\n"
    for s in SYMBOLS: body += f"{s}: {state['pnl'][s]:+8.2f} USD\n"
    body += f"\nOverall P&L: {state['overall_pnl']:+8.2f} USD"
    msg = MIMEText(body); msg["Subject"] = f"Crypto P&L {today}"; msg["From"] = EMAIL_ADDRESS; msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as srv:
            srv.starttls(); srv.login(EMAIL_ADDRESS, EMAIL_PASSWORD); srv.sendmail(EMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"{now_ts()} Email sent to {TO_EMAIL}"); state["last_email"] = str(today)
    except Exception as e:
        print(f"{now_ts()} Email failed: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f: data = json.load(f); state.update(data); print(f"{now_ts()} Loaded state")
    except FileNotFoundError:
        print(f"{now_ts()} No state file—fresh start")
    except Exception as e:
        print(f"{now_ts()} Load error: {e}")
    for s in SYMBOLS:
        state['pnl'].setdefault(s, 0.0)
        state['ema_short'].setdefault(s, None)
        state['ema_long'].setdefault(s, None)
        state['entry_price'].setdefault(s, None)


def save_state():
    try:
        with open(STATE_FILE,'w') as f: json.dump(state, f)
        print(f"{now_ts()} State saved")
    except Exception as e:
        print(f"{now_ts()} Save error: {e}")


def main():
    load_state()
    for s in SYMBOLS:
        if state['ema_short'][s] is None:
            p,_ = get_orderbook(s); state['ema_short'][s] = p; state['ema_long'][s] = p; history[s].extend([p]*(ATR_PERIOD+1)); print(f"{now_ts()} Init {s}: EMA={p:.2f}")
    save_state()
    try:
        while True:
            for s in SYMBOLS:
                try:
                    price,depth = get_orderbook(s)
                    ps,pl = state['ema_short'][s], state['ema_long'][s]
                    state['ema_short'][s] = ALPHA_SHORT*price + (1-ALPHA_SHORT)*ps
                    state['ema_long'][s]  = ALPHA_LONG*price  + (1-ALPHA_LONG)*pl
                    history[s].append(price)
                    atr = sum(abs(history[s][i] - history[s][i-1]) for i in range(1, len(history[s]))) / ATR_PERIOD if len(history[s])==ATR_PERIOD+1 else 0.0
                    vol24 = get_daily_volume(s)
                    qty,avg = get_position(s)
                    if qty>0 and state['entry_price'][s] is None: state['entry_price'][s] = avg
                    if qty==0 and price>state['ema_long'][s]:
                        pct = (price - state['ema_short'][s]) / state['ema_short'][s] * 100
                        if pct <= BUY_DIP and depth>=MIN_DEPTH_USD and vol24>=MIN_DAILY_VOL:
                            size = (RISK_PER_TRADE_USD/atr) if atr>0 else (NOTIONAL_USD/price)
                            size = round(size,6)
                            print(f"{now_ts()} {s} dip {pct:.2f}% ATR={atr:.2f} VOL={vol24:.2f} -> BUY qty={size}")
                            place_order(s,'buy','qty',size); state['entry_price'][s] = price
                    elif qty>0:
                        entry = state['entry_price'][s]; delta = price - entry
                        if atr>0 and (delta<= -STOP_ATR_MULT*atr or delta>= TAKE_ATR_MULT*atr):
                            tag = 'STOP' if delta<= -STOP_ATR_MULT*atr else 'TAKE'
                            print(f"{now_ts()} {s} {tag} {delta:.2f}% -> SELL qty={qty}")
                            place_order(s,'sell','qty',qty)
                            pnl = delta*qty; state['pnl'][s]+=pnl; state['overall_pnl']+=pnl; state['entry_price'][s]=None
                    print(f"{now_ts()} {s} Price={price:.2f} EMA15={ps:.2f} EMA60={pl:.2f} ATR={atr:.2f} VOL={vol24:.2f} Pos={qty}")
                except Exception as e:
                    print(f"{now_ts()} {s} error: {e}")
            send_daily_email(); print(f"{now_ts()} Overall P&L: {state['overall_pnl']:+.2f} USD"); save_state()
            time.sleep(60)
    except KeyboardInterrupt:
        print("Interrupted, saving state..."); save_state()

if __name__ == "__main__":
    main()
