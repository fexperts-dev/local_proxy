"""HTTP reverse proxy from the IDE-facing server to LM Studio."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Optional

import aiohttp
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

log = logging.getLogger("local_proxy.proxy")

CHUNK_SIZE = 64 * 1024
PAYLOAD_LOG_MAX = 4000

HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    )
)


def filter_headers(headers) -> dict:
    """Drop hop-by-hop headers; keep everything else as a plain dict."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def format_payload(body: bytes | None, limit: int = PAYLOAD_LOG_MAX) -> str:
    """Format request/response body for logging (JSON pretty-print, truncated)."""
    if not body:
        return "(empty)"
    try:
        text = json.dumps(json.loads(body.decode("utf-8")), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = body.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "\n… (truncated)"
    return text


def payload_model(body: bytes | None) -> str | None:
    """Extract the ``model`` field from a JSON request body, if present."""
    if not body:
        return None
    try:
        value = json.loads(body.decode("utf-8")).get("model")
        return str(value) if value else None
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return None


class LocalProxyServer:
    """Forwards HTTP requests to LM Studio; protects ``/v1/...`` with a bearer token."""

    def __init__(self, target: str, proxy_token: str) -> None:
        self.target = target.rstrip("/")
        self.proxy_token = proxy_token
        self._http: Optional[aiohttp.ClientSession] = None

    async def _on_startup(self, _app: web.Application) -> None:
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15),
        )

    async def _on_cleanup(self, _app: web.Application) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    @staticmethod
    def _bearer_token(request: web.Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    @staticmethod
    def _is_v1_path(path: str) -> bool:
        return path == "/v1" or path.startswith("/v1/")

    def _proxy_unauthorized_response(self) -> web.Response:
        return web.json_response(
            {
                "error": {
                    "message": "Incorrect API key provided.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
            status=401,
        )

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "client_connected": True})

    async def proxy(self, request: web.Request) -> web.StreamResponse:
        if self._is_v1_path(request.path):
            bearer = self._bearer_token(request)
            if bearer is None or not secrets.compare_digest(bearer, self.proxy_token):
                return self._proxy_unauthorized_response()

        http = self._http
        if http is None:
            return web.json_response(
                {"error": {"message": "Proxy not ready.", "type": "proxy_unavailable"}},
                status=503,
            )

        url = self.target + request.rel_url.raw_path_qs
        method = request.method
        body = await request.read()

        log.info("Forwarding %s %s", method, request.path)
        model = payload_model(body)
        if model:
            log.info("Payload model=%s", model)
        if body and request.path.startswith("/v1"):
            log.info("Payload request %s %s:\n%s", method, request.path, format_payload(body))

        try:
            async with http.request(
                method,
                url,
                headers=filter_headers(request.headers),
                data=body or None,
                allow_redirects=False,
            ) as resp:
                response = web.StreamResponse(status=resp.status)
                for key, value in filter_headers(resp.headers).items():
                    response.headers[key] = value
                await response.prepare(request)

                log_response = request.path.startswith("/v1")
                response_preview = bytearray() if log_response else None
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if response_preview is not None and len(response_preview) < PAYLOAD_LOG_MAX:
                        response_preview.extend(chunk[: PAYLOAD_LOG_MAX - len(response_preview)])
                    try:
                        await response.write(chunk)
                    except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                        log.debug("Client disconnected during stream (%s)", request.path)
                        break

                log.info("%s %s -> %s", method, request.path, resp.status)
                if log_response:
                    if response_preview:
                        log.info(
                            "Payload response %s %s:\n%s",
                            resp.status,
                            request.path,
                            format_payload(bytes(response_preview)),
                        )
                    else:
                        log.info("Payload response %s %s: (empty)", resp.status, request.path)

                try:
                    await response.write_eof()
                except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                    log.debug("Client disconnected before stream end (%s)", request.path)
                return response
        except Exception as exc:  # noqa: BLE001
            log.error("Error forwarding %s %s: %s", method, request.path, exc)
            return web.json_response(
                {"error": {"message": str(exc), "type": "proxy_error"}},
                status=502,
            )


def build_app(target: str, proxy_token: str) -> web.Application:
    """Create the aiohttp application (health check and HTTP proxy)."""
    app = web.Application(client_max_size=64 * 1024 * 1024)
    server = LocalProxyServer(target, proxy_token)
    app.on_startup.append(server._on_startup)
    app.on_cleanup.append(server._on_cleanup)
    app.router.add_get("/healthz", server.health)
    app.router.add_route("*", "/{tail:.*}", server.proxy)
    return app
