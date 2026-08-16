# Izalith Trading System

A modular trading system with 4 operation modes that can be activated independently.

## Modules

| Module | Description | Risk Level |
|--------|-------------|------------|
| **SIGNALS** | Alerts to Telegram, you decide | Zero |
| **DCA** | Automatic periodic purchases | Low |
| **GRID** | Grid trading on stablecoins | Medium |
| **SCALPER** | Active multi-asset trading | High |

## Supported Markets

- **Crypto** (Binance via ccxt): BTC/USDT, ETH/USDT, SOL/USDT, DOGE/USDT, USDT/USDC
- **Forex** (OANDA API v3): EUR_USD, GBP_USD
- **Stocks/ETFs** (Alpaca): VTI, VOO, SPY

## Quick Start

```bash
# 1. Clone and navigate
cd izalith-trader

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment template
cp .env.example .env

# 4. Edit .env with your API keys (optional - works without for signals/dashboard)

# 5. Run
python run.py                          # Run modules from .env
python run.py --modules signals        # Run only signals
python run.py --modules dashboard      # Run only dashboard
python run.py --modules signals,dashboard  # Run both
python run.py --list-modules           # List available modules
```

## Configuration (.env)

Key settings:

```bash
# IMPORTANT - Always start with these ON
DRY_RUN=true
BINANCE_TESTNET=true
OANDA_PRACTICE=true
ALPACA_PAPER=true

# Modules to activate
ACTIVE_MODULES=signals,dashboard

# Optional: Add API keys for real data
BINANCE_API_KEY=your_key
BINANCE_SECRET=your_secret
```

## Dashboard

Access at: http://localhost:8080

Features:
- Live price updates (WebSocket)
- Open positions tracking
- Recent signals display
- Portfolio value chart (24h)
- Market status indicators

## Module Details

### Signals Module
Monitors markets and detects:
- RSI oversold/overbought
- MACD crossovers
- Bollinger Band touches
- Volume spikes
- Price breakouts

Sends alerts to:
- Telegram (configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`)
- Discord webhook (configure `DISCORD_WEBHOOK_URL`)

### DCA Module
Dollar Cost Averaging with configurable:
- Assets to buy
- Purchase frequency (hourly, daily, weekly, monthly)
- Amount per purchase

### Grid Module
Grid trading for range-bound markets:
- Configurable price range
- Number of grid levels
- Automatic order placement

### Scalper Module
Active trading with strategies:
- Momentum (EMA crossovers + volume)
- Mean Reversion (Bollinger Bands)
- Breakout (24h high/low)

Built-in risk management:
- Max position size
- Daily trade limits
- Daily loss limits
- Stop loss / Take profit

## Project Structure

```
izalith-trader/
├── config/           # Settings and asset definitions
├── core/             # Shared infrastructure
│   ├── adapters/     # Exchange connections
│   ├── data/         # Indicators and unified feed
│   ├── portfolio/    # Position tracking
│   └── utils/        # Database and logging
├── modules/          # Trading modules
│   ├── signals/      # Alert system
│   ├── dca/          # Dollar cost averaging
│   ├── grid/         # Grid trading
│   ├── scalper/      # Active trading
│   └── dashboard/    # Web interface
├── data/             # SQLite database
├── logs/             # Log files
└── run.py            # Main entry point
```

## Safety Notes

1. **DRY_RUN=true by default** - No real trades without explicit change
2. **Testnet/Paper by default** - All exchanges use test environments
3. **Never commit .env** - API keys must stay private
4. **Start with signals only** - Observe before automating

## Testing

The project includes a comprehensive test suite:

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_indicators.py -v   # Technical indicators
python -m pytest tests/test_adapters.py -v     # Market adapters
python -m pytest tests/test_signals.py -v      # Signal detection
```

**Test Coverage:**
- 45 tests covering indicators, adapters, and signal detection
- Tests run in isolation using mocks and simulation mode
- No API keys required for testing

## API Endpoints (Dashboard)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | System status and active modules |
| `/api/prices` | GET | Current prices for all assets |
| `/api/positions` | GET | Open positions |
| `/api/trades` | GET | Recent trade history |
| `/api/signals` | GET | Recent detected signals |
| `/ws/prices` | WS | Real-time price updates |

## Technical Indicators

Calculated automatically on OHLCV data:

| Indicator | Period | Description |
|-----------|--------|-------------|
| SMA | 20 | Simple Moving Average |
| EMA | 5, 20 | Exponential Moving Average |
| RSI | 14 | Relative Strength Index (0-100) |
| MACD | 12/26/9 | Moving Average Convergence Divergence |
| Bollinger Bands | 20, 2std | Upper, Middle, Lower bands |
| ATR | 14 | Average True Range |
| Volume SMA | 20 | Volume moving average for spike detection |

## Simulation Mode

When API keys are not configured or connection fails, adapters automatically switch to simulation mode:

- Generates realistic random-walk prices
- Maintains price continuity across calls
- Simulates order execution with proper fills
- Useful for testing and development

## Troubleshooting

**NumPy Warning**: If you see a NumPy 1.x vs 2.x warning, you can ignore it or run:
```bash
pip install 'numpy<2'
```

**Connection Errors**: The system automatically falls back to simulation mode when exchanges are unreachable.

**Empty Signals**: If no signals appear, the market may be in a stable state. Signals only trigger on specific conditions.

## License

For Izalith Consulting internal use.
