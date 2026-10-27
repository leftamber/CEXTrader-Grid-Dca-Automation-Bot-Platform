import asyncio
import logging
import math
import time
import uuid

log = logging.getLogger("core")


def uid() -> str:
    return uuid.uuid4().hex[:12]


def now_ms() -> int:
    return int(time.time() * 1000)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def pct_change(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step + 1e-9) * step


def round_precision(value: float, precision: int, mode: str = "floor") -> float:
    if precision is None or precision < 0:
        return value
    q = 10 ** (-precision) if precision < 0 else 10 ** -precision
    if mode == "nearest" and q > 0:
        return round(value / q) * q
    return math.floor(value / q + 1e-9) * q


def decimals_from_step(step: float) -> int:
    if step >= 1:
        return 0
    d = 0
    s = step
    while d < 12 and abs(s - round(s)) > 1e-9:
        s *= 10
        d += 1
    return d


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    period_closes = closes[-(period + 1):]
    deltas = [period_closes[i + 1] - period_closes[i] for i in range(period)]
    for d in deltas:
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def geometric_levels(lower: float, upper: float, n: int) -> list[float]:
    if n <= 1:
        return [lower]
    ratio = (upper / lower) ** (1.0 / (n - 1)) if lower > 0 else 1.0
    return [lower * (ratio ** i) for i in range(n)]


def arithmetic_levels(lower: float, upper: float, n: int) -> list[float]:
    if n <= 1:
        return [lower]
    step = (upper - lower) / (n - 1)
    return [lower + step * i for i in range(n)]


def compute_levels(lower: float, upper: float, n: int, kind: str) -> list[float]:
    if kind == "geometric" and lower > 0:
        return geometric_levels(lower, upper, n)
    return arithmetic_levels(lower, upper, n)


def dca_safety_deviations(base_dev: float, count: int, scale: float) -> list[float]:
    return [base_dev * (scale ** i) for i in range(count)]


def true_ranges(candles: list) -> list[float]:
    trs = []
    for i in range(len(candles)):
        h, l = candles[i][2], candles[i][3]
        if i == 0:
            trs.append(h - l)
        else:
            pc = candles[i - 1][4]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = true_ranges(candles)[1:]
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


def adx_dmi(candles: list, period: int = 14) -> tuple:
    if len(candles) < period * 2 + 2:
        return 0.0, 0.0, 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i][2], candles[i][3]
        ph, pl = candles[i - 1][2], candles[i - 1][3]
        up, dn = h - ph, pl - l
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        plus_dm.append(pdm)
        minus_dm.append(mdm)
        pc = candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr_v = sum(trs[:period]) / period
    pdi = 100 * (sum(plus_dm[:period]) / period) / max(atr_v, 1e-9)
    mdi = 100 * (sum(minus_dm[:period]) / period) / max(atr_v, 1e-9)
    dxs = [abs(pdi - mdi) / max(pdi + mdi, 1e-9) * 100]
    for i in range(period, len(trs)):
        atr_v = (atr_v * (period - 1) + trs[i]) / period
        pdm_s = sum(plus_dm[i - period + 1:i + 1]) / period
        mdm_s = sum(minus_dm[i - period + 1:i + 1]) / period
        pdi_n = 100 * pdm_s / max(atr_v, 1e-9)
        mdi_n = 100 * mdm_s / max(atr_v, 1e-9)
        dxs.append(abs(pdi_n - mdi_n) / max(pdi_n + mdi_n, 1e-9) * 100)
    adx_v = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx_v = (adx_v * (period - 1) + dx) / period
    return adx_v, pdi, mdi


def bollinger_width_pct(closes: list, period: int = 20, k: float = 2.0) -> float:
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    sd = var ** 0.5
    if mean <= 0:
        return 0.0
    return (2 * k * sd) / mean * 100


def rolling_vwap(candles: list, window: int = 0) -> list:
    out = []
    pv, v = 0.0, 0.0
    for i, c in enumerate(candles):
        typical = (c[2] + c[3] + c[4]) / 3
        vol = c[5] or 1e-9
        pv += typical * vol
        v += vol
        if window and i >= window:
            old = candles[i - window]
            ot = (old[2] + old[3] + old[4]) / 3
            ov = old[5] or 1e-9
            pv -= ot * ov
            v -= ov
        out.append(pv / max(v, 1e-9))
    return out


class EventBus:
    def __init__(self):
        self._subs: dict = {}

    def on(self, topic: str, cb):
        self._subs.setdefault(topic, []).append(cb)
        return cb

    def off(self, topic: str, cb):
        subs = self._subs.get(topic, [])
        if cb in subs:
            subs.remove(cb)

    def publish(self, topic: str, payload: dict):
        for cb in list(self._subs.get(topic, [])) + list(self._subs.get("*", [])):
            try:
                res = cb(topic, payload)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception as e:
                log.exception("event handler error: %s", e)
