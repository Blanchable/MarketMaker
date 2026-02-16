# Kalshi BTC Hybrid Bot (Market-Make + Sniper)

A production-ready Python bot that trades Bitcoin binary markets on [Kalshi](https://kalshi.com) using a hybrid strategy:

1. **Market-Make** (provide two-sided liquidity) when realized volatility is calm.
2. **Snipe** (take mispriced quotes via IOC) when the edge is large.

Supports **demo mode**, **paper trading**, **live trading**, a **kill switch**, full **logging**, **backtesting** on recorded data, and a **PySide6 desktop GUI** with one-click start/stop.

---

## Safety Warnings

- **This bot trades real money when live mode is enabled.** Start with demo paper trading.
- The **kill switch** automatically cancels all orders and halts trading if daily realized PnL breaches your stop-loss.
- Never commit your `.env` file or private key to version control.
- Default risk parameters are aggressive. Tune them to your comfort level **before** enabling live trading.
- Always test in demo + paper mode first.

---

## Project Structure

```
kalshi_btc_bot/
  pyproject.toml          # Dependencies and project metadata
  README.md               # This file
  .env.example            # Template for environment variables
  .gitignore
  build_exe.py            # PyInstaller build script for Windows .exe
  src/
    app_gui/                 # <-- Desktop GUI (PySide6)
      __init__.py
      main.py              # GUI entry point
      widgets.py           # MainWindow, ControlPanel, StatusPanel, StatusCards
      process_runner.py    # Subprocess management with Qt signals
      log_tail.py          # Color-coded scrollable log viewer
      config_store.py      # %APPDATA%/KalshiBot/config.json persistence

    bot/
      __init__.py
      config.py            # Pydantic BotConfig – reads all env vars
      main.py              # Async entry point
      cli.py               # Typer CLI commands (with --config support)

      infra/
        log.py             # Rich + rotating file logging
        time.py            # Time helpers (epoch ms, UTC, seconds_until)
        storage.py         # SQLite storage layer
        metrics.py         # In-memory counters and gauges

      kalshi/
        auth.py            # RSA-PSS signature generation
        rest.py            # Async httpx REST client with retry
        ws.py              # WebSocket client (ticker, orderbook, fill)
        models.py          # Pydantic models for API objects
        market_discovery.py # BTC market scanning + strike extraction
        order_manager.py   # Order lifecycle (place, cancel, replace)
        portfolio.py       # Portfolio snapshot (balance, positions, fills)

      pricing/
        btc_feed.py        # Public BTC spot price feed (Coinbase/CoinGecko)
        vol.py             # EWMA realized volatility estimator
        digital_prob.py    # Black-Scholes digital call probability model
        fair_value.py      # Combines spot + vol + model into fair cents

      strategy/
        filters.py         # Pre-trade market/data-quality filters
        market_maker.py    # Two-sided quoting with inventory skew
        sniper.py          # Edge detection and IOC execution
        hybrid.py          # Orchestrator: MM when calm, snipe when edge
        risk.py            # Kill switch, exposure caps, PnL tracking

      sim/
        paper_broker.py    # Simulated order/fill engine
        fill_sim.py        # Fill probability and fee simulation
        backtest.py        # Replay recorded ticks through strategy
        recorder.py        # Write ticks/orders/fills/PnL to SQLite

  tests/
    test_auth.py           # Signature generation and verification
    test_prob.py           # Probability model monotonicity and bounds
    test_risk.py           # Risk engine limits and kill switch
    test_strategy.py       # Quote generation and strike parsing
    test_config_store.py   # GUI config persistence
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Kalshi account with API access
- An RSA private key for API authentication

### 2. Get Kalshi API Keys

1. Log in to [Kalshi](https://kalshi.com) (or the demo site).
2. Go to **Settings > API Keys**.
3. Generate a new API key. You will receive:
   - A **Key ID** (e.g., `abc123-def456-...`)
   - An **RSA private key** file (`.pem` or `.key`)
4. Save the private key file securely. **Never commit it to git.**

### 3. Install

```bash
cd kalshi_btc_bot
pip install -e ".[dev]"
```

### 4. Configure Environment

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```
ENVIRONMENT=demo
KALSHI_KEY_ID=your-actual-key-id
KALSHI_PRIVATE_KEY_PATH=./path/to/your/kalshi.key
LIVE_TRADING=false
```

Store your private key file at the path you specified. Make sure it is **not** tracked by git (it is in `.gitignore`).

---

## Desktop GUI

The bot includes a PySide6 desktop application for point-and-click operation.

### Launch the GUI

```bash
kalshi-gui
```

Or run directly:

```bash
python src/app_gui/main.py
```

### GUI Features

- **Left panel (Controls)**:
  - Environment dropdown (Demo / Prod)
  - Mode dropdown (Paper / Live)
  - Strategy toggles (Market Making, Sniper)
  - Risk settings (Daily Stop, Max Exposure, etc.)
  - Credentials (Key ID, Private Key file picker)
  - Start Bot / Stop Bot / Cancel All Orders / Open Logs Folder buttons
  - Export Config / Import Config buttons

- **Right panel (Status + Logs)**:
  - Live status cards: Connection, BTC Spot, Realized Vol, PnL, Exposure, Kill Switch
  - Scrollable log viewer with color-coded lines (ERROR=red, WARN=orange, SNIPER=green, FILL=blue)
  - Clear View button

- **Safety**: If you select Prod + Live, a confirmation dialog requires typing "LIVE" to proceed.

### How the GUI Works

The GUI does **not** run trading logic directly. It:
1. Saves your settings to `%APPDATA%/KalshiBot/config.json` (Windows) or `~/.kalshi_bot/config.json` (Linux/Mac).
2. Launches the bot CLI as a subprocess: `python -m bot.cli run --config <path>`.
3. Captures stdout/stderr in real time, parsing `STATUS_JSON:` lines to update the status cards.
4. On Stop: sends `CTRL_BREAK_EVENT` (Windows) or `SIGTERM`, waits 5 seconds, then kills if needed. Runs `cancel-all` after stop if in live mode.

### Config Persistence

All settings are stored at:
- **Windows**: `%APPDATA%\KalshiBot\config.json`
- **Linux/Mac**: `~/.kalshi_bot/config.json`

Only the file **path** to the private key is stored, never its contents. Use Export/Import to back up or share settings.

---

## Building a Windows .exe

### One-file executable (smaller, slower startup)

```bash
pip install pyinstaller
python build_exe.py
```

Output: `dist/KalshiBtcBot.exe`

### One-directory executable (larger, faster startup)

```bash
python build_exe.py --onedir
```

Output: `dist/KalshiBtcBot/KalshiBtcBot.exe`

### What the .exe bundles

- All Python dependencies including PySide6
- The bot trading engine (`src/bot/`)
- The GUI application (`src/app_gui/`)

On first run, the .exe creates `%APPDATA%\KalshiBot\` for config and logs.

---

## CLI Usage

The bot can also be run entirely from the command line.

### Run in Demo Paper Mode (safest)

```bash
bot run --env demo --paper
```

### Run in Demo Live Mode

```bash
bot run --env demo --no-paper
```

### Run in Production Live Mode

```bash
bot run --env prod --no-paper --live
```

**This places real orders with real money.**

### Using a JSON config file

```bash
bot run --config path/to/config.json
```

The `--config` flag is supported by `run`, `cancel-all`, and `status` commands.

### Cancel All Orders

```bash
bot cancel-all --env demo
```

### Check Status

```bash
bot status --env demo
```

### Run Backtest

```bash
bot backtest path/to/bot_data.db
```

---

## Kill Switch

The kill switch is **always on** and cannot be disabled. It triggers when:

- **Daily realized PnL** drops below `-DAILY_STOP_DOLLARS` (default: -$200)
- **WebSocket disconnects** or data becomes stale (> `STALE_MS`)
- **Spot price feed** is unavailable or stale

When triggered:
1. All resting orders are cancelled immediately.
2. No new orders are placed.
3. The bot logs a `CRITICAL` message.
4. In the GUI, the Kill Switch card turns red.
5. Manual restart is required after reviewing what happened.

---

## Configuration Reference

All settings are controlled via environment variables (or `.env` file). See `.env.example` for the full list.

### Key Risk Parameters

| Variable | Default | Description |
|---|---|---|
| `DAILY_STOP_DOLLARS` | 200 | Kill switch threshold (daily loss) |
| `MAX_GROSS_EXPOSURE_DOLLARS` | 250 | Max total exposure across all markets |
| `MAX_NET_EXPOSURE_DOLLARS` | 150 | Max directional exposure |
| `MAX_EXPOSURE_PER_MARKET_DOLLARS` | 125 | Max exposure in a single market |
| `MAX_ORDER_SIZE_CONTRACTS` | 25 | Max contracts per order |
| `NO_TRADE_WINDOW_SECONDS` | 300 | Stop quoting this close to expiry |

### Market-Making Parameters

| Variable | Default | Description |
|---|---|---|
| `MM_ENABLED` | true | Enable market-making |
| `MM_QUOTE_SIZE_CONTRACTS` | 10 | Contracts per quote side |
| `MM_EDGE_CENTS` | 1 | Target edge per round trip |
| `MM_INVENTORY_SKEW` | 0.25 | How aggressively to skew quotes |
| `MM_CANCEL_REQUOTE_MS` | 800 | Max age before re-quoting |
| `MM_ONLY_WHEN_VOL_BELOW` | 0.55 | Disable MM above this annualized vol |

### Sniper Parameters

| Variable | Default | Description |
|---|---|---|
| `SNIPER_ENABLED` | true | Enable sniping |
| `SNIPER_MIN_EDGE_CENTS` | 6 | Minimum edge (after fees) to fire |
| `SNIPER_MAX_SLIPPAGE_CENTS` | 2 | Max slippage tolerance |
| `SNIPER_COOLDOWN_SECONDS` | 10 | Cooldown per market after a shot |

---

## Logs and SQLite Database

### Logs

Logs are written to:
- **Console**: Rich-formatted stderr output
- **File**: Rotating logs at `%APPDATA%\KalshiBot\logs\bot.log` (Windows) or `~/.kalshi_bot/logs/bot.log` (Linux/Mac)
- **stdout**: Plain-text lines (captured by the GUI process runner)

The GUI "Open Logs Folder" button opens the logs directory in your file explorer.

Key log messages:
- `KILL SWITCH TRIGGERED` -- critical, all orders cancelled
- `SNIPER buy_yes ...` -- a sniper trade was fired
- `PAPER FILL` -- a paper trade was simulated
- `WS disconnected` -- WebSocket lost connection

### SQLite Database

When running (especially in paper mode), the bot records everything to `bot_data.db`:

| Table | Contents |
|---|---|
| `ticks` | Market price snapshots (yes_bid, yes_ask, volume) |
| `markets` | Discovered market metadata (ticker, strike, expiration) |
| `orders` | Every order submitted (price, size, status) |
| `fills` | Every fill (price, size, fees) |
| `pnl` | Periodic PnL snapshots |

You can query this database with any SQLite tool:

```bash
sqlite3 bot_data.db "SELECT * FROM fills ORDER BY ts_ms DESC LIMIT 20;"
```

---

## How It Works

### Hybrid Strategy

1. **Market Discovery**: Scans Kalshi for open BTC binary markets. Extracts strikes from titles (e.g., "Bitcoin above $50,000"). Filters by spread, volume, and time to expiry.

2. **Pricing**: Fetches BTC spot from Coinbase. Computes EWMA realized volatility. Uses a Black-Scholes digital call model to estimate fair value for each market.

3. **When vol is calm** (`vol < MM_ONLY_WHEN_VOL_BELOW`):
   - Market-maker provides two-sided quotes around fair value.
   - Quotes are skewed based on current inventory (long inventory = lower quotes to sell).
   - Uses `post_only` orders to guarantee maker fees.

4. **When edge is detected**:
   - Sniper fires IOC orders when market price deviates from fair value by more than `SNIPER_MIN_EDGE_CENTS` + estimated fees.
   - In high-vol mode, sniper requires 2x normal edge.

5. **Risk engine** continuously monitors exposure and enforces hard limits.

### STATUS_JSON Protocol

The bot emits a machine-readable status line to stdout every 2 seconds:

```
STATUS_JSON: {"connected":true,"spot":50234.12,"vol":0.47,"pnl_realized_today":-12.35,...}
```

The GUI parses these lines to update the status cards in real time. Fields:

| Field | Type | Description |
|---|---|---|
| `connected` | bool | WebSocket connection status |
| `spot` | float | Current BTC spot price |
| `vol` | float | Annualized realized volatility |
| `pnl_realized_today` | float | Realized PnL in dollars |
| `pnl_unrealized` | float | Unrealized PnL in dollars |
| `gross_exposure` | float | Total exposure in dollars |
| `net_exposure` | float | Net directional exposure |
| `kill_switch` | bool | Whether kill switch is active |
| `markets_quoted` | int | Number of markets being quoted |
| `mode` | string | "HYBRID", "MM", "SNIPER", or "OFF" |

### Probability Model

Uses a simple but effective lognormal digital call approximation:

```
d2 = (ln(S/K) - 0.5 * sigma^2 * t) / (sigma * sqrt(t))
P(above K) = N(d2)
```

Where `N()` is the standard normal CDF, `S` is spot, `K` is strike, `t` is time to expiry in years, and `sigma` is annualized realized vol.

---

## Tests

```bash
python3 -m pytest tests/ -v
```

48 tests covering:
- RSA-PSS signature generation and verification
- Probability model monotonicity (higher spot = higher prob)
- Risk engine caps and kill switch
- Quote generation (bid < ask, within 1..99)
- Strike extraction from market titles
- GUI config store persistence and security

---

## Development Roadmap

1. Scaffold + config + logging -- done
2. Kalshi auth + REST client -- done
3. WebSocket client -- done
4. BTC spot feed + vol -- done
5. Market discovery + strike parsing -- done
6. Risk engine -- done
7. Market maker (paper mode) -- done
8. Sniper (paper mode) -- done
9. Hybrid controller (paper mode) -- done
10. Desktop GUI (PySide6) -- done
11. PyInstaller packaging -- done
12. Demo live mode with tiny size -- ready
13. Production live mode -- ready (use with caution)

---

## License

MIT
