/* Glowline background service worker — single WebSocket relay.
 *
 * Why the relay instead of letting each content script open its own socket?
 *  1. Page Content-Security-Policy (connect-src) can block ws:// from a
 *     content script on some of these sites; the service worker isn't
 *     subject to page CSP.
 *  2. One connection instead of one per tab.
 *  3. MV3 service workers get suspended when idle — we reconnect with
 *     backoff and flush the latest known state per site on every reconnect,
 *     plus a 1-minute keepalive alarm. Any incoming state message also
 *     wakes the worker and triggers a reconnect if needed.
 */
"use strict";

const WS_URL = "ws://localhost:8765";
const MAX_BACKOFF_MS = 30000;

let ws = null;
let backoffMs = 1000;
/** Latest known state per site: { state, ts }. Flushed on reconnect. */
const latest = new Map();

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function flush() {
  for (const [site, s] of latest) {
    send({ site, state: s.state, ts: s.ts });
  }
}

function scheduleReconnect() {
  setTimeout(connect, backoffMs);
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

function connect() {
  if (
    ws &&
    (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  let sock;
  try {
    sock = new WebSocket(WS_URL);
  } catch {
    scheduleReconnect();
    return;
  }
  ws = sock;
  ws.onopen = () => {
    backoffMs = 1000;
    flush();
  };
  ws.onclose = scheduleReconnect;
  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      /* onclose will schedule the reconnect */
    }
  };
  ws.onmessage = () => {
    /* server is receive-only today; handler kept so the socket stays warm */
  };
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "glowline-state") return;
  latest.set(msg.site, { state: msg.state, ts: msg.ts || Date.now() });
  if (ws && ws.readyState === WebSocket.OPEN) {
    const s = latest.get(msg.site);
    send({ site: msg.site, state: s.state, ts: s.ts });
  } else {
    connect(); // flush() on open sends everything in `latest`
  }
});

// Keep the worker + socket warm; also re-heals a silently dead socket.
chrome.alarms.create("glowline-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "glowline-keepalive") connect();
});

connect();
