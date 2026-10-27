import math

import pytest

from claudetrader.live_broker import LiveBroker, LiveBrokerError, ReconcileError
from claudetrader.notify import Notifier
from tests.fake_exchange import FakeExchange


def make(ex: FakeExchange, **kw) -> LiveBroker:
    kw.setdefault("max_order_notional", 500.0)
    kw.setdefault("max_daily_orders", 5)
    b = LiveBroker(ex, "BTC/USDT", fee_rate=0.001, tax_sell_rate=0.0021, notifier=Notifier(echo=False),
                   fill_timeout_s=0.1, poll_interval_s=0.01, **kw)
    b.reconcile(local_qty=0.0)
    return b


def test_buy_and_sell_use_real_fills_and_fees():
    ex = FakeExchange(price=100.0, quote=1000.0)
    b = make(ex)
    assert b.cash == 1000.0
    fill = b.market_buy(qty=2.0, price=100.0, ts="t0", bar_index=0, reason="entry")
    assert fill and fill.qty == 2.0 and fill.price == 100.0
    assert math.isclose(b.cash, 1000.0 - 200.0 - 0.2)        # fee from the exchange, not estimated
    assert math.isclose(b.cash, ex.balances["USDT"])            # ledger matches the exchange
    assert b.in_position and b.qty == 2.0

    ex.price = 110.0
    fill = b.market_sell(price=110.0, ts="t1", bar_index=4, reason="exit")
    assert fill and fill.price == 110.0
    assert not b.in_position
    t = b.closed[0]
    assert t.exit_reason == "exit" and t.bars_held == 4
    assert math.isclose(t.pnl, (220.0 - 0.22 - 220.0 * 0.0021) - 200.0 * 1.001)  # pnl charges estimated tax
    assert math.isclose(b.cash, ex.balances["USDT"])                               # cash follows the exchange
    assert math.isclose(b.tax_paid, 220.0 * 0.0021)
    assert b.notifier.sent and "BUY" in b.notifier.sent[0] and "SELL" in b.notifier.sent[1]


def test_order_notional_is_capped():
    ex = FakeExchange(price=100.0, quote=10_000.0)
    b = make(ex, max_order_notional=150.0)
    fill = b.market_buy(qty=5.0, price=100.0, ts="t", bar_index=0, reason="x")
    assert fill.qty == pytest.approx(1.5)


def test_too_small_orders_are_skipped_not_sent():
    ex = FakeExchange(price=100.0, quote=1000.0, min_cost=50.0)
    b = make(ex)
    assert b.market_buy(qty=0.1, price=100.0, ts="t", bar_index=0, reason="x") is None
    assert not any(c[0] == "create_order" for c in ex.calls)


def test_daily_order_cap_raises():
    ex = FakeExchange(price=100.0, quote=10_000.0)
    b = make(ex, max_daily_orders=2)
    b.market_buy(1.0, 100.0, "t0", 0, "a")
    b.market_sell(100.0, "t1", 1, "b")
    with pytest.raises(LiveBrokerError):
        b.market_buy(1.0, 100.0, "t2", 2, "c")


def test_reconcile_refuses_on_mismatch():
    ex = FakeExchange(price=100.0, quote=1000.0, base=0.0)
    b = LiveBroker(ex, "BTC/USDT", notifier=Notifier(echo=False))
    with pytest.raises(ReconcileError):
        b.reconcile(local_qty=1.0)          # local thinks we hold 1 BTC, exchange has 0
    ex2 = FakeExchange(price=100.0, quote=1000.0, base=1.0)
    b2 = LiveBroker(ex2, "BTC/USDT", notifier=Notifier(echo=False))
    b2.reconcile(local_qty=1.0)             # matches: fine


def test_unfilled_order_is_cancelled_and_raises():
    ex = FakeExchange(price=100.0, quote=1000.0, fill=False)
    b = make(ex)
    with pytest.raises(LiveBrokerError):
        b.market_buy(1.0, 100.0, "t", 0, "x")
    assert any(c[0] == "cancel_order" for c in ex.calls)
    assert not b.in_position and b.cash == 1000.0


def test_exchange_stop_is_placed_moved_and_cancelled_on_sell():
    ex = FakeExchange(price=100.0, quote=1000.0)
    b = make(ex, exchange_stop=True)
    b.market_buy(1.0, 100.0, "t0", 0, "entry")
    b.update_stop(stop_price=95.0, ref_price=100.0)
    stops = [c for c in ex.calls if c[0] == "create_order" and c[1] == "stop_loss_limit"]
    assert len(stops) == 1 and stops[0][5]["stopPrice"] == 95.0 and stops[0][4] == pytest.approx(94.53, abs=0.01)
    first_id = b.stop_order_id
    b.update_stop(stop_price=95.02, ref_price=101.0)   # < 0.1% move: no churn
    assert b.stop_order_id == first_id
    b.update_stop(stop_price=97.0, ref_price=103.0)    # trailing up: cancel + replace
    assert b.stop_order_id != first_id and ex.orders[first_id]["status"] == "canceled"
    b.market_sell(103.0, "t1", 3, "cross down")
    assert ex.orders[b.closed and ex.orders and list(ex.orders)[-2]]["status"] in ("canceled", "closed")
    assert b.stop_order_id is None and not b.in_position


def test_sync_detects_exchange_stop_fill():
    ex = FakeExchange(price=100.0, quote=1000.0)
    b = make(ex, exchange_stop=True)
    b.market_buy(1.0, 100.0, "t0", 0, "entry")
    b.update_stop(95.0, 100.0)
    assert b.sync("t1", 1) is None                      # nothing happened yet
    ex.trigger_stop_fill(b.stop_order_id, fill_price=94.8)
    closed = b.sync("t2", 2)
    assert closed is not None and closed.exit_reason == "exchange stop filled"
    assert closed.exit_price == 94.8 and closed.pnl < 0
    assert not b.in_position and b.stop_order_id is None
    assert math.isclose(b.cash, ex.balances["USDT"])


def test_sync_detects_manual_sell_without_stop():
    ex = FakeExchange(price=100.0, quote=1000.0)
    b = make(ex)
    b.market_buy(1.0, 100.0, "t0", 0, "entry")
    ex.balances["BTC"] = 0.0; ex.balances["USDT"] += 99.0   # someone sold by hand on the exchange
    closed = b.sync("t1", 1)
    assert closed is not None and "outside bot" in closed.exit_reason


def test_fee_in_base_currency_is_converted():
    ex = FakeExchange(price=200.0, quote=1000.0)
    b = make(ex)
    order = {"filled": 1.0, "fees": [{"cost": 0.001, "currency": "BTC"}]}
    assert b._fee_from_order(order, 200.0) == pytest.approx(0.2)
    assert b._fee_from_order({"filled": 1.0}, 200.0) == pytest.approx(0.2)  # estimate when fees missing


def test_ledger_roundtrip():
    ex = FakeExchange(price=100.0, quote=1000.0)
    b = make(ex)
    b.market_buy(1.0, 100.0, "t0", 0, "entry")
    d = b.to_dict()
    assert d["kind"] == "live"
    b2 = LiveBroker(FakeExchange(), "BTC/USDT").load_ledger(d)
    assert b2.qty == 1.0 and b2.entry_price == 100.0 and len(b2.fills) == 1 and b2.orders_by_day
