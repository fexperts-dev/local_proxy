"""Runs the local HTTP proxy for IDE access to LM Studio."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import ssl
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

from .certs import ensure_self_signed_cert
from .config import LocalProxyConfig
from .proxy import build_app

log = logging.getLogger("local_proxy")


def write_session_file(path: Path, payload: dict) -> None:
    """Write IDE session metadata (API key, base URL) to *path* as JSON."""
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class LocalProxy:
    """HTTPS proxy that exposes LM Studio to IDEs via a local domain."""

    def __init__(
        self,
        config: LocalProxyConfig,
        on_session_ready: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.config = config
        self.on_session_ready = on_session_ready
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._proxy_token = ""

    async def start(self) -> None:
        """Start the HTTP(S) proxy and write session details for the IDE."""
        self._proxy_token = secrets.token_urlsafe(32)
        payload = {
            "proxy_token": self._proxy_token,
            "api_base_url": self.config.cursor_base_url,
            "target": self.config.lmstudio_url,
        }
        write_session_file(self.config.session_file, payload)
        log.info("Session written to %s", self.config.session_file)
        if self.on_session_ready:
            self.on_session_ready(payload)

        ssl_context = None
        if self.config.use_tls:
            certfile, keyfile = ensure_self_signed_cert(
                self.config.domain,
                self.config.data_dir / "certs",
            )
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(certfile, keyfile)
            log.info("TLS enabled (%s)", certfile)

        app = build_app(self.config.lmstudio_url, self._proxy_token)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            "0.0.0.0",
            self.config.port,
            ssl_context=ssl_context,
        )
        await self._site.start()
        log.info("Listening on %s", self.config.public_base_url)
        log.info("IDE Base URL: %s", self.config.cursor_base_url)
        log.info("Hosts entry required: 127.0.0.1 %s", self.config.domain)

    async def stop(self) -> None:
        """Tear down the HTTP(S) server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        log.info("Local proxy stopped")

    async def run_forever(self) -> None:
        """Start the proxy and block until cancelled."""
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await self.stop()
            raise
