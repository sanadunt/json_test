"""Paper-trading loop on live market data. State survives restarts via data/state.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from .config import Config
from .data import drop_unfinished, fetch_history, timeframe_seconds
from .dashboard_export import write_dashboard
from .engine import Engine, EngineState
from .journal import Journal
from .reviewer import ClaudeReviewer


def state_path(cfg: Config) -> Path:
    return cfg.data_dir / "state.json"


def load_engine(cfg: Config) -> Engine:
    reviewer = None
    if cfg.claude_review != "off":
        reviewer = ClaudeReviewer(model=cfg.claude_model, mode=cfg.claude_review)
    journal = Journal(cfg.data_dir / "journal.jsonl")
    p = state_path(cfg)
    state = EngineState.from_dict(json.loads(p.read_text())) if p.is_file() else None
    return Engine(cfg, state=state, reviewer=reviewer, journal=journal)


def save_engine(cfg: Config, engine: Engine) -> None:
    p = state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(engine.state.to_dict(), indent=1, default=str))


def process_new_candles(cfg: Config, engine: Engine, bars: int = 400) -> list:
    """Fetch data, run every closed candle not yet processed, persist state and dashboard."""
    raw = fetch_history(cfg.exchange, cfg.symbol, cfg.timeframe, bars, cfg.data_dir)
    raw = drop_unfinished(raw, cfg.timeframe)
    df = engine.strategy.prepare(raw)
    results = []
    if engine.state.last_ts is None:
        # first run: start the paper record at the newest closed candle instead of replaying history
        start = len(df) - 1
    else:
        last = pd.Timestamp(engine.state.last_ts)
        start = int((df["timestamp"] > last).idxmax()) if (df["timestamp"] > last).any() else len(df)
    for i in range(max(start, cfg.warmup_bars), len(df)):
        results.append(engine.step(df, i))
    save_engine(cfg, engine)
    write_dashboard(cfg, engine, df)
    return results


def run_paper(cfg: Config, once: bool = False) -> None:
    engine = load_engine(cfg)
    tf = timeframe_seconds(cfg.timeframe)
    print(f"[paper] {cfg.exchange} {cfg.symbol} {cfg.timeframe} | review={cfg.claude_review} "
          f"| tax={cfg.tax_sell_rate:.2%} fee={cfg.fee_rate:.2%}")
    while True:
        try:
            results = process_new_candles(cfg, engine)
            for r in results:
                tag = r.action.upper()
                print(f"[{r.ts}] {tag:8s} equity={r.equity:.2f} {r.reason}"
                      + (f" | claude: {r.review.decision} x{r.review.size_multiplier}" if r.review else ""))
            if not results:
                b = engine.state.broker
                print(f"[{pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds')}] no new closed candle | "
                      f"cash={b.cash:.2f} qty={b.qty:.6f} last_processed={engine.state.last_ts}")
        except Exception as e:  # keep the loop alive on transient errors
            print(f"[paper] error: {type(e).__name__}: {e}")
        if once:
            return
        next_close = (time.time() // tf + 1) * tf + 15  # 15s after the candle closes
        time.sleep(max(30, next_close - time.time()))
