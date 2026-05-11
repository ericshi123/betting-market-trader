import json
from unittest.mock import patch

import pytest

import src.calibration as cal_mod
from src.calibration import calibration_report, record_resolution


def _pos(
    direction="BUY_YES",
    pnl=10.0,
    entry_price=0.60,
    model_prob=0.75,
    confidence="medium",
    ticker="MKT-01",
    strategy=None,
):
    pos = {
        "id": "test-id",
        "ticker": ticker,
        "question": "Test question?",
        "direction": direction,
        "amount": 20.0,
        "entry_price": entry_price,
        "model_prob": model_prob,
        "edge": 0.15,
        "confidence": confidence,
        "opened_at": "2026-01-01T00:00:00+00:00",
        "closed_at": "2026-01-02T00:00:00+00:00",
        "pnl": pnl,
        "status": "closed",
    }
    if strategy is not None:
        pos["strategy"] = strategy
    return pos


def _record(
    outcome="YES",
    direction="BUY_YES",
    pnl=10.0,
    model_prob=0.7,
    confidence="medium",
    strategy="paper",
):
    return {
        "ticker": "MKT-01",
        "question": "Test?",
        "portfolio": "paper",
        "strategy": strategy,
        "direction": direction,
        "model_prob": model_prob,
        "market_prob": 0.55,
        "edge": 0.15,
        "confidence": confidence,
        "outcome": outcome,
        "pnl": pnl,
        "opened_at": "2026-01-01T00:00:00+00:00",
        "closed_at": "2026-01-02T00:00:00+00:00",
    }


# ── record_resolution ─────────────────────────────────────────────────────────

def test_record_resolution_appends_correct_fields(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        record_resolution(_pos(), "paper")

    records = json.loads(log_path.read_text())
    assert len(records) == 1
    r = records[0]
    assert r["ticker"] == "MKT-01"
    assert r["question"] == "Test question?"
    assert r["portfolio"] == "paper"
    assert r["direction"] == "BUY_YES"
    assert r["model_prob"] == 0.75
    assert r["market_prob"] == 0.60
    assert r["edge"] == 0.15
    assert r["confidence"] == "medium"
    assert r["outcome"] == "YES"   # BUY_YES + positive pnl → YES
    assert r["pnl"] == 10.0
    assert r["opened_at"] == "2026-01-01T00:00:00+00:00"
    assert r["closed_at"] == "2026-01-02T00:00:00+00:00"


def test_record_resolution_derives_no_outcome_on_loss(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        record_resolution(_pos(direction="BUY_YES", pnl=-20.0), "paper")
    records = json.loads(log_path.read_text())
    assert records[0]["outcome"] == "NO"


def test_record_resolution_buy_no_win_is_no(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        record_resolution(_pos(direction="BUY_NO", pnl=5.0), "momentum")
    records = json.loads(log_path.read_text())
    assert records[0]["outcome"] == "NO"   # BUY_NO + win → NO resolved


def test_record_resolution_appends_multiple(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        record_resolution(_pos(ticker="A"), "paper")
        record_resolution(_pos(ticker="B"), "paper")
    records = json.loads(log_path.read_text())
    assert len(records) == 2
    assert records[0]["ticker"] == "A"
    assert records[1]["ticker"] == "B"


# ── calibration_report Brier score ────────────────────────────────────────────

def test_calibration_report_brier_score(tmp_path):
    # (0.8 - 1)^2 = 0.04  and  (0.6 - 0)^2 = 0.36  →  mean = 0.20
    log_path = tmp_path / "calibration_log.json"
    records = [
        _record(outcome="YES", model_prob=0.8, pnl=13.33),
        _record(outcome="NO", model_prob=0.6, pnl=-20.0),
    ]
    log_path.write_text(json.dumps(records))
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        report = calibration_report()
    assert report["brier_score"] == pytest.approx(0.20, abs=0.001)


# ── win rate + by_confidence breakdown ────────────────────────────────────────

def test_win_rate_and_confidence_breakdown(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    records = [
        _record(confidence="high", pnl=10.0, outcome="YES"),
        _record(confidence="high", pnl=-20.0, outcome="NO"),
        _record(confidence="medium", pnl=5.0, outcome="YES"),
    ]
    log_path.write_text(json.dumps(records))
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        report = calibration_report()

    assert report["total"] == 3
    assert report["wins"] == 2
    assert report["losses"] == 1
    assert report["win_rate"] == pytest.approx(2 / 3, rel=0.01)

    high = report["by_confidence"]["high"]
    assert high["n"] == 2
    assert high["win_rate"] == pytest.approx(0.5, rel=0.01)

    medium = report["by_confidence"]["medium"]
    assert medium["n"] == 1
    assert medium["win_rate"] == pytest.approx(1.0, rel=0.01)

    low = report["by_confidence"]["low"]
    assert low["n"] == 0


# ── by_strategy breakdown ─────────────────────────────────────────────────────

def test_by_strategy_breakdown(tmp_path):
    log_path = tmp_path / "calibration_log.json"
    records = [
        _record(strategy="momentum", pnl=10.0),
        _record(strategy="momentum", pnl=-5.0),
        _record(strategy="news", pnl=8.0),
    ]
    log_path.write_text(json.dumps(records))
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        report = calibration_report()

    assert "momentum" in report["by_strategy"]
    assert "news" in report["by_strategy"]
    assert report["by_strategy"]["momentum"]["n"] == 2
    assert report["by_strategy"]["news"]["n"] == 1


# ── empty log ─────────────────────────────────────────────────────────────────

def test_empty_log_returns_zeroed_report(tmp_path):
    log_path = tmp_path / "missing_log.json"   # does not exist
    with patch.object(cal_mod, "_LOG_PATH", log_path):
        report = calibration_report()

    assert report["total"] == 0
    assert report["wins"] == 0
    assert report["losses"] == 0
    assert report["win_rate"] == 0.0
    assert report["brier_score"] is None
    assert report["expected_value"] == 0.0
    assert report["by_confidence"]["low"]["n"] == 0
    assert report["by_confidence"]["medium"]["n"] == 0
    assert report["by_confidence"]["high"]["n"] == 0
    assert report["by_strategy"] == {}
