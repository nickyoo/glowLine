# glowline

Your MacBook screen becomes an ambient status light for AI chats. Crack the
lid, and the glow bleeding through tells you what's happening:

| State     | Glow                  | Meaning                              |
|-----------|-----------------------|--------------------------------------|
| **BUSY**  | pulsing deep amber    | a model is generating                |
| **WAITING** | solid soft emerald  | response done, composer ready        |
| **ERROR** | pulsing red           | network failure / rate limit toast   |
| **IDLE**  | dim slate             | nothing happening                    |

```
┌──────────────┐      ws://localhost:8765      ┌──────────────────┐
│  Chrome/Brave │ ─── JSON {site, state} ────▶ │  glowline.py     │
│  extension    │                               │  (stdlib only)   │
│  chatgpt.com  │                               │  fullscreen glow │
│  claude.ai    │                               │  window, macOS   │
│  gemini.      │                               └──────────────────┘
│  google.com   │
└──────────────┘
```

The extension watches each chat tab with a `MutationObserver`, debounces
transitions (350 ms settle), and reports only on change. The server keeps
per-site state, aggregates by priority (**ERROR > BUSY > WAITING > IDLE**),
forgets sites that go quiet for 45 s, and crossfades the fullscreen window
between colors at ~30 fps.

## Setup

### 1. Start the server

```bash
python3 server/glowline.py
```

Needs tkinter: python.org macOS builds ship it; Homebrew python needs
`brew install python-tk@<your-version>`. Keep it running while you want the
glow. (Tip: add it to System Settings → General → Login Items to start on boot.)

### 2. Load the extension

1. Open `chrome://extensions` (works in Brave too: `brave://extensions`).
2. Enable **Developer mode** (top right).
3. **Load unpacked** → select the `extension/` folder.

No Web Store, no build step, no permissions beyond a 1-minute keepalive alarm.

### 3. Try it

Open [chatgpt.com](https://chatgpt.com), [claude.ai](https://claude.ai), or
[gemini.google.com](https://gemini.google.com), send a prompt, and crack the
lid. Amber pulse while it thinks, emerald when it's done. A tiny label in the
bottom-right corner shows `site · STATE` so you can confirm it's tracking.

## Shortcuts (glow window focused)

| Keys         | Action              |
|--------------|---------------------|
| `Esc`        | hide / show the glow |
| click        | hide / show the glow |
| `Cmd`+`Esc`  | quit                |
| `Cmd`+`Q`    | quit                |

## Design notes

- **Background relay, not direct socket.** The prompt sketched content scripts
  opening the WebSocket directly. Instead, content scripts send
  `chrome.runtime` messages and the service worker holds the single socket.
  Page `Content-Security-Policy` (`connect-src`) can block `ws://` from a
  content script on some of these sites; the worker isn't subject to page CSP.
  Bonus: one connection instead of one per tab.
- **`ws://localhost` from `https://` pages is fine.** Loopback is exempt from
  mixed-content blocking, so the extension can reach the local server without
  TLS. If a browser ever changes this, the fallback is `wss://` via `mkcert`.
- **MV3 service workers sleep.** The worker reconnects with backoff, flushes
  the latest state per site on every reconnect, runs a 1-minute keepalive
  alarm, and any state message wakes it. You'll never have to think about it.
- **Zero dependencies.** The WebSocket server is hand-rolled on stdlib sockets
  (RFC 6455 handshake + masked frame parsing, ping/pong, close). The window is
  tkinter: fullscreen, borderless, always-on-top.

## Customizing

Colors live in `COLORS` at the top of `server/glowline.py` as `(r, g, b)`
tuples; pulse speeds are in `GlowWindow.tick()`. Site detectors live in
`extension/content.js` under `DETECTORS` — layered as site-specific selectors
first, generic aria-label/text fallbacks second, so a renamed `data-testid`
doesn't silently break detection.

`assets/social-preview.png` is the repo's social image — upload it under
repo Settings → General → Social preview.

## Troubleshooting

- **"port 8765 is already in use"** — glowline is already running. Kill the old
  one or keep it.
- **Amber stuck on after closing a tab** — the server forgets quiet sites after
  45 s; worst case, restart it.
- **No color change at all** — check the extension is enabled, the server is
  running, and the tab is one of the three matched sites. The bottom-right
  label tells you what the server last heard.
- **Site UI changed, detection wrong** — chat UIs churn; open an issue with
  what the stop/send buttons look like now and the selectors get updated.
