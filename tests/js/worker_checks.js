// ALL of extension/background.js, run in a vm context against a fake `chrome`:
// listeners are recorded, storage/permissions/scripting keep real state and can be
// held open or made to fail. Covers loading (with and without importScripts, and
// with channels.js broken), the content-script registration, the auto-run budget,
// and the single writer of the channel list.
const fs = require("fs");
const vm = require("vm");
const WORKER = fs.readFileSync(process.argv[2], "utf8");
const CHANNELS = fs.readFileSync(process.argv[3], "utf8");

const failures = [];
const CHECKS = { n: 0 };
const check = (name, got, want) => {
  CHECKS.n++;
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};
const tick = () => new Promise((r) => setImmediate(r));
// A promise left rejected is a named failure, not just node's exit code.
process.on("unhandledRejection", (e) => { failures.push("unhandled rejection: " + e); });
const flush = async (n = 12) => { for (let i = 0; i < n; i++) await tick(); };
const ORIGIN = "https://www.youtube.com/*";
const ID = "AAAAAAAAAAA";
const WATCH = "https://www.youtube.com/watch?v=" + ID;

function gate() {
  let open;
  const p = new Promise((r) => { open = r; });
  return { p, open };
}

function event() {
  const listeners = [];
  return { listeners, addListener: (fn) => listeners.push(fn),
           fire: (...a) => listeners.map((fn) => fn(...a)) };
}

// mode: "chrome" (importScripts runs channels.js), "firefox" (no importScripts;
// channels.js already ran), "broken" (importScripts throws: no TLDW_CHANNELS).
function worker({ mode = "chrome", stored = {}, granted = false, registered = [],
                  fetchImpl } = {}) {
  const w = {
    stored: { ...stored }, granted, registered: registered.slice(),
    calls: [], sets: [], warnings: [], infos: [], fetches: [],
    holdGet: null, failGet: 0, failSet: 0, holdRegister: null, failRegister: null,
    executed: [], tabMessages: [], optionsOpened: 0, badge: [], badgeMode: null,
  };
  const ev = {
    onClicked: event(), onConnect: event(), onMessage: event(), onInstalled: event(),
    onStartup: event(), storageChanged: event(), permAdded: event(), permRemoved: event(),
  };
  w.ev = ev;
  const storageLocal = {
    async get(defaults) {
      w.calls.push("get");
      if (w.failGet > 0) { w.failGet--; throw new Error("storage get failed"); }
      if (w.holdGet) { const g = w.holdGet; w.holdGet = null; await g.p; }
      const out = {};
      for (const [k, v] of Object.entries(defaults || {})) {
        out[k] = Object.prototype.hasOwnProperty.call(w.stored, k)
          ? JSON.parse(JSON.stringify(w.stored[k])) : v;
      }
      return out;
    },
    async set(obj) {
      w.calls.push("set");
      if (w.failSet > 0) { w.failSet--; throw new Error("storage set failed"); }
      w.sets.push(JSON.parse(JSON.stringify(obj)));
      const changes = {};
      for (const [k, v] of Object.entries(obj)) {
        changes[k] = { oldValue: w.stored[k], newValue: v };
        w.stored[k] = JSON.parse(JSON.stringify(v));
      }
      ev.storageChanged.fire(changes, "local");
    },
  };
  const chrome = {
    runtime: {
      id: "tldw-ext-id",
      onConnect: ev.onConnect, onMessage: ev.onMessage, onInstalled: ev.onInstalled,
      onStartup: ev.onStartup,
      openOptionsPage: async () => { w.optionsOpened++; },
      getManifest: () => ({ action: { default_title: "FAKE default" } }),
    },
    // The badge setters record, and can be made to throw, reject, or be missing.
    action: { onClicked: ev.onClicked,
      ...Object.fromEntries(["setBadgeText", "setBadgeBackgroundColor", "setBadgeTextColor",
                             "setTitle"].map((name) => [name, (arg) => {
        w.badge.push([name, arg]);
        if (w.badgeMode === "throw") throw new Error("Invalid argument");
        if (w.badgeMode === "reject") return Promise.reject(new Error("No tab with id: 7."));
        return Promise.resolve();
      }])) },
    tabs: { sendMessage: async (tabId, msg) => { w.tabMessages.push({ tabId, msg }); } },
    storage: { local: storageLocal, onChanged: ev.storageChanged },
    permissions: {
      contains: async ({ origins }) => w.granted && origins.length === 1 && origins[0] === ORIGIN,
      onAdded: ev.permAdded, onRemoved: ev.permRemoved,
    },
    scripting: {
      executeScript: async (opts) => {
        w.executed.push(opts);
        if (w.failChannelsInject && opts.files.includes("channels.js")) {
          throw new Error("Could not load file: 'channels.js'.");
        }
      },
      async getRegisteredContentScripts({ ids } = {}) {
        w.calls.push("getRegistered");
        return w.registered.filter((s) => !ids || ids.includes(s.id)).map((s) => ({ ...s }));
      },
      async registerContentScripts(list) {
        w.calls.push("register");
        if (w.holdRegister) { const g = w.holdRegister; w.holdRegister = null; await g.p; }
        if (w.failRegister) { const e = w.failRegister; w.failRegister = null; throw e; }
        for (const s of list) {
          if (w.registered.some((r) => r.id === s.id)) {
            throw new Error(`Duplicate script ID '${s.id}'`);
          }
          w.registered.push({ ...s });
        }
      },
      async unregisterContentScripts({ ids }) {
        w.calls.push("unregister");
        const before = w.registered.length;
        w.registered = w.registered.filter((s) => !ids.includes(s.id));
        if (w.registered.length === before) throw new Error(`Nonexistent script ID '${ids[0]}'`);
      },
    },
  };
  w.chrome = chrome;
  const ctx = {
    chrome, URL, TextDecoder, TextEncoder, AbortController, atob, btoa,
    setTimeout, clearTimeout, setInterval, clearInterval,
    console: {
      log() {}, error() {},
      warn: (...a) => w.warnings.push(a.map(String).join(" ")),
      info: (...a) => w.infos.push(a.map(String).join(" ")),
    },
    fetch: async (url, init) => {
      w.fetches.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
      return (fetchImpl || resultResponse)(url, init);
    },
  };
  vm.createContext(ctx);
  if (mode === "chrome") ctx.importScripts = (name) => {
    if (name !== "channels.js") throw new Error("unexpected import " + name);
    vm.runInContext(CHANNELS, ctx);
  };
  if (mode === "broken") ctx.importScripts = () => { throw new Error("NetworkError: channels.js"); };
  if (mode === "firefox") vm.runInContext(CHANNELS, ctx);
  try {
    vm.runInContext(WORKER, ctx);
    w.loadError = null;
  } catch (e) {
    w.loadError = String(e);
  }
  w.ctx = ctx;
  // A message as the extension's own content script would send it.
  w.message = (msg, sender = { id: "tldw-ext-id" }) => {
    const res = { value: undefined, got: false };
    const ret = ev.onMessage.listeners.map((fn) => fn(msg, sender, (v) => { res.value = v; res.got = true; }));
    res.returned = ret[0];
    return res;
  };
  // A summarize over a fresh port, as content.js sends it.
  w.summarize = (msg) => {
    const port = { name: "tldw", posted: [], listeners: [],
      onMessage: { addListener: (fn) => port.listeners.push(fn) },
      onDisconnect: { addListener() {} },
      postMessage: (m) => port.posted.push(m) };
    ev.onConnect.fire(port);
    port.listeners.forEach((fn) => fn({ type: "summarize", url: WATCH, videoId: ID,
                                          page: { page_v: 2 }, ...msg }));
    return port;
  };
  return w;
}

function resultResponse() {
  const line = JSON.stringify({ type: "result", video_id: ID, title: "T", summary_md: "s" }) + "\n";
  let sent = false;
  return {
    ok: true, status: 200,
    body: { getReader: () => ({ read: async () => {
      if (sent) return { done: true };
      sent = true;
      return { done: false, value: new TextEncoder().encode(line) };
    } }) },
  };
}

const types = (port) => port.posted.map((m) => m.type);
const NOW = () => Date.now();

(async () => {
  // ------------------------------------------------------------ loading ----
  for (const mode of ["chrome", "firefox", "broken"]) {
    const w = worker({ mode });
    check(`${mode}: loads without throwing`, w.loadError, null);
    for (const [name, e] of Object.entries(w.ev)) {
      check(`${mode}: ${name} has a listener attached at load`, e.listeners.length >= 1, true);
    }
  }
  {
    const w = worker({ mode: "firefox" });
    check("firefox: the helpers are there", typeof w.ctx.TLDW_CHANNELS, "object");
    const w2 = worker({ mode: "chrome" });
    check("chrome: importScripts loaded the helpers", typeof w2.ctx.TLDW_CHANNELS, "object");
    const b = worker({ mode: "broken" });
    check("broken: no helpers", b.ctx.TLDW_CHANNELS, undefined);
    check("broken: the failure is logged", b.warnings.some((s) => s.includes("channels.js failed to load")), true);
  }

  // --------------------------------------------------- the toolbar click ----
  {
    const w = worker();
    await w.ev.onClicked.listeners[0]({ id: 7, url: WATCH });
    check("the toolbar injects channels.js before content.js",
      JSON.stringify(w.executed[0].files), JSON.stringify(["channels.js", "content.js"]));
    check("...then invokes it", w.tabMessages[0] && w.tabMessages[0].msg.type, "TLDW_INVOKE");
  }
  {
    // channels.js missing from a broken build: the click still summarizes.
    const w = worker();
    w.failChannelsInject = true;
    await w.ev.onClicked.listeners[0]({ id: 7, url: WATCH });
    check("a failed injection is retried with content.js alone",
      JSON.stringify(w.executed.map((e) => e.files)),
      JSON.stringify([["channels.js", "content.js"], ["content.js"]]));
    check("...and the click still invokes it",
      w.tabMessages[0] && w.tabMessages[0].msg.type, "TLDW_INVOKE");
  }

  // ------------------------------------------------------ registration ----
  {
    const w = worker();
    w.ev.onInstalled.fire({ reason: "install" });
    await flush();
    check("a fresh install registers nothing", w.registered.length, 0);
    check("...and never tries to", w.calls.includes("register"), false);
  }
  {
    // On and granted -> registered, with the exact shape the plan specifies.
    const w = worker({ stored: { autoRun: true }, granted: true });
    w.ev.onStartup.fire();
    await flush();
    check("onStartup actually syncs: registered", w.registered.length, 1);
    const s = w.registered[0] || {};
    check("id", s.id, "tldw-auto");
    check("matches", JSON.stringify(s.matches), JSON.stringify([ORIGIN]));
    check("js: channels.js then content.js", JSON.stringify(s.js), JSON.stringify(["channels.js", "content.js"]));
    check("allFrames: false (no live-chat frames)", s.allFrames, false);
    check("runAt", s.runAt, "document_idle");
    check("persistAcrossSessions", s.persistAcrossSessions, true);
    check("a register is logged", w.infos.some((x) => x.includes("[TL;DW auto] register")), true);
  }
  {
    const w = worker({ stored: { autoRun: true }, granted: false });
    w.ev.onStartup.fire(); await flush();
    check("on but not granted: not registered", w.registered.length, 0);
    const w2 = worker({ stored: { autoRun: false }, granted: true });
    w2.ev.onStartup.fire(); await flush();
    check("granted but off: not registered", w2.registered.length, 0);
  }
  {
    // Turning it on fires both events together; with register held open, the two
    // syncs would overlap if they weren't serialized.
    const w = worker({ granted: false });
    w.holdRegister = gate();
    const held = w.holdRegister;
    w.granted = true;
    await w.ctx.chrome.storage.local.set({ autoRun: true });   // fires storage.onChanged
    w.ev.permAdded.fire({ origins: [ORIGIN] });
    await flush();
    held.open();
    await flush();
    check("two syncs in flight leave one registration", w.registered.length, 1);
    check("...with exactly one register call",
      w.calls.filter((c) => c === "register").length, 1);
    check("...and nothing thrown or logged as failed",
      w.warnings.filter((x) => /failed/.test(x)).length, 0);
  }
  {
    // Turning it off, or revoking access, unregisters.
    const w = worker({ stored: { autoRun: true }, granted: true,
                       registered: [{ id: "tldw-auto" }] });
    await w.ctx.chrome.storage.local.set({ autoRun: false });
    await flush();
    check("off -> unregistered", w.registered.length, 0);
    const w2 = worker({ stored: { autoRun: true }, granted: true,
                        registered: [{ id: "tldw-auto" }] });
    w2.granted = false;
    w2.ev.permRemoved.fire({ origins: [ORIGIN] });
    await flush();
    check("access revoked -> unregistered", w2.registered.length, 0);
    check("an unregister is logged", w2.infos.some((x) => x.includes("unregister")), true);
  }
  {
    // A register that fails for an unexpected reason: logged, nothing left
    // registered, and the next sync still runs.
    const w = worker({ stored: { autoRun: true }, granted: true });
    w.failRegister = new Error("boom: quota");
    w.ev.onStartup.fire();
    await flush();
    check("a failed register leaves nothing registered", w.registered.length, 0);
    check("...and is logged", w.warnings.some((x) => x.includes("register failed")), true);
    w.ev.permAdded.fire({ origins: [ORIGIN] });
    await flush();
    check("...and doesn't jam the chain: the next sync registers", w.registered.length, 1);
  }
  {
    // A sync step that rejects outright (storage failing) mustn't stop later ones.
    const w = worker({ stored: { autoRun: true }, granted: true });
    w.failGet = 1;
    w.ev.onStartup.fire();
    await flush();
    w.ev.onStartup.fire();
    await flush();
    check("a rejected sync step doesn't stop the next", w.registered.length, 1);
  }
  {
    // After an update the registration is redone, so it names this build's files.
    const w = worker({ stored: { autoRun: true }, granted: true,
                       registered: [{ id: "tldw-auto", js: ["content.js"] }] });
    w.ev.onInstalled.fire({ reason: "update" });
    await flush();
    const order = w.calls.filter((c) => c === "register" || c === "unregister");
    check("onInstalled: unregister, then register", JSON.stringify(order),
      JSON.stringify(["unregister", "register"]));
    check("...with the current files", JSON.stringify(w.registered[0].js),
      JSON.stringify(["channels.js", "content.js"]));
    const off = worker({ stored: { autoRun: false }, granted: true,
                         registered: [{ id: "tldw-auto" }] });
    off.ev.onInstalled.fire({ reason: "update" });
    await flush();
    check("onInstalled while off: unregistered and not re-registered", off.registered.length, 0);
  }
  {
    const b = worker({ mode: "broken", stored: { autoRun: true }, granted: true,
                       registered: [{ id: "tldw-auto" }] });
    b.ev.onStartup.fire();
    await flush();
    check("without the helpers, sync unregisters", b.registered.length, 0);
  }

  // ------------------------------------------------------------- budget ----
  {
    const w = worker({ stored: { token: "tok" } });
    const p = w.summarize({ auto: true });
    await flush();
    check("an auto summarize under budget is fetched", w.fetches.length, 1);
    check("...and recorded once", (w.stored.autoRunLog || []).length, 1);
    check("...the result is delivered", types(p).includes("result"), true);
    // The same video again: the worker's own cache answers -- free, not counted.
    const p2 = w.summarize({ auto: true });
    await flush();
    check("a worker-cache hit is served", types(p2).includes("result"), true);
    check("...without a fetch", w.fetches.length, 1);
    check("...and costs no budget", w.stored.autoRunLog.length, 1);
  }
  {
    const w = worker({ stored: { token: "" } });
    const p = w.summarize({ auto: true });
    await flush();
    check("no token: an error", types(p)[0], "error");
    check("...that costs no budget", w.stored.autoRunLog, undefined);
  }
  {
    const w = worker({ stored: { token: "tok" } });
    const ids = ["BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD", "EEEEEEEEEEE"];
    for (const [i, auto] of ["true", 1, {}, undefined].entries()) {
      w.summarize({ auto, videoId: ids[i], url: WATCH.replace(ID, ids[i]) });
      await flush();
    }
    check("only auto === true counts: \"true\", 1, {} and none are manual",
      w.stored.autoRunLog, undefined);
    check("...and manual runs are fetched", w.fetches.length, 4);
  }
  {
    // 10 this hour: the next auto run is paused, says which limit, and costs nothing.
    const now = NOW();
    const w = worker({ stored: { token: "tok",
      autoRunLog: Array.from({ length: 10 }, (_, i) => now - 1000 * (i + 1)) } });
    const p = w.summarize({ auto: true });
    await flush();
    check("over the hourly budget: autoPaused", JSON.stringify(p.posted),
      JSON.stringify([{ type: "autoPaused", limit: "hour" }]));
    check("...with no fetch", w.fetches.length, 0);
    check("...logged", w.infos.some((x) => x.includes("paused")), true);
    // A manual run is never limited.
    const m = w.summarize({ videoId: "BBBBBBBBBBB" });
    await flush();
    check("a manual run is never limited", types(m).includes("result"), true);
  }
  {
    const now = NOW();
    const w = worker({ stored: { token: "tok",
      autoRunLog: Array.from({ length: 40 }, (_, i) => now - 2 * 3600e3 - 1000 * i) } });
    const p = w.summarize({ auto: true });
    await flush();
    check("over the daily budget: autoPaused with the day's limit",
      p.posted[0] && p.posted[0].limit, "day");
  }
  {
    // Two auto runs at 9 this hour, the first storage read held open: exactly one
    // gets through.
    const now = NOW();
    const w = worker({ stored: { token: "tok",
      autoRunLog: Array.from({ length: 9 }, (_, i) => now - 1000 * (i + 1)) } });
    const g = gate();
    // getSettings reads first; hold the budget's read instead, once the settings
    // reads are done -- simplest: hold every get briefly via a wrapper.
    const realGet = w.ctx.chrome.storage.local.get;
    let held = false;
    w.ctx.chrome.storage.local.get = async (d) => {
      if (!held && d && "autoRunLog" in d) { held = true; await g.p; }
      return realGet(d);
    };
    const a = w.summarize({ auto: true, videoId: "AAAAAAAAAAA" });
    const b = w.summarize({ auto: true, videoId: "BBBBBBBBBBB",
                            url: WATCH.replace(ID, "BBBBBBBBBBB") });
    await flush();
    g.open();
    await flush();
    const allowed = [a, b].filter((p) => types(p).includes("result")).length;
    const paused = [a, b].filter((p) => types(p).includes("autoPaused")).length;
    check("two in flight at 9: exactly one allowed", allowed, 1);
    check("...and the other paused", paused, 1);
    check("...one fetch", w.fetches.length, 1);
    check("...the log now holds 10", w.stored.autoRunLog.length, 10);
  }
  {
    // Every write prunes the log: old and future entries don't survive it.
    const now = NOW();
    const w = worker({ stored: { token: "tok",
      autoRunLog: [now - 30 * 3600e3, now + 3600e3, "x", now - 5000] } });
    w.summarize({ auto: true });
    await flush();
    check("the log is pruned on write", w.stored.autoRunLog.length, 2);
    check("...keeping only the last day's real starts",
      w.stored.autoRunLog.every((t) => typeof t === "number" && t <= Date.now() && t > now - 86400e3),
      true);
  }
  {
    // Storage failing during the budget check pauses -- fails closed.
    const w = worker({ stored: { token: "tok" } });
    const realGet = w.ctx.chrome.storage.local.get;
    w.ctx.chrome.storage.local.get = async (d) => {
      if (d && "autoRunLog" in d) throw new Error("storage unavailable");
      return realGet(d);
    };
    const p = w.summarize({ auto: true });
    await flush();
    check("a storage failure fails closed", types(p)[0], "autoPaused");
    check("...with no fetch", w.fetches.length, 0);
    // ...and the chain isn't jammed by it.
    w.ctx.chrome.storage.local.get = realGet;
    const q = w.summarize({ auto: true });
    await flush();
    check("...and the next auto run goes through", types(q).includes("result"), true);
  }
  {
    const b = worker({ mode: "broken", stored: { token: "tok" } });
    const p = b.summarize({ auto: true });
    await flush();
    check("without the helpers, an auto run is paused", types(p)[0], "autoPaused");
    check("...with no fetch", b.fetches.length, 0);
    const m = b.summarize({});
    await flush();
    check("...while a manual run still works", types(m).includes("result"), true);
  }

  // ------------------------------------------------------- the badge ----
  const TAB = { id: "tldw-ext-id", tab: { id: 7 }, frameId: 0 };
  {
    const w = worker();
    const r = w.message({ type: "badge:set" }, TAB);
    check("badge:set returns undefined (no reply, stays synchronous)", r.returned, undefined);
    await flush();
    check("...and never replies", r.got, false);
    check("set: text, colour, text colour and title, all for that tab",
      JSON.stringify(w.badge), JSON.stringify([
        ["setBadgeText", { tabId: 7, text: "✓" }],
        ["setBadgeBackgroundColor", { tabId: 7, color: "#137333" }],
        ["setBadgeTextColor", { tabId: 7, color: "#FFFFFF" }],
        ["setTitle", { tabId: 7, title: "TL;DW summarized this video for you — click to read" }]]));
    w.badge.length = 0;
    w.message({ type: "badge:clear" }, TAB);
    check("clear: no text, the manifest's own title",
      JSON.stringify(w.badge), JSON.stringify([
        ["setBadgeText", { tabId: 7, text: "" }],
        ["setTitle", { tabId: 7, title: "FAKE default" }]]));
    w.badge.length = 0;
    w.message({ type: "badge:set", tabId: 99 }, TAB);
    check("a tabId in the message is never used", w.badge.every(([, a]) => a.tabId === 7), true);
    check("...the message is still handled", w.badge.length, 4);
  }
  for (const [label, sender] of [
    ["no tab (the options page)", { id: "tldw-ext-id" }],
    ["frameId 0 but no tab", { id: "tldw-ext-id", frameId: 0 }],
    ["a sub-frame", { id: "tldw-ext-id", tab: { id: 7 }, frameId: 3 }],
    ["tab id -1", { id: "tldw-ext-id", tab: { id: -1 }, frameId: 0 }],
    ["a tab id that isn't an integer", { id: "tldw-ext-id", tab: { id: 1.5 }, frameId: 0 }],
    ["another extension", { id: "other-ext", tab: { id: 7 }, frameId: 0 }],
  ]) {
    const w = worker();
    w.message({ type: "badge:set" }, sender);
    check(`badge ignored from ${label}`, w.badge.length, 0);
  }
  {
    const w = worker();
    delete w.chrome.action.setBadgeTextColor;
    w.message({ type: "badge:set" }, TAB);
    check("without setBadgeTextColor the other three still run",
      JSON.stringify(w.badge.map(([n]) => n)),
      JSON.stringify(["setBadgeText", "setBadgeBackgroundColor", "setTitle"]));
  }
  for (const mode of ["throw", "reject"]) {
    const w = worker();
    w.badgeMode = mode;
    let threw = false;
    try { w.message({ type: "badge:set" }, TAB); w.message({ type: "badge:clear" }, TAB); }
    catch (_) { threw = true; }
    await flush();
    check(`action calls that ${mode} don't escape`, threw, false);
    check(`...every call was still attempted (${mode})`, w.badge.length, 6);
  }
  {
    // The tab check lives in the badge branch only: the options page has no tab.
    const w = worker();
    const r = w.message({ type: "channels:add", key: "@chan", name: "Chan" });
    await flush();
    check("channels:add from a tab-less sender still works", r.value && r.value.ok, true);
  }

  // ------------------------------------------------- the channel list ----
  const KEY = "@chan";
  {
    const w = worker();
    const r = w.message({ type: "channels:add", key: KEY, name: "  Chan  ", href: "/@Chan" });
    check("the listener answers synchronously with true (not async)", r.returned, true);
    await flush();
    check("add: ok", JSON.stringify(r.value), JSON.stringify({ ok: true, result: "ok" }));
    const list = w.stored.autoChannels;
    check("...stored once", list.length, 1);
    check("...name trimmed", list[0].name, "Chan");
    check("...href kept", list[0].href, "/@Chan");
    check("...addedAt set", typeof list[0].addedAt, "number");
    const again = w.message({ type: "channels:add", key: KEY, name: "Chan" });
    await flush();
    check("adding it again: exists", again.value.result, "exists");
    check("...not duplicated", w.stored.autoChannels.length, 1);
    const long = w.message({ type: "channels:add", key: "@long", name: "n".repeat(300) });
    await flush();
    check("the name is capped at 100",
      w.stored.autoChannels.find((c) => c.key === "@long").name.length, 100);
    const farHref = w.message({ type: "channels:add", key: "@far", name: "Far",
                                href: "/@far" + "x".repeat(300) });
    await flush();
    check("an href over 300 is not stored on add", farHref.value.result, "ok");
    check("...the entry is, without it",
      "href" in w.stored.autoChannels.find((c) => c.key === "@far"), false);
    const bad = w.message({ type: "channels:add", key: "Not A Key", name: "x" });
    await flush();
    check("an invalid key is refused", bad.value.ok, false);
    const upper = w.message({ type: "channels:add", key: "@Upper", name: "x" });
    await flush();
    check("a key that fails the round trip is refused", upper.value.ok, false);
    const noname = w.message({ type: "channels:add", key: "@x", name: 5 });
    await flush();
    check("a non-string name is refused", noname.value.ok, false);
    const blank = w.message({ type: "channels:add", key: "@blank", name: "   " });
    await flush();
    check("a name that is empty once trimmed is refused", blank.value.ok, false);
    const rm = w.message({ type: "channels:remove", key: KEY });
    await flush();
    check("remove: ok", rm.value.ok, true);
    check("...only that one", JSON.stringify(w.stored.autoChannels.map((c) => c.key)),
      JSON.stringify(["@long", "@far"]));
  }
  {
    const w = worker({ stored: { autoChannels:
      Array.from({ length: 500 }, (_, i) => ({ key: "@c" + i, name: "C" + i })) } });
    const r = w.message({ type: "channels:add", key: "@new", name: "New" });
    await flush();
    check("at 500: full", r.value.result, "full");
    check("...nothing evicted, nothing added", w.stored.autoChannels.length, 500);
    check("...the first is still there", w.stored.autoChannels[0].key, "@c0");
  }
  {
    // An add and a remove at once, the first read held open: both apply.
    const w = worker({ stored: { autoChannels: [{ key: "@old", name: "Old" }] } });
    w.holdGet = gate();
    const held = w.holdGet;
    const add = w.message({ type: "channels:add", key: "@new", name: "New" });
    const rm = w.message({ type: "channels:remove", key: "@old" });
    await flush();
    held.open();
    await flush();
    check("concurrent add + remove: both applied",
      JSON.stringify(w.stored.autoChannels.map((c) => c.key)), JSON.stringify(["@new"]));
    check("...both answered", add.got && rm.got, true);
  }
  {
    const w = worker();
    w.failGet = 1;
    const first = w.message({ type: "channels:add", key: "@a", name: "A" });
    await flush();
    check("a step that rejects answers a failure", first.value.ok, false);
    const second = w.message({ type: "channels:add", key: "@b", name: "B" });
    await flush();
    check("...and doesn't jam the chain", second.value.result, "ok");
  }
  {
    const w = worker();
    const foreign = w.message({ type: "channels:add", key: "@a", name: "A" }, { id: "other-ext" });
    check("another extension is ignored", foreign.returned, undefined);
    const none = w.message({ type: "channels:add", key: "@a", name: "A" }, null);
    check("no sender is ignored", none.returned, undefined);
    await flush();
    check("...and nothing was written", w.stored.autoChannels, undefined);
    const unknown = w.message({ type: "something:else" });
    check("an unknown type returns nothing", unknown.returned, undefined);
    const open = w.message({ type: "openOptions" });
    check("openOptions answers asynchronously", open.returned, true);
    await flush();
    check("...and opens the options page", w.optionsOpened, 1);
    check("...ok", open.value && open.value.ok, true);
  }
  {
    const b = worker({ mode: "broken" });
    const r = b.message({ type: "channels:add", key: "@a", name: "A" });
    await flush();
    check("without the helpers, channels:add fails closed", JSON.stringify(r.value),
      JSON.stringify({ ok: false }));
    check("...and writes nothing", b.stored.autoChannels, undefined);
  }

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok " + CHECKS.n + " checks");
})().catch((e) => { console.error("CRASH: " + (e && e.stack || e)); process.exit(1); });
