"""Wire protocol shared between tunnel server and tunnel client.

A single WebSocket connection multiplexes many concurrent HTTP requests.
Every message is JSON and carries a per-request ``id`` so that frames from
different in-flight requests can be interleaved safely.

Server -> Client
    {"t": "req",        "id", "method", "path", "headers", "body_b64"}

Client -> Server
    {"t": "res_start",  "id", "status", "headers"}
    {"t": "res_chunk",  "id", "data_b64"}
    {"t": "res_end",    "id"}
    {"t": "err",        "id", "message"}

Either direction
    {"t": "ping"} / {"t": "pong"}   (application level keep-alive)

Multi-client registration (client -> server, first message after connect)
    {"t": "register", "client_id", "registration_secret",
     "session_tunnel_token", "session_proxy_token", "target"}

Server -> Client
    {"t": "registered", "client_id", "session_proxy_token", "api_base_url"}
    {"t": "register_err", "message"}
"""

from __future__ import annotations

import base64

# Message type tags
REQ = "req"
RES_START = "res_start"
RES_CHUNK = "res_chunk"
RES_END = "res_end"
ERR = "err"
PING = "ping"
PONG = "pong"
REGISTER = "register"
REGISTERED = "registered"
REGISTER_ERR = "register_err"

# Hop-by-hop headers must not be forwarded across the tunnel (RFC 7230 §6.1).
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


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def filter_headers(headers) -> dict:
    """Drop hop-by-hop headers; keep everything else as a plain dict."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}
