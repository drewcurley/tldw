// T-020: the REAL extension/options.js (after extension/mute_words.js, the order
// options.html loads them in), with a fake page and a recording storage. Covers
// the "Cover listed words" fields: load, Restore and Save. Made-up words only;
// the shipped default list is never printed.
//
// Usage: node options_mute_checks.js <options.js> <mute_words.js> <content.js>
// Prints "checks=<n>"; exits non-zero on any failure.
const fs = require("fs");
const vm = require("vm");
const [, , OPTIONS, WORDS, CONTENT] = process.argv;

let n = 0;
const failures = [];
const check = (name, actual, expected) => {
  n++;
  const ok = typeof expected === "function" ? expected(actual)
    : JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures.push(`${name} -> ${JSON.stringify(actual)}`);
};
const flush = async () => { for (let k = 0; k < 8; k++) await new Promise((r) => setImmediate(r)); };

function page(stored) {
  const els = {};
  const el = (id) => els[id] || (els[id] = { id, value: "", textContent: "", disabled: false,
    innerHTML: "", options: [], handlers: {},
    addEventListener(t, fn) { this.handlers[t] = fn; }, play() { return Promise.resolve(); } });
  const calls = [];
  const data = { ...stored };
  const storage = {
    get: async (q) => {
      calls.push(["get", q]);
      if (Array.isArray(q)) return Object.fromEntries(q.filter((k) => k in data).map((k) => [k, data[k]]));
      if (typeof q === "string") return q in data ? { [q]: data[q] } : {};
      const out = { ...q };
      for (const k of Object.keys(q)) if (k in data) out[k] = data[k];
      return out;
    },
    set: async (o) => { calls.push(["set", Object.keys(o).sort()]); Object.assign(data, o); },
    remove: async (k) => { calls.push(["remove", k]); delete data[k]; },
  };
  const timeouts = [];
  const ctx = vm.createContext({
    document: { getElementById: el },
    fetch: () => Promise.reject(new Error("offline")),
    setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return timeouts.length; }, URL, console,
    chrome: { storage: { local: storage } },
  });
  vm.runInContext(fs.readFileSync(WORDS, "utf8"), ctx);
  vm.runInContext(fs.readFileSync(OPTIONS, "utf8"), ctx);
  return { ctx, el, calls, data, timeouts, save: () => el("save").handlers.click(),
    restore: () => el("muteRestore").handlers.click() };
}

(async () => {
  const def = (ctx) => ctx.TLDW_MUTE_DEFAULTS.join("\n");

  let p = page({});
  await flush();
  check("never set: textarea shows the default", p.el("muteWords").value === def(p.ctx), true);
  check("never set: sound reads bleep", p.el("muteSound").value, "bleep");
  check("load reads both keys by name", p.calls.some((c) => c[0] === "get"
    && JSON.stringify(c[1]) === JSON.stringify(["muteWords", "muteSound"])), true);
  p.calls.length = 0;
  await p.save();
  // Rev 5.6 (AC18): Save says edits apply at once, for 4 s, then clears.
  check("saved text says edits apply at once", p.el("status").textContent,
    "Saved. Videos where Cover listed words is on use the new settings right away.");
  const clear = p.timeouts.find((x) => x.ms === 4000);
  check("saved text stays 4 s", !!clear, true);
  clear.fn();
  check("saved text then clears", p.el("status").textContent, "");
  check("save with the default removes the key", p.calls.filter((c) => c[0] === "remove"),
    [["remove", "muteWords"]]);
  check("save writes 7 keys plus muteSound", p.calls.find((c) => c[0] === "set")[1],
    ["closeAction", "model", "muteSound", "segmentRatio", "serverUrl", "token", "voice", "whisper"]);
  check("muteWords not pinned", "muteWords" in p.data, false);

  p = page({ muteWords: "Blorp\nzarf", muteSound: "moo" });
  await flush();
  check("stored list shown", p.el("muteWords").value, "Blorp\nzarf");
  check("stored sound shown", p.el("muteSound").value, "moo");
  p.calls.length = 0;
  await p.save();
  check("a different list is saved, normalised", p.data.muteWords, "blorp\nzarf");
  check("no remove for a different list", p.calls.some((c) => c[0] === "remove"), false);

  p = page({ muteWords: "blorp" });
  await flush();
  p.el("muteWords").value = "   ";
  await p.save();
  check("an empty box saves empty (off)", p.data.muteWords, "");

  p = page({ muteWords: "blorp" });
  await flush();
  p.calls.length = 0;
  p.restore();
  check("restore refills the box", p.el("muteWords").value === def(p.ctx), true);
  check("restore writes nothing", p.calls.length, 0);
  await p.save();
  check("save after restore removes the key", [p.calls.some((c) => c[0] === "remove"),
    "muteWords" in p.data], [true, false]);

  p = page({ muteSound: "quack" });
  await flush();
  check("unknown stored sound reads bleep", p.el("muteSound").value, "bleep");
  p.el("muteSound").value = "quack";
  await p.save();
  check("save writes only a valid sound", p.data.muteSound, "bleep");
  p.el("muteSound").value = "mute";
  await p.save();
  check("save writes the picked sound", p.data.muteSound, "mute");

  // One normaliser: options.js and content.js both use mute_words.js's, and
  // re-running mute_words.js keeps the same function.
  const before = p.ctx.TLDW_normaliseMuteList;
  vm.runInContext(fs.readFileSync(WORDS, "utf8"), p.ctx);
  check("normaliser keeps its identity on re-run", p.ctx.TLDW_normaliseMuteList === before, true);
  const opts = fs.readFileSync(OPTIONS, "utf8");
  const content = fs.readFileSync(CONTENT, "utf8");
  check("options.js uses the shared normaliser", opts.includes("globalThis.TLDW_normaliseMuteList"), true);
  check("content.js uses the shared normaliser", content.includes("globalThis.TLDW_normaliseMuteList"), true);
  check("neither defines a second one", /TLDW_normaliseMuteList\s*\?\?=|TLDW_normaliseMuteList\s*=/
    .test(opts + content), false);
  check("defaults frozen", Object.isFrozen(p.ctx.TLDW_MUTE_DEFAULTS), true);

  console.log("checks=" + n);
  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
