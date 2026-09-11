// Runs the REAL renderSummary/renderAnswer out of extension/content.js. These are
// the riskiest client functions: they build HTML from model output, so an escaping
// or regex slip is an injection bug in the page. Driven by tests/test_extension_render.py.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const grab = (start, end) => {
  const i = src.indexOf(start);
  if (i < 0) throw new Error("marker not found in content.js: " + start);
  return src.slice(i, src.indexOf(end, i));
};
eval(grab("const esc = (s) =>", "function renderSummary")
   + grab("function renderSummary", "\n\n  // --- ")
   + grab("  function renderAnswer(text)", "\n  function seekTo"));

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

if (failures.length) {
  console.log("FAILURES:\n" + failures.join("\n"));
  process.exit(1);
}
console.log("ok");
