# local_proxy

Run **LM Studio** locally as an OpenAI-compatible API for **Cursor** and other IDEs — **no cloud server**, no public domain, no nginx.

`local_proxy` starts a local **HTTP(S) reverse proxy** on one machine. Your IDE talks to a fixed URL (e.g. `https://api.lmstudio.local:8443/v1`). An `/etc/hosts` entry maps that hostname to `127.0.0.1`; the proxy forwards requests to LM Studio. Streaming (SSE) is supported.

```
  Cursor / IDE
       │  HTTPS  https://api.lmstudio.local:8443/v1/…
       │  (/etc/hosts → 127.0.0.1)
       ▼
  local_proxy  ──HTTP──►  LM Studio
               (single process, one port)
```

Maintained by **[fexperts-dev](https://github.com/fexperts-dev)**.

---

## Documentation

| File | Description |
|---|---|
| **[INSTALLATION.md](INSTALLATION.md)** | Setup: `/etc/hosts`, config, TLS, Cursor, proxy bypass |
| **[local_proxy.env.example](local_proxy.env.example)** | Environment variable template |

---

## Quick start

**Prerequisite:** add to `/etc/hosts` (see [INSTALLATION.md §3](INSTALLATION.md#3-hosts-eintrag)):

```
127.0.0.1  api.lmstudio.local
```

```bash
git clone https://github.com/fexperts-dev/local_proxy.git
cd local_proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp local_proxy.env.example local_proxy.env   # optional
python -m local_proxy
```

After startup, copy **Base URL** and **API Key** from the console (or GUI) into Cursor — [INSTALLATION.md §9](INSTALLATION.md#9-cursor-konfigurieren).

On macOS: trust the self-signed cert in Keychain (§5.1) and add `api.lmstudio.local` to `http.noProxy` / `NO_PROXY` if you use a system proxy (§8–§9).

**GUI:**

```bash
python -m local_proxy --gui
```

LM Studio must be running (`http://localhost:1234` by default).

---

## CLI options

| Option | Description |
|---|---|
| `--domain NAME` | Local domain from `/etc/hosts` (default: `LOCAL_DOMAIN`) |
| `--port PORT` | Listen port (default: `8443` with TLS, `8088` without) |
| `--lmstudio-url URL` | LM Studio base URL |
| `--no-tls` | HTTP instead of HTTPS (not recommended for Cursor) |
| `--gui` | Desktop UI (tkinter) |

---

## Project layout

```
local_proxy/          # Application (proxy server, CLI, GUI)
tests/
deploy/               # Optional launchd / systemd examples
INSTALLATION.md
local_proxy.env.example
```

For **remote access from anywhere** (team, AWS, public domain), see the sibling project
[fexperts-dev/reverse_https](https://github.com/fexperts-dev/reverse_https).

---

## Tests

```bash
source .venv/bin/activate
python tests/test_local_proxy.py
```

Expected: `OK  local_proxy smoke test passed`

---

## License

MIT — see [LICENSE](LICENSE).
