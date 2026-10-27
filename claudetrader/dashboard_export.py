"""Writes data/dashboard.json for the Lightweight Charts dashboard."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import Config


def _to_sec(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).timestamp())


def build_payload(cfg: Config, engine, df: pd.DataFrame, stats: dict | None = None,
                  max_bars: int = 2000) -> dict:
    d = df.tail(max_bars)
    candles = [
        {"time": _to_sec(r.timestamp), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
        for r in d.itertuples()
    ]
    lines = {}
    for col in ("ema_fast", "ema_slow", "ema_trend"):
        if col in d:
            lines[col] = [{"time": _to_sec(r.timestamp), "value": float(getattr(r, col))}
                          for r in d.itertuples() if pd.notna(getattr(r, col))]
    first_time = candles[0]["time"] if candles else 0
    markers = []
    for f in engine.broker.fills:
        t = _to_sec(pd.Timestamp(f.ts))
        if t < first_time:
            continue
        markers.append({
            "time": t, "position": "belowBar" if f.side == "buy" else "aboveBar",
            "color": "#2962ff" if f.side == "buy" else "#e91e63",
            "shape": "arrowUp" if f.side == "buy" else "arrowDown",
            "text": f.side.upper(),
        })
    equity = [{"time": _to_sec(t), "value": round(float(e), 4)} for t, e in engine.equity_curve
              if _to_sec(t) >= first_time]
    broker = engine.broker
    last_close = float(d["close"].iloc[-1]) if len(d) else 0.0
    position = None
    if engine.state.position is not None:
        p = engine.state.position
        position = {"qty": p.qty, "entry_price": p.entry_price, "stop_price": p.stop_price,
                    "unrealized_pct": round((last_close / p.entry_price - 1) * 100, 2)}
    entries = engine.journal.entries
    acted = [e for e in entries if e["action"] != "hold"][-30:]
    holds = [e for e in entries if e["action"] == "hold"][-5:]
    recent = sorted(acted + holds, key=lambda e: e["ts"], reverse=True)
    return {
        "meta": {"exchange": cfg.exchange, "symbol": cfg.symbol, "timeframe": cfg.timeframe,
                 "execution": cfg.execution,
                 "review": cfg.claude_review, "model": cfg.claude_model,
                 "fee_rate": cfg.fee_rate, "tax_sell_rate": cfg.tax_sell_rate,
                 "generated_at": pd.Timestamp.utcnow().isoformat(timespec="seconds")},
        "stats": stats or {},
        "account": {"cash": round(broker.cash, 2), "equity": round(broker.equity(last_close), 2),
                    "initial_cash": cfg.initial_cash, "fees_paid": round(broker.fees_paid, 2),
                    "tax_paid": round(broker.tax_paid, 2), "position": position,
                    "halted": engine.risk.state.halted_reason},
        "candles": candles, "lines": lines, "markers": markers, "equity": equity,
        "closed_trades": [t.__dict__ for t in broker.closed[-100:]][::-1],
        "recent_decisions": recent,
    }


def write_dashboard(cfg: Config, engine, df: pd.DataFrame, stats: dict | None = None) -> Path:
    path = Path(cfg.data_dir) / "dashboard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_payload(cfg, engine, df, stats), default=str))
    return path
