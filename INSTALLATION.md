# local_proxy — Installation und Konfiguration

Anleitung, um **LM Studio** auf dem **gleichen Rechner** über **local_proxy** für
**Cursor** (oder andere OpenAI-kompatible IDEs) nutzbar zu machen — ohne AWS-Server.

---

## Architektur

```
IDE (Cursor)
    │  HTTPS  https://api.lmstudio.local:8443/v1/chat/completions
    │         Host: api.lmstudio.local  →  127.0.0.1  (/etc/hosts)
    ▼
local_proxy (Port 8443, TLS optional)
    └── HTTP-Proxy  ──►  LM Studio  http://localhost:1234
```

| Komponente | Wo | Port | Aufgabe |
|---|---|---|---|
| **local_proxy** | Dein Rechner | **8443** (TLS) oder **8088** (ohne TLS) | HTTPS-Reverse-Proxy für die IDE |
| **LM Studio** | Dein Rechner | **1234** (localhost) | Lokales Modell, OpenAI-kompatible API |

Kein AWS-Server, kein WebSocket-Tunnel, kein nginx — nur ein Python-Prozess.

---

## 1. Voraussetzungen

### Hardware & Software

- **macOS, Linux oder Windows** mit Python **3.9+**
- **LM Studio** mit geladenem Modell und gestartetem **Local Server**
- **OpenSSL** (für TLS-Zertifikate; auf macOS/Linux meist vorinstalliert)
- **Git** (Repository klonen)

Prüfen:

```bash
python3 --version    # ≥ 3.9
openssl version
```

### Python-Abhängigkeiten

Nur **`aiohttp`** (siehe `requirements.txt` im Repository-Root).

---

## 2. Repository und virtuelle Umgebung

```bash
git clone https://github.com/fexperts-dev/local_proxy.git
cd local_proxy

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 3. Hosts-Eintrag

Die IDE soll eine **HTTPS-URL mit Hostnamen** nutzen (nicht nur `localhost`). Dafür
eine lokale Domain auf `127.0.0.1` mappen.

### macOS / Linux

```bash
sudo nano /etc/hosts
```

Zeile hinzufügen (Domain muss zu `LOCAL_DOMAIN` passen):

```
127.0.0.1  api.lmstudio.local
```

Speichern und testen:

```bash
ping -c 1 api.lmstudio.local
# sollte 127.0.0.1 antworten
```

### Windows

Als Administrator `C:\Windows\System32\drivers\etc\hosts` bearbeiten:

```
127.0.0.1  api.lmstudio.local
```

---

## 4. Konfiguration (`local_proxy.env`)

Im Repository-Root:

```bash
cp local_proxy.env.example local_proxy.env
```

Beispiel `local_proxy.env`:

```bash
LOCAL_DOMAIN=api.lmstudio.local
LOCAL_PORT=8443
LOCAL_PROXY_USE_TLS=true
LMSTUDIO_URL=http://localhost:1234
LOCAL_PROXY_DATA_DIR=~/.local_proxy
LOCAL_PROXY_LOG_LEVEL=INFO
```

| Variable | Pflicht | Standard | Beschreibung |
|---|---|---|---|
| `LOCAL_DOMAIN` | Nein | `api.lmstudio.local` | Hostname aus `/etc/hosts`; CN des TLS-Zerts |
| `LOCAL_PORT` | Nein | `8443` (TLS) / `8088` (ohne TLS) | HTTP(S)-Port von local_proxy |
| `LOCAL_PROXY_USE_TLS` | Nein | `true` | HTTPS (für Cursor empfohlen) |
| `LMSTUDIO_URL` | Nein | `http://localhost:1234` | LM-Studio-Basis-URL |
| `LOCAL_PROXY_DATA_DIR` | Nein | `~/.local_proxy` | Zertifikate, Session, Logs |
| `LOCAL_PROXY_LOG_LEVEL` | Nein | `INFO` | Log-Level |
| `LOCAL_PROXY_LOG` | Nein | `<data_dir>/local_proxy.log` | Log-Datei |
| `LOCAL_PROXY_SESSION_FILE` | Nein | `<data_dir>/session.json` | Session für IDE |

`local_proxy.env` wird beim Start automatisch geladen (`local_proxy/config.py` liest
`local_proxy.env` und danach `.env` im Repository-Root). Es ist **kein** manuelles
`source` nötig, solange du `python -m local_proxy` aus dem Repo-Verzeichnis startest.

Optional — Variablen explizit in die Shell übernehmen (z. B. für Tests):

```bash
set -a
source local_proxy.env
set +a
python -m local_proxy
```

Beim Start schreibt local_proxy automatisch **`session.json`** mit Base URL und
API Key nach `LOCAL_PROXY_DATA_DIR` — manuell setzen ist nicht nötig.

---

## 5. TLS und Zertifikat

Mit `LOCAL_PROXY_USE_TLS=true` (Standard) erzeugt local_proxy beim **ersten Start** ein
**selbstsigniertes** Zertifikat:

```
~/.local_proxy/certs/api.lmstudio.local.pem
~/.local_proxy/certs/api.lmstudio.local.key
```

Der **Common Name (CN)** entspricht `LOCAL_DOMAIN`. Deshalb muss die IDE genau diese
Domain in der Base URL verwenden — nicht `127.0.0.1` und nicht `localhost`.

> **Hinweis:** Das Zertifikat wird erst erzeugt, wenn local_proxy mindestens einmal
> gestartet wurde. Vor dem Import in den Schlüsselbund ggf. kurz starten und wieder
> beenden.

### 5.1 Zertifikat in den macOS-Schlüsselbund (Keychain)

Cursor (Electron) vertraut selbstsignierten Zertifikaten nur, wenn sie im **System-**
oder **Anmelde-Schlüsselbund** als vertrauenswürdig eingetragen sind.

#### Variante A — Kommandozeile (System-Schlüsselbund, alle Benutzer)

Domain im Pfad anpassen, falls `LOCAL_DOMAIN` abweicht:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  ~/.local_proxy/certs/api.lmstudio.local.pem
```

Passwort des Mac-Administrators eingeben.

Nur für den **aktuellen Benutzer** (ohne `sudo`):

```bash
security add-trusted-cert -d -r trustRoot \
  -k ~/Library/Keychains/login.keychain-db \
  ~/.local_proxy/certs/api.lmstudio.local.pem
```

#### Variante B — Schlüsselbundverwaltung (GUI)

1. **Schlüsselbundverwaltung** öffnen (`Keychain Access.app`)
2. Menü **Ablage** → **Objekte importieren…**
3. Datei wählen: `~/.local_proxy/certs/api.lmstudio.local.pem`
4. Schlüsselbund: **System** (empfohlen) oder **Anmeldung**
5. Importiertes Zertifikat doppelklicken → Abschnitt **Vertrauen**
6. **Bei der Verwendung dieses Zertifikats:** → **Immer vertrauen**
7. Fenster schließen (ggf. Admin-Passwort bestätigen)

#### Prüfen, ob das Zertifikat greift

Ohne `-k` (insecure) muss HTTPS funktionieren:

```bash
curl --noproxy '*' https://api.lmstudio.local:8443/healthz
```

Erwartung: JSON mit `"client_connected": true` — **kein** SSL-Fehler.

Zertifikat im Schlüsselbund suchen:

```bash
security find-certificate -c api.lmstudio.local -a
```

#### Zertifikat entfernen (bei Domain-Wechsel)

```bash
sudo security delete-certificate -c api.lmstudio.local \
  /Library/Keychains/System.keychain
```

Anschließend local_proxy neu starten (erzeugt neues Zertifikat) und erneut importieren.

### 5.2 Linux (Debian/Ubuntu)

```bash
sudo cp ~/.local_proxy/certs/api.lmstudio.local.pem \
  /usr/local/share/ca-certificates/api.lmstudio.local.crt
sudo update-ca-certificates
```

### 5.3 Ohne TLS

`LOCAL_PROXY_USE_TLS=false` oder `--no-tls`: HTTP auf Port **8088**.
Cursor erwartet in der Regel HTTPS — nur für Tests oder wenn die IDE HTTP erlaubt.

---

## 6. LM Studio vorbereiten

1. Modell in LM Studio laden.
2. Tab **Local Server** → Server starten (Standard-Port **1234**).
3. Prüfen:

```bash
curl -s http://localhost:1234/v1/models | python3 -m json.tool
```

Modell-IDs aus dem Feld `"id"` notieren — in Cursor **exakt** so eintragen.

---

## 7. local_proxy starten

### Kommandozeile

```bash
cd local_proxy
source .venv/bin/activate
python -m local_proxy
```

Nach dem Start erscheint u. a.:

```
=== IDE configuration ===
Base URL:  https://api.lmstudio.local:8443/v1
API Key:   <proxy_token>
```

Dieselben Werte stehen in **`~/.local_proxy/session.json`**:

```json
{
  "proxy_token": "…",
  "api_base_url": "https://api.lmstudio.local:8443/v1",
  "target": "http://localhost:1234"
}
```

> **API Key rotiert bei jedem Neustart** von local_proxy. Nach Neustart Wert aus Konsole,
> GUI oder `session.json` erneut in Cursor eintragen.

### Desktop-GUI

```bash
python -m local_proxy --gui
```

Die GUI zeigt Domain, Port, LM-Studio-URL, Base URL, API Key und verfügbare Modell-IDs.
Logs: System, Anfragen, Payload, Response.

### CLI-Overrides

```bash
python -m local_proxy \
  --domain api.lmstudio.local \
  --port 8443 \
  --lmstudio-url http://localhost:1234
```

---

## 8. Umgebungsvariablen und Proxy-Bypass

### 8.1 local_proxy (automatisch)

| Quelle | Wann geladen |
|---|---|
| `local_proxy.env` (Repo-Root) | Beim Start von `python -m local_proxy` |
| `.env` (Repo-Root) | Fallback, wenn Variable noch nicht gesetzt |
| CLI-Flags (`--domain`, …) | Überschreiben Env-Werte |

launchd/systemd können dieselbe Datei per `EnvironmentFile=` übergeben (§11).

### 8.2 Cursor und System-Proxy (NO_PROXY)

Auf vielen Macs läuft parallel ein **HTTP-Proxy** (VPN-Client, Corporate Proxy,
Tools wie Proxifier). Typische Shell-Variablen:

```bash
HTTP_PROXY=http://127.0.0.1:52408
HTTPS_PROXY=http://127.0.0.1:52408
ALL_PROXY=…
NO_PROXY=127.0.0.1,localhost
```

`api.lmstudio.local` löst zwar über `/etc/hosts` auf `127.0.0.1` auf, ist aber **kein**
literaler `localhost`-String. Anfragen an `https://api.lmstudio.local:8443` können deshalb
**trotzdem durch den Proxy** laufen → oft **403 Forbidden** oder
*„We're having trouble connecting to the model provider“* in Cursor.

**Lösung:** die lokale Domain in **`NO_PROXY`** aufnehmen.

In `~/.zshrc` (oder `~/.zprofile`):

```bash
export NO_PROXY="127.0.0.1,localhost,api.lmstudio.local,.lmstudio.local"
export no_proxy="$NO_PROXY"
```

`.lmstudio.local` deckt Subdomains ab; `api.lmstudio.local` exakt für die Standard-Domain.

Shell neu laden:

```bash
source ~/.zshrc
```

**Wichtig:** Apps aus dem **Dock** erben Shell-Variablen oft **nicht**. Drei Wege:

| Methode | Wirkung |
|---|---|
| **`http.noProxy` in Cursor `settings.json`** (§9.2) | Zuverlässigste Lösung — unabhängig vom Startweg |
| Cursor aus Terminal starten | `open -a Cursor` **nach** `source ~/.zshrc` |
| `launchctl setenv` | Auch für Dock-Start (bis Neustart): `launchctl setenv NO_PROXY "127.0.0.1,localhost,api.lmstudio.local,.lmstudio.local"` |

Prüfen, ob der Proxy aktiv ist:

```bash
env | grep -i proxy
```

API-Test **mit** vs. **ohne** Proxy-Umgehung:

```bash
# sollte funktionieren (Proxy-Bypass):
curl --noproxy '*' -sk https://api.lmstudio.local:8443/healthz

# kann fehlschlagen, wenn NO_PROXY die Domain nicht enthält:
curl -sk https://api.lmstudio.local:8443/healthz
```

---

## 9. Cursor konfigurieren

### 9.1 Models (Base URL und API Key)

1. **Cursor** → **Settings** → **Models**
2. **OpenAI API Key:** Wert von `proxy_token` (Konsole, GUI oder `~/.local_proxy/session.json`)
3. **Override OpenAI Base URL:** z. B.

   ```
   https://api.lmstudio.local:8443/v1
   ```

   Muss mit **`/v1`** enden, **ohne** abschließenden Slash. Port und Domain müssen zu
   `LOCAL_DOMAIN` / `LOCAL_PORT` passen.

4. Modell hinzufügen — **exakt** wie in `/v1/models` (Groß-/Kleinschreibung beachten)
5. Andere Modelle deaktivieren
6. **Verify** klicken

> Mit Custom-OpenAI-Endpunkt funktionieren in Cursor i. d. R. nur OpenAI-kompatible
> Chat-Modelle. Modelle, die nur `reasoning_content` statt `content` liefern, schlagen
> Verify oft fehl — ein normales Chat-Modell verwenden.

### 9.2 `settings.json` — Proxy und noProxy

Datei öffnen: **Cmd+Shift+P** → **Preferences: Open User Settings (JSON)**

macOS-Pfad:

```
~/Library/Application Support/Cursor/User/settings.json
```

Minimale Ergänzung für **local_proxy** (Proxy-Bypass für die lokale Domain):

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

| Einstellung | Bedeutung |
|---|---|
| `http.proxySupport` | `"on"` = System-Proxy respektieren; `"fallback"` = `http.proxy` in settings, sonst System; `"override"` = nur `http.proxy` aus settings (Standard in Cursor — kann System-Proxy ignorieren) |
| `http.noProxy` | Hosts, die **nicht** über den Proxy gehen (Array oder kommagetrennt) |

**Domain anpassen:** Einträge in `http.noProxy` müssen zu `LOCAL_DOMAIN` passen.

Wenn du **keinen** System-Proxy brauchst und Cursor direkt verbinden soll:

```json
{
  "http.proxy": "",
  "http.proxySupport": "off",
  "http.noProxy": [
    "127.0.0.1",
    "localhost",
    "api.lmstudio.local",
    ".lmstudio.local"
  ]
}
```

Bei hartnäckigen TLS-Problemen (selten nötig, wenn Zertifikat im Schlüsselbund ist):

```json
{
  "http.proxyStrictSSL": false
}
```

Nach Änderungen an `settings.json`: **Cursor vollständig beenden** (nicht nur Fenster
schließen) und neu starten.

### 9.3 Checkliste Cursor

| # | Prüfung |
|---|---|
| 1 | Base URL = `https://<LOCAL_DOMAIN>:<LOCAL_PORT>/v1` |
| 2 | API Key = aktueller `proxy_token` aus `session.json` |
| 3 | Modell-ID exakt wie in LM Studio |
| 4 | Zertifikat im Schlüsselbund vertrauenswürdig (§5.1) |
| 5 | `http.noProxy` enthält `api.lmstudio.local` (§9.2) |
| 6 | `NO_PROXY` in Shell gesetzt, falls Cursor aus Terminal startet (§8.2) |
| 7 | Cursor nach allen Änderungen neu gestartet |

---

## 10. Funktionstest

| # | Aktion | Erwartung |
|---|---|---|
| 1 | LM Studio Local Server | `curl http://localhost:1234/v1/models` → JSON |
| 2 | `/etc/hosts` | `ping api.lmstudio.local` → `127.0.0.1` |
| 3 | `python -m local_proxy` | „Server listening on …“, IDE-Konfiguration ausgegeben |
| 4 | Health | `curl -k https://api.lmstudio.local:8443/healthz` → `"client_connected": true` |
| 5 | Modelle über Proxy | `curl -k -H "Authorization: Bearer <proxy_token>" https://api.lmstudio.local:8443/v1/models` |
| 6 | Cursor Verify | Erfolgreich |
| 7 | Proxy-Bypass | `curl` ohne `--noproxy` funktioniert (nach NO_PROXY / settings.json) |

Nach dem Vertrauen des Zertifikats (§5.1) kann `-k` bei `curl` entfallen.

Automatisierter Smoke-Test:

```bash
python tests/test_local_proxy.py
```

---

## 11. Betrieb im Hintergrund (optional)

### macOS — launchd

Beispiel-Plist an `~/Library/LaunchAgents/com.local.local-proxy.plist` anpassen
(Pfade, `WorkingDirectory`, `local_proxy.env`; optional `EnvironmentVariables` für
`NO_PROXY` — siehe §8.2):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.local.local-proxy</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/local_proxy/.venv/bin/python</string>
    <string>-m</string>
    <string>local_proxy</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/local_proxy</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NO_PROXY</key>
    <string>127.0.0.1,localhost,api.lmstudio.local,.lmstudio.local</string>
  </dict>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.local.local-proxy.plist
```

### Linux — systemd (User-Service)

`~/.config/systemd/user/local-proxy.service`:

```ini
[Unit]
Description=local_proxy for LM Studio
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/local_proxy
EnvironmentFile=/path/to/local_proxy/local_proxy.env
ExecStart=/path/to/local_proxy/.venv/bin/python -m local_proxy
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now local-proxy
```

---

## 12. Fehlerbehebung

### „Connection refused“ / Cursor erreicht API nicht

- Läuft `python -m local_proxy`?
- Stimmt `/etc/hosts` mit `LOCAL_DOMAIN` überein?
- Stimmen Port und Schema in der Cursor Base URL (`https` + `:8443`)?

### Cursor Verify schlägt fehl (TLS)

- Zertifikat für `LOCAL_DOMAIN` erzeugt? → `ls ~/.local_proxy/certs/`
- Zertifikat im Schlüsselbund als **Immer vertrauen** eingetragen? (§5.1)
- `curl` ohne `-k` erfolgreich?
- Base URL verwendet **Domain**, nicht `127.0.0.1`

### Cursor: „trouble connecting to the model provider“ (Proxy)

- `env | grep -i proxy` — ist ein System-Proxy aktiv?
- `api.lmstudio.local` in **`NO_PROXY`** (§8.2) und **`http.noProxy`** (§9.2)?
- Terminal-Test: `curl --noproxy '*' -sk …/healthz` vs. `curl -sk …/healthz`
- Cursor nach Änderungen **vollständig** neu gestartet?

### `"client_connected": false` in `/healthz`

- Sollte nicht vorkommen, solange local_proxy läuft — bei Fehlern Log prüfen: `~/.local_proxy/local_proxy.log`
- Port-Konflikt: anderer Dienst auf `LOCAL_PORT`?

### Falsches oder kein Modell in Cursor

- LM Studio läuft und Modell ist geladen?
- Modell-ID exakt aus `curl …/v1/models` übernehmen
- GUI zeigt Modell-IDs nach Start unter „Modell-IDs (exakt in Cursor)“

### API Key ungültig nach Neustart

- Normal: neuer `proxy_token` pro Lauf
- Key aus frischer `session.json` oder GUI in Cursor aktualisieren

---

## 13. Unterschied zu Remote-Zugriff

| | **local_proxy** | **reverse_https (AWS)** |
|---|---|---|
| Server | Lokal, ein Prozess | EC2 + nginx |
| Erreichbarkeit | Nur dieser Rechner (via hosts) | Öffentlich über Internet |
| TLS | Selbstsigniert, lokale Domain | Let's Encrypt |
| Konfiguration | `local_proxy.env` | AWS-Deploy, Tunnel-Setup |
| Doku | Diese Datei | [reverse_https/INSTALLATION.md](https://github.com/fexperts-dev/reverse_https/blob/main/INSTALLATION.md) |

Für Team- oder Remote-Zugriff siehe **[fexperts-dev/reverse_https](https://github.com/fexperts-dev/reverse_https)**.

---

## 14. Sicherheitshinweise

- **`session.json`** nicht ins Git committen.
- Der Dienst bindet an **`0.0.0.0`**. Auf Rechnern mit untrusted Netzwerk den Port in der
  Firewall blockieren oder nur lokal nutzen (Zugriff erfolgt ohnehin über `127.0.0.1`).
- Session-Tokens sind kurzlebig pro Lauf; bei Bedarf local_proxy neu starten.
- Selbstsignierte Zerts nur für lokale Entwicklung — nicht für produktive Multi-User-Dienste.
