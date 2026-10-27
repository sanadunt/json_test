import math

from claudetrader.broker import PaperBroker


def test_buy_sell_accounting_with_fee_tax_slippage():
    b = PaperBroker(cash=1000.0, fee_rate=0.001, tax_sell_rate=0.0021, slippage_bps=10)
    fill = b.market_buy(qty=1.0, price=100.0, ts="t0", bar_index=0, reason="test")
    assert fill is not None
    assert math.isclose(fill.price, 100.1)                       # 10 bps slippage up
    assert math.isclose(b.cash, 1000 - 100.1 - 0.1001)            # notional + 0.1% fee
    assert b.in_position and b.qty == 1.0

    fill = b.market_sell(price=110.0, ts="t1", bar_index=5, reason="exit")
    assert math.isclose(fill.price, 109.89)                      # slippage down
    notional = 109.89
    assert math.isclose(fill.fee, notional * 0.001)
    assert math.isclose(fill.tax, notional * 0.0021)
    assert not b.in_position
    t = b.closed[0]
    expected_pnl = (notional - fill.fee - fill.tax) - 100.1 * 1.001
    assert math.isclose(t.pnl, expected_pnl)
    assert t.bars_held == 5 and t.exit_reason == "exit"
    assert math.isclose(b.equity(0.0), b.cash)


def test_cannot_overspend_or_double_buy():
    b = PaperBroker(cash=100.0, fee_rate=0.001)
    assert b.market_buy(qty=2.0, price=100.0, ts="t", bar_index=0, reason="x") is None
    assert b.market_buy(qty=0.5, price=100.0, ts="t", bar_index=0, reason="x") is not None
    assert b.market_buy(qty=0.1, price=100.0, ts="t", bar_index=1, reason="x") is None
    assert b.market_sell(price=100.0, ts="t", bar_index=2, reason="x") is not None
    assert b.market_sell(price=100.0, ts="t", bar_index=3, reason="x") is None


def test_qty_is_floored_not_rounded():
    b = PaperBroker(cash=1000.0)
    fill = b.market_buy(qty=0.1234567899, price=1.0, ts="t", bar_index=0, reason="x")
    assert fill.qty == 0.123456


def test_roundtrip_serialization():
    b = PaperBroker(cash=500.0, fee_rate=0.002)
    b.market_buy(1.0, 10.0, "t0", 0, "a")
    b.market_sell(12.0, "t1", 3, "b")
    b2 = PaperBroker.from_dict(b.to_dict())
    assert b2.cash == b.cash and len(b2.closed) == 1 and b2.closed[0].pnl == b.closed[0].pnl
