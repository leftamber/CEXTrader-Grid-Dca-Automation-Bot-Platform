import asyncio
import sys
import socket
import webbrowser

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _pick_port() -> int:
    for port in (8000, 8001, 8002, 8080, 8090):
        if _port_free(port):
            return port
    return 8000


if __name__ == "__main__":
    port = _pick_port()
    url = f"http://127.0.0.1:{port}"
    print(f"\n  CEXTrader -> {url}\n")
    import threading
    threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False, loop="asyncio")