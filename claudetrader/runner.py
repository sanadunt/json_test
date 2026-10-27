"""Paper or live trading loop on real market data. State survives restarts via <data_dir>/state.json."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

from .config import Config
from .dashboard_export import write_dashboard
from .data import drop_unfinished, fetch_history, timeframe_seconds
from .engine import Engine, EngineState
from .journal import Journal
from .notify import Notifier
from .reviewer import ClaudeReviewer

LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_REAL_MONEY"


def state_path(cfg: Config) -> Path:
    return cfg.data_dir / "state.json"


def make_notifier(cfg: Config) -> Notifier:
    return Notifier(cfg.telegram_token or None, cfg.telegram_chat_id or None)


def check_live_preconditions(cfg: Config) -> list[str]:
    """Everything that must be true before a single real order is allowed. Returns problems."""
    problems = []
    if cfg.live_confirm != LIVE_CONFIRM_PHRASE:
        problems.append(f"LIVE_CONFIRM must be exactly {LIVE_CONFIRM_PHRASE}")
    if not cfg.api_key or not cfg.api_secret:
        problems.append("EXCHANGE_API_KEY and EXCHANGE_API_SECRET are required")
    if cfg.max_order_notional <= 0:
        problems.append("MAX_ORDER_NOTIONAL must be > 0")
    if cfg.max_daily_orders <= 0:
        problems.append("MAX_DAILY_ORDERS must be > 0")
    if cfg.max_position_pct > 0.5:
        problems.append("MAX_POSITION_PCT above 0.5 is not allowed in live mode")
    return problems


def build_live_broker(cfg: Config, notifier: Notifier | None = None, exchange=None):
    from .live_broker import LiveBroker, make_live_exchange

    if exchange is None:
        exchange = make_live_exchange(cfg.exchange, cfg.api_key, cfg.api_secret, cfg.api_password or None,
                                      sandbox=cfg.sandbox)
    return LiveBroker(exchange, cfg.symbol, fee_rate=cfg.fee_rate, tax_sell_rate=cfg.tax_sell_rate,
                      max_order_notional=cfg.max_order_notional, max_daily_orders=cfg.max_daily_orders,
                      exchange_stop=cfg.exchange_stop, notifier=notifier or make_notifier(cfg))


def load_engine(cfg: Config, exchange=None) -> Engine:
    reviewer = None
    if cfg.claude_review != "off":
        reviewer = ClaudeReviewer(model=cfg.claude_model, mode=cfg.claude_review)
    journal = Journal(cfg.data_dir / "journal.jsonl")
    p = state_path(cfg)
    saved = json.loads(p.read_text()) if p.is_file() else None

    if not cfg.is_live:
        state = EngineState.from_dict(saved) if saved else None
        return Engine(cfg, state=state, reviewer=reviewer, journal=journal)

    problems = check_live_preconditions(cfg)
    if problems:
        sys.exit("refusing to start live trading:\n  - " + "\n  - ".join(problems))
    notifier = make_notifier(cfg)
    broker = build_live_broker(cfg, notifier, exchange)
    local_qty = 0.0
    state = None
    if saved:
        if saved["broker"].get("kind") != "live":
            sys.exit(f"{p} is a paper state file; live mode uses its own DATA_DIR (default data/live)")
        state = EngineState.from_dict(saved, broker_factory=broker.load_ledger)
        local_qty = state.broker.qty
    try:
        info = broker.reconcile(local_qty)
    except Exception as e:  # auth error, network, or a real position mismatch: never start blind
        sys.exit(f"refusing to start live trading: reconcile with {cfg.exchange} failed: "
                 f"{type(e).__name__}: {e}")
    print(f"[live] reconciled with {cfg.exchange}: cash={info['cash']:.2f} {broker.quote}, "
          f"{broker.base} on exchange={info['base_total']}, local position={local_qty}")
    engine = Engine(cfg, state=state, reviewer=reviewer, journal=journal, broker=broker)
    if state is None:
        engine.risk.state.peak_equity = max(broker.cash, 1e-9)
    return engine


def save_engine(cfg: Config, engine: Engine) -> None:
    p = state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(engine.state.to_dict(), indent=1, default=str))


def handle_stop_file(cfg: Config, engine: Engine, price: float) -> bool:
    """If <data_dir>/STOP exists: close any open position at market and halt. Returns True if halted."""
    if not cfg.stop_file.is_file():
        return False
    ts = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    if engine.state.position is not None:
        fill = engine.broker.market_sell(price, ts, engine.state.bars_seen, "STOP file")
        if fill:
            engine.risk.on_trade_closed(engine.broker.closed[-1].pnl, engine.state.bars_seen)
            engine.state.position = None
    engine.risk.state.halted_reason = f"STOP file present at {cfg.stop_file}"
    save_engine(cfg, engine)
    return True


def process_new_candles(cfg: Config, engine: Engine, bars: int = 400) -> list:
    """Fetch data, run every closed candle not yet processed, persist state and dashboard."""
    raw = fetch_history(cfg.exchange, cfg.symbol, cfg.timeframe, bars, cfg.data_dir)
    raw = drop_unfinished(raw, cfg.timeframe)
    df = engine.strategy.prepare(raw)
    results = []
    if engine.state.last_ts is None:
        start = len(df) - 1  # first run: start at the newest closed candle, do not replay history
    else:
        last = pd.Timestamp(engine.state.last_ts)
        start = int((df["timestamp"] > last).idxmax()) if (df["timestamp"] > last).any() else len(df)
    for i in range(max(start, cfg.warmup_bars), len(df)):
        results.append(engine.step(df, i))
        if cfg.is_live:
            save_engine(cfg, engine)  # persist after every real order, not only at the end
    save_engine(cfg, engine)
    write_dashboard(cfg, engine, df)
    return results, float(df["close"].iloc[-1]) if len(df) else 0.0


def run_loop(cfg: Config, once: bool = False, exchange=None) -> None:
    engine = load_engine(cfg, exchange=exchange)
    notifier = make_notifier(cfg)
    tf = timeframe_seconds(cfg.timeframe)
    mode = "LIVE" if cfg.is_live else "paper"
    print(f"[{mode}] {cfg.exchange} {cfg.symbol} {cfg.timeframe} | review={cfg.claude_review} "
          f"| tax={cfg.tax_sell_rate:.2%} fee={cfg.fee_rate:.2%} | stop file: {cfg.stop_file}")
    if cfg.is_live:
        notifier.send(f"live bot started: {cfg.exchange} {cfg.symbol} {cfg.timeframe}, "
                      f"max order {cfg.max_order_notional}, max {cfg.max_daily_orders} orders/day")
    while True:
        try:
            results, last_close = process_new_candles(cfg, engine)
            for r in results:
                print(f"[{r.ts}] {r.action.upper():8s} equity={r.equity:.2f} {r.reason}"
                      + (f" | claude: {r.review.decision} x{r.review.size_multiplier}" if r.review else ""))
            if not results:
                b = engine.state.broker
                print(f"[{pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds')}] no new closed candle | "
                      f"cash={b.cash:.2f} qty={b.qty:.6f} last_processed={engine.state.last_ts}")
            if handle_stop_file(cfg, engine, last_close):
                msg = f"halted by STOP file; position closed. Delete {cfg.stop_file} to allow restart."
                print(f"[{mode}] {msg}")
                if cfg.is_live:
                    notifier.send(msg)
                return
        except Exception as e:  # keep the loop alive on transient errors
            print(f"[{mode}] error: {type(e).__name__}: {e}")
            if cfg.is_live:
                notifier.send(f"error: {type(e).__name__}: {e}"[:500])
        if once:
            return
        next_close = (time.time() // tf + 1) * tf + 15  # 15s after the candle closes
        time.sleep(max(30, next_close - time.time()))


# backwards-compatible name
def run_paper(cfg: Config, once: bool = False) -> None:
    run_loop(cfg, once=once)
