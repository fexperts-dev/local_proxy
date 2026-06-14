# Medium article (draft for paste into Medium)

**Suggested title:**  
*Cursor with local LM Studio — no cloud, no AWS: why I built `local_proxy`*

**Subtitle:**  
*A small open-source tool closes the gap between “the model runs on my Mac” and “Cursor accepts the connection.”*

---

You installed LM Studio, loaded a model, started the local server — and Cursor still says: *“We're having trouble connecting to the model provider.”*

Sound familiar? Same here. That’s exactly why I built **`local_proxy`**.

---

## Why this tool exists

I wanted to use **Cursor** (and similar IDEs) with **my own locally running models** — privately, without burning subscription tokens, without sending code to the cloud. LM Studio exposes an OpenAI-compatible API at `http://localhost:1234`. Sounds trivial. It isn’t.

### What goes wrong — even when “the network” isn’t the issue

1. **Cursor expects HTTPS and a proper URL**  
   A bare `http://127.0.0.1:1234/v1` often isn’t enough. Custom base URLs should end with `/v1`, bearer auth must match, and streaming (SSE) has to work.

2. **A reverse tunnel through AWS is overkill**  
   My first setup used a **public server** (nginx, Let’s Encrypt, WebSocket tunnel) — great when you need access from anywhere or as a team. For **one machine only**, that’s unnecessary complexity plus cost and maintenance.

3. **TLS needs a hostname, not just localhost**  
   Cursor and Electron handle certificates differently than `curl`. A local domain via `/etc/hosts` plus a self-signed cert trusted in the keychain solves this cleanly.

4. **VPN and corporate proxies swallow “local” hosts**  
   If `NO_PROXY` only lists `127.0.0.1,localhost` but your API lives at `api.lmstudio.local`, traffic still goes through the proxy — and you get **403** or mysterious verify failures.

5. **Everything in one process**  
   Instead of a server on AWS and a client on the laptop, I wanted **one command** on **one machine**: start, copy base URL and API key, paste into Cursor, done.

`local_proxy` is the answer to those points. It’s open source at [github.com/fexperts-dev/local_proxy](https://github.com/fexperts-dev/local_proxy) (MIT).

---

## What `local_proxy` does

On **your machine**, `local_proxy` starts a local **HTTPS reverse proxy** that forwards OpenAI-compatible requests to **LM Studio**.

All in **one Python process**, one port (default **8443** with TLS).

```
  Cursor
       │  HTTPS  https://api.lmstudio.local:8443/v1/…
       │  (/etc/hosts → 127.0.0.1)
       ▼
  local_proxy  ──HTTP──►  LM Studio :1234
```

You need **no AWS instance**, **no public domain**, **no nginx**, **no WebSocket tunnel**. Wi‑Fi isn’t required for inference — everything stays on localhost.

On startup, `local_proxy` generates a session API key and writes it to `~/.local_proxy/session.json` for easy copy-paste into Cursor.

There’s also an optional **desktop GUI** (`python -m local_proxy --gui`) with copyable fields for base URL, API key, and model IDs.

---

## Install in five minutes

### 1. Prerequisites

- Python **3.9+**
- **LM Studio** with the local server running (port **1234**)
- **OpenSSL** (for TLS; usually already installed)
- An entry in **`/etc/hosts`**:

```
127.0.0.1  api.lmstudio.local
```

### 2. Clone and run

```bash
git clone https://github.com/fexperts-dev/local_proxy.git
cd local_proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp local_proxy.env.example local_proxy.env   # optional
python -m local_proxy
```

After startup, the console shows:

```
=== IDE configuration ===
Base URL:  https://api.lmstudio.local:8443/v1
API Key:   <proxy_token>
```

The same values are in `~/.local_proxy/session.json`.

> **Note:** The API key is **regenerated on every restart** of `local_proxy`. Update Cursor after a restart.

### 3. Configure Cursor

1. **Settings → Models**
2. **OpenAI API Key:** value from `proxy_token` / console / GUI
3. **Override OpenAI Base URL:** `https://api.lmstudio.local:8443/v1`  
   (include `/v1`, **no** trailing slash)
4. Model name **exactly** as in LM Studio (`/v1/models`)
5. Click **Verify**

---

## macOS: two gotchas (and how to fix them)

### TLS certificate in Keychain

On first run, `local_proxy` creates a self-signed cert under `~/.local_proxy/certs/`. Import it and set **Always Trust**:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  ~/.local_proxy/certs/api.lmstudio.local.pem
```

Test without `-k`:

```bash
curl --noproxy '*' https://api.lmstudio.local:8443/healthz
```

Expected: `"client_connected": true`.

### System proxy / VPN

In Cursor **`settings.json`** (Cmd+Shift+P → *Open User Settings JSON*):

```json
{
  "http.proxySupport": "on",
  "http.noProxy": [
    "127.0.0.1",
    "localhost",
    "::1",
    "api.lmstudio.local",
    ".lmstudio.local"
  ]
}
```

Also in `~/.zshrc`:

```bash
export NO_PROXY="127.0.0.1,localhost,api.lmstudio.local,.lmstudio.local"
export no_proxy="$NO_PROXY"
```

Then **fully quit** Cursor and reopen it.

---

## When to use `local_proxy` vs. remote access

| Scenario | Recommendation |
|---|---|
| **This machine only**, LM Studio local | **`local_proxy`** |
| Access from **anywhere** / team over the internet | **[reverse_https](https://github.com/fexperts-dev/reverse_https)** (AWS + nginx) |
| Multiple machines on a **LAN** | Gateway with **reverse_https** on a shared host |

`local_proxy` is the **local shortcut** — a direct HTTP proxy with no remote infrastructure.

---

## What I learned

- **Local doesn’t mean simple.** IDEs expect HTTPS, hostnames, and specific response shapes (`content` vs. `reasoning_content` on some models).
- **Start debugging with `curl --noproxy '*'`** — separates proxy issues from Cursor issues.
- **Small tools beat big architecture** when the use case is small: one developer, one laptop, one model.

The full guide (launchd, systemd, troubleshooting) is in the repo: [INSTALLATION.md](https://github.com/fexperts-dev/local_proxy/blob/main/INSTALLATION.md).

---

## Get involved

- **GitHub:** [github.com/fexperts-dev/local_proxy](https://github.com/fexperts-dev/local_proxy)  
- **Issues & PRs** welcome  
- License: **MIT**

If this saves you time and frustration, I’d love feedback — or a coffee:

**☕ [Buy me a coffee](https://buymeacoffee.com/normannatzke)**

---

*Norman Nattke / [fexperts-dev](https://github.com/fexperts-dev)*

---

## Notes for Medium

- Paste **code blocks** as Medium “Code” blocks (not images).
- **Diagram:** the ASCII diagram above works as a screenshot or simple graphic.
- **Suggested tags:** `Cursor`, `LM Studio`, `Local AI`, `Open Source`, `Developer Tools`, `Privacy`
- **Canonical link:** point to the GitHub README if you republish elsewhere.
