"""Paper broker: simulates spot market orders with fee, slippage and Indonesian sell tax."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

QTY_DECIMALS = 6


@dataclass
class Fill:
    ts: str
    side: str
    qty: float
    price: float  # price after slippage
    fee: float
    tax: float
    reason: str
    bar_index: int


@dataclass
class ClosedTrade:
    entry_ts: str
    exit_ts: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float  # net of fee, tax, slippage
    pnl_pct: float
    bars_held: int
    exit_reason: str


@dataclass
class PaperBroker:
    cash: float
    fee_rate: float = 0.001
    tax_sell_rate: float = 0.0
    slippage_bps: float = 5.0
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: str = ""
    entry_bar: int = -1
    fills: list[Fill] = field(default_factory=list)
    closed: list[ClosedTrade] = field(default_factory=list)
    fees_paid: float = 0.0
    tax_paid: float = 0.0

    @property
    def in_position(self) -> bool:
        return self.qty > 0

    def equity(self, mark_price: float) -> float:
        return self.cash + self.qty * mark_price

    def _slip(self, price: float, side: str) -> float:
        s = self.slippage_bps / 10_000
        return price * (1 + s) if side == "buy" else price * (1 - s)

    def market_buy(self, qty: float, price: float, ts: str, bar_index: int, reason: str) -> Fill | None:
        qty = math.floor(qty * 10**QTY_DECIMALS) / 10**QTY_DECIMALS
        if qty <= 0 or self.in_position:
            return None
        fill_price = self._slip(price, "buy")
        notional = qty * fill_price
        fee = notional * self.fee_rate
        if notional + fee > self.cash + 1e-9:
            return None
        self.cash -= notional + fee
        self.fees_paid += fee
        self.qty, self.entry_price, self.entry_ts, self.entry_bar = qty, fill_price, ts, bar_index
        fill = Fill(ts, "buy", qty, fill_price, fee, 0.0, reason, bar_index)
        self.fills.append(fill)
        return fill

    def market_sell(self, price: float, ts: str, bar_index: int, reason: str) -> Fill | None:
        if not self.in_position:
            return None
        qty = self.qty
        fill_price = self._slip(price, "sell")
        notional = qty * fill_price
        fee = notional * self.fee_rate
        tax = notional * self.tax_sell_rate
        self.cash += notional - fee - tax
        self.fees_paid += fee
        self.tax_paid += tax
        entry_cost = qty * self.entry_price * (1 + self.fee_rate)
        pnl = (notional - fee - tax) - entry_cost
        self.closed.append(ClosedTrade(
            entry_ts=self.entry_ts, exit_ts=ts, qty=qty, entry_price=self.entry_price,
            exit_price=fill_price, pnl=pnl, pnl_pct=pnl / entry_cost * 100,
            bars_held=bar_index - self.entry_bar, exit_reason=reason,
        ))
        fill = Fill(ts, "sell", qty, fill_price, fee, tax, reason, bar_index)
        self.fills.append(fill)
        self.qty, self.entry_price, self.entry_ts, self.entry_bar = 0.0, 0.0, "", -1
        return fill

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PaperBroker":
        d = dict(d)
        d["fills"] = [Fill(**f) for f in d.get("fills", [])]
        d["closed"] = [ClosedTrade(**t) for t in d.get("closed", [])]
        return cls(**d)
