# Scan Review: 15 October 2026

Sources: supplied NIFTY Setup Lab ZIP/CSV and index scan ZIP from the personal laptop.
Times below are Indian Standard Time. Conclusions apply to this day's export, not
the complete lifetime of the scanners.

## Setup Lab

- Zero trades; the standalone CSV contains headers only.
- 275 decisions: 217 rules-not-met, 55 outside-entry-window and 3 technical signals.
- Setup1 and Setup2 signalled on the 09:15-09:30 candle; Setup3 on 09:30-09:35.
- Events show signals waiting on unavailable/stale spot quotes from 09:30:06 and
  09:35:01. They expired at 120.802, 121.419 and 120.927 seconds respectively.
- Therefore the scanner was evaluating candles. It was not merely failing to discover signals.
- The export lacks the contemporaneous blocked spot quote, so the exact spot failure
  cannot be reconstructed. New waiting events now include quote timestamps and prices.
- Of 12,546 option rows, 7,688 (61.28%) had exchange timestamps ahead of local receipt.
  All were within 1.64 seconds. The previous zero-tolerance timestamp guard rejects these.
  This directly explains much missing option evidence and is a plausible common cause
  of the spot-quote waits, but the old export cannot prove the latter.
- Lab Telegram was disabled and the dedicated Lab chat was not configured. This affects
  delivery, not whether technical paper trades can be created.
- A partial-candle cache bug was also found in code: a previously downloaded forming
  candle could be reused as final after the wall clock advanced. The Lab now requires
  post-close verification and excludes forming downloaded bars. Old decisions are not rewritten.

## Index Trades

The ZIP already includes `NIFTY.trades`, `BANKNIFTY.trades` and `SENSEX.trades`.
All four exported closed trades lost; three originated on earlier dates.

| Index / horizon | Entry | Exit | Gross points | Gross R |
| --- | ---: | ---: | ---: | ---: |
| NIFTY intraday, bullish | 23043.90 | 23029.80 | -14.10 | -1.01 |
| NIFTY positional, bearish | 22421.95 | 23032.10 | -610.15 | -1.57 |
| Bank Nifty positional, bearish | 55316.00 | 56029.75 | -713.75 | -3.89 |
| Sensex positional, bearish | 72781.89 | 73055.52 | -273.63 | -1.04 |

These are underlying spot observations, not option returns. NIFTY's older positional
trade dates to 1 October and has legacy midnight entry timing. Do not mix it uncritically
with new quote-priced observations. Bank Nifty's exit substantially exceeded the stop;
the supplied export does not contain enough quote history to separate unavailable-quote
delays from gaps or monitoring interruption. Do not assume a -1R fill was achievable.

The new NIFTY intraday signal was detected 12.379 seconds after candle close, not minutes
late. RSI was 79.39 and initial observed risk was 13.90 points against ATR14 35.21.
Trend extension and an unusually tight stop are hypotheses for controlled backtesting,
not changes justified by one loss. Scores such as 95 are not calibrated win probabilities.

Bank Nifty and Sensex each completed over 1,280 cycles with maximum duration three
seconds. Most new entries failed technical, option alignment or constituent breadth gates.
All five delivery records in this export were sent successfully.

## Historical Screen

The development laptop's latest cached NIFTY candles end 7 October 2026. Its 5-minute
cache starts 15 September, so it cannot substantiate a six-month 5-minute test.
The offline run used 7 October as the explicit end date, not 15 October.

342 period/timeframe/target combinations were evaluated. None passed all preliminary
coverage, sample-size, >=50% net-win, positive-expectancy and profit-factor checks.

Examples from six-month native 15-minute Lab technical-rule replays:

| Setup | Target R | Trades | Net win rate | Mean net R |
| --- | ---: | ---: | ---: | ---: |
| Setup1 EMA pullback | 1 | 64 | 46.88% | -0.212 |
| Setup1 EMA pullback | 1.5 | 63 | 44.44% | -0.069 |
| Setup1 EMA pullback | 2 | 63 | 41.27% | -0.075 |
| Setup2 range breakout | 1 | 102 | 46.08% | -0.284 |
| Setup2 range breakout | 1.5 | 102 | 36.27% | -0.338 |
| Setup2 range breakout | 2 | 101 | 34.65% | -0.321 |

A 60-minute RSI-reversal research configuration had 56.67% wins across 30 trades at
1:1, but only eight later-period trades and just +0.020R average net expectancy.
It is not validated. Apparent 5-minute winners with a handful of trades and incomplete
coverage are also not approval evidence.

Costs assume two basis points per side on spot. Results are a research execution model,
not actual option fills or an exact reconstruction of every live scanner gate. The
full matrix and trade records are generated under `reports/nifty-validation-2026-10-15/`.
See [technical validation policy](technical-validation-policy.md) for the screening limits.

## Changes And Next Session

- Three-second bounded clock tolerance for live quote and option timestamp checks;
  stale-age limits and the two-minute intraday signal expiry remain unchanged.
- Quote ages in shared-feed status and blocked quote details in Lab events.
- Post-close Lab candle verification; no trade-rule loosening or automatic strategy promotion.
- From/To dates for app, index, Lab and pullback log downloads; MCX already has range controls.
- Index ZIPs include per-index trade CSVs. A separate index trade CSV button is available.
- Range exports retain carried positions and deduplicate them. Rows show latest stored
  state, not a reconstructed as-of ledger. Only retained logs can be exported.
- On the personal laptop, synchronize Windows time automatically and download a new
  full-session export after updating. Configure the dedicated Lab Telegram keys only
  if Lab paper notifications are wanted. Trading requires separate independent judgment.
