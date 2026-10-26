# F&O swing scanner workflow

The F&O Stocks workspace now has one live-data paper swing scanner, its trade
ledger, historical validation and manual research. No strategy is certified
profitable and no broker orders are placed.

## Workspace

- Swing Scanner: Trend Pullback and Compression Breakout, long and short, Daily
  and hourly candles. One position per stock across setups and frames.
- Trades: current swing paper trades, local status/setup/stock/date filters,
  ten-row pagination, positive/negative result colours, VIX and audit downloads.
  The legacy pullback archive is collapsed and read only. Stored history is not
  deleted, migrated or filtered by the log download dates.
- Validation: the same setup rules and risk-plan calculations replayed against
  historical candles, including technical controls and market/sector variants.
- Research: single-instrument analysis and a collapsed advanced custom backtest.
  The former separate Screener navigation and legacy start/start-all controls
  are retired. Old legacy-start API routes return an actionable error.

## Validation status

"Validation complete" means the calculation finished, not that any setup passed
or was approved for live-money trading. The screen now shows the specific
coverage, sample-size and return-screen blockers instead of the blanket
"live approval remains blocked" progress message. A paper scanner can run
separately after explicit acknowledgment. The old automated live-start route
remains blocked; there is no override that certifies a strategy profitable.

Historical end dates on weekends/holidays resolve to the previous verified
regular session. For example, October 25, 2026 resolves to October 23. Completed
progress reaches the actual total. Current F&O membership and sector mappings
still introduce historical survivorship bias; events, historical F&O bans and
actual derivative fills are not reconstructed. Screen passes are research
leads, not approvals. The chronological later segment is not an untouched test.

## Running live data paper scans

1. Authenticate Zerodha in Data Ops and refresh NSE/NFO instrument masters.
2. Open F&O Stocks > Swing Scanner. Choose setups, Daily/hourly candles, the
   market benchmark, aligned or relative-strength confirmation and target R.
3. Acknowledge unvalidated paper signals. Optionally enable Telegram paper
   alerts, then Start. Opening a tab never starts workers or sends messages.
4. Keep the server awake during market hours. Initial candle verification and
   missing-history downloads run in the background with source progress.
5. Stop new entries leaves existing position exits monitored. A server restart
   requires an explicit Start to resume; interrupted observations stay excluded.

Telegram reuses `PULLBACK_TELEGRAM_BOT_TOKEN` and `PULLBACK_TELEGRAM_CHAT_ID`.
There is no need to create another chat if these are already configured. Index
Telegram routing is unchanged. Test messages are sent only by the test button.

## Rules and timing

Technical and market/sector computations come from `stock_research.py`; both
historical replay and live paper observation use `entry_plan`. Live prices are
fresh observed stock spot quotes, whereas historical entries are next-bar OHLC
opens. This timing/fill difference remains explicit, not disguised as identical
execution. Missing, unordered, invalid or gapped stock candles block entry.

Each new closed-bar boundary is verified from the broker before it can qualify
an entry. Forming candles are not written by the swing refresh worker. Verified
closed caches are reused, avoiding repeated full-history downloads. Daily
stock, benchmark and sector inputs must be complete as of the decision.

Entries are admitted within five minutes of the next bar opening, at most
0.25 initial R from the signal reference. End-of-day signals wait for the next
regular session, including weekends/holidays. A midday start cannot chase an
expired Daily signal. Transient quote/context failures may recover within the
entry window; a consumed decision cannot duplicate an entry. Market and sector
direction are required; relative strength is optional additional confirmation.

Stops are structural. Targets of 1R, 1.5R or 2R are measured from the actual
paper entry risk. Positions may remain overnight. Holding limits are ten Daily
or thirty-five hourly bars. Fresh quotes trigger stops/targets before the next
candle close. Genuine monitoring gaps and restarts remain flagged; missing
quotes never produce invented fills. Paper costs assume four bps per side and
are not option/futures fees or fills. No lot, portfolio-capital or margin model
is implied. Review events, bans and derivative liquidity before manual trading.

## Logs and regression checks

Trade filters and ZIP/CSV export ranges are independent. Export includes
overlapping positions, decisions, waiting-data reasons, monitor events and
Telegram delivery outcomes. Repeated identical polling blocks are deduplicated.
The app-wide log and common dashboard include the swing scanner.

Regression coverage includes shared entry risk, duplicate/concurrent entries,
market/sector and relative gates, stale quotes, holiday/overnight signals,
forming-bar exclusion, stop/drain behaviour, monitoring gaps, persistence,
date-range exports, opt-in alerts and read-only navigation. Browser smoke tests
use intercepted paper controls and synthetic display fixtures; they do not run
production scans or send alerts. Desktop and mobile screenshots are stored
under `logs/stock-ui-qa/`.
