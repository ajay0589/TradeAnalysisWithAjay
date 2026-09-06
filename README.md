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

Start the local F&O decision dashboard:

```powershell
.\scripts\start_web_ui.ps1
```

Then open `http://127.0.0.1:8765`.

Use this same URL going forward. The script stops any older UI process already listening on port `8765` and starts the latest code on the same stable port. For foreground logs while debugging:

```powershell
.\scripts\start_web_ui.ps1 -Foreground
```

### Purple Touch implementation guide

The visual workflow, confirmed rules, timeframe matrix, entry/exit lifecycle, UI count reconciliation, and Krishna review checklist are documented in [Purple Touch Implementation Guide](docs/Purple_Touch_Implementation_Guide_v1_2.pdf).

The current Weekly-only execution rules, valid/invalid candle diagrams, exact boundaries, live monitoring guide, alert gates, and audit worksheet are documented in [Weekly Purple Touch Validation Guide v1.1](docs/Weekly_Purple_Touch_Validation_Guide_v1_1.pdf). The [previous v1.0 guide](docs/Weekly_Purple_Touch_Validation_Guide_v1_0.pdf) is retained for comparison.

Purple Touch live scanning and monitoring are temporarily restricted to the Weekly profile while its results are validated. Each run refreshes Weekly touch candles, Monthly confirmation candles, 30-minute early/exit candles, and 2-hour final-entry candles. Existing Monthly and Daily history is preserved, but those profiles are not newly scanned or exit-monitored while paused.

Purple Touch entry alerts and trade lifecycle records are retained in local SQLite storage for audit and setup-quality review. The UI combines each created entry signal and its lifecycle into one `Trades Triggered: Open & Closed` table with profile, entry type, status, symbol, date, and sorting controls; it does not automatically purge open or closed Purple Touch trades.

Purple Touch lifecycle precedence:

- Weekly is the only active live profile during validation.
- A symbol can have one Weekly early and one Weekly final lifecycle row open at the same time.
- An early entry may be followed by a final entry for the same mapped Purple Touch setup.
- Once final entry has been recorded, a later early entry from that same mapped touch candle is suppressed, even if the final lifecycle row has subsequently closed.
- A newly mapped Purple Touch candle starts a new setup lifecycle.

The `Purple Touch` tab also includes a cached-candle historical simulation with date range, symbol/profile selection, early/final mode, touch tolerance, score threshold, stop model, target R, holding limit, slippage, costs, capital, and risk-per-trade controls. It uses next-candle-open entry and only candles available at each historical event. Results include profile and entry-type performance, trade rows, data coverage, win rate, expectancy, average R, profit factor, and maximum drawdown.

Risk/reward interpretation:

- `1R` is the initial per-share distance from entry to stop. The default setup stop is the lower of Candle 1 and Candle 2 lows, with a configurable percentage fallback.
- A `2R` target is twice that initial risk. The configured yellow-line exit or maximum holding period may close a simulation before the target.
- A positive average R/expectancy and profit factor above 1 show positive historical results for that sample, but must be reviewed with drawdown, costs, profile consistency, and sample size. A minimum of 30 trades is only a first evidence checkpoint, not proof of future performance.
- The simulation uses underlying-stock candles, not option premiums, IV, theta, margin, strike liquidity, or expiry effects. Its aggregate return does not reserve capital across overlapping symbols.

Regenerate the PDF after material rule changes:

```powershell
python -m pip install reportlab
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

1. Open the Web UI and select `NIFTY Desk`.
2. Choose `Auto`, `Intraday`, `Swing`, or `Positional`.
3. Select weekly/monthly expiries when cached snapshots exist, or leave them on auto.
4. Keep `Include option chain` and `Include IV context` checked when those datasets are available.
5. Click `Run NIFTY Analysis` for market, OI, and IV context.
6. Click `Suggest Strategies` to see strategy candidates with suitability score, reasons, risks, and required confirmations.
7. Use `Payoff` only after reviewing or editing exact legs/premiums; default UI legs are illustrative placeholders.
8. Use `Backtest` as a context-only historical simulation unless historical option premiums are available.

CLI examples:

```powershell
python -m trading_analysis.cli nifty-context --mode intraday --weekly-expiry 2026-07-02
python -m trading_analysis.cli nifty-strategies --mode swing --risk-profile defined
python -m trading_analysis.cli nifty-payoff --spot 24500 --legs '[{"side":"buy","option_type":"CE","strike":24500,"premium":120},{"side":"sell","option_type":"CE","strike":24700,"premium":50}]'
python -m trading_analysis.cli nifty-backtest --strategy nifty_short_strangle --mode swing --days 365 --params "{}"
```

## NIFTY Auto Scan

NIFTY Auto Scan is a local market-hour scheduler for the NIFTY Desk. It uses SQLite storage at `data\db\trading_analysis.db`, enables WAL mode, and stores job history, raw market-data snapshots, derived context, generated alert candidates, and alert outcomes. It is read-only: it refreshes local data caches where configured, analyzes context, records IV observations, creates alert candidates, and never places orders.

Default scan behavior:

- Market hours: Monday-Friday, 09:15 to 15:30 Asia/Kolkata. Holidays are a TODO/configurable calendar.
- Candles: every 1 minute.
- NIFTY option chain: every 3 minutes when refresh is available through the attached analysis service.
- IV snapshot/context: every 5 minutes.
- NIFTY context and opportunity scan: every 1 minute.
- Job cleanup: every 30 minutes, keeping recent job history.

SQLite tables:

- `market_jobs`: every scheduler job run, status, duration, error, and result JSON.
- `nifty_candles`: cached NIFTY OHLCV candles by timeframe, upserted from the same local candle CSVs used by analysis.
- `nifty_option_chain_snapshots`: option-chain snapshot summary rows, including expiry, PCR, max pain, ATM IV, OI totals, and raw CSV file path.
- `nifty_option_chain_rows`: strike-level CE/PE rows linked to each option snapshot, including OI, IV, volume, bid/ask, and build-up label.
- `nifty_iv_observations`: ATM IV observations linked to the option snapshot/source file when available.
- `nifty_alerts`: generated NIFTY context/setup alerts, reasons, risks, trigger/invalidation levels, and acknowledgement state.
- `nifty_context_snapshots`: exact technical, option-chain, IV, summary, warning, and error context captured during Auto Scan.
- `nifty_strategy_candidates`: strategy candidates linked to the context snapshot that produced them.
- `nifty_alert_outcomes`: saved signal-quality backtest outcomes for stored alerts.

Alert traceability:

- Raw input layer: `nifty_candles`, `nifty_option_chain_snapshots`, `nifty_option_chain_rows`, and `nifty_iv_observations`.
- Derived context layer: `nifty_context_snapshots` and linked `nifty_strategy_candidates`.
- Alert layer: `nifty_alerts` stores `context_snapshot_id` and metadata links to the latest candle timestamp, option snapshot id, and IV observation id when available.
- Outcome layer: `nifty_alert_outcomes` stores signal-quality backtest rows for stored alerts.
- Every newly generated alert can link to a `context_snapshot_id`.
- Use the NIFTY Desk `View Context` action to inspect the exact technical/options/IV context behind an alert.
- Recent context snapshots are shown in the NIFTY Desk so you can audit what Auto Scan saw at that time.
- Data Freshness cards in the NIFTY Desk show the latest 15-minute candle, latest option-chain snapshot, latest ATM IV observation, and local DB row counts.
- CSV remains the fallback/source cache for candle and option-chain data. SQLite is the local audit database, not a shared server database.

Web UI usage:

1. Open `NIFTY Desk`.
2. Use `Start Auto Scan` during market hours to start the local scheduler in the web server process.
3. Use `Run Once` to test one forced scan cycle outside market hours.
4. Review `Auto Scan` job history to see what data was refreshed/analyzed.
5. Review `Alerts`; use `Acknowledge` after you have reviewed an alert candidate.

CLI examples:

```powershell
python -m trading_analysis.cli nifty-auto-status
python -m trading_analysis.cli nifty-auto-run-once --force
python -m trading_analysis.cli nifty-auto-start
python -m trading_analysis.cli nifty-alerts --limit 20
python -m trading_analysis.cli nifty-alert-backtest --timeframe 15minute --limit 500 --horizons 3,5,10,15
```

Alert signal-quality backtest:

- Evaluates stored NIFTY alerts against cached NIFTY spot candles.
- Uses the next candle after the alert as the entry reference to avoid look-ahead bias.
- Reports directional accuracy, average forward move, average directional move, max favorable move, and max adverse move by holding window.
- Saves evaluated alert outcomes to local SQLite so each alert can build a review history over time.
- This is not an option strategy P&L backtest. It does not model option premium, IV decay, margin, slippage, or exact strike selection.

Auto-scan limitations:

- Alerts are analysis candidates only and require confirmation and manual risk review.
- Option-chain and IV context depend on cached snapshots/source availability.
- Duplicate alerts are suppressed for 15 minutes unless severity increases or score improves materially.
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

## Roadmap

- Phase 1: Local analysis foundation.
- Phase 2: Zerodha read-only historical/instrument sync.
- Phase 3: Angel One read-only fallback feed.
- Phase 4: Option-chain analytics: PCR, OI change, max pain, IV rank, strike liquidity.
- Phase 5: Backtesting and journal integration.
- Phase 6: Paper-trade alerts. Live order execution only after explicit approval and risk controls.
