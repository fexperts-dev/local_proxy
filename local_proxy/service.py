"""Runs tunnel server and client together on the local machine."""

from __future__ import annotations

import asyncio
import logging
import secrets
import ssl
from typing import Callable, Optional

from aiohttp import web

from tunnel.client import TunnelClient
from tunnel.server import build_app

from .certs import ensure_self_signed_cert
from .config import LocalProxyConfig

log = logging.getLogger("local_proxy")


def load_or_create_registration_secret(path) -> str:
    """Read the registration secret from *path* or generate and persist a new one."""
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    log.info("Created registration secret at %s", path)
    return value


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
        self._registration_secret = ""

    async def start(self) -> None:
        """Start the HTTP(S) server and embedded tunnel client."""
        self._registration_secret = load_or_create_registration_secret(
            self.config.registration_secret_path
        )

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
            registration_secret=self._registration_secret,
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

        self._client = TunnelClient(
            self.config.tunnel_url,
            self.config.lmstudio_url,
            client_id=self.config.client_id,
            registration_secret=self._registration_secret,
            public_api_base=self.config.public_base_url,
            session_file=str(self.config.session_file),
            on_registered=self.on_registered,
            insecure_ssl=self.config.use_tls,
        )
        self._client_task = asyncio.create_task(self._client.run_forever())

        for _ in range(50):
            if self.config.session_file.is_file():
                break
            await asyncio.sleep(0.1)

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
