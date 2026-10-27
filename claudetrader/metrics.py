"""Performance metrics for an equity curve and a list of closed trades."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import timeframe_seconds


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = 1 - equity / peak
    return float(dd.max()) if len(dd) else 0.0


def sharpe(equity: pd.Series, timeframe: str) -> float:
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return 0.0
    bars_per_year = 365 * 86400 / timeframe_seconds(timeframe)
    return float(rets.mean() / rets.std() * math.sqrt(bars_per_year))


def summarize(equity: pd.Series, closed: list, initial_cash: float, buy_hold_final: float,
              timeframe: str, fees: float, tax: float) -> dict:
    final = float(equity.iloc[-1]) if len(equity) else initial_cash
    pnls = np.array([t.pnl for t in closed]) if closed else np.array([])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / initial_cash - 1) * 100, 2),
        "buy_hold_return_pct": round((buy_hold_final / initial_cash - 1) * 100, 2),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe(equity, timeframe), 2),
        "trades": int(len(pnls)),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if len(pnls) else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "avg_trade_pct": round(float(np.mean([t.pnl_pct for t in closed])), 2) if closed else 0.0,
        "fees_paid": round(fees, 2),
        "tax_paid": round(tax, 2),
    }
