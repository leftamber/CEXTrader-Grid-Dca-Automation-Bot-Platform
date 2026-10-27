import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from .routes import app_state


class WsHub:
    def __init__(self):
        self.clients: set = set()

    async def handler(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        s = app_state()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        def on_event(topic, payload):
            try:
                queue.put_nowait({"topic": topic, "payload": payload})
            except asyncio.QueueFull:
                pass

        subs = [s.bus.on(t, on_event) for t in ("bot_event", "log", "price", "equity", "orderbook")]
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    await ws.send_json({"topic": "ping", "payload": {"ts": 1}})
                    continue
                try:
                    await ws.send_json(msg)
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            self.clients.discard(ws)
            for t, cb in zip(("bot_event", "log", "price", "equity", "orderbook"), subs):
                s.bus.off(t, cb)

    async def broadcast(self, topic: str, payload: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json({"topic": topic, "payload": payload})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
