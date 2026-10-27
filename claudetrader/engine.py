"""The decision pipeline shared by backtests and the paper-trading loop.

For each closed candle:
  strategy.signal  ->  risk.can_open / risk.size  ->  (optional) Claude review  ->  broker fill
Orders fill at the candle's close plus slippage. The next candle is not looked at.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .broker import PaperBroker
from .config import Config
from .journal import Journal
from .metrics import summarize
from .reviewer import ClaudeReviewer, Review
from .risk import RiskManager, RiskState
from .strategy import EmaRsiTrendStrategy, OpenPosition


@dataclass
class EngineState:
    broker: PaperBroker
    risk: RiskState
    position: OpenPosition | None
    last_ts: str | None = None
    bars_seen: int = 0

    def to_dict(self) -> dict:
        return {
            "broker": self.broker.to_dict(),
            "risk": self.risk.to_dict(),
            "position": self.position.__dict__ if self.position else None,
            "last_ts": self.last_ts,
            "bars_seen": self.bars_seen,
        }

    @classmethod
    def from_dict(cls, d: dict, broker_factory=None) -> "EngineState":
        """broker_factory(dict) builds the broker; defaults to PaperBroker. Live state needs a LiveBroker."""
        pos = OpenPosition(**d["position"]) if d.get("position") else None
        bd = d["broker"]
        if broker_factory is None:
            if bd.get("kind") == "live":
                raise ValueError("this state file belongs to a live run; load it with EXECUTION=live")
            broker = PaperBroker.from_dict({k: v for k, v in bd.items() if k != "kind"})
        else:
            broker = broker_factory(bd)
        return cls(broker, RiskState.from_dict(d["risk"]), pos, d.get("last_ts"), d.get("bars_seen", 0))


@dataclass
class StepResult:
    ts: str
    action: str
    reason: str
    equity: float
    review: Review | None = None
    fill: dict | None = None
    extra: dict = field(default_factory=dict)


class Engine:
    def __init__(self, cfg: Config, state: EngineState | None = None,
                 reviewer: ClaudeReviewer | None = None, journal: Journal | None = None, broker=None):
        self.cfg = cfg
        self.strategy = EmaRsiTrendStrategy(cfg)
        if state is None:
            if broker is None:
                broker = PaperBroker(cfg.initial_cash, cfg.fee_rate, cfg.tax_sell_rate, cfg.slippage_bps)
            start_equity = broker.equity(0.0) if broker.equity(0.0) > 0 else cfg.initial_cash
            state = EngineState(broker, RiskState(peak_equity=start_equity), None)
        self.state = state
        self.risk = RiskManager(cfg, state.risk)
        self.reviewer = reviewer
        self.journal = journal or Journal(None)
        self.equity_curve: list[tuple[pd.Timestamp, float]] = []

    @property
    def broker(self) -> PaperBroker:
        return self.state.broker

    def step(self, df: pd.DataFrame, i: int) -> StepResult:
        """Process closed candle `i` of an indicator-prepared frame."""
        row = df.iloc[i]
        ts = row["timestamp"]
        ts_str = ts.isoformat()
        close = float(row["close"])
        bar = self.state.bars_seen
        st = self.state

        # live only: a position may have been closed by an exchange-side stop or by hand since last bar
        if st.position is not None and hasattr(self.broker, "sync"):
            closed_outside = self.broker.sync(ts_str, bar)
            if closed_outside is not None:
                self.risk.on_trade_closed(closed_outside.pnl, bar)
                st.position = None

        if st.position is not None:
            st.position = self.strategy.update_trailing_stop(df, i, st.position)
            if hasattr(self.broker, "update_stop"):
                self.broker.update_stop(st.position.stop_price, close)

        equity = self.broker.equity(close)
        self.risk.on_bar(ts, equity)
        sig = self.strategy.signal(df, i, st.position)
        result = StepResult(ts_str, "hold", sig.reason, equity, extra=dict(sig.context or {}))

        if sig.action == "sell" and st.position is not None:
            fill = self.broker.market_sell(close, ts_str, bar, sig.reason)
            if fill:
                self.risk.on_trade_closed(self.broker.closed[-1].pnl, bar)
                st.position = None
                result.action, result.fill = "sell", fill.__dict__
                result.extra["trade_pnl"] = self.broker.closed[-1].pnl

        elif sig.action == "buy" and st.position is None:
            check = self.risk.can_open(equity, bar)
            if not check.allowed:
                result.reason = f"blocked by risk: {check.reason}"
                result.action = "blocked"
            else:
                qty, sizing = self.risk.size(equity, self.broker.cash, close, sig.stop_price)
                result.extra["sizing"] = sizing
                if self.reviewer is not None and qty > 0:
                    review = self.reviewer.review(self._proposal(sig, qty, equity, sizing))
                    result.review = review
                    qty *= review.size_multiplier
                if qty <= 0:
                    result.action = "skipped"
                    result.reason = (result.review.reasoning if result.review and result.review.decision == "veto"
                                     else "size is zero")
                else:
                    fill = self.broker.market_buy(qty, close, ts_str, bar, sig.reason)
                    if fill:
                        st.position = OpenPosition(fill.price, fill.qty, sig.stop_price, bar, close)
                        result.action, result.fill = "buy", fill.__dict__
                        if hasattr(self.broker, "update_stop"):
                            self.broker.update_stop(sig.stop_price, close)

        equity = self.broker.equity(close)
        result.equity = equity
        st.last_ts, st.bars_seen = ts_str, bar + 1
        self.equity_curve.append((ts, equity))
        if st.position is not None:
            result.extra["stop_price"] = st.position.stop_price
        self.journal.log({
            "ts": ts_str, "action": result.action, "reason": result.reason, "equity": round(equity, 4),
            "review": result.review.to_dict() if result.review else None, "fill": result.fill,
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in result.extra.items()},
        })
        return result

    def _proposal(self, sig, qty: float, equity: float, sizing: dict) -> dict:
        recent = [{"pnl_pct": round(t.pnl_pct, 2), "bars_held": t.bars_held, "exit": t.exit_reason}
                  for t in self.broker.closed[-5:]]
        drawdown = 1 - equity / self.risk.state.peak_equity if self.risk.state.peak_equity else 0.0
        return {
            "symbol": self.cfg.symbol, "timeframe": self.cfg.timeframe, "side": "buy",
            "price": sig.price, "stop_price": sig.stop_price,
            "stop_distance_pct": round((sig.price - sig.stop_price) / sig.price * 100, 3),
            "qty": qty, "notional_pct_of_equity": round(qty * sig.price / equity * 100, 2),
            "sizing_binding_constraint": sizing.get("binding"), "strategy_reason": sig.reason,
            "indicators": {k: round(v, 4) for k, v in (sig.context or {}).items()},
            "current_drawdown_pct": round(drawdown * 100, 2), "recent_trades": recent,
        }

    def run_backtest(self, raw: pd.DataFrame) -> dict:
        df = self.strategy.prepare(raw)
        start = self.cfg.warmup_bars
        if len(df) <= start:
            raise ValueError(f"need more than {start} bars, got {len(df)}")
        for i in range(start, len(df)):
            self.step(df, i)
        equity = pd.Series([e for _, e in self.equity_curve], index=[t for t, _ in self.equity_curve])
        first, last = float(df["close"].iloc[start]), float(df["close"].iloc[-1])
        bh = _buy_and_hold_final(self.cfg, first, last)
        stats = summarize(equity, self.broker.closed, self.cfg.initial_cash, bh, self.cfg.timeframe,
                          self.broker.fees_paid, self.broker.tax_paid)
        stats["bars"] = len(df) - start
        stats["period"] = f"{df['timestamp'].iloc[start].date()} to {df['timestamp'].iloc[-1].date()}"
        return {"stats": stats, "equity": equity, "df": df, "closed": self.broker.closed, "fills": self.broker.fills}


def _buy_and_hold_final(cfg: Config, first_close: float, last_close: float) -> float:
    """Buy everything at the first bar, sell at the last, same fee/tax/slippage model."""
    b = PaperBroker(cfg.initial_cash, cfg.fee_rate, cfg.tax_sell_rate, cfg.slippage_bps)
    cost_mult = (1 + cfg.slippage_bps / 10_000) * (1 + cfg.fee_rate)
    fill = b.market_buy(cfg.initial_cash / (first_close * cost_mult), first_close, "", 0, "bh")
    assert fill is not None, "buy-and-hold sizing bug"
    b.market_sell(last_close, "", 1, "bh")
    return b.cash
