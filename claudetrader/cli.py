"""python -m claudetrader <command>"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Config


def cmd_fetch(cfg: Config, args) -> None:
    from .data import fetch_history

    df = fetch_history(cfg.exchange, cfg.symbol, cfg.timeframe, args.bars, cfg.data_dir)
    print(f"{len(df)} candles {cfg.exchange} {cfg.symbol} {cfg.timeframe}: "
          f"{df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")


def cmd_backtest(cfg: Config, args) -> None:
    from .data import drop_unfinished, fetch_history
    from .dashboard_export import write_dashboard
    from .engine import Engine
    from .reviewer import ClaudeReviewer

    raw = drop_unfinished(fetch_history(cfg.exchange, cfg.symbol, cfg.timeframe, args.bars, cfg.data_dir),
                          cfg.timeframe)
    reviewer = None
    if args.review:
        if cfg.claude_review == "off":
            sys.exit("--review needs CLAUDE_REVIEW=advisory|required and ANTHROPIC_API_KEY")
        reviewer = ClaudeReviewer(model=cfg.claude_model, mode=cfg.claude_review)
    engine = Engine(cfg, reviewer=reviewer)
    res = engine.run_backtest(raw)
    stats = res["stats"]
    print(f"\n{cfg.exchange} {cfg.symbol} {cfg.timeframe} | {stats['period']} | {stats['bars']} bars "
          f"| fee {cfg.fee_rate:.2%} tax {cfg.tax_sell_rate:.2%} slippage {cfg.slippage_bps:.0f}bps\n")
    for k, v in stats.items():
        if k not in {"period", "bars"}:
            print(f"  {k:22s} {v}")
    print()
    if args.trades:
        for t in res["closed"]:
            print(f"  {t.entry_ts[:16]} -> {t.exit_ts[:16]}  {t.pnl_pct:+6.2f}%  {t.bars_held:4d} bars  {t.exit_reason}")
    path = write_dashboard(cfg, engine, res["df"], stats)
    print(f"dashboard data written to {path}  (run: python -m claudetrader serve)")


def cmd_paper(cfg: Config, args) -> None:
    from .paper import run_paper

    run_paper(cfg, once=args.once)


def cmd_serve(cfg: Config, args) -> None:
    from .serve import serve

    serve(cfg, port=args.port)


def cmd_review_test(cfg: Config, args) -> None:
    """Send one synthetic proposal to Claude to check the API key and the schema."""
    from .reviewer import ClaudeReviewer

    mode = cfg.claude_review if cfg.claude_review != "off" else "advisory"
    r = ClaudeReviewer(model=cfg.claude_model, mode=mode)
    proposal = {
        "symbol": cfg.symbol, "timeframe": cfg.timeframe, "side": "buy", "price": 65000.0,
        "stop_price": 63500.0, "stop_distance_pct": 2.31, "qty": 0.004, "notional_pct_of_equity": 26.0,
        "sizing_binding_constraint": "risk", "strategy_reason": "EMA cross up above trend, RSI ok",
        "indicators": {"rsi": 61.2, "atr_pct": 1.15, "ema_fast": 64800, "ema_slow": 64650, "ema_trend": 61000},
        "current_drawdown_pct": 1.4, "recent_trades": [{"pnl_pct": -1.8, "bars_held": 6, "exit": "stop hit"}],
    }
    print(json.dumps(r.review(proposal).to_dict(), indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="claudetrader", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="download/update candles into data/")
    s.add_argument("--bars", type=int, default=2000)
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("backtest", help="run the strategy on historical candles")
    s.add_argument("--bars", type=int, default=2000)
    s.add_argument("--trades", action="store_true", help="print every closed trade")
    s.add_argument("--review", action="store_true", help="call Claude on every entry (costs tokens)")
    s.set_defaults(fn=cmd_backtest)

    s = sub.add_parser("paper", help="paper-trade on live data (loops until Ctrl-C)")
    s.add_argument("--once", action="store_true", help="process new candles once and exit (for cron)")
    s.set_defaults(fn=cmd_paper)

    s = sub.add_parser("serve", help="serve the dashboard on localhost")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("review-test", help="send one synthetic proposal to Claude")
    s.set_defaults(fn=cmd_review_test)

    args = p.parse_args(argv)
    args.fn(Config.from_env(), args)


if __name__ == "__main__":
    main()
