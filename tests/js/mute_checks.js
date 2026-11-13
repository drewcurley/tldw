// T-020 "Cover listed words": the REAL pure functions lifted out of
// extension/content.js (between its BEGIN/END markers) plus extension/mute_words.js,
// run in a node vm. Fixtures use made-up words only; the shipped default list is
// never printed (assert on its length and frozen identity).
//
// Usage: node mute_checks.js <content.js> <mute_words.js> <group>
// Prints "checks=<n>" for the group; exits non-zero on any failure or an unknown group.
const fs = require("fs");
const vm = require("vm");
const [, , CONTENT, WORDS, GROUP] = process.argv;
const src = fs.readFileSync(CONTENT, "utf8");
const BEGIN = "// --- T-020 cover listed words: pure logic (BEGIN)";
const END = "// --- T-020 cover listed words: pure logic (END)";
const i = src.indexOf(BEGIN), j = src.indexOf(END);
if (i < 0 || j < 0) { console.error("pure block markers not found"); process.exit(2); }

const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(WORDS, "utf8"), ctx);
vm.runInContext(src.slice(i, j) + `
  globalThis.__t = { resolveMuteList, muteWordsPattern, muteMatches, mergeMuteRanges,
    buildMuteRanges, muteRanges, alignWindows, normMuteWord, assignWindowWords,
    refineHit, rangeAt, applyRefinement, flushRefinements, muteStep,
    nextBoundaryDelay, soundLengthMs, soundPlan, muteSwitchSound };`, ctx);
const T = ctx.__t;

let n = 0;
const failures = [];
const check = (name, actual, expected) => {
  n++;
  const ok = typeof expected === "function" ? expected(actual)
    : JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures.push(`${name} -> ${JSON.stringify(actual)}`);
};
const norm = ctx.TLDW_normaliseMuteList;
const P = (words) => T.muteWordsPattern(words);
const cue = (sec, text) => ({ start_ms: sec * 1000, text });

// Web Audio's biquad magnitude at f (Hz), Fs 48 kHz. Lowpass/highpass Q is in dB.
function biquadMag(type, f0, q, gainDb, f) {
  const Fs = 48000, w0 = 2 * Math.PI * f0 / Fs, c = Math.cos(w0), sn = Math.sin(w0);
  let b0, b1, b2, a0, a1, a2;
  if (type === "peaking") {
    const A = Math.pow(10, gainDb / 40), al = sn / (2 * q);
    [b0, b1, b2, a0, a1, a2] = [1 + al * A, -2 * c, 1 - al * A, 1 + al / A, -2 * c, 1 - al / A];
  } else {
    const al = sn / (2 * Math.pow(10, q / 20));
    if (type === "lowpass") [b0, b1, b2] = [(1 - c) / 2, 1 - c, (1 - c) / 2];
    else [b0, b1, b2] = [(1 + c) / 2, -(1 + c), (1 + c) / 2];
    [a0, a1, a2] = [1 + al, -2 * c, 1 - al];
  }
  const w = 2 * Math.PI * f / Fs;
  const z = (x0, x1, x2) => [x0 + x1 * Math.cos(w) + x2 * Math.cos(2 * w), -(x1 * Math.sin(w) + x2 * Math.sin(2 * w))];
  const [nr, ni] = z(b0, b1, b2), [dr, di] = z(a0, a1, a2);
  return Math.hypot(nr, ni) / Math.hypot(dr, di);
}

// A param's value at fraction t of the sound, from its set/linear/exp events.
function paramAt(plan, node, t) {
  const ev = plan.events.filter((e) => e.node === node && e.param === "frequency").sort((a, b) => a.at - b.at);
  const end = ev[ev.length - 1].at || 1;
  let v = ev[0].value, at = ev[0].at;
  for (const e of ev.slice(1)) {
    const te = e.at / end;
    if (t >= te) { v = e.value; at = te; continue; }
    const k = (t - at) / (te - at);
    return e.kind === "exp" ? v * Math.pow(e.value / v, k) : v + (e.value - v) * k;
  }
  return v;
}

// The loudest RMS (dB) over the sound: sawtooth harmonics (1/k) to 10 kHz.
function cascadeRmsDb(plan) {
  const filters = plan.chain.map((id) => plan.nodes.find((n) => n.id === id)).filter((n) => n.kind === "filter");
  let best = -Infinity;
  for (let i = 0; i <= 40; i++) {
    const t = i / 40, f0 = paramAt(plan, "osc", t);
    let sum = 0;
    for (let k = 1; k * f0 <= 10000; k++) {
      let h = 1 / k;
      for (const fl of filters) {
        const fc = plan.events.some((e) => e.node === fl.id) ? paramAt(plan, fl.id, t) : fl.freq;
        h *= biquadMag(fl.wave, fc, fl.q, fl.gain || 0, k * f0);
      }
      sum += h * h;
    }
    best = Math.max(best, 10 * Math.log10(sum * plan.level * plan.level));
  }
  return best;
}

const groups = {
  list() {
    check("defaults frozen", Object.isFrozen(ctx.TLDW_MUTE_DEFAULTS), true);
    check("defaults non-empty", ctx.TLDW_MUTE_DEFAULTS.length > 0, true);
    // Re-running the file (a second toolbar click) must not throw or replace it.
    const before = ctx.TLDW_MUTE_DEFAULTS;
    let threw = false;
    try { vm.runInContext(fs.readFileSync(WORDS, "utf8"), ctx); } catch (_) { threw = true; }
    check("mute_words.js re-runs cleanly", threw, false);
    check("re-run keeps the same array", ctx.TLDW_MUTE_DEFAULTS === before, true);
    check("normalise splits commas and new lines",
      norm(" Blorp, zarf\nBLORP\r\n,, q$$ "), ["blorp", "zarf", "q$$"]);
    check("normalise drops over-64", norm("x".repeat(65) + ",ok"), ["ok"]);
    check("normalise caps at 200",
      norm(Array.from({ length: 250 }, (_, k) => "w" + k).join(",")).length, 200);
    check("absent gives the default",
      T.resolveMuteList(undefined, ctx.TLDW_MUTE_DEFAULTS, norm).length,
      norm(ctx.TLDW_MUTE_DEFAULTS.join("\n")).length);
    check("empty string is off", T.resolveMuteList("", ctx.TLDW_MUTE_DEFAULTS, norm), []);
    check("user list", T.resolveMuteList("Blorp\nzarf", ctx.TLDW_MUTE_DEFAULTS, norm),
      ["blorp", "zarf"]);
    check("missing defaults gives off", T.resolveMuteList(undefined, undefined, norm), []);
    check("missing normaliser gives off", T.resolveMuteList("blorp", [], undefined), []);
  },

  pattern() {
    const p = P(["blorp", "q$$", "zärf", "x.y"]);
    const hit = (t) => T.muteMatches(t, p).length;
    check("whole word", hit("a blorp here"), 1);
    check("case-insensitive", hit("BLORP!"), 1);
    check("not inside a word", hit("blorpish unblorp"), 0);
    check("symbol entry", hit("you q$$ there"), 1);
    check("unicode word", hit("ZÄRF."), 1);
    check("unicode boundary", hit("zärfé"), 0);
    check("escaped dot", hit("xzy"), 0);
    check("literal dot", hit("x.y"), 1);
    check("marker", hit("what the [ __ ] is"), 1);
    check("marker tight", hit("[__]"), 1);
    check("two in one line", hit("blorp and [ __ ]"), 2);
    check("empty list is null", P([]), null);
    check("blank-only list is null", P(["  ", ""]), null);
    check("non-array is null", P("blorp"), null);
    check("dedupe + cap", P(Array.from({ length: 300 }, (_, k) => "w" + k)).source.split("|").length <= 201, true);
  },

  ranges() {
    const p = P(["blorp"]);
    const r = T.muteRanges([cue(10, "a blorp"), cue(13, "next"), cue(20, "blorp")], p, 60000);
    check("hit count", r.n, 2);
    check("line lead-in and +1000 tail", r.hits[0].line, [9800, 14000]);
    check("cueEnd is next start, no tail", r.hits[0].cueEnd, 13000);
    check("last cue capped at 8 s", r.hits[1].line, [19800, 28000]);
    check("kind word", r.hits[0].kind, "word");
    check("count", r.hits[0].count, 1);
    const shared = T.muteRanges([cue(5, "blorp"), cue(5, "same second"), cue(7, "later")], p, null);
    check("shared start second uses strictly later", shared.hits[0].line, [4800, 8000]);
    const gap = T.muteRanges([cue(5, "blorp"), cue(60, "far")], p, null);
    check("8 s cap after a long gap", gap.hits[0].line, [4800, 13000]);
    const nodur = T.muteRanges([cue(5, "blorp")], p, NaN);
    check("unknown duration: no NaN", nodur.hits[0].line, [4800, 13000]);
    const dur = T.muteRanges([cue(5, "blorp")], p, 7500);
    check("clamped to duration", dur.hits[0].line, [4800, 7500]);
    const merged = T.muteRanges([cue(1, "blorp"), cue(2, "blorp"), cue(3, "x")], p, null);
    check("touching ranges merge", merged.ranges.length, 1);
    check("merged span", [merged.ranges[0].s, merged.ranges[0].e], [800, 4000]);
    check("total ms", merged.totalMs, 3200);
    check("bad rows dropped", T.muteRanges([{ start_ms: NaN, text: "blorp" },
      { start_ms: 1000, text: "" }, null], p, null).n, 0);
    check("null pattern gives none", T.muteRanges([cue(1, "blorp")], null, null).n, 0);
    const mk = T.muteRanges([cue(1, "[ __ ] and blorp")], p, null);
    check("marker kind", mk.hits[0].kind, "marker");
    check("marker counts with word", mk.hits[0].count, 2);
  },

  windows() {
    const p = P(["blorp"]);
    const hits = T.muteRanges([cue(5, "blorp"), cue(8, "x"), cue(30, "blorp"),
      cue(33, "x"), cue(100, "blorp"), cue(130, "x")], p, 200000).hits;
    const w = T.alignWindows(hits, 0, 200000);
    check("window padding", [w[0].start_ms, w[0].end_ms], [4000, 8500]);
    // A hit whose window is empty once clamped to the duration is dropped.
    const late = [{ id: 0, cueStart: 9000, cueEnd: 9500 }];
    check("empty window after clamping is dropped", T.alignWindows(late, 0, 8000), []);
    // Joining runs on the time-sorted list before the playhead ordering: two hits
    // either side of nowMs still join into one window.
    const pair = [{ id: 0, cueStart: 10000, cueEnd: 12000 }, { id: 1, cueStart: 13000, cueEnd: 14000 }];
    const joined = T.alignWindows(pair, 12500, 100000);
    check("joined before ordering by the playhead", joined.map((x) => x.hits), [[0, 1]]);
    check("12 s cap", w[2].end_ms - w[2].start_ms, 12000);
    check("cap shape", [w[2].start_ms, w[2].end_ms], [99000, 111000]);
    check("all safe non-negative ints", w.every((x) => Number.isSafeInteger(x.start_ms)
      && Number.isSafeInteger(x.end_ms) && x.start_ms >= 0 && x.end_ms > x.start_ms), true);
    const ordered = T.alignWindows(hits, 40000, 200000);
    check("ahead of the playhead first", ordered.map((x) => x.start_ms), [99000, 4000, 29000]);
    const near = T.muteRanges([cue(5, "blorp"), cue(6, "blorp"), cue(7, "x")], p, null).hits;
    const nj = T.alignWindows(near, 0, null);
    check("overlapping windows join", nj.length, 1);
    check("joined hits", nj[0].hits, [0, 1]);
    check("same input, same windows", JSON.stringify(T.alignWindows(hits, 0, 200000)),
      JSON.stringify(w));
    check("unknown duration skips end clamp",
      T.alignWindows(T.muteRanges([cue(5, "blorp"), cue(9, "x")], p, null).hits, 0, NaN)[0].end_ms, 9500);
    check("start clamped at 0",
      T.alignWindows(T.muteRanges([cue(0, "blorp"), cue(2, "x")], p, null).hits, 0, null)[0].start_ms, 0);
    const many = Array.from({ length: 250 }, (_, k) => cue(k * 20, "blorp"));
    check("at most 200 windows", T.alignWindows(T.muteRanges(many, p, null).hits, 0, null).length, 200);
  },

  refine() {
    const p = P(["blorp", "zarf"]);
    const win = { start_ms: 4000, end_ms: 12000 };
    const hit = { id: 0, kind: "word", count: 1, cueStart: 5000, cueEnd: 9000 };
    check("punctuation stripped", T.normMuteWord("  “Blorp!”, "), "Blorp");
    check("word hit refined", T.refineHit(hit, [{ s: 6000, e: 6400, w: "blorp," }], p, win),
      [[5850, 6500]]);
    check("300 ms minimum", T.refineHit(hit, [{ s: 6000, e: 6010, w: "blorp" }], p, win),
      [[5850, 6150]]);
    check("unlisted word ignored", T.refineHit(hit, [{ s: 6000, e: 6400, w: "fine" }], p, win), []);
    check("outside window rejected", T.refineHit(hit, [{ s: 3000, e: 3400, w: "blorp" }], p, win), []);
    check("over 2 s rejected", T.refineHit(hit, [{ s: 6000, e: 8000, w: "blorp" }], p, win), []);
    check("bad timestamp rejected", T.refineHit(hit, [{ s: 6400, e: 6000, w: "blorp" }], p, win), []);
    check("s-150 clamped at 0", T.refineHit(hit, [{ s: 100, e: 500, w: "blorp" }], p,
      { start_ms: 0, end_ms: 9000 }), [[0, 600]]);
    const two = { ...hit, count: 2 };
    check("two listed, one heard: keep line", T.refineHit(two,
      [{ s: 6000, e: 6400, w: "blorp" }], p, win), []);
    check("two listed, both heard: refined", T.refineHit(two,
      [{ s: 6000, e: 6400, w: "blorp" }, { s: 7000, e: 7400, w: "zarf" }], p, win).length, 2);
    const mk = { ...hit, kind: "marker" };
    check("marker: listed word", T.refineHit(mk, [{ s: 6000, e: 6400, w: "zarf" }], p, win).length, 1);
    check("marker: masked word", T.refineHit(mk, [{ s: 6000, e: 6400, w: "b****" }], p, win).length, 1);
    check("marker: whisper writes the marker", T.refineHit(mk, [{ s: 6000, e: 6400, w: "[__]" }], p, win).length, 1);
    check("masked word ignored on a word hit", T.refineHit(hit, [{ s: 6000, e: 6400, w: "b****" }], p, win), []);
    const hits = [{ id: 0, cueStart: 5000, cueEnd: 8000 }, { id: 1, cueStart: 8000, cueEnd: 11000 }];
    const by = T.assignWindowWords({ hits: [0, 1] }, hits,
      [{ s: 5500, e: 5800, w: "a" }, { s: 8500, e: 8800, w: "b" }]);
    check("words assigned to their line", [by.get(0).length, by.get(1).length], [1, 1]);
  },

  refinement() {
    const p = P(["blorp"]);
    const mk = () => {
      const r = T.muteRanges([cue(10, "blorp"), cue(14, "x"), cue(30, "blorp"), cue(34, "x")], p, null);
      return { hits: r.hits, ranges: r.ranges, pending: [] };
    };
    let st = T.applyRefinement(mk(), 1, [[31000, 31500]], 0);
    check("applied at once outside its range", st.ranges.map((r) => [r.s, r.e, r.timed]),
      [[9800, 15000, "line"], [31000, 31500, "word"]]);
    st = T.applyRefinement(mk(), 0, [[11000, 11500]], 12000);
    check("deferred while inside", st.pending.length, 1);
    check("range unchanged while inside", st.ranges[0].e, 15000);
    st = T.flushRefinements(st, 12500);
    check("still deferred inside", st.pending.length, 1);
    st = T.flushRefinements(st, 16000);
    check("applied on exit", [st.ranges[0].s, st.ranges[0].e], [11000, 11500]);
    st = T.applyRefinement(mk(), 0, [], 0);
    check("no match keeps line", st.ranges[0].timed, "line");
    check("no match marks words []", st.hits[0].words, []);
    // Deferral uses the merged range: hit 1's line merges with hit 0's.
    const m = T.muteRanges([cue(10, "blorp"), cue(11, "blorp"), cue(14, "x")], p, null);
    const ms = T.applyRefinement({ hits: m.hits, ranges: m.ranges, pending: [] }, 1,
      [[11200, 11600]], 10000);
    check("merged-range deferral", ms.pending.length, 1);
  },

  quiet() {
    // Rev 5.6: a live sound switch keeps the range in progress silent.
    const base = () => ({ ranges: [{ s: 1000, e: 3000, hits: [0] }, { s: 5000, e: 6000, hits: [1] }],
      videoId: "v", owned: false, lastSet: null, cur: null, soundOn: false, mode: "bleep" });
    const inp = (o) => ({ nowMs: 0, videoMuted: false, videoVolume: 1, videoId: "v",
      adShowing: false, paused: false, userSignal: null, ...o });
    let r = T.muteStep(base(), inp({ nowMs: 1500 }));
    let st = T.muteSwitchSound(r.state, "moo");
    check("switch mid-range: quietCur is the range", st.quietCur, { s: 1000, e: 3000 });
    check("switch: the new mode, no sound", [st.mode, st.soundOn], ["moo", false]);
    check("switch keeps ownership and cur", [st.owned, st.cur], [true, { s: 1000, e: 3000 }]);
    check("switch returns a new object", st !== r.state, true);
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true }));
    check("same range: no sound restarts", [r.action, r.sound, r.state.soundOn], ["none", "none", false]);
    r = T.muteStep(r.state, inp({ nowMs: 3100, videoMuted: true }));
    check("leaving: unmute, quietCur cleared", [r.action, r.state.quietCur], ["exit", null]);
    r = T.muteStep(r.state, inp({ nowMs: 5100 }));
    check("next range: the new sound starts", [r.action, r.sound], ["enter", "start"]);
    check("switch outside a range: quietCur null", T.muteSwitchSound(base(), "moo").quietCur, null);
    // mute -> bleep mid-range stays silent
    r = T.muteStep({ ...base(), mode: "mute" }, inp({ nowMs: 1500 }));
    check("mute mode: no sound", r.sound, "none");
    st = T.muteSwitchSound(r.state, "bleep");
    r = T.muteStep(st, inp({ nowMs: 1700, videoMuted: true }));
    check("mute -> bleep mid-range: silent", r.sound, "none");
    // a seek straight into another range clears it
    r = T.muteStep(T.muteSwitchSound(T.muteStep(base(), inp({ nowMs: 1500 })).state, "moo"),
      inp({ nowMs: 5200, videoMuted: true }));
    check("seek into another range: sound starts", [r.sound, r.state.quietCur], ["start", null]);
    // an ad clears it
    st = T.muteSwitchSound(T.muteStep(base(), inp({ nowMs: 1500 })).state, "moo");
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true, adShowing: true }));
    check("an ad clears quietCur", r.state.quietCur, null);
  },

  step() {
    const base = () => ({ ranges: [{ s: 1000, e: 3000, hits: [0] }, { s: 5000, e: 6000, hits: [1] }],
      videoId: "v", owned: false, lastSet: null, cur: null, soundOn: false, mode: "bleep" });
    const inp = (o) => ({ nowMs: 0, videoMuted: false, videoVolume: 1, videoId: "v",
      adShowing: false, paused: false, userSignal: null, ...o });
    let r = T.muteStep(base(), inp({ nowMs: 1500 }));
    check("enter mutes", r.action, "enter");
    check("enter starts the sound", r.sound, "start");
    let st = r.state;
    r = T.muteStep(st, inp({ nowMs: 2000, videoMuted: true }));
    check("inside: nothing", r.action, "none");
    r = T.muteStep(r.state, inp({ nowMs: 2600, videoMuted: true }));
    check("cap: sound stops after 1.5 s", r.sound, "stop");
    check("cap: still muted", r.action, "none");
    r = T.muteStep(r.state, inp({ nowMs: 3100, videoMuted: true }));
    check("exit unmutes", r.action, "exit");
    // the user's mute wins
    r = T.muteStep(base(), inp({ nowMs: 1500, videoMuted: true }));
    check("already muted: no ownership", [r.action, r.sound, r.state.owned], ["none", "none", false]);
    r = T.muteStep(r.state, inp({ nowMs: 3100, videoMuted: true }));
    check("already muted: never unmuted", r.action, "none");
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: false }));
    check("user unmutes mid-range: release + stop", [r.state.owned, r.sound], [false, "stop"]);
    r = T.muteStep(r.state, inp({ nowMs: 3100 }));
    check("released: no unmute at exit", r.action, "none");
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true, userSignal: "release" }));
    check("user mutes while muted (signal): release + stop", [r.state.owned, r.sound], [false, "stop"]);
    r = T.muteStep(r.state, inp({ nowMs: 3100, videoMuted: true }));
    check("user mute kept at exit", r.action, "none");
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true, videoVolume: 0.4 }));
    check("volume slider keeps ownership", r.state.owned, true);
    r = T.muteStep(base(), inp({ nowMs: 1500, videoVolume: 0 }));
    check("volume 0: muted, no sound", [r.action, r.sound], ["enter", "none"]);
    const mute = { ...base(), mode: "mute" };
    r = T.muteStep(mute, inp({ nowMs: 1500 }));
    check("mute mode: no sound", [r.action, r.sound], ["enter", "none"]);
    const blocked = { ...base(), soundBlocked: true };
    check("sound blocked: mute only", T.muteStep(blocked, inp({ nowMs: 1500 })).sound, "none");
    // pause / play
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true, paused: true }));
    check("pause stops sound, keeps mute", [r.action, r.sound, r.state.owned], ["none", "stop", true]);
    r = T.muteStep(r.state, inp({ nowMs: 1700, videoMuted: true }));
    check("play under the cap restarts", r.sound, "start");
    st = T.muteStep(base(), inp({ nowMs: 1500, paused: true })).state;
    r = T.muteStep(st, inp({ nowMs: 2700, videoMuted: true }));
    check("play past the cap is silent", r.sound, "none");
    // seek
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 5500, videoMuted: true }));
    check("seek into another range: enter", r.action, "enter");
    r = T.muteStep(st, inp({ nowMs: 8000, videoMuted: true }));
    check("seek out: exit", r.action, "exit");
    // ads
    st = T.muteStep(base(), inp({ nowMs: 1500 })).state;
    r = T.muteStep(st, inp({ nowMs: 1600, videoMuted: true, adShowing: true }));
    check("ad: unmute our mute, stop sound", [r.action, r.sound], ["exit", "stop"]);
    r = T.muteStep(r.state, inp({ nowMs: 1700, videoMuted: false, adShowing: true }));
    check("ad: nothing while it shows", r.action, "none");
    // teardown
    r = T.muteStep(base(), inp({ videoId: "other" }));
    check("new video id: teardown", r.action, "teardown");
  },

  timing() {
    const st = { ranges: [{ s: 1000, e: 1300, hits: [0] }], soundOn: false, cur: null };
    check("delay to next edge", T.nextBoundaryDelay(st, 800, 1, false), 200);
    check("rate divides", T.nextBoundaryDelay(st, 800, 2, false), 100);
    check("paused: null", T.nextBoundaryDelay(st, 800, 1, true), null);
    check("rate 0: null", T.nextBoundaryDelay(st, 800, 0, false), null);
    check("far edge: null", T.nextBoundaryDelay(st, -5000, 1, false), null);
    check("exit edge", T.nextBoundaryDelay(st, 1100, 1, false), 200);
    const long = { ranges: [{ s: 1000, e: 8000, hits: [0] }], soundOn: true, cur: { s: 1000, e: 8000 } };
    check("sound cap is an edge", T.nextBoundaryDelay(long, 2000, 1, false), 500);
    // Backstop: 250 ms timeupdate ticks alone catch a 300 ms range at 1x...
    const base = { ranges: [{ s: 1000, e: 1300, hits: [0] }], videoId: "v", owned: false,
      lastSet: null, cur: null, soundOn: false, mode: "mute" };
    const ticks = (rate, offset) => {
      let s = base, entered = false;
      for (let t = offset; t < 3000; t += 250 * rate) {
        const r = T.muteStep(s, { nowMs: t, videoMuted: s.owned, videoVolume: 1, videoId: "v",
          adShowing: false, paused: false, userSignal: null });
        if (r.action === "enter") entered = true;
        s = r.state;
      }
      return entered;
    };
    check("ticks alone enter a 300 ms range at 1x", [0, 50, 100, 150, 200].every((o) => ticks(1, o)), true);
    check("a sub-tick range can be skipped at 1.25x", [0, 50, 100, 150, 200, 250, 300].some((o) => !ticks(1.25, o)), true);
  },

  sound() {
    const r = { s: 1000, e: 1600 };
    check("bleep length to range end", T.soundLengthMs("bleep", r, 1000, 1), 600);
    check("bleep capped at 1.5 s", T.soundLengthMs("bleep", { s: 1000, e: 9000 }, 1000, 1), 1500);
    check("bleep cap counts from range start", T.soundLengthMs("bleep", { s: 1000, e: 9000 }, 2000, 1), 500);
    check("past the cap: silent", T.soundLengthMs("bleep", { s: 1000, e: 9000 }, 2600, 1), 0);
    check("rate divides real length", T.soundLengthMs("bleep", { s: 1000, e: 9000 }, 1000, 2), 750);
    check("moo min 250", T.soundLengthMs("moo", { s: 1000, e: 1100 }, 1000, 1), 250);
    check("moo max 1500", T.soundLengthMs("moo", { s: 1000, e: 9000 }, 1000, 1), 1500);
    const b = T.soundPlan("bleep", 600, 0.5);
    check("bleep sine 1000 Hz", [b.nodes[0].wave, b.nodes[0].freq], ["sine", 1000]);
    check("bleep gain 0.15 x volume", b.volume, 0.075);
    check("bleep 5 ms attack", b.events[1].at, 0.005);
    check("bleep 10 ms release", +(b.events[3].at - b.events[2].at).toFixed(3), 0.01);
    const m = T.soundPlan("moo", 1000, 1);
    const ids = m.nodes.map((x) => x.id);
    check("moo nodes in order", m.nodes.map((x) => [x.id, x.kind, x.wave]), [
      ["osc", "osc", "sawtooth"], ["lfo", "osc", "sine"], ["hp", "filter", "highpass"],
      ["f1", "filter", "peaking"], ["f2", "filter", "peaking"], ["lp", "filter", "lowpass"]]);
    check("moo chain", m.chain, ["osc", "hp", "f1", "f2", "lp"]);
    check("moo has no node called env", ids.includes("env"), false);
    check("every event names a node (or env)", m.events.every((e) => e.node === "env" || ids.includes(e.node)), true);
    const evs = (node) => m.events.filter((e) => e.node === node).map((e) => [e.kind, e.value, +e.at.toFixed(4)]);
    check("moo pitch 120 -> 155 @30% -> 110", evs("osc"), [["set", 120, 0], ["exp", 155, 0.3], ["exp", 110, 1]]);
    check("moo vibrato 4.5 Hz +-2", [m.nodes[1].freq, m.nodes[1].depth, m.nodes[1].modulates], [4.5, 2, "osc"]);
    check("moo highpass 120 Hz, flat Q, no glide", [m.nodes[2].freq, m.nodes[2].q, m.nodes[2].gain, evs("hp")],
      [120, -3.01, undefined, []]);
    check("moo F1 peaking Q3 +9 dB", [m.nodes[3].q, m.nodes[3].gain, m.nodes[3].freq], [3, 9, 300]);
    check("moo F1 300 -> 650 @35% -> 450", evs("f1"), [["set", 300, 0], ["exp", 650, 0.35], ["exp", 450, 1]]);
    check("moo F2 peaking Q4 +5 dB", [m.nodes[4].q, m.nodes[4].gain, m.nodes[4].freq], [4, 5, 1100]);
    check("moo F2 1100 -> 800", evs("f2"), [["set", 1100, 0], ["exp", 800, 1]]);
    check("moo lowpass flat Q, no gain", [m.nodes[5].q, m.nodes[5].gain], [-3.01, undefined]);
    check("moo lowpass 450 -> 1600 @30% -> 900", evs("lp"), [["set", 450, 0], ["exp", 1600, 0.3], ["exp", 900, 1]]);
    check("moo gain 0.25 x volume", [m.level, m.volume, T.soundPlan("moo", 1000, 0.4).volume], [0.25, 0.25, 0.1]);
    const env = (plan) => plan.events.filter((e) => e.node === "env").map((e) => [e.kind, e.value, +e.at.toFixed(4)]);
    check("moo envelope at 1000 ms: 100 ms attack, 250 ms release (caps)", env(m),
      [["set", 0, 0], ["linear", 1, 0.1], ["set", 1, 0.75], ["linear", 0, 1]]);
    check("moo envelope at 250 ms: 25% attack, 35% release", env(T.soundPlan("moo", 250, 1)),
      [["set", 0, 0], ["linear", 1, 0.0625], ["set", 1, 0.1625], ["linear", 0, 0.25]]);
    check("moo ramps never target zero", m.events.filter((e) => e.kind === "exp").every((e) => e.value > 0), true);

    // Loudness guard (rev 5.5): the RMS of a sawtooth through each plan's filter
    // cascade, at its loudest moment, from the Web Audio (RBJ) biquad formulas.
    // The rev 5 moo is the reference; the new one may be at most 1 dB louder.
    const REV5_MOO = { level: 0.25, chain: ["osc", "filter"],
      nodes: [{ id: "osc", kind: "osc" }, { id: "filter", kind: "filter", wave: "lowpass", q: 6, freq: 350 }],
      events: [
        { node: "osc", param: "frequency", kind: "set", value: 105, at: 0 },
        { node: "osc", param: "frequency", kind: "exp", value: 135, at: 0.25 },
        { node: "osc", param: "frequency", kind: "exp", value: 95, at: 1 },
        { node: "filter", param: "frequency", kind: "set", value: 350, at: 0 },
        { node: "filter", param: "frequency", kind: "linear", value: 900, at: 0.35 },
        { node: "filter", param: "frequency", kind: "linear", value: 400, at: 1 }] };
    check("moo RMS within 1 dB of the rev 5 moo", +(cascadeRmsDb(m) - cascadeRmsDb(REV5_MOO)).toFixed(2),
      (d) => d <= 1);
    check("mute mode has no plan", T.soundPlan("mute", 600, 1), null);
  },
};

if (!groups[GROUP]) { console.error("unknown group: " + GROUP); process.exit(2); }
groups[GROUP]();
console.log("checks=" + n);
if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
