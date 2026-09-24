# iPhone / iPad app (PWA over Tailscale)

SkullMaster iQ ships as an **installable PWA** (Progressive Web App). On iOS you
add it to the Home Screen and it runs **full-screen, with its own icon, like a
native app** — no App Store, no Apple Developer account, no code signing, and it
always matches whatever the repo serves (no second build to keep in sync).

You already reach it remotely over Tailscale. The one thing that turns "a web
page" into "an app" is a **secure context**: iOS only registers the service
worker (and gives the full PWA treatment) over **HTTPS**. Tailscale Serve gives
you a real, trusted certificate on your `*.ts.net` name for free.

## Why a PWA (and not a native wrapper)

| Option | Verdict |
|---|---|
| **PWA via Tailscale Serve** | ✅ Recommended. No signing, no store, instant updates, works today. |
| Native wrapper (Capacitor/WKWebView) | Needs Xcode + a $99/yr Apple account to install beyond 7 days, and the app would just load the same Tailscale URL. Only worth it if you need native APIs (widgets, background audio). |
| Shortcuts/Web Clip | Falls back to Safari chrome; the PWA is strictly better. |

## Setup

### 1. Keep the server on loopback (recommended)

Tailscale Serve proxies from the tailnet to `127.0.0.1`, so nothing needs to be
exposed on your LAN. In `~/skullmaster-iq/.env`:

```ini
HOST=127.0.0.1
PORT=8501
```

If you previously set `HOST=0.0.0.0` to reach it by Tailscale IP, you can tighten
it back to loopback now — Serve is the only door in, and it's authenticated by
your tailnet.

### 2. Enable HTTPS on your tailnet (once)

In the Tailscale admin console: turn on **MagicDNS** and **HTTPS Certificates**
for the tailnet. (Tailscale uses Let's Encrypt for `*.ts.net`.)

### 3. Serve the app over HTTPS

On the Mac running SkullMaster:

```bash
tailscale serve status              # FIRST: see what the tailnet already serves
tailscale serve --bg 8501           # serve at the root: https://<machine>.<tailnet>.ts.net/
```

You'll get something like `https://<machine>.<tailnet>.ts.net/`. Use your `PORT`
if you changed it from 8501.

#### If the root URL is already taken

`tailscale serve` maps **one** service per HTTPS port, so if something else
already owns `/` you must **not** overwrite it. Check first:

```bash
tailscale serve status
# https://maxine.royal-gacrux.ts.net (tailnet only)
# |-- / proxy http://127.0.0.1:3737      ← another local service owns the root
```

Give SkullMaster **its own HTTPS port** instead — non-destructive, and the app
still gets a clean origin with scope `/`:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8501
tailscale serve status
# https://maxine.royal-gacrux.ts.net:8443 (tailnet only)
# |-- / proxy http://127.0.0.1:8501      ← SkullMaster iQ
```

Use **`https://<machine>.<tailnet>.ts.net:8443/`** on the iPhone. Any free
HTTPS port works (`9443`, …).

> **Do not use `--set-path /skullmaster`.** A sub-path breaks the app's absolute
> URLs (`/static/…`, `/api/…`, the manifest's `start_url`/`scope`), so the PWA
> would not install correctly. Use a dedicated port, or repoint the root (which
> replaces the other service).

### 4. Install it on the iPhone

1. Make sure the iPhone is on the tailnet (Tailscale app connected).
2. Open the app's `https://…` URL **in Safari** (not Chrome — only Safari can
   add to the Home Screen on iOS).
3. Sign in with your owner password.
4. **Share → Add to Home Screen** → name it "SkullMaster" → Add.

It now opens from the Home Screen **without Safari's address bar**, with the
SkullMaster icon, and the status bar blends into the dark theme.

## What the PWA adds

- **Offline shell** — a service worker pre-caches the static UI, so the app opens
  instantly and shows the sign-in screen even if the server is briefly down.
- **It never caches your data.** The worker deliberately ignores `/api/*`,
  `/health`, `/healthz`, and media: answers, citations, and sources always come
  live from the server. A stale cached answer would be worse than none.
- **Secure cookie over HTTPS** — once served via Tailscale, the session cookie is
  automatically marked `Secure` (it stays non-Secure on plain-HTTP localhost so
  dev still works).

## Keep the server always available

The iPhone app is only reachable while the server runs. For "it just works",
run it at login with a LaunchAgent (`~/Library/LaunchAgents/iq.skullmaster.server.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>iq.skullmaster.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/revenueroy/skullmaster-iq/.venv/bin/python</string>
    <string>-m</string>
    <string>app</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/revenueroy/skullmaster-iq</string>
  <!-- launchd starts with a minimal PATH. Without Homebrew on it, the app
       cannot find ffmpeg (Video Overviews) or tesseract (OCR) at runtime. -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/revenueroy/skullmaster-iq/data/server.log</string>
  <key>StandardErrorPath</key><string>/Users/revenueroy/skullmaster-iq/data/server.log</string>
</dict>
</plist>
```

```bash
launchctl unload ~/Library/LaunchAgents/iq.skullmaster.server.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/iq.skullmaster.server.plist
launchctl list | grep skullmaster          # → <pid>  0  iq.skullmaster.server
curl -s http://127.0.0.1:8501/healthz      # → {"status":"alive",...}
```

`RunAtLoad` starts it at login; `KeepAlive` restarts it if it exits. `launchctl
unload …` is the off switch. (Also keep Ollama running, since chat/embeddings
depend on it.)

### After changing code: restart, or the route serves the old build

`tailscale serve` is a **live proxy** to `127.0.0.1:8501` — it does not cache and
it does not watch the working tree. It serves whatever the *running* process
serves. `KeepAlive` only restarts the process if it **exits**, so a code change
(Python or `static/`) is picked up by the iPhone app **only after a restart**:

```bash
launchctl kickstart -k "gui/$(id -u)/iq.skullmaster.server"
```

`-k` kills the current process and launchd starts a fresh one, which re-imports
the code and re-reads `static/` from disk. Then confirm the route really is
serving the current build (not a stale one):

```bash
# a served static asset must hash-match the file on disk
curl -sS https://maxine.royal-gacrux.ts.net:8443/static/app.js | shasum -a 256
shasum -a 256 static/app.js
```

**Python changes require the restart** — the process must re-import them.
`/static/*` is read from disk per request, so the server serves the new asset
immediately, but the PWA's service worker is *stale-while-revalidate*: the phone
may show the previous asset for one load and pick up the new one on the next
(bump `CACHE_VERSION` in `static/sw.js` for a material shell change). When in
doubt, restart: it is cheap and idempotent.

## Recorded configuration (this deployment)

Captured 2026-09-22 so the setup is reproducible and auditable.

| Item | Value |
|---|---|
| Tailnet DNS name | `maxine.royal-gacrux.ts.net` |
| App bind address | `127.0.0.1:8501` (loopback only — not on the LAN) |
| iPhone URL | `https://maxine.royal-gacrux.ts.net:8443/` |
| Serve rule | `--https=8443` → `http://127.0.0.1:8501` |
| Pre-existing serve (untouched) | `--https=443` root → `http://127.0.0.1:3737` |
| Always-on agent | `~/Library/LaunchAgents/iq.skullmaster.server.plist` (label `iq.skullmaster.server`) |
| Logs | `~/skullmaster-iq/data/server.log` |

Verification performed (all passing):

```bash
tailscale serve status                                   # both mappings present
curl -sS https://maxine.royal-gacrux.ts.net:8443/healthz # 200, TLS verified, v1.7.0
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' \
  https://maxine.royal-gacrux.ts.net:8443/static/manifest.webmanifest  # 200 application/manifest+json
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' \
  https://maxine.royal-gacrux.ts.net:8443/static/sw.js                 # 200 text/javascript
curl -sS -o /dev/null -w '%{http_code} -> %{redirect_url}\n' \
  -H 'accept: text/html' https://maxine.royal-gacrux.ts.net:8443/      # 303 -> /login
```

Undo:

```bash
tailscale serve --https=8443 off
launchctl unload ~/Library/LaunchAgents/iq.skullmaster.server.plist
```

## Updating the installed app

Because it's a web app, a server update is picked up on the next launch — the
worker serves navigations network-first. If a UI change ever looks stale, bump
`CACHE_VERSION` in `static/sw.js` and reload once.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Added to Home Screen but opens with Safari chrome | Not a secure context. Confirm you're on the **https://…ts.net** URL (step 3), not `http://<tailscale-ip>:8501`. |
| "Add to Home Screen" missing | You're in Chrome/Firefox, or on a Private tab. Use Safari. |
| App loads but sign-in fails | The server isn't running, or `HOST`/`PORT` don't match what Serve proxies to. Check `tailscale serve status` and `python -m app.diagnostics`. |
| Tailscale URL won't load | MagicDNS/HTTPS not enabled, or the iPhone's Tailscale is disconnected. |
| Root URL shows a *different* app | Another local service owns `/` (check `tailscale serve status`). Use the dedicated-port URL, e.g. `:8443`. |
| Video Overviews / OCR stop working after installing launchd | The LaunchAgent's `PATH` lacks Homebrew, so `ffmpeg`/`tesseract` aren't found. Add the `EnvironmentVariables` block above and reload. |
| Stale UI after an update | Bump `CACHE_VERSION` in `static/sw.js`. |
| Answers missing/old | Expected: the worker never caches API responses; if you see old answers, you're reading a cached *page*, not a cached answer — reload. |

## Security note

Binding to the tailnet (via Serve) means the app is reachable by any device on
your tailnet; the **owner password is the only guard**. Keep Tailscale device
approval/ACLs tight, and prefer Serve (loopback upstream) over binding `0.0.0.0`.
See `docs/security.md`.
