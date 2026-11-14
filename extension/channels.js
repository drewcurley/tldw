// Shared, pure helpers for automatic summaries: which channel a page belongs to,
// what the stored list may contain, whether to run, and the auto-run budget.
//
// Loaded in four places -- the content script (toolbar injection and the
// registered script), the options page, the Chrome service worker (importScripts)
// and Firefox's background scripts -- as ONE file, never copies.
//
// The whole file is an IIFE that declares nothing at top level: the toolbar
// re-injects it on every click into a page that may already have it, and a
// top-level const would throw "already declared" there, or collide with
// background.js's and options.js's own top-level names. The result is assigned
// unconditionally (not ??=): a page element with id="TLDW_CHANNELS" would
// otherwise be picked up through named access and taken as the real thing.
(() => {
  const ORIGIN = "https://www.youtube.com/*";
  const MAX_CHANNELS = 500;
  const MAX_NAME = 100;
  const MAX_HREF = 300;
  const HOUR_MS = 3600 * 1000;
  const DAY_MS = 24 * HOUR_MS;
  const HOUR_LIMIT = 10;
  const DAY_LIMIT = 40;

  // A channel's identity from the owner link's href: "@handle" (NFC, lowercase)
  // or "channel/UC…" (case kept: ids are case-sensitive). Anything else -- another
  // host, a legacy /c/ or /user/ link, a malformed escape -- is null.
  function channelKey(href) {
    if (typeof href !== "string" || !href) return null;
    let url;
    try { url = new URL(href, "https://www.youtube.com"); } catch (_) { return null; }
    if (url.hostname !== "www.youtube.com") return null;
    const path = url.pathname;
    let m = /^\/@([^/]+)(?:\/.*)?$/.exec(path);
    if (m) {
      let handle;
      try { handle = decodeURIComponent(m[1]); } catch (_) { return null; }
      handle = handle.normalize("NFC").toLowerCase();
      return handle && !/[\s/]/.test(handle) ? "@" + handle : null;
    }
    m = /^\/channel\/(UC[A-Za-z0-9_-]{22})(?:\/.*)?$/.exec(path);
    return m ? "channel/" + m[1] : null;
  }

  // The one rule for a stored key: it must come out of channelKey unchanged.
  function isValidKey(key) {
    return typeof key === "string" && channelKey("/" + key) === key;
  }

  const capName = (name) => String(name).trim().slice(0, MAX_NAME);

  // What's stored, made safe to use. Unknown fields are kept (a later version may
  // add one), nothing is ever written back from here, and the first of any
  // duplicate wins.
  function sanitizeChannels(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    const seen = new Set();
    for (const item of raw) {
      if (out.length >= MAX_CHANNELS) break;
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      if (!isValidKey(item.key) || typeof item.name !== "string") continue;
      if (seen.has(item.key)) continue;
      seen.add(item.key);
      const clean = { ...item, name: capName(item.name) };
      if (typeof clean.href !== "string" || clean.href.length > MAX_HREF) delete clean.href;
      if (typeof clean.addedAt !== "number" || !Number.isFinite(clean.addedAt)) {
        delete clean.addedAt;
      }
      out.push(clean);
    }
    return out;
  }

  // The auto-run log: start times. Entries in the future are dropped, so a clock
  // set back can't keep automatic summaries paused until it catches up.
  // (NaN and ±Infinity fail these comparisons, so they're dropped too.)
  function sanitizeRunLog(raw, now) {
    if (!Array.isArray(raw)) return [];
    return raw.filter((t) => typeof t === "number" && t <= now && now - t < DAY_MS);
  }

  // "run", or "skip:<reason>". The page-side guards; the worker's budget is the
  // one the page can't influence.
  function autoRunDecision(s) {
    if (!s || !s.channel || !Array.isArray(s.channels)
        || !s.channels.some((c) => c && c.key === s.channel.key)) {
      return "skip:not-listed";
    }
    if (s.handled) return "skip:already-handled";
    if (s.requestActive) return "skip:request-active";
    if (s.panelOpen) return "skip:panel-open";
    if (s.haveSummary) return "skip:have-summary";
    return "run";
  }

  // { ok: true } or { ok: false, limit: "hour" | "day" }, for a log already run
  // through sanitizeRunLog.
  function budgetDecision(log, now) {
    const recent = log.filter((t) => now - t < HOUR_MS).length;
    if (recent >= HOUR_LIMIT) return { ok: false, limit: "hour" };
    if (log.length >= DAY_LIMIT) return { ok: false, limit: "day" };
    return { ok: true };
  }

  // The panel row's words. Plain strings: the row is built with textContent and
  // setAttribute, so a channel name can never become markup.
  function autoRowText({ listed, name }) {
    const n = capName(name || "");
    return listed
      ? { label: "✓ " + n + " is summarized automatically", manage: "Manage" }
      : { label: "⚡ Always summarize " + n,
          title: "Summarize new videos from this channel in the background when you open them" };
  }

  globalThis.TLDW_CHANNELS = Object.freeze({
    ORIGIN, MAX_CHANNELS, MAX_NAME, HOUR_LIMIT, DAY_LIMIT, HOUR_MS, DAY_MS,
    channelKey, isValidKey, sanitizeChannels, sanitizeRunLog, autoRunDecision,
    budgetDecision, autoRowText,
  });
})();
