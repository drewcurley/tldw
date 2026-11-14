// Automatic summaries in extension/content.js, driven through the REAL code:
// the page read, startSummarize and the drains, the toasts, the auto-check
// scheduling, the panel row and the toolbar handler are sliced out of content.js
// and run in a vm context with a fake clock, the fake DOM (tests/js/fakedom.js),
// fake ports and fake storage. Only the panel's own drawing (showLoading,
// showResult, ...) is stubbed -- and recorded.
//
// The whole file is also loaded once, for what only the top of it decides: the
// init guard and failing closed without channels.js.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const D = require(path.join(__dirname, "fakedom.js"));
const SRC = fs.readFileSync(process.argv[2], "utf8");
const CHANNELS = fs.readFileSync(process.argv[3], "utf8");

const failures = [];
const CHECKS = { n: 0 };
const check = (name, got, want) => {
  CHECKS.n++;
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};
const between = (start, end, from = 0) => {
  const i = SRC.indexOf(start, from);
  if (i < 0) throw new Error("not found: " + start);
  const j = SRC.indexOf(end, i + start.length);
  if (j < 0) throw new Error("end not found: " + end);
  return SRC.slice(i, j);
};
const tick = () => new Promise((r) => setImmediate(r));
// A promise left rejected anywhere is a failure, not just node's opinion.
const unhandled = [];
process.on("unhandledRejection", (e) => { unhandled.push(e); failures.push("unhandled rejection: " + e); });
const flush = async (n = 10) => { for (let i = 0; i < n; i++) await tick(); };

const CODE = [
  between("  const CLOCK = ", "\n  function probeOutcome(m)"),          // page read
  between("  function probeOutcome(m)", "\n  function showLoading()"),  // runs, drains
  between("  function clearTimers() {", "\n  }\n") + "\n  }\n",
  between("  // A summary that finished while the panel was closed.",
          "  // \"\" means the server couldn't size this video"),            // toasts
  between("  function stopTranscribe(url, via)", "\n  function showError("),
  between("  // Settings the first check and the panel row wait for", "\n  initAutoRun();"),
  SRC.slice(SRC.indexOf('  api.runtime.onMessage.addListener((msg) => {\n    if (msg.type === "TLDW_INVOKE")'),
            SRC.lastIndexOf("})();")),                                  // the toolbar
].join("\n");
const TRUST_GUARD = between('    root.addEventListener("click", (e) => {', "}, true);") + "}, true);";

const V1 = "AAAAAAAAAAA", V2 = "BBBBBBBBBBB", V3 = "CCCCCCCCCCC";
const watch = (v) => "https://www.youtube.com/watch?v=" + v;
const LISTED = { key: "@chan", name: "Chan" };

function makeClock() {
  const c = { now: 1_000_000, timers: [], seq: 0 };
  c.setTimeout = (fn, ms) => { const id = ++c.seq; c.timers.push({ id, at: c.now + Math.max(0, ms || 0), fn, every: 0 }); return id; };
  c.setInterval = (fn, ms) => { const id = ++c.seq; c.timers.push({ id, at: c.now + ms, fn, every: ms }); return id; };
  c.clear = (id) => { c.timers = c.timers.filter((x) => x.id !== id); };
  return c;
}

function setup({ stored = { autoRun: true, autoChannels: [LISTED] }, url = watch(V1),
                 block = { vid: V1 }, visible = true, holdSettings = false } = {}) {
  const clock = makeClock();
  const doc = new D.Document();
  doc.visibilityState = visible ? "visible" : "hidden";
  const t = { clock, doc, ports: [], infos: [], messages: [], onMessage: [], changed: [],
              shown: [], errors: [], offers: [], showLoading: 0, settingsGate: null };
  t.reply = () => Promise.resolve({ ok: true, result: "ok" });
  let settingsResolve;
  const settings = new Promise((r) => { settingsResolve = r; });
  t.releaseSettings = () => settingsResolve();
  if (!holdSettings) settingsResolve();
  const newPort = () => {
    const p = { posted: [], pings: 0, listeners: [], disc: [], disconnected: false,
      postMessage(m) {
        if (p.disconnected) throw new Error("Attempting to use a disconnected port object");
        if (m.type === "ping") p.pings++; else p.posted.push(m);
      },
      onMessage: { addListener: (fn) => p.listeners.push(fn) },
      onDisconnect: { addListener: (fn) => p.disc.push(fn) },
      disconnect() { p.disconnected = true; },
      deliver(m) { p.listeners.slice().forEach((fn) => fn(m)); },
      drop() { p.disc.slice().forEach((fn) => fn()); },
      types() { return p.posted.map((m) => m.type); } };
    t.ports.push(p);
    return p;
  };
  // window's own events (pagehide/pageshow), dispatched as a browser would.
  t.win = { listeners: {}, addEventListener(type, fn) {
    (t.win.listeners[type] = t.win.listeners[type] || []).push(fn); } };
  t.fire = (type, { persisted = false, trusted = true } = {}) =>
    (t.win.listeners[type] || []).forEach((fn) => fn({ type, persisted, isTrusted: trusted }));
  const ctx = {
    document: doc, location: { href: url }, URL, window: t.win,
    console: { info: (...a) => t.infos.push(a.map(String).join(" ")), log() {}, warn() {}, error() {} },
    setTimeout: clock.setTimeout, setInterval: clock.setInterval,
    clearTimeout: clock.clear, clearInterval: clock.clear,
    Date: { now: () => clock.now },
    api: {
      runtime: {
        connect: () => newPort(),
        sendMessage: (msg) => { t.messages.push(msg); return t.reply(msg); },
        onMessage: { addListener: (fn) => t.onMessage.push(fn) },
      },
      storage: {
        local: { get: (d) => settings.then(() => ({ ...d, ...JSON.parse(JSON.stringify(stored)) })) },
        onChanged: { addListener: (fn) => t.changed.push(fn) },
      },
    },
    // content.js's own state, as declared at the top of its IIFE.
    autoRun: false, autoChannels: [], autoHandled: new Set(), autoRunVid: null,
    autoTimer: null, currentRun: null, drains: new Map(), autoLogged: new Set(),
    pausedShown: false, lastCached: false, badgeVid: null, autoResults: new Set(),
    host: null, root: null, port: null,
    requestActive: false, currentVideoId: null, backgrounded: false, lastProgress: null,
    partialState: null, txLive: null, pendingOffer: null, lastPayload: null,
    safetyTimer: null, pingTimer: null, creepTimer: null, stageTimer: null, progressPct: 0,
    // The panel's drawing, stubbed and recorded.
    showLoading() { t.showLoading++; ctx.host = "panel"; },
    // As the real showResult does first (a source check below pins that).
    showResult(p, cached) {
      ctx.badgeShown(p); ctx.servedShown(p);
      t.shown.push({ title: p && p.title, cached });
    },
    showError(m) { t.errors.push(m); },
    showTranscribeOffer(offer, url, vid) { t.offers.push(vid); },
    onPartial() {}, updateProgress() {}, addStopControl() {}, showPartial() {}, applyWidth() {},
  };
  vm.createContext(ctx);
  vm.runInContext(CHANNELS, ctx);
  ctx.CH = ctx.TLDW_CHANNELS;
  vm.runInContext(CODE, ctx);
  t.ctx = ctx;
  if (block) t.block = setBlock(t, block);
  t.navigate = (vid, blk = { vid }) => {
    ctx.location.href = vid ? watch(vid) : "https://www.youtube.com/";
    if (blk) t.block = setBlock(t, blk);
    doc.dispatchEvent(D.event("yt-navigate-finish"));
  };
  t.invoke = (vid) => t.onMessage.forEach((fn) => fn({ type: "TLDW_INVOKE", url: watch(vid), videoId: vid }));
  t.setVisible = (v) => {
    doc.visibilityState = v ? "visible" : "hidden";
    doc.dispatchEvent(D.event("visibilitychange"));
  };
  t.advance = async (ms) => {
    const end = clock.now + ms;
    for (;;) {
      await flush();
      const due = clock.timers.filter((x) => x.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      clock.now = due.at;
      if (due.every) due.at += due.every; else clock.timers = clock.timers.filter((x) => x !== due);
      due.fn();
    }
    clock.now = end;
    await flush();
  };
  t.toast = () => {
    const el = doc.getElementById("tldw-toast")
      || (doc.fullscreenElement && doc.fullscreenElement.querySelector("#tldw-toast"));
    return el;
  };
  t.toastText = () => (t.toast() ? t.toast().textContent : null);
  return t;
}

function setBlock(t, { vid, href = "/@chan", name = "Chan", hrefs }) {
  const doc = t.doc;
  for (const old of doc.querySelectorAll("ytd-watch-metadata")) old.remove();
  if (vid === null) return null;
  const E = (tag, attrs, kids) => D.el(doc, tag, attrs, kids);
  const block = E("ytd-watch-metadata", { "video-id": vid }, [
    E("div", { id: "title" }, [E("h1", {}, "Title " + vid)]),
    E("div", { id: "owner" }, (hrefs || [href]).map((h) =>
      E("ytd-channel-name", {}, [E("a", { href: h }, name)]))),
  ]);
  doc.body.appendChild(block);
  return block;
}

// An auto run for V1, past its dwell, its probe answered "miss" so the summarize
// has gone out.
async function running(opts) {
  const t = setup(opts);
  t.ctx.initAutoRun();
  await t.advance(3000);
  const p = t.ports[0];
  if (p) { p.deliver({ type: "probe", hit: false }); await t.advance(0); }
  return { t, p };
}

(async () => {
  // ================================================================ AC7 ====
  {
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(2999);
    check("full load: nothing before the 3s dwell", t.ports.length, 0);
    await t.advance(1);
    check("full load: one quiet summarize after 3s", t.ports.length, 1);
    check("...asks the cache first", JSON.stringify(t.ports[0].types()), '["probeCache"]');
    check("...never mounts the panel", t.showLoading, 0);
    t.ports[0].deliver({ type: "probe", hit: false });
    await t.advance(0);
    const sum = t.ports[0].posted.find((m) => m.type === "summarize");
    check("...then summarizes", !!sum, true);
    check("...marked auto", sum && sum.auto, true);
    check("...for this video", sum && sum.videoId, V1);
    check("...the page object carries page_v 2", sum && sum.page && sum.page.page_v, 2);
    check("the decision is logged", t.infos.filter((x) => x.includes("run " + V1)).length, 1);
  }
  {
    // Settings arrive after init: the first check waits for them.
    const t = setup({ holdSettings: true });
    t.ctx.initAutoRun();
    await t.advance(5000);
    check("settings not loaded yet: nothing runs", t.ports.length, 0);
    t.releaseSettings();
    await t.advance(3000);
    check("settings arriving after init: the full load still auto-runs", t.ports.length, 1);
  }
  {
    // An in-page navigation onto a listed channel's video.
    const t = setup({ url: "https://www.youtube.com/", block: null });
    t.ctx.initAutoRun();
    await t.advance(5000);
    check("the home page: nothing", t.ports.length, 0);
    t.navigate(V2);
    await t.advance(3000);
    check("yt-navigate-finish to a listed channel: runs", t.ports.length, 1);
    check("...for the new video", t.ports[0].posted[0].videoId, V2);
  }
  for (const [label, opts] of [
    ["an unlisted channel", { block: { vid: V1, href: "/@other" } }],
    ["an empty list", { stored: { autoRun: true, autoChannels: [] } }],
    ["the option off", { stored: { autoRun: false, autoChannels: [LISTED] } }],
    ["a /shorts/ URL", { url: "https://www.youtube.com/shorts/" + V1 }],
    ["a non-/watch URL", { url: "https://www.youtube.com/@chan/videos" }],
    ["a video with several channels", { block: { vid: V1, hrefs: ["/@chan", "/@other"] } }],
  ]) {
    const t = setup(opts);
    t.ctx.initAutoRun();
    await t.advance(20000);
    check(`nothing starts for ${label}`, t.ports.length, 0);
  }
  {
    // The page never catches up within 15s: give up, and stay given up.
    const t = setup({ block: { vid: V3 } });
    t.ctx.initAutoRun();
    await t.advance(15000);
    check("15s without the page syncing: gives up", t.clock.timers.length, 0);
    check("...and logs it once", t.infos.filter((x) => x.includes("timeout")).length, 1);
    t.block = setBlock(t, { vid: V1 });
    await t.advance(5000);
    check("...a late sync doesn't revive it", t.ports.length, 0);
  }
  {
    // The URL moves on while the page is still catching up: that check ends.
    const t = setup({ block: { vid: V3 } });
    t.ctx.initAutoRun();
    await t.advance(1000);
    t.ctx.location.href = watch(V2);           // no event yet
    await t.advance(250);
    check("the URL's v changing during the poll ends that check", t.clock.timers.length, 0);
    t.block = setBlock(t, { vid: V1 });
    await t.advance(5000);
    check("...nothing starts for the old video", t.ports.length, 0);
  }
  {
    // Hidden before 3s: nothing; shown again: a fresh 3s.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(2000);
    t.setVisible(false);
    check("hiding the tab cancels the pending dwell", t.clock.timers.length, 0);
    await t.advance(10000);
    check("hidden before the dwell ends: nothing", t.ports.length, 0);
    t.setVisible(true);
    await t.advance(2999);
    check("shown again: the dwell restarts from 0", t.ports.length, 0);
    await t.advance(1);
    check("...and runs after a full 3s", t.ports.length, 1);
  }
  {
    // Moving to another video during the dwell: nothing for the first.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(2000);
    t.ctx.location.href = "https://www.youtube.com/";   // left, without an event
    await t.advance(5000);
    check("leaving during the dwell: nothing", t.ports.length, 0);
  }
  {
    const t = setup({ visible: false });
    t.ctx.initAutoRun();
    await t.advance(30000);
    check("a hidden tab: nothing", t.ports.length, 0);
    t.setVisible(true);
    await t.advance(3000);
    check("...until it's shown, and then 3s", t.ports.length, 1);
  }
  {
    const t = setup({ visible: false });
    t.ctx.initAutoRun();
    await t.advance(1000);
    t.ctx.location.href = watch(V2);
    t.block = setBlock(t, { vid: V2, href: "/@other" });
    t.setVisible(true);
    await t.advance(5000);
    check("a hidden tab whose video changed meanwhile: nothing for either", t.ports.length, 0);
  }
  {
    // Many navigations leave one pending check, not a pile of them.
    const t = setup({ block: { vid: V3 } });
    t.ctx.initAutoRun();
    for (let i = 0; i < 20; i++) {
      t.navigate(i % 2 ? V1 : V2, { vid: V3 });
      await t.advance(100);
    }
    check("20 navigations leave exactly one pending timer", t.clock.timers.length, 1);
  }
  {
    // A toolbar click during the poll or dwell: the click decides.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(1000);
    t.invoke(V1);
    check("a click during the dwell starts the click's run", t.ports.length, 1);
    check("...a manual one", t.ports[0].posted.some((m) => m.auto), false);
    check("...and the video is handled", t.ctx.autoHandled.has(V1), true);
    await t.advance(10000);
    check("...no automatic run on top", t.ports.length, 1);
    t.ctx.requestActive = false;   // that run long finished
    t.ctx.host = null;
    t.navigate(V2, { vid: V2, href: "/@other" });
    t.navigate(V1);
    await t.advance(10000);
    check("...nor after navigating away and back", t.ports.length, 1);
  }
  {
    const t = setup({ block: { vid: V3 } });
    t.ctx.initAutoRun();
    await t.advance(500);                     // still polling
    t.invoke(V1);
    t.block = setBlock(t, { vid: V1 });
    await t.advance(10000);
    check("a click during the poll cancels the check too",
      t.ports.filter((p) => p.posted.some((m) => m.auto)).length, 0);
  }
  {
    const t = setup({ block: null, url: watch(V2) });
    t.ctx.initAutoRun();
    t.ctx.pendingOffer = { videoId: V2, offer: {}, url: watch(V2) };
    t.navigate(V2, null);
    check("navigating within the same video keeps a pending offer", !!t.ctx.pendingOffer, true);
    t.navigate(V3, null);
    check("navigating to another video clears it", t.ctx.pendingOffer, null);
  }
  {
    // Each decision once per video: no log line per poll tick.
    const t = setup({ block: { vid: V1, href: "/@other" } });
    t.ctx.initAutoRun();
    await t.advance(4000);
    t.setVisible(false); t.setVisible(true);
    await t.advance(4000);
    check("a skip is logged once per video",
      t.infos.filter((x) => x.includes("skip:not-listed " + V1)).length, 1);
    const t2 = setup({ block: { vid: V3 } });
    t2.ctx.initAutoRun();
    await t2.advance(14000);
    check("polling logs nothing per tick", t2.infos.length, 0);
  }

  // ================================================================ AC8 ====
  {
    const t = setup({ block: null });
    t.ctx.progressPct = 57;
    t.ctx.pendingOffer = { videoId: V2, offer: {}, url: watch(V2) };
    t.ctx.startSummarize(watch(V1), V1, false, false, { auto: true });
    check("quiet start: never mounts", t.showLoading, 0);
    check("...is backgrounded from the start", t.ctx.backgrounded, true);
    check("...resets the progress", t.ctx.progressPct, 0);
    check("...clears an old offer", t.ctx.pendingOffer, null);
    check("...remembers it's automatic", t.ctx.autoRunVid, V1);
    // A cache hit: the "ready" notice in the automatic wording.
    t.ports[0].deliver({ type: "probe", hit: true, payload: { title: "Cached", video_id: V1 } });
    check("a cache hit gives the automatic ready notice",
      t.toastText(), "TL;DW summarized this video for you — click to read");
    check("...and nothing is drawn", t.shown.length, 0);
    check("...the run is over", t.ctx.autoRunVid, null);
  }
  {
    const { t, p } = await running();
    p.deliver({ type: "result", payload: { title: "Fresh", video_id: V1 }, cached: false });
    check("a result gives the automatic ready notice",
      t.toastText(), "TL;DW summarized this video for you — click to read");
    check("...kept for when they click", t.ctx.lastPayload.title, "Fresh");
    check("...with its cached flag", t.ctx.lastCached, false);
  }
  {
    const { t, p } = await running();
    p.deliver({ type: "error", error: "Can't reach the tldw server." });
    check("an error is prefixed", t.toastText(), "Automatic summary failed: Can't reach the tldw server.");
  }
  {
    // A run that failed isn't retried by coming back to the video on this page.
    const { t, p } = await running();
    p.deliver({ type: "error", error: "Boom." });
    t.navigate(V2, { vid: V2, href: "/@other" });
    t.navigate(V1);
    await t.advance(10000);
    check("an auto run is once per video per page, even after it failed", t.ports.length, 1);
  }
  {
    const { t, p } = await running();
    p.deliver({ type: "needsTranscription", offer: { video_id: V1 } });
    check("a captionless video gives the offer notice",
      t.toastText(), "TL;DW needs your answer about this video — click to see it");
    check("...and keeps the offer", t.ctx.pendingOffer && t.ctx.pendingOffer.videoId, V1);
  }
  {
    // Accepting the offer is a click: that run is never automatic.
    const t = setup({ block: { vid: V1 } });
    t.ctx.startSummarize(watch(V1), V1, false, true);
    await t.advance(0);
    const sum = t.ports[0].posted.find((m) => m.type === "summarize");
    check("the transcribe retry summarizes straight away", !!sum, true);
    check("...without the auto flag", sum && "auto" in sum, false);
  }
  {
    // A run that's no longer the live one can't touch anything.
    const { t, p } = await running();
    t.ctx.startSummarize(watch(V2), V2, false, false);   // a click on another video
    p.deliver({ type: "result", payload: { title: "Old", video_id: V1 } });
    p.deliver({ type: "error", error: "late" });
    p.drop();
    check("a superseded port's messages are ignored", t.toastText(), null);
    check("...nothing drawn", t.shown.length + t.errors.length, 0);
    check("...and the new run is still live", t.ctx.requestActive, true);
  }
  {
    // Re-attached by a click: the run is the user's; its error isn't "automatic".
    const { t, p } = await running();
    t.invoke(V1);
    check("re-attaching mid-run starts nothing new", t.ports.length, 1);
    check("...and makes the run the user's", t.ctx.autoRunVid, null);
    t.ctx.backgrounded = true;              // they closed the panel again
    p.deliver({ type: "error", error: "Boom." });
    check("...its error has no automatic prefix", t.toastText(), "Boom.");
  }

  {
    // Clicked before the summarize went out: the run is the user's, so it is not
    // counted against the budget.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(3000);
    const p = t.ports[0];
    t.invoke(V1);
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    const sent = p.posted.find((m) => m.type === "summarize");
    check("a run re-attached before its summarize is sent goes out as the user's",
      !!sent && !("auto" in sent), true);
    // ...and if the worker refuses it anyway (the reply was already on its way),
    // the open panel says so instead of sitting on "Starting…".
    p.deliver({ type: "autoPaused", limit: "hour" });
    check("a refusal while watching is shown in the panel", t.errors.length, 1);
    check("...not as a notice", t.toastText(), null);
  }

  // ================================================================ AC9 ====
  {
    // Left before the summarize went out: dropped, nothing sent.
    const t = setup();
    let release;
    t.ctx.collectPageTranscript = () => new Promise((r) => { release = r; });
    t.ctx.initAutoRun();
    await t.advance(3000);
    const p = t.ports[0];
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    t.navigate(V2, { vid: V2, href: "/@other" });
    check("left during the page read: the port is dropped", p.disconnected, true);
    release({ page_v: 2, cues: [] });
    await t.advance(0);
    check("...and the summarize never goes out", p.types().includes("summarize"), false);
    check("...no notice", t.toastText(), null);
    check("...no drain", t.ctx.drains.size, 0);
  }
  {
    // Left after: drained -- kept alive by its own ping until it finishes.
    const { t, p } = await running();
    t.navigate(V2, { vid: V2, href: "/@other" });
    check("left after the summarize: not dropped", p.disconnected, false);
    check("...drained", t.ctx.drains.size, 1);
    check("...the run is no longer the live one", t.ctx.requestActive, false);
    const before = p.pings;
    await t.advance(20000);
    check("...its own ping keeps the worker alive", p.pings > before, true);
    p.deliver({ type: "result", payload: { title: "Done", video_id: V1 } });
    check("...the result ends the drain", p.disconnected, true);
    check("...silently", t.toastText(), null);
    check("...and isn't shown", t.shown.length, 0);
    const after = p.pings;
    await t.advance(60000);
    check("...its ping stops", p.pings, after);
    check("...nothing left pending", t.clock.timers.length, 0);
  }
  {
    const { t, p } = await running();
    t.navigate(V2, { vid: V2, href: "/@other" });
    p.deliver({ type: "autoPaused", limit: "hour" });
    check("autoPaused ends a drain", p.disconnected, true);
    check("...silently", t.toastText(), null);
  }
  {
    // Two drains at once, each with its own ping and cap.
    const { t, p: a } = await running();
    t.navigate(V2);                                    // listed too
    await t.advance(3000);
    const b = t.ports[1];
    b.deliver({ type: "probe", hit: false });
    await t.advance(0);
    t.navigate(V3, { vid: V3, href: "/@other" });
    check("two drains at once", t.ctx.drains.size, 2);
    a.deliver({ type: "result", payload: { title: "A", video_id: V1 } });
    check("ending one leaves the other", a.disconnected && !b.disconnected, true);
    const bp = b.pings;
    await t.advance(20000);
    check("...which keeps its own ping", b.pings > bp, true);
  }
  {
    // A drain that never hears back ends at its 160s cap.
    const { t, p } = await running();
    t.navigate(V2, { vid: V2, href: "/@other" });
    await t.advance(159000);
    check("a silent drain is still alive at 159s", p.disconnected, false);
    await t.advance(1000);
    check("...and ends at the 160s cap", p.disconnected, true);
    check("...with no notice", t.toastText(), null);
    const n = p.pings;
    await t.advance(60000);
    check("...its ping cleared", p.pings, n);
  }
  {
    // The next run owns its own timers; a drained port dying doesn't touch it.
    const { t, p: a } = await running();
    t.navigate(V2);
    await t.advance(3000);
    const b = t.ports[1];
    check("the next video's run starts", !!b, true);
    a.drop();                                          // the old port dies
    check("a drained port disconnecting doesn't fail the new run", t.ctx.requestActive, true);
    check("...no 'lost connection'", t.toastText(), null);
    const bp = b.pings, ap = a.pings;
    await t.advance(20000);
    check("the new run's ping is on its own port", b.pings > bp, true);
    check("...the dead drain gets none", a.pings, ap);
  }
  {
    // An automatic run whose transcription outlasts its budget says it was automatic.
    const { t, p } = await running();
    p.deliver({ type: "transcribing", videoId: V1, estimateS: 300 });
    await t.advance(2460000);
    check("a transcription that times out on an automatic run is labelled",
      t.toastText(), "Automatic summary failed: Transcribing is taking longer than "
        + "expected. It may still finish on the server — try again in a few minutes.");
  }
  {
    // Leaving while it's transcribing: stopped, not drained.
    const { t, p } = await running();
    p.deliver({ type: "transcribing", videoId: V1, estimateS: 300 });
    t.navigate(V2, { vid: V2, href: "/@other" });
    const stop = p.posted.find((m) => m.type === "stopTranscribe");
    check("leaving while transcribing stops it", !!stop, true);
    check("...for the run's own URL", stop && stop.url, watch(V1));
    check("...and disconnects", p.disconnected, true);
    check("...not drained", t.ctx.drains.size, 0);
    check("...no notice", t.toastText(), null);
  }
  {
    const { t, p } = await running();
    t.navigate(V2, { vid: V2, href: "/@other" });
    p.deliver({ type: "transcribing", videoId: V1, estimateS: 300 });
    const stop = p.posted.find((m) => m.type === "stopTranscribe");
    check("a drain that starts transcribing is stopped", !!stop, true);
    check("...for the run's own URL", stop && stop.url, watch(V1));
    check("...and ended", p.disconnected && t.ctx.drains.size === 0, true);
  }
  {
    // A click's run, with the panel closed, still says when it's done -- wherever
    // you've gone since.
    const t = setup({ block: { vid: V1 } });
    t.ctx.initAutoRun();
    t.invoke(V1);
    const p = t.ports[0];
    t.ctx.backgrounded = true;
    t.navigate(V2, { vid: V2, href: "/@other" });
    check("a manual run isn't dropped by navigating", p.disconnected, false);
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    p.deliver({ type: "result", payload: { title: "Mine", video_id: V1 } });
    check("...and still gives its notice", t.toastText(), "TL;DW summary ready — click to read");
  }
  {
    // Clicked mid-run, then navigated away: the run is the user's, so it stays.
    const { t, p } = await running();
    t.invoke(V1);
    t.navigate(V2, { vid: V2, href: "/@other" });
    check("a re-attached run survives navigating away", p.disconnected, false);
    check("...and isn't drained", t.ctx.drains.size, 0);
  }
  {
    const { t, p } = await running();
    p.deliver({ type: "result", payload: { title: "Done", video_id: V1 }, cached: false });
    t.ctx.host = null;
    t.invoke(V1);
    check("clicking after it finished shows it", t.shown.length, 1);
    check("...through the re-open path", t.shown[0].title, "Done");
    check("...not labelled cached when it was fresh", t.shown[0].cached, false);
    check("...with no new request", t.ports.length, 1);
  }

  // =============================================================== AC11 ====
  {
    const t = setup({ block: null });
    t.ctx.lastPayload = { title: "Last", video_id: V1 };
    t.ctx.showPausedNotice("hour");
    check("paused notice (hour)", t.toastText(),
      "TL;DW paused automatic summaries: 10 already this hour. Click the TL;DW button to summarize this one.");
    check("...offers nothing to read", t.toastText().includes("click to read"), false);
    const el = t.toast();
    el.dispatchEvent(D.event("click"));
    check("...and clicking it opens nothing", t.shown.length + t.offers.length, 0);
    check("...nor dismisses it", !!t.toast(), true);
    t.ctx.showPausedNotice("day");
    check("...once per page", t.toast(), el);
    const t2 = setup({ block: null });
    t2.ctx.showPausedNotice("day");
    check("paused notice (day)", t2.toastText(),
      "TL;DW paused automatic summaries: 40 already today. Click the TL;DW button to summarize this one.");
  }
  {
    // autoPaused from the worker ends the run with that notice.
    const { t, p } = await running();
    p.deliver({ type: "autoPaused", limit: "day" });
    check("autoPaused: the run ends", t.ctx.requestActive, false);
    check("...its port closes", p.disconnected, true);
    check("...and the notice names the limit", (t.toastText() || "").includes("40 already today"), true);
    const n = p.pings;
    await t.advance(60000);
    check("...no ping keeps going", p.pings, n);
  }
  {
    const t = setup({ block: null });
    t.ctx.pendingOffer = { offer: { x: 1 }, url: watch(V1), videoId: V1 };
    t.ctx.lastPayload = { title: "Last", video_id: V1 };
    t.ctx.toast("offer text", "offer");
    t.toast().dispatchEvent(D.event("click"));
    check("an offer notice opens the offer", JSON.stringify(t.offers), JSON.stringify([V1]));
    check("...not the summary", t.shown.length, 0);
    t.ctx.lastCached = false;
    t.ctx.toast("ready text", "ready");
    t.toast().dispatchEvent(D.event("click", { trusted: false }));
    check("an untrusted click does nothing", t.shown.length, 0);
    check("...and the notice stays", !!t.toast(), true);
    t.toast().dispatchEvent(D.event("click"));
    check("a ready notice opens the summary, not a stale offer", t.shown.length, 1);
    check("...with the right cached flag", t.shown[0].cached, false);
    check("...and is dismissed", t.toast(), null);
  }
  {
    const t = setup({ block: null });
    const fs = D.el(t.doc, "div", { id: "player" });
    t.doc.body.appendChild(fs);
    t.doc.fullscreenElement = fs;
    t.ctx.toast("in fullscreen", "notice");
    check("in fullscreen the notice is mounted in the fullscreen element",
      !!fs.querySelector("#tldw-toast"), true);
    check("...not hidden behind it in body", t.doc.body.childNodes.some((n) => n.id === "tldw-toast"), false);
    const err = setup({ block: null });
    err.ctx.toast("bad", "error");
    check("an error notice doesn't offer to read", err.toastText(), "bad");
  }

  // =============================================================== AC12 ====
  const panelFor = (t) => {
    // Attached, as mount() does: a browser won't focus anything in a detached tree.
    const hostEl = D.el(t.doc, "div", { id: "tldw-host" });
    t.doc.body.appendChild(hostEl);
    const root = new D.ShadowRoot(t.doc, hostEl);
    const slot = D.el(t.doc, "div", { class: "autorow", hidden: "" });
    root.appendChild(slot);
    const accept = D.el(t.doc, "button", { class: "accept" });
    root.appendChild(accept);
    t.accepted = 0;
    accept.addEventListener("click", () => { t.accepted++; });
    t.ctx.root = root;
    t.ctx.host = root.host;
    vm.runInContext("(function (root) {\n" + TRUST_GUARD + "\n})", t.ctx)(root);
    return { root, slot, accept };
  };
  {
    const t = setup();
    await t.advance(0);
    t.ctx.autoRun = true;
    t.ctx.lastPayload = { video_id: V1 };
    const { root, slot } = panelFor(t);
    t.ctx.autoRun = false;
    t.ctx.renderAutoRow();
    check("the option off: no row", slot.hidden, true);
    t.ctx.autoRun = true;
    t.block = setBlock(t, { vid: V3 });
    t.ctx.renderAutoRow();
    check("the channel unknown: no row", slot.hidden, true);
    // A link whose key wouldn't survive the worker's round-trip check: offering
    // "Always summarize" would only ever fail.
    t.block = setBlock(t, { vid: V1, href: "/@a%2525" });
    t.ctx.renderAutoRow();
    check("a key the worker would refuse: no row", slot.hidden, true);
    t.block = setBlock(t, { vid: V1, href: "/@new", name: "<img src=x onerror=alert(1)>" });
    t.ctx.autoChannels = [];
    t.ctx.renderAutoRow();
    check("on, channel known, not listed: the row shows", slot.hidden, false);
    const add = root.querySelector(".autoadd");
    check("...an Always summarize button", add && add.textContent,
      "⚡ Always summarize <img src=x onerror=alert(1)>");
    check("...with its title", add && add.getAttribute("title"),
      "Summarize new videos from this channel in the background when you open them");
    check("...a name with markup stays text", root.querySelectorAll("img").length, 0);
    const status = root.querySelector(".autostatus");
    check("...and a live status", status && status.getAttribute("aria-live"), "polite");

    // An untrusted click (page script reaching into the open shadow root) does nothing.
    add.dispatchEvent(D.event("click", { trusted: false }));
    add.click();
    check("untrusted clicks don't add the channel", t.messages.length, 0);

    let answer;
    t.reply = () => new Promise((r) => { answer = r; });
    add.dispatchEvent(D.event("click"));
    check("a real click asks the worker",
      JSON.stringify(t.messages[0]), JSON.stringify({ type: "channels:add", key: "@new",
        name: "<img src=x onerror=alert(1)>", href: "/@new" }));
    check("...the button is disabled while it's asked", add.disabled, true);
    answer({ ok: true, result: "ok" });
    await flush();
    const manage = root.querySelector(".automanage");
    check("ok: the row flips to listed", !!manage && !root.querySelector(".autoadd"), true);
    check("...the listed text", root.querySelector(".autoctl").textContent,
      "✓ <img src=x onerror=alert(1)> is summarized automatically · Manage");
    check("...focus moves to Manage", root.activeElement, manage);
    check("...which the page sees as focus on the panel", t.doc.activeElement, root.host);
    check("...announced", status.textContent,
      "Added. <img src=x onerror=alert(1)>'s videos will be summarized when you open them.");
    check("...by the same status node", root.querySelector(".autostatus"), status);
    // The worker's write lands as a storage change *after* the reply: the row must
    // not be rebuilt out from under the focused Manage button.
    t.changed.forEach((fn) => fn({ autoChannels: { newValue: [
      { key: "@new", name: "<img src=x onerror=alert(1)>" }] } }, "local"));
    check("a late storage update keeps focus on Manage", root.activeElement, manage);
    check("...and the same Manage button", root.querySelector(".automanage"), manage);
    manage.dispatchEvent(D.event("click", { trusted: false }));
    check("an untrusted click on Manage does nothing", t.messages.length, 1);
    manage.dispatchEvent(D.event("click"));
    check("Manage asks the worker to open Settings",
      JSON.stringify(t.messages[1]), JSON.stringify({ type: "openOptions" }));
  }
  for (const [label, reply, said] of [
    ["exists", () => Promise.resolve({ ok: true, result: "exists" }), "Chan is already on your list."],
  ]) {
    const t = setup();
    await t.advance(0);
    t.ctx.lastPayload = { video_id: V1 };
    const { root } = panelFor(t);
    t.ctx.autoChannels = [];
    t.ctx.renderAutoRow();
    t.reply = reply;
    root.querySelector(".autoadd").dispatchEvent(D.event("click"));
    await flush();
    check(`${label}: announced`, root.querySelector(".autostatus").textContent, said);
    check(`${label}: shown as listed`, !!root.querySelector(".automanage"), true);
  }
  for (const [label, reply, said] of [
    ["full", () => Promise.resolve({ ok: true, result: "full" }),
     "Your list is full (500 channels). Remove some in Settings."],
    ["refused", () => Promise.resolve({ ok: false }), "Couldn't add Chan. Try again."],
    ["a rejected message", () => Promise.reject(new Error("worker gone")), "Couldn't add Chan. Try again."],
    ["a throwing sendMessage", () => { throw new Error("context invalidated"); }, "Couldn't add Chan. Try again."],
  ]) {
    const t = setup();
    await t.advance(0);
    t.ctx.lastPayload = { video_id: V1 };
    const { root } = panelFor(t);
    t.ctx.autoChannels = [];
    t.ctx.renderAutoRow();
    t.reply = reply;
    const add = root.querySelector(".autoadd");
    add.dispatchEvent(D.event("click"));
    await flush();
    check(`${label}: announced`, root.querySelector(".autostatus").textContent, said);
    check(`${label}: the button is usable again`, add.disabled, false);
    check(`${label}: and keeps focus`, root.activeElement, add);
    check(`${label}: still not listed`, !!root.querySelector(".autoadd"), true);
  }
  {
    // Added in another tab: the open panel's row catches up.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(0);
    t.ctx.lastPayload = { video_id: V1 };
    const { root } = panelFor(t);
    t.ctx.autoChannels = [];
    t.ctx.renderAutoRow();
    check("not listed yet", !!root.querySelector(".autoadd"), true);
    root.querySelector(".autoadd").focus();
    t.changed.forEach((fn) => fn({ autoChannels: { newValue: [LISTED] } }, "local"));
    check("storage.onChanged re-renders the row as listed", !!root.querySelector(".automanage"), true);
    check("...and focus follows to the new control", root.activeElement,
      root.querySelector(".automanage"));
  }
  {
    // The shadow-root guard covers the panel's other controls too.
    const t = setup({ block: null });
    const { accept } = panelFor(t);
    accept.click();
    check("an untrusted click on the transcription accept does nothing", t.accepted, 0);
    accept.dispatchEvent(D.event("click"));
    check("...a real one works", t.accepted, 1);
  }

  // ================================================ AC4: the top of the file ====
  const whole = ({ withChannels = true, preset } = {}) => {
    const doc = new D.Document();
    const t = { doc, onMessage: [], sent: [] };
    const ctx = {
      document: doc, location: { href: watch(V1) }, URL,
      console: { info() {}, log() {}, warn() {}, error() {} },
      setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
      chrome: {
        runtime: { connect() {}, sendMessage: async (m) => { t.sent.push(m); return {}; },
                   onMessage: { addListener: (fn) => t.onMessage.push(fn) } },
        storage: { local: { get: async (d) => ({ ...d }), set: async () => {} },
                   onChanged: { addListener() {} } },
      },
    };
    ctx.window = ctx;
    ctx.addEventListener = () => {};
    vm.createContext(ctx);
    if (preset !== undefined) ctx.__tldwInit = preset;
    if (withChannels) vm.runInContext(CHANNELS, ctx);
    vm.runInContext(SRC, ctx);
    t.ctx = ctx;
    return t;
  };
  {
    const el = D.el(new D.Document(), "div", { id: "__tldwInit" });
    const t = whole({ preset: el });
    check("a page element named __tldwInit doesn't block init", t.onMessage.length, 1);
    check("...auto-run listens for navigations", t.doc.listenerCount("yt-navigate-finish"), 1);
    const again = whole({ preset: true });
    check("a real second injection does nothing", again.onMessage.length, 0);
    await flush();
    const fresh = whole();
    await flush();
    check("a fresh page clears the badge exactly once",
      JSON.stringify(fresh.sent.map((m) => m.type)), '["badge:clear"]');
    check("...and a second injection sends nothing", again.sent.length, 0);
  }
  {
    const t = whole({ withChannels: false });
    check("without channels.js: still initialises", t.onMessage.length, 1);
    check("...but no automatic summaries", t.doc.listenerCount("yt-navigate-finish"), 0);
    const broken = whole({ withChannels: false });
    void broken;
  }
  {
    const doc = new D.Document();
    const ctx = { document: doc, location: { href: watch(V1) }, URL,
      console: { info() {}, log() {}, warn() {}, error() {} },
      setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
      TLDW_CHANNELS: { notTheRealThing: true },
      chrome: { runtime: { connect() {}, onMessage: { addListener() {} } },
                storage: { local: { get: async (d) => ({ ...d }) }, onChanged: { addListener() {} } } } };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx);
    check("a TLDW_CHANNELS without channelKey counts as missing",
      doc.listenerCount("yt-navigate-finish"), 0);
  }

  // ============================================================ the badge ====
  const badgeMsgs = (t) => t.messages.map((m) => m.type).filter((x) => x.startsWith("badge:"));
  const result = (vid = V1, extra = {}) => ({ type: "result", payload: { title: "T", video_id: vid }, ...extra });
  {
    const { t, p } = await running();
    check("start: one clear, nothing else", JSON.stringify(badgeMsgs(t)), '["badge:clear"]');
    check("...no video id is ever sent", t.messages.some((m) => m.type.startsWith("badge:")
      && Object.keys(m).length !== 1), false);
    p.deliver(result());
    check("an automatic result with the panel closed sets the badge",
      JSON.stringify(badgeMsgs(t)), '["badge:clear","badge:set"]');
    check("...for its video", t.ctx.badgeVid, V1);
    check("...logged once", t.infos.filter((x) => x === "[TL;DW auto] badge " + V1).length, 1);
    // Opened from the notice: read, so cleared.
    t.toast().dispatchEvent(D.event("click"));
    check("opening it from the notice clears the badge", badgeMsgs(t).slice(-1)[0], "badge:clear");
    check("...and forgets it", t.ctx.badgeVid, null);
    t.ctx.showResult({ video_id: V1 });
    check("showing it again sends nothing more", badgeMsgs(t).length, 3);
  }
  {
    // A cache hit served to an automatic run badges too.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(3000);
    t.ports[0].deliver({ type: "probe", hit: true, payload: { title: "C", video_id: V1 } });
    check("an automatic cache hit sets the badge", badgeMsgs(t).includes("badge:set"), true);
  }
  {
    // Opened with the toolbar instead.
    const { t, p } = await running();
    p.deliver(result());
    t.ctx.host = null;
    t.invoke(V1);
    check("opening it with the toolbar clears the badge", badgeMsgs(t).slice(-1)[0], "badge:clear");
  }
  {
    const { t, p } = await running();
    p.deliver(result());
    t.ctx.showResult({ video_id: V2, title: "other" });
    check("showing another video's summary keeps the badge", t.ctx.badgeVid, V1);
    check("...and sends nothing", badgeMsgs(t).slice(-1)[0], "badge:set");
  }
  for (const [label, dest] of [["another video", V2], ["a page that isn't a video", null]]) {
    const { t, p } = await running();
    p.deliver(result());
    t.navigate(dest, dest ? { vid: dest, href: "/@other" } : null);
    check(`navigating to ${label} clears the badge`, badgeMsgs(t).slice(-1)[0], "badge:clear");
    check(`...and forgets it (${label})`, t.ctx.badgeVid, null);
  }
  {
    // Leaving the page, and coming back from the back/forward cache.
    const { t, p } = await running();
    p.deliver(result());
    const n = badgeMsgs(t).length;
    t.fire("pagehide", { trusted: false });
    t.fire("pageshow", { persisted: true, trusted: false });
    check("fake pagehide/pageshow events do nothing", badgeMsgs(t).length, n);
    t.fire("pagehide", { persisted: false });
    check("leaving the page clears the badge", badgeMsgs(t).slice(-1)[0], "badge:clear");
    check("...but keeps what it was for", t.ctx.badgeVid, V1);
    t.fire("pageshow", { persisted: false });
    check("an ordinary pageshow doesn't set it again", badgeMsgs(t).length, n + 1);
    t.fire("pagehide", { persisted: true });
    t.fire("pageshow", { persisted: true });
    check("restored from the back/forward cache: set again", badgeMsgs(t).slice(-1)[0], "badge:set");
  }
  {
    // With nothing waiting, nothing is sent.
    const { t } = await running();
    const n = badgeMsgs(t).length;
    t.ctx.showResult({ video_id: V1 });
    t.fire("pagehide");
    t.navigate(V2, { vid: V2, href: "/@other" });
    check("no badge: showResult, pagehide and navigating send nothing", badgeMsgs(t).length, n);
  }
  {
    // A second automatic result, for the video navigated to, takes over.
    const { t, p } = await running({ stored: { autoRun: true, autoChannels: [LISTED, { key: "@other", name: "O" }] } });
    p.deliver(result());
    t.navigate(V2, { vid: V2, href: "/@other" });
    await t.advance(3000);
    const p2 = t.ports[t.ports.length - 1];
    p2.deliver({ type: "probe", hit: false });
    await t.advance(0);
    p2.deliver(result(V2));
    check("the next video's automatic result is the badge's now", t.ctx.badgeVid, V2);
  }
  {
    // A new automatic result is the badge's, whatever was there before (state set
    // directly: no real flow leaves an old one behind, as navigating clears it).
    const { t, p } = await running();
    t.ctx.badgeVid = V3;
    p.deliver(result());
    check("a new automatic result takes the badge", t.ctx.badgeVid, V1);
    t.ctx.showResult({ video_id: V1 });
    check("...and opening it clears it", badgeMsgs(t).slice(-1)[0], "badge:clear");
  }
  // Nothing else badges.
  const noBadge = async (label, act, opts) => {
    const { t, p } = await running(opts);
    await act(t, p);
    check(`no badge: ${label}`, badgeMsgs(t).includes("badge:set"), false);
  };
  await noBadge("an error", async (t, p) => p.deliver({ type: "error", error: "x" }));
  await noBadge("needsTranscription", async (t, p) => p.deliver({ type: "needsTranscription", offer: {} }));
  await noBadge("autoPaused", async (t, p) => p.deliver({ type: "autoPaused", limit: "hour" }));
  await noBadge("a lost worker connection", async (t, p) => p.drop());
  await noBadge("a transcription that times out", async (t, p) => {
    p.deliver({ type: "transcribing", videoId: V1, estimateS: 300 });
    await t.advance(2460000);
  });
  await noBadge("a run made manual by a click", async (t, p) => {
    t.invoke(V1);
    t.ctx.backgrounded = true;
    p.deliver(result());
  });
  await noBadge("a result shown in an open panel", async (t, p) => {
    t.ctx.backgrounded = false;          // no real flow gets here with run.auto true
    p.deliver(result());
  });
  await noBadge("a drained run's result", async (t, p) => {
    t.navigate(V2, { vid: V2, href: "/@other" });
    p.deliver(result());
  });
  {
    // Dropped before its summarize went out: nothing to announce, ever.
    const t = setup();
    let release;
    t.ctx.collectPageTranscript = () => new Promise((r) => { release = r; });
    t.ctx.initAutoRun();
    await t.advance(3000);
    const p = t.ports[0];
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    t.navigate(V2, { vid: V2, href: "/@other" });
    release({ cues: [] });
    await flush();
    p.deliver(result());
    check("no badge: a run dropped before its summarize", badgeMsgs(t).includes("badge:set"), false);
  }
  {
    // A manual run that finishes behind a closed panel: its notice, no badge.
    const t = setup({ stored: { autoRun: false, autoChannels: [] } });
    await t.advance(0);
    t.invoke(V1);
    const p = t.ports[0];
    t.ctx.backgrounded = true;
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    p.deliver(result());
    check("no badge: a manual run", badgeMsgs(t).includes("badge:set"), false);
  }
  {
    // Sends never throw: a reloaded extension throws outright; no reply rejects.
    const { t, p } = await running();
    t.reply = () => { throw new Error("Extension context invalidated."); };
    let threw = false;
    try { p.deliver(result()); } catch (_) { threw = true; }
    check("a throwing sendMessage doesn't escape", threw, false);
    t.reply = () => Promise.reject(new Error("The message port closed before a response was received."));
    t.ctx.showResult({ video_id: V1 });
    await flush();
    check("...nor does a rejecting one (no unhandled rejection)", unhandled.length, 0);
  }
  // ======================================================= read, or not ====
  const servedMsgs = (t) => t.messages.filter((m) => m.type === "usage:served");
  {
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(3000);
    const probe = t.ports[0].posted.find((m) => m.type === "probeCache");
    check("an automatic run's probe says so", probe && probe.auto, true);
  }
  {
    const { t, p } = await running();
    const sent = p.posted.find((m) => m.type === "summarize");
    check("...and its summarize", sent && sent.auto, true);
    p.deliver(result());
    check("an automatic result isn't a read yet", servedMsgs(t).length, 0);
    t.toast().dispatchEvent(D.event("click"));
    check("opening it is: one usage:served, for its video",
      JSON.stringify(servedMsgs(t)), JSON.stringify([{ type: "usage:served", video_id: V1 }]));
    t.ctx.showResult({ video_id: V1 });
    check("...once per page", servedMsgs(t).length, 1);
  }
  {
    // Served from the cache to an automatic run: opened later, still a read.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(3000);
    t.ports[0].deliver({ type: "probe", hit: true, payload: { title: "C", video_id: V1 } });
    t.ctx.host = null;
    t.invoke(V1);
    check("an automatic cache hit, opened: a read", servedMsgs(t).length, 1);
  }
  {
    // Clicked after the summarize went out as automatic: the server logged it so,
    // and the panel showing it is the read.
    const { t, p } = await running();
    t.invoke(V1);
    p.deliver(result());
    check("re-attached after the send, shown in the panel: a read",
      JSON.stringify(servedMsgs(t)), JSON.stringify([{ type: "usage:served", video_id: V1 }]));
  }
  {
    // Clicked after the probe went out as automatic, before the summarize.
    const t = setup();
    t.ctx.initAutoRun();
    await t.advance(3000);
    const p = t.ports[0];
    t.invoke(V1);
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    const sent = p.posted.find((m) => m.type === "summarize");
    check("a click before the summarize: it goes out as the user's", !!sent && !("auto" in sent), true);
    p.deliver(result());
    check("...and showing it still counts the probe's automatic start as read",
      servedMsgs(t).length, 1);
  }
  {
    // A run that was only ever the user's.
    const t = setup({ stored: { autoRun: false, autoChannels: [] } });
    await t.advance(0);
    t.invoke(V1);
    const p = t.ports[0];
    const probe = p.posted.find((m) => m.type === "probeCache");
    check("a manual run's probe carries no auto", !!probe && !("auto" in probe), true);
    p.deliver({ type: "probe", hit: false });
    await t.advance(0);
    p.deliver(result());
    check("a manual run's result sends no read", servedMsgs(t).length, 0);
  }
  {
    const { t, p } = await running();
    p.deliver(result());
    t.ctx.showResult({ video_id: V2, title: "other" });
    check("showing another video sends no read", servedMsgs(t).length, 0);
  }
  check("the real showResult tells the read too",
    /badgeShown\(p\);\s*servedShown\(p\);/.test(SRC), true);
  check("the real showResult starts by telling the badge",
    /function showResult\(p, cached\) \{\s*badgeShown\(p\);/.test(SRC), true);

  // ---------------------------------------------- keyboard: real keys only ----
  {
    const ONKEY = between("  function onKey(e) {", "\n  function close(opts) {");
    const ctx = { host: {}, root: { activeElement: null, querySelectorAll: () => [] },
      sent: 0, closed: 0, fromModal: () => true,
      submitQuestion() { ctx.sent++; }, close() { ctx.closed++; } };
    vm.createContext(ctx);
    vm.runInContext(ONKEY + "\nthis.onKey = onKey;", ctx);
    const box = { tagName: "TEXTAREA" };
    const key = (isTrusted, k) => ({ isTrusted, key: k, shiftKey: false,
      composedPath: () => [box], target: box, stopPropagation() {}, preventDefault() {} });
    ctx.onKey(key(false, "Enter"));
    check("a synthetic Enter in the question box sends nothing", ctx.sent, 0);
    ctx.onKey(key(false, "Escape"));
    check("a synthetic Escape doesn't close the panel", ctx.closed, 0);
    ctx.onKey(key(true, "Enter"));
    check("a real Enter sends the question", ctx.sent, 1);
  }

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok " + CHECKS.n + " checks");
})().catch((e) => { console.error("CRASH: " + (e && e.stack || e)); process.exit(1); });
