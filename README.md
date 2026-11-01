# Izalith Trader

A personal trading system with one rule: **no strategy touches money until it passes an honest
backtest and then a month of paper trading.** It has two halves that share the same code:

- **The lab** (`izalith-trader/lab/`) backtests 52 strategies, from a 60/40 portfolio to crypto
  trend following and funding carry, with real fees, slippage and funding costs. Each one goes
  through six robustness checks plus guards for short histories, too few trades and known biases.
  Strategies invented here also have to beat a deflated Sharpe test (Bailey and López de Prado,
  2014), so a lucky backtest can't pass as an edge.
- **The live bot** (`izalith-trader/run.py`) runs on paper by default and trades only what passed the
  lab, with the same signal code as the backtest. It has a risk guard (max drawdown brake, daily
  limits, idempotent order ids), a daily Telegram report, a healthchecks.io heartbeat and a local
  dashboard.

It also has collectors for markets few people watch: funding rates across Binance, Bybit and
Hyperliquid; the peso premium on Bitso; and the crypto dollar and BTC price in 16 countries
(Argentina, Venezuela, India, Turkey, Nigeria...). There's also a study of how well Polymarket
prices predict outcomes.

I wrote it for my own small account. It's not financial advice, and nothing here promises returns.

## Stack

Python 3.11 with asyncio · ccxt (crypto exchanges) · alpaca-py and IBKR (stocks) · yfinance ·
pandas · SQLite (aiosqlite) · FastAPI plus a plain HTML/JS dashboard · pytest (132 tests).

## Run it

```bash
cd izalith-trader
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env                        # paper mode needs no API keys

python -m lab.review                        # backtest every strategy (downloads data the first time)
python run.py --modules allocator,collectors,signals,dashboard
# dashboard at http://127.0.0.1:8765
```

With no keys it runs on paper with public exchange prices and Yahoo quotes. Live trading needs two
explicit switches in `.env` and trade-only API keys; see `izalith-trader/docs/LLAVES.md`.

## Where things are

| Path | What |
|---|---|
| `izalith-trader/lab/` | backtest engine, strategy catalog, robustness review, small-account and Polymarket studies |
| `izalith-trader/modules/` | live modules: allocator, collectors, reports, dashboard, signals, DCA, grid, scalper (paper only) |
| `izalith-trader/core/` | exchange and broker adapters, portfolio, execution, risk guard |
| `izalith-trader/tests/` | pytest suite |
| `izalith-trader/docs/` | strategy results and ideas |
| `BITACORA.md` | development log (in Spanish), with the reasoning behind each decision |

The detailed README and the docs are in Spanish:
[izalith-trader/README.md](izalith-trader/README.md).

## Roadmap

[ROADMAP.md](ROADMAP.md)

## License

[MIT](LICENSE)
