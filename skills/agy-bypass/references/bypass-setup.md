# agy Proxy Bypass Setup (geo-blocked regions)

If you are in a geo-blocked region (e.g. Hong Kong), `agy` will fail with
"Eligibility check failed: not available in your location". This reference
describes the proxy bypass setup that makes the eligibility check pass.

## Architecture

```
agy (wrapper script)
  → sets HTTPS_PROXY / HTTP_PROXY / SSL_CERT_FILE
  → auto-starts mitmproxy if not running
  → execs agy.real (the real binary)
  → mitmproxy intercepts eligibility endpoints
  → chat traffic routed through GEMINI_API_KEY
```

Three paths, each with different auth:

| Path | Auth | Capabilities |
|---|---|---|
| `agy` (wrapper) | mitmproxy → GEMINI_API_KEY | Chat + file read only |
| `agy-direct` | OAuth direct | Full tools (interactive only) |
| `agyx` (Hermes tool) | agy.real OAuth | Text, read, write, images, exec, verify, auto-fix |

## Prerequisites

- `agy` binary v1.1.5 (188,830,144 bytes) from GitHub releases
- `mitmdump` (install via `pipx install mitmproxy`)
- `GEMINI_API_KEY` in `~/.hermes/.env`
- Hermes agyx plugin installed

## Step 1: Install agy v1.1.5

> **IMPORTANT**: v1.1.7 changed the `onboardUser` endpoint to a Google LRO
> (Long-Running Operation) proto format that our bypass cannot match. Use v1.1.5.

```bash
curl -fsSL https://github.com/google-antigravity/antigravity-cli/releases/download/1.1.5/agy_cli_linux_x64.tar.gz -o /tmp/agy.tar.gz
cd /tmp && tar xzf agy.tar.gz
# binary inside tar is named 'antigravity', not 'agy'
cp antigravity ~/.local/bin/agy.real
chmod +x ~/.local/bin/agy.real
```

Authenticate `agy.real` with Google OAuth:
```bash
agy-direct -p "hello"
# Opens browser for OAuth flow
```

## Step 2: Bypass script

Create the mitmproxy bypass script. It intercepts these endpoints:

| Endpoint | Action |
|---|---|
| `loadCodeAssist` | Moves free-tier from ineligibleTiers → allowedTiers |
| `quotaSummary` | Injects fake unlimited quota |
| `onboardUser` | Returns `{"done": true}` |
| `streamGenerateContent` | Routes through `GEMINI_API_KEY` |
| `fetchUserInfo` | Returns `regionCode: "US"` |
| `setUserSettings` | Echoes back |
| `listExperiments` | Returns empty experiment list |
| `fetchAvailableModels` | Passthrough |
| `/v1internal/` probe | Returns `{"done": true}` |
| Unleash features | Returns empty feature set |
| Google userinfo | Patches locale to `"en-US"` |

Key settings in the bypass script:
- `GEMINI_MODEL = "gemini-2.5-flash"` (3.5-flash streaming returns 503)
- Always intercept `streamGenerateContent` (not just on HTTP ≥ 400)
- `make_200()` must clear ALL headers and use raw `.content` bytes

## Step 3: Wrapper script

The wrapper at `~/.local/bin/agy` sets proxy env vars and auto-starts the proxy:

```bash
#!/usr/bin/env bash
set -euo pipefail
REAL_AGY="${HOME}/.local/bin/agy.real"
PROXY_PORT=8085
CA_CERT="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem"

if ! ss -tlnp 2>/dev/null | grep -q ":${PROXY_PORT} "; then
    systemctl --user start agy-bypass-proxy 2>/dev/null || true
    sleep 1
fi

export HTTPS_PROXY="http://127.0.0.1:${PROXY_PORT}"
export HTTP_PROXY="http://127.0.0.1:${PROXY_PORT}"
export SSL_CERT_FILE="${CA_CERT}"
export NO_PROXY="localhost,127.0.0.1"

exec "${REAL_AGY}" "$@"
```

## Step 4: systemd service

```ini
# ~/.config/systemd/user/agy-bypass-proxy.service
[Unit]
Description=AGY mitmproxy bypass for geo-restriction
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/mitmdump --listen-port 8085 \
  --scripts %h/.hermes/scripts/agy_bypass.py \
  --set block_global=false --set console_eventlog_verbosity=info
Restart=on-failure
RestartSec=5
Environment=HOME=%h

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable agy-bypass-proxy.service
systemctl --user start agy-bypass-proxy.service
```

## Step 5: Verify

```bash
# 1. Proxy running
systemctl --user status agy-bypass-proxy

# 2. Chat works
agy -p "Say PONG"

# 3. File write works (via agyx)
agyx "Create hello.py that prints OK" --out-dir /tmp/agyx_check
cat /tmp/agyx_check/hello.py

# 4. Multi-turn works
agyx "What file did we just create?" --continue --out-dir /tmp/agyx_check
```

## Pitfalls

1. **v1.1.7 downgrade**: The binary in the GitHub release tar is named
   `antigravity`, NOT `agy`. Rename to `agy.real` after extraction.

2. **mitmproxy full path**: systemd user services have no PATH — use absolute
   path to `mitmdump` in the service ExecStart.

3. **VPN alone won't work**: Google checks account region (tied to your Google
   account's registered country), not just IP. The proxy bypass faking
   server-side responses is required even with VPN active.

4. **Port conflict**: Only one mitmdump on port 8085. Kill stale processes
   before starting systemd service.

5. **`block_global=false` is mandatory**: without it, mitmproxy intercepts ALL
   HTTPS traffic system-wide when HTTPS_PROXY is set.

6. **agy v1.1.6+ not supported**: v1.1.6 polls `/v1internal/` indefinitely
   after eligibility. v1.1.7 uses LRO proto format for `onboardUser` that our
   bypass cannot match. Stick with v1.1.5.
