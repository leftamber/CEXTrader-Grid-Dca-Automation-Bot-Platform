from .base import ExchangeAdapter, MarketInfo, Ticker, OrderBook, ExchangeError, tf_seconds
from .paper import PaperExchange
from .ccxt_adapter import CcxtAdapter, PublicCcxtAdapter

SUPPORTED_EXCHANGES = [
    {"id": "paper", "name": "Paper Trading", "kind": "simulated", "orderbook": "full"},
    {"id": "binance", "name": "Binance", "kind": "ccxt", "orderbook": "full"},
    {"id": "okx", "name": "OKX", "kind": "ccxt", "orderbook": "full"},
    {"id": "bybit", "name": "Bybit", "kind": "ccxt", "orderbook": "full"},
    {"id": "bitget", "name": "Bitget", "kind": "ccxt", "orderbook": "full"},
    {"id": "coinbase", "name": "Coinbase", "kind": "ccxt", "orderbook": "full"},
    {"id": "kraken", "name": "Kraken", "kind": "ccxt", "orderbook": "full"},
    {"id": "gate", "name": "Gate.io", "kind": "ccxt", "orderbook": "full"},
    {"id": "kucoin", "name": "KuCoin", "kind": "ccxt", "orderbook": "full"},
    {"id": "mexc", "name": "MEXC", "kind": "ccxt", "orderbook": "full"},
    {"id": "htx", "name": "HTX (Huobi)", "kind": "ccxt", "orderbook": "full"},
    {"id": "hyperliquid", "name": "Hyperliquid", "kind": "native-sdk/ccxt", "orderbook": "full"},
    {"id": "dydx", "name": "dYdX v4", "kind": "native-sdk", "orderbook": "full"},
]


def create_adapter(account_cfg: dict, bus=None, storage=None) -> ExchangeAdapter:
    ex = account_cfg.get("exchange", "paper")
    if ex == "paper" or account_cfg.get("paper"):
        return PaperExchange(account_cfg, bus=bus, storage=storage)
    if ex == "hyperliquid":
        from .hyperliquid import HyperliquidAdapter, HAS_HL_SDK
        if account_cfg.get("api_key") and HAS_HL_SDK and account_cfg.get("use_native_sdk", True):
            return HyperliquidAdapter(account_cfg)
        return CcxtAdapter({**account_cfg, "exchange": "hyperliquid"})
    if ex == "dydx":
        from .dydx import DydxAdapter
        return DydxAdapter(account_cfg)
    return CcxtAdapter(account_cfg)


def create_public_adapter(exchange_id: str, perp: bool = False) -> PublicCcxtAdapter:
    return PublicCcxtAdapter({"exchange": exchange_id, "api_key": "", "market_type": "perp" if perp else "spot"})
