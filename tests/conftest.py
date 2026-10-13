import numpy as np
import pandas as pd
import pytest

from claudetrader.config import Config


@pytest.fixture
def cfg() -> Config:
    return Config(exchange="binance", initial_cash=1000.0, fee_rate=0.001, tax_sell_rate=0.0021,
                  slippage_bps=5.0, data_dir="/tmp/claudetrader-tests")


def synthetic_candles(n: int = 800, seed: int = 7, start_price: float = 100.0) -> pd.DataFrame:
    """Trending random walk with a few regime shifts so EMA crossovers actually happen."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 4, 0.0015), np.full(n // 4, -0.001), np.full(n // 4, 0.002), np.full(n - 3 * (n // 4), -0.0005)])
    rets = drift + rng.normal(0, 0.006, n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    spread = np.abs(rng.normal(0, 0.004, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    ts = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(10, 100, n)})


@pytest.fixture
def candles() -> pd.DataFrame:
    return synthetic_candles()
