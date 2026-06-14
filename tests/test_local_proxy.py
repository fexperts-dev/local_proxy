"""Smoke test for local_proxy (server + client on one machine)."""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_proxy.config import LocalProxyConfig
from local_proxy.service import LocalProxy


async def fake_lmstudio(request):
    if request.path == "/v1/models":
        return web.json_response({"data": [{"id": "local-model", "object": "model"}]})
    return web.Response(status=404)


async def main():
    data_dir = Path("tests/.local_proxy-test")
    data_dir.mkdir(exist_ok=True)

    config = LocalProxyConfig(
        domain="test.local",
        port=18443,
        lmstudio_url="http://127.0.0.1:11236",
        data_dir=data_dir,
        client_id="test-client",
        log_level="WARNING",
        log_file=data_dir / "test.log",
        session_file=data_dir / "session.json",
        use_tls=False,
    )

    lm_app = web.Application()
    lm_app.router.add_route("*", "/{tail:.*}", fake_lmstudio)
    lm_runner = web.AppRunner(lm_app)
    await lm_runner.setup()
    await web.TCPSite(lm_runner, "127.0.0.1", 11236).start()

    session_payload: dict = {}

    def on_registered(payload: dict) -> None:
        session_payload.update(payload)

    proxy = LocalProxy(config, on_registered=on_registered)
    task = asyncio.create_task(proxy.run_forever())
    await asyncio.sleep(0.8)

    assert session_payload.get("proxy_token"), session_payload
    token = session_payload["proxy_token"]

    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:18443/healthz") as r:
            health = await r.json()
            assert health["client_connected"] is True

        async with session.get(
            "http://127.0.0.1:18443/v1/models",
            headers={"Authorization": f"Bearer {token}"},
        ) as r:
            data = await r.json()
            assert data["data"][0]["id"] == "local-model"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await lm_runner.cleanup()
    print("OK  local_proxy smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
