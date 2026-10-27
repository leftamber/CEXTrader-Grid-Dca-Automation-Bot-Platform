from ..core.utils import adx_dmi, atr, bollinger_width_pct, ema


class RegimeDetector:
    def __init__(self, adx_period: int = 14, adx_threshold: float = 23.0,
                 atr_period: int = 14, atr_high_pct: float = 4.0,
                 bb_period: int = 20, min_candles: int = 60):
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_high_pct = atr_high_pct
        self.bb_period = bb_period
        self.min_candles = min_candles

    def detect(self, candles: list) -> dict:
        if not candles or len(candles) < self.min_candles:
            return {"regime": "unknown", "adx": 0.0, "pdi": 0.0, "mdi": 0.0,
                    "atr_pct": 0.0, "bb_width": 0.0, "trend": "none"}
        close = candles[-1][4]
        adx_v, pdi, mdi = adx_dmi(candles, self.adx_period)
        atr_v = atr(candles, self.atr_period)
        atr_pct = atr_v / close * 100 if close else 0.0
        closes = [c[4] for c in candles]
        bb_w = bollinger_width_pct(closes, self.bb_period)
        if atr_pct > self.atr_high_pct:
            regime = "volatile"
            trend = "none"
        elif adx_v >= self.adx_threshold:
            regime = "trending"
            trend = "up" if pdi > mdi else "down"
        else:
            regime = "ranging"
            trend = "none"
        return {"regime": regime, "trend": trend, "adx": round(adx_v, 1),
                "pdi": round(pdi, 1), "mdi": round(mdi, 1),
                "atr_pct": round(atr_pct, 2), "bb_width": round(bb_w, 2)}
