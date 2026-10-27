import asyncio

from ..core.utils import now_ms, round_precision, uid
from ..exchanges.base import MarketInfo, ExchangeError

TRANSIENT_HINTS = ("timeout", "timed out", "network", "temporarily", "rate limit", "ratelimit",
                   "429", "-1003", "connection", "reset", "503", "502", "service unavailable")


def _is_transient(err_msg: str) -> bool:
    low = str(err_msg).lower()
    return any(h in low for h in TRANSIENT_HINTS)


class OrderManager:
    def __init__(self, bot_id: str, adapter, storage, bus, risk_manager):
        self.bot_id = bot_id
        self.adapter = adapter
        self.storage = storage
        self.bus = bus
        self.risk = risk_manager
        self.open_map: dict = {}
        self.all_orders: dict = {}

    def market(self, symbol) -> MarketInfo:
        return self.adapter.market(symbol)

    def _olog(self, msg: str, level: str = "info"):
        self.storage.insert_event(level, self.bot_id, "orders", msg)
        self.bus.publish("log", {"ts": now_ms(), "level": level, "bot_id": self.bot_id,
                                 "message": msg, "data": {}})

    def open_orders(self, symbol=None) -> list:
        out = []
        for o in self.open_map.values():
            if symbol and o["symbol"] != symbol:
                continue
            out.append(o)
        return out

    async def place(self, symbol: str, side: str, otype: str, qty: float, price: float = None,
                    post_only: bool = False, reduce_only: bool = False, role: str = "",
                    cycle_id: str = "", meta: dict = None) -> dict:
        mi = self.market(symbol)
        qty = round_precision(qty, mi.amount_precision, mode="nearest")
        price = round_precision(price, mi.price_precision) if price else None
        if qty <= 0:
            raise ExchangeError(f"qty {qty} below min precision")
        if price and price <= 0:
            raise ExchangeError("bad price")
        if qty < mi.min_amount:
            raise ExchangeError(f"qty {qty} below exchange min {mi.min_amount}")
        if price and price * qty < mi.min_cost:
            raise ExchangeError(f"notional {price * qty:.2f} below exchange min cost {mi.min_cost}")
        self.risk.check_order(symbol, side, qty, price or 0, len(self.open_map))
        oid = "o" + uid() + uid()[:6]
        client_id = f"kb:{self.bot_id}:{oid}"[:36]
        last_err = None
        res = None
        for attempt in range(3):
            try:
                res = await self.adapter.create_order(symbol, side, otype, qty, price,
                                                      post_only=post_only, reduce_only=reduce_only,
                                                      client_id=client_id)
                break
            except Exception as e:
                last_err = e
                if attempt < 2 and _is_transient(str(e)):
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                break
        if res is None:
            self._record({**self._blank(symbol, side, otype, qty, price, post_only, role, cycle_id, meta),
                          "oid": oid, "status": "rejected", "meta": {**(meta or {}), "error": str(last_err)[:300]}})
            raise last_err
        o = self._blank(symbol, side, otype, qty, price, post_only, role, cycle_id, meta)
        o["oid"] = oid
        o["exchange_oid"] = str(res.get("exchange_oid") or "")
        o["status"] = res.get("status", "open") if res.get("status") in ("open", "partially_filled", "filled") else "open"
        o["filled"] = float(res.get("filled") or 0)
        o["created_at"] = res.get("raw_ts") or o["created_at"]
        self.open_map[o["oid"]] = o
        self.all_orders[o["oid"]] = o
        self._record(o)
        px_str = "market" if not o["price"] else f"{o['price']:.8g}"
        role = o["meta"].get("role", "")
        self._olog(f"order placed: {otype} {side} {o['qty']:g} {symbol} @ {px_str}"
                   + (f" [{role}]" if role else ""))
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "order_placed",
                                       "order": o, "ts": now_ms()})
        if o["status"] == "filled" and o["filled"] >= qty:
            self._mark_filled(o, res.get("avg_price") or price or 0)
        return o

    async def cancel(self, oid: str) -> bool:
        o = self.open_map.get(oid)
        if not o:
            return False
        try:
            await self.adapter.cancel_order(o.get("exchange_oid") or oid, o["symbol"])
        except Exception:
            pass
        o["status"] = "canceled"
        self.open_map.pop(oid, None)
        self._record(o)
        self._olog(f"order canceled: {o['type']} {o['side']} {o['qty']:g} {o['symbol']} @ {o['price']:.8g}", "debug")
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "order_canceled",
                                       "order": o, "ts": now_ms()})
        return True

    async def amend(self, oid: str, price: float = None, qty: float = None):
        o = self.open_map.get(oid)
        if not o:
            raise ExchangeError("order not found")
        res = await self.adapter.amend_order(o.get("exchange_oid") or oid, o["symbol"],
                                             o["side"], price, qty)
        if price:
            o["price"] = price
        if qty:
            o["qty"] = qty
            o["filled"] = 0
        self._record(o)
        return o

    async def cancel_all(self, symbol=None):
        oids = [o["oid"] for o in self.open_map.values() if not symbol or o["symbol"] == symbol]
        for oid in oids:
            await self.cancel(oid)

    async def cancel_all_adapter(self, symbol=None):
        try:
            for o in await self.adapter.open_orders(symbol):
                await self.adapter.cancel_order(o.get("exchange_oid"), o["symbol"])
        except Exception:
            pass
        if not symbol:
            self.open_map.clear()
        else:
            for oid in [k for k, v in self.open_map.items() if v["symbol"] == symbol]:
                self.open_map.pop(oid, None)

    def on_fill(self, order_id_hint: str, side: str, price: float, qty: float, fee: float, ts: int, taker: bool):
        o = self.open_map.get(order_id_hint) or self.all_orders.get(order_id_hint)
        if not o:
            for cand in self.open_map.values():
                if cand["side"] == side and abs(cand["price"] - price) / max(price, 1e-9) < 0.001:
                    o = cand
                    break
        if not o:
            return None
        o["filled"] = min(o["qty"], o["filled"] + qty)
        o["avg_price"] = price
        if o["filled"] >= o["qty"] - 1e-12:
            self._mark_filled(o, price)
        else:
            o["status"] = "partially_filled"
            self._record(o)
        self.all_orders[o["oid"]] = o
        return o

    def _mark_filled(self, o: dict, price: float):
        o["status"] = "filled"
        o["avg_price"] = price or o["price"]
        self.open_map.pop(o["oid"], None)
        self._record(o)
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "order_filled",
                                       "order": o, "ts": now_ms()})

    def reconcile(self, adapter_orders: list):
        live_ids = set()
        for ao in adapter_orders:
            live_ids.add(str(ao.get("exchange_oid")))
        for oid in list(self.open_map.keys()):
            o = self.open_map[oid]
            if o.get("exchange_oid") and str(o["exchange_oid"]) not in live_ids:
                o["status"] = "filled" if o["filled"] > 0 else "canceled"
                if o["status"] == "filled" and not o["avg_price"]:
                    o["avg_price"] = o["price"]
                self.open_map.pop(oid)
                self._record(o)

    def _blank(self, symbol, side, otype, qty, price, post_only, role, cycle_id, meta):
        return {"oid": "", "exchange_oid": "", "bot_id": self.bot_id, "symbol": symbol, "side": side,
                "type": otype, "price": price or 0, "qty": qty, "filled": 0, "avg_price": 0,
                "status": "open", "post_only": post_only, "created_at": now_ms(),
                "meta": {**(meta or {}), "role": role, "cycle_id": cycle_id}}

    def _record(self, o: dict):
        self.storage.upsert_order({**o, "bot_id": self.bot_id})
