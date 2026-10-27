from dataclasses import dataclass, field
from typing import Optional

# Preference order used when a market-data feed is unreachable.
FEED_FALLBACK = ["binance", "bybit", "okx", "gate", "kucoin", "mexc", "bitget", "htx"]
# Exchanges we never fall back to (need credentials or special SDKs).
NON_FALLBACK = {"paper", "hyperliquid", "dydx"}


@dataclass
class MarketInfo:
    symbol: str
    base: str
    quote: str
    price_step: float = 0.01
    price_precision: int = 2
    amount_step: float = 0.00001
    amount_precision: int = 5
    min_amount: float = 0.0
    min_cost: float = 0.0
    maker_fee_pct: float = 0.1
    taker_fee_pct: float = 0.1
    spot: bool = True
    contract_size: float = 1.0
    max_leverage: float = 1.0
    supports_post_only: bool = True
    supports_amend: bool = True


@dataclass
class Ticker:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    ts: int = 0

    def mid(self) -> float:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.last


@dataclass
class OrderBook:
    symbol: str
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    ts: int = 0

    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0


class ExchangeError(Exception):
    pass


class ExchangeAdapter:
    id: str = "base"
    name: str = "Base"

    def __init__(self, account_cfg: dict):
        self.cfg = account_cfg
        self._markets: dict = {}

    async def load_markets(self):
        raise NotImplementedError

    def market(self, symbol: str) -> MarketInfo:
        if symbol not in self._markets:
            raise ExchangeError(f"unknown market {symbol}")
        return self._markets[symbol]

    def symbols(self) -> list:
        return list(self._markets.keys())

    async def ticker(self, symbol: str) -> Ticker:
        raise NotImplementedError

    async def ohlcv(self, symbol: str, timeframe: str, limit: int = 300, since: int = 0) -> list:
        raise NotImplementedError

    async def orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        raise NotImplementedError

    async def balances(self) -> dict:
        raise NotImplementedError

    async def fetch_balances(self) -> dict:
        return await self.balances()

    async def positions(self) -> list:
        return []

    async def create_order(self, symbol: str, side: str, otype: str, qty: float, price: float = None,
                           post_only: bool = False, reduce_only: bool = False, client_id: str = None) -> dict:
        raise NotImplementedError

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        raise NotImplementedError

    async def amend_order(self, order_id: str, symbol: str, price: float = None, qty: float = None) -> dict:
        raise NotImplementedError

    async def batch_create(self, orders: list) -> list:
        out = []
        for o in orders:
            try:
                out.append(await self.create_order(**o))
            except Exception as e:
                out.append({"error": str(e)})
        return out

    async def batch_cancel(self, items: list) -> list:
        out = []
        for it in items:
            try:
                out.append(await self.cancel_order(it["order_id"], it["symbol"]))
            except Exception as e:
                out.append({"error": str(e)})
        return out

    async def open_orders(self, symbol: str = None) -> list:
        raise NotImplementedError

    async def my_trades(self, symbol: str, since: int = 0) -> list:
        return []

    async def close(self):
        pass

    @property
    def is_paper(self) -> bool:
        return False


def tf_seconds(tf: str) -> int:
    n = int(tf[:-1]) if tf[:-1].isdigit() else 1
    unit = tf[-1]
    return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 2592000}.get(unit, 60)
