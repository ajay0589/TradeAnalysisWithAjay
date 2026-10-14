# MCX Swing Scanner

## Scope and Data

Version `mcx-swing-v1` is a read-only paper scanner for **SILVERM, GOLDM,
NATURALGAS and CRUDEOIL futures**. It places no orders and does not measure option
P&L. SILVERM is Silver Mini, not Silver Micro. India VIX is not used as a
commodity volatility filter; each trade stores Daily ATR and ATR% instead.

Zerodha access was verified on 14 October 2026: current quotes, bid/ask depth,
volume, OI, individual-contract daily/hourly candles, and two years of continuous
daily history were returned for all four commodity families. Available history
depends on contract listing and liquidity; downloading 400 days does not create
400 daily candles for a recently listed contract.

- Live monitoring batches quotes every 20 seconds. This is polling, not tick-level
  execution. Candle refresh runs independently and shares the existing global
  Zerodha request limiter. Index scanners need not finish first.
- Contract Daily candles: initial lookback 400 calendar days. Hourly: 90 days.
  After bootstrap, refreshes are incremental and only needed after a candle closes.
- Forming candles are excluded from the analysis cache; a pre-close download
  cannot become a completed-candle signal simply because the clock advances.
- `Download MCX history` also archives 730 days of continuous Daily history.
  This action downloads data only; it cannot open paper trades or send signals.
- Continuous history is context only, never used as a trade price or fed into this
  strategy. Unadjusted rollover gaps can distort indicators and backtest P&L.
- Each contract has separate atomic CSV files, keyed by its full trading symbol.
  The MCX master is cached daily and dated copies are retained because instrument
  tokens may be reused after expiry. No lookup of expired tokens is attempted.

Kite documentation: [quotes](https://kite.trade/docs/connect/v3/market-quotes/),
[history and continuous futures](https://kite.trade/docs/connect/v3/historical/),
[streaming](https://kite.trade/docs/connect/v3/websocket/).
Continuous expired-contract retrieval is supported for **daily** candles, not a
promise of expired hourly history. Archive hourly contracts while they are live.
For deeper research, MCX [Bhav Copy](https://www.mcxindia.com/market-data/bhavcopy)
is an official EOD source; it does not replace an intraday streaming feed. A
licensed historical-data vendor is needed if unavailable expired intraday history
is essential. Overseas futures and USD/INR can be context, not MCX execution prices.

## Starting on the Personal Laptop

1. Update the v5 checkout and restart the web server on port 8766.
2. Authenticate Zerodha under Data Ops. Existing API key/token settings are reused.
3. Open **MCX Swing**, then **Download MCX history** for the initial cache.
   This includes the MCX instrument master; no MCX command-line preparation is needed.
4. Confirm contract names, expiries, data freshness and any errors in the table.
5. Select **Start swing scanner**. All four commodities share the data worker and
   are evaluated independently; a failed commodity does not stop the others.
6. Keep the laptop awake and server running throughout MCX hours. The browser may
   be closed. **Stop** stops both entries and exits; it does not liquidate a trade.

Optional separate Telegram destination, with no fallback to the NIFTY chat:

```dotenv
MCX_TELEGRAM_BOT_TOKEN=
MCX_TELEGRAM_CHAT_ID=
```

Restart after changing these values. Use **Test MCX Telegram** for a labelled
connection test, then enable **Telegram alerts** before starting. Tokens never
appear in exports. Telegram is off by default. Signals are persisted before
delivery; delivery failures and expired/ambiguous attempts remain visible.

## Swing Rules

Both long and short directions use **the selected individual futures contract**:

| Gate | Long | Short |
| --- | --- | --- |
| Closed Daily trend | Close > EMA20 > EMA50; EMA20 above its value 3 bars earlier | Close < EMA20 < EMA50; EMA20 below its value 3 bars earlier |
| Closed hourly trend | Close > EMA20 > EMA50 | Close < EMA20 < EMA50 |
| Hourly breakout | Close above preceding 20 hourly highs | Close below preceding 20 hourly lows |
| Hourly RSI14 | 52-75 inclusive | 25-48 inclusive |

At least 60 completed bars in each timeframe are required. Malformed prices,
unordered candles, and gaps in the recent hourly breakout window are rejected.
Daily ATR14 uses the repository's existing arithmetic true-range implementation.

Entry is eligible only within five minutes after the hourly candle closes, at
10:00 through 22:05 IST during an eligible session. Quotes must be at most 45
seconds old, with a trade within 120 seconds, positive volume/OI and a bid/ask
spread at most 0.15%. The breakout must remain valid and the entry quote must be
within 0.25 Daily ATR of the signal close. Every rejection is logged. No late
catch-up entry is manufactured after a restart.

Paper entry price is the observed ask for long or bid for short. The signal close
and delay are stored separately. Stops are 1.5 Daily ATR away, rounded outward to
the contract tick; targets are 2R. Trades may span multiple sessions. There is no
NSE-style 15:30 square-off. Exit reasons:

- Stop or target breached at a fresh observed exit-side quote.
- Closed Daily candle reverses across EMA20.
- Ten calendar days holding elapsed.
- Expiry risk buffer reached.

The monitor does not reconstruct missed stop fills from candle extremes. When
stopped, restarted, receiving a stale/unavailable open-contract quote, or missing
market-time checks for over 120 seconds,
affected paper trades are marked interrupted. They remain auditable and continue
on restart, but are excluded from headline win rate. Normal overnight and weekend
closures are not outages. Gap-through stops use the actual observed quote, not an
optimistic fill at the stop level.

## Expiry and Session Safety

New entries use the nearest listed contract with at least 21 calendar days until
expiry. Open trades stay pinned to their original symbol, token and expiry, even
when discovery selects the next contract. The paper risk exit begins ten calendar
days before expiry. These are conservative strategy settings, **not** an exchange
delivery calendar or a guarantee of broker availability. Check contract-specific
tender and broker square-off deadlines before placing any manual position.
See [Zerodha's holding rules](https://support.zerodha.com/category/trading-and-markets/trading-faqs/commoditytrading/articles/how-long-can-i-hold-mcx-contracts).

Sessions use MCX 09:00 starts, seasonal 23:30/23:55 closes, and the published
[2026 holiday calendar](https://www.mcxindia.com/market-operations/trading-surveillance/trading-holidays),
including evening-only holidays and the New Year morning-only session. The final
hour can be partial; it is not used for new entries. Daily bars have a five-minute
finalization allowance. Special Muhurat sessions are not eligible entry sessions.
Unexpected closures are additionally guarded by quote freshness. **Calendar years
after 2026 block new entries until reviewed and updated**; existing paper exits
remain quote-gated. Calendar/circular changes require a software update.

## Reviewing Results

Commodity subtabs show contract history, indicator values and trades. Open/closed
filters and ten-row pagination apply. Review dates are IST and include positions
overlapping the selected period, not just trades opened that day. Closed-trade
performance is counted by exit date within the review range.

**Download trades & logs (.zip)** includes all four commodities for the selected
range: `trades.csv`, full JSON trade/decision/event/delivery history, source candle
CSVs for selected and traded contracts, strategy version and risk assumptions.
The UI shows only the latest 100 events/deliveries; the export is not truncated.
It contains no `.env`, credentials, or brokerage account information. Share this
after MCX closes, including days with no signals, so rejection reasons can be reviewed.

Results are points, percentage moves and model R, not rupees per lot. Contract
units differ, and broker `lot_size` alone is not a reliable MCX price multiplier.
Headline win rate uses uninterrupted closed paper trades after an assumed 5 bps
cost per side; actual fees, taxes, market impact and fills can differ. A large win
rate from a small sample is not evidence of an edge. Evaluate expectancy, drawdown,
sample size and out-of-sample results before relying on these signals.

## Storage and APIs

- `data/db/mcx_swing.db`: persistent trades, decisions, events, state and outbox.
- `data/raw/mcx/<CONTRACT>/day.csv`, `60minute.csv`: exact-contract candles.
- `data/raw/mcx/continuous_context_only/<ROOT>.csv`: separate research archive.
- `GET /api/mcx/status`, `/api/mcx/export`: optional `from_date`, `to_date`; export supports `format=zip`.
- `POST /api/mcx/start`, `/stop`, `/download`, `/test-telegram`.
- MCX status and open trades also appear on the common Dashboard; global log
  downloads include MCX evidence. Existing NSE scanners and instrument-master
  readiness checks do not depend on MCX data.

No win-rate claim or optimized MCX backtest is made by this initial implementation.
