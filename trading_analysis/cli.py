from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from trading_analysis.analysis.fundamental import analyze_fundamentals
from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.options import (
    OptionChainAnalysis,
    analyze_option_chain,
    load_option_chain_snapshot,
    nearest_expiry,
    option_contracts_for_symbol,
    select_strikes_around_spot,
    write_option_chain_snapshot,
)
from trading_analysis.analysis.relative_strength import (
    RelativeStrengthReport,
    analyze_relative_strength,
    load_sector_map,
    sector_config_for_symbol,
)
from trading_analysis.analysis.scanners import SETUP_LABELS
from trading_analysis.analysis.scoring import combine_signals
from trading_analysis.analysis.technical import analyze_technical
from trading_analysis.analysis.trade_decision import TradeDecision, build_trade_decision
from trading_analysis.brokers.zerodha import (
    ZerodhaKiteClient,
    build_login_url,
    generate_session,
    load_instruments_csv,
    resolve_instrument_token,
    write_candles_csv,
    write_instruments_csv,
)
from trading_analysis.candles import (
    candle_path,
    candle_window,
    fetch_interval,
    normalize_timeframe,
    safe_symbol_filename,
    source_timeframe,
)
from trading_analysis.config import load_settings, load_watchlist, upsert_env_value
from trading_analysis.data_sources.fno_universe import build_fno_watchlist, fno_stock_symbols, write_watchlist
from trading_analysis.data_sources.csv_loader import load_candles, load_candles_for_item
from trading_analysis.data_sources.nse_equity import (
    build_sector_map_from_csv,
    build_sector_map_from_metadata,
    fetch_metadata_for_symbols,
)
from trading_analysis.data_sources.nse_fii_dii import fetch_fii_dii_activity, write_fii_dii_csv
from trading_analysis.reporting.console import render_signal_table
from trading_analysis.strategies.registry import get_strategy, list_strategies, strategy_info
from trading_analysis.web_services import AnalysisService


def main() -> None:
    parser = argparse.ArgumentParser(description="Indian stock/options analysis helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a watchlist from local candle CSVs")
    analyze_parser.add_argument("--watchlist", required=True, help="Path to watchlist JSON")
    analyze_parser.add_argument("--data-dir", required=True, help="Directory containing symbol CSV files")
    analyze_parser.add_argument("--min-score", type=int, default=0, help="Only show symbols at or above this score")
    analyze_parser.add_argument("--output-json", help="Optional path to write full report JSON")
    analyze_parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip watchlist symbols when their candle CSV file is missing",
    )

    subparsers.add_parser("env-check", help="Show which broker credentials are configured")

    login_url_parser = subparsers.add_parser(
        "zerodha-login-url",
        help="Print the Zerodha Kite Connect login URL for the API key in .env",
    )
    login_url_parser.add_argument(
        "--redirect-state",
        help="Optional state value that Zerodha will echo back as redirect_params",
    )

    access_token_parser = subparsers.add_parser(
        "zerodha-access-token",
        help="Exchange a Zerodha request_token for an access_token",
    )
    access_token_parser.add_argument(
        "--request-token",
        required=True,
        help="Paste the request_token value or the full redirected URL containing request_token",
    )
    access_token_parser.add_argument(
        "--write-env",
        action="store_true",
        help="Update ZERODHA_ACCESS_TOKEN in local .env",
    )
    access_token_parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file to update when --write-env is used",
    )

    instruments_parser = subparsers.add_parser(
        "zerodha-instruments",
        help="Download Zerodha instrument master CSV for token lookup",
    )
    instruments_parser.add_argument("--exchange", help="Optional exchange filter, for example NSE or NFO")
    instruments_parser.add_argument(
        "--output",
        default="data/raw/zerodha/instruments.csv",
        help="Output CSV path",
    )

    watchlist_parser = subparsers.add_parser(
        "generate-fno-watchlist",
        help="Generate an all-stock F&O watchlist from Zerodha NFO and NSE instrument masters",
    )
    watchlist_parser.add_argument(
        "--nfo-instruments",
        default="data/raw/zerodha/instruments_NFO.csv",
        help="Cached Zerodha NFO instruments CSV",
    )
    watchlist_parser.add_argument(
        "--nse-instruments",
        default="data/raw/zerodha/instruments_NSE.csv",
        help="Cached Zerodha NSE instruments CSV",
    )
    watchlist_parser.add_argument(
        "--output",
        default="config/watchlist.fno.json",
        help="Output watchlist JSON path",
    )

    sector_map_parser = subparsers.add_parser(
        "generate-sector-map",
        help="Generate stock-to-sector-index mapping for F&O stocks from NSE equity metadata",
    )
    sector_map_parser.add_argument(
        "--watchlist",
        default="config/watchlist.fno.json",
        help="F&O watchlist JSON",
    )
    sector_map_parser.add_argument(
        "--nse-instruments",
        default="data/raw/zerodha/instruments_NSE.csv",
        help="Cached Zerodha NSE instruments CSV",
    )
    sector_map_parser.add_argument(
        "--cache",
        default="data/raw/nse/equity_metadata_cache.json",
        help="NSE quote metadata cache",
    )
    sector_map_parser.add_argument(
        "--output",
        default="config/sector_map.generated.json",
        help="Generated sector-map JSON path",
    )
    sector_map_parser.add_argument("--refresh", action="store_true", help="Refresh cached NSE metadata")
    sector_map_parser.add_argument(
        "--limit",
        type=int,
        help="Optional limit for testing the first N watchlist symbols",
    )
    sector_map_parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.15,
        help="Delay between NSE metadata requests",
    )
    sector_map_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="Timeout for each NSE metadata request",
    )
    sector_map_parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries per symbol before recording an error",
    )

    csv_sector_map_parser = subparsers.add_parser(
        "generate-sector-map-from-csv",
        help="Generate stock-to-sector-index mapping from a local CSV file",
    )
    csv_sector_map_parser.add_argument("--input", required=True, help="CSV with symbol plus sector/industry/index columns")
    csv_sector_map_parser.add_argument(
        "--nse-instruments",
        default="data/raw/zerodha/instruments_NSE.csv",
        help="Cached Zerodha NSE instruments CSV",
    )
    csv_sector_map_parser.add_argument(
        "--watchlist",
        default="config/watchlist.fno.json",
        help="Watchlist used to filter CSV rows to current F&O stocks",
    )
    csv_sector_map_parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include every CSV symbol instead of filtering to the watchlist",
    )
    csv_sector_map_parser.add_argument(
        "--output",
        default="config/sector_map.generated.json",
        help="Generated sector-map JSON path",
    )

    candles_parser = subparsers.add_parser(
        "zerodha-candles",
        help="Download historical candles from Zerodha Kite Connect",
    )
    candles_parser.add_argument("--instrument-token", help="Kite instrument token")
    candles_parser.add_argument("--exchange", default="NSE", help="Exchange used with --tradingsymbol")
    candles_parser.add_argument("--tradingsymbol", help="Trading symbol used to resolve token from cache")
    candles_parser.add_argument(
        "--instrument-cache",
        default="data/raw/zerodha/instruments.csv",
        help="Cached instruments CSV used with --tradingsymbol",
    )
    candles_parser.add_argument("--interval", default="day", help="minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day")
    candles_parser.add_argument("--from-date", required=True, help="Start date/time, for example 2026-01-01 or 2026-01-01 09:15:00")
    candles_parser.add_argument("--to-date", required=True, help="End date/time, for example 2026-06-13 or 2026-06-13 15:30:00")
    candles_parser.add_argument("--include-oi", action="store_true", help="Request open interest data when available")
    candles_parser.add_argument("--continuous", action="store_true", help="Request continuous futures data where supported")
    candles_parser.add_argument(
        "--output",
        required=True,
        help="Output candle CSV path",
    )

    mtf_parser = subparsers.add_parser(
        "update-mtf-candles",
        help="Download day and 60-minute candles for a stock, benchmark, and optional sector index",
    )
    mtf_parser.add_argument("--symbol", required=True, help="NSE symbol, for example RELIANCE")
    mtf_parser.add_argument(
        "--instrument-cache",
        default="data/raw/zerodha/instruments_NSE.csv",
        help="Cached Zerodha NSE instruments CSV",
    )
    mtf_parser.add_argument("--from-date", required=True, help="Start date/time")
    mtf_parser.add_argument("--to-date", required=True, help="End date/time")
    mtf_parser.add_argument(
        "--output-root",
        default="data/raw/candles",
        help="Root candle directory. Day candles go here; 60-minute candles go under 60minute.",
    )
    mtf_parser.add_argument(
        "--benchmark-symbol",
        default="NIFTY 50",
        help="Benchmark symbol from Zerodha NSE instruments",
    )
    mtf_parser.add_argument(
        "--sector-map",
        default="config/sector_map.generated.json",
        help="Optional stock-to-sector-index mapping JSON",
    )
    mtf_parser.add_argument("--skip-benchmark", action="store_true", help="Only download the stock candles")
    mtf_parser.add_argument("--skip-sector", action="store_true", help="Skip sector-index candles")

    bulk_parser = subparsers.add_parser(
        "bulk-fno-candles",
        help="Bulk-download candles for the full F&O stock watchlist",
    )
    bulk_parser.add_argument(
        "--watchlist",
        default="config/watchlist.fno.json",
        help="F&O watchlist JSON",
    )
    bulk_parser.add_argument(
        "--instrument-cache",
        default="data/raw/zerodha/instruments_NSE.csv",
        help="Cached Zerodha NSE instruments CSV",
    )
    bulk_parser.add_argument(
        "--sector-map",
        default="config/sector_map.generated.json",
        help="Optional stock-to-sector-index mapping JSON",
    )
    bulk_parser.add_argument(
        "--output-root",
        default="data/raw/candles",
        help="Root candle directory. Day candles go here; intraday candles go under interval subfolders.",
    )
    bulk_parser.add_argument(
        "--timeframes",
        default="day,60minute,15minute",
        help="Comma-separated timeframes: month, week, day, 60minute, 15minute. Week/month use day candles.",
    )
    bulk_parser.add_argument("--from-date", help="Start date/time. Overrides --days when provided.")
    bulk_parser.add_argument("--to-date", help="End date/time. Defaults to now.")
    bulk_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Days back from --to-date/now when --from-date is not provided.",
    )
    bulk_parser.add_argument("--limit", type=int, help="Optional symbol limit for testing")
    bulk_parser.add_argument("--sleep-seconds", type=float, default=0.35, help="Delay between broker requests")
    bulk_parser.add_argument("--skip-benchmark", action="store_true", help="Skip Nifty 50 candles")
    bulk_parser.add_argument("--skip-sectors", action="store_true", help="Skip mapped sector-index candles")
    bulk_parser.add_argument("--fail-fast", action="store_true", help="Stop on the first download error")

    option_chain_parser = subparsers.add_parser(
        "option-chain",
        help="Analyze an F&O stock option chain using Zerodha quote snapshots",
    )
    option_chain_parser.add_argument("--symbol", required=True, help="F&O stock symbol, for example RELIANCE")
    option_chain_parser.add_argument(
        "--nfo-instruments",
        default="data/raw/zerodha/instruments_NFO.csv",
        help="Cached Zerodha NFO instruments CSV",
    )
    option_chain_parser.add_argument(
        "--expiry",
        help="Expiry date YYYY-MM-DD. Defaults to nearest available expiry.",
    )
    option_chain_parser.add_argument(
        "--strikes-around",
        type=int,
        default=10,
        help="Number of strikes on each side of spot to include",
    )
    option_chain_parser.add_argument(
        "--all-strikes",
        action="store_true",
        help="Include every strike for the selected expiry. This can produce a large table.",
    )
    option_chain_parser.add_argument(
        "--previous-snapshot",
        help="Previous option-chain CSV snapshot used to calculate OI change/build-up",
    )
    option_chain_parser.add_argument(
        "--snapshot-output",
        help="Where to write the current option-chain CSV snapshot",
    )
    option_chain_parser.add_argument(
        "--output-json",
        help="Optional path to write full option-chain analysis JSON",
    )

    fii_dii_parser = subparsers.add_parser(
        "fii-dii",
        help="Fetch NSE FII/FPI and DII trading activity",
    )
    fii_dii_parser.add_argument(
        "--output",
        default="data/raw/nse/fii_dii.csv",
        help="Output CSV path",
    )

    decision_parser = subparsers.add_parser(
        "trade-decision",
        help="Create a structured options trade-decision report for one F&O stock",
    )
    decision_parser.add_argument("--symbol", required=True, help="F&O stock symbol, for example RELIANCE")
    decision_parser.add_argument("--daily-data-dir", default="data/raw/candles", help="Daily candle CSV directory")
    decision_parser.add_argument(
        "--hourly-data-dir",
        default="data/raw/candles/60minute",
        help="60-minute candle CSV directory",
    )
    decision_parser.add_argument(
        "--benchmark-file",
        default="NIFTY_50.csv",
        help="Benchmark candle file inside daily-data-dir",
    )
    decision_parser.add_argument(
        "--sector-map",
        default="config/sector_map.generated.json",
        help="Stock-to-sector-index mapping JSON",
    )
    decision_parser.add_argument(
        "--sector-data-dir",
        default="data/raw/candles",
        help="Sector-index candle CSV directory",
    )
    decision_parser.add_argument(
        "--rs-lookback",
        type=int,
        default=20,
        help="Lookback candles for relative strength",
    )
    decision_parser.add_argument(
        "--nfo-instruments",
        default="data/raw/zerodha/instruments_NFO.csv",
        help="Cached Zerodha NFO instruments CSV",
    )
    decision_parser.add_argument("--expiry", help="Option expiry YYYY-MM-DD. Defaults to nearest available.")
    decision_parser.add_argument("--strikes-around", type=int, default=10, help="Option strikes around spot")
    decision_parser.add_argument(
        "--previous-snapshot",
        help="Previous option-chain CSV snapshot for OI build-up classification",
    )
    decision_parser.add_argument(
        "--skip-option-chain",
        action="store_true",
        help="Build decision from price/relative strength only",
    )
    decision_parser.add_argument("--output-json", help="Optional path to write report JSON")

    scan_opportunities_parser = subparsers.add_parser(
        "scan-opportunities",
        help="Scan cached F&O candles for richer opportunity setup types",
    )
    scan_opportunities_parser.add_argument(
        "--type",
        dest="opportunity_type",
        default="all",
        choices=["all", *SETUP_LABELS.keys()],
        help="Opportunity setup type to scan",
    )
    scan_opportunities_parser.add_argument(
        "--direction",
        choices=["bullish", "bearish", "neutral", "watch", "avoid"],
        help="Optional direction filter",
    )
    scan_opportunities_parser.add_argument("--timeframe", default="day", help="day, 60minute, 15minute, week, or month")
    scan_opportunities_parser.add_argument("--days", type=int, help="Number of calendar days to analyze")
    scan_opportunities_parser.add_argument("--from-date", help="Start date YYYY-MM-DD")
    scan_opportunities_parser.add_argument("--to-date", help="End date YYYY-MM-DD")
    scan_opportunities_parser.add_argument("--limit", type=int, default=50, help="Maximum rows to show")
    scan_opportunities_parser.add_argument("--output-json", help="Optional path to write full scan JSON")

    backtest_parser = subparsers.add_parser(
        "backtest-krishna-setup",
        help="Backtest the Krishna bullish daily setup using cached candles",
    )
    backtest_parser.add_argument("--symbol", help="Optional stock/index symbol. Omit to test the F&O watchlist.")
    backtest_parser.add_argument("--days", type=int, default=730, help="Number of calendar days to backtest")
    backtest_parser.add_argument("--from-date", help="Start date YYYY-MM-DD")
    backtest_parser.add_argument("--to-date", help="End date YYYY-MM-DD")
    backtest_parser.add_argument("--holding-days", type=int, default=10, help="Fixed futures-style holding period")
    backtest_parser.add_argument(
        "--limit-symbols",
        default="50",
        help="Number of watchlist symbols to test, or all. Ignored when --symbol is provided.",
    )
    backtest_parser.add_argument("--output-json", help="Optional path to write full backtest JSON")

    subparsers.add_parser("list-strategies", help="List parameterized backtest strategies")

    strategy_info_parser = subparsers.add_parser("strategy-info", help="Show strategy metadata and default parameters")
    strategy_info_parser.add_argument("--strategy", required=True, help="Strategy id, for example bullish_breakout")

    generic_backtest_parser = subparsers.add_parser(
        "backtest-strategy",
        help="Backtest any registered parameterized strategy using cached candles",
    )
    generic_backtest_parser.add_argument("--strategy", required=True, help="Strategy id, for example bullish_breakout")
    generic_backtest_parser.add_argument("--symbols", help="Optional comma-separated symbols. Omit to test watchlist.")
    generic_backtest_parser.add_argument("--timeframe", default="day", help="day, 60minute, 15minute, week, or month")
    generic_backtest_parser.add_argument("--days", type=int, help="Number of calendar days to backtest")
    generic_backtest_parser.add_argument("--from-date", help="Start date YYYY-MM-DD")
    generic_backtest_parser.add_argument("--to-date", help="End date YYYY-MM-DD")
    generic_backtest_parser.add_argument("--params", default="{}", help="Strategy parameter JSON object")
    generic_backtest_parser.add_argument("--backtest-params", default="{}", help="Backtest parameter JSON object")
    generic_backtest_parser.add_argument(
        "--limit-symbols",
        default="50",
        help="Number of watchlist symbols to test, or all. Ignored when --symbols is provided.",
    )
    generic_backtest_parser.add_argument("--output-json", help="Optional path to write full backtest JSON")

    args = parser.parse_args()
    if args.command == "analyze":
        run_analyze(args)
    elif args.command == "env-check":
        run_env_check()
    elif args.command == "zerodha-login-url":
        run_zerodha_login_url(args)
    elif args.command == "zerodha-access-token":
        run_zerodha_access_token(args)
    elif args.command == "zerodha-instruments":
        run_zerodha_instruments(args)
    elif args.command == "generate-fno-watchlist":
        run_generate_fno_watchlist(args)
    elif args.command == "generate-sector-map":
        run_generate_sector_map(args)
    elif args.command == "generate-sector-map-from-csv":
        run_generate_sector_map_from_csv(args)
    elif args.command == "zerodha-candles":
        run_zerodha_candles(args)
    elif args.command == "update-mtf-candles":
        run_update_mtf_candles(args)
    elif args.command == "bulk-fno-candles":
        run_bulk_fno_candles(args)
    elif args.command == "option-chain":
        run_option_chain(args)
    elif args.command == "fii-dii":
        run_fii_dii(args)
    elif args.command == "trade-decision":
        run_trade_decision(args)
    elif args.command == "scan-opportunities":
        run_scan_opportunities(args)
    elif args.command == "backtest-krishna-setup":
        run_backtest_krishna_setup(args)
    elif args.command == "list-strategies":
        run_list_strategies()
    elif args.command == "strategy-info":
        run_strategy_info(args)
    elif args.command == "backtest-strategy":
        run_backtest_strategy(args)


def run_analyze(args: argparse.Namespace) -> None:
    watchlist = load_watchlist(args.watchlist)
    signals = []
    missing_files: list[str] = []

    for item in watchlist:
        try:
            candles = load_candles_for_item(item, args.data_dir)
        except FileNotFoundError:
            missing_files.append(str(Path(args.data_dir) / (item.data_file or f"{item.symbol}.csv")))
            if args.skip_missing:
                continue
            continue
        technical = analyze_technical(candles)
        fundamental = analyze_fundamentals(item.fundamentals)
        notes = tuple(note for note in (item.notes,) if note)
        signals.append(combine_signals(item.symbol, technical, fundamental, notes=notes))

    if missing_files and not args.skip_missing:
        raise SystemExit(
            "Missing candle files:\n"
            + "\n".join(f"- {path}" for path in missing_files)
            + "\n\nFetch the missing candles, remove those symbols from the watchlist, "
            "or rerun with --skip-missing."
        )
    if not signals:
        raise SystemExit("No symbols were analyzed. Check your watchlist and candle CSV files.")
    if missing_files:
        print("Skipped missing candle files:")
        print("\n".join(f"- {path}" for path in missing_files))
        print()

    filtered = [signal for signal in signals if signal.score >= args.min_score]
    print(render_signal_table(filtered))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([asdict(signal) for signal in filtered], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nWrote report: {output_path}")


def run_env_check() -> None:
    settings = load_settings()
    creds = settings.broker_credentials
    status = {
        "trading_mode": settings.trading_mode,
        "zerodha_api_key": bool(creds.zerodha_api_key),
        "zerodha_api_secret": bool(creds.zerodha_api_secret),
        "zerodha_access_token": bool(creds.zerodha_access_token),
        "angel_one_api_key": bool(creds.angel_one_api_key),
        "angel_one_client_code": bool(creds.angel_one_client_code),
        "angel_one_pin": bool(creds.angel_one_pin),
        "angel_one_totp_secret": bool(creds.angel_one_totp_secret),
    }
    print(json.dumps(status, indent=2))


def run_zerodha_login_url(args: argparse.Namespace) -> None:
    creds = load_settings().broker_credentials
    if not creds.zerodha_api_key:
        raise SystemExit("Missing ZERODHA_API_KEY in .env")
    redirect_params = {"state": args.redirect_state} if args.redirect_state else None
    print(build_login_url(creds.zerodha_api_key, redirect_params=redirect_params))


def run_zerodha_access_token(args: argparse.Namespace) -> None:
    creds = load_settings().broker_credentials
    missing = [
        name
        for name, value in {
            "ZERODHA_API_KEY": creds.zerodha_api_key,
            "ZERODHA_API_SECRET": creds.zerodha_api_secret,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing Zerodha credentials in .env: {', '.join(missing)}")

    session = generate_session(
        api_key=creds.zerodha_api_key or "",
        api_secret=creds.zerodha_api_secret or "",
        request_token=args.request_token,
    )
    access_token = session.get("access_token")
    if not access_token:
        raise SystemExit("Zerodha response did not contain access_token.")

    if args.write_env:
        upsert_env_value(args.env_file, "ZERODHA_ACCESS_TOKEN", access_token)
        print(f"Updated ZERODHA_ACCESS_TOKEN in {args.env_file}")
    else:
        print(access_token)


def run_zerodha_instruments(args: argparse.Namespace) -> None:
    client = _zerodha_client_from_settings()
    instruments = client.instruments(args.exchange)
    write_instruments_csv(args.output, instruments)
    scope = args.exchange.upper() if args.exchange else "all exchanges"
    print(f"Wrote {len(instruments)} Zerodha instruments for {scope}: {args.output}")


def run_generate_fno_watchlist(args: argparse.Namespace) -> None:
    nfo_instruments = load_instruments_csv(args.nfo_instruments)
    nse_instruments = load_instruments_csv(args.nse_instruments)
    symbols = fno_stock_symbols(nfo_instruments, nse_instruments)
    watchlist = build_fno_watchlist(symbols, source=f"{args.nfo_instruments} + {args.nse_instruments}")
    write_watchlist(args.output, watchlist)
    print(f"Wrote {len(symbols)} F&O stock symbols: {args.output}")


def run_generate_sector_map(args: argparse.Namespace) -> None:
    watchlist = load_watchlist(args.watchlist)
    symbols = [item.symbol for item in watchlist]
    if args.limit:
        symbols = symbols[: args.limit]
    nse_instruments = load_instruments_csv(args.nse_instruments)
    metadata = fetch_metadata_for_symbols(
        symbols,
        cache_path=args.cache,
        refresh=args.refresh,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    sector_map = build_sector_map_from_metadata(symbols, metadata, nse_instruments)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sector_map, indent=2), encoding="utf-8")
    print(
        f"Wrote sector map: {args.output} "
        f"({len(sector_map['symbols'])} mapped, {len(sector_map['unmapped'])} unmapped)"
    )


def run_generate_sector_map_from_csv(args: argparse.Namespace) -> None:
    symbols_filter = None
    if not args.include_all:
        symbols_filter = [item.symbol for item in load_watchlist(args.watchlist)]
    nse_instruments = load_instruments_csv(args.nse_instruments)
    sector_map = build_sector_map_from_csv(
        path=args.input,
        nse_instruments=nse_instruments,
        symbols_filter=symbols_filter,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sector_map, indent=2), encoding="utf-8")
    print(
        f"Wrote sector map: {args.output} "
        f"({len(sector_map['symbols'])} mapped, {len(sector_map['unmapped'])} unmapped)"
    )


def run_zerodha_candles(args: argparse.Namespace) -> None:
    client = _zerodha_client_from_settings()
    instrument_token = args.instrument_token
    if not instrument_token:
        if not args.tradingsymbol:
            raise SystemExit("Pass either --instrument-token or --tradingsymbol.")
        instruments = load_instruments_csv(args.instrument_cache)
        instrument_token = resolve_instrument_token(instruments, args.exchange, args.tradingsymbol)

    candles = client.historical_candles(
        instrument_token=instrument_token,
        interval=args.interval,
        from_time=_parse_cli_datetime(args.from_date),
        to_time=_parse_cli_datetime(args.to_date),
        include_oi=args.include_oi,
        continuous=args.continuous,
    )
    write_candles_csv(args.output, candles)
    print(f"Wrote {len(candles)} candles: {args.output}")


def run_update_mtf_candles(args: argparse.Namespace) -> None:
    client = _zerodha_client_from_settings()
    instruments = load_instruments_csv(args.instrument_cache)
    targets = [(args.symbol.upper(), _safe_symbol_filename(args.symbol))]

    if not args.skip_benchmark:
        targets.append((args.benchmark_symbol.upper(), _safe_symbol_filename(args.benchmark_symbol)))

    if not args.skip_sector:
        sector_config = sector_config_for_symbol(load_sector_map(args.sector_map), args.symbol)
        if sector_config:
            targets.append((sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))

    seen: set[tuple[str, str]] = set()
    unique_targets = []
    for tradingsymbol, file_stem in targets:
        key = (tradingsymbol, file_stem)
        if key not in seen:
            unique_targets.append(key)
            seen.add(key)

    for tradingsymbol, file_stem in unique_targets:
        token = resolve_instrument_token(instruments, "NSE", tradingsymbol)
        for interval in ("day", "60minute"):
            output = _mtf_output_path(args.output_root, interval, file_stem)
            candles = client.historical_candles(
                instrument_token=token,
                interval=interval,
                from_time=_parse_cli_datetime(args.from_date),
                to_time=_parse_cli_datetime(args.to_date),
            )
            write_candles_csv(output, candles)
            print(f"Wrote {len(candles)} {interval} candles for {tradingsymbol}: {output}")


def run_bulk_fno_candles(args: argparse.Namespace) -> None:
    client = _zerodha_client_from_settings()
    instruments = load_instruments_csv(args.instrument_cache)
    watchlist = load_watchlist(args.watchlist)
    symbols = [item.symbol for item in watchlist]
    if args.limit:
        symbols = symbols[: args.limit]

    source_timeframes = _bulk_source_timeframes(args.timeframes)
    window = candle_window(from_date=args.from_date, to_date=args.to_date, days=None if args.from_date else args.days)
    if window.from_time is None:
        window = type(window)(
            from_time=window.to_time - timedelta(days=args.days),
            to_time=window.to_time,
            days=args.days,
        )

    targets = [(symbol, safe_symbol_filename(symbol)) for symbol in symbols]
    if not args.skip_benchmark:
        targets.append(("NIFTY 50", "NIFTY_50"))
    if not args.skip_sectors:
        targets.extend(_sector_targets_for_symbols(symbols, args.sector_map))
    targets = _dedupe_targets(targets)

    print(
        f"Downloading {len(targets)} targets x {len(source_timeframes)} timeframe(s) "
        f"from {window.from_time} to {window.to_time}"
    )

    successes = 0
    errors: list[str] = []
    for tradingsymbol, file_stem in targets:
        for timeframe in source_timeframes:
            try:
                token = resolve_instrument_token(instruments, "NSE", tradingsymbol)
                candles = client.historical_candles(
                    instrument_token=token,
                    interval=fetch_interval(timeframe),
                    from_time=window.from_time,
                    to_time=window.to_time,
                )
                output = candle_path(args.output_root, timeframe, file_stem)
                write_candles_csv(output, candles)
                successes += 1
                print(f"Wrote {len(candles)} {timeframe} candles for {tradingsymbol}: {output}")
            except Exception as exc:
                message = f"{tradingsymbol} {timeframe}: {exc}"
                errors.append(message)
                print(f"ERROR {message}")
                if args.fail_fast:
                    raise
            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)

    print(f"\nCompleted {successes} downloads with {len(errors)} error(s).")
    if errors:
        print("Errors:")
        for error in errors[:50]:
            print(f"- {error}")


def run_option_chain(args: argparse.Namespace) -> None:
    analysis = _build_option_chain_analysis(
        symbol=args.symbol,
        nfo_instruments_path=args.nfo_instruments,
        expiry=_parse_cli_date(args.expiry) if args.expiry else None,
        strikes_around=args.strikes_around,
        all_strikes=args.all_strikes,
        previous_snapshot=args.previous_snapshot,
    )

    print(_render_option_chain_summary(analysis))
    print()
    print(_render_option_chain_rows(analysis))

    snapshot_output = args.snapshot_output or f"data/raw/option_chain/{analysis.symbol}_{analysis.expiry.isoformat()}.csv"
    write_option_chain_snapshot(snapshot_output, analysis)
    print(f"\nWrote option-chain snapshot: {snapshot_output}")

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(asdict(analysis), indent=2, default=str), encoding="utf-8")
        print(f"Wrote option-chain JSON: {output_path}")


def run_fii_dii(args: argparse.Namespace) -> None:
    rows = fetch_fii_dii_activity()
    write_fii_dii_csv(args.output, rows)
    print(f"Wrote {len(rows)} FII/DII rows: {args.output}")
    if rows:
        print(_plain_table(list(rows[0].keys()), [[str(row.get(key, "")) for key in rows[0].keys()] for row in rows]))


def run_trade_decision(args: argparse.Namespace) -> None:
    symbol = args.symbol.upper()
    daily_candles = load_candles(Path(args.daily_data_dir) / f"{symbol}.csv")
    daily_technical = analyze_technical(daily_candles)
    daily_structure = analyze_market_structure(daily_candles)

    hourly_candles = _load_optional_candles(Path(args.hourly_data_dir) / f"{symbol}.csv")
    hourly_technical = analyze_technical(hourly_candles) if hourly_candles and len(hourly_candles) >= 20 else None
    hourly_structure = (
        analyze_market_structure(hourly_candles) if hourly_candles and len(hourly_candles) >= 10 else None
    )

    relative_strength = _build_relative_strength_report(args, daily_candles)
    option_chain = None
    extra_warnings: list[str] = []
    if not args.skip_option_chain:
        try:
            option_chain = _build_option_chain_analysis(
                symbol=symbol,
                nfo_instruments_path=args.nfo_instruments,
                expiry=_parse_cli_date(args.expiry) if args.expiry else None,
                strikes_around=args.strikes_around,
                all_strikes=False,
                previous_snapshot=args.previous_snapshot,
            )
        except Exception as exc:
            extra_warnings.append(f"Option-chain fetch failed: {exc}")

    decision = build_trade_decision(
        symbol=symbol,
        daily_technical=daily_technical,
        daily_structure=daily_structure,
        hourly_technical=hourly_technical,
        hourly_structure=hourly_structure,
        relative_strength=relative_strength,
        option_chain=option_chain,
    )
    print(
        _render_trade_decision_report(
            decision=decision,
            daily_technical=daily_technical,
            daily_structure=daily_structure,
            hourly_technical=hourly_technical,
            hourly_structure=hourly_structure,
            relative_strength=relative_strength,
            option_chain=option_chain,
            extra_warnings=extra_warnings,
        )
    )

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "decision": asdict(decision),
            "daily_technical": asdict(daily_technical),
            "daily_structure": asdict(daily_structure),
            "hourly_technical": asdict(hourly_technical) if hourly_technical else None,
            "hourly_structure": asdict(hourly_structure) if hourly_structure else None,
            "relative_strength": asdict(relative_strength),
            "option_chain": asdict(option_chain) if option_chain else None,
            "warnings": extra_warnings,
        }
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote trade-decision JSON: {output_path}")


def run_scan_opportunities(args: argparse.Namespace) -> None:
    service = AnalysisService()
    payload = service.scan_opportunities(
        opportunity_type=args.opportunity_type,
        direction=args.direction,
        timeframe=args.timeframe,
        from_date=args.from_date,
        to_date=args.to_date,
        days=args.days,
        limit=args.limit,
    )
    print(_render_scan_opportunities(payload))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote scan-opportunities JSON: {output_path}")


def run_backtest_krishna_setup(args: argparse.Namespace) -> None:
    service = AnalysisService()
    payload = service.backtest_krishna_setup(
        symbol=args.symbol,
        days=args.days,
        from_date=args.from_date,
        to_date=args.to_date,
        holding_days=args.holding_days,
        limit_symbols=_parse_optional_limit(args.limit_symbols),
    )
    print(_render_backtest(payload))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote backtest JSON: {output_path}")


def run_list_strategies() -> None:
    rows = [
        [
            item["strategy_id"],
            item["label"],
            item["direction"],
            item["default_timeframe"],
            str(item["min_candles"]),
        ]
        for item in list_strategies()
    ]
    print(_plain_table(["Strategy", "Label", "Direction", "Timeframe", "Min Candles"], rows))


def run_strategy_info(args: argparse.Namespace) -> None:
    payload = strategy_info(args.strategy)
    print(json.dumps(payload, indent=2, default=str))


def run_backtest_strategy(args: argparse.Namespace) -> None:
    service = AnalysisService()
    symbols = _split_cli_symbols(args.symbols)
    payload = service.backtest_strategy(
        strategy_id=args.strategy,
        symbols=symbols,
        timeframe=args.timeframe,
        from_date=args.from_date,
        to_date=args.to_date,
        days=args.days,
        strategy_params=_parse_json_object(args.params, "--params"),
        backtest_params=_parse_json_object(args.backtest_params, "--backtest-params"),
        limit_symbols=_parse_optional_limit(args.limit_symbols),
    )
    print(_render_generic_backtest(payload))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote strategy backtest JSON: {output_path}")


def _zerodha_client_from_settings() -> ZerodhaKiteClient:
    creds = load_settings().broker_credentials
    missing = [
        name
        for name, value in {
            "ZERODHA_API_KEY": creds.zerodha_api_key,
            "ZERODHA_ACCESS_TOKEN": creds.zerodha_access_token,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing Zerodha credentials in .env: {', '.join(missing)}")
    return ZerodhaKiteClient(
        api_key=creds.zerodha_api_key or "",
        access_token=creds.zerodha_access_token or "",
    )


def _parse_cli_datetime(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def _parse_cli_date(value: str) -> date:
    return date.fromisoformat(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_json_object(value: str, label: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label} must be a JSON object.")
    return parsed


def _split_cli_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    symbols = [part.strip() for part in value.split(",") if part.strip()]
    return symbols or None


def _load_optional_candles(path: Path):
    try:
        return load_candles(path)
    except FileNotFoundError:
        return None


def _build_relative_strength_report(args: argparse.Namespace, daily_candles) -> RelativeStrengthReport:
    benchmark_candles = _load_optional_candles(Path(args.daily_data_dir) / args.benchmark_file)
    sector_config = sector_config_for_symbol(load_sector_map(args.sector_map), args.symbol)
    sector_candles = None
    if sector_config:
        sector_candles = _load_optional_candles(Path(args.sector_data_dir) / sector_config["data_file"])
    return analyze_relative_strength(
        stock_candles=daily_candles,
        nifty_candles=benchmark_candles,
        sector_candles=sector_candles,
        lookback=args.rs_lookback,
    )


def _build_option_chain_analysis(
    symbol: str,
    nfo_instruments_path: str,
    expiry: date | None,
    strikes_around: int,
    all_strikes: bool,
    previous_snapshot: str | None,
) -> OptionChainAnalysis:
    client = _zerodha_client_from_settings()
    nfo_instruments = load_instruments_csv(nfo_instruments_path)
    symbol = symbol.upper()
    contracts = option_contracts_for_symbol(nfo_instruments, symbol)
    if not contracts:
        raise ValueError(f"No NFO option contracts found for {symbol}. Refresh instruments_NFO.csv or check symbol.")

    selected_expiry = expiry or nearest_expiry(contracts)
    contracts = option_contracts_for_symbol(nfo_instruments, symbol, expiry=selected_expiry)
    if not contracts:
        raise ValueError(f"No option contracts found for {symbol} expiry {selected_expiry}.")

    spot_key = f"NSE:{symbol}"
    spot_quote = client.quotes([spot_key]).get(spot_key, {})
    spot_price = _optional_float(spot_quote.get("last_price"))
    selected_contracts = contracts if all_strikes else select_strikes_around_spot(
        contracts,
        spot_price=spot_price,
        strikes_around=strikes_around,
    )
    quotes = client.quotes([contract.kite_key for contract in selected_contracts])
    previous_rows = load_option_chain_snapshot(previous_snapshot) if previous_snapshot else {}
    return analyze_option_chain(
        symbol=symbol,
        expiry=selected_expiry,
        contracts=selected_contracts,
        quotes=quotes,
        spot_price=spot_price,
        previous_rows=previous_rows,
    )


def _safe_symbol_filename(symbol: str) -> str:
    return symbol.upper().replace(" ", "_").replace("/", "_").replace("&", "AND")


def _mtf_output_path(output_root: str, interval: str, file_stem: str) -> Path:
    root = Path(output_root)
    if interval == "day":
        return root / f"{file_stem}.csv"
    return root / interval / f"{file_stem}.csv"


def _bulk_source_timeframes(value: str) -> list[str]:
    order = ["day", "60minute", "15minute"]
    normalized = {source_timeframe(normalize_timeframe(part)) for part in value.split(",") if part.strip()}
    return [timeframe for timeframe in order if timeframe in normalized]


def _sector_targets_for_symbols(symbols: list[str], sector_map_path: str) -> list[tuple[str, str]]:
    sector_map = load_sector_map(sector_map_path)
    targets = []
    for symbol in symbols:
        sector_config = sector_config_for_symbol(sector_map, symbol)
        if sector_config:
            targets.append((sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))
    return targets


def _dedupe_targets(targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    output = []
    seen: set[tuple[str, str]] = set()
    for tradingsymbol, file_stem in targets:
        key = (tradingsymbol.upper(), file_stem)
        if key in seen:
            continue
        output.append(key)
        seen.add(key)
    return output


def _render_option_chain_summary(analysis) -> str:
    headers = [
        "Symbol",
        "Expiry",
        "Spot",
        "Contracts",
        "ATM IV",
        "IV Chg",
        "Volume",
        "OI % Chg",
        "PCR OI",
        "Max Pain",
        "High CE OI",
        "High PE OI",
    ]
    rows = [
        [
            analysis.symbol,
            analysis.expiry.isoformat(),
            _fmt(analysis.spot_price),
            str(analysis.contract_count),
            _fmt(analysis.atm_iv),
            _fmt(analysis.atm_iv_change),
            str(analysis.total_volume),
            _fmt(analysis.total_oi_change_percent),
            _fmt(analysis.pcr_oi),
            _fmt(analysis.max_pain),
            _fmt(analysis.highest_call_oi_strike),
            _fmt(analysis.highest_put_oi_strike),
        ]
    ]
    return _plain_table(headers, rows)


def _render_option_chain_rows(analysis) -> str:
    headers = ["Strike", "Type", "LTP", "Chg", "IV", "IV Chg", "OI", "OI Chg", "OI % Chg", "Volume", "Bid", "Ask", "Build-up"]
    rows = [
        [
            _fmt(row.strike),
            row.option_type,
            _fmt(row.last_price),
            _fmt(row.price_change),
            _fmt(row.implied_volatility),
            _fmt(row.iv_change),
            str(row.oi),
            "-" if row.oi_change is None else str(row.oi_change),
            _fmt(row.oi_change_percent),
            str(row.volume),
            _fmt(row.bid_price),
            _fmt(row.ask_price),
            row.buildup,
        ]
        for row in analysis.rows
    ]
    return _plain_table(headers, rows)


def _render_scan_opportunities(payload: dict) -> str:
    summary = payload.get("summary") or {}
    lines = [
        f"Scan type: {payload.get('type')} | Direction: {payload.get('direction') or 'any'} | "
        f"Timeframe: {payload.get('timeframe_label')}",
        f"Analyzed: {payload.get('analyzed_symbols')} | Matched: {payload.get('matched_symbols')} | "
        f"Shown: {len(payload.get('results') or [])} | Errors: {summary.get('error_count', 0)}",
    ]
    rows = []
    for row in payload.get("results") or []:
        zone = row.get("trigger_zone") or row.get("target_zone") or "-"
        rows.append(
            [
                row.get("symbol", "-"),
                row.get("setup", row.get("setup_type", "-")),
                row.get("direction", "-"),
                str(row.get("score", "-")),
                row.get("confidence", "-"),
                _fmt(row.get("close")),
                _fmt(row.get("support")),
                _fmt(row.get("resistance")),
                _fmt(row.get("invalidation")),
                _short_text(zone, 58),
                _short_text(row.get("reasons_text") or "; ".join(row.get("reasons") or []), 72),
            ]
        )
    if rows:
        lines.extend(
            [
                "",
                _plain_table(
                    [
                        "Symbol",
                        "Setup",
                        "Direction",
                        "Score",
                        "Confidence",
                        "Close",
                        "Support",
                        "Resistance",
                        "Invalidation",
                        "Trigger/Zone",
                        "Reasons",
                    ],
                    rows,
                ),
            ]
        )
    else:
        lines.append("\nNo matching setups found.")
    points = summary.get("points") or []
    if points:
        lines.extend(["", "Summary:"])
        lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def _render_backtest(payload: dict) -> str:
    metrics = payload.get("metrics") or {}
    lines = [
        f"Backtest: {payload.get('setup_label')} | Timeframe: {payload.get('timeframe_label')} | "
        f"Hold: {payload.get('holding_days')} day(s)",
        f"Analyzed: {payload.get('analyzed_symbols')} | Signals: {payload.get('signal_count')} | "
        f"Trades: {payload.get('trade_count')} | Errors: {len(payload.get('errors') or [])}",
        "",
        _plain_table(
            ["Trades", "Win %", "Avg %", "Expectancy %", "Profit Factor", "Max DD %", "Ending %"],
            [
                [
                    str(metrics.get("trades", 0)),
                    _fmt(metrics.get("win_rate")),
                    _fmt(metrics.get("avg_return")),
                    _fmt(metrics.get("expectancy")),
                    _fmt(metrics.get("profit_factor")),
                    _fmt(metrics.get("max_drawdown")),
                    _fmt(metrics.get("ending_return")),
                ]
            ],
        ),
    ]
    forward_rows = [
        [
            f"{row.get('horizon_days')}d",
            str(row.get("signals", 0)),
            str(row.get("successes", 0)),
            _fmt(row.get("accuracy")),
            _fmt(row.get("avg_forward_return")),
        ]
        for row in payload.get("forward_accuracy") or []
    ]
    if forward_rows:
        lines.extend(["", "Forward accuracy:", _plain_table(["Horizon", "Signals", "Wins", "Accuracy %", "Avg %"], forward_rows)])

    bucket_rows = [
        [
            row.get("score_bucket", "-"),
            str(row.get("trades", 0)),
            _fmt(row.get("win_rate")),
            _fmt(row.get("avg_return")),
            _fmt(row.get("expectancy")),
        ]
        for row in payload.get("confidence_buckets") or []
    ]
    if bucket_rows:
        lines.extend(["", "Score buckets:", _plain_table(["Score", "Trades", "Win %", "Avg %", "Expectancy %"], bucket_rows)])

    symbol_rows = [
        [
            row.get("symbol", "-"),
            row.get("status", "-"),
            str(row.get("signals", 0)),
            str(row.get("trades", 0)),
            _fmt(row.get("win_rate")),
            _fmt(row.get("avg_return")),
        ]
        for row in (payload.get("symbol_results") or [])[:20]
    ]
    if symbol_rows:
        lines.extend(["", "Top symbols:", _plain_table(["Symbol", "Status", "Signals", "Trades", "Win %", "Avg %"], symbol_rows)])

    points = (payload.get("summary") or {}).get("points") or []
    if points:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def _render_generic_backtest(payload: dict) -> str:
    metrics = payload.get("metrics") or {}
    strategy = payload.get("strategy") or {}
    lines = [
        f"Backtest: {strategy.get('label') or payload.get('strategy_id')} | Timeframe: {payload.get('timeframe_label') or payload.get('timeframe')}",
        f"Analyzed: {payload.get('analyzed_symbols')} | Signals: {payload.get('signal_count')} | "
        f"Trades: {payload.get('trade_count')} | Errors: {len(payload.get('errors') or [])}",
        "",
        _plain_table(
            ["Trades", "Win %", "Avg %", "Expectancy %", "Profit Factor", "Max DD %", "Ending %", "Avg R"],
            [
                [
                    str(metrics.get("trades", 0)),
                    _fmt(metrics.get("win_rate")),
                    _fmt(metrics.get("avg_return")),
                    _fmt(metrics.get("expectancy")),
                    _fmt(metrics.get("profit_factor")),
                    _fmt(metrics.get("max_drawdown")),
                    _fmt(metrics.get("ending_return")),
                    _fmt(metrics.get("avg_r_multiple")),
                ]
            ],
        ),
    ]

    forward_rows = [
        [
            f"{row.get('horizon_bars')} bars",
            str(row.get("signals", 0)),
            str(row.get("successes", 0)),
            _fmt(row.get("accuracy")),
            _fmt(row.get("avg_forward_return")),
        ]
        for row in payload.get("forward_accuracy") or []
    ]
    if forward_rows:
        lines.extend(["", "Forward accuracy:", _plain_table(["Horizon", "Signals", "Wins", "Accuracy %", "Avg %"], forward_rows)])

    bucket_rows = [
        [
            row.get("score_bucket", "-"),
            str(row.get("trades", 0)),
            _fmt(row.get("win_rate")),
            _fmt(row.get("avg_return")),
            _fmt(row.get("expectancy")),
        ]
        for row in payload.get("score_buckets") or []
    ]
    if bucket_rows:
        lines.extend(["", "Score buckets:", _plain_table(["Score", "Trades", "Win %", "Avg %", "Expectancy %"], bucket_rows)])

    symbol_rows = [
        [
            row.get("symbol", "-"),
            str(row.get("trades", 0)),
            _fmt(row.get("win_rate")),
            _fmt(row.get("avg_return")),
            _fmt(row.get("profit_factor")),
            _fmt(row.get("ending_return")),
        ]
        for row in (payload.get("symbol_performance") or [])[:20]
    ]
    if symbol_rows:
        lines.extend(
            [
                "",
                "Top symbols:",
                _plain_table(["Symbol", "Trades", "Win %", "Avg %", "Profit Factor", "Ending %"], symbol_rows),
            ]
        )

    points = (payload.get("summary") or {}).get("points") or []
    if points:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _short_text(value: object, limit: int) -> str:
    text = str(value or "-")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _parse_optional_limit(value: str | None) -> int | None:
    cleaned = (value or "").strip().lower()
    if cleaned in {"", "all"}:
        return None
    return int(cleaned)


def _plain_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    header_line = " | ".join(headers[index].ljust(widths[index]) for index in range(len(headers)))
    separator = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in rows
    ]
    return "\n".join([header_line, separator, *body])


def _render_trade_decision_report(
    decision: TradeDecision,
    daily_technical,
    daily_structure,
    hourly_technical,
    hourly_structure,
    relative_strength: RelativeStrengthReport,
    option_chain: OptionChainAnalysis | None,
    extra_warnings: list[str],
) -> str:
    sections = [
        _plain_table(
            ["Symbol", "Score", "Bias", "Decision", "Preferred Strategy"],
            [[decision.symbol, str(decision.score), decision.bias, decision.decision, decision.preferred_strategy]],
        ),
        "",
        _plain_table(
            ["Frame", "Close", "Tech Trend", "Structure", "Support", "Resistance", "Invalidation"],
            [
                [
                    "Daily",
                    _fmt(daily_technical.close),
                    daily_technical.trend,
                    daily_structure.trend,
                    _fmt(daily_structure.support),
                    _fmt(daily_structure.resistance),
                    _fmt(daily_structure.invalidation),
                ],
                [
                    "60m",
                    _fmt(hourly_technical.close) if hourly_technical else "-",
                    hourly_technical.trend if hourly_technical else "-",
                    hourly_structure.trend if hourly_structure else "-",
                    _fmt(hourly_structure.support) if hourly_structure else "-",
                    _fmt(hourly_structure.resistance) if hourly_structure else "-",
                    _fmt(hourly_structure.invalidation) if hourly_structure else "-",
                ],
            ],
        ),
        "",
        _render_relative_strength(relative_strength),
    ]

    if option_chain:
        sections.extend(["", _render_option_chain_summary(option_chain)])

    sections.extend(
        [
            "",
            "Reasons:",
            *[f"- {reason}" for reason in decision.reasons],
        ]
    )
    warnings = list(decision.warnings) + extra_warnings
    if warnings:
        sections.extend(["", "Warnings:", *[f"- {warning}" for warning in warnings]])
    return "\n".join(sections)


def _render_relative_strength(report: RelativeStrengthReport) -> str:
    rows = []
    for signal in (report.stock_vs_nifty, report.stock_vs_sector, report.sector_vs_nifty):
        if signal:
            rows.append(
                [
                    signal.comparison,
                    _fmt(signal.subject_return_percent),
                    _fmt(signal.benchmark_return_percent),
                    _fmt(signal.relative_return_percent),
                    signal.label,
                ]
            )
    if not rows:
        rows.append(["Relative strength", "-", "-", "-", "not available"])
    return _plain_table(["Comparison", "Subject %", "Benchmark %", "Relative %", "Label"], rows)


if __name__ == "__main__":
    main()
