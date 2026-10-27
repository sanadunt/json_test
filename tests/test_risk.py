import pandas as pd

from claudetrader.risk import RiskManager, RiskState


def ts(day: int, hour: int = 0) -> pd.Timestamp:
    return pd.Timestamp(f"2025-01-{day:02d} {hour:02d}:00", tz="UTC")


def test_size_by_risk_then_capped(cfg):
    rm = RiskManager(cfg)
    # 1% of 1000 = 10 at risk; stop 2 below price -> 5 units; position cap 50% -> 500/100 = 5 units
    qty, info = rm.size(equity=1000, cash=1000, price=100, stop_price=98)
    assert abs(qty - 5.0) < 1e-9
    # tighter stop -> risk sizing would allow 20 units; position cap binds at 5
    qty, info = rm.size(equity=1000, cash=1000, price=100, stop_price=99.5)
    assert abs(qty - 5.0) < 1e-9 and info["binding"] == "position_cap"
    # little cash -> cash cap binds
    qty, info = rm.size(equity=1000, cash=100, price=100, stop_price=98)
    assert qty < 1.0 and info["binding"] == "cash"
    # stop above price -> no trade
    assert rm.size(1000, 1000, 100, 101)[0] == 0.0


def test_daily_loss_limit_blocks_until_next_day(cfg):
    rm = RiskManager(cfg)
    rm.on_bar(ts(1, 0), 1000.0)
    assert rm.can_open(1000.0, 0).allowed
    rm.on_bar(ts(1, 5), 965.0)  # -3.5% today
    assert not rm.can_open(965.0, 5).allowed
    rm.on_bar(ts(2, 0), 965.0)  # new day resets the window
    assert rm.can_open(965.0, 24).allowed


def test_max_drawdown_kill_switch_is_sticky(cfg):
    rm = RiskManager(cfg)
    rm.on_bar(ts(1), 1000.0)
    rm.on_bar(ts(2), 1200.0)
    rm.on_bar(ts(3), 1000.0)  # -16.7% from peak
    assert not rm.can_open(1000.0, 48).allowed
    rm.on_bar(ts(4), 1300.0)  # recovery does not un-halt
    assert not rm.can_open(1300.0, 72).allowed
    assert "kill switch" in rm.state.halted_reason


def test_cooldown_after_loss(cfg):
    rm = RiskManager(cfg)
    rm.on_bar(ts(1), 1000.0)
    rm.on_trade_closed(pnl=-5.0, bar_index=10)
    assert not rm.can_open(995.0, 11).allowed
    assert rm.can_open(995.0, 13).allowed
    rm.on_trade_closed(pnl=+5.0, bar_index=20)
    assert rm.can_open(1000.0, 21).allowed


def test_state_roundtrip(cfg):
    rm = RiskManager(cfg)
    rm.on_bar(ts(1), 1000.0)
    st = RiskState.from_dict(rm.state.to_dict())
    assert st == rm.state
