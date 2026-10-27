"""Hard risk limits. These run in code on every decision and cannot be overridden by the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import pandas as pd

from .config import Config


@dataclass
class RiskState:
    peak_equity: float
    day: str | None = None
    day_start_equity: float = 0.0
    halted_reason: str | None = None
    cooldown_until_bar: int = -1
    last_trade_pnl: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RiskState":
        return cls(**d)


@dataclass
class RiskCheck:
    allowed: bool
    reason: str
    details: dict = field(default_factory=dict)


class RiskManager:
    def __init__(self, cfg: Config, state: RiskState | None = None):
        self.cfg = cfg
        self.state = state or RiskState(peak_equity=cfg.initial_cash)

    def on_bar(self, ts: pd.Timestamp, equity: float) -> None:
        """Roll the daily window and track the equity peak. Call once per closed bar."""
        day = ts.strftime("%Y-%m-%d")
        if self.state.day != day:
            self.state.day = day
            self.state.day_start_equity = equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
        dd = 1 - equity / self.state.peak_equity if self.state.peak_equity > 0 else 0.0
        if dd >= self.cfg.max_drawdown and self.state.halted_reason is None:
            self.state.halted_reason = f"max drawdown {dd:.1%} >= {self.cfg.max_drawdown:.0%} (kill switch)"

    def on_trade_closed(self, pnl: float, bar_index: int) -> None:
        self.state.last_trade_pnl = pnl
        if pnl < 0:
            self.state.cooldown_until_bar = bar_index + self.cfg.cooldown_bars

    def can_open(self, equity: float, bar_index: int) -> RiskCheck:
        s = self.state
        if s.halted_reason:
            return RiskCheck(False, s.halted_reason)
        if s.day_start_equity > 0:
            day_loss = 1 - equity / s.day_start_equity
            if day_loss >= self.cfg.daily_loss_limit:
                return RiskCheck(False, f"daily loss {day_loss:.1%} >= limit {self.cfg.daily_loss_limit:.0%}")
        if bar_index < s.cooldown_until_bar:
            return RiskCheck(False, f"cooldown after loss until bar {s.cooldown_until_bar}")
        return RiskCheck(True, "ok")

    def size(self, equity: float, cash: float, price: float, stop_price: float) -> tuple[float, dict]:
        """Quantity so that a stop-out loses `risk_per_trade` of equity, capped by position and cash limits."""
        risk_amount = equity * self.cfg.risk_per_trade
        per_unit_risk = price - stop_price
        if per_unit_risk <= 0:
            return 0.0, {"reason": "stop not below price"}
        qty_risk = risk_amount / per_unit_risk
        qty_pos_cap = equity * self.cfg.max_position_pct / price
        cost_mult = 1 + self.cfg.fee_rate + self.cfg.slippage_bps / 10_000
        qty_cash_cap = cash / (price * cost_mult)
        qty = max(0.0, min(qty_risk, qty_pos_cap, qty_cash_cap))
        return qty, {
            "risk_amount": risk_amount,
            "qty_by_risk": qty_risk,
            "qty_cap_position": qty_pos_cap,
            "qty_cap_cash": qty_cash_cap,
            "binding": min(
                ("risk", qty_risk), ("position_cap", qty_pos_cap), ("cash", qty_cash_cap), key=lambda t: t[1]
            )[0],
        }
