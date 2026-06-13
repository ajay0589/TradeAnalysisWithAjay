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
