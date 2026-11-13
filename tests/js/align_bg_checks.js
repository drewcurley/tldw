// T-020: the REAL handleAlign out of extension/background.js, with only its
// collaborators faked: settings, a port that records what it is sent, and a fetch
// that streams NDJSON (or fails) as told.
//
// Usage: node align_bg_checks.js <background.js>   (prints checks=<n>)
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const i = src.indexOf("const ALIGN_TIMEOUT_MS");
const j = src.indexOf("const PROBE_TIMEOUT_MS");
if (i < 0 || j < 0) { console.error("handleAlign not found"); process.exit(2); }

let n = 0;
const failures = [];
const check = (name, got, want) => {
  n++;
  const ok = typeof want === "function" ? want(got)
    : JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};

function streamOf(lines) {
  const enc = new TextEncoder();
  const chunks = [enc.encode(lines.map((l) => JSON.stringify(l)).join("\n") + "\n")];
  return { getReader: () => ({
    read: async () => chunks.length ? { done: false, value: chunks.shift() }
                                     : { done: true, value: undefined } }) };
}

function sandbox({ token = "t", respond, gate }) {
  const port = { posted: [], disc: [],
    postMessage(m) { port.posted.push(m); },
    onDisconnect: { addListener: (fn) => port.disc.push(fn) } };
  const seen = { aborted: false, body: null, url: null };
  const ctx = {
    getSettings: async () => { if (gate) await gate;
      return { serverUrl: "http://127.0.0.1:8766/", token, whisper: "ask" }; },
    safePost: (p, m) => { try { p.postMessage(m); } catch (_) {} },
    setTimeout: () => 1, clearTimeout() {},
    AbortController: class { constructor() { this.signal = {}; }
      abort() { seen.aborted = true; } },
    TextDecoder, TextEncoder, JSON,
    fetch: async (url, opts) => { seen.url = url; seen.body = JSON.parse(opts.body); return respond(); },
  };
  vm.createContext(ctx);
  vm.runInContext(src.slice(i, j) + "\nglobalThis.handleAlign = handleAlign;", ctx);
  return { run: (msg) => ctx.handleAlign(port, msg), port, seen };
}

const MSG = { type: "align", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
              windows: [{ start_ms: 0, end_ms: 5000 }] };

(async () => {
  // The happy path: progress, a window, then done.
  let s = sandbox({ respond: () => ({ ok: true, status: 200, body: streamOf([
    { type: "progress", message: "Fetching…" },
    { type: "window", i: 0, start_ms: 0, end_ms: 5000, words: [{ s: 1, e: 2, w: "zarf" }], cached: false },
    { type: "result", timed: 1, cached: 0, failed: 0 }]) }) });
  await s.run(MSG);
  check("posts to /align/stream", s.seen.url, "http://127.0.0.1:8766/align/stream");
  check("sends url, whisper and windows only", Object.keys(s.seen.body).sort(), ["url", "whisper", "windows"]);
  check("forwards in order", s.port.posted.map((m) => m.type), ["alignProgress", "alignWindow", "alignDone"]);
  check("window shape", s.port.posted[1], { type: "alignWindow", i: 0, words: [{ s: 1, e: 2, w: "zarf" }], cached: false });
  check("done counts", s.port.posted[2], { type: "alignDone", timed: 1, cached: 0, failed: 0 });
  // A port disconnect aborts the fetch.
  check("registers onDisconnect", s.port.disc.length, 1);
  s.port.disc[0]();
  check("disconnect aborts", s.seen.aborted, true);

  // No token: said before any fetch.
  s = sandbox({ token: "", respond: () => { throw new Error("fetched"); } });
  await s.run(MSG);
  check("no token", s.port.posted, [{ type: "alignError", status: 0, reason: "token", error: "", installHint: "" }]);
  check("no token: no fetch", s.seen.url, null);

  // A plain-JSON refusal keeps the server's reason and install hint.
  s = sandbox({ respond: () => ({ ok: false, status: 503,
    json: async () => ({ error: "Whisper isn't installed for tldw serve.", reason: "whisper_missing", install_hint: "pipx inject youtube-tldw faster-whisper" }) }) });
  await s.run(MSG);
  check("plain-JSON error", s.port.posted[0], { type: "alignError", status: 503, reason: "whisper_missing",
    error: "Whisper isn't installed for tldw serve.", installHint: "pipx inject youtube-tldw faster-whisper" });

  // An older server: 404 with no reason, so the panel falls back to the status.
  s = sandbox({ respond: () => ({ ok: false, status: 404, json: async () => ({ error: "not found" }) }) });
  await s.run(MSG);
  check("404 has no reason", [s.port.posted[0].status, s.port.posted[0].reason], [404, null]);

  // An in-stream error after a cached window.
  s = sandbox({ respond: () => ({ ok: true, status: 200, body: streamOf([
    { type: "window", i: 0, start_ms: 0, end_ms: 5000, words: [], cached: true },
    { type: "error", status: 429, reason: "busy", error: "busy text" }]) }) });
  await s.run(MSG);
  check("in-stream error", s.port.posted.map((m) => m.type), ["alignWindow", "alignError"]);
  check("in-stream reason", s.port.posted[1].reason, "busy");

  // A stream that ends with no terminal event is "other".
  s = sandbox({ respond: () => ({ ok: true, status: 200, body: streamOf([{ type: "progress", message: "x" }]) }) });
  await s.run(MSG);
  check("no terminal is other", s.port.posted.at(-1).reason, "other");

  // Network failure and abort.
  s = sandbox({ respond: () => { throw new TypeError("Failed to fetch"); } });
  await s.run(MSG);
  check("unreachable", s.port.posted[0].reason, "unreachable");
  s = sandbox({ respond: () => { const e = new Error("aborted"); e.name = "AbortError"; throw e; } });
  await s.run(MSG);
  check("timeout", s.port.posted[0].reason, "timeout");

  // A read that fails after the result arrived posts nothing more.
  s = sandbox({ respond: () => {
    const enc = new TextEncoder();
    let sent = false;
    return { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
      if (!sent) { sent = true; return { done: false,
        value: enc.encode(JSON.stringify({ type: "result", timed: 0, cached: 0, failed: 0 }) + "\n") }; }
      throw new TypeError("network changed"); } }) } };
  } });
  await s.run(MSG);
  check("no error after done", s.port.posted.map((m) => m.type), ["alignDone"]);

  // Rev 5.6: the page can go away while settings are read. The listener is in
  // place before that await, and nothing is fetched or posted afterwards.
  let open;
  s = sandbox({ gate: new Promise((r) => { open = r; }), respond: () => { throw new Error("fetched"); } });
  const pending = s.run(MSG);
  check("onDisconnect registered before the settings read", s.port.disc.length, 1);
  s.port.disc[0]();
  open();
  await pending;
  check("gone before settings: no fetch", s.seen.url, null);
  check("gone before settings: nothing posted", s.port.posted, []);

  console.log("checks=" + n);
  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
})();
