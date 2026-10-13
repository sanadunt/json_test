"""Configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no overrides of existing env."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


# PPh Pasal 22 final on crypto sales (PMK 50/2025, effective 2025-08-01):
# 0.21% on OJK-licensed domestic exchanges, 1% on foreign exchanges.
DEFAULT_TAX_BY_EXCHANGE = {"indodax": 0.0021, "tokocrypto": 0.0021, "binance": 0.01}


@dataclass
class Config:
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_cash: float = 1000.0

    fee_rate: float = 0.001
    tax_sell_rate: float | None = None
    slippage_bps: float = 5.0

    risk_per_trade: float = 0.01
    max_position_pct: float = 0.5
    daily_loss_limit: float = 0.03
    max_drawdown: float = 0.15
    cooldown_bars: int = 3

    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    rsi_max: float = 70.0
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    atr_trail_mult: float = 3.0

    claude_review: str = "off"  # off | advisory | required
    claude_model: str = "claude-opus-5-5"

    data_dir: Path = field(default_factory=lambda: Path("data"))

    def __post_init__(self) -> None:
        self.exchange = self.exchange.lower()
        if self.tax_sell_rate is None:
            self.tax_sell_rate = DEFAULT_TAX_BY_EXCHANGE.get(self.exchange, 0.01)
        if self.claude_review not in {"off", "advisory", "required"}:
            raise ValueError(f"CLAUDE_REVIEW must be off|advisory|required, got {self.claude_review!r}")
        if not 0 < self.risk_per_trade <= 0.1:
            raise ValueError("RISK_PER_TRADE must be in (0, 0.1]")
        if not 0 < self.max_position_pct <= 1:
            raise ValueError("MAX_POSITION_PCT must be in (0, 1]")
        self.data_dir = Path(self.data_dir)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        tax = os.environ.get("TAX_SELL_RATE")
        return cls(
            exchange=_env("EXCHANGE", "binance"),
            symbol=_env("SYMBOL", "BTC/USDT"),
            timeframe=_env("TIMEFRAME", "1h"),
            initial_cash=_env_float("INITIAL_CASH", 1000.0),
            fee_rate=_env_float("FEE_RATE", 0.001),
            tax_sell_rate=float(tax) if tax else None,
            slippage_bps=_env_float("SLIPPAGE_BPS", 5.0),
            risk_per_trade=_env_float("RISK_PER_TRADE", 0.01),
            max_position_pct=_env_float("MAX_POSITION_PCT", 0.5),
            daily_loss_limit=_env_float("DAILY_LOSS_LIMIT", 0.03),
            max_drawdown=_env_float("MAX_DRAWDOWN", 0.15),
            cooldown_bars=_env_int("COOLDOWN_BARS", 3),
            ema_fast=_env_int("EMA_FAST", 20),
            ema_slow=_env_int("EMA_SLOW", 50),
            ema_trend=_env_int("EMA_TREND", 200),
            rsi_period=_env_int("RSI_PERIOD", 14),
            rsi_max=_env_float("RSI_MAX", 70.0),
            atr_period=_env_int("ATR_PERIOD", 14),
            atr_stop_mult=_env_float("ATR_STOP_MULT", 2.0),
            atr_trail_mult=_env_float("ATR_TRAIL_MULT", 3.0),
            claude_review=_env("CLAUDE_REVIEW", "off").lower(),
            claude_model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
            data_dir=Path(_env("DATA_DIR", "data")),
        )

    @property
    def warmup_bars(self) -> int:
        return max(self.ema_trend, self.ema_slow, self.rsi_period, self.atr_period) + 5
