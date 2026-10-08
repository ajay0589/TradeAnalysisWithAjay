# Trading Data Analysis

Read-only automation for Indian stock and options preparation. The first goal is to reduce daily analysis time by turning watchlists, price data, and fundamentals into a repeatable pre-trade checklist.

This project is analysis support only. It does not place orders, and its output is not financial advice.

## Current Phase

- Local CSV candle ingestion
- Watchlist-based technical scoring
- Manual/structured fundamental scoring
- Broker adapter scaffolding for Zerodha Kite Connect and Angel One SmartAPI
- NSE data-source notes that prefer official/authorized data access over brittle scraping

## Quick Start

Run the sample analysis:

```powershell
python -m trading_analysis.cli analyze --watchlist config\watchlist.example.json --data-dir data\sample
```

If your watchlist has symbols whose CSV files are not downloaded yet, either fetch those files or skip them temporarily:

```powershell
python -m trading_analysis.cli analyze --watchlist config\watchlist.example.json --data-dir data\raw\candles --skip-missing
```

Check whether broker credentials are available in your environment:

```powershell
python -m trading_analysis.cli env-check
```

## Wire Zerodha Historical Candles

1. Create a Zerodha Kite Connect app in the developer portal and complete the login flow to get an `access_token`.
   Zerodha access tokens expire at 6 AM the next day, so expect to refresh this token daily.
2. Copy `.env.example` to `.env`, then fill:

```env
ZERODHA_API_KEY=your_api_key
ZERODHA_API_SECRET=your_api_secret
```

3. Generate the login URL:

```powershell
python -m trading_analysis.cli zerodha-login-url
```

4. Open the printed URL in your browser. After successful login, Zerodha redirects to your configured redirect URL with `request_token=...` in the address bar. If your redirect domain is not running, the browser may show "This site can't be reached"; that is fine as long as the URL contains `status=success` and `request_token`.

5. Exchange the redirected URL for an access token and write it to `.env`:

```powershell
python -m trading_analysis.cli zerodha-access-token --request-token "PASTE_FULL_REDIRECTED_URL_HERE" --write-env
```

6. Confirm this project can see the credentials:

```powershell
python -m trading_analysis.cli env-check
```

7. Download and cache Zerodha's instrument master. Do this once per trading day, ideally before market open.

```powershell
python -m trading_analysis.cli zerodha-instruments --exchange NSE --output data\raw\zerodha\instruments_NSE.csv
```

For options and futures, cache `NFO` as well:

```powershell
python -m trading_analysis.cli zerodha-instruments --exchange NFO --output data\raw\zerodha\instruments_NFO.csv
```

8. Fetch daily candles by symbol:

```powershell
python -m trading_analysis.cli zerodha-candles --exchange NSE --tradingsymbol RELIANCE --instrument-cache data\raw\zerodha\instruments_NSE.csv --interval day --from-date 2026-01-01 --to-date 2026-06-13 --output data\raw\candles\RELIANCE.csv
```

9. Fetch intraday candles by token or symbol:

```powershell
python -m trading_analysis.cli zerodha-candles --exchange NSE --tradingsymbol RELIANCE --instrument-cache data\raw\zerodha\instruments_NSE.csv --interval 15minute --from-date "2026-06-01 09:15:00" --to-date "2026-06-12 15:30:00" --output data\raw\candles\RELIANCE_15minute.csv
```

For F&O instruments, add `--include-oi` when you need open interest:

```powershell
python -m trading_analysis.cli zerodha-candles --exchange NFO --tradingsymbol NIFTY26JUNFUT --instrument-cache data\raw\zerodha\instruments_NFO.csv --interval 15minute --from-date "2026-06-01 09:15:00" --to-date "2026-06-12 15:30:00" --include-oi --output data\raw\candles\NIFTY26JUNFUT_15minute.csv
```

## F&O Options Workflow

Generate the current F&O stock watchlist from the daily Zerodha instrument masters:

```powershell
python -m trading_analysis.cli generate-fno-watchlist --nfo-instruments data\raw\zerodha\instruments_NFO.csv --nse-instruments data\raw\zerodha\instruments_NSE.csv --output config\watchlist.fno.json
```

Analyze any F&O stock's nearest-expiry option chain:

```powershell
python -m trading_analysis.cli option-chain --symbol RELIANCE --strikes-around 10
```

The first run writes a snapshot under `data\raw\option_chain`. For build-up classification, compare the next run against the previous snapshot:

```powershell
python -m trading_analysis.cli option-chain --symbol RELIANCE --strikes-around 10 --previous-snapshot data\raw\option_chain\RELIANCE_2026-06-30.csv
```

Build-up labels use price change plus OI change:

- `Long build-up`: price up, OI up
- `Short build-up`: price down, OI up
- `Long unwinding`: price down, OI down
- `Short covering`: price up, OI down

Pull market-wide institutional flow:

```powershell
python -m trading_analysis.cli fii-dii --output data\raw\nse\fii_dii.csv
```

This FII/DII report is market-wide capital-market flow. It is useful as a risk-on/risk-off context input, not as stock-specific buying data.

## Trade Decision Engine

Download multi-timeframe candles for a stock, Nifty 50, and the mapped sector index:

```powershell
python -m trading_analysis.cli update-mtf-candles --symbol RELIANCE --from-date 2026-01-01 --to-date 2026-06-13
```

This writes:

- daily candles under `data\raw\candles`
- 60-minute candles under `data\raw\candles\60minute`

Sector relative strength uses `config\sector_map.generated.json` by default.

If you have a CSV with symbol and sector/industry details, generate the sector map without NSE API calls:

```powershell
python -m trading_analysis.cli generate-sector-map-from-csv --input some_sector_file.csv
```

Supported CSV columns include:

- `symbol` or `Symbol`
- `industry`, `sector`, or `macro`
- optional `index_symbol` when you want to explicitly map to a Zerodha index such as `NIFTY IT`, `NIFTY BANK`, or `NIFTY OIL AND GAS`

See `config\sector_map_input.example.csv` for the expected shape.

Create a trade decision report:

```powershell
python -m trading_analysis.cli trade-decision --symbol RELIANCE --previous-snapshot data\raw\option_chain\RELIANCE_2026-06-30.csv --output-json reports\RELIANCE_trade_decision.json
```

The report combines:

- daily trend, support, resistance, and invalidation
- 60-minute trend, support, resistance, and invalidation
- stock vs Nifty relative strength
- stock vs sector relative strength when sector candles exist
- sector vs Nifty relative strength when sector candles exist
- option-chain PCR, max pain, high CE/PE OI, and OI build-up when a previous snapshot is available

Use `--skip-option-chain` when you only want price and relative-strength context:

```powershell
python -m trading_analysis.cli trade-decision --symbol RELIANCE --skip-option-chain
```

## Web UI

### Windows launcher (recommended)

Double-click `Trading Desk.cmd` in the project folder. A desktop control window opens independently of the web server, so it works even when the server is stopped. Python with Tcl/Tk and Git must already be installed; the standard python.org Windows installer includes Tcl/Tk. This launcher does not require changing PowerShell execution policy or running as Administrator. You can create a Windows desktop shortcut to this file; keep the original file in the project folder.

- `Start Server`: starts this project's server on port 8766, or recognizes an already-running copy. It does not automatically start market scanners.
- `Stop Server`: stops the verified server and its scanner workers. Stored candles, credentials, and trade history are retained.
- `Open App`: opens the app in your browser.
- `Update App`: requires the `scanner-audit-and-v4` branch and a clean working folder, stops the server if running, executes `git pull --ff-only origin scanner-audit-and-v4`, installs `requirements.txt` with the selected Python, and restarts only if the server was previously running. Start the market scanners again in the app afterward. Update outside market hours.
- `View Logs` / `Save Support Log`: shows or exports launcher and server log tails for troubleshooting. Review exported logs before sharing.

Updates never reset, stash, overwrite local edits, or erase data. A failed pull or dependency installation leaves the server stopped and displays the error. Authentication errors require Git access to be configured once; the launcher does not ask for passwords or store Git credentials. Reopen the launcher after updating to load changes to the launcher itself. Closing the launcher asks whether to stop the server or leave it running.

If a port belongs to another project or an old server without identifying health metadata, the launcher refuses to stop it. Inspect that process or ask the maintainer for help. A startup timeout cleans up only the process tree it just launched, instead of leaving a hidden process occupying the port. Local health checks bypass network proxies and verify the project, port, and launch identity, not the Python alias path or launcher PID.

### Command-line alternative

Start the local F&O decision dashboard:

```powershell
python -m pip install -r requirements.txt
.\scripts\start_web_ui.ps1
```

Then open `http://127.0.0.1:8766`.

Use this same URL going forward on `scanner-audit-and-v4`. The script uses `.venv\Scripts\python.exe` when present (install requirements with that Python), otherwise `python` from PATH. The scripts and desktop launcher share the same process controller. Start recognizes a healthy server from this folder, waits for readiness when launching, and refuses a port occupied by an unverified process. The v3 server can continue running separately on port `8765`. Stop the current v4 server before restarting:

```powershell
.\scripts\stop_web_ui.ps1 -Port 8766
.\scripts\start_web_ui.ps1 -Port 8766
```

For a server started by an older script, use Ctrl+C in its foreground window. If it ran in the background and the stop script cannot identify it, inspect the listener before stopping its Python PID:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8766 | Select-Object LocalAddress, LocalPort, OwningProcess
# Replace 12345 with the displayed PID, after confirming it is this app's Python process.
Get-Process -Id 12345 | Select-Object Id, ProcessName, Path
Stop-Process -Id 12345
```

For foreground logs while debugging:

```powershell
.\scripts\start_web_ui.ps1 -Foreground
```

### Purple Touch implementation guide

The latest v4 architecture, Windows installation, visual workflow, confirmed filters, timeframe matrix, freshness handling, persistent setup lifecycle, independent entry/exit checks, UI interpretation, and review checklist are documented in [Purple Touch Implementation Guide v1.3](docs/Purple_Touch_Implementation_Guide_v1_3.pdf). The [previous v1.2 guide](docs/Purple_Touch_Implementation_Guide_v1_2.pdf) is retained for comparison.

The current Weekly-only execution rules, valid/invalid candle diagrams, exact boundaries, live monitoring guide, alert gates, and audit worksheet are documented in [Weekly Purple Touch Validation Guide v1.1](docs/Weekly_Purple_Touch_Validation_Guide_v1_1.pdf). The [previous v1.0 guide](docs/Weekly_Purple_Touch_Validation_Guide_v1_0.pdf) is retained for comparison.

Purple Touch live monitoring supports Monthly, Weekly, and Daily profiles on independent server-side schedules. A continuous, rate-limited candle service prioritizes open trades, active entry setups, and then the full setup universe. Scanners read only atomically completed cache files and do not wait for a full-universe refresh.

Purple Touch entry alerts and trade lifecycle records are retained in local SQLite storage for audit and setup-quality review. The UI combines each created entry signal and its lifecycle into one `Trades Triggered: Open & Closed` table with profile, entry type, status, symbol, date, and sorting controls; it does not automatically purge open or closed Purple Touch trades.

Purple Touch setup lifecycle precedence:

- A strict Monthly, Weekly, or Daily setup is persisted by symbol, profile, and touch-candle timestamp.
- Purple EMA9 and the upper discard level (`captured EMA9 x 1.03`) are fixed when the touch is first stored; later EMA9 changes do not move that limit.
- Exactly 3.00% remains active. A price strictly above the fixed limit discards further entry checking, but does not close an existing trade.
- Candle 1 and Candle 2 can trigger entries. From Candle 3 onward, a break below the lower C1/C2 low discards the setup.
- Early entry keeps the setup active for Final. Final entry stops later Early checks. Each resulting trade keeps its own exit lifecycle.
- Freshness and last-processed candle timestamps prevent stale-data alerts and repeated processing of the same closed entry candle.

The `Purple Touch` tab also includes a cached-candle historical simulation with date range, symbol/profile selection, early/final mode, touch tolerance, score threshold, stop model, target R, holding limit, slippage, costs, capital, and risk-per-trade controls. It uses next-candle-open entry and only candles available at each historical event. Results include profile and entry-type performance, trade rows, data coverage, win rate, expectancy, average R, profit factor, and maximum drawdown.

Risk/reward interpretation:

- `1R` is the initial per-share distance from entry to stop. The default setup stop is the lower of Candle 1 and Candle 2 lows, with a configurable percentage fallback.
- A `2R` target is twice that initial risk. The configured yellow-line exit or maximum holding period may close a simulation before the target.
- A positive average R/expectancy and profit factor above 1 show positive historical results for that sample, but must be reviewed with drawdown, costs, profile consistency, and sample size. A minimum of 30 trades is only a first evidence checkpoint, not proof of future performance.
- The simulation uses underlying-stock candles, not option premiums, IV, theta, margin, strike liquidity, or expiry effects. Its aggregate return does not reserve capital across overlapping symbols.

Regenerate the PDF after material rule changes:

```powershell
python -m pip install reportlab
python -m scripts.generate_purple_touch_v4_guide
python scripts\generate_purple_touch_guide.py
python scripts\generate_weekly_purple_touch_validation_guide.py
```

The dashboard supports:

- analyzing one F&O stock by symbol, or by company name when the Zerodha NSE instrument cache is present
- a dedicated NIFTY Desk tab for intraday, swing, and positional NIFTY context
- checking whether the Zerodha access token is valid, expired, missing, or unreachable
- opening the Zerodha login URL and updating `.env` by pasting the redirected URL containing `request_token`
- scanning bullish candidates for put selling
- scanning bearish candidates for call selling
- scanning neutral candidates for short strangle candidates
- bulk-downloading all F&O candles with progress and failure reporting
- generating the sector map by uploading a CSV
- viewing latest cached/refreshed FII/DII market flow
- selecting Monthly, Weekly, Daily, 1 hour, or 15 min chart analysis
- selecting either a days-back window or explicit from/to dates
- optionally refreshing candles from Zerodha before analysis
- reviewing Day + 1 hour + 15 min multi-timeframe direction, including volume and volume-vs-20-candle average
- advanced option-chain context with expiry selection, all-strikes mode, strikes-around-spot mode, previous snapshot comparison, PCR, max pain, IV, OI change, and build-up
- saving the current trade-decision report as JSON under `reports`

Scans only include F&O symbols whose candle CSV exists for the selected timeframe. Weekly and monthly charts are derived from daily candles.
Multi-timeframe direction uses daily candles for swing bias, 1-hour candles for setup confirmation, and 15-minute candles for intraday timing. Volume comes from Zerodha candle data and is shown as last-candle volume plus `Vol x20`.

Bulk-download candles for the F&O universe:

```powershell
python -m trading_analysis.cli bulk-fno-candles --timeframes day,60minute,15minute --days 90
```

The Web UI bulk downloader also offers Monthly and Weekly. Those frames are derived from daily candles, so selecting them downloads daily source candles with a longer lookback. For monthly chart analysis, keep a longer daily history:

```powershell
python -m trading_analysis.cli bulk-fno-candles --timeframes day --days 1460
```

Option-chain analytics currently includes PCR, max pain, total option volume, ATM IV, IV change from the previous snapshot, OI change, and OI percent change. IV percentile needs accumulated historical IV snapshots before it can be calculated reliably.

## NIFTY Desk

The NIFTY Desk is a read-only analysis workspace for NIFTY-specific setup planning. It combines cached NIFTY candles, cached NIFTY option-chain snapshots, IV history, and strategy suitability rules. It does not place orders and does not produce advisory language.

Required data:

- NIFTY spot candles: `data\raw\candles\NIFTY_50.csv`
- Optional intraday candles: `data\raw\candles\60minute\NIFTY_50.csv` and `data\raw\candles\15minute\NIFTY_50.csv`
- NIFTY option-chain snapshots under `data\raw\option_chain`, for example `NIFTY_2026-07-02.csv`
- IV history under `data\raw\iv_history\NIFTY_iv_history.csv` for IV rank and IV percentile

UI usage:

1. Open `Index Scanning > NIFTY > Manual Research`.
2. Choose `Auto`, `Intraday`, `Swing`, or `Positional`.
3. Select weekly/monthly expiries when cached snapshots exist, or leave them on auto.
4. Keep `Include option chain` and `Include IV context` checked when those datasets are available.
5. Click `Run Manual Analysis` for market, OI, and IV context.
6. Click `Suggest Strategies` to see strategy candidates with suitability score, reasons, risks, and required confirmations.
7. For live monitoring, switch to `Live Scanner` and start `NIFTY Live Scanner` during market hours to monitor Intraday, Swing, and Positional entries and exits.
8. Review `NIFTY Trades: Open & Closed` in `Live Scanner` for entry time, exit time, open duration, stop, target, and directional result.
9. Use `Backtests > Index Spot Backtest` to compare technical-only setups or the existing scanner rules by date range, horizon, direction, target R, and maximum holding bars.

`Live Scanner` is the default NIFTY view. Its schedule, data freshness, job progress, Telegram delivery, alerts and trades stay together. `Manual Analysis Settings` (formerly `NIFTY Controls`) applies only to one-off research: its mode, expiry, date and risk selections do not configure live scanning or create Telegram entry/exit alerts. `Suggest Strategies` uses its own default candle lookback with option/IV context; the strategy risk profile applies to that action only. Research messages and saved alert context are displayed within their respective views. Switching views preserves inputs and results and never starts or stops a scanner. `Start all three` remains the shared live-monitor command.

CLI examples:

```powershell
python -m trading_analysis.cli nifty-context --mode intraday --weekly-expiry 2026-07-02
python -m trading_analysis.cli nifty-strategies --mode swing --risk-profile defined
python -m trading_analysis.cli nifty-payoff --spot 24500 --legs '[{"side":"buy","option_type":"CE","strike":24500,"premium":120},{"side":"sell","option_type":"CE","strike":24700,"premium":50}]'
python -m trading_analysis.cli nifty-backtest --strategy nifty_short_strangle --mode swing --days 365 --params "{}"
```

## NIFTY Live Scanner

### Index scanner performance and diagnostics

- Development preview on the maintainer's laptop uses port `8767`; personal installations keep `8766`. Ports do not determine broker or Telegram credentials: the server's project folder and `.env` do.
- Bank Nifty and Sensex analyze cached data independently of a shared refresh worker. Fresh data wakes analysis early; the selected interval is the maximum scheduled wait between cached checks. Exit checks run before entry analysis.
- The refresh worker prioritizes open-trade candles, batches option quotes across indexes and shared constituents, and refreshes candles at their close boundaries. Option snapshots are requested every three minutes when capacity permits. Stale snapshots still block entry; slow networks are not treated as fresh data.
- `Data Refresh` shows pending sources, the current download, per-source durations and errors. Download both index and application logs after the session. Broker events separate queue, TLS-context setup, connection-to-headers (including DNS/TLS/server wait), and body-read time. Repeated unchanged status polls are sampled rather than reported as new failures.
- Intraday spot-signal trades exit on the final 15-minute candle closing at 15:30 IST, or earlier stop/target/reversal rules. Keep scanners running through the 15:45 finalization window. No orders are placed. Missed closed candles are replayed in order on the next exit check. Entry metadata separates signal candle start/close from detection time; history is not rewritten.
- Update outside market hours using the desktop launcher's `Update App`, which installs dependencies. For a manual update, install `requirements.txt` using the same Python/virtual environment as the server, then restart. Verified HTTPS connection pooling uses `urllib3`; certificate validation and proxy settings remain enabled. Do not run two server instances against the same data folder during live trading.

NIFTY Live Scanner is a local market-hour scheduler for the NIFTY Desk. It uses SQLite storage at `data\db\trading_analysis.db`, enables WAL mode, and stores job history, market-data snapshots, context, entry/exit alerts, and complete trade lifecycles. It is read-only and never places orders.

Configure its separate Telegram destination in `.env`:

```text
NIFTY_TELEGRAM_BOT_TOKEN=...
NIFTY_TELEGRAM_CHAT_ID=...
```

The bot token may be reused, but the chat ID should identify the dedicated NIFTY chat or group.

Default scan behavior:

- Market hours: Monday-Friday, 09:15 to 15:30 Asia/Kolkata. Holidays are a TODO/configurable calendar.
- Candle readiness: checked every 10 seconds, with downloads only when the expected closed bar is due or missing.
- NIFTY option chain: every 3 minutes when refresh is available through the attached analysis service.
- IV snapshot/context: every 5 minutes.
- Intraday, Swing, and Positional entry checks: every selected UI interval, default 1 minute.
- Open-trade spot stop/target checks: every second using fresh shared quotes; closed-candle EMA/holding exits remain independent.
- Job cleanup: every 30 minutes, keeping recent job history.

Horizon rules:

- Intraday: 15-minute closed candles; default maximum hold 8 bars.
- Swing: 60-minute closed candles; default maximum hold 12 bars.
- Positional: Daily closed candles; default maximum hold 10 bars.
- Entry requires aligned EMA20/EMA50 trend, RSI, candle direction, a score of at least 70, and option-chain bias that is aligned or neutral.
- Stop uses the nearest valid support/resistance, then ATR/fallback risk when no valid level exists. Default target is 2R.
- Exit occurs at stop, target, EMA20 reversal, or maximum holding period. If stop and target occur within the same candle, the backtest takes the conservative stop-first result.

SQLite tables:

- `market_jobs`: every scheduler job run, status, duration, error, and result JSON.
- `nifty_candles`: cached NIFTY OHLCV candles by timeframe, upserted from the same local candle CSVs used by analysis.
- `nifty_option_chain_snapshots`: option-chain snapshot summary rows, including expiry, PCR, max pain, ATM IV, OI totals, and raw CSV file path.
- `nifty_option_chain_rows`: strike-level CE/PE rows linked to each option snapshot, including OI, IV, volume, bid/ask, and build-up label.
- `nifty_iv_observations`: ATM IV observations linked to the option snapshot/source file when available.
- `nifty_alerts`: NIFTY entry/exit events, linked trade ID, Telegram status, context, reasons, and risk levels.
- `nifty_trades`: open and closed trades with entry/exit timestamps, open duration, stop, target, result, and exit reason.
- `nifty_context_snapshots`: exact technical, option-chain, IV, summary, warning, and error context captured during Auto Scan.
- `nifty_strategy_candidates`: strategy candidates linked to the context snapshot that produced them.
- `nifty_alert_outcomes`: saved signal-quality backtest outcomes for stored alerts.

Alert traceability:

- Raw input layer: `nifty_candles`, `nifty_option_chain_snapshots`, `nifty_option_chain_rows`, and `nifty_iv_observations`.
- Derived context layer: `nifty_context_snapshots` and linked `nifty_strategy_candidates`.
- Alert layer: `nifty_alerts` stores the event kind, trade ID, `context_snapshot_id`, Telegram state, and source-data links.
- Trade layer: `nifty_trades` independently tracks each Intraday, Swing, and Positional lifecycle.
- Outcome layer: `nifty_alert_outcomes` stores signal-quality backtest rows for stored alerts.
- Every newly generated alert can link to a `context_snapshot_id`.
- Use the NIFTY Desk `View Context` action to inspect the exact technical/options/IV context behind an alert.
- Data Freshness cards in the NIFTY Desk show the latest 15-minute candle, latest option-chain snapshot, latest ATM IV observation, and local DB row counts.
- CSV remains the fallback/source cache for candle and option-chain data. SQLite is the local audit database, not a shared server database.

Web UI usage:

1. Open `Index Scanning > NIFTY > Live Scanner`.
2. Choose the live scan interval and use `Start scanner` during market hours.
3. Confirm the Started, Next Entry Check, Next Exit Check, and Telegram status values.
4. Use `Run one cycle` for a forced diagnostic outside market hours; stale inputs cannot create an entry.
5. Review `NIFTY Entry & Exit Alerts`, `NIFTY Trades: Open & Closed`, and linked context.

CLI examples:

```powershell
python -m trading_analysis.cli nifty-auto-status
python -m trading_analysis.cli nifty-auto-run-once --force
python -m trading_analysis.cli nifty-auto-start
python -m trading_analysis.cli nifty-alerts --limit 20
python -m trading_analysis.cli nifty-alert-backtest --timeframe 15minute --limit 500 --horizons 3,5,10,15
```

NIFTY scanner backtest:

- Replays closed-candle EMA20, EMA50, RSI14, ATR14, candle direction, stop, target, EMA reversal, and maximum-hold rules.
- Reports win rate, average/total R, expectancy, profit factor, maximum drawdown in R, entry/exit times, and open duration.
- Uses a date range and separate Intraday, Swing, or Positional source timeframe.
- Historical option-chain confirmation is not replayed unless timestamped historical snapshots are available.
- This is a NIFTY spot signal test, not option premium P&L; brokerage, slippage, IV decay, margin, and strike-level payoff are not modeled.

Technical-only NIFTY spot backtest:

1. In `Index Scanning > NIFTY > Backtests > Index Spot Backtest`, select `Technical setup (next candle)`.
2. For the daily candidate, select `EMA20 pullback`, `Positional (Daily)`, `Bullish & Bearish`, `Target R = 1`, `Maximum bars = 10`, and `Cost per side = 2 bps`.
3. Run separate date windows: `2023-01-01` to `2024-12-31` (development), `2025-01-01` to `2025-12-31` (validation), and `2026-01-01` onward (holdout). Compare trade counts, win rate, average R, profit factor, and max drawdown. A date range alone does not change indicator warmup; calculations use prior cached candles.
4. Other entry setups are `10-candle breakout`, `RSI reversal`, and `Opening range (15m)` (Intraday only). `Existing scanner rules` reproduces the earlier backtest and retains its original assumptions.

For the EMA20 pullback, bullish requires a previous close at/below EMA20, then a green close above EMA20 with EMA20 above EMA50 and RSI14 from 52 to 70. Bearish reverses those conditions with RSI14 from 30 to 48. Signals use the completed candle; execution is simulated at the next candle open with a 1 ATR14 stop, selected R target, stop-first resolution if both prices are touched, and a maximum holding period. Intraday research trades close by the end of the session. A daily stop/target exit is timestamped at 15:30 IST because daily OHLC does not reveal the exact intraday hit time. The cost input is a spot-equivalent stress assumption, not a measured option fill. NIFTY index volume in the cached data is zero, so this research does not use volume-based VWAP.

Research comparison can be reproduced from the command line with `python -m scripts.research_nifty_technicals`. These technical research rules do not alter live NIFTY alerts. Both UI backtest methods use NIFTY 50 spot index candles in `data/raw/candles`; neither uses NIFTY futures candles or option premium returns.

Auto-scan limitations:

- Alerts are read-only signals and require manual risk review.
- Option-chain and IV context depend on cached snapshots/source availability.
- Only one open trade per horizon is allowed, preventing duplicate same-horizon entries.
- Historical IV rank needs accumulated IV history.
- Alert signal-quality backtest is not options P&L. Accurate options P&L still requires reliable historical option premiums.
- SQLite is intended for local single-user storage.
- The initial holiday calendar is weekday-only and should be extended manually later.

Limitations:

- Strategy suggestions are candidates only; they require confirmation and manual risk review.
- Accurate options strategy P&L requires historical option premium snapshots. Without them, NIFTY backtests report forward spot movement and signal quality only.
- IV rank and IV percentile require enough saved IV observations. The tool warns instead of faking values when history is insufficient.
- Greeks are placeholders until a reliable live or historical Greeks source is added.

Sector map CSV upload expects a symbol column plus sector/industry/index detail. Supported columns are the same as `generate-sector-map-from-csv`, including `symbol`, `industry`, `sector`, `macro`, and optional `index_symbol`.

Zerodha token refresh from the UI:

1. Click `Login` in the Zerodha panel.
2. Complete Kite login in the browser.
3. Copy the full redirected URL from the browser address bar. It should contain `request_token=...`.
4. Paste it into `Redirected URL or request token`.
5. Click `Update access token`.
6. Click `Check` to confirm the token is valid.

## Credentials

Create a local `.env` file from `.env.example` when you are ready to connect broker APIs. Do not share or commit `.env`.

Zerodha Kite Connect requires an active trading account, a developer app, an API key/secret, a redirect URL, and an authenticated access token. Kite Connect provides REST APIs, WebSocket streaming, and historical candle data.

Angel One SmartAPI provides REST-like APIs and the official Python SDK includes examples for session generation, candle data, and WebSocket streaming.

For NSE, use official pages for public/manual checks and authorized NSE Data & Analytics products or broker feeds for automated real-time use.

## Suggested Daily Workflow

1. Update candles and option-chain data.
2. Run watchlist analysis.
3. Review only the highest-quality setups.
4. Check events, results, news, market regime, and option liquidity manually.
5. Record the decision and outcome in your journal.

## Bank Nifty and Sensex scanner

The main navigation groups Analyze, Scans, Krishna Setup, and Backtest under `Stock Research`. Open `Index Scanning` for the shared monitor controls, then choose the `NIFTY`, `Bank Nifty`, or `Sensex` sub-tab. `Start all three` starts independent schedules; individual start/stop buttons still work in each sub-tab. Broker requests are serialized and rate-limited, so an initial refresh can take longer than the selected scan interval. The shared progress table shows the current work item, and the Bank Nifty/Sensex sub-tabs show completed/total refresh and analysis steps. Stop requests take effect after an in-flight broker request returns. The NIFTY job table shows candle, option, IV, entry, and exit activity, including the first gate that blocked each entry horizon.

Scanners use a 15-minute post-close window to check the final Daily candle. A forced NIFTY cycle outside this scan window may refresh data but cannot create entry/exit alerts or trades; an old Daily candle cannot create a new positional entry on a later day. `Analyze now` for Bank Nifty/Sensex refreshes data and shows why a setup passed or waited; outside the scan window it creates no alerts. No order is placed.

After market hours, choose the IST date in `Index Scanning` and click `Download scan log`. The ZIP contains full-day JSON with NIFTY job outcomes and technical gate checks, index run/step timings, alert delivery statuses and errors, read-only trade records, and timestamped option observations. It does not contain broker tokens. Logs created before this version cannot contain timing or option evidence that was not recorded. Share the downloaded file for diagnosis. Use `Send test` beside an index to send a clearly labeled message to its configured Telegram chat without creating a trade. Zerodha token exchange reports completion without waiting for a second quote check; use `Check` in Data Ops to validate separately when needed.

Before starting all three, authenticate Zerodha, then open `Data Ops` and click `Refresh NSE, NFO, BSE, BFO` under `Zerodha Instrument Masters`. Wait until all four show `fresh`. The refresh runs in the background and preserves an existing file if an exchange download fails. Stop index monitors before refreshing masters. A master older than seven days is blocked; refreshing each trading day is preferable. These command-line equivalents remain available:

```powershell
python -m trading_analysis.cli zerodha-instruments --exchange NSE --output data\raw\zerodha\instruments_NSE.csv
python -m trading_analysis.cli zerodha-instruments --exchange NFO --output data\raw\zerodha\instruments_NFO.csv
python -m trading_analysis.cli zerodha-instruments --exchange BSE --output data\raw\zerodha\instruments_BSE.csv
python -m trading_analysis.cli zerodha-instruments --exchange BFO --output data\raw\zerodha\instruments_BFO.csv
```

Set `BANKNIFTY_TELEGRAM_BOT_TOKEN` and `BANKNIFTY_TELEGRAM_CHAT_ID`, and likewise `SENSEX_...`, in local `.env` for separate alert chats. If absent, the configured `NIFTY_...` destination is used. Without either destination, alerts stay in the UI and database with `not_configured` delivery status.

The Bank Nifty sample uses the five dated constituents and weights in `config/index_scanner_leaders.json`; update them when the official factsheet changes. Sensex uses selected leaders without index-weight claims. An OI increase paired with falling option premium is only an *inference* of writing pressure, not proof of trader identity or future direction. Alerts require aligned index trend, two fresh option snapshots, and constituent breadth; missing or stale inputs block alerts. Signals use index spot candles, not option premium P&L. Validate results prospectively before trading.

## App diagnostics and performance

The `Download app log` control above the main tabs exports the selected IST date for all sections: Stock Research, Index Scanning, Purple Touch, Data Ops, and Reports. It includes API timings and outcomes, browser errors, background refresh events, broker queue/network timings, Telegram delivery errors, and the detailed index scan/trade audit. Tokens, API secrets, and chat IDs are redacted; symbol/trade data and local runtime paths are still included. Review the file before sharing. Old logs cannot provide timing details that were not recorded at the time.

Logs are stored in `logs\diagnostics\app-YYYY-MM-DD-PID.jsonl`, rotated at 10 MB. An export returns the latest 50,000 application events and reports truncation or incomplete records. Files are not automatically deleted; archive or remove old diagnostic files after retaining anything needed for review. This does not delete trade or alert history. No logging failure should stop a scanner; `/api/health` reports logging errors.

The header shows the running build and PID. `/api/health` and the exported runtime also identify Python, the project folder, start time, and TLS provider. Use these when comparing ports `8766` and `8767`: the port itself does not change outgoing Telegram certificate validation. Restarting one port does not upgrade another running Python process.

### Telegram certificate errors

`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain` means Python could not verify the HTTPS certificate chain. It is a delivery failure, not a missing trading signal. Install `requirements.txt` using the same Python that starts the server, then restart. The application uses [truststore](https://truststore.readthedocs.io/en/stable/index.html) for native Windows certificate trust. Certificate and hostname verification stay enabled. If a security product or managed network uses a private CA, obtain its trusted PEM certificate through the appropriate administrator and set `TRADING_CA_BUNDLE` in local `.env`; do not disable verification or trust an unknown certificate.

After restart, use `Index Scanning > Send test` and check the result before starting monitors. Failed historical messages are not automatically resent. If delivery still fails, download that day's app log from the affected laptop. A development-laptop success does not establish that the personal laptop's network or trust configuration works.

### Refresh scheduling

All Zerodha data requests share a fair, rate-limited queue. A waiter times out after 60 seconds instead of remaining behind other scanners indefinitely; errors remain visible for retry on the next scheduled cycle. Shared Bank Nifty/Sensex source locks prevent duplicate refreshes without holding an additional global lock across a complete option-chain operation. New closed-candle boundaries invalidate cached refresh slots.

NIFTY background refreshes reuse existing candles and request short overlap windows (3 days for 15-minute, 4 for hourly, 8 for Daily). An empty installation still downloads the configured history. Readiness is checked every 10 seconds; a successful cache is reused until the next expected closed candle. A pending provider candle retries after 20 seconds, and fresh data wakes entry/exit analysis. Bank Nifty/Sensex provider-pending responses have bounded backoff and are shown as `waiting`, not failures. Their shared data queue and cached-data analysis remain independent. The IV job reads cached context rather than downloading all candles again. Explicit manual refresh and historical-range requests retain their existing behavior.

### Index alert timing and research comparisons

Install updated `requirements.txt` after pulling, or use the launcher's Update action. The new `kiteconnect` dependency provides one read-only quote stream shared by all three indices, with rate-limited REST fallback. No order API is used. Keep only one scanner instance connected to a given database.

- Entries still require a completed candle and the existing live option/constituent gates. Signal expiry is 120 seconds for intraday, 300 for swing, and 900 for positional. A fresh spot quote (at most 20 seconds old) is required, with drift no greater than the smaller of 25% of initial risk and 25% of ATR, and remaining reward/risk at least 1.5. Stops and targets are not moved to justify a late entry.
- Signal candle close, detection time, observed spot/time, and reference price are separate fields, displayed in IST. Prices are observations, not broker fills or option strikes. During post-close finalization, a same-session closing quote may be used for swing/daily signals within their expiry window, explicitly labelled `session_close_reference_not_fill`.
- Fresh quote stop/target checks do not wait for candle closure. EMA/holding exits still need closed candles. Candle-only/recovered exits are labelled reference prices; their exact historical touch or execution time is unknown. Pre-entry highs/lows from a partly elapsed entry bar cannot create a retrospective stop/target.
- Telegram delivery is queued separately from analysis. Definite connection failures and rate limits may retry (up to three attempts); ambiguous failures become `delivery_unknown` and are not automatically resent. Verify Telegram before taking action on an unknown delivery. Delayed messages are labelled; messages over 15 minutes old expire into the audit. Historical failed alerts are not blindly replayed.
- `Index Scanning > Spot quotes & OI evidence` shows quote health, technical/confirmation state, and research OI windows of 3, 6, 15 and 30 minutes. OI comparisons require same-session, same-expiry matched contracts with timestamp coverage. Exchange quote time, receipt time, and last observed OI change are distinct; none establishes the exchange's OI publication time or identifies large traders. Unchanged OI is not treated as a fresh directional update.
- These rolling filters do **not** replace the live NIFTY or Bank Nifty/Sensex filters. Collect evidence before choosing a live strategy change.

Under `Index Scanning > NIFTY > Backtests > Index Spot Backtest`, choose `Compare technical / flow / OI`, then select any of the three indices, horizon, dates, costs, target R and holding bars. `Refresh research candles` fetches the required price series (plus the existing benchmark dependency), not historical options. The optional `15m trend / 5m trigger` is intraday research only and uses completed 15-minute trend candles. `Run backtest` compares technical-only, technical plus option price/volume, technical plus rolling OI, and both filters. Review trade counts, missing evidence, net R, drawdown, and the later 30% period, not win rate alone. Download the complete comparison for review.

Option-filter variants only use observations recorded after this version was installed, at or before each decision. Missing history produces skipped signals, not invented evidence or zero-win trades. The controlled baseline uses EMA20/50, RSI14 and candle direction, next-bar entries, a 1 ATR stop and explicit cost assumptions. It is not a complete replay of live scoring/constituent/latency gates and does not model options P&L. Intraday signal cutoff is 14:45; session exits start at 15:00. Reusing the later period to tune parameters invalidates it as an untouched holdout.

Optional UI regression check (local server already running, Chrome installed): `python -m pip install playwright`, then `python tests/browser_index_smoke.py http://127.0.0.1:8766`. It blocks scanner-start and Telegram actions, exercises research and exports, and saves desktop/mobile screenshots under `logs/index-ui-qa`.

In a mocked 09:15-10:14 run with one-minute polling, the candle scheduler issues 17 source refreshes instead of the previous 180, excluding option-chain requests. This tests request reduction, not measured market-network latency. Broker timings in the next live log will show remaining queue, network, or write delays. Temporary Windows file-replacement locks are retried while preserving the previous cache; keeping live data outside OneDrive can avoid sync contention.

## Regression checks

### F&O Pullbacks (v5)

`Stock Research > Backtest` now separates Generic Strategy and Krishna Setup into independent workspaces. Generic bullish/bearish pullbacks support as-of Daily market and sector filters, choosing NIFTY 50/100/200/500. `Stock Research > F&O Pullbacks` adds an independent, persistent paper-alert scanner and optional combined start with the three index monitors. Configure `PULLBACK_TELEGRAM_BOT_TOKEN` and `PULLBACK_TELEGRAM_CHAT_ID` for its separate destination.

Read the [v5 pullback guide and measured backtest comparisons](docs/pullback-scanner-v5.md) for exact rules, symbol-limit meaning, setup/entry/exit lifecycle and limitations. The filtered breakout tests were not profitable after assumed costs; this is paper monitoring, not a validated trading recommendation. Sequential backtest compounding is explicitly labelled as not portfolio performance.

### Test Commands

Run from the project folder using the server's Python environment and Node.js:

```powershell
python -m pip install -r requirements.txt pytest tzdata
python -m pytest -q
node --check web/app.js
node --test tests/web_app.test.cjs
python -m compileall -q trading_analysis tests
git diff --check
```

The suite covers scanner rules, lifecycle persistence, stale-data gates, alert delivery, refresh scheduling, API logging, fair broker queueing, cache-write retries, and browser request races/navigation. Test networking is restricted to loopback so synthetic signals cannot send Telegram messages or broker requests. GitHub Actions runs the suite on Windows with Python 3.12 and 3.14 and Node 22. Browser visual testing and a live-market paper-monitoring session remain separate checks; automated tests do not guarantee trading performance.

## Roadmap

- Phase 1: Local analysis foundation.
- Phase 2: Zerodha read-only historical/instrument sync.
- Phase 3: Angel One read-only fallback feed.
- Phase 4: Option-chain analytics: PCR, OI change, max pain, IV rank, strike liquidity.
- Phase 5: Backtesting and journal integration.
- Phase 6: Paper-trade alerts. Live order execution only after explicit approval and risk controls.
