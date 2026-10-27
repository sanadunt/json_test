"""Minimal ccxt-like exchange for tests. Market orders fill instantly at `price`, fees in quote."""

from __future__ import annotations


class FakeExchange:
    id = "fake"
    has = {"createStopLimitOrder": True, "createMarketOrder": True, "sandbox": True}

    def __init__(self, price: float = 100.0, quote: float = 1000.0, base: float = 0.0, fee_rate: float = 0.001,
                 min_amount: float = 0.0001, min_cost: float = 10.0, fill: bool = True):
        self.price, self.fee_rate, self.fill = price, fee_rate, fill
        self.balances = {"USDT": quote, "BTC": base}
        self.orders: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.markets = {"BTC/USDT": {"limits": {"amount": {"min": min_amount}, "cost": {"min": min_cost}},
                                     "precision": {"amount": 6, "price": 2}}}
        self._n = 0

    def load_markets(self):
        self.calls.append(("load_markets",))
        return self.markets

    def amount_to_precision(self, symbol, amount):
        return f"{float(amount):.6f}"

    def price_to_precision(self, symbol, price):
        return f"{float(price):.2f}"

    def fetch_balance(self):
        return {"free": dict(self.balances), "used": {"USDT": 0.0, "BTC": 0.0}, "total": dict(self.balances)}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self._n += 1
        oid = f"o{self._n}"
        self.calls.append(("create_order", type_, side, float(amount), price, params))
        if type_ == "stop_loss_limit":
            o = {"id": oid, "status": "open", "filled": 0.0, "average": None, "amount": float(amount),
                 "type": type_, "side": side, "stopPrice": params["stopPrice"], "price": price}
            self.orders[oid] = o
            return dict(o)
        if not self.fill:
            o = {"id": oid, "status": "open", "filled": 0.0, "average": None, "amount": float(amount)}
            self.orders[oid] = o
            return dict(o)
        amount = float(amount)
        cost = amount * self.price
        fee = cost * self.fee_rate
        if side == "buy":
            if self.balances["USDT"] < cost + fee:
                raise Exception("InsufficientFunds")
            self.balances["USDT"] -= cost + fee
            self.balances["BTC"] += amount
        else:
            if self.balances["BTC"] < amount - 1e-12:
                raise Exception("InsufficientFunds")
            self.balances["BTC"] -= amount
            self.balances["USDT"] += cost - fee
        o = {"id": oid, "status": "closed", "filled": amount, "average": self.price, "amount": amount,
             "fee": {"cost": fee, "currency": "USDT"}, "type": type_, "side": side}
        self.orders[oid] = o
        return dict(o)

    def fetch_order(self, oid, symbol=None):
        self.calls.append(("fetch_order", oid))
        return dict(self.orders[oid])

    def cancel_order(self, oid, symbol=None):
        self.calls.append(("cancel_order", oid))
        o = self.orders[oid]
        if o["status"] == "closed":
            raise Exception("OrderNotFound: already filled")
        o["status"] = "canceled"
        return dict(o)

    def fetch_open_orders(self, symbol=None):
        return [dict(o) for o in self.orders.values() if o["status"] == "open"]

    # test helpers
    def trigger_stop_fill(self, oid: str, fill_price: float):
        """Simulate the exchange filling the stop order while the bot slept."""
        o = self.orders[oid]
        amount = o["amount"]
        self.balances["BTC"] -= amount
        self.balances["USDT"] += amount * fill_price * (1 - self.fee_rate)
        o.update(status="closed", filled=amount, average=fill_price,
                 fee={"cost": amount * fill_price * self.fee_rate, "currency": "USDT"})
