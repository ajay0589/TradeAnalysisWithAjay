# Technical Setup Validation

New technical strategies remain research/paper-only until validation is reviewed.
Bug fixes to clocks, storage, freshness or UI do not establish a strategy's trading edge.
Existing scanners are not automatically approved by this policy.

## Required Matrix

- Test 1 week, 2 weeks, 1, 2, 3 and 6 calendar months, with indicator warm-up before each window.
- Compare relevant combinations and timeframes using fixed rules declared before testing.
- Test target risk/reward 1:1, 1:1.5 and 1:2, including realized results from early/session exits.
- Report net and gross results, sample size, expectancy, profit factor, maximum drawdown and costs.
- Use completed candles only, next-bar execution, adverse gap stops and conservative same-bar stop/target ordering.
- Do not substitute missing historical options, quotes or constituent data with present-day observations.
- Separate spot-direction research from option-premium P&L and actual broker fills.

## Screening And Promotion

The offline matrix requires >=50% net wins, positive net expectancy and profit factor >1,
with >=30 closed trades overall and >=10 in the later 30% of the period. Both parts must
meet the win-rate/expectancy conditions. These are preliminary screening thresholds,
not statistical proof or automatic approval. Small short-window samples are inconclusive,
not a reason to force more entries or relax risk controls.

Before promotion, also review drawdown, longer bull/bear/sideways regimes, sensitivity
to costs and nearby parameters, and genuinely untouched walk-forward/forward-paper data.
The six nested periods overlap and are not six independent validations. Looking across
many configurations also contaminates a holdout used to choose the winner.

At fixed full stop/target outcomes, before costs, break-even win rates are 50%, 40%,
and 33.33% for 1:1, 1:1.5 and 1:2. A 50% floor is the user's preferred screen;
it is neither sufficient for profitability nor necessary for every profitable strategy.
No setup is guaranteed to retain a historical win rate.

## Reproduction

Run in the project folder, substituting the last complete date in the available cache:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_nifty_setups --as-of 2026-10-07
```

Outputs: `reports/nifty-validation/validation.csv` and `validation.json`.
The JSON includes assumptions and trades. This command uses only local candles and
never starts a scanner, sends Telegram alerts, fetches credentials or places orders.
It evaluates native Lab technical predicates and additional research combinations.
The next-bar/ATR execution model is not an exact replay of live quote/drift gates,
the index candidate score, constituent breadth or historical option confluence.
Unavailable coverage must be replenished and audited before any setup is approved.
