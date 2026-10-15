import json
import threading
from urllib.request import urlopen

from trading_analysis.help_guide import guide, REVIEW_TEMPLATE
from trading_analysis.nifty_lab.setups import SETUPS, RISK, VERSION
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


def test_help_documents_live_rules_and_research_limits():
    result = guide()
    sections = {section["id"]: section for section in result["sections"]}
    assert len(sections) == len(result["sections"])
    assert result["lab_version"] == VERSION
    rules = dict(sections["lab-rules"]["items"])
    for key, definition in SETUPS.items():
        assert rules[f"{key}: {definition['name']} ({definition['frame']})"] == definition["rules"]
    assert str(RISK["session_exit"]) in rules["Initial risk"]
    content = json.dumps(result)
    for phrase in ("not win probability", "not an additional mandatory rolling-OI entry gate",
                   "not fully replayed", "Missing option history is skipped", "1:1.5", "6m",
                   "not an as-of reconstruction", "No historical win rate guarantees", "Wilder smoothing"):
        assert phrase in content
    assert "no tokens" in REVIEW_TEMPLATE and "actual fills" in content


def test_help_uses_current_shared_constants(monkeypatch):
    monkeypatch.setitem(RISK, "max_holding_minutes", 77)
    monkeypatch.setitem(SETUPS["Nifty_Setup1"], "rules", "Changed rule for test")
    content = json.dumps(guide())
    assert "Maximum holding 77 minutes" in content
    assert "Changed rule for test" in content


def test_help_endpoint_and_asset_are_read_only(monkeypatch):
    class NoScannerAccess:
        def __getattr__(self, name):
            raise AssertionError(f"Help must not access scanners: {name}")
    for name in ("nifty_lab", "all_index_monitor", "nifty_auto_service", "mcx_scanner"):
        monkeypatch.setattr(TradingRequestHandler, name, NoScannerAccess())
    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/help") as response:
            assert json.load(response)["review_template"] == REVIEW_TEMPLATE
        with urlopen(base + "/help.js") as response:
            assert "function loadHelpGuide" in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
