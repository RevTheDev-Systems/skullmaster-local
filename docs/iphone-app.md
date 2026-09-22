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
tailscale serve --bg 8501          # older syntax: tailscale serve --bg http://127.0.0.1:8501
tailscale serve status             # shows the https://…ts.net URL it created
```

You'll get something like `https://<machine>.<tailnet>.ts.net/`. Use your `PORT`
if you changed it from 8501.

### 4. Install it on the iPhone

1. Make sure the iPhone is on the tailnet (Tailscale app connected).
2. Open the `https://<machine>.<tailnet>.ts.net/` URL **in Safari** (not Chrome —
   only Safari can add to the Home Screen on iOS).
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
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/revenueroy/skullmaster-iq/data/server.log</string>
  <key>StandardErrorPath</key><string>/Users/revenueroy/skullmaster-iq/data/server.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/iq.skullmaster.server.plist
```

(Also keep Ollama running the same way, since chat/embeddings depend on it.)

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
| Stale UI after an update | Bump `CACHE_VERSION` in `static/sw.js`. |
| Answers missing/old | Expected: the worker never caches API responses; if you see old answers, you're reading a cached *page*, not a cached answer — reload. |

## Security note

Binding to the tailnet (via Serve) means the app is reachable by any device on
your tailnet; the **owner password is the only guard**. Keep Tailscale device
approval/ACLs tight, and prefer Serve (loopback upstream) over binding `0.0.0.0`.
See `docs/security.md`.
