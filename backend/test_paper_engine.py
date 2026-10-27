import asyncio
import sys

sys.path.insert(0, ".")

from app.exchanges.paper import PaperExchange
from app.exchanges.base import Ticker


class DeadPublic:
    """Stub that simulates an unreachable exchange."""

    async def load_markets(self):
        raise ConnectionError("network unreachable")

    async def ticker(self, symbol):
        raise ConnectionError("network unreachable")

    async def ohlcv(self, *a, **k):
        raise ConnectionError("network unreachable")


async def main():
    cfg = {"id": "acc_probe", "paper": True, "underlying": "bybit",
           "maker_fee_pct": 0.1, "taker_fee_pct": 0.1, "slippage_bps": 2,
           "initial_balances": {"USDT": 1000}}
    ad = PaperExchange(cfg)

    # 1) synthetic feed: no cached price, dead public -> random walk around seed
    ad._public = DeadPublic()
    t = await ad.ticker("BTC/USDT")
    assert 1000 < t.last < 200000, f"synthetic price sane: {t.last}"
    assert ad.feed_status == "synthetic", ad.feed_status
    p2 = (await ad.ticker("BTC/USDT")).last
    assert abs(p2 - t.last) / t.last < 0.01, "synthetic walk is small"
    print(f"OK synthetic feed: price {t.last:.2f} -> {p2:.2f}, status={ad.feed_status}")

    # 2) market buy fills immediately against synthetic price
    o = await ad.create_order("BTC/USDT", "buy", "market", 0.01, None)
    assert o["status"] == "filled", o["status"]
    assert o["filled"] == 0.01
    bal = await ad.fetch_balances()
    assert bal["USDT"] < 1000 and bal.get("BTC", 0) > 0, bal
    print(f"OK market buy: filled {o['filled']} @ {o['avg_price']:.2f}, USDT {bal['USDT']:.2f}, BTC {bal['BTC']:.6f}")

    # 3) post-only limit buy above the book is rejected
    try:
        await ad.create_order("BTC/USDT", "buy", "limit", 0.01, t.ask * 1.01, post_only=True)
        raise SystemExit("FAIL: post-only cross was accepted")
    except Exception as e:
        print(f"OK post-only reject: {str(e)[:60]}")

    # 4) limit buy below price -> open order -> sync_ticker fill on price drop
    limit_px = t.last * 0.99
    o2 = await ad.create_order("BTC/USDT", "buy", "limit", 0.004, limit_px)
    assert o2["status"] == "open", o2["status"]
    oo = await ad.open_orders("BTC/USDT")
    assert len(oo) == 1
    t2 = Ticker(symbol="BTC/USDT", bid=limit_px * 0.9995, ask=limit_px * 0.9999, last=limit_px, ts=0)
    ad.sync_ticker("BTC/USDT", t2)
    oo = await ad.open_orders("BTC/USDT")
    assert not oo, "limit buy should have filled on price crossing down"
    bal = await ad.fetch_balances()
    assert bal["BTC"] > 0.0139, bal
    print(f"OK limit buy fill: BTC now {bal['BTC']:.6f}, USDT {bal['USDT']:.2f}")

    # 5) limit sell of held base fills on price crossing up
    sell_px = t.last * 1.01
    o3 = await ad.create_order("BTC/USDT", "sell", "limit", 0.005, sell_px)
    assert o3["status"] == "open", o3["status"]
    t3 = Ticker(symbol="BTC/USDT", bid=sell_px * 1.0001, ask=sell_px * 1.0005, last=sell_px, ts=0)
    ad.sync_ticker("BTC/USDT", t3)
    oo = await ad.open_orders("BTC/USDT")
    assert not oo, "limit sell should have filled"
    bal = await ad.fetch_balances()
    assert bal["BTC"] < 0.0091, bal
    print(f"OK limit sell fill: BTC {bal['BTC']:.6f}, USDT {bal['USDT']:.2f}")

    # 6) market sell clamps to available balance
    o4 = await ad.create_order("BTC/USDT", "sell", "market", 999.0, None)
    assert o4["filled"] <= bal["BTC"] + 1e-9
    print(f"OK market sell clamp: sold {o4['filled']:.6f}")

    await ad.close()
    print("ALL PAPER ENGINE TESTS PASSED")


asyncio.run(main())