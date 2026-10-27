import os
from pathlib import Path

import pytest

from claudetrader.config import Config, load_dotenv, _strip_inline_comment

REPO = Path(__file__).resolve().parent.parent
ENV_KEYS = ("EXCHANGE", "SYMBOL", "TIMEFRAME", "INITIAL_CASH", "FEE_RATE", "TAX_SELL_RATE", "SLIPPAGE_BPS",
            "RISK_PER_TRADE", "MAX_POSITION_PCT", "DAILY_LOSS_LIMIT", "MAX_DRAWDOWN", "COOLDOWN_BARS",
            "CLAUDE_REVIEW", "CLAUDE_MODEL", "EXECUTION", "MAX_ORDER_NOTIONAL", "MAX_DAILY_ORDERS",
            "EXCHANGE_STOP", "DATA_DIR")


@pytest.fixture
def clean_env():
    """load_dotenv writes to os.environ; keep that from leaking between tests."""
    def wipe():
        for k in ENV_KEYS:
            os.environ.pop(k, None)
    wipe()
    yield
    wipe()


def test_strip_inline_comment():
    assert _strip_inline_comment("100             # quote currency per order") == "100"
    assert _strip_inline_comment("paper                    # paper | live") == "paper"
    assert _strip_inline_comment("BTC/USDT") == "BTC/USDT"
    assert _strip_inline_comment('"a # not a comment"') == "a # not a comment"
    assert _strip_inline_comment("abc#def") == "abc#def"   # '#' without a preceding space is part of the value
    assert _strip_inline_comment("") == ""


def test_env_example_loads_and_parses(tmp_path, monkeypatch, clean_env):
    """Copying .env.example to .env (the README's first step) must produce a valid Config."""
    (tmp_path / ".env").write_text((REPO / ".env.example").read_text())
    monkeypatch.chdir(tmp_path)
    cfg = Config.from_env()
    assert cfg.execution == "paper" and not cfg.is_live
    assert cfg.max_order_notional == 100.0 and cfg.max_daily_orders == 10
    assert cfg.exchange_stop is False
    assert cfg.exchange == "binance" and cfg.symbol == "BTC/USDT" and cfg.timeframe == "1h"
    assert cfg.initial_cash == 1000.0 and cfg.risk_per_trade == 0.01
    assert cfg.tax_sell_rate == 0.01          # binance default, TAX_SELL_RATE commented out
    assert cfg.data_dir == Path("data")


def test_dotenv_does_not_override_existing_env(tmp_path, clean_env):
    os.environ["SYMBOL"] = "ETH/USDT"
    (tmp_path / ".env").write_text("SYMBOL=BTC/USDT\nexport TIMEFRAME=4h   # inline\n")
    load_dotenv(tmp_path / ".env")
    assert os.environ["SYMBOL"] == "ETH/USDT"
    assert os.environ["TIMEFRAME"] == "4h"
