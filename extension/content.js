/* Glowline content script — watches AI chat UI state and reports transitions.
 *
 * States: BUSY (model generating) > ERROR (toast/rate-limit) > WAITING
 * (composer ready) > IDLE. Only sends on change, debounced to kill flicker.
 *
 * Detection is layered: site-specific selectors first, then generic fallbacks
 * (aria-label / button text). Chat UIs change often; the fallbacks keep it
 * working when a data-testid gets renamed.
 */
(() => {
  "use strict";

  const DEBOUNCE_MS = 350;

  const host = location.hostname;
  const SITE = host.includes("chatgpt.com")
    ? "chatgpt"
    : host.includes("claude.ai")
      ? "claude"
      : host.includes("gemini.google.com")
        ? "gemini"
        : "unknown";

  // ---- tiny DOM helpers -------------------------------------------------
  const $ = (sel, root = document) => {
    try {
      return root.querySelector(sel);
    } catch {
      return null;
    }
  };
  const $$ = (sel, root = document) => {
    try {
      return [...root.querySelectorAll(sel)];
    } catch {
      return [];
    }
  };
  const visible = (el) =>
    !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const labelOf = (el) =>
    ((el.getAttribute("aria-label") || "") + " " + (el.textContent || "")).toLowerCase();

  // ---- shared detectors ---------------------------------------------------
  const ERROR_RE =
    /something went wrong|network error|too many requests|rate[ -]?limit|usage limit|failed to (send|generate)|couldn['’]t (send|generate)|an error occurred|try again/i;

  /** Error toasts / alert banners. */
  function detectError() {
    const nodes = [
      ...$$('[role="alert"]'),
      ...$$('[data-testid*="toast" i]'),
      ...$$('[class*="toast" i]'),
    ];
    return nodes.some((n) => visible(n) && ERROR_RE.test(n.textContent || ""));
  }

  /** Generic "stop generating" button, used as a fallback on all sites. */
  function genericStopButton() {
    return $$("button").some((b) => visible(b) && /\bstop\b/.test(labelOf(b)));
  }

  // ---- per-site detectors ---------------------------------------------------
  // Each site: busy() = model generating; ready() = composer interactive.
  const DETECTORS = {
    chatgpt: {
      busy: () =>
        visible($('[data-testid="stop-button"]')) || genericStopButton(),
      ready: () =>
        visible($("#prompt-textarea")) ||
        visible($('[data-testid="composer"]')),
    },
    claude: {
      busy: () =>
        visible($('button[aria-label="Stop response"]')) || genericStopButton(),
      ready: () =>
        $$('div[contenteditable="true"]').some(visible),
    },
    gemini: {
      busy: () =>
        visible($('button[aria-label="Stop response"]')) || genericStopButton(),
      ready: () =>
        visible($("rich-textarea")) ||
        $$(".ql-editor[contenteditable]").some(visible),
    },
    unknown: {
      busy: () => genericStopButton(),
      ready: () => $$('[contenteditable="true"], textarea').some(visible),
    },
  };

  function detect() {
    const d = DETECTORS[SITE] || DETECTORS.unknown;
    if (detectError()) return "ERROR";
    if (d.busy()) return "BUSY";
    if (d.ready()) return "WAITING";
    return "IDLE";
  }

  // ---- debounced reporting --------------------------------------------------
  let lastSent = null;
  let timer = null;
  let lastHref = location.href;

  function report(state) {
    try {
      chrome.runtime.sendMessage({
        type: "glowline-state",
        site: SITE,
        state,
        url: location.href,
        ts: Date.now(),
      });
    } catch {
      /* extension reloaded / context invalidated — harmless */
    }
  }

  function evaluate() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const state = detect();
      if (state !== lastSent) {
        lastSent = state;
        report(state);
      }
    }, DEBOUNCE_MS);
  }

  // SPA navigation (these are all single-page apps): reset on URL change.
  const observer = new MutationObserver(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      lastSent = null;
    }
    evaluate();
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["disabled", "aria-disabled", "aria-label", "data-testid"],
  });

  evaluate(); // initial state on load
})();
