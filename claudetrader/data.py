"""Market data via ccxt, with CSV caching.

Binance's main API is geo-blocked in some regions; the public data mirror
(data-api.binance.vision) serves the same klines without a key.
"""

from __future__ import annotations

import time
from pathlib import Path

import ccxt
import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


def timeframe_seconds(timeframe: str) -> int:
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as e:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from e


def make_exchange(name: str) -> ccxt.Exchange:
    name = name.lower()
    if name == "binance":
        # spot only: the public data mirror has no futures endpoints
        ex = ccxt.binance({"enableRateLimit": True, "options": {"fetchMarkets": ["spot"]}})
        ex.urls["api"]["public"] = "https://data-api.binance.vision/api/v3"
    elif name in ccxt.exchanges:
        ex = getattr(ccxt, name)({"enableRateLimit": True})
    else:
        raise ValueError(f"unknown exchange {name!r}")
    # ccxt disables trust_env; re-enable it so HTTPS_PROXY / REQUESTS_CA_BUNDLE from the
    # environment are honoured (corporate proxies, sandboxes).
    ex.session.trust_env = True
    return ex


def to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for c in COLUMNS[1:]:
        df[c] = df[c].astype(float)
    return df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str,
                limit: int = 500, since_ms: int | None = None) -> pd.DataFrame:
    """Fetch up to `limit` candles, paginating forward from `since_ms` if given."""
    per_call = min(limit, 1000)
    rows: list[list] = []
    cursor = since_ms
    while len(rows) < limit:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=per_call)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < per_call or cursor is None:
            break
        cursor = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    return to_frame(rows[-limit:] if since_ms is None else rows)


def cache_path(data_dir: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "-")
    return Path(data_dir) / f"{exchange}_{safe}_{timeframe}.csv"


def load_cached(path: Path) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def save_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def fetch_history(exchange_name: str, symbol: str, timeframe: str, bars: int,
                  data_dir: Path) -> pd.DataFrame:
    """Fetch `bars` candles ending now, merging with the local CSV cache."""
    ex = make_exchange(exchange_name)
    path = cache_path(data_dir, exchange_name, symbol, timeframe)
    cached = load_cached(path)
    tf_ms = timeframe_seconds(timeframe) * 1000
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - bars * tf_ms

    if cached is not None and len(cached) > 0:
        last_ms = int(cached["timestamp"].iloc[-1].timestamp() * 1000)
        since = max(start_ms, last_ms - tf_ms)  # re-fetch last bar: it may have been unfinished
    else:
        since = start_ms

    fresh = fetch_ohlcv(ex, symbol, timeframe, limit=bars + 2, since_ms=since)
    df = pd.concat([cached, fresh]) if cached is not None else fresh
    df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] >= pd.Timestamp(start_ms, unit="ms", tz="UTC")].reset_index(drop=True)
    save_cache(df, path)
    return df


def drop_unfinished(df: pd.DataFrame, timeframe: str, now_s: float | None = None) -> pd.DataFrame:
    """Drop the last candle if it has not closed yet."""
    if df.empty:
        return df
    now_s = time.time() if now_s is None else now_s
    last_open = df["timestamp"].iloc[-1].timestamp()
    if last_open + timeframe_seconds(timeframe) > now_s:
        return df.iloc[:-1].reset_index(drop=True)
    return df
