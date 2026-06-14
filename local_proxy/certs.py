"""Self-signed TLS certificates for the local domain."""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_self_signed_cert(domain: str, cert_dir: Path) -> tuple[Path, Path]:
    """Return ``(certfile, keyfile)``, creating a self-signed cert via OpenSSL if missing."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    certfile = cert_dir / f"{domain}.pem"
    keyfile = cert_dir / f"{domain}.key"
    if certfile.is_file() and keyfile.is_file():
        return certfile, keyfile

    subject = f"/CN={domain}"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(keyfile),
            "-out", str(certfile),
            "-days", "825",
            "-subj", subject,
        ],
        check=True,
        capture_output=True,
    )
    return certfile, keyfile
