"""Command-line interface for the local combined proxy."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import load_config
from .logging_setup import setup_logging
from .service import LocalProxy

log = logging.getLogger("local_proxy.cli")


def _print_session(payload: dict) -> None:
    """Print IDE connection details after the proxy starts."""
    print()
    print("=== IDE configuration ===")
    print(f"Base URL:  {payload.get('api_base_url', '—')}")
    print(f"API Key:   {payload.get('proxy_token', '—')}")
    print()
    print("/etc/hosts entry (if not set yet):")
    print("  127.0.0.1  <your-domain>")
    print()


def main() -> None:
    """Parse CLI arguments, load config, and run the combined local proxy."""
    parser = argparse.ArgumentParser(
        description="Local proxy for LM Studio on this machine",
    )
    parser.add_argument(
        "--domain",
        help="Local domain from /etc/hosts (default: LOCAL_DOMAIN env)",
    )
    parser.add_argument("--port", type=int, help="Listen port (default: LOCAL_PORT env)")
    parser.add_argument(
        "--lmstudio-url",
        help="LM Studio URL (default: LMSTUDIO_URL env)",
    )
    parser.add_argument(
        "--no-tls",
        action="store_true",
        help="Use HTTP/WS instead of HTTPS/WSS (not recommended for Cursor)",
    )
    args = parser.parse_args()

    config = load_config()
    if args.domain:
        config.domain = args.domain
    if args.port:
        config.port = args.port
    if args.lmstudio_url:
        config.lmstudio_url = args.lmstudio_url
    if args.no_tls:
        config.use_tls = False

    setup_logging(config.log_level, str(config.log_file))

    proxy = LocalProxy(config, on_session_ready=_print_session)

    async def run() -> None:
        try:
            await proxy.run_forever()
        except asyncio.CancelledError:
            await proxy.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
