// T-020 "Cover listed words": the REAL wiring out of extension/content.js (the
// pure block plus the wiring block, between their BEGIN/END markers) and
// extension/mute_words.js, driven with fakes for the page, the video, Web Audio,
// the port, storage and timers. Fixtures use made-up words only; the shipped
// default list is never printed.
//
// Usage: node mute_wiring_checks.js <content.js> <mute_words.js> <group>
// Prints "checks=<n>"; exits non-zero on any failure or an unknown group.
const fs = require("fs");
const vm = require("vm");
const [, , CONTENT, WORDS, GROUP] = process.argv;
const src = fs.readFileSync(CONTENT, "utf8");
const BEGIN = "// --- T-020 cover listed words: pure logic (BEGIN)";
const END = "// --- T-020 cover listed words: wiring (END)";
const WBEGIN = "// --- T-020 cover listed words: wiring (BEGIN)";
const i = src.indexOf(BEGIN), j = src.indexOf(END), w = src.indexOf(WBEGIN);
if (i < 0 || j < 0 || w < 0) { console.error("block markers not found"); process.exit(2); }
const wiringSrc = src.slice(w, j);

const ctx = vm.createContext({ Promise });
vm.runInContext(fs.readFileSync(WORDS, "utf8"), ctx);
vm.runInContext(src.slice(i, j) + `
  globalThis.__t = { createMuteCover, playPlan, buildMutePill, alignFailureText,
    alignDoneText, soundPlan, MUTE_TEXT, coverPanelUi, setupCoverToggle };`, ctx);
const T = ctx.__t;
const TX = T.MUTE_TEXT;

let n = 0;
const failures = [];
const check = (name, actual, expected) => {
  n++;
  const ok = typeof expected === "function" ? expected(actual)
    : JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures.push(`${name} -> ${JSON.stringify(actual)}`);
};
const flush = async () => { for (let k = 0; k < 8; k++) await new Promise((r) => setImmediate(r)); };

// --- fakes ---------------------------------------------------------------------
// Listeners are keyed on (type, fn, capture), as the DOM does: a remove with a
// different capture flag leaves the listener in place.
function listenable(o) {
  o._ls = [];
  o.addEventListener = (type, fn, opt) => {
    const capture = opt === true || !!(opt && opt.capture);
    if (!o._ls.some((l) => l.type === type && l.fn === fn && l.capture === capture)) {
      o._ls.push({ type, fn, capture });
    }
  };
  o.removeEventListener = (type, fn, opt) => {
    const capture = opt === true || !!(opt && opt.capture);
    o._ls = o._ls.filter((l) => !(l.type === type && l.fn === fn && l.capture === capture));
  };
  o.fire = (type, ev) => { for (const l of o._ls.slice()) if (l.type === type) l.fn(ev || {}); };
  return o;
}

class El {
  constructor(tag, doc) {
    listenable(this);
    this.tagName = tag.toUpperCase(); this.doc = doc; this.children = [];
    this.parentNode = null; this.attrs = {}; this._text = ""; this.style = { cssText: "" };
    this.id = ""; this.className = ""; this.type = ""; this.host = null;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this;
    this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  attachShadow() { this.shadowRoot = new El("#shadow", this.doc); this.shadowRoot.host = this;
    return this.shadowRoot; }
  get isConnected() { let x = this; while (x) { if (x === this.doc.body) return true;
    x = x.parentNode || x.host; } return false; }
  focus() { this.doc.activeElement = this; }
  matches(sel) { return sel.startsWith(".") && this.className.split(/\s+/).includes(sel.slice(1)); }
  closest(sel) { let x = this; while (x) { if (x.matches && x.matches(sel)) return x; x = x.parentNode; }
    return null; }
  all() { const out = []; const walk = (e) => { for (const c of e.children) { out.push(c); walk(c); }
    if (e.shadowRoot) walk(e.shadowRoot); }; walk(this); return out; }
}

function fakeDoc() {
  const doc = listenable({});
  doc.createElement = (t) => new El(t, doc);
  doc.body = new El("body", doc);
  doc.fullscreenElement = null;
  doc.activeElement = null;
  doc.player = new El("div", doc);
  doc.player.id = "movie_player";
  doc.body.appendChild(doc.player);
  doc.querySelector = (sel) => (sel === "#movie_player" ? doc.player : null);
  doc.getElementById = (id) => doc.body.all().find((e) => e.id === id) || null;
  return doc;
}

function fakeVideo(doc) {
  const v = listenable({ currentTime: 0, volume: 1, paused: false, playbackRate: 1,
    duration: 120, _muted: false, sets: [] });
  Object.defineProperty(v, "muted", { get() { return v._muted; },
    set(x) { v._muted = !!x; v.sets.push(!!x); } });
  v.connected = true;
  Object.defineProperty(v, "isConnected", { get() { return v.connected; } });
  return v;
}

function param(v) {
  const p = { value: v, ev: [] };
  for (const m of ["setValueAtTime", "linearRampToValueAtTime", "exponentialRampToValueAtTime",
    "cancelScheduledValues"]) p[m] = (a, b) => { p.ev.push([m, a, b]); };
  return p;
}

function fakeAudio(log, opts = {}) {
  return class {
    constructor() {
      this.state = opts.state || "running"; this.currentTime = 10; this.closed = false;
      this.nodes = []; this.destination = { id: "dest" }; this.resumed = 0;
      log.push(this);
    }
    resume() { this.resumed++; return opts.reject ? Promise.reject(new Error("no")) : Promise.resolve(); }
    close() { this.closed = true; return Promise.resolve(); }
    node(kind, extra) {
      const nd = { kind, out: [], disc: 0, ...extra,
        connect: (t) => { nd.out.push(t); }, disconnect: () => { nd.disc++; } };
      this.nodes.push(nd); return nd;
    }
    createGain() { return this.node("gain", { gain: param(1) }); }
    createOscillator() {
      const o = this.node("osc", { type: "sine", frequency: param(440), starts: [], stops: [],
        onended: null });
      o.start = (t) => o.starts.push(t); o.stop = (t) => o.stops.push(t);
      return o;
    }
    createBiquadFilter() { return this.node("filter", { type: "", Q: param(1), frequency: param(350), gain: param(0) }); }
  };
}

function fakeTimers() {
  const t = { next: 1, live: new Map() };
  t.setTimeout = (fn, ms) => { const id = t.next++; t.live.set(id, { fn, ms, kind: "t" }); return id; };
  t.clearTimeout = (id) => { t.live.delete(id); };
  t.setInterval = (fn, ms) => { const id = t.next++; t.live.set(id, { fn, ms, kind: "i" }); return id; };
  t.clearInterval = (id) => { t.live.delete(id); };
  t.run = (pred) => { for (const [id, x] of [...t.live]) { if (pred(x)) { if (x.kind === "t") t.live.delete(id);
    x.fn(); } } };
  t.byMs = (ms) => [...t.live.values()].filter((x) => x.ms === ms);
  return t;
}

function fakePort() {
  const p = { posted: [], msg: [], disc: [], disconnected: 0, throws: false };
  p.postMessage = (m) => { if (p.throws) throw new Error("gone"); p.posted.push(m); };
  p.onMessage = { addListener: (fn) => p.msg.push(fn) };
  p.onDisconnect = { addListener: (fn) => p.disc.push(fn) };
  p.disconnect = () => { p.disconnected++; };
  p.emit = (m) => { for (const fn of p.msg) fn(m); };
  p.drop = () => { for (const fn of p.disc) fn(); };
  return p;
}

const CUES = [
  { start_ms: 10000, text: "well blorp that" },
  { start_ms: 14000, text: "nothing here" },
  { start_ms: 30000, text: "a zarf again" },
  { start_ms: 34000, text: "fine" },
];

function setup(o = {}) {
  const doc = fakeDoc();
  const win = listenable({});
  const video = o.noVideo ? null : fakeVideo(doc);
  if (video && o.duration !== undefined) video.duration = o.duration;
  const timers = fakeTimers();
  const ctxs = [];
  const ports = [];
  const panel = o.panel === false ? null : { statuses: [], announced: [], pressedV: null, busyV: null,
    status(t) { this.statuses.push(t); }, announce(t) { this.announced.push(t); },
    pressed(b) { this.pressedV = b; }, busy(b) { this.busyV = b; } };
  const env = { doc, win, video, timers, ctxs, ports, panel, vid: "vid1", gets: [] };
  const store = { muteWords: "muteWords" in o ? o.muteWords : "blorp\nzarf",
    muteSound: o.muteSound || "bleep" };
  env.release = {};   // deferStorage: release[key]() resolves that read now
  const get = (key) => { env.gets.push(key);
    if (o.deferStorage) return new Promise((res) => { env.release[key] = () => res({ [key]: store[key] }); });
    return o.holdStorage ? new Promise(() => {}) : Promise.resolve({ [key]: store[key] }); };
  env.cover = T.createMuteCover({
    doc, win,
    findVideo: () => video,
    videoId: () => env.vid,
    pageUrl: () => "https://www.youtube.com/watch?v=vid1",
    readTranscript: o.readTranscript || (async () => ({ cues: CUES })),
    getWords: () => get("muteWords"),
    getSound: () => get("muteSound"),
    connect: () => { const p = fakePort(); ports.push(p); return p; },
    AudioCtx: o.noAudio ? null : fakeAudio(ctxs, o.audio || {}),
    defaults: () => ctx.TLDW_MUTE_DEFAULTS,
    normalise: () => ctx.TLDW_normaliseMuteList,
    ui: () => env.panelOpen === false ? null : panel,
    setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    setInterval: timers.setInterval, clearInterval: timers.clearInterval,
  });
  if (o.panel === false) env.panelOpen = false;
  env.at = (sec) => { video.currentTime = sec; video.fire("timeupdate"); };
  env.sess = () => env.cover._session();
  env.pill = () => doc.body.all().find((e) => e.id === "tldw-mute-pill") || null;
  env.counts = () => [video ? video._ls.length : 0, doc._ls.length, win._ls.length];
  return env;
}

async function running(o) {
  const env = setup(o);
  env.cover.start();
  await flush();
  return env;
}

const groups = {
  async harness() {
    // The listener fake must catch a remove whose capture flag doesn't match.
    const o = listenable({});
    const fn = () => {};
    o.addEventListener("keydown", fn, { capture: true });
    o.removeEventListener("keydown", fn, false);
    check("mismatched remove leaves one listener", o._ls.length, 1);
    o.removeEventListener("keydown", fn, { capture: true });
    check("matched remove leaves none", o._ls.length, 0);
    check("wiring never calls showError", wiringSrc.includes("showError"), false);
    check("wiring never sets innerHTML", /\.innerHTML\s*=/.test(wiringSrc), false);
    check("defaults stay frozen", Object.isFrozen(ctx.TLDW_MUTE_DEFAULTS), true);
  },

  async sound() {
    const log = [];
    const A = fakeAudio(log);
    const ac = new A();
    const plan = T.soundPlan("bleep", 600, 0.5);
    const h = T.playPlan(ac, plan);
    const osc = ac.nodes.find((x) => x.kind === "osc");
    const gains = ac.nodes.filter((x) => x.kind === "gain");
    check("bleep: one oscillator", ac.nodes.filter((x) => x.kind === "osc").length, 1);
    check("bleep: type set by assignment", osc.type, "sine");
    check("bleep: 1000 Hz", osc.frequency.value, 1000);
    const [env, vol] = gains;
    check("volume gain = 0.15 x volume", vol.gain.value, 0.075);
    check("chain osc -> env -> vol -> out", [osc.out[0] === env, env.out[0] === vol,
      vol.out[0] === ac.destination], [true, true, true]);
    check("envelope automation applied at t0", env.gain.ev.map((e) => e[0]),
      ["setValueAtTime", "linearRampToValueAtTime", "setValueAtTime", "linearRampToValueAtTime"]);
    check("envelope attack at t0 + 5 ms", +(env.gain.ev[1][2] - 10).toFixed(3), 0.005);
    check("started at t0", osc.starts, [10]);
    const envBefore = env.gain.ev.length;
    h.setVolume(0.2);
    check("volume change touches only the volume gain",
      [env.gain.ev.length === envBefore, vol.gain.ev.length], [true, 1]);
    check("volume change rescales", +vol.gain.ev[0][1].toFixed(3), 0.03);
    ac.currentTime = 10.2;
    h.stop();
    const tail = env.gain.ev.slice(envBefore).map((e) => e[0]);
    check("stop: cancel, then set, then ramp", tail,
      ["cancelScheduledValues", "setValueAtTime", "linearRampToValueAtTime"]);
    h.stop();
    check("stop twice is safe", env.gain.ev.length, envBefore + 3);
    osc.onended();
    check("ended disconnects every node", ac.nodes.every((x) => x.disc >= 1), true);
    const ac2 = new A();
    T.playPlan(ac2, T.soundPlan("moo", 1000, 1));
    const flt = ac2.nodes.filter((x) => x.kind === "filter");
    const oscs = ac2.nodes.filter((x) => x.kind === "osc");
    check("moo: sawtooth + sine LFO", oscs.map((x) => x.type), ["sawtooth", "sine"]);
    check("moo: four filters, type/Q/gain set by assignment",
      flt.map((f) => [f.type, f.Q.value, f.gain.value]),
      [["highpass", -3.01, 0], ["peaking", 3, 9], ["peaking", 4, 5], ["lowpass", -3.01, 0]]);
    check("moo: filters in chain order", oscs[0].out[0] === flt[0] && flt[0].out[0] === flt[1]
      && flt[1].out[0] === flt[2] && flt[2].out[0] === flt[3], true);
    const lfoOut = oscs[1].out[0];
    check("moo: LFO depth 2 Hz into the pitch", !!lfoOut && lfoOut.kind === "gain"
      && lfoOut.gain.value === 2 && lfoOut.out[0] === oscs[0].frequency, true);
    const glide = (f) => f.frequency.ev.map((e) => [e[0], e[1]]);
    check("moo: the highpass gets no glide", flt[0].frequency.ev, []);
    check("moo: F1 glides on its own node", glide(flt[1]),
      [["setValueAtTime", 300], ["exponentialRampToValueAtTime", 650], ["exponentialRampToValueAtTime", 450]]);
    check("moo: F2 glides on its own node", glide(flt[2]),
      [["setValueAtTime", 1100], ["exponentialRampToValueAtTime", 800]]);
    check("moo: lowpass glides on its own node", glide(flt[3]),
      [["setValueAtTime", 450], ["exponentialRampToValueAtTime", 1600], ["exponentialRampToValueAtTime", 900]]);
    check("moo: the last filter feeds the envelope", flt[3].out[0].kind, "gain");
    // A filter with no finite gain in its plan keeps the node's default.
    const ac3 = new A();
    const noGain = T.soundPlan("moo", 1000, 1);
    noGain.nodes = noGain.nodes.map((n) => n.id === "f2" ? { ...n, gain: undefined } : n);
    T.playPlan(ac3, noGain);
    check("moo: no plan gain leaves the default", ac3.nodes.filter((x) => x.kind === "filter")[2].gain.value, 0);
  },

  async session() {
    // The AudioContext is made and resumed before the first await.
    let env = setup({ holdStorage: true });
    env.cover.start();
    check("context created synchronously", env.ctxs.length, 1);
    check("resume called synchronously", env.ctxs[0].resumed, 1);
    check("loading announced", env.panel.announced, [TX.loading]);
    check("toggle pressed and busy while reading", [env.panel.pressedV, env.panel.busyV], [true, true]);
    check("reads only the two keys, by name", env.gets, ["muteWords"]);
    env = await running({ muteSound: "mute" });
    check("mute mode closes the context at once", env.ctxs[0].closed, true);
    env = await running({ muteWords: "" });
    check("empty list: its status", env.panel.announced.slice(-1), [TX.emptyList]);
    check("empty list: toggle off", [env.cover.isOn(), env.panel.pressedV], [false, false]);
    check("empty list: no listeners", env.counts(), [0, 0, 0]);
    env = await running({ readTranscript: async () => ({ cues: [{ start_ms: 0, text: "nothing" }] }) });
    check("no matches: its status", env.panel.announced.slice(-1), [TX.noMatches]);
    check("no matches: toggle stays off", [env.cover.isOn(), env.counts()], [false, [0, 0, 0]]);
    env = await running({ readTranscript: () => Promise.reject(new Error("x")) });
    check("transcript read fails: its status", env.panel.announced.slice(-1), [TX.noTranscript]);
    check("transcript read fails: nothing muted, no listeners",
      [env.video.sets.length, env.counts()], [0, [0, 0, 0]]);
    env = await running({ duration: Infinity });
    check("live stream: its status, no action", [env.panel.announced, env.counts(), env.ctxs[0].closed],
      [[TX.live], [0, 0, 0], true]);
    env = await running({ duration: NaN });
    check("NaN duration is not live", env.cover.isOn(), true);
    env = setup();
    env.doc.player.className = "html5-video-player ad-showing";
    env.cover.start();
    await flush();
    check("ad at toggle-on: its status", [env.panel.announced, env.cover.isOn()], [[TX.ad], false]);
    env = await running({ noVideo: true });
    check("no player: its status", env.panel.announced, [TX.noPlayer]);
    env = await running();
    check("on: status names the spots", env.panel.announced.slice(-1)[0],
      "Bleeping 2 spots from the captions (about 10 seconds of audio). Timing them to the word…");
    check("on: toggle pressed, not busy", [env.panel.pressedV, env.panel.busyV], [true, false]);
    check("on: one align request", env.ports[0].posted.map((m) => m.type), ["align"]);
    const req = env.ports[0].posted[0];
    check("align windows are whole numbers", req.windows.every((x) => Number.isSafeInteger(x.start_ms)
      && Number.isSafeInteger(x.end_ms) && x.start_ms >= 0), true);
    check("the word list never leaves the page", JSON.stringify(req).includes("blorp")
      || JSON.stringify(req).includes("zarf"), false);
    check("align request carries the page url", req.url, "https://www.youtube.com/watch?v=vid1");
    env.at(10.5);
    check("line ranges are active before any align message", env.video.muted, true);
    check("the pill is on the page", !!env.pill(), true);
    env = await running({ muteSound: "moo" });
    check("moo verb", env.panel.announced.slice(-1)[0].startsWith("Mooing over 2 spots"), true);
    env = await running({ readTranscript: async () => ({ cues: [{ start_ms: 1000, text: "blorp" }] }) });
    check("one spot, singular", env.panel.announced.slice(-1)[0].startsWith("Bleeping 1 spot from"), true);
  },

  async ownership() {
    let env = await running();
    env.at(10.5);
    check("enter mutes", env.video.muted, true);
    check("bleep started", env.ctxs[0].nodes.some((x) => x.kind === "osc"), true);
    env.video.volume = 0.4; env.video.fire("volumechange");
    check("slider move keeps ownership", env.sess().st.owned, true);
    const vol = env.ctxs[0].nodes.filter((x) => x.kind === "gain")[1];
    check("volume change rescales the sound", vol.gain.ev.length, 1);
    env.at(16);
    check("exit unmutes", env.video.muted, false);
    // already muted by the user
    env = await running();
    env.video._muted = true;
    env.at(10.5);
    check("already muted: no ownership, no sound",
      [env.sess().st.owned, env.ctxs[0].nodes.length], [false, 0]);
    env.at(16);
    check("already muted: never unmuted", env.video.muted, true);
    // mute button mid-range
    env = await running();
    env.at(10.5);
    const btn = new El("button", env.doc);
    btn.className = "ytp-mute-button";
    const inner = new El("span", env.doc);
    btn.appendChild(inner);
    env.doc.fire("click", { target: inner });
    check("mute-button click releases", env.sess().st.owned, false);
    const osc = env.ctxs[0].nodes.find((x) => x.kind === "osc");
    check("release stops the sound", osc.stops.length, 2);
    env.at(16);
    check("released: no unmute at exit", env.video.muted, true);
    // m key
    env = await running();
    env.at(10.5);
    env.doc.fire("keydown", { key: "m", ctrlKey: true, target: env.doc.body });
    check("Ctrl+m ignored", env.sess().st.owned, true);
    const input = new El("input", env.doc);
    env.doc.fire("keydown", { key: "m", target: input });
    check("m in a text field ignored", env.sess().st.owned, true);
    env.doc.fire("keydown", { key: "m", target: env.doc.body, composedPath: () => [input, env.doc.body] });
    check("m in a shadow-root text field ignored", env.sess().st.owned, true);
    env.doc.fire("keydown", { key: "m", target: env.doc.body });
    check("m releases", env.sess().st.owned, false);
    check("keydown and click are capture-phase",
      ["keydown", "click"].map((t) => env.doc._ls.find((l) => l.type === t).capture), [true, true]);
    // a user unmute mid-range stops the sound
    env = await running();
    env.at(10.5);
    env.video._muted = false; env.video.fire("volumechange");
    check("user unmute releases", env.sess().st.owned, false);
    // volume 0 plays no sound
    env = await running();
    env.video.volume = 0;
    env.at(10.5);
    check("volume 0: muted, no sound", [env.video.muted, env.ctxs[0].nodes.length], [true, 0]);
  },

  async sounds() {
    let env = await running({ audio: { state: "suspended" } });
    env.at(10.5);
    check("suspended context: muted only", [env.video.muted, env.ctxs[0].nodes.length], [true, 0]);
    check("blocked status once", env.panel.announced.filter((t) => t === TX.soundBlocked).length, 1);
    env.at(16); env.at(30.5);
    check("blocked status still once", env.panel.announced.filter((t) => t === TX.soundBlocked).length, 1);
    env = await running({ audio: { reject: true } });
    env.at(10.5);
    check("resume rejected: muted only", [env.video.muted, env.ctxs[0].nodes.length], [true, 0]);
    env = await running();
    env.at(10.5);
    env.video.paused = true; env.video.fire("pause");
    const osc = env.ctxs[0].nodes.find((x) => x.kind === "osc");
    check("pause stops the sound, keeps the mute", [osc.stops.length, env.video.muted], [2, true]);
    env.video.paused = false;
    env = await running();
    env.at(10.5);
    env.at(11.5);
    const o2 = env.ctxs[0].nodes.find((x) => x.kind === "osc");
    check("cap edge stops the sound without unmuting", [o2.stops.length, env.video.muted], [2, true]);
    env = await running({ muteSound: "moo", readTranscript: async () => ({ cues: [
      { start_ms: 10000, text: "blorp" }, { start_ms: 10000, text: "x" }] }) });
    env.sess().st.ranges = [{ s: 10000, e: 10400, hits: [0], timed: "word" }];
    env.at(10.1);
    const moo = env.ctxs[0].nodes.find((x) => x.kind === "osc");
    env.at(10.5);
    check("a moo rings out past its range end", [moo.stops.length, env.video.muted], [1, false]);
    env = await running();
    env.at(10.5);
    const first = env.ctxs[0].nodes.find((x) => x.kind === "osc");
    env.video.playbackRate = 2;
    env.video.fire("ratechange");
    const oscs = env.ctxs[0].nodes.filter((x) => x.kind === "osc");
    check("ratechange re-plans the sound at the new rate",
      [first.stops.length, oscs.length, +(oscs[1].stops[0] - 10).toFixed(3)], [2, 2, 0.42]);  // 800 ms of media left at 2x = 400 ms, + 20 ms
    env.video.playbackRate = 1;
    env.video.currentTime = 12; env.video.fire("seeked");
    check("seek inside past the cap: silent, muted",
      [env.ctxs[0].nodes.find((x) => x.kind === "osc").stops.length, env.video.muted], [2, true]);
  },

  async teardown() {
    let env = await running();
    env.at(10.5);
    env.cover.stop();
    check("stop: zero listeners everywhere", env.counts(), [0, 0, 0]);
    check("stop: unmuted, context closed, port closed",
      [env.video.muted, env.ctxs[0].closed, env.ports[0].disconnected], [false, true, 1]);
    check("stop: pill removed", env.pill(), null);
    check("stop: off announced", env.panel.announced.slice(-1), [TX.offBleep]);
    check("stop: no timers left", env.timers.live.size, 0);
    env = await running();
    env.at(10.5);
    env.vid = "other";
    env.at(10.6);
    check("new video id: torn down, unmuted", [env.counts(), env.video.muted, env.cover.isOn()],
      [[0, 0, 0], false, false]);
    check("new video id: no off announcement", env.panel.announced.includes(TX.offBleep), false);
    env = await running();
    env.at(10.5);
    env.doc.player.className = "ad-showing";
    env.video.fire("loadstart");
    check("loadstart, same id: session kept", env.cover.isOn(), true);
    check("loadstart: our mute given up", env.video.muted, false);
    env.at(1);
    check("mid-session ad: the paused status", env.panel.announced.slice(-1), [TX.adPaused]);
    const before = env.panel.statuses[env.panel.statuses.length - 2];
    const seen = env.panel.statuses.length;
    env.ports[0].emit({ type: "alignDone", timed: 0, cached: 0, failed: 0 });
    check("a status during the ad is held, not shown", env.panel.statuses.length, seen);
    const held = "Bleeping 2 spots by caption line: Whisper didn't find the words in the audio.";
    env.doc.player.className = "";
    const announcedN = env.panel.announced.length;
    env.at(2);
    check("after the ad: the held status is shown, unannounced",
      [env.panel.statuses.slice(-1)[0], env.panel.announced.length, before !== held], [held, announcedN, true]);
    env.doc.player.className = "ad-showing";
    env.at(3);
    env.vid = "other";
    env.video.fire("loadstart");
    check("loadstart, new id: zero listeners", env.counts(), [0, 0, 0]);
    env = await running();
    env.video.connected = false;
    env.at(10.5);
    check("detached video tears down", env.counts(), [0, 0, 0]);
    env = await running();
    env.at(10.5);
    env.win.fire("pagehide");
    check("pagehide unmutes and tears down", [env.video.muted, env.counts()], [false, [0, 0, 0]]);
    env = await running();
    const port = env.ports[0];
    env.cover.stop();
    let threw = false;
    try { port.emit({ type: "alignWindow", i: 0, words: [{ s: 10300, e: 10600, w: "blorp" }] }); }
    catch (_) { threw = true; }
    check("late alignWindow after teardown ignored", [threw, env.cover.isOn()], [false, false]);
    // slow read: the id changes, or the user turns it off, while reading
    let release;
    env = setup({ readTranscript: () => new Promise((r) => { release = r; }) });
    env.cover.start();
    await flush();
    env.vid = "other";
    release({ cues: CUES });
    await flush();
    check("id change during the read: nothing attached", [env.counts(), env.cover.isOn()],
      [[0, 0, 0], false]);
    env = setup({ readTranscript: () => new Promise((r) => { release = r; }) });
    env.cover.start();
    await flush();
    env.cover.stop();
    release({ cues: CUES });
    await flush();
    check("off during the read: nothing attached, context closed",
      [env.counts(), env.ctxs[0].closed, env.ports.length], [[0, 0, 0], true, 0]);
    // Play key moments shares the video: its seeks arrive as seeked events.
    env = await running();
    const skip = () => {};
    env.video.addEventListener("timeupdate", skip);
    env.video.currentTime = 30.5; env.video.fire("seeked");
    check("a key-moments seek into a range mutes", env.video.muted, true);
    env.cover.stop();
    check("stop leaves key moments' listener alone", env.video._ls.map((l) => l.fn === skip), [true]);
  },

  async align() {
    let env = await running();
    const port = env.ports[0];
    const req = port.posted[0];
    const wi = req.windows.findIndex((x) => x.start_ms <= 10000 && x.end_ms >= 14000);
    // The playhead is inside the hit's line range: the refinement waits.
    env.at(10.1);
    port.emit({ type: "alignWindow", i: wi, words: [{ s: 10300, e: 10600, w: "Blorp," }] });
    check("deferred while inside the merged range", env.sess().st.ranges[0].timed, "line");
    check("progress is shown, not announced", [env.panel.statuses.slice(-1)[0],
      env.panel.announced.some((t) => t.startsWith("Timed"))], ["Timed to the word: 1 of 2.", false]);
    env.at(16);
    check("applied once the playhead leaves", env.sess().st.ranges[0].timed, "word");
    check("word range is padded", [env.sess().st.ranges[0].s, env.sess().st.ranges[0].e], [10150, 10700]);
    check("server words never reach the DOM", env.doc.body.all().some((e) =>
      e.textContent.includes("Blorp") || Object.values(e.attrs).some((a) => a.includes("Blorp"))), false);
    const wj = req.windows.findIndex((x) => x.start_ms <= 30000 && x.end_ms >= 34000);
    port.emit({ type: "alignWindow", i: wj, words: [{ s: 30300, e: 30500, w: "zarf" }] });
    port.emit({ type: "alignDone", timed: 2, cached: 0, failed: 0 });
    check("done, all timed", env.panel.announced.slice(-1)[0].startsWith("Bleeping 2 spots, all timed"), true);
    check("done closes the port", port.disconnected, 1);
    // The boundary timer is re-armed after a refinement moves the next edge.
    env = await running();
    env.at(9.5);
    check("timer armed for the line range's start", env.timers.byMs(300).length, 1);
    env.ports[0].emit({ type: "alignWindow", i: wi, words: [{ s: 10300, e: 10600, w: "blorp" }] });
    check("re-armed for the word range's start", [env.timers.byMs(300).length,
      env.timers.byMs(650).length], [0, 1]);
    // partly timed, none timed, all failed
    env = await running();
    env.ports[0].emit({ type: "alignWindow", i: wi, words: [{ s: 10300, e: 10600, w: "blorp" }] });
    env.ports[0].emit({ type: "alignDone", timed: 1, cached: 0, failed: 0 });
    check("done, partly timed", env.panel.announced.slice(-1)[0],
      (t) => t.startsWith("Bleeping 2 spots: 1 timed to the word, the rest by caption line"));
    env = await running();
    env.ports[0].emit({ type: "alignDone", timed: 0, cached: 0, failed: 0 });
    check("done, none timed", env.panel.announced.slice(-1)[0],
      "Bleeping 2 spots by caption line: Whisper didn't find the words in the audio.");
    env = await running();
    env.ports[0].emit({ type: "alignDone", timed: 0, cached: 0, failed: 2 });
    check("every window failed: other failure", env.panel.announced.slice(-1)[0],
      "Bleeping 2 spots by caption line: timing them to the word failed. Turn this off and on again to retry.");
    // the port drops before alignDone
    env = await running();
    env.ports[0].drop();
    check("disconnect before done: other failure", env.panel.announced.slice(-1)[0],
      (t) => t.endsWith("timing them to the word failed. Turn this off and on again to retry."));
    env.at(10.5);
    env.ports[0].throws = true;
    let threw = false;
    try { env.timers.run((x) => x.kind === "i"); env.at(16); } catch (_) { threw = true; }
    check("after a disconnect the next exit still unmutes", [threw, env.video.muted], [false, false]);
    // the safety timer
    env = await running();
    env.timers.run((x) => x.ms === 660000);
    check("660 s safety timer: timeout status, port closed",
      [env.panel.announced.slice(-1)[0].includes("failed"), env.ports[0].disconnected], [true, 1]);
    // boundary timer: fires at the edge, re-arms at most twice
    env = await running();
    env.at(14.5);
    const edge = [...env.timers.live.values()].filter((x) => x.kind === "t" && x.ms < 1000);
    check("boundary timer armed near an edge", edge.length, 1);
    env.video.currentTime = 15.0;
    env.timers.run((x) => x.kind === "t" && x.ms < 1000);
    check("boundary timer exits before the next timeupdate", env.video.muted, false);
    env.at(14.5);
    let armed = 0;
    for (let k = 0; k < 5; k++) {
      const before = [...env.timers.live.values()].filter((x) => x.kind === "t" && x.ms < 1000).length;
      if (!before) break;
      armed++;
      env.timers.run((x) => x.kind === "t" && x.ms < 1000);
    }
    check("re-arms at most twice for the same edge", armed, 3);
  },

  async reasons() {
    const tot = { n: 2, k: 0, totalMs: 9800 };
    const f = (m) => T.alignFailureText(m, "bleep", tot);
    const pre = "Bleeping 2 spots by caption line";
    check("whisper_off", f({ status: 503, reason: "whisper_off", error: "Whisper is turned off." }),
      pre + ", because Whisper is turned off. To time them to the word, set 'Videos with no subtitles' in TL;DW's options to either transcribe choice.");
    check("whisper_off pinned: server text", f({ status: 503, reason: "whisper_off", error: "Pinned off here." }),
      pre + ": Pinned off here.");
    check("whisper_missing with hint", f({ status: 503, reason: "whisper_missing", installHint: "pipx inject x y" }),
      pre + ". To time them to the word, install Whisper for tldw serve: pipx inject x y, then restart tldw serve and turn this off and on again.");
    check("whisper_missing default hint", f({ reason: "whisper_missing" }).includes("pipx inject youtube-tldw faster-whisper"), true);
    check("model_missing", f({ reason: "model_missing" }), pre + ": Whisper's model isn't downloaded yet. Transcribe one video that has no subtitles to fetch it, then turn this off and on again.");
    check("busy: server text + suffix", f({ status: 429, reason: "busy", error: "Busy now." }),
      "Busy now. Until then, spots are covered by caption line.");
    check("rate_limited: server text + suffix", f({ status: 429, reason: "rate_limited", error: "Slow down." }),
      "Slow down. Until then, spots are covered by caption line.");
    check("ffmpeg_missing: detail", f({ status: 503, reason: "ffmpeg_missing", error: "Need ffmpeg." }), pre + ": Need ffmpeg.");
    check("too_long: detail", f({ status: 413, reason: "too_long", error: "Too long." }), pre + ": Too long.");
    check("download_failed: detail", f({ status: 502, reason: "download_failed", error: "No length." }), pre + ": No length.");
    check("timeout without detail: generic", f({ reason: "timeout" }), pre + ": timing them to the word failed. Turn this off and on again to retry.");
    check("unreachable", f({ reason: "unreachable" }), pre + ": couldn't reach tldw serve. Start it, then turn this off and on again.");
    check("token", f({ reason: "token" }), (t) => t.startsWith(pre + ": TL;DW's token"));
    check("401 without reason: token", f({ status: 401 }), (t) => t.includes("token"));
    check("404: older server", f({ status: 404 }), pre + ": this tldw serve can't time words. Update tldw, then restart tldw serve and turn this off and on again.");
    check("other with detail", f({ reason: "other", error: "Oops." }), pre + ": Oops.");
    check("mute verb", T.alignFailureText({ reason: "unreachable" }, "mute", tot).startsWith("Muting 2 spots"), true);
    // A server string with markup stays text all the way into the pill.
    const env = await running({ panel: false });
    env.ports[0].emit({ type: "alignError", status: 502, reason: "download_failed", error: "<b>x</b>" });
    env.timers.run((x) => x.ms === 100);
    const live = env.pill().shadowRoot.all().find((e) => e.getAttribute("aria-live") === "polite");
    check("markup from the server is text", [live.textContent.endsWith("<b>x</b>"), live.children.length],
      [true, 0]);
  },

  async panel() {
    // The real panel adapter, against a fake shadow root.
    const doc = fakeDoc();
    const make = (cls) => { const e = new El("div", doc); e.className = cls; return e; };
    const parts = { ".covertoggle": make("covertoggle"), ".coverstatus": make("coverstatus"),
      ".coverlive": make("coverlive"), ".coverwrap": make("coverwrap") };
    const root = { querySelector: (sel) => parts[sel] || null };
    const timers = fakeTimers();
    const ui = T.coverPanelUi(root, timers.setTimeout);
    check("no panel, no adapter", T.coverPanelUi(null, timers.setTimeout), null);
    ui.busy(true);
    const btn = parts[".covertoggle"];
    check("busy: aria-disabled, never disabled", [btn.getAttribute("aria-disabled"), "disabled" in btn],
      ["true", false]);
    check("busy: the region is aria-busy", parts[".coverwrap"].getAttribute("aria-busy"), "true");
    ui.busy(false);
    check("not busy: cleared", [btn.getAttribute("aria-disabled"), parts[".coverwrap"].getAttribute("aria-busy")],
      ["false", null]);
    ui.announce("hello");
    check("announce clears, then fills on a later tick", parts[".coverlive"].textContent, "");
    timers.run(() => true);
    check("announced", parts[".coverlive"].textContent, "hello");
    // The toggle against a real session: a second press during the read stops it.
    let release;
    const env = setup({ readTranscript: () => new Promise((r) => { release = r; }) });
    T.setupCoverToggle(root, env.cover, timers.setTimeout);
    check("toggle starts unpressed", btn.getAttribute("aria-pressed"), "false");
    btn.onclick();
    await flush();
    check("on while loading", [env.cover.isOn(), env.cover.isLoading()], [true, true]);
    btn.onclick();
    check("a second press during the read stops it", env.cover.isOn(), false);
    release({ cues: CUES });
    await flush();
    check("nothing attached after the read", env.counts(), [0, 0, 0]);
    // A re-opened panel reflects a live session.
    const e2 = await running();
    T.setupCoverToggle(root, e2.cover, timers.setTimeout);
    check("re-opened: pressed and showing the live status",
      [btn.getAttribute("aria-pressed"), parts[".coverstatus"].textContent], ["true", e2.cover.status()]);
  },

  async live() {
    const LBL = (env) => env.pill().shadowRoot.all().find((e) => e.className === "label").textContent;
    const STOPNAME = (env) => env.pill().shadowRoot.all().find((e) => e.tagName === "BUTTON").getAttribute("aria-label");
    const EMOJI = (env) => env.pill().shadowRoot.all().find((e) => e.getAttribute("aria-hidden") === "true").textContent;
    const oscs = (c) => c.nodes.filter((x) => x.kind === "osc");

    // No session: nothing happens.
    let env = setup();
    env.cover.settingsChanged({ sound: { newValue: "moo" }, words: { newValue: "blorp" } });
    check("no session: no-op", [env.cover.isOn(), env.ctxs.length, env.ports.length], [false, 0, 0]);

    // Sound bleep -> moo mid-range: the sound stops, the range stays muted and silent.
    env = await running();
    env.at(10.5);
    const c0 = env.ctxs[0];
    const bleep = oscs(c0)[0];
    env.cover.settingsChanged({ sound: { newValue: "moo" } });
    check("switch stops the playing sound", bleep.stops.length, 2);
    check("switch keeps the range muted", [env.video.muted, env.sess().st.owned], [true, true]);
    env.at(11);
    check("the range in progress stays silent", oscs(c0).length, 1);
    check("switch announced", env.panel.announced.slice(-1)[0],
      "Sound changed: now mooing over listed words, starting at the next covered spot.");
    check("pill follows the switch", [EMOJI(env), LBL(env), STOPNAME(env)],
      ["🐄", "Mooing over listed words · now", "Stop mooing"]);
    env.at(16);
    check("leaving the range unmutes", env.video.muted, false);
    env.at(30.5);
    check("the next range plays the new sound", oscs(c0).slice(1).map((x) => x.type), ["sawtooth", "sine"]);
    check("the same context is kept", env.ctxs.length, 1);
    check("the mode is the session's and the state's", [env.sess().mode, env.sess().st.mode], ["moo", "moo"]);

    // The same mode is a no-op; an unknown value and a removed key are bleep.
    env = await running({ muteSound: "moo" });
    const before = env.panel.announced.length;
    env.cover.settingsChanged({ sound: { newValue: "moo" } });
    check("same mode: no-op", env.panel.announced.length, before);
    env.cover.settingsChanged({ sound: { newValue: "kazoo" } });
    check("unknown sound is bleep", env.sess().mode, "bleep");
    env = await running({ muteSound: "moo" });
    env.cover.settingsChanged({ sound: { newValue: undefined } });
    check("removed key is bleep", env.sess().mode, "bleep");

    // To mute: the context closes and no sound plays; ranges still mute.
    env = await running();
    env.cover.settingsChanged({ sound: { newValue: "mute" } });
    check("to mute closes the context", [env.ctxs[0].closed, env.sess().ctx], [true, null]);
    env.at(10.5);
    check("mute mode: muted, no sound", [env.video.muted, oscs(env.ctxs[0]).length], [true, 0]);
    check("pill says muting", [EMOJI(env), STOPNAME(env)], ["🔇", "Stop muting"]);

    // From mute: a new context is made and resumed; mid-range stays silent.
    env = await running({ muteSound: "mute" });
    env.at(10.5);
    env.cover.settingsChanged({ sound: { newValue: "bleep" } });
    check("from mute: a new context, resumed", [env.ctxs.length, env.ctxs[1].resumed], [2, 1]);
    env.at(11);
    check("from mute mid-range: silent", oscs(env.ctxs[1]).length, 0);
    env.at(16); env.at(30.5);
    check("from mute: the next range bleeps", oscs(env.ctxs[1]).map((x) => x.type), ["sine"]);

    // From mute when the new context won't run: muted only, said once; the flags reset.
    const aopts = {};
    env = await running({ muteSound: "mute", audio: aopts });
    env.sess().resumeFailed = true;
    env.sess().blockedSaid = true;
    aopts.state = "suspended";
    env.cover.settingsChanged({ sound: { newValue: "bleep" } });
    check("a new context resets the failure flags", [env.sess().resumeFailed, env.sess().blockedSaid,
      env.sess().st.soundBlocked], [false, false, false]);
    env.at(10.5);
    check("a context that won't run: muted only", [env.video.muted, oscs(env.ctxs[1]).length], [true, 0]);
    check("blocked said once", env.panel.announced.filter((t) => t === TX.soundBlocked).length, 1);

    // No AudioContext at all: a switch to bleep doesn't throw.
    env = await running({ muteSound: "mute", noAudio: true });
    let threw = false;
    try { env.cover.settingsChanged({ sound: { newValue: "bleep" } }); env.at(10.5); } catch (_) { threw = true; }
    check("no AudioContext: no throw, still muted", [threw, env.video.muted], [false, true]);

    // A change while loading wins over a late stored read.
    env = setup({ deferStorage: true });
    env.cover.start();
    env.cover.settingsChanged({ sound: { newValue: "moo" }, words: { newValue: "zarf" } });
    env.release.muteWords(); await flush();
    env.release.muteSound(); await flush();
    check("loading: the live sound wins", env.sess().mode, "moo");
    check("loading: the live list wins", env.sess().st.hits.length, 1);
    env = setup({ deferStorage: true });
    env.cover.start();
    env.cover.settingsChanged({ sound: { newValue: "bleep" } });
    env.release.muteWords(); await flush();
    env.release.muteSound(); await flush();
    check("loading: a live value equal to the default still wins", env.sess().mode, "bleep");
    env = setup({ deferStorage: true, readTranscript: () => new Promise((r) => { env.tx = r; }) });
    env.cover.start();
    env.release.muteWords(); await flush();
    env.release.muteSound(); await flush();
    env.cover.settingsChanged({ words: { newValue: "" } });
    env.tx({ cues: CUES }); await flush();
    check("emptied during the caption read", [env.cover.isOn(), env.panel.announced.slice(-1)[0]],
      [false, TX.emptyList]);

    // Words changed while on: a rebuild in place.
    env = await running();
    env.at(10.5);
    const p0 = env.ports[0];
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    check("rebuild: the old port is ended", p0.disconnected, 1);
    check("rebuild: the owned mute is given up", [env.video.sets.slice(-1)[0], env.video.muted], [false, false]);
    check("rebuild: new hits", env.sess().st.hits.length, 1);
    check("rebuild: announced", env.panel.announced.slice(-1)[0],
      "Word list updated: Bleeping 1 spot from the captions (about 5 seconds of audio).");
    check("rebuild: one new port, only the new window", [env.ports.length,
      env.ports[1].posted[0].windows.length], [2, 1]);
    check("rebuild: the pill stays", !!env.pill(), true);
    check("rebuild: state reset", [env.sess().alignDone, env.sess().rearms, env.sess().edge], [false, 0, null]);
    p0.emit({ type: "alignWindow", i: 0, words: [{ s: 10200, e: 10500, w: "blorp" }] });
    check("a late message on the old port is dropped", env.sess().st.hits.every((h) => !h.words), true);
    env.at(30.5);
    check("rebuild: the new range mutes", env.video.muted, true);
    // The reset, proven from non-default values: a finished stream, re-arms, an
    // edge and a pending boundary timer all from before the change.
    env = await running();
    env.video.currentTime = 0;
    const sb = env.sess();
    sb.alignDone = true; sb.rearms = 2; sb.edge = 4321;
    const stale = env.timers.setTimeout(() => {}, 777);
    sb.timer = stale;
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    check("rebuild resets alignDone, rearms and edge from set values (via tick and startAlign)",
      [sb.alignDone, sb.rearms, sb.edge], [false, 0, null]);
    check("rebuild clears the pending boundary timer", env.timers.live.has(stale), false);

    // A rebuild inside a range that stays covered keeps it muted in the same task.
    env = await running();
    env.at(10.5);
    env.cover.settingsChanged({ words: { newValue: "blorp\nqux" } });
    check("still covered: muted again at once", env.video.muted, true);

    // The range under the playhead, unchanged by the rebuild, carries on: a
    // playing bleep isn't restarted, and the user's own unmute still wins.
    env = await running();
    env.at(10.5);
    const playing = oscs(env.ctxs[0]);
    env.cover.settingsChanged({ words: { newValue: "blorp\nqux" } });
    env.at(10.7);
    check("same range: the bleep isn't restarted", [oscs(env.ctxs[0]).length, playing[0].stops.length], [1, 1]);
    check("same range: still owned and muted", [env.sess().st.owned, env.video.muted], [true, true]);
    env = await running();
    env.at(10.5);
    env.video._muted = false; env.video.fire("volumechange");
    check("the user unmuted inside the range", [env.sess().st.owned, env.video.muted], [false, false]);
    env.cover.settingsChanged({ words: { newValue: "blorp\nqux" } });
    env.at(10.7);
    check("same range: the user's unmute still wins", [env.video.muted, env.sess().st.released], [false, true]);
    env.at(16); env.at(30.5);
    check("zarf is no longer listed, so 30 s is not covered", env.video.muted, false);
    // Unchanged list (also after normalising): no rebuild.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "blorp\nzarf" } });
    env.cover.settingsChanged({ words: { newValue: "  BLORP , zarf\n" } });
    check("unchanged list: no rebuild", [env.ports.length, env.ports[0].disconnected], [1, 0]);

    // Both keys in one event: sound first, then one rebuild in the new mode.
    env = await running();
    env.cover.settingsChanged({ sound: { newValue: "moo" }, words: { newValue: "zarf" } });
    check("both keys: one rebuild, in the new mode", [env.ports.length,
      env.panel.announced.slice(-1)[0].startsWith("Word list updated: Mooing over 1 spot")], [2, true]);

    // Heard windows are reused: all heard -> no port, the done status.
    env = await running();
    const req = env.ports[0].posted[0];
    const wb = req.windows.findIndex((x) => x.start_ms <= 10000 && x.end_ms >= 14000);
    const wz = req.windows.findIndex((x) => x.start_ms <= 30000 && x.end_ms >= 34000);
    env.ports[0].emit({ type: "alignWindow", i: wb, words: [{ s: 10300, e: 10600, w: "blorp" }] });
    env.ports[0].emit({ type: "alignWindow", i: wz, words: [{ s: 30300, e: 30500, w: "zarf" }] });
    env.ports[0].emit({ type: "alignDone", timed: 2, cached: 0, failed: 0 });
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    check("all heard: no new port", env.ports.length, 1);
    check("all heard: refined locally", env.sess().st.ranges.map((r) => r.timed), ["word"]);
    check("all heard: the done status", env.panel.statuses.slice(-1)[0],
      "Bleeping 1 spot, all timed to the word (about 1 seconds of audio).");
    // Partly heard: only the rest is sent.
    env = await running();
    env.ports[0].emit({ type: "alignWindow", i: wb, words: [{ s: 10300, e: 10600, w: "blorp" }] });
    env.cover.settingsChanged({ words: { newValue: "blorp\nzarf\nqux" } });
    check("partly heard: a port for the rest only", env.ports.length === 2
      && env.ports[1].posted[0].windows.every((x) => x.start_ms > 20000), true);

    // Busy on a rebuild: retried before a download, at 5/10/20 s, then said.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    for (const [k, ms] of [[1, 5000], [2, 10000], [3, 20000]]) {
      env.ports[k].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
      check("busy retry " + k + " waits " + ms, env.timers.byMs(ms).length, 1);
      env.timers.run((x) => x.ms === ms);
      check("busy retry " + k + " opens a fresh port", env.ports.length, k + 2);
    }
    env.ports[4].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
    check("after 3 retries: the busy text", env.panel.announced.slice(-1)[0].includes("Until then, spots are covered by caption line."), true);
    // Busy after progress (a download began): not retried.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    env.ports[1].emit({ type: "alignProgress", message: "Fetching the video's audio…" });
    env.ports[1].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
    check("busy after a download began: not retried", [env.timers.byMs(5000).length, env.ports.length], [0, 2]);
    // rate_limited is never retried; nor is a first toggle-on busy.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    env.ports[1].emit({ type: "alignError", status: 429, reason: "rate_limited", error: "" });
    check("rate_limited: not retried", env.timers.byMs(5000).length, 0);
    env = await running();
    env.ports[0].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
    check("first toggle-on busy: not retried", env.timers.byMs(5000).length, 0);
    // Stop during a retry wait clears the timer.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    env.ports[1].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
    env.cover.stop();
    check("stop clears the retry timer", env.timers.byMs(5000).length, 0);
    // A later rebuild clears a pending retry.
    env = await running();
    env.cover.settingsChanged({ words: { newValue: "zarf" } });
    env.ports[1].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
    env.cover.settingsChanged({ words: { newValue: "blorp" } });
    check("a later rebuild clears the retry timer", env.timers.byMs(5000).length, 0);

    // Live ends: an empty list, and no match. Everything goes; cues and heard too.
    for (const [val, text] of [["", TX.liveEmptyList], ["qux", TX.liveNoMatches]]) {
      env = await running();
      env.at(10.5);
      const sess = env.sess();
      env.ports[0].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
      env.cover.settingsChanged({ words: { newValue: val } });
      check("live end (" + JSON.stringify(val) + "): its status", env.panel.announced.slice(-1)[0], text);
      check("live end: off, toggle released, pill gone", [env.cover.isOn(), env.panel.pressedV, !!env.pill()],
        [false, false, false]);
      check("live end: mute given up, no listeners", [env.video.muted, env.counts()], [false, [0, 0, 0]]);
      check("live end: cues and heard dropped", [sess.cues, sess.heard], [null, null]);
    }
    check("the live-end texts differ from toggle-on's", [TX.liveEmptyList !== TX.emptyList,
      TX.liveNoMatches !== TX.noMatches], [true, true]);
    // A live end during a rebuild's retry wait clears the retry timer.
    for (const val of ["", "qux"]) {
      env = await running();
      env.cover.settingsChanged({ words: { newValue: "zarf" } });
      env.ports[1].emit({ type: "alignError", status: 429, reason: "busy", error: "" });
      check("retry pending before the live end (" + JSON.stringify(val) + ")", env.timers.byMs(5000).length, 1);
      env.cover.settingsChanged({ words: { newValue: val } });
      check("live end (" + JSON.stringify(val) + ") clears the retry timer", env.timers.byMs(5000).length, 0);
    }
    // Two heard hits in one window: the words are shared out as the stream does,
    // so a neighbour's word never times this hit.
    env = await running({ readTranscript: async () => ({ cues: [
      { start_ms: 10000, text: "blorp one" }, { start_ms: 12000, text: "zarf two" },
      { start_ms: 14000, text: "end" }] }) });
    const w0 = env.ports[0].posted[0].windows;
    check("one joined window for both hits", w0.length, 1);
    env.ports[0].emit({ type: "alignWindow", i: 0, words: [{ s: 10200, e: 10500, w: "blorp" },
      { s: 12300, e: 12600, w: "zarf" }] });
    env.cover.settingsChanged({ words: { newValue: "zarf\nqux" } });
    const hz = env.sess().st.hits[0];
    check("heard reuse: only its own word times the hit", hz.words, [[12150, 12700]]);
    check("heard reuse: no new port", env.ports.length, 1);
    // Panel closed: the pill says why, then goes after a second; focus isn't moved.
    env = await running({ panel: false });
    const pillHost = env.pill();
    const pillLive = pillHost.shadowRoot.all().find((e) => e.getAttribute("aria-live") === "polite");
    env.cover.settingsChanged({ words: { newValue: "" } });
    check("panel closed: the pill says why", pillLive.textContent, TX.liveEmptyList);
    check("panel closed: the pill stays a second", [!!env.pill(), env.timers.byMs(1000).length], [true, 1]);
    env.timers.run((x) => x.ms === 1000);
    check("panel closed: then the pill goes", [!!env.pill(), env.cover.isOn(), env.cover.status()],
      [false, false, TX.liveEmptyList]);
    check("panel closed: focus not moved", env.doc.activeElement === env.doc.player, false);
    // A plain stop drops them as well.
    env = await running();
    const s2 = env.sess();
    env.cover.stop();
    check("stop drops cues and heard", [s2.cues, s2.heard], [null, null]);
  },

  async pill() {
    for (const [mode, name, label] of [["bleep", "Stop bleeping", "Bleeping listed words"],
      ["mute", "Stop muting", "Muting listed words"], ["moo", "Stop mooing", "Mooing over listed words"]]) {
      const env = await running({ muteSound: mode });
      const host = env.pill();
      const stop = host.shadowRoot.all().find((e) => e.tagName === "BUTTON");
      check(mode + ": Stop is a button with its name", [stop.type, stop.textContent, stop.getAttribute("aria-label")],
        ["button", "Stop", name]);
      check(mode + ": label", host.shadowRoot.all().find((e) => e.className === "label").textContent, label);
    }
    const env = await running();
    const host = env.pill();
    const all = host.shadowRoot.all();
    check("pill has its own shadow root", !!host.shadowRoot, true);
    check("pill slot is 136 px", host.style.cssText.includes("bottom:136px"), true);
    check("emoji is aria-hidden", all.find((e) => e.textContent === "🔔").getAttribute("aria-hidden"), "true");
    check("label is not a live region", all.find((e) => e.className === "label").getAttribute("aria-live"), null);
    check("Stop is at least 44x44", all.find((e) => e.tagName === "STYLE").textContent
      .includes("min-width:44px;min-height:44px"), true);
    const live = all.find((e) => e.getAttribute("aria-live") === "polite");
    check("panel open: the pill's live region stays empty", live.textContent, "");
    env.at(10.5);
    check("label says now while active", all.find((e) => e.className === "label").textContent,
      "Bleeping listed words · now");
    const fs = new El("div", env.doc);
    env.doc.body.appendChild(fs);
    env.doc.fullscreenElement = fs;
    env.doc.fire("fullscreenchange");
    check("fullscreen keeps the pill", host.parentNode === fs, true);
    // panel closed: announcements go to the pill, on a later tick
    const e2 = await running({ panel: false });
    const live2 = e2.pill().shadowRoot.all().find((e) => e.getAttribute("aria-live") === "polite");
    check("pill live region created empty", live2.textContent, "");
    e2.ports[0].emit({ type: "alignDone", timed: 0, cached: 0, failed: 0 });
    check("still empty before the next tick", live2.textContent, "");
    e2.timers.run((x) => x.ms === 100);
    check("filled on a later tick", live2.textContent.startsWith("Bleeping 2 spots by caption line"), true);
    // Stop from the pill
    const h2 = e2.pill();
    e2.at(10.5);
    h2.shadowRoot.all().find((e) => e.tagName === "BUTTON").fire("click");
    check("Stop from pill: focus on the player", e2.doc.activeElement === e2.doc.player, true);
    check("Stop from pill: off text in the pill's live region", live2.textContent, TX.offBleep);
    check("Stop from pill: pill still there", !!e2.pill(), true);
    e2.timers.run((x) => x.ms === 1000);
    check("Stop from pill: gone after 1000 ms", e2.pill(), null);
    check("Stop from pill: unmuted, no listeners", [e2.video.muted, e2.counts()], [false, [0, 0, 0]]);
    const e3 = await running();
    e3.pill().shadowRoot.all().find((e) => e.tagName === "BUTTON").fire("click");
    e3.cover.start();
    await flush();
    check("a restart right after Stop leaves one pill",
      e3.doc.body.all().filter((e) => e.id === "tldw-mute-pill").length, 1);
  },
};

if (!groups[GROUP]) { console.error("unknown group: " + GROUP); process.exit(2); }
groups[GROUP]().then(() => {
  console.log("checks=" + n);
  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
}).catch((e) => { console.error(e && e.stack || e); process.exit(1); });
