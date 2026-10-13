import numpy as np
import pandas as pd

from claudetrader.indicators import atr, ema, rsi


def test_ema_converges_to_constant():
    s = pd.Series([50.0] * 100)
    assert abs(ema(s, 10).iloc[-1] - 50.0) < 1e-9


def test_ema_has_nan_warmup():
    s = pd.Series(np.arange(30, dtype=float))
    out = ema(s, 10)
    assert out.iloc[:9].isna().all() and not np.isnan(out.iloc[9])


def test_rsi_bounds_and_direction():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert rsi(up).iloc[-1] > 90
    assert rsi(down).iloc[-1] < 10
    mixed = pd.Series(100 + np.sin(np.arange(200) / 3) * 5)
    r = rsi(mixed).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_positive_and_scales_with_range():
    n = 100
    close = pd.Series(np.full(n, 100.0))
    narrow = atr(close + 1, close - 1, close, 14).iloc[-1]
    wide = atr(close + 5, close - 5, close, 14).iloc[-1]
    assert narrow > 0 and wide > narrow * 4
