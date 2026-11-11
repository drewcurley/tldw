// Drives the REAL cache-check code, not regexes over it: startSummarize and
// probeOutcome out of content.js, and handleProbe out of background.js, each run in
// a sandbox where only their collaborators are faked -- a port that records what it
// is sent, a page read the test can hold open, a fetch that answers as told.
//
// Exists because the first tests for this were regexes, and four real regressions
// (an old server's 404 treated as an error, the model not sent, a hit not cached,
// a cancelled run still posting) all passed them.
const fs = require("fs");
const vm = require("vm");
const content = fs.readFileSync(process.argv[2], "utf8");
const worker = fs.readFileSync(process.argv[3], "utf8");

const failures = [];
const check = (name, got, want) => {
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};
const between = (src, start, end) => {
  const i = src.indexOf(start);
  if (i < 0) throw new Error("not found: " + start);
  const j = src.indexOf(end, i + start.length);
  if (j < 0) throw new Error("end not found: " + end);
  return src.slice(i, j);
};
const tick = () => new Promise((r) => setImmediate(r));

// ---------------------------------------------------------------- the panel ----
const PANEL_SRC = between(content, "  function probeOutcome(m)", "\n  function showLoading()");

function panel() {
  const ctx = {
    ports: [], shown: [], pageReads: 0, pendingReads: [],
    page: { cues: [{ start_ms: 0, text: "hi" }], status: "ok-panel (1 cues)" },
    holdPageRead: false,
    collectPageTranscript() {
      ctx.pageReads++;
      if (!ctx.holdPageRead) return Promise.resolve(ctx.page);
      return new Promise((resolve) => ctx.pendingReads.push(() => resolve(ctx.page)));
    },
    api: { runtime: { connect: () => {
      const p = { posted: [], listeners: [], onDisc: [], disconnected: false,
        postMessage(m) { if (m.type !== "ping") p.posted.push(m); },
        onMessage: { addListener: (fn) => p.listeners.push(fn) },
        onDisconnect: { addListener: (fn) => p.onDisc.push(fn) },
        disconnect() { p.disconnected = true; },
        deliver(m) { p.listeners.forEach((fn) => fn(m)); } };
      ctx.ports.push(p);
      return p;
    } } },
    showResult: (payload, cached) => ctx.shown.push({ result: payload.title, cached }),
    showError: (error) => ctx.shown.push({ error }),
    endBackground: (msg, isError) => ctx.shown.push({ background: msg, isError }),
    showLoading() {}, clearTimers() {}, updateProgress() {}, onPartial() {},
    addStopControl() {}, showTranscribeOffer() {},
    setInterval: () => 0, setTimeout: () => 0, clearTimeout() {}, clearInterval() {},
    port: null, requestActive: false, currentVideoId: null, backgrounded: false,
    lastProgress: null, partialState: null, txLive: null, pendingOffer: null,
    lastPayload: null, safetyTimer: null, pingTimer: null,
  };
  vm.createContext(ctx);
  vm.runInContext(PANEL_SRC, ctx);
  return ctx;
}
const A = ["https://www.youtube.com/watch?v=AAAAAAAAAAA", "AAAAAAAAAAA"];
const B = ["https://www.youtube.com/watch?v=BBBBBBBBBBB", "BBBBBBBBBBB"];
const types = (p) => p.posted.map((m) => m.type);

// --------------------------------------------------------------- the worker ----
const WORKER_SRC = between(worker, "const PROBE_TIMEOUT_MS", "\nasync function handleSummarize(");

function workerWith({ token = "tok", respond, cached } = {}) {
  const ctx = {
    posted: [], fetches: [], cache: new Map(cached ? [cached] : []),
    getSettings: async () => ({ serverUrl: "http://127.0.0.1:8765/", token,
                                model: "sonnet", segmentRatio: 0.25 }),
    safePost: (_port, m) => ctx.posted.push(m),
    fetch: async (url, init) => {
      ctx.fetches.push({ url, body: JSON.parse(init.body), auth: init.headers.Authorization });
      return respond();
    },
    AbortController, setTimeout, clearTimeout, JSON, Error,
  };
  vm.createContext(ctx);
  vm.runInContext(WORKER_SRC, ctx);
  return ctx;
}
const okJson = (body) => () => ({ ok: true, json: async () => body });

(async () => {
  // A cached summary: asked first, rendered, the page never read.
  let c = panel();
  c.startSummarize(...A, false, false);
  check("asks before reading the page", JSON.stringify(types(c.ports[0])), '["probeCache"]');
  check("...and hasn't read it", c.pageReads, 0);
  c.ports[0].deliver({ type: "probe", hit: true, payload: { title: "Cached" } });
  await tick();
  check("a hit is shown as a cached result",
    JSON.stringify(c.shown), JSON.stringify([{ result: "Cached", cached: true }]));
  check("a hit never reads the page", c.pageReads, 0);
  check("a hit sends no summarize", types(c.ports[0]).includes("summarize"), false);

  // A miss: exactly the old flow.
  c = panel();
  c.startSummarize(...A, false, false);
  c.ports[0].deliver({ type: "probe", hit: false });
  await tick(); await tick();
  const sent = c.ports[0].posted.find((m) => m.type === "summarize");
  check("a miss reads the page once", c.pageReads, 1);
  check("...then summarizes with its transcript", !!(sent && sent.page && sent.page.cues), true);
  check("...under the same flags as before",
    JSON.stringify([sent.refresh, sent.transcribe, sent.videoId]),
    JSON.stringify([false, false, "AAAAAAAAAAA"]));

  // Regenerate and an accepted transcription want new work: no probe.
  for (const [label, args] of [["refresh", [true, false]], ["transcribe", [false, true]]]) {
    c = panel();
    c.startSummarize(...A, ...args);
    await tick(); await tick();
    check(`${label} skips the probe`, types(c.ports[0]).includes("probeCache"), false);
    const s = c.ports[0].posted.find((m) => m.type === "summarize");
    check(`${label} summarizes straight away`, !!s, true);
    check(`${label} keeps its flag`, label === "refresh" ? s.refresh : s.transcribe, true);
  }

  // A run cancelled while the page was being read posts nothing.
  c = panel();
  c.holdPageRead = true;
  c.startSummarize(...A, false, false);
  c.ports[0].deliver({ type: "probe", hit: false });
  await tick();
  c.ports[0].deliver({ type: "error", error: "worker said no" });   // ends the run
  c.pendingReads.forEach((r) => r());
  await tick(); await tick();
  check("a cancelled run doesn't summarize", types(c.ports[0]).includes("summarize"), false);

  // Video A's page read finishing after video B started must not go out at all --
  // least of all on B's request.
  c = panel();
  c.holdPageRead = true;
  c.startSummarize(...A, false, false);
  c.ports[0].deliver({ type: "probe", hit: false });
  await tick();
  c.startSummarize(...B, false, false);                               // superseded
  c.pendingReads.forEach((r) => r());
  await tick(); await tick();
  const leaked = c.ports.flatMap((p) => p.posted)
    .filter((m) => m.type === "summarize" && m.videoId === "AAAAAAAAAAA");
  check("a superseded run's transcript is never sent", leaked.length, 0);
  check("the newer run is untouched", JSON.stringify(types(c.ports[1])), '["probeCache"]');

  // ------------------------------------------------------------ the worker ----
  // An older server answers 404: that is a miss, never an error -- otherwise a new
  // extension against an old server would stop working instead of falling back.
  let w = workerWith({ respond: () => ({ ok: false, status: 404 }) });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("a 404 from an older server is a miss",
    JSON.stringify(w.posted), JSON.stringify([{ type: "probe", hit: false }]));

  w = workerWith({ respond: () => { throw new TypeError("Failed to fetch"); } });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("an unreachable server is a miss", w.posted[0].hit, false);

  w = workerWith({ respond: okJson({ type: "miss" }) });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("a server miss is a miss", w.posted[0].hit, false);
  check("the model is sent -- the summary key depends on it", w.fetches[0].body.model, "sonnet");
  check("...and the segment ratio", w.fetches[0].body.segment_ratio, 0.25);
  check("...with the token", w.fetches[0].auth, "Bearer tok");
  check("...to the cached endpoint",
    w.fetches[0].url, "http://127.0.0.1:8765/summarize/cached");

  w = workerWith({ respond: okJson({ type: "result", title: "From server" }) });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("a server hit is delivered", w.posted[0].hit, true);
  check("...and kept in the worker's own cache", w.cache.has("AAAAAAAAAAA"), true);

  w = workerWith({ respond: () => { throw new Error("should not fetch"); },
                   cached: ["AAAAAAAAAAA", { title: "In memory" }] });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("the worker's own cache answers without a request", w.fetches.length, 0);
  check("...as a hit", w.posted[0].payload.title, "In memory");

  w = workerWith({ token: "", respond: () => { throw new Error("should not fetch"); } });
  await w.handleProbe({}, { url: A[0], videoId: A[1] });
  check("no token is reported straight away", w.posted[0].type, "error");
  check("...without a request", w.fetches.length, 0);

  // ------------------------------------------------- which limit was hit ----
  // The server has one limit for claude calls and one for reading aloud, and its
  // 429 says which. All three of the worker's message functions used to replace
  // that with their own fixed text -- one of them blaming "another summary" even
  // when what was busy was the speech.
  for (const name of ["httpError", "speakError", "askError"]) {
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(between(worker, `function ${name}(status, detail)`, "\n}\n") + "\n}\n" +
                    `this.fn = ${name};`, ctx);
    const said = "Already reading 2 summaries aloud. Try again when one finishes.";
    check(`${name}: a 429 shows the server's reason`, ctx.fn(429, said), said);
    check(`${name}: ...and still says something without one`, ctx.fn(429, ""),
          (m) => typeof m === "string" && m.length > 0);
  }

  if (failures.length) {
    console.log("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok");
})().catch((e) => { console.log("CRASH: " + (e && e.stack || e)); process.exit(1); });
