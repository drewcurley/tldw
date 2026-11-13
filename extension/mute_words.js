// "Cover listed words" (T-020): the built-in default word list, and the one
// normaliser both content.js and options.js use.
//
// executeScript re-runs this file on every toolbar click (background.js), so it
// declares nothing at top level: a top-level const/let would throw "already
// declared" on the second click. Everything hangs off globalThis with ??=.
//
// The defaults are hand-written, mild and common, with no slurs. This file is the
// ONLY place they appear: never in docs, tasks, reviews, commits or test fixtures.

globalThis.TLDW_MUTE_DEFAULTS ??= Object.freeze([
  "damn", "goddamn", "hell", "shit", "bullshit", "fuck", "fucking",
  "ass", "asshole", "piss", "bastard",
]);

// Split on commas and new lines; trim, lowercase, dedupe; drop empties and entries
// over 64 characters; keep at most 200. Returns a new array.
globalThis.TLDW_normaliseMuteList ??= function (text) {
  const out = [];
  const seen = new Set();
  for (const raw of String(text ?? "").split(/[,\n\r]/)) {
    const w = raw.trim().toLowerCase();
    if (!w || w.length > 64 || seen.has(w)) continue;
    seen.add(w);
    out.push(w);
    if (out.length >= 200) break;
  }
  return out;
};
