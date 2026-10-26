"""Start the bundled local Agent sidecar on an ephemeral loopback port."""
import asyncio
import json
import os
import socket
import sys

import uvicorn
from app.local_agent_app import app as local_app


async def _serve(sock: socket.socket) -> None:
    config = uvicorn.Config(
        "app.local_agent_app:app",
        host="127.0.0.1",
        port=sock.getsockname()[1],
        log_level="warning",
        access_log=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    local_app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started and not task.done():
        await asyncio.sleep(0.01)
    if task.done():
        await task
        return
    sys.stdout.write(json.dumps({
        "event": "ready",
        "host": "127.0.0.1",
        "port": sock.getsockname()[1],
    }, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    try:
        await task
    finally:
        local_app.state.request_shutdown = None


def main() -> None:
    if os.getenv("YINGXU_LOCAL_AGENT_MODE") != "1":
        raise SystemExit("desktop host must explicitly enable local Agent mode")
    token = os.getenv("YINGXU_LOCAL_AGENT_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("desktop host must provide a high-entropy local bearer token")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    try:
        asyncio.run(_serve(listener))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
