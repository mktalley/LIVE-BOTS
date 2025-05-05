#!/usr/bin/env python3
import os
import time
import json
import requests
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
import pytz

# === CONFIGURATION ===
BASE_URL    = "https://api.alpaca.markets"
API_KEY     = "AK8XASA88WMTSRKFMRN3"
API_SECRET  = "96cnaUhRf4OaGM98QbDZJbCLCuWRmwAKEvreIzEu"
HEADERS     = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json"
}

EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_ADDRESS = "mktalley@gmail.com"
EMAIL_PASSWORD = "eooncglziamrtcyw"
TO_EMAIL = "mktalley@icloud.com"

symbols = [
    "UNI/USD","LTC/USD","LINK/USD","DOGE/USD","YFI/USD",
    "BTC/USD","BCH/USD","CRV/USD","GRT/USD","TRUMP/USD",
    "BAT/USD","AAVE/USD","XRP/USD","ETH/USD","DOT/USD",
    "SUSHI/USD","SOL/USD","USDT/USD","SHIB/USD","MKR/USD",
    "PEPE/USD","AVAX/USD","USDC/USD","XTZ/USD"
]

BUY_DIP      = -1.5
SELL_RISE    =  2.0
STOP_LOSS    = -3.0
NOTIONAL_USD =  10.0
DATA_FILE    = "baseline_data.json"

baseline_prices     = {}
baseline_timestamps = {}
pnl_by_symbol       = {}
overall_pnl         = 0.0
last_email_sent_date = None

def get_timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def load_state():
    global baseline_prices, baseline_timestamps, pnl_by_symbol, overall_pnl
    try:
        with open(DATA_FILE) as f:
            d = json.load(f)
        baseline_prices     = {k: float(v) for k,v in d.get("prices",{}).items()}
        baseline_timestamps = {k: float(v) for k,v in d.get("timestamps",{}).items()}
        pnl_by_symbol       = {k: float(v) for k,v in d.get("pnl_by_symbol",{}).items()}
        overall_pnl         = float(d.get("overall_pnl", 0.0))
        print(f"{get_timestamp()} Loaded state from {DATA_FILE}")
    except FileNotFoundError:
        print(f"{get_timestamp()} No saved state, starting fresh.")
    except Exception as e:
        print(f"{get_timestamp()} Error loading state: {e}")

def save_state():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump({
                "prices":     baseline_prices,
                "timestamps": baseline_timestamps,
                "pnl_by_symbol": pnl_by_symbol,
                "overall_pnl": overall_pnl
            }, f)
        print(f"{get_timestamp()} State saved.")
    except Exception as e:
        print(f"{get_timestamp()} Error saving state: {e}")

def get_market_price(symbol):
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/orderbooks"
    r = requests.get(url, headers=HEADERS, params={"symbols": symbol})
    r.raise_for_status()
    ob = r.json()["orderbooks"][symbol]
    price = (ob["b"][0]["p"] + ob["a"][0]["p"]) / 2.0
    return price

def place_order(symbol, side, order_type, value):
    payload = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "gtc"}
    if order_type=="notional": payload["notional"] = str(value)
    else:                      payload["qty"]     = str(value)
    r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload)
    ts = get_timestamp()
    if r.status_code==200:
        print(f"{ts} [{symbol}] ✅ {side.upper()} {order_type} {value}")
    else:
        print(f"{ts} [{symbol}] ❌ {r.status_code} {r.text}")
    return r.json()

def get_position(symbol):
    sym = symbol.replace("/","")
    r = requests.get(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS)
    if r.status_code==200:
        d = r.json()
        return float(d["qty"]), float(d["avg_entry_price"])
    return 0.0, None

def send_daily_email():
    global last_email_sent_date

    today = datetime.now(pytz.timezone("US/Pacific")).date()
    if last_email_sent_date == today:
        return

    body = f"📈 Daily Trade Summary – {today.strftime('%Y-%m-%d')}\n\n"
    for symbol, pnl in pnl_by_symbol.items():
        body += f"{symbol}: {pnl:+.2f} USD\n"
    body += f"\nTotal P&L: {overall_pnl:+.2f} USD"

    msg = MIMEText(body)
    msg["Subject"] = f"Daily Crypto P&L Report – {today.strftime('%Y-%m-%d')}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"{get_timestamp()} ✅ Email sent")
        last_email_sent_date = today
    except Exception as e:
        print(f"{get_timestamp()} ❌ Failed to send email: {e}")

def main():
    global overall_pnl

    load_state()
    for s in symbols:
        pnl_by_symbol.setdefault(s, 0.0)

    for s in symbols:
        try:
            if s not in baseline_prices:
                qty, avg = get_position(s)
                baseline_prices[s] = avg if qty > 0 and avg else get_market_price(s)
                baseline_timestamps[s] = time.time()
                print(f"{get_timestamp()} [{s}] Baseline → {baseline_prices[s]:.8f}")
            else:
                print(f"{get_timestamp()} [{s}] Using saved baseline {baseline_prices[s]:.8f}")
        except Exception as e:
            print(f"{get_timestamp()} [{s}] Err init baseline: {e}")

    save_state()

    while True:
        for s in symbols:
            try:
                price = get_market_price(s)
                ts    = get_timestamp()
                last  = baseline_prices.get(s, price)
                if last == 0:
                    last = price
                    baseline_timestamps[s] = time.time()

                pct = (price - last)/last*100
                qty, _ = get_position(s)

                print(f"{ts} [{s}] ${price:.4f} ({pct:+.2f}%)  Pos={qty}")

                if qty==0 and time.time()-baseline_timestamps[s] > 6*3600:
                    baseline_prices[s]=price
                    baseline_timestamps[s]=time.time()
                    print(f"{ts} [{s}] Baseline refreshed to {price:.8f}")

                if qty==0 and pct <= BUY_DIP:
                    print(f"{ts} [{s}] Dip {BUY_DIP}% hit → BUY ${NOTIONAL_USD}")
                    place_order(s, "buy", "notional", NOTIONAL_USD)
                    baseline_prices[s]=price; baseline_timestamps[s]=time.time()

                elif qty>0 and pct <= STOP_LOSS:
                    trade_pnl = (price - last)*qty
                    pnl_by_symbol[s] += trade_pnl; overall_pnl += trade_pnl
                    print(f"{ts} [{s}] Stop {STOP_LOSS}% → SELL qty {qty}")
                    place_order(s, "sell", "qty", qty)
                    print(f"{ts}] Trade PnL={trade_pnl:.4f}  Tot PnL={overall_pnl:.4f}")
                    baseline_prices[s]=price; baseline_timestamps[s]=time.time()

                elif qty>0 and pct >= SELL_RISE:
                    trade_pnl = (price - last)*qty
                    pnl_by_symbol[s] += trade_pnl; overall_pnl += trade_pnl
                    print(f"{ts} [{s}] Rise {SELL_RISE}% → SELL qty {qty}")
                    place_order(s, "sell", "qty", qty)
                    print(f"{ts}] Trade PnL={trade_pnl:.4f}  Tot PnL={overall_pnl:.4f}")
                    baseline_prices[s]=price; baseline_timestamps[s]=time.time()

            except Exception as e:
                print(f"{get_timestamp()} [{s}] Err during monitor: {e}")

        now_pacific = datetime.now(pytz.timezone("US/Pacific"))
        if now_pacific.hour == 22 and now_pacific.minute < 5:
            send_daily_email()

        print(f"{get_timestamp()} Overall PnL: {overall_pnl:.4f}")
        save_state()
        time.sleep(60)

if __name__ == "__main__":
    main()