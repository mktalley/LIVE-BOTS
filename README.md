# LIVE-BOTS Repository

## Market Sentinel Bot

A trading bot that monitors market conditions using the Alpaca API, manages baselines, and executes trades based on configurable triggers. It sends end-of-day summaries via email.

### Configuration

The bot is configured entirely via environment variables. Below is a list of supported variables and their defaults:

- **APCA_API_KEY** (required): Alpaca API key.
- **APCA_API_SECRET** (required): Alpaca API secret.
- **APCA_BASE_URL** (default: `https://api.alpaca.markets`): Base URL for the Alpaca API.

**File Paths (relative to `Market Sentinel/` directory)**
- **BOT_A_SYMBOLS_FILE** (default: `botA_symbols.txt`): Symbols file for Bot A.
- **BOT_B_SYMBOLS_FILE** (default: `botB_symbols.txt`): Symbols file for Bot B.
- **BASELINE_FILE** (default: `baselines.json`): JSON file storing baselines.
- **TRADE_LOG_FILE** (default: `trade_log.csv`): CSV log of executed trades.
- **PRICE_HISTORY_FILE** (default: `price_history.csv`): CSV log of price history.
- **LOG_FILE_PATH** (default: `sentinel.log`): Path to bot log file.

**Trading Parameters**
- **BUY_TRIGGER_A** (default: `0.995`)
- **SELL_TRIGGER_A** (default: `1.09`)
- **STOP_MULTIPLIER_A** (default: `0.3`)
- **BUY_TRIGGER_B** (default: `0.98`)
- **SELL_TRIGGER_B** (default: `1.03`)
- **STOP_MULTIPLIER_B** (default: `0.5`)
- **ATR_PERIOD** (default: `14`)
- **RISK_PCT** (default: `0.015`)
- **RESET_HOURS** (default: `6`)
- **BASELINE_DRIFT** (default: `0.05`)
- **VOLATILITY_FILTER** (default: `0.02`)

**Email Settings**
- **EMAIL_ADDRESS** (required): Email address used to send notifications.
- **EMAIL_PASSWORD** (required): Password or app-specific password for the email account.
- **TO_EMAIL** (default: same as `EMAIL_ADDRESS`): Recipient address for summary emails.
- **EMAIL_HOST** (default: `smtp.gmail.com`)
- **EMAIL_PORT** (default: `587`)

**Timezone & Market Hours**
- **ET_TIMEZONE** (default: `US/Eastern`)
- **LUNCH_START_HOUR**, **LUNCH_START_MIN** (default: 11:30 ET)
- **LUNCH_END_HOUR**, **LUNCH_END_MIN** (default: 13:00 ET)
- **MARKET_CLOSE_HOUR**, **MARKET_CLOSE_MIN** (default: 16:00 ET)

### Dependencies

- Python 3.8+
- [alpaca-trade-api](https://pypi.org/project/alpaca-trade-api/)
- [pytz](https://pypi.org/project/pytz/)

Install dependencies:
```bash
pip install alpaca-trade-api pytz
```

### Running the Bot

From the root of the repository:

```bash
export APCA_API_KEY=your_key
export APCA_API_SECRET=your_secret
# ... set other variables as needed
python3 "Market Sentinel/main.py"
```

### Persistence

Market Sentinel persists state across restarts using two JSON files:

- **SMA_STATE_FILE** (default: `sma_state.json`): Stores sliding windows for SMA calculations.
  You can override the file path via the `SMA_STATE_FILE` environment variable.
- **PURCHASE_DATES_FILE** (default: `purchase_dates.json`): Stores purchase dates mapping to enforce one sell per purchase per day.
  You can override the file path via the `PURCHASE_DATES_FILE` environment variable.

#### PURCHASE_DATES_FILE JSON schema

```json
{
  "date": "YYYY-MM-DD",
  "purchase_dates": {
    "SYMBOL1": "YYYY-MM-DD",
    "SYMBOL2": "YYYY-MM-DD"
  }
}
```

On startup, the bot loads the purchase dates file and:
- If the `date` matches today's date (in US/Eastern timezone), loads the `purchase_dates` mapping.
- Otherwise, ignores stale data and starts with an empty mapping.

During operation, purchase dates are flushed to disk immediately after each buy to ensure persistence even if the bot exits unexpectedly.

