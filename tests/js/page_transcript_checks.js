// Runs the REAL page-reading code out of extension/content.js. This is what lets a
// summarize happen without the server contacting YouTube, so both its parsing and
// its failure modes matter — and every failure must report *which* one it was.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const grab = (start, end) => {
  const i = src.indexOf(start);
  if (i < 0) throw new Error("not found: " + start);
  const j = src.indexOf(end, i);
  if (j < 0) throw new Error("end not found: " + end);
  return src.slice(i, j);
};

const failures = [];
const check = (name, actual, expected) => {
  const ok = typeof expected === "function" ? expected(actual) : actual === expected;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(actual)}`);
};

eval(grab("  function extractJson(", "\n  function pickCaptionTrack"));
eval(grab("  function pickCaptionTrack(", "\n  // Always resolves"));
const COLLECT = grab("  async function collectPageTranscript(", "\n  function startSummarize");

// --- pulling the player blob out of a megabyte of page source ---
check("simple object", extractJson('x = {"a":1};', "x"), '{"a":1}');
check("nested braces", extractJson('var p = {"a":{"b":2}}; more', "p"), '{"a":{"b":2}}');
check("braces inside strings don't end it",
  extractJson('p = {"a":"}{","b":1}', "p"), '{"a":"}{","b":1}');
check("escaped quotes inside strings",
  extractJson('p = {"a":"say \\\\"hi\\\\" }","b":2}', "p"), '{"a":"say \\\\"hi\\\\" }","b":2}');
check("stops at the end of this object, not the next",
  extractJson('p = {"a":1}; q = {"b":2}', "p"), '{"a":1}');
check("marker absent", extractJson("nothing here", "p"), null);
check("unterminated object", extractJson('p = {"a":1', "p"), null);
check("real shape survives", JSON.parse(extractJson(
  'var ytInitialPlayerResponse = {"videoDetails":{"title":"A } B"}};',
  "ytInitialPlayerResponse")).videoDetails.title, "A } B");

// --- choosing a caption track ---
const player = (tracks) => ({
  captions: { playerCaptionsTracklistRenderer: { captionTracks: tracks } } });
check("real transcript beats auto-captions", pickCaptionTrack(player([
  { baseUrl: "auto-en", kind: "asr", languageCode: "en" },
  { baseUrl: "manual-de", languageCode: "de" }])).baseUrl, "manual-de");
check("English beats another language among equals", pickCaptionTrack(player([
  { baseUrl: "auto-es", kind: "asr", languageCode: "es" },
  { baseUrl: "auto-en", kind: "asr", languageCode: "en" }])).baseUrl, "auto-en");
check("no tracks", pickCaptionTrack(player([])), null);
check("no captions section", pickCaptionTrack({}), null);
check("no player at all", pickCaptionTrack(null), null);

// --- the whole collection, and what it reports when it can't ---
// capResponses: one response per caption attempt, in order (json3, vtt, raw).
function collector(html, capResponses) {
  const fetches = [];
  const queue = Array.isArray(capResponses) ? capResponses.slice() : [capResponses];
  global.URL = URL;
  global.location = { href: "https://www.youtube.com/watch?v=abc",
                      origin: "https://www.youtube.com" };
  global.fetch = (url) => {
    fetches.push(url);
    if (fetches.length === 1) {
      return html === null
        ? Promise.reject(new TypeError("Failed to fetch"))
        : Promise.resolve({ ok: true, text: () => Promise.resolve(html) });
    }
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  };
  const m = {};
  eval(COLLECT + "\nm.fn = collectPageTranscript;");
  return { run: m.fn, fetches };
}
const PLAYER = (tracks) => 'var ytInitialPlayerResponse = ' + JSON.stringify({
  videoDetails: { title: "T", author: "C", lengthSeconds: "600" },
  captions: { playerCaptionsTracklistRenderer: { captionTracks: tracks } },
}) + ';';
const OK_CAPS = { ok: true, text: () => Promise.resolve("WEBVTT\n\nhi\n") };

(async () => {
  let c = collector(PLAYER([{ baseUrl: "https://x/en", languageCode: "en" }]), OK_CAPS);
  let r = await c.run();
  check("status names the format that worked", r.status, "ok-json3");
  check("and the format travels with the text", r.captions_format, "json3");
  check("title", r.title, "T");
  check("channel", r.channel, "C");
  check("duration in ms", r.duration_ms, 600000);
  check("captions", r.captions, "WEBVTT\n\nhi\n");
  check("asks the player's own format first",
    c.fetches[1], (u) => u.includes("fmt=json3"));

  // YouTube answers 200-with-nothing for a format it won't serve, so falling back
  // to another one is the difference between working and not.
  const EMPTY = { ok: true, text: () => Promise.resolve("") };
  c = collector(PLAYER([{ baseUrl: "https://x/en" }]), [EMPTY, OK_CAPS]);
  r = await c.run();
  check("empty json3 falls through to vtt", r.status, "ok-vtt");
  check("...and asked for vtt second", c.fetches[2], (u) => u.includes("fmt=vtt"));

  c = collector(PLAYER([{ baseUrl: "https://x/en" }]), [EMPTY, EMPTY, OK_CAPS]);
  r = await c.run();
  check("then the url as the page gave it", r.status, "ok-raw");
  check("...with no fmt of ours attached", c.fetches[3], (u) => !u.includes("fmt="));

  c = collector(PLAYER([{ baseUrl: "https://x/en?fmt=srv3" }]), OK_CAPS);
  await c.run();
  check("an fmt already on the url is replaced, not duplicated",
    (c.fetches[1].match(/fmt=/g) || []).length, 1);

  r = await collector("no player blob here", OK_CAPS).run();
  check("page without player data says so", r.status, "no-player-data");

  r = await collector("ytInitialPlayerResponse = {oops", OK_CAPS).run();
  check("truncated blob says so", r.status, "no-player-data");

  r = await collector(PLAYER([]), OK_CAPS).run();
  check("video with no captions says so", r.status, "no-captions");
  check("...but still reports the metadata", r.title, "T");

  r = await collector(PLAYER([{ baseUrl: "https://x/en" }]),
    { ok: false, status: 429 }).run();
  check("every attempt refused reports each code",
    r.status, "captions-empty (json3-429,vtt-429,raw-429)");

  r = await collector(PLAYER([{ baseUrl: "https://x/en" }]),
    { ok: true, text: () => Promise.resolve("   ") }).run();
  check("every attempt empty says so",
    r.status, "captions-empty (json3-empty,vtt-empty,raw-empty)");

  r = await collector(null, OK_CAPS).run();
  check("network failure is named, not swallowed", r.status, "error-TypeError");

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok");
})();
