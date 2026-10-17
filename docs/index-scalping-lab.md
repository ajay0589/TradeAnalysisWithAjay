# Index Scalping Lab

Version: `scalping-v1-experimental`. This is an unvalidated, read-only paper
experiment. No order endpoint is used. Existing scanners and their databases
are unchanged. Default live signals use completed one-minute candles.

## Independent experiments

NIFTY, BANKNIFTY and SENSEX each run three setups. Each setup has three independent
ledgers: A technical only, B technical plus option premiums, C B plus OI activity.
There are nine setup controls and 27 ledgers, sharing one scalping WebSocket and
one rate-limited historical refresh worker. Open a setup from the Scalping Lab
tab; simply viewing the page never starts workers or requests broker data.
The ledgers are correlated experiments, not 27 independent portfolio positions;
their summed R is not an account return or a sizing recommendation.

| Setup | Bullish rule | Bearish rule |
| --- | --- | --- |
| Scalp1 | Completed 5m close > EMA20 > EMA50; EMA20 rising; preceding three entry bars touch EMA20; green entry bar closes above EMA9 and previous high | Reverse trend, EMA20 touch, red close below EMA9 and previous low |
| Scalp2 | Completed 09:15-09:30 opening range; preceding entry candle closes above range high; current low retests within -0.25/+0.1 ATR; green close above high and 5m trend up | Reverse below opening low with -0.1/+0.25 ATR retest and 5m downtrend |
| Scalp3 | 5m EMA20/50 separation <=0.5 higher ATR; preceding candle breaks opening low and recovers; current green candle closes above previous high | Failed opening-high breakout and red close below previous low |

Stops use the pullback/retest/failure extreme plus 0.1 entry ATR. Risk must be
0.3-2.5 ATR; signal range must not exceed 2.5 ATR. Targets are fixed at 1.5R from
signal close. Actual spot entry must be within 0.25 times the smaller of ATR and
initial risk, and retain at least 1R reward/risk. It never moves the target to
justify chasing. Rules and the simple ATR/EMA implementations are shared with
historical research. Signal bars and higher candles must be completed; missing
recent candles block entries. No synthetic spot VWAP or volume is used.

## Price and OI confirmation

Choose nearest listed unexpired expiry and nearest strike to observed spot (tie:
lower strike). Subscribe to its CE/PE and adjacent strikes. Both option quotes
and spot must be fresh; compare the same ATM contracts across 1m and 3m windows.
Changing ATM resets the evidence. Keep an open trade's option contract subscribed
until exit even if ATM changes. Contract selection is not an order recommendation.

B requires spot direction plus CE midpoint increasing and PE midpoint decreasing
for bullish entries (reversed for bearish), at least 0.2% each in both windows.
Maximum spread is 1.5%; both sides require positive displayed size. Quotes may
be at most 3 seconds old; option and spot exchange timestamps must be within
2 seconds. The existing bounded three-second exchange-clock lead tolerance is
preserved without changing stored timestamps; future receipt times are rejected.
Baselines may precede the window boundary by at most 5 seconds.

C additionally requires absolute OI change >=0.5% and positive traded-volume
increment in each CE and PE for both windows. Long buildup/short buildup/covering/
unwinding are recorded as hypotheses, not trader identities. This initial C rule
tests additional OI activity, not an inferred institutional net position. Missing
or unchanged OI blocks C, not A or B. Premium changes are partly mechanical
responses to spot/IV/time; added predictive value has not been established.

One decision timestamp applies to all variants. B/C cannot use newer option
evidence to convert a rejected decision into a later entry. Occupancy/cooldown
may differ across ledgers; compare shared decision IDs as well as aggregate
results. A closed candle is persisted once; SQLite writes and alert queuing are
transactional. Failure in one index worker does not stop the others.

## Timing, exits, and accounting

- Entries 09:30-14:45 IST, with a maximum signal age of 10 seconds.
- Stop/target checks use fresh observed spot, not historical candle-close prices.
- Maximum holding time 20 minutes; session exit 15:20 IST.
- Three-minute cooldown and at most six entries per ledger/session.
- Stop new entries keeps open-trade exit monitoring running. Do not close the
  server or sleep the laptop before trades close.
- Restart/quote gaps exclude affected trades from performance. A missing prior
  session exit is closed as unknown, with no invented exit price and no alert.
- Quote callbacks aggregate complete minutes. A partial start or a >5-second
  intra-minute gap prevents finalization; a historical refresh repairs gaps.
- Observed quote exits can miss between-check touches and are not broker fills.
- Spot net R assumes two basis points of friction per side. This is NOT a model
  of option fees or option net P&L. Option reference entry is ask and exit is bid
  for the original contract; gross points exclude fees, depth impact and slippage.
- India VIX is captured when available; no missing VIX is fabricated. VIX is a
  research reference, not a baseline entry gate.
- Holiday detection currently follows the app's weekday calendar; fresh data
  gates block entries on closed weekdays. Special sessions are not supported.

## Setup and use

1. Authenticate Zerodha and refresh NSE, BSE, NFO and BFO masters in Data Ops.
2. Open Scalping Lab. All scanners initially remain stopped.
3. Start individual experiments or all nine. Warm-up and errors appear in Feed
   health. Keep the server and computer awake. No browser tab needs to stay open.
4. For separate optional paper alerts, set `SCALP_TELEGRAM_BOT_TOKEN` and
   `SCALP_TELEGRAM_CHAT_ID` in the local `.env`, restart, send a connection test,
   then enable Telegram before starting. No fallback to any existing chat.
5. Alerts are explicitly paper observations. Scalping deliveries expire after
   15 seconds rather than sending a stale scalp later. Delivery records remain
   in the downloadable audit even when Telegram fails.
6. Filter by index, setup, variant and open/closed status. Date ranges use IST.
   Tables paginate at ten rows. Download ZIP for full decisions, trade evidence,
   policy, monitor events and delivery results. CSV is a compact spot trade list;
   use ZIP for option contract/quote evidence.

Database: `data/db/scalping_lab.db`. Minute cache: `data/scalping/`. Back up with
the application stopped. Credentials are not included in exports.

## Research and promotion

Run cached research or explicitly download 190 calendar days of minute history.
Broker requests share the existing global rate limiter. Do research outside live
scalping; start is blocked while research runs. The report tests 1m/3m signals,
1R/1.5R/2R targets, and 1/2 weeks plus 1/2/3/6 months. Missing coverage remains
missing; partial data is never presented as a complete six-month backtest.

Baseline replay enters at the next minute's open, handles adverse stop gaps,
assumes the stop first if stop and target are touched together, and deducts the
same assumed spot costs. It is development research only. It does not reproduce
the live websocket sampling, sub-minute manual latency, spread, or option gates.
Option variants cannot be historically replayed from spot-only data. Fixed-
contract option quotes/OI and availability timestamps are required first.

Nothing is automatically promoted. Before treating a candidate as actionable:
review chronological held-out results, transaction costs and delayed entries;
segment by expiry, time of day and volatility; then forward-test several weeks.
Your 50% win preference is not a guarantee, and at 1:1 it is break-even before
costs. Never optimize repeatedly against the held-out period.

Futures VWAP/volume, constituent breadth, VIX gating and exact 5/15/30-second
latency replay are deferred experiments, not silently assumed confirmations.
