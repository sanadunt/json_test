"""Rule-based, long-only trend strategy.

Entry: fast EMA crosses above slow EMA, price above the trend EMA, RSI below the overbought cap.
Exit:  fast EMA crosses below slow EMA, or price closes below the ATR stop (initial or trailing).

The strategy only proposes. Position size comes from RiskManager; execution from the broker.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Config
from .indicators import add_indicators


@dataclass
class Signal:
    action: str  # "buy" | "sell" | "hold"
    reason: str
    price: float
    stop_price: float | None = None
    context: dict | None = None


@dataclass
class OpenPosition:
    entry_price: float
    qty: float
    stop_price: float
    entry_index: int
    highest_close: float


class EmaRsiTrendStrategy:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        return add_indicators(df, c.ema_fast, c.ema_slow, c.ema_trend, c.rsi_period, c.atr_period)

    def ready(self, df: pd.DataFrame, i: int) -> bool:
        row = df.iloc[i]
        return i >= 1 and not pd.isna(row[["ema_fast", "ema_slow", "ema_trend", "rsi", "atr"]]).any()

    def update_trailing_stop(self, df: pd.DataFrame, i: int, pos: OpenPosition) -> OpenPosition:
        row = df.iloc[i]
        close = float(row["close"])
        if close > pos.highest_close:
            pos.highest_close = close
        trail = pos.highest_close - self.cfg.atr_trail_mult * float(row["atr"])
        if trail > pos.stop_price:
            pos.stop_price = trail
        return pos

    def signal(self, df: pd.DataFrame, i: int, pos: OpenPosition | None) -> Signal:
        row, prev = df.iloc[i], df.iloc[i - 1]
        close = float(row["close"])
        if not self.ready(df, i):
            return Signal("hold", "warming up", close)

        cross_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
        cross_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]
        ctx = {
            "close": close,
            "ema_fast": float(row["ema_fast"]),
            "ema_slow": float(row["ema_slow"]),
            "ema_trend": float(row["ema_trend"]),
            "rsi": float(row["rsi"]),
            "atr": float(row["atr"]),
            "atr_pct": float(row["atr"]) / close * 100,
        }

        if pos is not None:
            if close < pos.stop_price:
                return Signal("sell", f"stop hit ({pos.stop_price:.2f})", close, context=ctx)
            if cross_down:
                return Signal("sell", "EMA cross down", close, context=ctx)
            return Signal("hold", "in position", close, context=ctx)

        if cross_up and close > row["ema_trend"] and row["rsi"] < self.cfg.rsi_max:
            stop = close - self.cfg.atr_stop_mult * float(row["atr"])
            return Signal("buy", "EMA cross up above trend, RSI ok", close, stop_price=stop, context=ctx)
        if cross_up:
            why = "below trend EMA" if close <= row["ema_trend"] else f"RSI {row['rsi']:.0f} too high"
            return Signal("hold", f"cross up rejected: {why}", close, context=ctx)
        return Signal("hold", "no setup", close, context=ctx)
