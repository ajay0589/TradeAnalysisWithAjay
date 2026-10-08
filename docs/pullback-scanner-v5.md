# F&O Pullback Research and Paper Scanner (v5)

## Status and measured results

Implemented on `scanner-audit-and-v5`. The new scanner is **paper monitoring only**, stopped by default. It does not place orders. Current evidence does not establish a profitable live strategy, particularly for bearish breakout entries.

On 8 October 2026, the existing local cache reproduced the screenshot's 1,221 trades using Daily candles, 730 days, the first 50 watchlist symbols, and the old entry settings. The comparisons below used that same stock sample and cached period:

| Experiment | Trades | Win rate | Profit factor | Average trade return |
| --- | ---: | ---: | ---: | ---: |
| Bullish, old entry settings, no index filters | 1,221 | 48.08% | 1.19 | 0.30% |
| Bullish, same settings, aligned NIFTY 50 and sector | 126 | 50.79% | 1.48 | 0.68% |
| Bullish, filtered breakout confirmation, assumed costs | 100 | 34.00% | 0.90 | -0.17% |
| Bearish, filtered breakout confirmation, assumed costs | 122 | 27.87% | 0.39 | -1.69% |

Old settings: next-open entry, holding_bars=5, ATR stop=1.5, target=2R, zero brokerage/slippage. Breakout settings: breakout_stop, entry_valid_bars=3, holding_bars=5, signal stop, target=2R, 2 bps slippage plus 2 bps brokerage per side. Filtered experiments require both daily index trend and confirmed swing structure. Nothing was optimized on an untouched holdout.

The first comparison isolates the filters. The breakout comparisons change execution and stop assumptions too; their different results cannot be attributed only to the filters. The live quote-based paper checker also rejects chased entries, whereas this historical OHLC replay does not reproduce those quote-time restrictions. It is a research proxy, not a complete live replay.

1,221 is a trade count, not a quality score. A 1.19 profit factor is a modest historical margin, and average R was only 0.049 before costs. The screenshot's 63.73% drawdown and 1,120.24% ending return were sequential compounding across symbols, not a capital-constrained portfolio. Sorting those same trades chronologically changes sequential drawdown to 87.59%; the compounded ending return stays the same. Neither figure represents an investable portfolio with simultaneous trades, allocation limits, lot sizes and margin.

## Backtest navigation and symbol limit

Go to `Stock Research > Backtest`. Choose one separate workspace:

- `Generic Strategy Backtest`: configurable bullish/bearish pullbacks and other registered strategies, with its own inputs, results and full JSON export.
- `Krishna Setup Backtest`: the existing Krishna setup simulation, with its own inputs and results. Its logic has not been replaced by the generic strategy.

`Maximum watchlist symbols` defaults to `all`, meaning the entire configured watchlist. A numeric value such as 50 selects the first 50 symbols in watchlist order, not the 50 strongest stocks, 50 trades, or 50 simultaneous positions. An explicit comma-separated Symbols list takes precedence over the limit. Missing candle files are reported and reduce the number actually analyzed. Full-watchlist runs take longer than limited runs.

For a controlled comparison, keep dates, symbols, timeframe, entry/stop/target settings and costs identical, and change only the market/sector checkboxes. Download each complete result. The old `next_open` settings remain available through Backtest Params JSON; the new pullback UI defaults to breakout confirmation with explicit cost assumptions.

## Existing stock criteria

Bullish pullback:

1. At least 50 signal-timeframe candles for strategy calculation (the live freshness check requires 55).
2. Close above SMA50 **or** SMA20 above SMA50. A downtrend structure combined with a close below SMA50 is rejected.
3. RSI14 between 40 and 55, inclusive.
4. Close above identified support, where support is available.
5. Nearest EMA20, SMA20 or support is within 0.85 ATR14 of the close.
6. Confirmation trigger is signal-candle high plus 0.10% of that high. The default signal stop is the lower of the candle low and available support.

Bearish pullback mirrors the trend tests, uses RSI14 45-60, close below available resistance, and proximity to EMA20/SMA20/resistance. Its trigger is the signal low minus 0.10% of that low; the stop is the higher of the candle high and available resistance.

Turning candles/RSI and stock structure contribute to the existing scanner score; score is not a calibrated probability. A stock's confirmed swing trend remains an optional strategy parameter. The **index** structure switch is separate from that stock parameter.

The screenshot's next-open backtest did not wait for the above breakout trigger: the chosen Backtest Params overrode the signal's proposed entry method.

## Added market and sector filters

Choose one benchmark: NIFTY 50, 100, 200 or 500. This changes market context, not the stock trading universe. A broad index is not itself an F&O eligibility list. The live universe is the intersection of NSE equity watchlist stocks and current unexpired NFO futures names.

Both market and mapped sector use the latest **completed Daily candle**, even when stock setup candles are 15-minute or hourly:

- Bullish: close > SMA50, EMA20 > SMA50, and SMA50 > its value five sessions earlier.
- Bearish: the strict reverse of all three comparisons.
- With confirmed structure enabled, bullish additionally needs confirmed higher-high/higher-low structure; bearish needs lower-high/lower-low structure.
- Neutral, conflicting, missing, insufficient or stale required context blocks new entries. There is no silent fallback to stock-only signals.
- Sector context comes from the existing sector map. Some mappings are proxies; inspect the actual index name in the audit rather than assuming every mapping is a dedicated sector index.

Backtests inspect only index candles available by signal close and again at the proposed entry. The current session's completed Daily candle is not visible to a morning entry. Live waiting setups also recheck the latest completed daily context before entry. These are slower regime filters, not a real-time intraday reversal detector.

## Running paper alerts

1. Authenticate Zerodha in Data Ops and refresh today's NSE/NFO instrument masters. For all three index scanners, refresh BSE/BFO too.
2. Create a separate Telegram group/channel, add the bot with permission to post, and put these values in local `.env`:

   ```dotenv
   PULLBACK_TELEGRAM_BOT_TOKEN=your_bot_token
   PULLBACK_TELEGRAM_CHAT_ID=your_separate_chat_id
   ```

3. Restart the server. Do not commit `.env`. No dependency changes are required for this feature.
4. Open `Stock Research > F&O Pullbacks`, choose the setup timeframe and benchmark, and use `Test pullback Telegram` for a clearly labelled test message.
5. Use `Start paper alerts` for both bullish and bearish stock checks. `Start indices + pullbacks` additionally starts NIFTY, Bank Nifty and Sensex through their existing independent monitors. It preserves already-running index monitors. `Stop pullbacks` affects only the stock checker; use Index Scanning's controls to stop indices.
6. Start shortly before NSE market hours. Checks operate on weekdays from 09:15 to 15:30 IST and wait outside that window. The server must remain running and the laptop awake; the browser tab need not stay open. An exchange holiday calendar is not yet integrated. Freshness gates may conservatively block the next session after a holiday until data is available.

The stock scanner has a candle-refresh worker and a cached-data checker. They run independently of index analysis. All Zerodha network calls still share the existing rate-limited request queue. Initial missing history can take time; fresh cached symbols can be analyzed without waiting for a full-universe download. Checks occur roughly every 15 seconds plus processing/network time, not tick-by-tick. Requests already in flight can delay stop completion.

Closed stock candles create persistent waiting setups, not immediate trade alerts. Fresh observed spot must cross the stored trigger within the next three setup bars. At entry the checker rejects movement over 25% of original risk beyond the trigger or remaining reward/risk below 1.5. It keeps the original stop and 2R target. It does not move levels to justify a late entry. A changed regime, expired waiting period or stop breach discards the waiting setup.

New entries require a current-session instrument universe. A new session with outdated masters blocks new entries until refreshed, while existing open trades retain exit checks. Restart preserves setups and trade history but does not automatically start the monitor.

Open paper trades use fresh spot observations for stop/target exits and a five-subsequent-closed-bar holding limit. Quotes must be no more than 30 seconds old. Entry/exit timestamps are observation times in IST; open time is exit minus entry, or elapsed time for open trades. Stops can be crossed between polls or while the app is off; the app does not guarantee a fill at the stop. No option strikes, expiries, premium fills, futures fills or orders are created. Intraday setup timeframe does not imply compulsory same-day square-off.

## Audit and validation

The screen separates stock decisions, waiting setups/spot trades and alert delivery. It shows progress, current stock, refresh status, failures and lifecycle timestamps. Download the scanner log after the session; the application-wide log includes pullback diagnostics too. Review exports before sharing because symbol and trade details remain visible.

Setup transitions, event records and queued delivery are committed together. Repeated checks cannot create duplicate entry/exit events. Delivery uses only the `PULLBACK_` destination, never the NIFTY fallback. Messages are labelled PAPER. Shared outbox rules prevent blind retries after uncertain delivery and expire messages older than 15 minutes. Configuration alone does not prove Telegram delivery: verify the test and delivery status on the actual scanning laptop.

Validate later untouched periods, market regimes, sectors, larger stock samples and cost sensitivity before considering actual F&O trades. Current constituents introduce survivorship bias. Corporate-action adjustments depend on source candles. Historical OHLC cannot resolve intrabar ordering; stop-first treatment is conservative when both levels appear in one bar, but stop-gap execution is not fully modeled. Option liquidity, bid/ask spread, IV, theta, margin and contract selection require separate validation.

Optional UI regression: `python tests/browser_pullback_smoke.py http://127.0.0.1:8766`. This only runs cached research and UI exports; it blocks scanner starts and Telegram calls.
