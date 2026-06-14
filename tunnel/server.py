"""Tunnel server — runs on the public AWS host.

Supports two modes:

1. **Legacy (single client):** static ``TUNNEL_TOKEN`` + ``PROXY_TOKEN``.
2. **Multi-client:** clients register on connect with fresh session tokens;
   the server routes ``/v1/...`` by ``Authorization: Bearer <session_proxy_token>``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import ssl
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiohttp import WSMsgType, web
from aiohttp.client_exceptions import ClientConnectionResetError

from . import protocol as proto

log = logging.getLogger("tunnel.server")

RESPONSE_START_TIMEOUT = 120.0
REGISTER_TIMEOUT = 15.0
_END = object()


class _PendingRequest:
    def __init__(self) -> None:
        self.start: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self.chunks: "asyncio.Queue" = asyncio.Queue()
        self.failed: Optional[str] = None

    def fail(self, message: str) -> None:
        self.failed = message
        if not self.start.done():
            self.start.set_exception(RuntimeError(message))
        self.chunks.put_nowait(_END)


@dataclass
class _ClientSession:
    client_id: str
    ws: web.WebSocketResponse
    proxy_token: str
    remote: str
    target: str = ""
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: Dict[str, _PendingRequest] = field(default_factory=dict)


class TunnelServer:
    """HTTP reverse proxy and WebSocket multiplexer for tunnel clients."""

    def __init__(
        self,
        registration_secret: Optional[str] = None,
        legacy_token: Optional[str] = None,
        legacy_proxy_token: Optional[str] = None,
        public_api_base: str = "",
    ) -> None:
        self.registration_secret = registration_secret
        self.legacy_token = legacy_token
        self.legacy_proxy_token = legacy_proxy_token
        self.public_api_base = public_api_base.rstrip("/")
        self.clients: Dict[str, _ClientSession] = {}
        self.proxy_index: Dict[str, str] = {}
        self.legacy_session: Optional[_ClientSession] = None

    @property
    def multi_client(self) -> bool:
        return bool(self.registration_secret)

    @staticmethod
    def _bearer_token(request: web.Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    @staticmethod
    def _is_v1_path(path: str) -> bool:
        return path == "/v1" or path.startswith("/v1/")

    def _token_matches(self, presented: Optional[str], expected: str) -> bool:
        if presented is None:
            return False
        return secrets.compare_digest(presented, expected)

    def _legacy_authorized(self, request: web.Request) -> bool:
        if not self.legacy_token:
            return False
        token = request.query.get("token")
        if token is None:
            token = self._bearer_token(request)
        if token is None:
            token = request.headers.get("X-Tunnel-Token")
        return self._token_matches(token, self.legacy_token)

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

    def _remove_client(self, client_id: str) -> None:
        session = self.clients.pop(client_id, None)
        if session is None:
            return
        self.proxy_index.pop(session.proxy_token, None)
        for pending in list(session.pending.values()):
            pending.fail("tunnel client disconnected")

    def _session_for_proxy(self, request: web.Request) -> Optional[_ClientSession]:
        bearer = self._bearer_token(request)
        if bearer:
            client_id = self.proxy_index.get(bearer)
            if client_id:
                session = self.clients.get(client_id)
                if session and not session.ws.closed:
                    return session
            if self.legacy_proxy_token and self._token_matches(bearer, self.legacy_proxy_token):
                if self.legacy_session and not self.legacy_session.ws.closed:
                    return self.legacy_session
        elif not self.legacy_proxy_token and len(self.clients) == 1:
            session = next(iter(self.clients.values()))
            if not session.ws.closed:
                return session
        return None

    async def _send(self, session: _ClientSession, message: dict) -> None:
        if session.ws.closed:
            raise RuntimeError("Tunnel client disconnected")
        async with session.send_lock:
            await session.ws.send_json(message)

    def _handle_client_message(self, session: _ClientSession, data: dict) -> None:
        t = data.get("t")
        if t == proto.PING:
            asyncio.ensure_future(self._send(session, {"t": proto.PONG}))
            return
        if t == proto.PONG:
            return

        req_id = data.get("id")
        pending = session.pending.get(req_id)
        if pending is None:
            return

        if t == proto.RES_START:
            if not pending.start.done():
                pending.start.set_result((data["status"], data["headers"]))
        elif t == proto.RES_CHUNK:
            pending.chunks.put_nowait(proto.b64decode(data["data_b64"]))
        elif t == proto.RES_END:
            pending.chunks.put_nowait(_END)
        elif t == proto.ERR:
            pending.fail(data.get("message", "remote error"))

    async def _serve_session(self, session: _ClientSession) -> None:
        ws = session.ws
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self._handle_client_message(session, msg.json())
                elif msg.type == WSMsgType.ERROR:
                    log.error("WebSocket error (%s): %s", session.client_id, ws.exception())
                    break
        finally:
            if session.client_id in self.clients and self.clients[session.client_id] is session:
                self._remove_client(session.client_id)
            if self.legacy_session is session:
                self.legacy_session = None
            log.info("Tunnel client disconnected: %s", session.client_id)

    async def _register_client(self, ws: web.WebSocketResponse, remote: str) -> None:
        client_id = ""
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=REGISTER_TIMEOUT)
            if msg.type != WSMsgType.TEXT:
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "expected register JSON"})
                await ws.close()
                return
            data = msg.json()
            if data.get("t") != proto.REGISTER:
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "first message must be register"})
                await ws.close()
                return

            client_id = str(data.get("client_id", "")).strip()
            if not client_id:
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "client_id required"})
                await ws.close()
                return

            if not self._token_matches(data.get("registration_secret"), self.registration_secret or ""):
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "invalid registration secret"})
                await ws.close()
                return

            proxy_token = str(data.get("session_proxy_token", "")).strip()
            if len(proxy_token) < 16:
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "session_proxy_token required"})
                await ws.close()
                return

            if client_id in self.clients:
                log.info("Replacing existing client session: %s", client_id)
                old = self.clients[client_id]
                await old.ws.close()
                self._remove_client(client_id)

            session = _ClientSession(
                client_id=client_id,
                ws=ws,
                proxy_token=proxy_token,
                remote=remote,
                target=str(data.get("target", "")),
            )
            self.clients[client_id] = session
            self.proxy_index[proxy_token] = client_id

            api_base = f"{self.public_api_base}/v1" if self.public_api_base else "/v1"
            await ws.send_json({
                "t": proto.REGISTERED,
                "client_id": client_id,
                "session_proxy_token": proxy_token,
                "api_base_url": api_base,
            })
            log.info("Client registered: %s from %s", client_id, remote)
            await self._serve_session(session)
        except asyncio.TimeoutError:
            log.warning("Registration timeout from %s", remote)
            try:
                await ws.send_json({"t": proto.REGISTER_ERR, "message": "registration timeout"})
                await ws.close()
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            log.exception("Registration failed for %s: %s", client_id or remote, exc)
            if client_id:
                self._remove_client(client_id)

    async def tunnel_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)

        if self.multi_client and not self._legacy_authorized(request):
            await self._register_client(ws, request.remote or "")
            return ws

        if not self._legacy_authorized(request):
            raise web.HTTPUnauthorized(text="invalid tunnel token\n")

        if self.legacy_session is not None and not self.legacy_session.ws.closed:
            log.warning("Replacing existing legacy tunnel client connection")
            await self.legacy_session.ws.close()

        session = _ClientSession(
            client_id="legacy",
            ws=ws,
            proxy_token=self.legacy_proxy_token or "",
            remote=request.remote or "",
        )
        self.legacy_session = session
        log.info("Legacy tunnel client connected from %s", request.remote)
        await self._serve_session(session)
        return ws

    def _any_client_connected(self) -> bool:
        if self.legacy_session is not None and not self.legacy_session.ws.closed:
            return True
        return any(not s.ws.closed for s in self.clients.values())

    async def proxy(self, request: web.Request) -> web.StreamResponse:
        if self._is_v1_path(request.path):
            session = self._session_for_proxy(request)
            if session is None:
                if not self._any_client_connected():
                    return web.json_response(
                        {"error": {"message": "Local tunnel client is not connected.",
                                   "type": "tunnel_unavailable"}},
                        status=503,
                    )
                return self._proxy_unauthorized_response()
        else:
            session = self.legacy_session
            if session is None or session.ws.closed:
                session = next(
                    (s for s in self.clients.values() if not s.ws.closed),
                    None,
                )
            if session is None:
                return web.json_response(
                    {"error": {"message": "Local tunnel client is not connected.",
                               "type": "tunnel_unavailable"}},
                    status=503,
                )

        req_id = uuid.uuid4().hex
        pending = _PendingRequest()
        session.pending[req_id] = pending

        body = await request.read()
        path = request.rel_url.raw_path_qs

        try:
            await self._send(session, {
                "t": proto.REQ,
                "id": req_id,
                "method": request.method,
                "path": path,
                "headers": proto.filter_headers(request.headers),
                "body_b64": proto.b64encode(body),
            })

            try:
                status, headers = await asyncio.wait_for(
                    pending.start, timeout=RESPONSE_START_TIMEOUT
                )
            except asyncio.TimeoutError:
                return web.json_response(
                    {"error": {"message": "Timed out waiting for local model.",
                               "type": "tunnel_timeout"}},
                    status=504,
                )

            response = web.StreamResponse(status=status)
            for k, v in proto.filter_headers(headers).items():
                response.headers[k] = v
            await response.prepare(request)

            while True:
                chunk = await pending.chunks.get()
                if chunk is _END:
                    break
                try:
                    await response.write(chunk)
                except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                    log.debug("Client disconnected during stream (request %s)", req_id)
                    break

            if pending.failed:
                log.warning("Request %s ended with error: %s", req_id, pending.failed)
            try:
                await response.write_eof()
            except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                log.debug("Client disconnected before stream end (request %s)", req_id)
            return response
        finally:
            session.pending.pop(req_id, None)

    async def health(self, request: web.Request) -> web.Response:
        active = [
            {
                "client_id": s.client_id,
                "connected": not s.ws.closed,
                "target": s.target or None,
            }
            for s in self.clients.values()
            if not s.ws.closed
        ]
        if self.legacy_session and not self.legacy_session.ws.closed:
            active.append({"client_id": "legacy", "connected": True, "target": None})

        return web.json_response({
            "status": "ok",
            "multi_client": self.multi_client,
            "client_connected": bool(active),
            "clients": active,
        })

    async def list_clients(self, request: web.Request) -> web.Response:
        if not self.multi_client:
            raise web.HTTPNotFound()
        secret = request.query.get("registration_secret", "")
        if not self._token_matches(secret, self.registration_secret or ""):
            raise web.HTTPUnauthorized(text="invalid registration secret\n")
        clients = [
            {
                "client_id": s.client_id,
                "connected": not s.ws.closed,
                "target": s.target or None,
            }
            for s in self.clients.values()
        ]
        return web.json_response({"clients": clients})


def build_app(
    token: Optional[str] = None,
    proxy_token: Optional[str] = None,
    registration_secret: Optional[str] = None,
    public_api_base: str = "",
) -> web.Application:
    """Create the aiohttp application (health, tunnel WS, HTTP proxy)."""
    app = web.Application(client_max_size=64 * 1024 * 1024)
    server = TunnelServer(registration_secret, token, proxy_token, public_api_base)
    app["server"] = server
    app.router.add_get("/healthz", server.health)
    app.router.add_get("/clients", server.list_clients)
    app.router.add_get("/_tunnel", server.tunnel_ws)
    app.router.add_route("*", "/{tail:.*}", server.proxy)
    return app


def main() -> None:
    """CLI entry point for ``python -m tunnel.server``."""
    parser = argparse.ArgumentParser(description="Reverse HTTPS tunnel server (AWS side)")
    parser.add_argument("--host", default=os.environ.get("TUNNEL_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("TUNNEL_PORT", "8188")))
    parser.add_argument("--token", default=os.environ.get("TUNNEL_TOKEN"),
                        help="Legacy shared secret for single-client mode.")
    parser.add_argument("--proxy-token", default=os.environ.get("PROXY_TOKEN"),
                        help="Legacy bearer token for /v1/... in single-client mode.")
    parser.add_argument(
        "--registration-secret",
        default=os.environ.get("TUNNEL_REGISTRATION_SECRET"),
        help="Enables multi-client mode; clients register with fresh session tokens.",
    )
    parser.add_argument(
        "--public-api-base",
        default=os.environ.get("TUNNEL_PUBLIC_API_BASE", ""),
        help="Public HTTPS base URL returned to clients (e.g. https://api.example.com).",
    )
    parser.add_argument("--certfile", default=os.environ.get("TUNNEL_CERTFILE"))
    parser.add_argument("--keyfile", default=os.environ.get("TUNNEL_KEYFILE"))
    parser.add_argument("--log-level", default=os.environ.get("TUNNEL_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.registration_secret and not args.token:
        parser.error("Set --registration-secret (multi-client) or --token (legacy).")

    ssl_ctx = None
    if args.certfile and args.keyfile:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(args.certfile, args.keyfile)

    app = build_app(
        args.token,
        args.proxy_token,
        args.registration_secret,
        args.public_api_base,
    )
    if args.registration_secret:
        log.info("Multi-client mode enabled (dynamic session tokens)")
    elif args.proxy_token:
        log.info("Legacy mode: bearer auth enabled for /v1/...")
    else:
        log.warning("Legacy mode: PROXY_TOKEN not set; /v1/... is publicly reachable")
    log.info("Listening on %s:%s (TLS=%s)", args.host, args.port, bool(ssl_ctx))
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx,
                access_log=None)


if __name__ == "__main__":
    main()
