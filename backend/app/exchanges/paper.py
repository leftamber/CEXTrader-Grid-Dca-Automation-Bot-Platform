import asyncio
import random
import time
import traceback

from .base import (ExchangeAdapter, MarketInfo, Ticker, OrderBook, ExchangeError,
                   tf_seconds, FEED_FALLBACK)
from .ccxt_adapter import PublicCcxtAdapter
from ..core.utils import now_ms, uid, round_precision, decimals_from_step

# Seed prices for the synthetic feed used when no public source is reachable.
SYNTH_SEED = {
    "BTC/USDT": 65000.0, "BTC/USDC": 65000.0, "ETH/USDT": 3200.0, "SOL/USDT": 150.0,
    "BNB/USDT": 580.0, "XRP/USDT": 2.1, "ADA/USDT": 0.75, "DOGE/USDT": 0.16,
}


class PaperExchange(ExchangeAdapter):
    id = "paper"
    name = "Paper Trading"

    def __init__(self, account_cfg: dict, bus=None, storage=None):
        super().__init__(account_cfg)
        self.account_id = account_cfg.get("id") or "paper"
        self.bus = bus
        self.storage = storage
        self.underlying = account_cfg.get("underlying") or "bybit"
        self.maker_fee_pct = float(account_cfg.get("maker_fee_pct", 0.1))
        self.taker_fee_pct = float(account_cfg.get("taker_fee_pct", 0.1))
        self.slippage_bps = float(account_cfg.get("slippage_bps", 2.0))
        self.wallet: dict = {k: float(v) for k, v in (account_cfg.get("initial_balances") or {"USDT": 10000}).items()}
        self.orders: dict = {}
        self.pos_map: dict = {}
        self._last_price: dict = {}
        self._synth_price: dict = {}
        self.feed_status: str = "starting"  # live | cached | synthetic | starting
        self._last_feed_error: str = ""
        self._pub_fail_count = 0
        self._pub_skip_until = 0
        self._dirty = False
        self._persist_task = None
        self._public = None
        for ex in [self.underlying] + FEED_FALLBACK:
            try:
                self._public = PublicCcxtAdapter(ex)
                if ex != self.underlying:
                    self.underlying = ex
                break
            except Exception:
                continue
        self._restore()

    @property
    def is_paper(self):
        return True

    def _restore(self):
        if not self.storage:
            return
        snap = self.storage.get_setting(f"paper_state_{self.account_id}")
        if snap:
            try:
                self.wallet = {k: float(v) for k, v in snap.get("balances", {}).items()}
                self.orders = snap.get("orders", {})
                self.pos_map = snap.get("positions", {})
            except Exception:
                pass

    def _snapshot(self):
        if not self.storage:
            return
        self.storage.set_setting(f"paper_state_{self.account_id}",
                                 {"balances": self.wallet, "orders": self.orders,
                                  "positions": self.pos_map, "ts": now_ms()})

    async def start_persistence(self):
        async def loop():
            while True:
                await asyncio.sleep(20)
                if self._dirty:
                    self._snapshot()
                    self._dirty = False
        self._persist_task = asyncio.create_task(loop())

    async def close(self):
        if self._persist_task:
            self._persist_task.cancel()
        self._snapshot()

    def reset(self, balances: dict = None):
        if balances:
            merged = dict(self.wallet)
            merged.update({k: float(v) for k, v in balances.items() if v})
            self.wallet = merged
        else:
            self.wallet = {k: float(v) for k, v in (self.cfg.get("initial_balances") or {"USDT": 10000}).items()}
        self.orders = {}
        self.pos_map = {}
        self._snapshot()

    async def load_markets(self):
        try:
            await self._public.load_markets()
            self._markets = dict(self._public._markets)
        except Exception as e:
            self._markets = {}
            self._last_feed_error = str(e)[:200]

    def _default_market(self, symbol: str) -> MarketInfo:
        base, quote = (symbol.split("/") + ["USDT"])[:2]
        quote = quote.split(":")[0]
        return MarketInfo(symbol=symbol, base=base, quote=quote, price_step=0.01, amount_step=0.00001,
                          min_amount=0, min_cost=0, maker_fee_pct=self.maker_fee_pct,
                          taker_fee_pct=self.taker_fee_pct)

    def market(self, symbol: str) -> MarketInfo:
        if symbol in self._markets:
            mi = self._markets[symbol]
            mi.maker_fee_pct = self.maker_fee_pct
            mi.taker_fee_pct = self.taker_fee_pct
            return mi
        return self._default_market(symbol)

    def _synthetic_ticker(self, symbol: str) -> Ticker:
        """Random-walk price used only when no public feed is reachable."""
        p = self._synth_price.get(symbol)
        if not p:
            seed = SYNTH_SEED.get(symbol)
            if seed is None:
                for sym, px in self._last_price.items():
                    if sym.split("/")[0] == symbol.split("/")[0]:
                        seed = px.mid() or px.last
                        break
            p = seed if seed and seed > 0 else 100.0
        p = max(p * (1 + random.gauss(0, 0.0004)), 1e-8)
        self._synth_price[symbol] = p
        return Ticker(symbol=symbol, bid=p * 0.9999, ask=p * 1.0001, last=p,
                      ts=now_ms())

    def _pub_blocked(self) -> bool:
        """Circuit breaker: stop hammering a dead public feed for a while."""
        return now_ms() < self._pub_skip_until

    def _pub_fail(self, err: str):
        self._pub_fail_count += 1
        self._last_feed_error = str(err)[:200]
        if self._pub_fail_count >= 3:
            self._pub_skip_until = now_ms() + 60000
            self._pub_fail_count = 0

    async def ticker(self, symbol: str) -> Ticker:
        if not self._pub_blocked():
            try:
                t = await self._public.ticker(symbol)
                if t and (t.mid() or t.last):
                    self._pub_fail_count = 0
                    self._last_price[symbol] = t
                    if self.feed_status != "live":
                        self.feed_status = "live"
                        self._emit("log", {"level": "info", "bot_id": "",
                                           "message": f"paper feed live via {self.underlying} ({symbol})",
                                           "data": {}})
                    return t
                raise ExchangeError("empty ticker from public feed")
            except Exception as e:
                self._pub_fail(e)
        cached = self._last_price.get(symbol)
        if cached and (cached.mid() or cached.last):
            if self.feed_status == "live":
                self.feed_status = "cached"
                self._emit("log", {"level": "warning", "bot_id": "",
                                   "message": f"paper feed '{self.underlying}' failing — using last "
                                              f"known prices ({self._last_feed_error[:80]})",
                                   "data": {}})
            return cached
        prev = self.feed_status
        self.feed_status = "synthetic"
        if prev != "synthetic":
            self._emit("log", {"level": "warning", "bot_id": "",
                               "message": f"market feed '{self.underlying}' unreachable "
                                          f"({self._last_feed_error[:80]}) — paper engine switched "
                                          f"to simulated prices for {symbol}",
                               "data": {}})
        return self._synthetic_ticker(symbol)

    async def ohlcv(self, symbol, timeframe, limit=300, since=0):
        try:
            return await self._public.ohlcv(symbol, timeframe, limit, since)
        except Exception:
            return []

    async def orderbook(self, symbol, limit=20):
        try:
            return await self._public.orderbook(symbol, limit)
        except Exception:
            t = await self.ticker(symbol)
            mid = t.mid() or t.last
            return OrderBook(symbol=symbol, bids=[[mid * 0.999, 1]], asks=[[mid * 1.001, 1]], ts=now_ms())

    async def fetch_balances(self) -> dict:
        return dict(self.wallet)

    async def positions(self) -> list:
        return [{"symbol": s, "qty": p["qty"], "avg_price": p["avg_price"], "leverage": p.get("leverage", 1)}
                for s, p in self.pos_map.items() if p["qty"] != 0]

    async def open_orders(self, symbol=None):
        return [o for o in self.orders.values() if o["status"] in ("open", "partially_filled")
                and (symbol is None or o["symbol"] == symbol)]

    def _emit(self, topic, payload):
        if self.bus:
            self.bus.publish(topic, payload)

    def sync_ticker(self, symbol: str, ticker: Ticker):
        if ticker and (ticker.mid() or ticker.last):
            self._last_price[symbol] = ticker
            self._pub_fail_count = 0
            self._pub_skip_until = 0
            if self.feed_status in ("synthetic", "starting", "cached"):
                self.feed_status = "live"
        self._check_fills(symbol, ticker)

    def _check_fills(self, symbol: str, t: Ticker):
        ask = t.ask or t.last
        bid = t.bid or t.last
        for o in list(self.orders.values()):
            if o["symbol"] != symbol or o["status"] not in ("open", "partially_filled"):
                continue
            if o["type"] != "limit":
                continue
            try:
                if o["side"] == "buy" and ask > 0 and ask <= o["price"]:
                    self._fill(o, o["price"], o["qty"] - o["filled"], taker=False)
                elif o["side"] == "sell" and bid > 0 and bid >= o["price"]:
                    base = self.market(symbol).base
                    avail = self.wallet.get(base, 0)
                    q = min(o["qty"] - o["filled"], avail) if not self.is_perp_symbol(symbol) \
                        else o["qty"] - o["filled"]
                    if q > 0:
                        self._fill(o, o["price"], q, taker=False)
            except ExchangeError as e:
                self._emit("log", {"level": "warning", "bot_id": "",
                                   "message": f"paper fill skipped: {e}", "data": {}})
                o["status"] = "canceled"

    def _fill(self, o: dict, price: float, qty: float, taker: bool):
        qty = round_precision(qty, self.market(o["symbol"]).amount_precision) or qty
        if qty <= 0:
            o["status"] = "filled"
            return
        fee_pct = self.taker_fee_pct if taker else self.maker_fee_pct
        fee = price * qty * fee_pct / 100.0
        o["filled"] += qty
        o["avg_price"] = price if not o["avg_price"] else (o["avg_price"] * (o["filled"] - qty) + price * qty) / o["filled"]
        if o["filled"] >= o["qty"] - 1e-12:
            o["status"] = "filled"
        else:
            o["status"] = "partially_filled"
        self._apply_balance(o, price, qty, fee)
        self._dirty = True
        self._emit("fill", {"account_id": self.account_id, "paper": True, "symbol": o["symbol"],
                            "order_id": o["oid"], "client_id": o.get("client_id", ""),
                            "side": o["side"], "price": price, "qty": qty, "fee": fee,
                            "fee_currency": self.market(o["symbol"]).quote,
                            "notional": price * qty, "taker": taker, "ts": now_ms(),
                            "type": o["type"]})
        self._emit("order_update", {"account_id": self.account_id, "order": self.norm_order(o)})

    def _apply_balance(self, o: dict, price: float, qty: float, fee: float):
        mi = self.market(o["symbol"])
        base, quote = mi.base, mi.quote
        notional = price * qty
        if self.is_perp_symbol(o["symbol"]):
            pos = self.pos_map.setdefault(o["symbol"], {"qty": 0.0, "avg_price": 0.0, "leverage": 1.0})
            signed = qty if o["side"] == "buy" else -qty
            if o.get("reduce_only"):
                close_qty = min(abs(pos["qty"]), qty)
                if pos["qty"] > 0:
                    pnl = (price - pos["avg_price"]) * close_qty
                elif pos["qty"] < 0:
                    pnl = (pos["avg_price"] - price) * close_qty
                else:
                    pnl = 0.0
                self.wallet[quote] = self.wallet.get(quote, 0) + pnl - fee
                pos["qty"] += signed
                if abs(pos["qty"]) < 1e-12:
                    pos["qty"] = 0.0
                    pos["avg_price"] = 0.0
            else:
                if (pos["qty"] > 0 and signed > 0) or (pos["qty"] < 0 and signed < 0) or pos["qty"] == 0:
                    total = abs(pos["qty"]) * pos["avg_price"] + qty * price
                    pos["qty"] += signed
                    pos["avg_price"] = total / abs(pos["qty"]) if pos["qty"] != 0 else 0.0
                else:
                    pos["qty"] += signed
                self.wallet[quote] = self.wallet.get(quote, 0) - fee
            return
        if o["side"] == "buy":
            if self.wallet.get(quote, 0) < notional + fee:
                raise ExchangeError(f"insufficient {quote} balance")
            self.wallet[quote] = self.wallet.get(quote, 0) - notional - fee
            self.wallet[base] = self.wallet.get(base, 0) + qty
        else:
            req_qty = qty
            avail = self.wallet.get(base, 0)
            if avail < qty:
                qty = avail
                o["qty"] = qty
                o["filled"] = min(o["filled"], qty)
                if o["filled"] >= o["qty"] - 1e-12:
                    o["status"] = "filled"
                fee = fee * (qty / req_qty) if req_qty else fee
                notional = price * qty
            self.wallet[base] = self.wallet.get(base, 0) - qty
            self.wallet[quote] = self.wallet.get(quote, 0) + notional - fee

    def is_perp_symbol(self, symbol: str) -> bool:
        return ":" in symbol or (self.cfg.get("market_type") == "perp")

    async def create_order(self, symbol, side, otype, qty, price=None, post_only=False,
                           reduce_only=False, client_id=None):
        mi = self.market(symbol)
        qty = round_precision(qty, mi.amount_precision, mode="nearest")
        if qty <= 0:
            raise ExchangeError("qty below precision")
        t = await self.ticker(symbol)
        oid = "p" + uid()
        order = {"oid": oid, "exchange_oid": oid, "client_id": client_id or "", "symbol": symbol,
                 "side": side, "type": otype, "price": round_precision(price, mi.price_precision) if price else 0.0,
                 "qty": qty, "filled": 0.0, "avg_price": 0.0, "status": "open",
                 "post_only": bool(post_only), "reduce_only": bool(reduce_only),
                 "created_at": now_ms(), "meta": {}}
        if otype == "market":
            if side == "buy":
                px = (t.ask or t.last) * (1 + self.slippage_bps / 10000.0)
            else:
                px = (t.bid or t.last) * (1 - self.slippage_bps / 10000.0)
            if px <= 0:
                raise ExchangeError("no market price")
            if side == "sell" and not self.is_perp_symbol(symbol):
                base = self.market(symbol).base
                avail = self.wallet.get(base, 0)
                if avail <= 0:
                    raise ExchangeError(f"no {base} balance to sell")
                if qty > avail:
                    qty = round_precision(avail, mi.amount_precision, mode="nearest") or avail
                    order["qty"] = qty
            self.orders[oid] = order
            self._fill(order, px, qty, taker=True)
            return self.norm_order(order)
        if not order["price"] or order["price"] <= 0:
            raise ExchangeError("limit order requires price")
        if post_only:
            if side == "buy" and (t.ask or t.last) <= order["price"]:
                raise ExchangeError("post-only would cross book")
            if side == "sell" and (t.bid or t.last) >= order["price"]:
                raise ExchangeError("post-only would cross book")
        self.orders[oid] = order
        self._dirty = True
        self._emit("order_update", {"account_id": self.account_id, "order": self.norm_order(order)})
        return self.norm_order(order)

    async def cancel_order(self, order_id, symbol=None):
        o = self.orders.get(order_id)
        if not o or o["status"] not in ("open", "partially_filled"):
            return {"ok": True}
        o["status"] = "canceled"
        self._dirty = True
        self._emit("order_update", {"account_id": self.account_id, "order": self.norm_order(o)})
        return {"ok": True}

    async def amend_order(self, order_id, symbol=None, side=None, price=None, qty=None):
        o = self.orders.get(order_id)
        if not o or o["status"] not in ("open",):
            raise ExchangeError("order not amendable")
        mi = self.market(o["symbol"])
        if price:
            o["price"] = round_precision(price, mi.price_precision)
        if qty:
            o["qty"] = round_precision(qty, mi.amount_precision)
            o["filled"] = 0.0
        self._dirty = True
        return self.norm_order(o)

    async def my_trades(self, symbol, since=0):
        return []

    def unrealized_pnl(self, symbol: str, price: float) -> float:
        pos = self.pos_map.get(symbol)
        if not pos or pos["qty"] == 0 or not price:
            return 0.0
        return (price - pos["avg_price"]) * pos["qty"]

    def equity(self, prices: dict) -> float:
        total = 0.0
        for cur, amt in self.wallet.items():
            if cur in ("USDT", "USD", "USDC", "BUSD"):
                total += amt
                continue
            for sym, px in prices.items():
                if sym.split("/")[0] == cur:
                    p = px if isinstance(px, (int, float)) else (px.mid() or px.last or 0)
                    total += amt * p
                    break
        for sym, pos in self.pos_map.items():
            px = prices.get(sym)
            if px is not None and pos["qty"] != 0:
                p = px if isinstance(px, (int, float)) else (px.mid() or px.last or 0)
                total += (p - pos["avg_price"]) * pos["qty"]
        return total

    def norm_order(self, o: dict) -> dict:
        return {"oid": o["oid"], "exchange_oid": o["exchange_oid"], "client_id": o.get("client_id", ""),
                "symbol": o["symbol"], "side": o["side"], "type": o["type"], "price": o["price"],
                "qty": o["qty"], "filled": o["filled"], "avg_price": o["avg_price"], "status": o["status"],
                "post_only": o.get("post_only", False), "reduce_only": o.get("reduce_only", False),
                "ts": o.get("created_at", now_ms())}


