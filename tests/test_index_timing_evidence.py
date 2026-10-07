from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from trading_analysis.live_timing import IST, expected_closed_bar, prepare_live_entry, live_quote_exit, price_closed_exit
from trading_analysis.live_quotes import LiveQuoteService
from trading_analysis.models import Candle
from trading_analysis.nifty.live_scanner import evaluate_live_exit
from trading_analysis.nifty.comparison_backtest import compare_setups
from trading_analysis.nifty.comparison_service import parameters, refresh_comparison_data
from trading_analysis.notifications.outbox import AlertOutbox
from trading_analysis.option_history import OptionHistory, rolling_evidence, compare_snapshots
from trading_analysis.storage import NiftyTradeRepository, NiftyAlertRepository

NOW = datetime(2026, 10, 7, 10, 30, 15, tzinfo=IST)


def quote(price=100, when=NOW):
    return {"price": price, "quote_time": when.isoformat(), "received_at": when.isoformat(), "source": "fixture"}


def signal():
    return {"horizon": "intraday", "direction": "bullish", "entry_time": NOW,
            "entry_candle_timestamp": NOW.replace(minute=15, second=0), "entry_timeframe": "15minute",
            "entry_price": 100, "stop_level": 96, "target_level": 108, "score": 85, "risk_points": 4,
            "metadata": {"signal_candle_closed_at": NOW.replace(second=0).isoformat(), "atr14": 4}}


@pytest.mark.parametrize("frame,stamp,expected", [
    ("15minute", "2026-10-07T09:29:59", "2026-10-06T15:15:00"),
    ("15minute", "2026-10-07T09:30:00", "2026-10-07T09:15:00"),
    ("60minute", "2026-10-07T15:30:00", "2026-10-07T15:15:00"),
    ("day", "2026-10-07T15:29:59", "2026-10-06T00:00:00"),
    ("day", "2026-10-07T15:30:00", "2026-10-07T00:00:00"),
    ("5minute", "2026-10-07T09:20:00", "2026-10-07T09:15:00"),
])
def test_expected_closed_boundary(frame, stamp, expected):
    assert expected_closed_bar(frame, datetime.fromisoformat(stamp)).replace(tzinfo=None).isoformat() == expected


def test_actual_quote_separate_from_signal_reference():
    result = prepare_live_entry(signal(), quote(100.5), NOW)
    assert result["entry_price"] == 100.5
    assert result["entry_time"] == NOW
    assert result["metadata"]["reference_price"] == 100
    assert result["metadata"]["signal_delay_seconds"] == 15
    assert result["stop_level"] == 96 and result["target_level"] == 108


@pytest.mark.parametrize("delay,price,quote_age,expected", [
    (121, 100, 0, "signal_expired"), (-1, 100, 0, "signal_expired"),
    (120, 100, 0, "passed"), (15, 102, 0, "price_moved_too_far"),
    (15, 100, 21, "spot_quote_unavailable_or_stale"),
    (15, 100, -1, "spot_quote_unavailable_or_stale"),
    (15, float("nan"), 0, "spot_quote_unavailable_or_stale"),
])
def test_entry_expiry_drift_and_quote_freshness(delay, price, quote_age, expected):
    entry = signal()
    entry["metadata"]["signal_candle_closed_at"] = (NOW - timedelta(seconds=delay)).isoformat()
    checks = {}
    result = prepare_live_entry(entry, quote(price, NOW - timedelta(seconds=quote_age)), NOW, checks)
    assert checks["gate"] == expected
    assert bool(result) == (expected == "passed")


def test_live_exit_before_candle_close_requires_fresh_quote():
    trade = {**signal(), "status": "open", "entry_time": NOW - timedelta(minutes=2)}
    assert live_quote_exit(trade, quote(95.5), NOW)["price"] == 95.5
    assert live_quote_exit(trade, quote(95.5), NOW)["reason"] == "stop_loss"
    assert live_quote_exit(trade, quote(108.5), NOW)["reason"] == "target_reached"
    assert live_quote_exit(trade, quote(95.5, NOW - timedelta(minutes=1)), NOW) is None
    assert live_quote_exit({**trade, "status": "closed"}, quote(95), NOW) is None


def test_candle_exit_is_labeled_reference_and_recovery():
    trade = signal()
    outcome = {"price": 96, "reason": "stop_loss", "candle_timestamp": NOW.replace(minute=0, second=0)}
    result = price_closed_exit(trade, outcome, None, NOW, "15minute")
    assert result["metadata"]["exit_price_basis"] == "candle_reference_not_fill"
    assert result["exit_time"] == NOW
    result = price_closed_exit(trade, outcome, quote(90, NOW + timedelta(days=1)), NOW + timedelta(days=1), "15minute")
    assert result["price"] == 96
    assert result["metadata"]["recovered_event"]


def test_daily_finalization_labels_last_session_quote_not_live_fill():
    entry = signal()
    close = NOW.replace(hour=15, minute=30, second=0)
    entry.update(horizon="positional", entry_timeframe="day")
    entry["metadata"]["signal_candle_closed_at"] = close.isoformat()
    result = prepare_live_entry(entry, quote(100, close), close + timedelta(minutes=2))
    assert result["metadata"]["price_basis"] == "session_close_reference_not_fill"
    assert prepare_live_entry(entry, quote(100, close - timedelta(minutes=1)), close + timedelta(minutes=2)) is None


def test_partial_entry_bar_does_not_replay_pre_entry_stop_touch():
    end = NOW.replace(minute=15, second=0)
    candles = [Candle(end - timedelta(minutes=(49 - i) * 15), 100, 101, 99, 100, 100) for i in range(50)]
    candles[-1] = Candle(end, 100, 120, 80, 101, 100)
    trade = {**signal(), "status": "open", "entry_time": end + timedelta(minutes=1),
             "entry_candle_timestamp": end - timedelta(minutes=15),
             "last_candle_timestamp": end - timedelta(minutes=15), "metadata": {"timing_version": 2}}
    assert evaluate_live_exit(trade, candles, now=NOW)["checked_only"]
    assert evaluate_live_exit({**trade, "metadata": {}}, candles, now=NOW)["reason"] == "stop_loss"


def test_observed_quote_cannot_be_replaced_by_older_exchange_tick():
    service = LiveQuoteService()
    service.publish("NIFTY", 100, NOW, source="test")
    service.publish("NIFTY", 90, NOW - timedelta(seconds=1), source="test")
    assert service.get("NIFTY")["price"] == 100


def snapshot(minute, expiry="2026-10-13", count=8):
    when = NOW.replace(minute=0, second=0) + timedelta(minutes=minute)
    rows = []
    for index in range(count):
        side = "PE" if index % 2 else "CE"
        price = 100 - minute if side == "PE" else 100 + minute
        rows.append({"strike": 22000 + (index // 2) * 50, "option_type": side, "expiry": expiry,
                     "oi": 1000 + minute * (10 if side == "PE" else 0),
                     "last_price": price, "bid_price": price - 0.5, "ask_price": price + 0.5,
                     "volume": 100 + minute * 10, "received_at": when.isoformat(), "exchange_timestamp": when.isoformat()})
    return {"received_at": when.isoformat(), "expiry": expiry, "rows": rows}


def test_rolling_history_uses_asof_and_multiple_windows():
    snapshots = [snapshot(minute) for minute in range(0, 31, 3)]
    evidence = rolling_evidence(snapshots, NOW)
    assert evidence["bias"] == "bullish" and evidence["flow_bias"] == "bullish"
    assert evidence["valid_windows"] == 4
    assert evidence["oi_event_time"] is None
    assert evidence["last_observed_oi_change_at"] == snapshots[-1]["received_at"]
    assert rolling_evidence(snapshots, NOW.replace(minute=3))["bias"] == "unavailable"
    assert rolling_evidence(snapshots, NOW.replace(minute=0) - timedelta(seconds=1))["bias"] == "unavailable"
    assert rolling_evidence(snapshots, NOW + timedelta(minutes=11))["state"] == "stale"


def test_unchanged_oi_is_not_a_new_exchange_publication():
    snapshots = [snapshot(minute) for minute in range(0, 31, 3)]
    for row in snapshots:
        for contract in row["rows"]:
            contract["oi"] = 1000
    evidence = rolling_evidence(snapshots, NOW)
    assert evidence["bias"] == "unclear"
    assert evidence["last_observed_oi_change_at"] is None
    assert evidence["flow_bias"] == "bullish"


def test_missing_liquidity_blocks_flow_but_preserves_oi_evidence():
    old, new = snapshot(0), snapshot(6)
    for row in new["rows"]: row["bid_price"] = None
    result = compare_snapshots(new, old)
    assert result["flow_bias"] == "unavailable"
    assert result["bias"] == "bullish"


@pytest.mark.parametrize("problem", ["expiry", "coverage", "stale", "reset", "next_day"])
def test_unusable_options_fail_closed(problem):
    old, new = snapshot(0), snapshot(3)
    if problem == "expiry": new["expiry"] = "2026-10-20"
    if problem == "coverage": new["rows"] = new["rows"][:4]
    if problem == "stale": new["rows"][0]["exchange_timestamp"] = "2026-10-06T10:03:00+05:30"
    if problem == "reset": new["rows"][0]["volume"] = 0
    if problem == "next_day": new["received_at"] = "2026-10-08T10:03:00+05:30"
    assert compare_snapshots(new, old)["bias"] == "unavailable"


def test_option_history_is_durable_and_deduplicated(tmp_path):
    path = tmp_path / "evidence.db"
    history = OptionHistory(path)
    for _ in range(2): history.save("NIFTY", snapshot(3)["rows"])
    rows = OptionHistory(path).load("NIFTY", NOW - timedelta(hours=1), NOW)
    assert len(rows) == 1
    assert len(rows[0]["rows"]) == 8


def test_transactional_trade_dedup_uses_signal_bar_not_notification_time(tmp_path):
    repo = NiftyTradeRepository(tmp_path / "trades.db")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: repo.open_trade(signal()), range(6)))
    assert sum(row["created"] for row in results) == 1


def test_transactional_alert_persistence_is_idempotent(tmp_path):
    repo = NiftyAlertRepository(tmp_path / "alerts.db")
    def save(_):
        return repo.create_alert(alert_type="entry", mode="intraday", severity="watch", title="Test",
                                 message="Test only", trade_id="TEST", event_kind="entry")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(save, range(6)))
    assert len({row["id"] for row in rows}) == 1
    assert len(repo.list_recent_alerts()) == 1


@pytest.mark.parametrize("status", ["sent", "delivery_unknown", "failed"])
def test_outbox_claim_is_unique_and_terminal_status_is_not_resent(tmp_path, status):
    path = tmp_path / "outbox.db"
    outbox = AlertOutbox(path)
    for _ in range(2): outbox.enqueue("NIFTY", "TEST", "entry", "NIFTY_", "TEST ONLY", start=False)
    sender = Mock()
    sender.send_message.return_value = {"sent": status == "sent", "delivery_status": status}
    now = datetime.now(IST) + timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        counts = list(pool.map(lambda _: AlertOutbox(path).drain(sender, now), range(4)))
    assert sum(counts) == 1 and sender.send_message.call_count == 1
    assert outbox.history()[0]["status"] == status
    assert outbox.drain(sender, now + timedelta(seconds=40)) == 0


def test_outbox_retries_only_definitive_failure_and_bounds_attempts(tmp_path):
    outbox = AlertOutbox(tmp_path / "queue.db")
    outbox.enqueue("NIFTY", "TEST", "entry", "NIFTY_", "TEST ONLY", start=False)
    sender = Mock()
    sender.send_message.return_value = {"sent": False, "delivery_status": "retry", "retry_after": 30}
    now = datetime.now(IST) + timedelta(seconds=1)
    for attempt in range(3):
        assert outbox.drain(sender, now + timedelta(seconds=attempt * 31)) == 1
    assert outbox.history()[0]["attempts"] == 3
    assert outbox.history()[0]["status"] == "failed"


def test_expired_outbox_does_not_deliver_old_entry(tmp_path):
    outbox = AlertOutbox(tmp_path / "queue.db")
    outbox.enqueue("NIFTY", "TEST", "entry", "NIFTY_", "TEST ONLY", start=False)
    sender = Mock()
    outbox.drain(sender, datetime.now(IST) + timedelta(minutes=16))
    sender.send_message.assert_not_called()
    assert outbox.history()[0]["status"] == "expired"


def test_interrupted_delivery_is_unknown_not_retried(tmp_path):
    outbox = AlertOutbox(tmp_path / "queue.db")
    key = outbox.enqueue("NIFTY", "TEST", "entry", "NIFTY_", "TEST ONLY", start=False)
    with closing(outbox.connect()) as conn, conn:
        conn.execute("UPDATE alert_delivery_outbox SET status='sending' WHERE event_key=?", (key,))
    sender = Mock()
    assert outbox.drain(sender, datetime.now(IST) + timedelta(seconds=61)) == 0
    sender.send_message.assert_not_called()
    assert outbox.history()[0]["status"] == "delivery_unknown"


def bars():
    return [Candle(datetime(2026, 1, 1, tzinfo=IST) + timedelta(days=i),
                   100 + i, 102 + i, 99 + i, 101 + i, 1000) for i in range(180)]


def test_comparison_missing_history_never_fabricates_option_trades():
    result = compare_setups(bars(), "positional", [], from_date="2026-03-01", to_date="2026-06-30", now=NOW)
    assert result["variants"][0]["trade_count"] > 0
    for variant in result["variants"][1:]:
        assert variant["trade_count"] == 0
        assert variant["coverage"]["missing_option_evidence"] > 0
    assert len(result["variants"][0]["periods"]) == 2


def test_option_backtest_cannot_read_future_snapshots():
    future = [snapshot(minute) for minute in range(0, 31, 3)]
    result = compare_setups(bars(), "positional", future, from_date="2026-03-01", to_date="2026-06-30", now=NOW)
    assert result["observation_count"] == 0
    assert all(row["trade_count"] == 0 for row in result["variants"][1:])


def test_five_minute_research_requires_correct_horizon_and_trend_cache():
    with pytest.raises(ValueError, match="Intraday"):
        parameters({"horizon": "swing", "trigger": "15m_5m"})
    with pytest.raises(ValueError, match="trend candles"):
        compare_setups(bars(), "intraday", [], trigger="15m_5m", now=NOW)


def test_five_minute_trigger_sees_only_completed_fifteen_minute_trend():
    end = NOW.replace(second=0)
    trend = [Candle(end - timedelta(minutes=(59 - i) * 15), 100, 102, 99, 101, 100) for i in range(60)]
    def simulate_stub(candles, settings, entry_filter):
        assert settings.trigger_minutes == 5
        entry_filter(60, "bullish", NOW)
        return []
    with patch("trading_analysis.nifty.comparison_backtest.simulate", side_effect=simulate_stub), \
         patch("trading_analysis.nifty.comparison_backtest._historical_direction", return_value="bullish") as direction:
        compare_setups(bars(), "intraday", [], trigger="15m_5m", trend_candles=trend,
                       from_date="2026-01-01", now=NOW + timedelta(hours=1))
    assert direction.call_count == 4
    assert all(call.args[0].timestamp == end - timedelta(minutes=15) for call in direction.call_args_list)


def test_research_downloads_only_required_timeframes():
    analysis = Mock()
    analysis.refresh_candles.return_value = []
    refresh_comparison_data({"symbol": "BANKNIFTY", "horizon": "intraday", "trigger": "15m_5m", "from_date": "2026-10-01", "to_date": "2026-10-06"}, analysis)
    assert [call.args[:2] for call in analysis.refresh_candles.call_args_list] == [("BANKNIFTY", "5minute"), ("BANKNIFTY", "15minute")]
