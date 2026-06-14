"""Runs tunnel server and client together on the local machine."""

from __future__ import annotations

import asyncio
import logging
import secrets
import ssl
from typing import Callable, Optional

from aiohttp import web

from tunnel.client import TunnelClient, generate_session_token, write_session_file
from tunnel.server import build_app

from .certs import ensure_self_signed_cert
from .config import LocalProxyConfig

log = logging.getLogger("local_proxy")


class LocalProxy:
    """Combined tunnel server and client for local IDE access."""

    def __init__(
        self,
        config: LocalProxyConfig,
        on_registered: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.config = config
        self.on_registered = on_registered
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._client: Optional[TunnelClient] = None
        self._client_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the HTTP(S) server and embedded tunnel client."""
        tunnel_token = secrets.token_urlsafe(32)
        proxy_token = generate_session_token()

        ssl_context = None
        if self.config.use_tls:
            certfile, keyfile = ensure_self_signed_cert(
                self.config.domain,
                self.config.data_dir / "certs",
            )
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(certfile, keyfile)
            log.info("TLS enabled (%s)", certfile)

        app = build_app(
            token=tunnel_token,
            proxy_token=proxy_token,
            public_api_base=self.config.public_base_url,
        )
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            "0.0.0.0",
            self.config.port,
            ssl_context=ssl_context,
        )
        await self._site.start()
        log.info("Server listening on %s", self.config.public_base_url)

        payload = {
            "client_id": self.config.client_id,
            "proxy_token": proxy_token,
            "api_base_url": self.config.cursor_base_url,
            "target": self.config.lmstudio_url,
        }
        write_session_file(self.config.session_file, payload)
        log.info("Cursor API Key: %s", proxy_token)
        log.info("Session written to %s", self.config.session_file.resolve())
        if self.on_registered:
            self.on_registered(payload)

        self._client = TunnelClient(
            self.config.tunnel_url,
            self.config.lmstudio_url,
            token=tunnel_token,
            client_id=self.config.client_id,
            public_api_base=self.config.public_base_url,
            session_file=str(self.config.session_file),
            insecure_ssl=self.config.use_tls,
        )
        self._client_task = asyncio.create_task(self._client.run_forever())

        log.info("IDE Base URL: %s", self.config.cursor_base_url)
        log.info("Hosts entry required: 127.0.0.1 %s", self.config.domain)

    async def stop(self) -> None:
        """Cancel the tunnel client and tear down the HTTP(S) server."""
        if self._client_task is not None:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        log.info("Local proxy stopped")

    async def run_forever(self) -> None:
        """Start services and block until cancelled."""
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await self.stop()
            raise
