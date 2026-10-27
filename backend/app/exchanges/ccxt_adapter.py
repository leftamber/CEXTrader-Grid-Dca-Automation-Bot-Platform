import ccxt.async_support as ccxt
from .base import ExchangeAdapter, MarketInfo, Ticker, OrderBook, ExchangeError, tf_seconds


def _ccxt_class(exchange_id: str):
    cls = getattr(ccxt, exchange_id, None)
    if cls is None:
        raise ExchangeError(f"ccxt has no exchange '{exchange_id}'")
    return cls


class CcxtAdapter(ExchangeAdapter):
    def __init__(self, account_cfg: dict, public_only: bool = False):
        super().__init__(account_cfg)
        self.exchange_id = account_cfg.get("exchange") or account_cfg.get("underlying") or "binance"
        self.public_only = public_only or not account_cfg.get("api_key")
        cls = _ccxt_class(self.exchange_id)
        mtype = "linear" if account_cfg.get("market_type") == "perp" else "spot"
        opts = {"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": mtype}}
        if self.public_only:
            opts["timeout"] = 9000
        if self.exchange_id == "binance":
            opts["options"]["fetchMarkets"] = ["spot"] if mtype == "spot" else ["linear"]
        if self.public_only:
            self.client = cls(opts)
        else:
            opts.update({"apiKey": account_cfg.get("api_key") or "", "secret": account_cfg.get("secret") or ""})
            if account_cfg.get("password"):
                opts["password"] = account_cfg["password"]
            if account_cfg.get("subaccount"):
                opts["options"]["subaccount"] = account_cfg["subaccount"]
            self.client = cls(opts)
            if account_cfg.get("testnet"):
                try:
                    self.client.set_sandbox_mode(True)
                except Exception:
                    pass

    @property
    def is_paper(self):
        return False

    async def load_markets(self):
        markets = await self.client.load_markets()
        for sym, m in markets.items():
            if m.get("active") is False:
                continue
            limits = m.get("limits", {})
            amount = limits.get("amount", {})
            cost = limits.get("cost", {})
            price_precision = self._norm_precision(m.get("precision", {}).get("price"), 0.01)
            amount_precision = self._norm_precision(m.get("precision", {}).get("amount"), 0.00001)
            mi = MarketInfo(
                symbol=sym, base=m.get("base", ""), quote=m.get("quote", ""),
                price_precision=price_precision,
                amount_precision=amount_precision,
                price_step=10 ** -price_precision if price_precision >= 0 else 1.0,
                amount_step=10 ** -amount_precision if amount_precision >= 0 else 1.0,
                min_amount=float(amount.get("min") or 0),
                min_cost=float(cost.get("min") or 0),
                maker_fee_pct=float((m.get("maker") or 0.001) * 100),
                taker_fee_pct=float((m.get("taker") or 0.001) * 100),
                spot=m.get("spot", True),
                contract_size=float(m.get("contractSize") or 1),
            )
            self._markets[sym] = mi

    @staticmethod
    def _norm_precision(raw, default_step: float) -> int:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 4
        if v >= 1:
            return int(v)
        if v <= 0:
            return 4
        d = 0
        s = v
        while d < 12 and abs(s - round(s)) > 1e-9:
            s *= 10
            d += 1
        return d

    def market(self, symbol: str) -> MarketInfo:
        if symbol not in self._markets:
            for s, m in self._markets.items():
                if s.replace("/", "").replace(":", "").upper() == symbol.replace("/", "").replace(":", "").upper():
                    return m
            raise ExchangeError(f"unknown market {symbol}")
        return self._markets[symbol]

    async def ticker(self, symbol: str) -> Ticker:
        t = await self.client.fetch_ticker(symbol)
        return Ticker(symbol=symbol, bid=float(t.get("bid") or 0), ask=float(t.get("ask") or 0),
                      last=float(t.get("last") or t.get("close") or 0),
                      high=float(t.get("high") or 0), low=float(t.get("low") or 0),
                      volume=float(t.get("baseVolume") or 0), ts=int(t.get("timestamp") or 0))

    async def ohlcv(self, symbol: str, timeframe: str, limit: int = 300, since: int = 0) -> list:
        if since:
            candles = await self.client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        else:
            candles = await self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in candles]

    async def orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        ob = await self.client.fetch_order_book(symbol, limit=limit)
        return OrderBook(symbol=symbol, bids=ob.get("bids") or [], asks=ob.get("asks") or [],
                         ts=int(ob.get("timestamp") or 0))

    async def balances(self) -> dict:
        bal = await self.client.fetch_balance()
        out = {}
        for k, v in (bal.get("total") or {}).items():
            if v:
                out[k] = float(v)
        return out

    async def create_order(self, symbol, side, otype, qty, price=None, post_only=False,
                           reduce_only=False, client_id=None):
        params = {}
        if post_only:
            params["postOnly"] = True
        if reduce_only:
            params["reduceOnly"] = True
        if client_id:
            params["clientOrderId"] = client_id
        o = await self.client.create_order(symbol, otype, side, qty, price, params)
        return self._norm_order(o)

    async def cancel_order(self, order_id, symbol):
        try:
            await self.client.cancel_order(order_id, symbol)
        except Exception as e:
            if "OrderNotFound" not in str(e) and "not found" not in str(e).lower():
                raise
        return {"ok": True}

    async def amend_order(self, order_id, symbol, side="buy", price=None, qty=None):
        try:
            o = await self.client.edit_order(order_id, symbol, "limit", side, qty, price)
            return self._norm_order(o)
        except Exception as e:
            raise ExchangeError(f"amend not supported: {e}")

    async def open_orders(self, symbol=None):
        orders = await self.client.fetch_open_orders(symbol)
        return [self._norm_order(o) for o in orders]

    async def my_trades(self, symbol, since=0):
        trades = await self.client.fetch_my_trades(symbol, since=since, limit=100)
        return [{"id": t.get("id") or t.get("order"), "ts": int(t.get("timestamp") or 0), "symbol": symbol,
                 "side": t.get("side"), "price": float(t.get("price") or 0), "qty": float(t.get("amount") or 0),
                 "fee": float((t.get("fee") or {}).get("cost") or 0),
                 "fee_currency": (t.get("fee") or {}).get("currency") or "",
                 "order_id": t.get("order") or "",
                 "taker": t.get("takerOrMaker") == "taker"} for t in trades]

    def _norm_order(self, o: dict) -> dict:
        return {"exchange_oid": o.get("id"), "status": o.get("status", "open"),
                "side": o.get("side"), "type": o.get("type"), "price": float(o.get("price") or 0),
                "qty": float(o.get("amount") or 0), "filled": float(o.get("filled") or 0),
                "avg_price": float(o.get("average") or 0), "post_only": bool(o.get("postOnly")),
                "raw_ts": int(o.get("timestamp") or 0)}


class PublicCcxtAdapter(CcxtAdapter):
    def __init__(self, exchange_id):
        if isinstance(exchange_id, dict):
            super().__init__(exchange_id, public_only=True)
        else:
            super().__init__({"exchange": exchange_id, "api_key": ""}, public_only=True)
