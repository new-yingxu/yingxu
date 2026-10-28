"""Start the bundled local Agent sidecar on an ephemeral loopback port."""
import asyncio
import json
import os
import socket
import sys
from typing import Any

import uvicorn
from app.local_agent_app import create_local_agent_app


async def _serve(sock: socket.socket, local_app: Any) -> None:
    config = uvicorn.Config(
        local_app,
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
    startup_line = sys.stdin.buffer.readline(8193)
    if not startup_line or len(startup_line) > 8192:
        raise SystemExit("desktop host must provide a bounded startup profile")
    try:
        profile = json.loads(startup_line)
        if not isinstance(profile, dict) or set(profile) - {"provider", "model", "apiKey"}:
            raise ValueError()
        provider, model = profile.get("provider"), profile.get("model")
        if provider not in {"ollama", "dashscope", "deepseek"} or not isinstance(model, str):
            raise ValueError()
        if provider == "ollama":
            if set(profile) != {"provider", "model"}:
                raise ValueError()
        elif set(profile) != {"provider", "model", "apiKey"} or not isinstance(profile["apiKey"], str):
            raise ValueError()
    except (ValueError, TypeError, json.JSONDecodeError):
        raise SystemExit("desktop host startup profile is invalid") from None
    local_app = create_local_agent_app(provider_profile=profile)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    try:
        asyncio.run(_serve(listener, local_app))
    finally:
        listener.close()


if __name__ == "__main__":
    main()
