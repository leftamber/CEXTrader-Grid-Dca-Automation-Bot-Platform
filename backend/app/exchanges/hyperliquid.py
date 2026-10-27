import asyncio

from .base import ExchangeAdapter, MarketInfo, Ticker, OrderBook, ExchangeError
from .ccxt_adapter import CcxtAdapter

try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange as HlExchange
    from hyperliquid.utils import constants as hl_constants
    from eth_account import Account as EthAccount
    HAS_HL_SDK = True
except Exception:
    HAS_HL_SDK = False

HL_INTERVALS = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1d"}


class HyperliquidAdapter(ExchangeAdapter):
    id = "hyperliquid"
    name = "Hyperliquid (native SDK)"

    def __init__(self, account_cfg: dict):
        super().__init__(account_cfg)
        self.use_native = HAS_HL_SDK and bool(account_cfg.get("api_key"))
        self._ccxt = None
        self._info = None
        self._ex = None
        self._coin_map = {}
        self._name_map = {}

    def _ensure_ccxt(self):
        if not self._ccxt:
            self._ccxt = CcxtAdapter(self.cfg)
        return self._ccxt

    def _ensure_native(self):
        if not HAS_HL_SDK:
            raise ExchangeError("hyperliquid-python-sdk not installed (pip install hyperliquid-python-sdk)")
        if not self._info:
            base_url = hl_constants.TESTNET_API_URL if self.cfg.get("testnet") else hl_constants.MAINNET_API_URL
            self._info = Info(base_url, skip_ws=True)
            address = self.cfg.get("wallet_address") or ""
            self._info.clear_user_state_cache()
            self._base_url = base_url
            if self.cfg.get("api_key"):
                wallet = EthAccount.from_key(self.cfg["api_key"])
                self._ex = HlExchange(wallet, base_url, account_address=address or wallet.address)

    async def load_markets(self):
        await self._ensure_ccxt().load_markets()
        self._markets = dict(self._ensure_ccxt()._markets)
        if self.use_native:
            try:
                meta = await asyncio.to_thread(self._info_call, "spot_meta")
                for tok in meta.get("universe", []):
                    name = tok.get("name")
                    self._coin_map[name] = {"index": tok.get("index"), "is_perp": False}
            except Exception:
                pass

    def _info_call(self, method, *args):
        return getattr(self._info, method)(*args)

    async def ticker(self, symbol) -> Ticker:
        if self.use_native:
            try:
                self._ensure_native()
                mids = await asyncio.to_thread(self._info_call, "all_mids")
                coin = symbol.split("/")[0]
                px = float(mids.get(coin) or mids.get(self._coin_map.get(coin, {}).get("name", coin)) or 0)
                if px:
                    return Ticker(symbol=symbol, last=px, bid=px, ask=px, ts=int(asyncio.get_event_loop().time() * 1000))
            except Exception:
                pass
        return await self._ensure_ccxt().ticker(symbol)

    async def ohlcv(self, symbol, timeframe, limit=300, since=0):
        if self.use_native:
            try:
                self._ensure_native()
                coin = symbol.split("/")[0]
                interval = HL_INTERVALS.get(timeframe, "1h")
                end = int(asyncio.get_event_loop().time() * 1000) if not since else since + limit * 1000
                start = since or (end - limit * self._tf_ms(timeframe))
                snap = await asyncio.to_thread(self._info_call, "candles_snapshot", coin, interval, start, end)
                return [[int(s["t"]), float(s["o"]), float(s["h"]), float(s["l"]), float(s["c"]), float(s["v"])]
                        for s in snap]
            except Exception:
                pass
        return await self._ensure_ccxt().ohlcv(symbol, timeframe, limit, since)

    def _tf_ms(self, tf):
        n = int(tf[:-1])
        return n * {"m": 60000, "h": 3600000, "d": 86400000}.get(tf[-1], 60000)

    async def orderbook(self, symbol, limit=20):
        if self.use_native:
            try:
                self._ensure_native()
                coin = symbol.split("/")[0]
                l2 = await asyncio.to_thread(self._info_call, "l2_snapshot", coin)
                levels = l2.get("level", [])
                bids = [[float(x["px"]), float(x["sz"])] for x in levels if x["side"] == "B"][:limit]
                asks = [[float(x["px"]), float(x["sz"])] for x in levels if x["side"] == "A"][:limit]
                return OrderBook(symbol=symbol, bids=bids, asks=asks)
            except Exception:
                pass
        return await self._ensure_ccxt().orderbook(symbol, limit)

    async def balances(self) -> dict:
        if self.use_native:
            try:
                self._ensure_native()
                address = self.cfg.get("wallet_address")
                state = await asyncio.to_thread(self._info_call, "user_state", address)
                out = {"USDC": float(state.get("marginSummary", {}).get("accountValue", 0))}
                for b in state.get("withdrawable", []):
                    pass
                for bal in state.get("spot", {}).get("balances", []):
                    out[bal.get("coin")] = float(bal.get("total", 0))
                return out
            except Exception:
                pass
        return await self._ensure_ccxt().balances()

    async def create_order(self, symbol, side, otype, qty, price=None, post_only=False,
                           reduce_only=False, client_id=None):
        if self.use_native and otype == "limit":
            self._ensure_native()
            coin = symbol.split("/")[0]
            is_buy = side == "buy"
            limit_px = float(price)
            sz_decimals = self._sz_decimals(symbol)
            tif = {"limit": {"tif": "Alo"}} if post_only else {"limit": {"tif": "Gtc"}}
            def _place():
                return self._ex.order(coin, is_buy, qty, limit_px, tif, reduce_only=reduce_only)
            res = await asyncio.to_thread(_place)
            resp = res if isinstance(res, dict) else {}
            return {"exchange_oid": str(resp.get("response", {}).get("data", {}).get("statuses", [{}])[0]
                                        .get("oid", "")), "status": "open", "side": side, "type": otype,
                    "price": price or 0, "qty": qty, "filled": 0, "avg_price": 0}
        return await self._ensure_ccxt().create_order(symbol, side, otype, qty, price, post_only,
                                                      reduce_only, client_id)

    def _sz_decimals(self, symbol):
        mi = self._markets.get(symbol)
        return mi.amount_precision if mi else 4

    async def cancel_order(self, order_id, symbol=None):
        if self.use_native:
            self._ensure_native()
            coin = symbol.split("/")[0]
            await asyncio.to_thread(self._ex.cancel, coin, int(order_id))
            return {"ok": True}
        return await self._ensure_ccxt().cancel_order(order_id, symbol)

    async def open_orders(self, symbol=None):
        if self.use_native:
            self._ensure_native()
            address = self.cfg.get("wallet_address")
            front = await asyncio.to_thread(self._info_call, "open_orders", address)
            out = []
            for o in front:
                if symbol and o.get("coin") != symbol.split("/")[0]:
                    continue
                out.append({"exchange_oid": str(o.get("oid")), "symbol": f"{o.get('coin')}/USDC",
                            "side": o.get("side", "buy").lower(), "type": "limit",
                            "price": float(o.get("px", 0)), "qty": float(o.get("sz", 0)),
                            "filled": float(o.get("sz", 0)) - float(o.get("sz", 0)), "status": "open"})
            return out
        return await self._ensure_ccxt().open_orders(symbol)


class HyperliquidCcxtAdapter(CcxtAdapter):
    id = "hyperliquid"
    name = "Hyperliquid (ccxt)"
