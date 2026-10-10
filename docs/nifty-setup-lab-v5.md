# NIFTY Setup Lab: v1 experiment

This is a forward paper-trading experiment on NIFTY 50 **spot**, not futures,
option premiums, orders, or a proven profitable strategy. Existing index,
Purple Touch and pullback scanners are unchanged. The lab starts separately.

## Five rules, ten independent ledgers

| Setup | Closed candles | Bullish rule | Bearish rule |
| --- | --- | --- | --- |
| Nifty_Setup1 | 15 minutes | Previous close at/below its EMA20; current close > EMA20 > EMA50, green candle, RSI14 52-70 | Reverse trend/reclaim, red candle, RSI14 30-48 |
| Nifty_Setup2 | 15 minutes | Close above the preceding ten bars' highest high, EMA20/50 uptrend, RSI14 55-75 | Close below ten-bar low, downtrend, RSI14 25-45 |
| Nifty_Setup3 | 5 minutes + closed 15 minutes | Cross above the completed 09:15-09:30 range; green candle and 15-minute close > EMA20 > EMA50 | Cross below opening range, red candle and inverse 15-minute trend |
| Nifty_Setup4 | 5 minutes | Previous close outside lower 20-bar/2-population-SD Bollinger band; close returns inside, RSI crosses upward through 35, green candle | Upper-band re-entry, RSI crosses downward through 65, red candle |
| Nifty_Setup5 | 5 minutes + closed 15 minutes + previous daily | Previous 5-minute close above previous-day high, current candle retests that level and closes above it, green and 15-minute uptrend | Previous-day low retest from below, red and 15-minute downtrend |

Setup4 requires absolute EMA20/50 separation <= 0.5 ATR to select a flatter
regime. Setup5 accepts a retest within 0.1 ATR on the breakout side of the
level, but rejects penetration deeper than 0.25 ATR. Indicators use the
existing application library. At least 60 closed bars are needed. Missing,
nonfinite, invalid, duplicate or gapped recent intraday candles block analysis.

Each setup has a technical-only ledger and a separately gated combined ledger.
Each ledger can hold one open trade. Both can take the same signal at the same
observed spot price, but later occupancy can differ. Compare paired signals as
well as aggregate results: the combined sample is a filtered subset, not an
independent randomized trial.

## Shared data, independent analysis

```text
Rate-limited Zerodha access + shared live NIFTY quote service
        |
        +-- one lab candle worker: 5m, 15m, daily -> atomic cache
        +-- one lab option worker: nearest expiry, around spot, once/minute
                    |
        +-----------+-----------+-----------+-----------+
      Setup1      Setup2      Setup3      Setup4      Setup5
        |           |           |           |           |
        +-- technical-only ledger for each setup
        +-- technical + price/OI confirmation ledger for each setup
                    |
        independent exits, durable audit, optional Telegram outbox
```

Five cached-data workers check approximately every two seconds. They evaluate
a completed technical candle only once after a decision is made. A fresh-quote
wait may retry within the entry-age limit. No full-universe downloads are
required. Existing broker rate limiting also covers the other app scanners.
An option download/failure does not delay technical entries or open-trade exits.

## Price and OI confirmation

Compare the same contract/strike/type/expiry within the same IST session over
3, 6 and 15 minutes, using only snapshots already received at the decision.

| Premium | OI | Hypothesis | Call direction | Put direction |
| --- | --- | --- | --- | --- |
| Up | Up | Long buildup | Bullish | Bearish |
| Down | Up | Short buildup | Bearish | Bullish |
| Up | Down | Short covering | Bullish | Bearish |
| Down | Down | Long unwinding | Bearish | Bullish |

These are **positioning hypotheses**, not proof of institutional activity.
Each open contract has a buyer and seller; OI does not identify the initiator.
Option premiums also change with implied volatility, time decay and moneyness.
See [Zerodha's OI explanation](https://zerodha.com/varsity/chapter/open-interest/)
and [Kite quote field documentation](https://kite.trade/docs/connect/v3/market-quotes/).

Versioned experimental gates:

- Latest snapshot <= 120 seconds old. Exchange and last-trade timestamps must
  each be <= 120 seconds old at snapshot receipt. No claimed fixed OI delay.
- Valid positive premium/OI and bid/ask; bid-ask spread <= 5% of premium.
- At least eight liquid matched contracts and 70% coverage; both CE and PE.
- Contract vote needs positive traded-volume increment, absolute premium
  change >= 0.5%, and absolute OI change >= 0.5%. Unchanged OI gets no vote.
- Direction needs >= four votes, >= two agreeing CE and two agreeing PE,
  majority on both sides, and at least twice opposing votes overall.
- 3- and 6-minute views must agree with the technical direction. An available
  opposing 15-minute view vetoes; unavailable 15-minute history is not a veto.
- A window baseline may be up to 90 seconds earlier than its boundary.
  Expiry rolls, volume resets and insufficient matches block confirmation.

Expect at least six minutes of same-session warm-up. Do not convert a blocked
combined signal into a later entry using newer option evidence. This preserves
the decision-time comparison. Every decision records its gates and evidence.

## Common entry and exit policy

- Signal candle closes from 09:30 inclusive to 14:30 exclusive IST. No new
  entries at/after 14:30, outside weekday NSE hours, or > 120 seconds late.
- Entry uses a positive, fresh observed spot quote, <= 20 seconds old and
  timestamped at/after signal close. Reference candle close is stored separately.
- Fixed stop one ATR from signal close; fixed target two ATR away. Recheck at
  live spot: drift <= 0.25 ATR and remaining reward/risk >= 1.5. Never chase
  by moving the target. Actual initial risk is live spot minus the fixed stop.
- Exit each ledger independently at observed stop/target breach, 90-minute
  holding limit, or 15:20 IST. No option confirmation is needed for exits.
- Polling can miss between-check touches; these are observed prices, not fills.
- If the session exit was missed, retain a clearly labelled 15:30 historical
  reference once the final 5-minute candle is available. Do not send this as
  a live exit. Exclude it from performance.
- Stopping or restarting during an open trade marks monitoring interrupted.
  Such trades remain in the audit and are excluded from performance on closure.
  Stop pauses both entries and exits. Resume explicitly; no automatic start
  after a server restart. Keep the laptop awake and monitor running until exits.

Net R includes an **assumed** two basis points of spot per side. It does not
model option execution, taxes, premium decay, IV, futures basis or lot sizes.
Daily metrics group eligible closed observations by exit date in IST. Drawdown
is additive R, not account drawdown. Profit factor is undefined without losses.
Do not select a winner on win rate alone: compare trade count, expectancy,
profit factor, drawdown, paired decisions and forward out-of-sample sessions.
These hypotheses have not been established as profitable.

## Running and sharing

1. Use branch `scanner-audit-and-v5`. Start the app normally on port 8766.
2. Authenticate Zerodha in Data Ops and refresh **NSE and NFO** instrument masters.
   The lab checks local master age (seven-day maximum). NFO is required only
   for option refresh; missing options must not stop technical observation.
3. Open **NIFTY Setup Lab**. Click **Start all five**, or start individual rows.
   This does not start/stop the existing index or stock scanners. Use one app
   server for live monitoring on the laptop.
4. Inspect shared data freshness, option windows and each setup's last check,
   last closed candle, blocked reasons, trade ledger and delivery status.
5. Select the IST session. Download **trades & logs (.zip)** for complete JSON:
   decisions, raw option observations, trade-entry evidence, runtime, monitor
   events, thresholds, performance, and Telegram delivery errors. CSV contains
   that session's trades across all ten ledgers. Exports exclude credentials.
6. Share the ZIP after the market session. Retain multiple sessions before
   changing rules. Rule version `nifty-lab-v1` is saved with every decision/trade.

Telegram is optional and off by default. Configure a dedicated bot/group in
`.env`, then restart the server:

```dotenv
NIFTY_LAB_TELEGRAM_BOT_TOKEN=
NIFTY_LAB_TELEGRAM_CHAT_ID=
```

Use **Send test**, then enable **Telegram paper alerts** before starting. There
is no fallback to other scanner chats. Messages identify setup, variant and
paper status; spot is never described as a strike or option premium.
Trade/alert persistence is transactional, with the existing durable asynchronous
outbox handling delivery. Telegram failure cannot prevent a trade record.

Persistent lab state lives in `data/db/nifty_setup_lab.db` (relative to the app's
existing database root). Back it up with the application stopped. Do not delete
or copy live SQLite files. An exchange holiday calendar is not implemented;
closed/fresh-data gates prevent old candles from becoming new intraday signals.
