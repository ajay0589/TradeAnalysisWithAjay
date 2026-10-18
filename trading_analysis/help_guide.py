"""Read-only guide. Shared rule constants stay aligned with the running build."""
from trading_analysis.live_timing import CLOCK_SKEW_SECONDS, MAX_ENTRY_DELAY
from trading_analysis.nifty.live_scanner import HORIZON_CONFIG
from trading_analysis.nifty_lab.setups import RISK, SETUPS, VERSION


REVIEW_TEMPLATE = """Trading Desk session review (all times IST)
Session date / date range:
App build / Git commit / branch:
Scanner names, setup versions, horizons and intervals:
Started / stopped / pauses / laptop sleep or network interruptions:
Time synchronization and authentication issues (no tokens):
Files attached: app ZIP / index ZIP / Lab ZIP / trade CSV / candle cache
Missing data or failed refreshes:

For EACH issue, including good trades and no-trade days:
Trade ID / setup / technical or combined variant:
Signal candle OPEN time / candle CLOSE time:
Detection time / Telegram received time:
Expected behavior BEFORE knowing the outcome:
Observed behavior / exact error / screenshot with chart timeframe:
Was this a paper signal or an actual manual trade?
Optional actual trade: instrument, expiry, CE/PE, strike, buy/sell,
quantity, entry/exit times and fills, all charges, reason for exit.
For spreads, provide each leg. Remove account IDs and personal details.

Constraints agreed BEFORE the next experiment:
Allowed holding period / overnight exposure:
Maximum acceptable drawdown / losing streak / alert frequency:
Proposed hypothesis (one change) / unchanged baseline:
Evaluation dates / untouched future dates / acceptance criteria:

Do not include .env, access tokens, bot tokens, credentials or OTPs.
Keep all trades and sessions, not only winners or losers.
"""


def guide():
    from trading_analysis.stock_research import RULES
    from trading_analysis.expiry.rules import RULES as EXPIRY_RULES
    sections = [
        {"id": "expiry", "title": "Expiry Day Lab", "items": [(f"Rule {i+1}", rule) for i,rule in enumerate(EXPIRY_RULES)] + [
            ("Validation", "Recorded option quotes can be replayed using the same versioned engine. This is reproducibility, not historical proof of profitability. No historical option result is invented from spot candles. Missing exits and observation gaps are excluded."),
            ("Paper and manual", "Eight independent research ledgers plus one shared-budget paper portfolio. One lot per trade, INR 12,500 debit/cost allowance, INR 25,000 combined budget and daily loss trigger. Manual fills are a separate user-reported journal and do not change paper positions. No broker orders."),
            ("Operations", "Acknowledge paper experiments before starting. Stop new entries leaves open exits monitored. Keep server awake through 15:10 IST and until all exits resolve. After restart, explicitly start to resume monitoring; interrupted trades are excluded. EXPIRY_TELEGRAM_BOT_TOKEN and EXPIRY_TELEGRAM_CHAT_ID are isolated opt-in alerts."),
        ]},
        {"id": "fno-stocks", "title": "F&O Stock Research", "items": [(f"Rule {i+1}", rule) for i, rule in enumerate(RULES)]},
        {"id": "scalping", "title": "Index Scalping Lab", "items": [
            ("Unvalidated experiments", "NIFTY, Bank Nifty and Sensex each have trend-pullback, opening-range-retest and failed-breakout setups. Three variants per setup: A technical, B technical plus fixed-contract ATM call/put premiums, C B plus OI activity. All 27 ledgers are paper-only; no orders are placed."),
            ("Timing and execution", "Live entries use completed 1m candles and closed 5m context. Signal age <=10s; spot/options <=3s old; option spread <=1.5%. Entry cutoff 14:45 IST, maximum holding 20m, session exit 15:20. Targets remain fixed at 1.5R from signal close. Stop new entries keeps exits running."),
            ("Premium and OI evidence", "Spot, same-expiry ATM CE and PE must agree over 1m and 3m windows. ATM changes reset evidence. B needs opposite CE/PE midpoint moves of at least 0.2%; C also needs >=0.5% absolute OI change and positive volume increments on both sides. These are positioning hypotheses, not institutional identities. A does not wait for options."),
            ("Results and research", "Observed spot net R assumes 2 bps per side. Indicative option ask-to-bid gross points exclude fees/slippage and are not fills. Research tests 1m/3m, 1R/1.5R/2R, 1/2 weeks and 1/2/3/6 months. Missing coverage stays missing. No automatic promotion, no claim of historical option replay, and no profitability guarantee."),
            ("Review packet", "Scalping Lab date-range ZIP includes decisions, option evidence, trades, costs, policies, monitor events and Telegram delivery. Download CSV for compact spot results. Logs do not include credentials. SCALP_TELEGRAM_BOT_TOKEN and SCALP_TELEGRAM_CHAT_ID configure a dedicated opt-in paper chat."),
        ]},
        {"id": "scope", "title": "About & Price Basis", "items": [
            ("Research, not execution", "This is a read-only scanner and paper-trade audit application. It does not place orders. A signal is a testable rule outcome, not a recommendation or a guaranteed edge."),
            ("Index signals", "NIFTY, Bank Nifty and Sensex entries/results track the underlying spot index, not futures, option premiums, strikes or spread P&L. Option strategy names are context, not executed option legs. A correct spot direction can still lose on options because of time decay, IV changes, spreads and costs."),
            ("Scanner horizons", "; ".join(f"{name}: {row['timeframe']} closed candles" for name, row in HORIZON_CONFIG.items()) + ". The monitor interval is a scheduling interval, not the candle timeframe."),
            ("NIFTY Setup Lab", f"{VERSION}: five intraday-only setups, each with technical-only and technical-plus-options ledgers. These are ten paper variants sharing data, not ten independent broker downloaders. No swing or positional Lab signals."),
            ("Other modules", "F&O Stocks separates research candidates, historical validation and the legacy pullback trade archive. New swing setups have no live-start control pending review. Krishna and Purple Touch sections are retired; their stored records are retained. MCX Swing tracks specific futures contracts and expiry, not index spot. Do not pool results across engines."),
            ("Validation status", "No setup is certified profitable. The 15 October review screened 342 comparisons ending 7 October; none passed all coverage, sample-size, win-rate and expectancy checks. Five-minute history was incomplete. Existing scanners remain experiments, not validated strategies."),
        ]},
        {"id": "evidence", "title": "Data To Share After Market Hours", "items": [
            ("Daily review packet", "Select the same IST From/To dates for Download app log, Index Scanning > Download scan log, and NIFTY Setup Lab > Download trades & logs. Include the index and Lab trade CSVs when convenient. The app ZIP also embeds scanner records; the smaller dedicated ZIPs make review easier."),
            ("All sessions count", "Send winning, losing and zero-trade days. Start with 10-20 complete sessions to diagnose operations, then continue until each strategy has a meaningful sample across market regimes. This is not a profitability sample-size guarantee. Include carried/open positions and avoid changing rules midway through the comparison."),
            ("Backtest inputs", "For a reproducible six-month test, provide the relevant cached candle CSVs under data/raw/candles for NIFTY_50, NIFTY_BANK and SENSEX: 5minute, 15minute, 60minute and day as available. Include earlier warm-up history. For constituent tests, include the tracked stocks and the dated leader configuration. ZIP copies after downloads have stopped; do not edit original candles."),
            ("Option and volatility history", "Keep timestamped snapshots already included in exports, with receipt time, exchange time, expiry, strike, premium, OI, bid/ask and volume. Entry/exit India VIX references help grouping, but are not a complete historical VIX series. Missing past OI or quote observations cannot be reconstructed honestly from today's chain."),
            ("Session notes", "Attach the review template: build/version, scanner settings, start/stop times, restarts, sleep/VPN/network issues and trade IDs. For a delayed alert, give candle close, detection and Telegram receipt times separately. A chart screenshot needs symbol, timeframe and IST timestamp."),
            ("Actual option trades, optional", "Provide a redacted broker trade export with expiry, strike, CE/PE, buy/sell, quantity, actual fills, time and charges. Include every spread leg. Keep it separate from the paper spot ledger; it measures execution and option-selection effects, not just signal quality."),
            ("Privacy and retention", "Never share .env, passwords, access tokens, bot tokens, OTPs or an unreviewed whole project/database. Review exports before sharing; remove personal identifiers. Keep daily ZIPs locally because retained logs can be pruned. Range trade rows show latest stored state, not an as-of reconstruction."),
        ]},
        {"id": "factors", "title": "Technical Factors Explained", "items": [
            ("Candle and timeframe", "OHLC means open, high, low and close over the selected interval. Live direction requires a completed candle: bullish close > open; bearish close < open. The timestamp normally identifies the candle start, not when its close became known. A 09:15 15-minute candle is actionable only after 09:30. Earlier intrabar entries are a different strategy, not merely a faster implementation."),
            ("EMA20 and EMA50: trend", "An exponential moving average weights recent closes more heavily. The core index rule requires close > EMA20 > EMA50 for bullish signals and the reverse for bearish signals. Twenty bars means different elapsed time on a 5-minute versus daily chart. EMAs lag and can whipsaw in ranges; they do not predict the next candle."),
            ("RSI14: momentum", "Wilder-smoothed average gains/losses form a 0-100 momentum reading. Core index bullish threshold is >=52; bearish is <=48. These rules have no upper bullish RSI cap. Lab setups use their own bounded ranges or crossings below. RSI is not a probability; high RSI can mean strength or an extended entry."),
            ("ATR14: volatility", "True range is the largest of high-low, abs(high-previous close), abs(low-previous close). Live index/Lab ATR is the arithmetic mean of the last 14 true ranges. The historical simulator's ATR series is Wilder-smoothed instead. This is a known calculation mismatch, not just a chart setting: stop distances and results can differ. ATR sizes stops and tolerances; it does not provide direction."),
            ("Structure and support/resistance", "Swing highs/lows, range boundaries and previous-day levels describe trend and nearby obstacles. Live index stops prefer the nearest valid support below a long entry or resistance above a short entry. That can produce a much tighter stop than one ATR. A minimum stop-distance filter is a research hypothesis, not an existing universal gate."),
            ("SMA200 and VWAP: context", "SMA200 is an equal-weight long-term average used in NIFTY positional context. VWAP is volume-weighted price when usable volume exists and can affect NIFTY context bias. Index spot volume may be absent; missing VWAP is not a zero reading. Neither factor is a universal hard gate in all scanners or the simple technical backtest."),
            ("Higher timeframe confirmation", "A higher timeframe tests whether a lower-timeframe trigger agrees with a broader trend. Lab Setup3 and Setup5 require the latest closed 15-minute EMA trend. The existing index horizons are independently checked; there is no universal daily/weekly trend gate on every intraday entry. A broader regime filter needs a separate controlled test."),
            ("Breakout, pullback and rejection", "A breakout closes beyond an already-known range; a pullback reclaims an average within a trend; a retest touches a previously broken level and rejects it. Levels must be built from preceding bars. Lab's precise rules are listed below, not all candle-pattern names such as engulfing or hammer."),
            ("Bollinger bands: range re-entry", "Lab Setup4 uses a 20-close mean with bands two population standard deviations away. A prior outside close must return inside, with an RSI crossing and a small EMA gap. This is a range hypothesis, unlike trend-following breakout setups."),
            ("Volume and liquidity", "Option volume increments and bid/ask spreads help reject inactive/noisy contracts. Index spot volume is not futures volume. These index technical gates do not impose a universal volume, VWAP or ADX filter. Do not interpret a displayed context field as an enforced entry rule."),
            ("India VIX versus option IV", "India VIX is broad expected-volatility context, not a directional vote. Option IV is contract-specific implied volatility; IV rank compares it with its available history. Entry/exit VIX is recorded where available. There is no universal VIX threshold blocking these index/Lab trades. NIFTY candidate scoring can use IV regime; the technical replay does not."),
            ("Constituent breadth", "Bank Nifty/Sensex require at least max(3, ceil(tracked stocks/2)) technically aligned stocks, at least two with aligned option bias, at least two aligned on both, and more technical supporters than opponents. This is a count-based sample of leaders, not the entire index. Available weights describe sampled pressure, not a weighted entry gate."),
        ]},
        {"id": "options", "title": "Option Evidence & Scores", "items": [
            ("OI is outstanding contracts", "Every open contract has a buyer and seller. Price up/OI up is labelled long buildup; price down/OI up short buildup; price up/OI down short covering; price down/OI down long unwinding. These are hypotheses, not proof of participant identity or institutional intent. Premium changes also reflect IV, time and underlying price."),
            ("Existing NIFTY scanner", "Entry accepts the current NIFTY option-context bias when aligned or neutral, with an option snapshot no older than ten minutes and a suitable directional candidate. PCR (put OI / call OI), OI support/resistance and changes inform that context. Logged rolling 3/6/15-minute evidence is diagnostic; it is not an additional mandatory rolling-OI entry gate."),
            ("Existing Bank Nifty/Sensex scanners", "The active gate compares two successive same-expiry snapshots, with at least eight matched contracts and 70% coverage. Bullish heuristic: at least two put contracts with rising OI/falling premiums, put-writing OI >1.25 times call-writing OI, and PCR >=0.9. Bearish reverses calls/puts with PCR <=1.1. The index and constituent gates require directional agreement. Rolling evidence is recorded separately, not substituted for this gate."),
            ("Lab combined variant", "Technical-only entries do not depend on options. The combined variant requires 3-minute and 6-minute price/OI interpretations to agree with the technical direction, without an opposing 15-minute view. Each comparison needs eight liquid matched contracts, >=70% coverage and both CE/PE represented. Qualifying votes need increased volume and >=0.5% absolute price and OI changes; winning direction needs >=4 votes, >=2x opposition and support on both CE/PE sides."),
            ("Liquidity and timing", "Lab option spreads must be <=5% of premium; trade/exchange timestamps and the latest snapshot must meet 120-second freshness checks. A baseline may precede a 3/6/15-minute cutoff by at most 90 seconds. Missing history means waiting, not a bearish vote. The application does not know the exchange's actual OI publication time and does not assume a fixed three-minute delay is tradable information."),
            ("NIFTY directional score", "Suitability starts at 35: +25 matching horizon context, +20 aligned/neutral options, +8 relevant OI support/resistance, +7 appropriate IV regime. Conflicting technical context subtracts 25; conflicting options subtracts 20; result is bounded to 0-100. Live default minimum is 70. Neutral/hedge suggestions have different formulas. This is a heuristic score, not win probability."),
            ("Bank Nifty/Sensex score", "After confluence, score = min(90, 70 + 4 x technically aligned tracked stocks). Five aligned stocks give 90, not a 90% likelihood of profit. Lab has rule-based pass/fail, not this suitability score."),
        ]},
        {"id": "lab-rules", "title": "Five Lab Setups & Shared Risk", "items": [
            (f"{key}: {row['name']} ({row['frame']})", row["rules"]) for key, row in SETUPS.items()
        ] + [
            ("Initial risk", f"{RISK['stop_atr']:g} ATR stop and {RISK['target_r']:g}R target from the signal reference; live quote validation can change observed R:R without moving the target. Maximum holding {RISK['max_holding_minutes']} minutes. Last entry before {RISK['last_entry']} IST; session exit at {RISK['session_exit']} IST when monitoring/data are available."),
            ("Independent ledgers", "Technical and combined variants have separate entries/exits and can differ in timing. Missing option confirmation must not block a technical-only trade. Stops/targets are paper observations, not guaranteed broker fills. Missed sessions and recovered exits must be reviewed separately from uninterrupted observations."),
        ]},
        {"id": "timing", "title": "Entry, Exit & Timing", "items": [
            ("Live entry sequence", "Completed candle -> technical/context gates -> fresh quote -> drift and remaining R:R checks -> persistent trade/alert -> Telegram delivery. These timestamps need not be equal. App entry price is the observed spot at acceptance, not the spot when Telegram appears on your phone."),
            ("Freshness and expiry", f"Normal live quotes must be received within 20 seconds. Exchange clocks may lead by at most {CLOCK_SKEW_SECONDS} seconds; future local receipt times are rejected. Signal age limits: " + ", ".join(f"{horizon} {seconds}s" for horizon, seconds in MAX_ENTRY_DELAY.items()) + ". Swing/positional session-close references have an explicit up-to-15-minute exception and are labelled, not called executable fills."),
            ("Drift and reward", "An accepted quote must stay within the smaller of 25% of initial risk and 25% of ATR from the signal reference. Stop/target stay fixed. Remaining reward/risk must be >=1.5. A delayed signal that no longer offers this is rejected, not chased."),
            ("Index stop and exit", "Index stop uses nearby support/resistance; if absent, risk is max(ATR, horizon fallback percentage of spot). Quote exits check stop/target; unseen completed bars are also inspected for stop, target and EMA20 reversal, with intraday session closure. Gap or unavailable-quote exits can exceed -1R. Historical holding-bar caps are not a promise that live index exits use the same cap."),
            ("Fallback percentages", "; ".join(f"{h}: {c['fallback_risk_percent']:g}%" for h, c in HORIZON_CONFIG.items()) + ". These floors apply only when no valid structural level is found, not to every live index stop."),
            ("Duplicates and delivery", "An already-open index horizon prevents another same-horizon entry. Setup/candle identifiers suppress duplicates. Trade creation and Telegram delivery are separate outcomes: an alert can exist with failed delivery. A stopped server cannot monitor an open paper trade; restart does not erase that monitoring gap."),
        ]},
        {"id": "parity", "title": "Backtest Versus Live: What Matches?", "items": [
            ("Short answer: not completely", "The shared EMA/RSI/candle concepts match, but the current research engines do not reproduce every live gate, stop choice, feed delay or fill. Always compare the named backtest method and assumptions, not just two win-rate numbers."),
            ("Simple scanner backtest", "Replays core EMA20/50, RSI14 52/48 and candle direction on spot, using a signal-close reference entry and ATR/fallback stop. Checks later candles for stop/target, EMA reversal and maximum holding/session exit. It does not replay candidate suitability, option context, constituent breadth, observed quote/drift checks, real delivery or option costs. Live structural stops can differ materially."),
            ("Controlled comparison backtest", "Uses next-bar open, one-ATR stop, selectable target R, EMA20 reversal, holding/session rules and configurable spot-equivalent costs (default 2 bps per side). Technical, price/volume, rolling OI and combined variants use only option observations received by the decision time. Missing option history is skipped, not invented. Live two-snapshot constituent gates and NIFTY suitability scores are not fully replayed."),
            ("Offline validation matrix", "scripts/validate_nifty_setups.py tests multiple technical families/timeframes and native Lab predicates at 1:1, 1:1.5 and 1:2 over 1w, 2w, 1m, 2m, 3m and 6m. Lab predicates reuse live code; execution uses next-bar open and one-ATR risk, not live quote/drift or option confirmation. The model's session exit and holding rules are disclosed in its report and can differ from live."),
            ("Calculation parity gap", "Live ATR uses a simple 14-range average; historical _atr_series uses Wilder smoothing. Even the offline Lab predicate replay shares the live signal evaluator but sizes historical risk with the simulator's ATR. Align and regression-test formulas plus structural/ATR stop policy before treating research results as live validation. This guide does not silently change either calculation."),
            ("Why identical indicators still differ", "Next-open versus observed-quote entry, structural versus ATR stops, candle revisions, warm-up, stale data, gaps, spread/slippage, operating interruptions and delivery latency alter trades. OHLC cannot determine the exact order of events inside a candle; historical tests use stop-first when both target and stop are hit. A backtest exit timestamp can be only a candle-close bound."),
            ("Other backtests", "Generic Strategy Backtest, Krishna/Purple simulations, alert replay and MCX evaluations are separate methods. Their own parameters and assumptions apply. Do not treat a generic stock strategy result or old alert replay as validation of a current index or option strategy."),
        ]},
        {"id": "metrics", "title": "Reading Performance", "items": [
            ("R and risk/reward", "1R is the initial entry-to-stop distance. A 1:2 target offers +2R if reached, but actual average wins can be smaller after reversal/time exits. A gap can lose more than 1R. R normalizes spot signals; it does not translate directly to option premium risk."),
            ("Win rate and expectancy", "Win rate = profitable closed trades / all eligible closed trades. Expectancy = win fraction x average win - loss fraction x average loss - costs. If using net returns, do not subtract costs twice. A 60% win rate with +0.5R wins and -1R losses loses 0.10R per trade before costs."),
            ("Break-even targets", "Only if every outcome is exactly target or -1R, before costs: 1:1 needs 50%; 1:1.5 needs 40%; 1:2 needs 33.33%. Partial/time exits, costs and gap losses invalidate that simplification. Your >=50% target is a research requirement, not proof of profitability."),
            ("Profit factor and drawdown", "Profit factor = total gains / absolute total losses; >1 indicates a positive aggregate for that sample, not certainty. Drawdown measures peak-to-trough loss. Also track losing streak, trade count, exposure, average holding time and returns by horizon, direction, VIX regime, time of day and expiry proximity. Do not pool correlated signals as independent evidence."),
            ("MAE, MFE and operational quality", "Maximum adverse/favorable excursion shows how far price went against/for a trade. It needs sufficiently granular intervening prices, not only entry and exit. Also measure stale skips, quote availability, signal-to-entry delay, delivery failure, and how many signals each filter rejected. Missing measurements must stay unknown."),
            ("Samples and uncertainty", "Ten wins from ten trades are not enough to establish an edge. Confidence intervals, costs, sample coverage and out-of-sample performance matter. The current preliminary screen needs >=30 total and >=10 later-period trades plus >=50% net wins, positive expectancy and profit factor >1 in both samples; those floors alone are not approval."),
        ]},
        {"id": "improvement", "title": "Improvement Plan & Review Discipline", "items": [
            ("First, reliable measurement", "Separate feed/clock/exit/delivery faults from strategy losses. Audit large stop overruns before tuning indicators. Then align live/backtest ATR, stop selection and execution assumptions so comparisons measure the same rules. Keep the current version as a frozen baseline and record every change. Paper-observe until there is evidence; more alerts alone is not improvement."),
            ("Controlled experiments", "Test one change at a time: trend/range regime classification, higher-timeframe agreement, minimum stop distance, extension caps, session windows, entry timing, and whether OI or constituent filters add value. Compare baseline, technical-only and added-filter variants on the same eligible dates; report coverage and rejected opportunities too. These are proposed experiments, not newly active filters."),
            ("Validation before promotion", "Run your six windows and three R:R levels with warm-up and realistic cost stress. Split time chronologically, tune only earlier periods, freeze rules, then test untouched later data and forward paper sessions. Overlapping windows are not six independent confirmations. Repeatedly choosing the best holdout result overfits it; obtain a new untouched period."),
            ("What helps the collaborator", "Complete data, reproducible examples, explicit constraints and observations recorded before the outcome help more than requests to force a winning setup. This workflow improves versioned rules, tests and documentation; sharing logs here is not a fine-tuning job or automatic live self-learning. Decisions remain reviewable code changes."),
            ("Acceptance and rollback", "Agree drawdown, sample coverage, cost stress and alert-frequency limits before examining results. Keep weak or inconclusive candidates experimental. Record failed experiments as well as successful ones; preserve a rollback version. No historical win rate guarantees future returns."),
        ]},
    ]
    return {"schema_version": 1, "reviewed_on": "2026-10-15", "lab_version": VERSION,
            "sections": sections, "review_template": REVIEW_TEMPLATE,
            "sources": [
                {"title": "Kite historical candles", "url": "https://kite.trade/docs/connect/v3/historical/"},
                {"title": "Kite streaming timestamps", "url": "https://kite.trade/docs/connect/v3/websocket/"},
                {"title": "CFTC: limitations of trading-system results", "url": "https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/fraudadv_tradingsystem.html"},
                {"title": "OpenAI: fine-tuning is a separate training workflow", "url": "https://developers.openai.com/api/docs/guides/supervised-fine-tuning"},
            ]}
