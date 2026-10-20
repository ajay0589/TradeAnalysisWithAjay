# Personal-laptop review: October 19, 2026

Reviewed October 20, 2026 (IST). Sources: the supplied NIFTY Setup Lab October
12-19 ZIP, October 19 scalping ZIP, and October 19 application ZIP. These are
observations from the personal laptop, not local production tests or broker fills.

## Findings

- The weekly NIFTY Lab export contains 10 closed paper trades: seven technical
  and three combined observations. Some are paired versions of the same signal,
  not ten independent opportunities. Five trades appear in the October 19 app
  packet. Older entries are present in the weekly packet.
- NIFTY Lab and Scalping (also Expiry/MCX) status views used the log/review dates
  to choose displayed trades. Changing dates could hide history. No reviewed
  export path deletes trades. These exports alone cannot establish whether all
  earlier database records are still on the personal laptop.
- All 39 scalp trades are closed. Thirty-eight are marked `quote_gap` and excluded
  from performance. Only one has uninterrupted, eligible monitoring. Their
  observed exit prices and R values still exist; the UI previously replaced the
  result with just `Excluded`. These observations cannot establish scalp win rate.
- All Lab/scalp trades in these packets have Telegram disabled. Index alerts have
  separate settings: all five NIFTY and two BANKNIFTY alerts are marked sent.
- October 19 index closed trades: NIFTY bullish -27.00 gross spot points, NIFTY
  bearish -22.50, BANKNIFTY bearish -26.25. A positional NIFTY bearish trade was
  still open. SENSEX has no trades. This is not option P&L and excludes costs.
- Index entry detection lag was approximately 11.8 and 14.4 seconds for the two
  NIFTY intraday entries, 29.3 seconds for BANKNIFTY. Signal references and observed
  entry quotes are recorded separately. No evidence supports substituting the
  earlier candle close as an executable fill.
- NIFTY Lab has 1,100 decisions, 14 technical signals and 10 ledger entries. Six
  variant checks expired; others were blocked by option disagreement, an existing
  trade, drift or reward/risk. There are 733 five-minute and 208 fifteen-minute
  stale/missing progress events; event counts are not counts of unique outages.
- The application packet has 106,374 events, mostly HTTP polling and scanner
  progress. It includes one broker HTTP 403 and one HTTP 503; these alone do not
  explain every quote gap. Prior scalp logs did not retain the first gap's exact
  quote/receipt timestamps, so network latency versus timestamp handling cannot
  be determined retrospectively.

## NIFTY Lab sample, not a strategy ranking

| Setup | Variant | Closed | Wins | Net spot R |
| --- | --- | ---: | ---: | ---: |
| Setup1 | Technical | 3 | 1 | +0.309 |
| Setup1 | Combined | 1 | 1 | +2.632 |
| Setup2 | Technical | 2 | 1 | -0.345 |
| Setup2 | Combined | 2 | 1 | -0.345 |
| Setup3 | Technical | 1 | 1 | +0.858 |
| Setup4 | Technical | 1 | 0 | -1.195 |

Setup5 has no trades in this packet. One winning combined Setup1 trade is not
evidence of a reliable edge. Do not add correlated variant R values as a portfolio.

## Implemented corrections

- All-history trade payloads are independent of log export/review dates. Each Lab,
  MCX and Expiry trade section has entry/exit date filters and an All dates reset,
  alongside existing status/variant filters and ten-row pagination.
- Known outcomes show green Profit / red Loss with units and price basis.
  Interrupted observations remain explicitly excluded; missing exits remain
  unavailable. Unrealized marks are not realized profits. Shared dashboard rows
  distinguish model R from spread INR and gross index spot points.
- Exports remain date-scoped and do not mutate history. Future exports add the
  first quote-gap timestamps, stream state and freshness threshold.

## Futures approach: collect first, validate before entry gating

Added `futures-shadow-v1` for NIFTY, BANKNIFTY and SENSEX. The existing shared
quote worker makes one additional rate-limited batch at most every 15 seconds.
It selects the nearest unexpired futures contract from NFO/BFO masters, not a
hardcoded expiry weekday. No separate futures WebSocket connection is opened.

Recorded fields: actual contract/expiry, futures price, broker session average
price (VWAP), volume, OI, bid/ask, exchange and receipt timestamps, synchronized
spot and basis. Samples persist in `data/db/futures_context.db` and are exported
with index/Lab/scalp date-range ZIPs. Only start controls activate collection.

Candidate confirmation uses aligned futures and spot returns, positive volume
increments and the correct side of VWAP over **3, 6 and 15 minutes**. Price noise
threshold is 0.03%. OI changes above 0.5% get separate buildup/covering/unwinding
labels. Basis is context, not a bullish/bearish gate. All are research heuristics.
Quotes/trades must be <=30 seconds old, synchronized within five seconds, with
positive depth and a spread <=0.1%; gaps >45 seconds invalidate a window. Day,
contract and process changes require fresh warm-up. No cross-expiry OI comparison.

Decisions/trades capture point-in-time evidence. All-history setup/variant cohorts
show aligned, conflicting, unconfirmed and unavailable observations. Existing
entry and exit rules are unchanged. This is a shadow comparison, not an activated
new strategy, independent validation or a claim of better win rate. Illiquid
SENSEX futures stay unavailable instead of blocking the baseline.

Next review should compare complete future sessions, preserve the baseline, exclude
gaps, and separate setup/variant/direction/time-of-day cohorts. Do not optimize on
these few trades. Historical packets lack futures quotes, so no honest retrospective
futures-filter backtest or claimed 50% win rate is possible from them.

## Sources and limitations

- [Kite quote fields](https://kite.trade/docs/connect/v3/market-quotes/) document
  futures/options OI, volume, average price, depth and exchange timestamps.
- [CME open-interest explanation](https://www.cmegroup.com/trading/about-volume.html):
  each open contract has a buyer and seller. OI does not identify institutions or
  show whether an individual trader opened a hedge, speculative or arbitrage trade.
- [CME equity index basis](https://www.cmegroup.com/education/courses/introduction-to-equity-index-products/what-is-equity-index-basis):
  futures minus spot includes carry considerations; a premium is not automatically
  directional conviction.

No broker orders or test Telegram messages were sent during this change. Existing
trade files and credentials are not copied into source control.
