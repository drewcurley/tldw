// Runs the REAL renderSummary/renderAnswer out of extension/content.js. These are
// the riskiest client functions: they build HTML from model output, so an escaping
// or regex slip is an injection bug in the page. Driven by tests/test_extension_render.py.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const grab = (start, end) => {
  const i = src.indexOf(start);
  if (i < 0) throw new Error("start marker not found in content.js: " + start);
  const j = src.indexOf(end, i);
  // A missing end marker used to slice to EOF and eval the rest of the file, which
  // surfaces as an unrelated syntax error. Fail on the real cause instead.
  if (j < 0) throw new Error("end marker not found in content.js: " + end);
  return src.slice(i, j);
};
eval(grab("const esc = (s) =>", "function renderSummary")
   + grab("function renderSummary", "\n\n  // --- ")
   + grab("  function renderAnswer(text)", "\n  function seekTo"));

// Structural checks match against code, never prose: a comment explaining a fix
// invariably contains the very call the check forbids.
const stripComments = (t) => t.replace(/\/\/[^\n]*/g, "");

const failures = [];
const check = (name, got, want) => {
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(name + " -> " + got);
};

// --- markdown the answers actually use ---
check("bold", renderSummary("say **this**"), "<p>say <strong>this</strong></p>");
check("italic", renderSummary("*Not in the video:* rest"),
  "<p><em>Not in the video:</em> rest</p>");
check("bullets", renderSummary("- one\n- two"), "<ul><li>one</li><li>two</li></ul>");
check("bold in bullets", renderSummary("- **a** b"), "<ul><li><strong>a</strong> b</li></ul>");
check("paragraphs", renderSummary("one\n\ntwo"), "<p>one</p><p>two</p>");

// --- citations become seek links, with the right offsets ---
check("mm:ss", renderAnswer("phone book [12:34]."),
  (h) => h.includes('data-t="754"') && h.includes(">12:34</a>"));
check("h:mm:ss", renderAnswer("later [1:02:05]"),
  (h) => h.includes('data-t="3725"') && h.includes(">1:02:05</a>"));
check("zero", renderAnswer("[0:00] start"), (h) => h.includes('data-t="0"'));
check("several", renderAnswer("a [0:05] b [12:34] c"), (h) => h.split("data-t").length === 3);
check("impossible clock ignored", renderAnswer("[99:99]"), (h) => !h.includes("data-t"));
check("ordinary brackets ignored", renderAnswer("[citation needed]"),
  (h) => !h.includes("data-t"));

// --- model output is data, never markup ---
check("script escaped", renderAnswer("<script>alert(1)</script>"),
  (h) => h.includes("&lt;script&gt;") && !h.includes("<script>"));
check("img onerror escaped", renderAnswer("<img src=x onerror=alert(1)>"),
  (h) => !h.includes("<img") && h.includes("&lt;img"));
check("quotes and ampersands escaped", renderAnswer('he said "hi" & left'),
  (h) => h.includes("&quot;") && h.includes("&amp;"));

// --- the tag-split guard: a timestamp inside an attribute must not be rewritten ---
{
  const saved = renderSummary;
  renderSummary = () => '<a href="http://x/[12:34]">see [12:34]</a>';
  const out = renderAnswer("see [12:34]");
  renderSummary = saved;
  check("timestamp inside a tag attribute left alone", out,
    (h) => h.includes('href="http://x/[12:34]"') && h.split("data-t").length === 2);
}

// --- the key shield's "is this from our modal?" test ---
// Events crossing a shadow boundary are retargeted, so composedPath is the only
// reliable way to tell. Getting this wrong either lets keystrokes reach YouTube
// (space pauses the video while you type) or swallows the page's own shortcuts.
{
  let host = { contains: (n) => n === "inside-host" };
  eval(grab("  function fromModal(e)", "\n  // Swallow every key"));
  check("keystroke from inside the modal is recognised",
    fromModal({ composedPath: () => ["textarea", "shadowroot", host, "body", "document"] }), true);
  check("keystroke from the page is not claimed",
    fromModal({ composedPath: () => ["video", "body", "document"] }), false);
  check("falls back to contains() when composedPath is unavailable",
    fromModal({ target: "inside-host" }), true);
  check("fallback rejects an outside target",
    fromModal({ target: "somewhere-else" }), false);
}

// --- the shield must be installed where it can actually win ---
// YouTube binds space/arrows as capture-phase listeners on `document`; only a
// window capture listener runs earlier. A revert to document-level, or to the
// input's own handler, silently reintroduces the pause-while-typing bug.
{
  check("keydown shield is on window, capturing",
    /window\.addEventListener\("keydown", onKey, true\)/.test(src), true);
  check("keyup and keypress are shielded too",
    /window\.addEventListener\("keyup", shieldKey, true\)/.test(src)
      && /window\.addEventListener\("keypress", shieldKey, true\)/.test(src), true);
  check("shield is removed on close",
    /window\.removeEventListener\("keydown", onKey, true\)/.test(src), true);
  check("chat input has no keydown handler of its own (it could never win)",
    /askinput[\s\S]{0,400}addEventListener\("keydown"/.test(src), false);
}

// --- close() policy: abort vs keep running in the background ---
{
  eval(grab("  function closePolicy(opts, pref)", "\n  let askLive"));
  check("default keeps in-flight work running",
    closePolicy(undefined, "continue").background, true);
  check("unset preference also keeps working (safe default)",
    closePolicy(undefined, undefined).background, true);
  check("abort preference stops the work", closePolicy(undefined, "abort").background, false);
  check("keepSeg is independent of the preference",
    closePolicy({ keepSeg: true }, "abort").keepSeg
      && !closePolicy({ keepSeg: true }, "abort").background, true);
  check("a click event argument is not mistaken for keepSeg",
    closePolicy({ type: "click", target: {} }, "continue").keepSeg, false);
}

// --- the keepalive must survive a background close ---
// clearTimers() kills pingTimer, and that ping is the only thing keeping the MV3
// worker alive. Calling it on the background path is exactly the bug being fixed.
{
  const body = stripComments(
    src.slice(src.indexOf("  function close(opts)"), src.indexOf("  function unmount()")));
  const bg = body.slice(0, body.indexOf("policy.keepSeg") >= 0
    ? body.indexOf("stopSpeak") : body.length);
  check("background close does not clear the keepalive timers",
    /clearTimers\(\)/.test(bg), false);
  check("abort close does clear them",
    /clearTimers\(\)/.test(body.slice(body.indexOf("stopSpeak"))), true);
  check("abort sends the explicit stops a bare disconnect would not",
    /stopSpeak/.test(body) && /stopAsk/.test(body), true);
  check("background close leaves the summarize port connected",
    /port\.disconnect/.test(bg), false);
}

// --- re-opening the panel mid-run must not re-flag the run as backgrounded ---
// mount() used to call close(). close() decides what to do with in-flight work, so
// re-opening immediately marked the run backgrounded again and the summary was
// stashed silently instead of rendering into the modal that had just been opened.
{
  const mountBody = src.slice(src.indexOf("  function mount()"),
                              src.indexOf("  function showLoading()"));
  // Only the prologue matters — mount() legitimately *registers* close() as the
  // backdrop and ✕ handler further down.
  const prologue = stripComments(
    mountBody.slice(0, mountBody.indexOf("host = document.createElement")));
  check("mount() does not run the close() policy before rendering",
    /close\(\)/.test(prologue), false);
  check("mount() unmounts instead", /unmount\(\)/.test(prologue), true);

  const startBody = src.slice(src.indexOf("  function startSummarize("),
                              src.indexOf("  function endBackground("));
  check("a new run retires the previous run's port itself",
    /port\.disconnect/.test(startBody) && /clearTimers\(\)/.test(startBody), true);

  // The bar climbs ~1%/s during the Claude step, so resetting it to the server's
  // last reported figure visibly rewinds. It must carry, and must keep advancing
  // while the panel is shut.
  check("re-attach carries the progress the creep had reached",
    /const carried = progressPct;[\s\S]{0,200}progressPct = carried;/.test(src), true);
  {
    const closeBody = stripComments(src.slice(src.indexOf("  function close(opts)"),
                                              src.indexOf("  function unmount()")));
    check("a background close leaves the creep running",
      /!policy\.background && creepTimer/.test(closeBody), true);
  }
  check("progress is remembered so a re-attach can restore it",
    /lastProgress = m;/.test(src), true);
  check("re-attach replays the remembered progress",
    /backgrounded = false;[\s\S]{0,400}updateProgress\(lastProgress\.message/.test(src), true);
}

// --- mp3 download filename ---
// Mirrors naming.sanitize_field server-side; a stray "/" or ":" here is a failed
// download rather than a cosmetic issue.
{
  eval(grab("  function sanitizeField(value, maxLen)", "\n  // Only offered once"));
  check("plain title and channel",
    audioFileName("How the Internet Works", "Tech Explained"),
    "How the Internet Works - Tech Explained - tldw version.mp3");
  check("path separators and illegal characters are removed",
    audioFileName('A/B: "C" <D>|E?F*G\\H', "Chan"),
    "A B C D E F G H - Chan - tldw version.mp3");
  check("the \" - \" joiner inside a title can't fake a field boundary",
    audioFileName("Part One - Part Two", "Chan"),
    "Part One Part Two - Chan - tldw version.mp3");
  check("whitespace and newlines collapse",
    audioFileName("  spaced\n\tout  ", " Chan "), "spaced out - Chan - tldw version.mp3");
  check("empty fields fall back rather than producing ' - .mp3'",
    audioFileName("", ""), "untitled - untitled - tldw version.mp3");
  check("a title of only illegal characters falls back",
    audioFileName("///", "Chan"), "untitled - Chan - tldw version.mp3");
  check("long titles are capped",
    audioFileName("x".repeat(400), "Chan"), (n) => n.length < 200 && n.endsWith(".mp3"));
  check("no leading or trailing dot survives",
    audioFileName("...dots...", "Chan"), "dots - Chan - tldw version.mp3");
}

// --- the decode behind the download ---
// A wrong byte here is a corrupt mp3 that still "downloads successfully", so the
// round-trip is checked over bytes that break naive string handling.
{
  eval(grab("  function b64ToBytes(b64)", "\n  function pushAudioChunk"));
  const bytes = new Uint8Array([
    0x49, 0x44, 0x33, 0x04, 0x00, 0x00,   // ID3 header, with NULs
    0xff, 0xfb, 0x90, 0x00,               // mp3 frame sync, high bytes
    0x00, 0x7f, 0x80, 0xfe, 0x0a, 0x0d,   // boundary values + newlines
  ]);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  const decoded = b64ToBytes(btoa(bin));
  check("base64 -> bytes round-trips exactly",
    decoded.length === bytes.length && bytes.every((b, i) => decoded[i] === b), true);
  check("decoding yields a Uint8Array Blob can consume",
    decoded instanceof Uint8Array, true);
}

// --- the button must only appear for a complete file ---
{
  const body = stripComments(src.slice(src.indexOf("  function enableDownload("),
                                       src.indexOf("  function renderAudio(")));
  check("download builds a Blob rather than handing over the data: URL",
    /createObjectURL\(new Blob\(/.test(body), true);
  check("the object URL is released", /revokeObjectURL/.test(body), true);
  check("the download starts hidden until a finished clip exists",
    /dl\.hidden = true;/.test(src), true);
  check("a finished stream reveals it",
    /enableDownload\(m\.dataUrl\)/.test(src), true);
}

if (failures.length) {
  console.log("FAILURES:\n" + failures.join("\n"));
  process.exit(1);
}
console.log("ok");
