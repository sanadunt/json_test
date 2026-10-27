import pandas as pd

from claudetrader.data import drop_unfinished, timeframe_seconds, to_frame


def test_to_frame_sorts_and_dedupes():
    rows = [[2_000_000, 1, 2, 0.5, 1.5, 10], [1_000_000, 1, 2, 0.5, 1.5, 10], [2_000_000, 1, 2, 0.5, 1.6, 10]]
    df = to_frame(rows)
    assert list(df["timestamp"]) == [pd.Timestamp(1_000_000, unit="ms", tz="UTC"), pd.Timestamp(2_000_000, unit="ms", tz="UTC")]
    assert df["close"].iloc[-1] == 1.5  # first occurrence kept
    assert df["close"].dtype == float


def test_drop_unfinished_only_drops_open_candle():
    ts = pd.date_range("2025-01-01", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    last_open = ts[-1].timestamp()
    assert len(drop_unfinished(df, "1h", now_s=last_open + 1800)) == 2
    assert len(drop_unfinished(df, "1h", now_s=last_open + 3600)) == 3


def test_timeframe_seconds():
    assert timeframe_seconds("4h") == 14400
    try:
        timeframe_seconds("3w")
        assert False
    except ValueError:
        pass
