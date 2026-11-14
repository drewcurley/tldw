// The real extension/channels.js, run in a vm context: channel identity, what the
// stored list may contain, the run log, the budget, the auto-run decision and the
// panel row's words. Plus the loading rule: running it twice in one context (the
// toolbar re-injects it on every click) must not throw, and it must replace a
// page-defined TLDW_CHANNELS rather than adopt it.
const fs = require("fs");
const vm = require("vm");
const SRC = fs.readFileSync(process.argv[2], "utf8");

const failures = [];
const CHECKS = { n: 0 };
const check = (name, got, want) => {
  CHECKS.n++;
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};
const eq = (name, got, want) => check(name, JSON.stringify(got), JSON.stringify(want));

function load() {
  const ctx = { URL };
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  return ctx;
}

// ------------------------------------------------------------------ loading ----
{
  const ctx = { URL };
  vm.createContext(ctx);
  // What a page element with id="TLDW_CHANNELS" looks like through named access.
  ctx.TLDW_CHANNELS = { channelKey: () => "@evil", fake: true };
  let threw = null;
  try {
    vm.runInContext(SRC, ctx);
    vm.runInContext(SRC, ctx);           // the toolbar's second injection
  } catch (e) { threw = String(e); }
  check("running channels.js twice in one context doesn't throw", threw, null);
  check("a page-defined TLDW_CHANNELS is replaced, not adopted",
    ctx.TLDW_CHANNELS.fake, undefined);
  check("...by the real one", ctx.TLDW_CHANNELS.channelKey("/@Real"), "@real");
  check("the result is frozen", Object.isFrozen(ctx.TLDW_CHANNELS), true);
  // Nothing at top level: a second script declaring the same names must load.
  let clash = null;
  try {
    vm.runInContext("const ORIGIN = 1; const channelKey = 2; let MAX_CHANNELS = 3;", ctx);
  } catch (e) { clash = String(e); }
  check("channels.js declares nothing at top level", clash, null);
}

const C = load().TLDW_CHANNELS;

// --------------------------------------------------------------- channelKey ----
check("handle", C.channelKey("/@Veritasium"), "@veritasium");
check("handle with a trailing path", C.channelKey("/@Veritasium/videos"), "@veritasium");
check("handle, absolute on the right host",
  C.channelKey("https://www.youtube.com/@Veritasium?si=x#y"), "@veritasium");
check("query and hash are ignored", C.channelKey("/@abc?x=1#z"), "@abc");
check("an encoded slash in a handle is not a handle", C.channelKey("/@a%2Fb"), null);
check("an encoded space in a handle is not a handle", C.channelKey("/@a%20b"), null);
check("a percent-encoded handle is decoded",
  C.channelKey("/@%E3%83%86%E3%82%B9%E3%83%88"), "@テスト");
// "é" as e + combining acute (NFD) and as one code point (NFC) are one channel.
check("NFC normalization", C.channelKey("/@Café"), "@café");
check("...the same key as the composed form", C.channelKey("/@CAFÉ"), "@café");
check("channel id", C.channelKey("/channel/UCuVPpxrm2VAgpH3Ktln4HXg"),
  "channel/UCuVPpxrm2VAgpH3Ktln4HXg");
check("channel id keeps its case", C.channelKey("/channel/UCabcdefghijklmnopqrstuV"),
  "channel/UCabcdefghijklmnopqrstuV");
check("channel id with a trailing path",
  C.channelKey("/channel/UCuVPpxrm2VAgpH3Ktln4HXg/featured"),
  "channel/UCuVPpxrm2VAgpH3Ktln4HXg");
check("a short UC id is rejected", C.channelKey("/channel/UCshort"), null);
check("a long UC id is rejected", C.channelKey("/channel/UCuVPpxrm2VAgpH3Ktln4HXgX"), null);
check("another host is rejected", C.channelKey("https://evil.example/@veritasium"), null);
check("youtube.com without www is rejected", C.channelKey("https://youtube.com/@x"), null);
check("m.youtube.com is rejected", C.channelKey("https://m.youtube.com/@x"), null);
check("a malformed escape is null, not a throw", C.channelKey("/@%E0%A4%A"), null);
check("legacy /c/ is out of scope", C.channelKey("/c/SomeName"), null);
check("legacy /user/ is out of scope", C.channelKey("/user/SomeName"), null);
check("a watch link is not a channel", C.channelKey("/watch?v=abcdefghijk"), null);
check("an empty handle", C.channelKey("/@"), null);
check("not a string", C.channelKey(42), null);
check("null", C.channelKey(null), null);
check("empty", C.channelKey(""), null);

// --------------------------------------------------------------- isValidKey ----
check("valid handle key", C.isValidKey("@veritasium"), true);
check("valid id key", C.isValidKey("channel/UCuVPpxrm2VAgpH3Ktln4HXg"), true);
check("an upper-case handle doesn't survive the round trip", C.isValidKey("@Veritasium"), false);
check("an NFD handle doesn't survive the round trip", C.isValidKey("@café"), false);
check("a /c/ key is not valid", C.isValidKey("c/name"), false);
check("a bare name is not valid", C.isValidKey("veritasium"), false);
check("an absolute URL is not a key", C.isValidKey("https://www.youtube.com/@x"), false);
check("a non-string is not a key", C.isValidKey(7), false);

// --------------------------------------------------------- sanitizeChannels ----
eq("a non-array is empty", C.sanitizeChannels("nope"), []);
eq("undefined is empty", C.sanitizeChannels(undefined), []);
eq("junk items are dropped", C.sanitizeChannels([null, 3, "x", [], { key: "@a" },
  { name: "n" }, { key: "Bad Key", name: "n" }, { key: "@ok", name: 5 }]), []);
{
  const out = C.sanitizeChannels([
    { key: "@a", name: "  First  ", href: "/@A", addedAt: 5, extra: { kept: true } },
    { key: "@a", name: "Duplicate" },
    { key: "@b", name: "x".repeat(150), href: "h".repeat(301), addedAt: NaN },
    { key: "@c", name: "C", href: 7, addedAt: Infinity },
    { key: "@d", name: "D", addedAt: "yesterday" },
  ]);
  eq("keys in order, first duplicate wins", out.map((c) => c.key), ["@a", "@b", "@c", "@d"]);
  check("the name is trimmed", out[0].name, "First");
  check("the first duplicate's name is kept", out.filter((c) => c.key === "@a").length, 1);
  eq("unknown fields are kept", out[0].extra, { kept: true });
  check("a valid href is kept", out[0].href, "/@A");
  check("a finite addedAt is kept", out[0].addedAt, 5);
  check("the name is capped at 100", out[1].name.length, 100);
  check("an href over 300 is dropped", "href" in out[1], false);
  check("a NaN addedAt is dropped", "addedAt" in out[1], false);
  check("a non-string href is dropped", "href" in out[2], false);
  check("an infinite addedAt is dropped", "addedAt" in out[2], false);
  check("a string addedAt is dropped", "addedAt" in out[3], false);
  const raw = [{ key: "@a", name: "x".repeat(150) }];
  C.sanitizeChannels(raw);
  check("never writes back into what it read", raw[0].name.length, 150);
}
{
  const many = Array.from({ length: 520 }, (_, i) => ({ key: "@c" + i, name: "C" + i }));
  const out = C.sanitizeChannels(many);
  check("capped at 500", out.length, 500);
  check("...keeping the first 500", out[499].key, "@c499");
}

// ----------------------------------------------------------- sanitizeRunLog ----
const NOW = 1_700_000_000_000;
const H = 3600 * 1000;
eq("run log: a non-array is empty", C.sanitizeRunLog({}, NOW), []);
eq("run log: non-finite and non-numbers dropped",
  C.sanitizeRunLog([NaN, Infinity, "1", null, NOW - 1000], NOW), [NOW - 1000]);
eq("run log: entries in the future are dropped (clock set back)",
  C.sanitizeRunLog([NOW + 60_000, NOW - 60_000], NOW), [NOW - 60_000]);
eq("run log: entries older than a day are dropped",
  C.sanitizeRunLog([NOW - 25 * H, NOW - 23 * H], NOW), [NOW - 23 * H]);

// ------------------------------------------------------------ budgetDecision ----
const recent = (n) => Array.from({ length: n }, (_, i) => NOW - 1000 * (i + 1));
eq("9 this hour: the 10th is allowed", C.budgetDecision(recent(9), NOW), { ok: true });
eq("10 this hour: the 11th is refused, and says which limit",
  C.budgetDecision(recent(10), NOW), { ok: false, limit: "hour" });
const older = (n) => Array.from({ length: n }, (_, i) => NOW - 2 * H - 1000 * i);
eq("39 today (none this hour): the 40th is allowed", C.budgetDecision(older(39), NOW), { ok: true });
eq("40 today: the 41st is refused, as the day's limit",
  C.budgetDecision(older(40), NOW), { ok: false, limit: "day" });
eq("an hour-old run no longer counts towards the hour",
  C.budgetDecision([...recent(9), NOW - H - 1], NOW), { ok: true });
check("the limits are 10 and 40", `${C.HOUR_LIMIT}/${C.DAY_LIMIT}`, "10/40");

// ----------------------------------------------------------- autoRunDecision ----
const chan = { key: "@a", name: "A" };
const base = { channel: chan, channels: [{ key: "@a", name: "A" }], handled: false,
               requestActive: false, panelOpen: false, haveSummary: false };
check("listed, nothing else going on: run", C.autoRunDecision(base), "run");
check("not listed", C.autoRunDecision({ ...base, channels: [{ key: "@b" }] }), "skip:not-listed");
check("empty list", C.autoRunDecision({ ...base, channels: [] }), "skip:not-listed");
check("no channel", C.autoRunDecision({ ...base, channel: null }), "skip:not-listed");
check("already handled", C.autoRunDecision({ ...base, handled: true }), "skip:already-handled");
check("a request is active", C.autoRunDecision({ ...base, requestActive: true }),
  "skip:request-active");
check("the panel is open", C.autoRunDecision({ ...base, panelOpen: true }), "skip:panel-open");
check("already have this summary", C.autoRunDecision({ ...base, haveSummary: true }),
  "skip:have-summary");
check("no state at all", C.autoRunDecision(undefined), "skip:not-listed");

// --------------------------------------------------------------- autoRowText ----
const evil = "<img src=x onerror=alert(1)>";
const notListed = C.autoRowText({ listed: false, name: evil });
check("not listed: the label", notListed.label, "⚡ Always summarize " + evil);
check("...the name stays a plain string", typeof notListed.label, "string");
check("...with a title",
  notListed.title, "Summarize new videos from this channel in the background when you open them");
const listed = C.autoRowText({ listed: true, name: "Chan" });
check("listed: the label", listed.label, "✓ Chan is summarized automatically");
check("listed: Manage", listed.manage, "Manage");
check("the name is capped at 100", C.autoRowText({ listed: false, name: "n".repeat(200) }).label.length,
  "⚡ Always summarize ".length + 100);

if (failures.length) {
  console.error("FAILURES:\n" + failures.join("\n"));
  process.exit(1);
}
console.log("ok " + CHECKS.n + " checks");
