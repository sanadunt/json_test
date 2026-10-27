"""Live execution through ccxt. Same interface as PaperBroker, real orders.

Safety model:
  - only constructed when EXECUTION=live and LIVE_CONFIRM matches (see runner.py)
  - every order is capped by MAX_ORDER_NOTIONAL and MAX_DAILY_ORDERS
  - reconcile() at startup refuses to run if the exchange balance disagrees with local state
  - sync() every candle detects a position closed outside the bot (exchange-side stop, manual sell)
  - the Claude reviewer and RiskManager run before this class is ever asked to buy
Stops: Binance/Tokocrypto can hold a stop-limit sell on the exchange (EXCHANGE_STOP=1).
Indodax has no stop orders, so the bot evaluates stops per closed candle, as in paper mode.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from .broker import ClosedTrade, Fill, QTY_DECIMALS


class LiveBrokerError(RuntimeError):
    pass


class ReconcileError(LiveBrokerError):
    pass


class LiveBroker:
    kind = "live"

    def __init__(self, exchange, symbol: str, fee_rate: float = 0.001, tax_sell_rate: float = 0.0,
                 max_order_notional: float = 100.0, max_daily_orders: int = 10, exchange_stop: bool = False,
                 notifier=None, fill_timeout_s: float = 30.0, poll_interval_s: float = 1.0):
        self.exchange = exchange
        self.symbol = symbol
        self.base, self.quote = symbol.split("/")
        self.fee_rate = fee_rate
        self.tax_sell_rate = tax_sell_rate
        self.max_order_notional = max_order_notional
        self.max_daily_orders = max_daily_orders
        self.exchange_stop = exchange_stop
        self.notifier = notifier
        self.fill_timeout_s = fill_timeout_s
        self.poll_interval_s = poll_interval_s
        # ledger (same fields as PaperBroker)
        self.cash = 0.0
        self.qty = 0.0
        self.entry_price = 0.0
        self.entry_ts = ""
        self.entry_bar = -1
        self.fills: list[Fill] = []
        self.closed: list[ClosedTrade] = []
        self.fees_paid = 0.0
        self.tax_paid = 0.0  # estimate for reporting: Indonesian exchanges withhold it, foreign ones leave it to you
        self.orders_by_day: dict[str, int] = {}
        self.stop_order_id: str | None = None
        self.stop_price: float | None = None
        self.last_mark = 0.0
        self._markets_loaded = False

    # ---------- interface shared with PaperBroker ----------
    @property
    def in_position(self) -> bool:
        return self.qty > 0

    def equity(self, mark_price: float) -> float:
        self.last_mark = mark_price
        return self.cash + self.qty * mark_price

    def market_buy(self, qty: float, price: float, ts: str, bar_index: int, reason: str) -> Fill | None:
        if qty <= 0 or self.in_position:
            return None
        qty = self._normalize_amount(qty, price)
        if qty <= 0:
            return None
        filled, avg, fee = self._place("buy", qty, price)
        if filled <= 0:
            return None
        self.cash -= filled * avg + fee
        self._refresh_cash()
        self.fees_paid += fee
        self.qty, self.entry_price, self.entry_ts, self.entry_bar = filled, avg, ts, bar_index
        fill = Fill(ts, "buy", filled, avg, fee, 0.0, reason, bar_index)
        self.fills.append(fill)
        self._notify(f"BUY {filled} {self.base} @ {avg:.2f} ({reason}) fee={fee:.4f}")
        return fill

    def market_sell(self, price: float, ts: str, bar_index: int, reason: str) -> Fill | None:
        if not self.in_position:
            return None
        self._cancel_stop()
        filled, avg, fee = self._place("sell", self.qty, price)
        if filled <= 0:
            return None
        tax = filled * avg * self.tax_sell_rate  # withheld by Indonesian exchanges, owed by you on foreign ones
        self.cash += filled * avg - fee
        self._refresh_cash()
        self.fees_paid += fee
        self.tax_paid += tax
        self._record_close(filled, avg, fee, tax, ts, bar_index, reason)
        fill = Fill(ts, "sell", filled, avg, fee, tax, reason, bar_index)
        self.fills.append(fill)
        remaining = self.qty - filled
        self.qty = remaining if remaining > 10 ** -QTY_DECIMALS else 0.0
        if self.qty == 0.0:
            self.entry_price, self.entry_ts, self.entry_bar = 0.0, "", -1
        self._notify(f"SELL {filled} {self.base} @ {avg:.2f} ({reason}) pnl={self.closed[-1].pnl:.4f}")
        return fill

    # ---------- live-only hooks used by the engine / runner ----------
    def reconcile(self, local_qty: float, tolerance: float = 0.02) -> dict:
        """Load exchange balance into the ledger; refuse if the base balance disagrees with local state."""
        self._load_markets()
        bal = self.exchange.fetch_balance()
        quote_free = float((bal.get("free") or {}).get(self.quote) or 0.0)
        base_total = float((bal.get("total") or {}).get(self.base) or 0.0)
        self.cash = quote_free
        if local_qty > 0 and abs(base_total - local_qty) > max(local_qty * tolerance, 10 ** -QTY_DECIMALS):
            raise ReconcileError(
                f"local state says {local_qty} {self.base} but exchange holds {base_total}. "
                f"Fix the position by hand (or delete the live state file) before running.")
        if local_qty == 0 and self.qty == 0:
            self.qty = 0.0
        return {"cash": quote_free, "base_total": base_total}

    def sync(self, ts: str, bar_index: int) -> ClosedTrade | None:
        """Detect a position that was closed outside the bot (exchange stop filled, manual sell)."""
        if not self.in_position:
            return None
        bal = self.exchange.fetch_balance()
        base_total = float((bal.get("total") or {}).get(self.base) or 0.0)
        if base_total >= self.qty * 0.5:
            return None
        avg, fee, reason = self.last_mark or self.entry_price, 0.0, "position closed outside bot"
        if self.stop_order_id:
            try:
                o = self.exchange.fetch_order(self.stop_order_id, self.symbol)
                if o.get("status") == "closed" and o.get("average"):
                    avg = float(o["average"])
                    fee = self._fee_from_order(o, avg)
                    reason = "exchange stop filled"
            except Exception as e:
                reason += f" (stop order lookup failed: {type(e).__name__})"
        self.stop_order_id, self.stop_price = None, None
        qty = self.qty
        tax = qty * avg * self.tax_sell_rate
        self.cash = float((bal.get("free") or {}).get(self.quote) or self.cash)
        self.fees_paid += fee
        self.tax_paid += tax
        self._record_close(qty, avg, fee, tax, ts, bar_index, reason)
        self.fills.append(Fill(ts, "sell", qty, avg, fee, tax, reason, bar_index))
        self.qty, self.entry_price, self.entry_ts, self.entry_bar = 0.0, 0.0, "", -1
        self._notify(f"position closed outside bot: {reason} @ {avg:.2f}")
        return self.closed[-1]

    def update_stop(self, stop_price: float, ref_price: float) -> None:
        """Keep an exchange-side stop-limit sell in line with the strategy's stop (if enabled/supported)."""
        if not (self.exchange_stop and self.in_position and self._supports_stop()):
            return
        if self.stop_order_id and self.stop_price and abs(stop_price - self.stop_price) / self.stop_price < 0.001:
            return
        self._cancel_stop()
        limit = float(self.exchange.price_to_precision(self.symbol, stop_price * 0.995))
        stop = float(self.exchange.price_to_precision(self.symbol, stop_price))
        amount = float(self.exchange.amount_to_precision(self.symbol, self.qty))
        o = self.exchange.create_order(self.symbol, "stop_loss_limit", "sell", amount, limit, {"stopPrice": stop})
        self.stop_order_id, self.stop_price = o["id"], stop

    # ---------- internals ----------
    def _supports_stop(self) -> bool:
        return bool(self.exchange.has.get("createStopLimitOrder"))

    def _cancel_stop(self) -> None:
        if not self.stop_order_id:
            return
        try:
            self.exchange.cancel_order(self.stop_order_id, self.symbol)
        except Exception as e:  # already filled or gone: sync() will pick it up
            print(f"[live] cancel stop {self.stop_order_id}: {type(e).__name__}: {e}")
        self.stop_order_id, self.stop_price = None, None

    def _load_markets(self) -> None:
        if not self._markets_loaded:
            self.exchange.load_markets()
            self._markets_loaded = True

    def _market(self) -> dict:
        self._load_markets()
        return self.exchange.markets[self.symbol]

    def _normalize_amount(self, qty: float, price: float) -> float:
        m = self._market()
        limits = m.get("limits") or {}
        min_amt = (limits.get("amount") or {}).get("min") or 0.0
        min_cost = (limits.get("cost") or {}).get("min") or 0.0
        if qty * price > self.max_order_notional:
            qty = self.max_order_notional / price
        qty = float(self.exchange.amount_to_precision(self.symbol, qty))
        while qty > 0 and qty * price > self.max_order_notional:  # precision rounding may round up
            qty = float(self.exchange.amount_to_precision(self.symbol, qty - 10 ** -QTY_DECIMALS))
        if qty < min_amt or qty * price < min_cost:
            print(f"[live] order too small: qty={qty} notional={qty * price:.2f} "
                  f"(min amount {min_amt}, min cost {min_cost}); skipped")
            return 0.0
        return qty

    def _refresh_cash(self) -> None:
        """The exchange balance is the source of truth for cash; the computed value is only a fallback."""
        try:
            bal = self.exchange.fetch_balance()
            self.cash = float((bal.get("free") or {}).get(self.quote) or 0.0)
        except Exception as e:
            print(f"[live] balance refresh failed, keeping computed cash: {type(e).__name__}: {e}")

    def _check_daily_cap(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.orders_by_day.get(day, 0)
        if n >= self.max_daily_orders:
            raise LiveBrokerError(f"daily order cap reached ({n}/{self.max_daily_orders})")
        self.orders_by_day = {day: n + 1}

    def _place(self, side: str, qty: float, ref_price: float) -> tuple[float, float, float]:
        """Market order, wait for the fill, return (filled_qty, avg_price, fee_in_quote)."""
        self._check_daily_cap()
        amount = float(self.exchange.amount_to_precision(self.symbol, qty))
        order = self.exchange.create_order(self.symbol, "market", side, amount)
        deadline = time.time() + self.fill_timeout_s
        while order.get("status") not in ("closed", "canceled", "rejected", "expired") and time.time() < deadline:
            time.sleep(self.poll_interval_s)
            order = self.exchange.fetch_order(order["id"], self.symbol)
        if order.get("status") not in ("closed",) and float(order.get("filled") or 0) == 0:
            try:
                self.exchange.cancel_order(order["id"], self.symbol)
            except Exception:
                pass
            raise LiveBrokerError(f"{side} order {order.get('id')} not filled (status {order.get('status')})")
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or ref_price)
        fee = self._fee_from_order(order, avg)
        return filled, avg, fee

    def _fee_from_order(self, order: dict, avg_price: float) -> float:
        """Fee in quote currency. Exchanges may charge in base (Binance buys) or quote."""
        total = 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            cost = float(f["cost"])
            total += cost * avg_price if f.get("currency") == self.base else cost
        if total == 0.0 and order.get("filled"):
            total = float(order["filled"]) * avg_price * self.fee_rate  # estimate when the API omits fees
        return total

    def _record_close(self, qty, exit_price, fee, tax, ts, bar_index, reason) -> None:
        entry_cost = qty * self.entry_price * (1 + self.fee_rate)
        pnl = (qty * exit_price - fee - tax) - entry_cost
        self.closed.append(ClosedTrade(
            entry_ts=self.entry_ts, exit_ts=ts, qty=qty, entry_price=self.entry_price, exit_price=exit_price,
            pnl=pnl, pnl_pct=pnl / entry_cost * 100 if entry_cost else 0.0,
            bars_held=bar_index - self.entry_bar, exit_reason=reason))

    def _notify(self, text: str) -> None:
        if self.notifier:
            self.notifier.send(f"[{self.exchange.id} {self.symbol}] {text}")

    # ---------- persistence ----------
    def to_dict(self) -> dict:
        return {
            "kind": "live", "cash": self.cash, "qty": self.qty, "entry_price": self.entry_price,
            "entry_ts": self.entry_ts, "entry_bar": self.entry_bar,
            "fills": [f.__dict__ for f in self.fills], "closed": [t.__dict__ for t in self.closed],
            "fees_paid": self.fees_paid, "tax_paid": self.tax_paid, "orders_by_day": self.orders_by_day,
            "stop_order_id": self.stop_order_id, "stop_price": self.stop_price,
            "fee_rate": self.fee_rate, "tax_sell_rate": self.tax_sell_rate, "slippage_bps": 0.0,
        }

    def load_ledger(self, d: dict) -> "LiveBroker":
        self.cash = float(d.get("cash", 0.0))
        self.qty = float(d.get("qty", 0.0))
        self.entry_price = float(d.get("entry_price", 0.0))
        self.entry_ts = d.get("entry_ts", "")
        self.entry_bar = int(d.get("entry_bar", -1))
        self.fills = [Fill(**f) for f in d.get("fills", [])]
        self.closed = [ClosedTrade(**t) for t in d.get("closed", [])]
        self.fees_paid = float(d.get("fees_paid", 0.0))
        self.tax_paid = float(d.get("tax_paid", 0.0))
        self.orders_by_day = dict(d.get("orders_by_day", {}))
        self.stop_order_id = d.get("stop_order_id")
        self.stop_price = d.get("stop_price")
        return self


def make_live_exchange(name: str, api_key: str, secret: str, password: str | None = None,
                       sandbox: bool = False):
    """Authenticated ccxt client. Binance uses the spot-only public mirror for market data elsewhere;
    here we need the real (or testnet) private API."""
    import ccxt

    name = name.lower()
    if name not in ccxt.exchanges:
        raise ValueError(f"unknown exchange {name!r}")
    params = {"apiKey": api_key, "secret": secret, "enableRateLimit": True}
    if password:
        params["password"] = password
    if name == "binance":
        params["options"] = {"defaultType": "spot", "fetchMarkets": ["spot"]}
    ex = getattr(ccxt, name)(params)
    ex.session.trust_env = True
    if sandbox:
        if not ex.has.get("sandbox"):
            raise ValueError(f"{name} has no sandbox/testnet; test with the smallest real order instead")
        ex.set_sandbox_mode(True)
    return ex
