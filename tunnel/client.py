"""Tunnel client — runs on your local machine next to LM Studio.

Opens an outbound WebSocket to the AWS tunnel server and forwards proxied HTTP
requests to LM Studio.

**Legacy mode:** static ``TUNNEL_TOKEN`` in the WebSocket URL.

**Multi-client mode:** on each start fresh session tokens are generated and
exchanged with the server via a ``register`` handshake. Cursor uses the new
``session_proxy_token`` from ``session.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import socket
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import WSMsgType

from . import protocol as proto

log = logging.getLogger("tunnel.client")

CHUNK_SIZE = 64 * 1024
PAYLOAD_LOG_MAX = 4000


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


def generate_session_token() -> str:
    """Return a URL-safe random token for a tunnel session."""
    return secrets.token_urlsafe(32)


def default_client_id() -> str:
    """Stable client identifier from ``CLIENT_ID`` or the host name."""
    return os.environ.get("CLIENT_ID") or socket.gethostname()


def write_session_file(path: Path, payload: dict) -> None:
    """Write IDE session metadata (API key, base URL) to *path* as JSON."""
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class TunnelClient:
    """Outbound WebSocket client that forwards proxied HTTP to LM Studio."""

    def __init__(
        self,
        server_url: str,
        target: str,
        *,
        token: str = "",
        client_id: str = "",
        registration_secret: str = "",
        session_file: str = "session.json",
        public_api_base: str = "",
        on_registered: Optional[Callable[[dict], None]] = None,
        insecure_ssl: bool = False,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        if not self.server_url.endswith("/_tunnel"):
            if "/_tunnel" not in self.server_url:
                self.server_url = self.server_url + "/_tunnel"
        self.target = target.rstrip("/")
        self.token = token
        self.client_id = client_id or default_client_id()
        self.registration_secret = registration_secret
        self.session_file = Path(session_file)
        self.public_api_base = public_api_base
        self.on_registered = on_registered
        self.insecure_ssl = insecure_ssl
        self.multi_client = bool(registration_secret)
        self.session_proxy_token = ""
        self._ws: "aiohttp.ClientWebSocketResponse | None" = None
        self._send_lock = asyncio.Lock()

    def _connect_url(self) -> str:
        if self.multi_client:
            return self.server_url
        return self._with_token(self.server_url, self.token)

    @staticmethod
    def _with_token(url: str, token: str) -> str:
        parts = urlsplit(url)
        query = parts.query
        if "token=" not in query:
            query = (query + "&" if query else "") + f"token={token}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    async def run_forever(self) -> None:
        """Connect to the tunnel server and reconnect with exponential backoff."""
        backoff = 1.0
        while True:
            try:
                await self._connect_and_serve()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Connection lost (%s); retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _register(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.session_proxy_token = generate_session_token()
        session_tunnel_token = generate_session_token()
        await ws.send_json({
            "t": proto.REGISTER,
            "client_id": self.client_id,
            "registration_secret": self.registration_secret,
            "session_tunnel_token": session_tunnel_token,
            "session_proxy_token": self.session_proxy_token,
            "target": self.target,
        })
        msg = await ws.receive()
        if msg.type != WSMsgType.TEXT:
            raise ConnectionError("expected registration response")
        data = msg.json()
        if data.get("t") == proto.REGISTER_ERR:
            raise ConnectionError(data.get("message", "registration rejected"))
        if data.get("t") != proto.REGISTERED:
            raise ConnectionError(f"unexpected registration response: {data.get('t')}")

        payload = {
            "client_id": data.get("client_id", self.client_id),
            "proxy_token": data.get("session_proxy_token", self.session_proxy_token),
            "api_base_url": data.get("api_base_url") or f"{self.public_api_base.rstrip('/')}/v1",
            "target": self.target,
        }
        write_session_file(self.session_file, payload)
        log.info("Registered as %s", payload["client_id"])
        log.info("Cursor API Key (session): %s", payload["proxy_token"])
        log.info("Cursor Base URL: %s", payload["api_base_url"])
        log.info("Session written to %s", self.session_file.resolve())
        if self.on_registered:
            self.on_registered(payload)

    async def _connect_and_serve(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
        connect_url = self._connect_url()
        safe_url = connect_url.split("token=")[0] + ("token=***" if "token=" in connect_url else "")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            log.info("Connecting to %s", safe_url)
            async with session.ws_connect(
                connect_url,
                heartbeat=30,
                max_msg_size=64 * 1024 * 1024,
                ssl=False if self.insecure_ssl else True,
            ) as ws:
                self._ws = ws
                if self.multi_client:
                    await self._register(ws)
                log.info("Tunnel established. Forwarding to %s", self.target)
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = msg.json()
                        if data.get("t") == proto.REQ:
                            asyncio.ensure_future(self._handle_request(session, data))
                        elif data.get("t") == proto.PING:
                            await self._send({"t": proto.PONG})
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
        self._ws = None
        raise ConnectionError("WebSocket closed")

    async def _send(self, message: dict) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("Tunnel not connected")
        async with self._send_lock:
            await ws.send_json(message)

    async def _handle_request(self, session: aiohttp.ClientSession, data: dict) -> None:
        req_id = data["id"]
        url = self.target + data["path"]
        method = data["method"]
        headers = data.get("headers", {})
        body = proto.b64decode(data.get("body_b64", "")) if data.get("body_b64") else None

        log.info("Forwarding %s %s", method, data["path"])
        model = payload_model(body)
        if model:
            log.info("Payload model=%s", model)
        if body and data["path"].startswith("/v1"):
            log.info("Payload request %s %s:\n%s", method, data["path"], format_payload(body))
        try:
            async with session.request(
                method, url, headers=headers, data=body,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15),
                allow_redirects=False,
            ) as resp:
                await self._send({
                    "t": proto.RES_START,
                    "id": req_id,
                    "status": resp.status,
                    "headers": proto.filter_headers(resp.headers),
                })
                log_response = data["path"].startswith("/v1")
                response_preview = bytearray() if log_response else None
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if response_preview is not None and len(response_preview) < PAYLOAD_LOG_MAX:
                        response_preview.extend(chunk[: PAYLOAD_LOG_MAX - len(response_preview)])
                    await self._send({
                        "t": proto.RES_CHUNK,
                        "id": req_id,
                        "data_b64": proto.b64encode(chunk),
                    })
                await self._send({"t": proto.RES_END, "id": req_id})
                log.info("%s %s -> %s", method, data["path"], resp.status)
                if log_response:
                    if response_preview:
                        log.info(
                            "Payload response %s %s:\n%s",
                            resp.status,
                            data["path"],
                            format_payload(bytes(response_preview)),
                        )
                    else:
                        log.info("Payload response %s %s: (empty)", resp.status, data["path"])
        except Exception as exc:  # noqa: BLE001
            log.error("Error forwarding %s %s: %s", method, data["path"], exc)
            try:
                await self._send({"t": proto.ERR, "id": req_id, "message": str(exc)})
            except Exception:
                pass


def setup_logging(
    level: str,
    log_file: str | None,
    *,
    to_console: bool = True,
    to_file: bool = True,
    extra_handlers: list[logging.Handler] | None = None,
    enabled_filter: logging.Filter | None = None,
) -> None:
    """Configure root logging for the tunnel client (console, file, optional handlers)."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handlers: list[logging.Handler] = []
    if to_console:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        handlers.append(stream)
    if to_file and log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)
    if extra_handlers:
        for handler in extra_handlers:
            handler.setFormatter(fmt)
            handlers.append(handler)

    for handler in handlers:
        if enabled_filter is not None:
            handler.addFilter(enabled_filter)
        root.addHandler(handler)

    if to_file and log_file:
        log.info("Logging to %s", log_file)


def main() -> None:
    """CLI entry point for ``python -m tunnel.client``."""
    parser = argparse.ArgumentParser(description="Reverse HTTPS tunnel client (local side)")
    parser.add_argument("--server-url", default=os.environ.get("TUNNEL_SERVER_URL"),
                        help="WebSocket URL of the AWS server, e.g. wss://api.example.com/_tunnel")
    parser.add_argument("--token", default=os.environ.get("TUNNEL_TOKEN"),
                        help="Legacy shared secret (single-client mode).")
    parser.add_argument("--client-id", default=os.environ.get("CLIENT_ID"),
                        help="Stable client id for multi-client mode (default: hostname).")
    parser.add_argument(
        "--registration-secret",
        default=os.environ.get("TUNNEL_REGISTRATION_SECRET"),
        help="Shared secret for multi-client registration (must match server).",
    )
    parser.add_argument(
        "--public-api-base",
        default=os.environ.get("TUNNEL_PUBLIC_API_BASE", ""),
        help="Public HTTPS base returned in session.json for Cursor.",
    )
    parser.add_argument("--target", default=os.environ.get("LMSTUDIO_URL", "http://localhost:1234"),
                        help="LM Studio base URL (default: http://localhost:1234).")
    parser.add_argument("--log-level", default=os.environ.get("TUNNEL_LOG_LEVEL", "INFO"))
    parser.add_argument(
        "--log-file",
        default=os.environ.get("TUNNEL_CLIENT_LOG", "client.log"),
        help="Log file path (default: client.log or TUNNEL_CLIENT_LOG).",
    )
    parser.add_argument(
        "--session-file",
        default=os.environ.get("TUNNEL_SESSION_FILE", "session.json"),
        help="Where to write the current session tokens for Cursor.",
    )
    args = parser.parse_args()

    setup_logging(args.log_level, args.log_file)

    if not args.server_url:
        parser.error("A server URL is required (set --server-url or TUNNEL_SERVER_URL).")
    if not args.registration_secret and not args.token:
        parser.error("Set --registration-secret (multi-client) or --token (legacy).")

    client = TunnelClient(
        args.server_url,
        args.target,
        token=args.token or "",
        client_id=args.client_id or "",
        registration_secret=args.registration_secret or "",
        session_file=args.session_file,
        public_api_base=args.public_api_base,
    )
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
