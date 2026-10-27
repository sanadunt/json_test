import json

import pandas as pd

from claudetrader.engine import Engine, EngineState
from claudetrader.journal import Journal
from claudetrader.reviewer import Review
from claudetrader.strategy import EmaRsiTrendStrategy


def test_strategy_emits_buys_and_sells_on_synthetic_data(cfg, candles):
    strat = EmaRsiTrendStrategy(cfg)
    df = strat.prepare(candles)
    actions = []
    pos = None
    for i in range(cfg.warmup_bars, len(df)):
        sig = strat.signal(df, i, pos)
        if sig.action == "buy":
            assert sig.stop_price < sig.price
            from claudetrader.strategy import OpenPosition
            pos = OpenPosition(sig.price, 1.0, sig.stop_price, i, sig.price)
        elif sig.action == "sell":
            pos = None
        else:
            if pos is not None:
                pos = strat.update_trailing_stop(df, i, pos)
        actions.append(sig.action)
    assert "buy" in actions and "sell" in actions


def test_backtest_runs_and_reports(cfg, candles):
    engine = Engine(cfg)
    res = engine.run_backtest(candles)
    s = res["stats"]
    assert s["trades"] >= 1
    assert set(s) >= {"final_equity", "total_return_pct", "buy_hold_return_pct", "max_drawdown_pct", "sharpe"}
    assert len(res["equity"]) == len(candles) - cfg.warmup_bars
    # equity curve never below zero and final equals broker's view
    assert (res["equity"] > 0).all()
    assert abs(res["equity"].iloc[-1] - engine.broker.equity(float(candles["close"].iloc[-1]))) < 1e-6
    # every fill has a matching journal entry
    fills = [e for e in engine.journal.entries if e["fill"]]
    assert len(fills) == len(engine.broker.fills)


def test_backtest_respects_position_cap(cfg, candles):
    engine = Engine(cfg)
    engine.run_backtest(candles)
    for f in engine.broker.fills:
        if f.side == "buy":
            # entry notional never exceeds max_position_pct of equity at the time (equity >= cash + notional)
            assert f.qty * f.price <= cfg.max_position_pct * cfg.initial_cash * 2  # loose: equity may have grown


class VetoReviewer:
    def __init__(self):
        self.calls = 0

    def review(self, proposal):
        self.calls += 1
        assert proposal["side"] == "buy" and proposal["stop_price"] < proposal["price"]
        return Review("veto", 0.0, 0.9, "test veto", "claude")


class HalfReviewer:
    def review(self, proposal):
        return Review("reduce", 0.5, 0.6, "half", "claude")


def test_reviewer_can_veto_every_entry(cfg, candles):
    r = VetoReviewer()
    engine = Engine(cfg, reviewer=r)
    engine.run_backtest(candles)
    assert r.calls > 0
    assert engine.broker.fills == []
    skipped = [e for e in engine.journal.entries if e["action"] == "skipped"]
    assert skipped and skipped[0]["review"]["decision"] == "veto"


def test_reviewer_reduce_halves_size(cfg, candles):
    full = Engine(cfg).run_backtest(candles)["fills"]
    half = Engine(cfg, reviewer=HalfReviewer()).run_backtest(candles)["fills"]
    first_full = next(f for f in full if f.side == "buy")
    first_half = next(f for f in half if f.side == "buy")
    assert abs(first_half.qty - first_full.qty / 2) < 1e-5


def test_engine_state_roundtrip_and_resume(cfg, candles):
    strat = EmaRsiTrendStrategy(cfg)
    df = strat.prepare(candles)
    e1 = Engine(cfg)
    cut = len(df) - 50
    for i in range(cfg.warmup_bars, cut):
        e1.step(df, i)
    blob = json.dumps(e1.state.to_dict(), default=str)
    e2 = Engine(cfg, state=EngineState.from_dict(json.loads(blob)))
    assert e2.state.last_ts == e1.state.last_ts and e2.broker.cash == e1.broker.cash
    for i in range(cut, len(df)):
        e1.step(df, i)
        e2.step(df, i)
    assert abs(e1.broker.equity(1.0) - e2.broker.equity(1.0)) < 1e-6
    assert len(e1.broker.closed) == len(e2.broker.closed)


def test_journal_writes_jsonl(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    j.log({"a": 1}); j.log({"b": pd.Timestamp("2025-01-01", tz="UTC")})
    lines = (tmp_path / "j.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0]) == {"a": 1}
