from ..core.utils import now_ms
from ..core.models import AdaptiveConfig, GridConfig, DcaConfig, MomentumConfig
from .grid import GridStrategy
from .dca import DcaStrategy
from .momentum import MomentumStrategy
from .regime import RegimeDetector
from .base import Strategy, BotContext


class AdaptiveStrategy(Strategy):
    name = "adaptive"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.cfg = AdaptiveConfig(**params.get("adaptive", {}))
        total = self.cfg.total_budget_quote
        grid_params = {**(params.get("grid") or {}), "total_budget_quote": total * self.cfg.grid_budget_pct / 100.0}
        dca_params = {**(params.get("dca") or {}), "total_budget_quote": total * self.cfg.dca_budget_pct / 100.0}
        mom_params = {**(params.get("momentum") or {}), "total_budget_quote": total * self.cfg.momentum_budget_pct / 100.0}
        gctx = _GatedContext(ctx, "grid")
        dctx = _GatedContext(ctx, "dca")
        mctx = _GatedContext(ctx, "momentum")
        self.grid = GridStrategy(gctx, grid_params)
        self.dca = DcaStrategy(dctx, dca_params)
        self.momentum = MomentumStrategy(mctx, mom_params)
        self.detector = RegimeDetector(self.cfg.adx_period, self.cfg.adx_trend_threshold,
                                       self.cfg.atr_period, self.cfg.atr_high_pct,
                                       self.cfg.bb_period)
        self.regime = {"regime": "unknown", "trend": "none", "adx": 0.0, "pdi": 0.0,
                       "mdi": 0.0, "atr_pct": 0.0, "bb_width": 0.0}
        self._last_regime_ts = 0

    async def _update_regime(self, candles):
        if not candles or len(candles) < 60:
            return
        self.regime = self.detector.detect(candles)

    def _grid_allowed(self) -> bool:
        if not self.cfg.enable_grid:
            return False
        r, t = self.regime["regime"], self.regime["trend"]
        if r == "volatile" and self.cfg.volatile_action == "stand_aside":
            return False
        if r == "trending" and self.cfg.in_trend_pause_grid:
            return False
        return r in ("ranging", "trending", "unknown")

    def _dca_allowed(self) -> bool:
        if not self.cfg.enable_dca:
            return False
        if self.regime["regime"] == "volatile" and self.cfg.volatile_action == "stand_aside":
            return False
        return True

    def _momentum_allowed(self) -> bool:
        if not self.cfg.enable_momentum:
            return False
        if self.regime["regime"] == "trending" and self.regime["trend"] == "up":
            return True
        if self.regime["regime"] == "trending" and self.regime["trend"] == "down" \
                and self.momentum.cfg.allow_short:
            return True
        return False

    async def start(self):
        if self.cfg.enable_grid:
            await self.grid.start()
        if self.cfg.enable_dca:
            await self.dca.start()
        if self.cfg.enable_momentum:
            await self.momentum.start()
        self.ctx.log(f"adaptive started: grid={self.cfg.enable_grid} dca={self.cfg.enable_dca} "
                     f"momentum={self.cfg.enable_momentum}, regime tf={self.cfg.regime_timeframe}")

    async def on_tick(self, price, candles):
        if now_ms() - self._last_regime_ts > 60000:
            regime_candles = await self.ctx.candles(self.cfg.regime_timeframe, 150)
            await self._update_regime(regime_candles)
            self._last_regime_ts = now_ms()
        if self.cfg.enable_grid and (self._grid_allowed() or self.grid.has_open_activity()):
            await self.grid.on_tick(price, candles)
        if self.cfg.enable_dca and (self._dca_allowed() or self.dca.cycle is not None):
            await self.dca.on_tick(price, candles)
        if self.cfg.enable_momentum and (self._momentum_allowed() or self.momentum.position_qty() > 0):
            await self.momentum.on_tick(price, candles)

    async def on_fill(self, fill):
        role = fill.get("role", "")
        if role.startswith("grid"):
            await self.grid.on_fill(fill)
        elif role.startswith("dca"):
            await self.dca.on_fill(fill)
        else:
            await self.momentum.on_fill(fill)

    async def stop(self, reason=""):
        await self.grid.stop(reason)
        await self.dca.stop(reason)
        await self.momentum.stop(reason)

    def actions(self) -> dict:
        out = {}
        if self.cfg.enable_grid:
            out.update({f"grid_{k}": v for k, v in self.grid.actions().items()})
        if self.cfg.enable_dca:
            out.update({f"dca_{k}": v for k, v in self.dca.actions().items()})
        if self.cfg.enable_momentum:
            out.update({f"momentum_{k}": v for k, v in self.momentum.actions().items()})
        return out

    def state(self) -> dict:
        return {"strategy": "adaptive", "regime": self.regime,
                "grid": self.grid.state() if self.cfg.enable_grid else None,
                "dca": self.dca.state() if self.cfg.enable_dca else None,
                "momentum": self.momentum.state() if self.cfg.enable_momentum else None,
                "realized_pnl": round(self.grid.realized_pnl + self.dca.realized_pnl +
                                      self.momentum.realized_pnl, 4)}


class _GatedContext(BotContext):
    def __init__(self, parent: BotContext, leg: str):
        self.__dict__["_parent"] = parent
        self._leg = leg

    def __getattr__(self, name):
        return getattr(self._parent, name)

    def log(self, message, level="info", data=None):
        self._parent.log(f"[{self._leg}] {message}", level, data)
