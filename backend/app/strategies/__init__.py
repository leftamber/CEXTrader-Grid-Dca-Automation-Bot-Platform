from .base import Strategy, BotContext
from .grid import GridStrategy
from .dca import DcaStrategy
from .combo import ComboStrategy
from .momentum import MomentumStrategy
from .vwap import VwapStrategy
from .adaptive import AdaptiveStrategy


def create_strategy(ctx: BotContext, config: dict) -> Strategy:
    t = config.get("type", "grid")
    if t == "grid":
        if not config.get("grid"):
            raise ValueError("grid config required")
        return GridStrategy(ctx, config["grid"])
    if t == "dca":
        if not config.get("dca"):
            raise ValueError("dca config required")
        return DcaStrategy(ctx, config["dca"])
    if t == "combo":
        if not config.get("grid") or not config.get("dca"):
            raise ValueError("combo requires grid and dca configs")
        return ComboStrategy(ctx, dict(config))
    if t == "momentum":
        if not config.get("momentum"):
            raise ValueError("momentum config required")
        return MomentumStrategy(ctx, config["momentum"])
    if t == "vwap":
        if not config.get("vwap"):
            raise ValueError("vwap config required")
        return VwapStrategy(ctx, config["vwap"])
    if t == "adaptive":
        if not config.get("adaptive"):
            raise ValueError("adaptive config required")
        return AdaptiveStrategy(ctx, dict(config))
    raise ValueError(f"unknown bot type {t}")
