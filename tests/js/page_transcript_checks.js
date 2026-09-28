// Runs the REAL readPageTranscript out of extension/background.js. This is what
// lets a summarize happen without the server contacting YouTube at all, so its
// track selection and its failure modes both matter.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const i = src.indexOf("function readPageTranscript()");
if (i < 0) throw new Error("readPageTranscript not found in background.js");
const BODY = src.slice(i, src.indexOf("async function pageTranscript"));

const failures = [];
const check = (name, actual, expected) => {
  const ok = typeof expected === "function" ? expected(actual) : actual === expected;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(actual)}`);
};

function load(playerResponse, fetchImpl) {
  global.window = playerResponse ? { ytInitialPlayerResponse: playerResponse } : {};
  global.fetch = fetchImpl || (() =>
    Promise.resolve({ ok: true, text: () => Promise.resolve("WEBVTT\n") }));
  const module = {};
  eval(BODY + "\nmodule.fn = readPageTranscript;");
  return module.fn();
}

const tracks = (list) => ({
  videoDetails: { title: "T", author: "C", lengthSeconds: "600" },
  captions: { playerCaptionsTracklistRenderer: { captionTracks: list } },
});

(async () => {
  let asked = null;
  const spy = (u) => {
    asked = u;
    return Promise.resolve({ ok: true, text: () => Promise.resolve("WEBVTT\n") });
  };

  await load(tracks([
    { baseUrl: "https://x/auto-es", kind: "asr", languageCode: "es" },
    { baseUrl: "https://x/auto-en", kind: "asr", languageCode: "en" },
    { baseUrl: "https://x/manual-en", languageCode: "en" },
  ]), spy);
  check("prefers a real transcript over auto-captions", asked, "https://x/manual-en&fmt=vtt");

  await load(tracks([
    { baseUrl: "https://x/auto-es", kind: "asr", languageCode: "es" },
    { baseUrl: "https://x/auto-en", kind: "asr", languageCode: "en" },
  ]), spy);
  check("prefers English among auto-captions", asked, "https://x/auto-en&fmt=vtt");

  check("asks for vtt, which the server already parses",
    asked.endsWith("&fmt=vtt"), true);

  const meta = await load(tracks([{ baseUrl: "https://x/en", languageCode: "en" }]));
  check("carries the title", meta.title, "T");
  check("carries the channel", meta.channel, "C");
  check("carries the duration in ms", meta.duration_ms, 600000);
  check("carries the captions", meta.captions, "WEBVTT\n");

  // Failure modes: every one of these must degrade to "let the server fetch it"
  // rather than throw, because a throw inside executeScript loses the summary.
  check("no player data at all", JSON.stringify(await load(null)), "{}");
  const noTracks = await load({ videoDetails: { title: "T", author: "C",
                                                lengthSeconds: "600" } });
  check("player data but no caption tracks", noTracks.captions, undefined);
  check("...still returns the metadata", noTracks.title, "T");
  check("empty track list", (await load(tracks([]))).captions, undefined);
  check("a track with no url", (await load(tracks([{ languageCode: "en" }]))).captions,
    undefined);
  const failed = await load(tracks([{ baseUrl: "https://x/en" }]),
    () => Promise.resolve({ ok: false }));
  check("caption fetch rejected by YouTube", failed.captions, "");
  const threw = await load(tracks([{ baseUrl: "https://x/en" }]),
    () => Promise.reject(new Error("network")));
  check("caption fetch threw", threw.captions, undefined);

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok");
})();
