import json

import pytest

from claudetrader.config import Config
from claudetrader.engine import Engine, EngineState
from claudetrader.live_broker import LiveBroker
from claudetrader.notify import Notifier
from claudetrader.runner import LIVE_CONFIRM_PHRASE, check_live_preconditions, handle_stop_file, load_engine, save_engine
from tests.conftest import synthetic_candles
from tests.fake_exchange import FakeExchange


def live_cfg(tmp_path, **kw) -> Config:
    base = dict(exchange="binance", execution="live", live_confirm=LIVE_CONFIRM_PHRASE, api_key="k",
                api_secret="s", data_dir=tmp_path, initial_cash=1000.0, tax_sell_rate=0.01,
                max_order_notional=300.0, max_daily_orders=20)
    base.update(kw)
    return Config(**base)


def test_preconditions_block_live_by_default(tmp_path):
    cfg = Config(execution="live", data_dir=tmp_path)
    problems = check_live_preconditions(cfg)
    assert any("LIVE_CONFIRM" in p for p in problems) and any("API_KEY" in p for p in problems)
    assert check_live_preconditions(live_cfg(tmp_path)) == []
    assert check_live_preconditions(live_cfg(tmp_path, max_position_pct=0.8))


def test_paper_mode_refuses_live_state_file(tmp_path):
    cfg = live_cfg(tmp_path)
    ex = FakeExchange(price=100.0, quote=1000.0)
    engine = load_engine(cfg, exchange=ex)
    save_engine(cfg, engine)
    paper_cfg = Config(execution="paper", data_dir=tmp_path)
    with pytest.raises(ValueError):
        load_engine(paper_cfg)


def test_live_engine_trades_through_exchange_and_resumes(tmp_path, cfg):
    lcfg = live_cfg(tmp_path)
    ex = FakeExchange(price=100.0, quote=1000.0)
    engine = load_engine(lcfg, exchange=ex)
    assert engine.broker.cash == 1000.0 and engine.risk.state.peak_equity == 1000.0

    candles = synthetic_candles()
    df = engine.strategy.prepare(candles)
    for i in range(lcfg.warmup_bars, len(df)):
        ex.price = float(df["close"].iloc[i])     # the fake exchange fills at the candle close
        engine.step(df, i)
    buys = [f for f in engine.broker.fills if f.side == "buy"]
    assert buys, "strategy should have traded on the synthetic data"
    assert all(f.qty * f.price <= lcfg.max_order_notional + 1e-6 for f in buys)
    assert abs(engine.broker.cash - ex.balances["USDT"]) < 1e-6   # ledger == exchange

    save_engine(lcfg, engine)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["broker"]["kind"] == "live"
    engine2 = load_engine(lcfg, exchange=ex)                      # reconcile passes, state restored
    assert engine2.broker.qty == engine.broker.qty and len(engine2.broker.closed) == len(engine.broker.closed)


def test_stop_file_closes_position_and_halts(tmp_path):
    lcfg = live_cfg(tmp_path)
    ex = FakeExchange(price=100.0, quote=1000.0)
    engine = load_engine(lcfg, exchange=ex)
    engine.broker.market_buy(1.0, 100.0, "t0", 0, "entry")
    from claudetrader.strategy import OpenPosition
    engine.state.position = OpenPosition(100.0, 1.0, 95.0, 0, 100.0)
    assert handle_stop_file(lcfg, engine, price=100.0) is False
    lcfg.stop_file.write_text("x")
    ex.price = 101.0
    assert handle_stop_file(lcfg, engine, price=101.0) is True
    assert engine.state.position is None and not engine.broker.in_position
    assert engine.broker.closed[-1].exit_reason == "STOP file"
    assert "STOP" in engine.risk.state.halted_reason
    assert not engine.risk.can_open(1000.0, 5).allowed
