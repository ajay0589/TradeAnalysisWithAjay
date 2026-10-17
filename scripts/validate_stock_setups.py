"""Offline F&O stock matrix. Optional explicit download uses the controlled broker client."""
import argparse
import json
from datetime import date

from trading_analysis.stock_research import StockResearch
from trading_analysis.web_services import AnalysisService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--benchmark", choices=["NIFTY 50", "NIFTY 500"], default="NIFTY 50")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    runner = StockResearch(AnalysisService())
    runner._worker(date.fromisoformat(args.as_of), args.benchmark, args.download)
    state = runner.status()
    report = state.get("report") or {}
    print(json.dumps({"phase":state["phase"], "progress":state["progress"], "as_of":report.get("as_of"),
                      "comparisons":len(report.get("rows",[])), "errors":report.get("errors",[]),
                      "screens":{k:sum(r["screen"]==k for r in report.get("rows",[])) for k in
                                 ("screen_pass_not_approved","screen_failed","incomplete_data","insufficient_sample")}},indent=2))
    if state["phase"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
