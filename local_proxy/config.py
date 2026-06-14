"""Configuration for the local combined proxy."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from *path* into os.environ (setdefault only)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class LocalProxyConfig:
    """Runtime settings for the combined tunnel server and client."""

    domain: str
    port: int
    lmstudio_url: str
    data_dir: Path
    client_id: str
    log_level: str
    log_file: Path
    session_file: Path
    use_tls: bool

    @property
    def public_base_url(self) -> str:
        """Public HTTP(S) origin exposed to the IDE (includes port)."""
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.domain}:{self.port}"

    @property
    def tunnel_url(self) -> str:
        """WebSocket URL for the embedded tunnel client (loopback)."""
        scheme = "wss" if self.use_tls else "ws"
        return f"{scheme}://127.0.0.1:{self.port}/_tunnel"

    @property
    def cursor_base_url(self) -> str:
        """OpenAI-compatible API base URL for Cursor and similar IDEs."""
        return f"{self.public_base_url}/v1"


def load_config() -> LocalProxyConfig:
    """Build config from ``local_proxy.env``, ``.env``, and environment variables."""
    for name in ("local_proxy.env", ".env"):
        _load_dotenv(Path(name))

    data_dir = Path(os.environ.get("LOCAL_PROXY_DATA_DIR", "~/.local_proxy")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    use_tls = os.environ.get("LOCAL_PROXY_USE_TLS", "true").lower() in ("1", "true", "yes")
    port_default = "8443" if use_tls else "8088"

    return LocalProxyConfig(
        domain=os.environ.get("LOCAL_DOMAIN", "api.lmstudio.local"),
        port=int(os.environ.get("LOCAL_PORT", port_default)),
        lmstudio_url=os.environ.get("LMSTUDIO_URL", "http://localhost:1234"),
        data_dir=data_dir,
        client_id=os.environ.get("CLIENT_ID", socket.gethostname()),
        log_level=os.environ.get("LOCAL_PROXY_LOG_LEVEL", "INFO"),
        log_file=Path(os.environ.get("LOCAL_PROXY_LOG", data_dir / "local_proxy.log")),
        session_file=Path(os.environ.get("LOCAL_PROXY_SESSION_FILE", data_dir / "session.json")),
        use_tls=use_tls,
    )
