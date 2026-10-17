# Scalping baseline research: 17 October 2026

This is a development screen, NOT an out-of-sample approval or option P&L test.
No setup is promoted. All scalp monitors remain stopped by default.

## Data and design

- Zerodha historical one-minute spot candles for NIFTY, BANKNIFTY and SENSEX.
- Each cache contains 48,750 bars, 10 April to 16 October 2026, including warm-up.
- Three setups: trend pullback, opening-range retest, failed-breakout reversal.
- Entry frames: 1 and 3 minutes, with completed 5-minute context.
- Targets: 1R, 1.5R and 2R. Windows: 7, 14, 30, 60, 90 and 180 calendar days.
- Total: 324 overlapping comparisons; 54 six-month comparisons.
- Next-minute-open entry proxy, structural stops, maximum 20-minute hold,
  stop first when both levels are touched, and 2 bps per side assumed spot costs.
- The same technical rule function is used by replay and live experiments.
- No historical option confirmation, actual option costs or sub-minute latency
  replay. These require fixed-contract historical option quotes and timestamps.

## Six-month baseline: one minute, 1.5R target

| Index | Setup | Trades | Net win % | Expectancy R | Profit factor |
| --- | --- | ---: | ---: | ---: | ---: |
| NIFTY | Scalp1 | 734 | 39.24 | -0.702 | 0.273 |
| NIFTY | Scalp2 | 130 | 26.15 | -1.342 | 0.096 |
| NIFTY | Scalp3 | 82 | 32.93 | -0.770 | 0.238 |
| BANKNIFTY | Scalp1 | 746 | 37.40 | -0.570 | 0.352 |
| BANKNIFTY | Scalp2 | 162 | 31.48 | -1.091 | 0.146 |
| BANKNIFTY | Scalp3 | 85 | 41.18 | -0.399 | 0.474 |
| SENSEX | Scalp1 | 748 | 36.50 | -0.755 | 0.254 |
| SENSEX | Scalp2 | 144 | 22.92 | -1.412 | 0.081 |
| SENSEX | Scalp3 | 70 | 35.71 | -0.759 | 0.218 |

Every six-month configuration had negative expectancy under this cost model.
Some three-minute reversal configurations exceeded 50% wins but still lost net R.
This illustrates why a win-rate threshold alone is insufficient. R values can
be worse than -1 after friction or adverse gaps; total R is not portfolio return.

The complete report, coverage checksums and all windows persist in the local
Scalping Lab research view and date-range audit ZIP. Data is ignored by Git and
does not transfer to another laptop by pulling code. Use the research download
button on that laptop to rebuild its cache, or transfer an authorized stopped-
app data backup separately.

## Next research step

Do not use these baselines for real-money scalping. Collect synchronized spot,
ATM CE/PE quotes and OI in paper mode to evaluate the incremental benefit of B/C.
Investigate costs relative to typical stop distances, time-of-day and volatility
regimes before introducing more indicators. Predeclare a small change, test it
on development data, then evaluate on a newly reserved chronological period.
Do not repeatedly tune to these already inspected six months.
