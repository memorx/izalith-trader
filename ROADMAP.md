# Roadmap

Work I plan to do, roughly in order. Estimates are hours of hands-on work.

## 1. Finish the path to real money (~8 h)

- [ ] Test the IBKR adapter against a paper account (IB Gateway, port 4002). Today it's only tested
      against fakes.
- [ ] Place one small real order on Kraken end to end: limit order, partial fill, fallback to market
      after 15 minutes, and the trade recorded once.
- [ ] Shutdown: wrap `system.stop()` in `try/finally` so a second Ctrl+C can't leave it half done,
      and exit with code 1 on a fatal error or a missing live key.

## 2. Perpetual futures adapter (~6 h)

- [ ] Adapter for Binance USD-M and Hyperliquid perpetuals. Without it, the funding carry and the
      strategies with shorts can only run in the lab.
- [ ] Margin checks and liquidation distance in the risk guard.

## 3. Lab, round 3 (~6 h)

- [ ] Net opposite orders between strategies that share an exchange, to pay one fee instead of two.
- [ ] Monthly contributions that also rebalance, instead of buying in fixed proportions.
- [ ] Hour-of-day study of the Kraken spread from the collected history, to schedule orders when
      it's cheapest.

## 4. Use the collected data (~5 h)

- [ ] Backtests for the markets under research (funding spreads, the peso premium, local crypto
      dollars) once the collectors have a few weeks of hourly history.
- [ ] Compare a month of paper trading with the lab's expectations on the dashboard and flag any
      strategy whose live results drift outside its backtested range.

## 5. Quality (~3 h)

- [ ] CI on GitHub Actions running the pytest suite.
- [ ] English translation of the README and the strategy docs.

Total: about 28 hours.
