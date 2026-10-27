import asyncio
import json

from .base import ExchangeAdapter, MarketInfo, Ticker, OrderBook, ExchangeError

try:
    from dydx_v4_client import NodeClient, Wallet
    from dydx_v4_client.network import TESTNET, MAINNET
    from dydx_v4_client.keypair import KeyPair
    HAS_DYDX_SDK = True
except Exception:
    HAS_DYDX_SDK = False

INDEXER_MAINNET = "https://indexer.dydx.trade/v4"
INDEXER_TESTNET = "https://indexer-stage-v4.dydx.trade/v4"
RESOLUTIONS = {"1m": "1MIN", "5m": "5MINS", "15m": "15MINS", "30m": "30MINS",
               "1h": "1HOUR", "4h": "4HOURS", "1d": "1DAY"}


class DydxAdapter(ExchangeAdapter):
    id = "dydx"
    name = "dYdX v4"

    def __init__(self, account_cfg: dict):
        super().__init__(account_cfg)
        self.indexer = INDEXER_TESTNET if account_cfg.get("testnet") else INDEXER_MAINNET
        self._node = None
        self._wallet = None
        self._markets = {}
        self._aiohttp = None

    def _http(self):
        if not self._aiohttp:
            import aiohttp
            self._aiohttp = aiohttp.ClientSession()
        return self._aiohttp

    async def _get(self, path, params=None):
        async with self._http().get(self.indexer + path, params=params) as r:
            if r.status != 200:
                raise ExchangeError(f"dydx indexer {r.status}: {await r.text()}")
            return json.loads(await r.text())

    async def load_markets(self):
        data = await self._get("/perpetual-markets")
        for sym, m in data.get("markets", {}).items():
            mi = MarketInfo(symbol=sym, base=sym.split("-")[0], quote="USD",
                            price_step=10 ** -int(m.get("stepSizeDecimals", 1) or 1),
                            amount_step=10 ** -int(m.get("stepSizeDecimals", 4) or 4),
                            min_amount=0, min_cost=0,
                            maker_fee_pct=float(m.get("makerFee", "0.0002")) * 100,
                            taker_fee_pct=float(m.get("takerFee", "0.0005")) * 100,
                            spot=False, max_leverage=float(m.get("maxLeverage", "10")))
            self._markets[sym] = mi

    async def ticker(self, symbol) -> Ticker:
        data = await self._get("/perpetual-markets", {"ticker": symbol})
        m = data.get("markets", {}).get(symbol)
        if not m:
            raise ExchangeError(f"unknown dydx market {symbol}")
        return Ticker(symbol=symbol, bid=float(m.get("oraclePrice", 0) or 0), ask=float(m.get("oraclePrice", 0) or 0),
                      last=float(m.get("price", 0) or m.get("oraclePrice", 0) or 0),
                      high=float(m.get("high24h", 0) or 0), low=float(m.get("low24h", 0) or 0),
                      volume=float(m.get("volume24h", 0) or 0))

    async def ohlcv(self, symbol, timeframe, limit=300, since=0):
        res = RESOLUTIONS.get(timeframe, "1HOUR")
        params = {"resolution": res, "id": symbol}
        data = await self._get("/candles", params)
        return [[int(c["startedAt"][:-6]) if c.get("startedAt") else 0, float(c["open"]), float(c["high"]),
                 float(c["low"]), float(c["close"]), float(c.get("baseTokenVolume", 0))]
                for c in data.get("candles", [])][-limit:]

    async def orderbook(self, symbol, limit=20):
        data = await self._get("/orderbooks", params={"ticker": symbol})
        ob = data.get(symbol, {})
        bids = [[float(x["price"]), float(x["size"])] for x in ob.get("bids", [])][:limit]
        asks = [[float(x["price"]), float(x["size"])] for x in ob.get("asks", [])][:limit]
        return OrderBook(symbol=symbol, bids=bids, asks=asks)

    async def balances(self) -> dict:
        if not HAS_DYDX_SDK:
            raise ExchangeError("dydx-v4-client not installed (pip install dydx-v4-client)")
        self._ensure_node()
        addr = self._wallet.address
        perp = await asyncio.to_thread(self._node.get_perpetual_positions_for_address, addr)
        asset = await asyncio.to_thread(self._node.get_account_balances, addr)
        out = {}
        try:
            for a in asset.get("assetPositions", []):
                size = a.get("size", "0")
                out[a.get("assetId", "USDC")] = float(size)
        except Exception:
            out = {"USDC": 0.0}
        return out

    def _ensure_node(self):
        if not HAS_DYDX_SDK:
            raise ExchangeError("dydx-v4-client not installed")
        if not self._node:
            network = TESTNET if self.cfg.get("testnet") else MAINNET
            keypair = KeyPair.from_mnemonic(self.cfg.get("mnemonic", ""))
            self._wallet = Wallet(address=self.cfg.get("wallet_address", ""), keypair=keypair)
            self._node = NodeClient.from_network(network)

    async def create_order(self, symbol, side, otype, qty, price=None, post_only=False,
                           reduce_only=False, client_id=None):
        if not HAS_DYDX_SDK:
            raise ExchangeError("dydx-v4-client not installed")
        self._ensure_node()
        from dydx_v4_client import OrderParams, OrderType, OrderSide, MarketId, TimeInForce
        params = OrderParams(
            order_type=OrderType.LIMIT if otype == "limit" else OrderType.MARKET,
            size=qty if side == "buy" else -qty,
            price=price or 0,
            time_in_force=TimeInForce.GTT if otype == "limit" else TimeInForce.IOC,
            good_til_time_seconds=3600,
            reduce_only=reduce_only,
            post_only=post_only,
        )
        order_id = await asyncio.to_thread(
            self._node.place_order, self._wallet, MarketId(symbol), side == "buy", params)
        return {"exchange_oid": str(order_id.hash), "status": "open", "side": side, "type": otype,
                "price": price or 0, "qty": qty, "filled": 0, "avg_price": 0}

    async def cancel_order(self, order_id, symbol=None):
        self._ensure_node()
        from dydx_v4_client import OrderId
        await asyncio.to_thread(self._node.cancel_order, self._wallet,
                                OrderId.from_hash(int(order_id)))
        return {"ok": True}

    async def open_orders(self, symbol=None):
        return []

    async def close(self):
        if self._aiohttp:
            await self._aiohttp.close()
            self._aiohttp = None
