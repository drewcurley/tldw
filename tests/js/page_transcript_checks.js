// Runs the REAL page-reading code out of extension/content.js. This is what lets a
// summarize happen without the server contacting YouTube, so both its parsing and
// its failure modes matter -- and every failure must report *which* one it was.
//
// The DOM here is a small but genuine one: nodes match by tag, class, id, attribute
// and descendant, so the selectors in content.js are exercised rather than stubbed.
// If a selector is wrong, these tests are meant to notice.
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

// ---------------------------------------------------------------- tiny DOM ----
function matchPart(n, part) {
  if (part === "*") return true;
  const m = /^([a-z0-9-]+)?((?:[.#][\w-]+)*)((?:\[[^\]]+\])*)$/i.exec(part);
  if (!m) return false;
  if (m[1] && n.tag !== m[1].toLowerCase()) return false;
  for (const bit of (m[2] || "").match(/[.#][\w-]+/g) || []) {
    if (bit[0] === ".") {
      if (!String(n.className).split(/\s+/).includes(bit.slice(1))) return false;
    } else if (n.id !== bit.slice(1)) return false;
  }
  for (const raw of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
    const a = /^([\w-]+)(?:=["']?([^"'\]]*)["']?)?$/.exec(raw.slice(1, -1));
    if (!a) return false;
    const v = n.getAttribute(a[1]);
    if (v === null || v === undefined) return false;
    if (a[2] !== undefined && v !== a[2]) return false;
  }
  return true;
}
function matches(n, sel) {
  return sel.split(",").map((s) => s.trim()).filter(Boolean).some((one) => {
    const parts = one.split(/\s+/);
    if (!matchPart(n, parts[parts.length - 1])) return false;
    let node = n.parent, i = parts.length - 2;
    while (i >= 0) {
      while (node && !matchPart(node, parts[i])) node = node.parent;
      if (!node) return false;
      node = node.parent;
      i--;
    }
    return true;
  });
}
class N {
  constructor(spec = {}) {
    this.tag = (spec.tag || "div").toLowerCase();
    this.id = spec.id || "";
    this.className = spec.cls || "";
    this.attrs = spec.attrs || {};
    this._text = spec.text || "";
    this.onclick = spec.onclick || null;
    this.parent = null;
    this.children = spec.children || [];
    for (const c of this.children) c.parent = this;
  }
  get textContent() {
    return this.children.length
      ? this.children.map((c) => c.textContent).join(" ") : this._text;
  }
  getAttribute(name) {
    if (name === "class") return this.className;
    if (name === "id") return this.id;
    return Object.prototype.hasOwnProperty.call(this.attrs, name)
      ? this.attrs[name] : null;
  }
  click() { if (this.onclick) this.onclick(); }
  add(child) { child.parent = this; this.children.push(child); }
  remove(child) { this.children = this.children.filter((c) => c !== child); }
  all() {
    const out = [];
    for (const c of this.children) { out.push(c); out.push(...c.all()); }
    return out;
  }
  querySelectorAll(sel) { return this.all().filter((n) => matches(n, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// ---------------------------------------------------- the code under test ----
// The real waits are sized for a real page; shrink them so the suite doesn't spend
// twelve seconds proving that a panel which never fills is reported as empty.
const CODE = grab("  const CLOCK = ", "\n  function startSummarize")
  .replace(/PANEL_WAIT_MS = \d+/, "PANEL_WAIT_MS = 500")
  .replace(/PANEL_SETTLE_MS = \d+/, "PANEL_SETTLE_MS = 400")
  .replace(/PANEL_POLL_MS = \d+/, "PANEL_POLL_MS = 20");
const M = {};
eval(CODE + "\nObject.assign(M, { clockToMs, isoDurationMs, readTranscriptRows,"
  + " pageMeta, collectPageTranscript, rowText, rowStartMs });");

// ------------------------------------------------------------- page builder ----
const modernRow = (stamp, words) => new N({
  tag: "transcript-segment-view-model",
  children: [
    new N({ cls: "ytwTranscriptSegmentViewModelTimestamp", text: stamp }),
    new N({ cls: "ytwTranscriptSegmentViewModelTimestampA11yLabel",
            text: "5 seconds" }),
    new N({ tag: "span", cls: "ytAttributedStringHost", text: words }),
  ],
});
const legacyRow = (stamp, words) => new N({
  tag: "ytd-transcript-segment-renderer",
  children: [new N({ cls: "segment", children: [
    new N({ cls: "segment-timestamp", text: stamp }),
    new N({ tag: "yt-formatted-string", cls: "segment-text", text: words })] })],
});

// A page that behaves like YouTube: the rows only exist once the button is clicked,
// and closing the panel is what puts them away again.
function page({ rows = [], duration = "PT10M1S", button = true,
                fillsOnClick = true, title = "T", channel = "C" } = {}) {
  const panelBody = new N({ cls: "panel-body" });
  const closeButton = new N({ tag: "button", attrs: { "aria-label": "Close" } });
  const panel = new N({
    tag: "ytd-engagement-panel-section-list-renderer",
    attrs: { "target-id": "engagement-panel-searchable-transcript" },
    children: [
      new N({ id: "visibility-button", children: [closeButton] }),
      panelBody,
    ],
  });
  const state = { opened: 0, closed: 0 };
  closeButton.onclick = () => {
    state.closed++;
    for (const r of panelBody.children.slice()) panelBody.remove(r);
  };
  const trigger = new N({ tag: "button", onclick: () => {
    state.opened++;
    if (fillsOnClick) for (const r of rows) panelBody.add(r);
  } });
  const kids = [
    new N({ tag: "meta", attrs: { name: "title", content: title } }),
    new N({ tag: "span", attrs: { itemprop: "author" }, children: [
      new N({ tag: "link", attrs: { itemprop: "name", content: channel } })] }),
    panel,
  ];
  if (duration) {
    kids.push(new N({ tag: "meta", attrs: { itemprop: "duration", content: duration } }));
  }
  if (button) {
    kids.push(new N({ tag: "ytd-video-description-transcript-section-renderer",
                      children: [trigger] }));
  }
  global.document = new N({ tag: "#document", children: kids });
  return state;
}

// ------------------------------------------------------------------ units ----
check("clock mm:ss", M.clockToMs("1:05"), 65000);
check("clock h:mm:ss", M.clockToMs("1:02:03"), 3723000);
check("clock rejects a bare number", M.clockToMs("42"), null);
check("clock rejects words", M.clockToMs("a:bc"), null);
check("clock rejects four parts", M.clockToMs("1:2:3:4"), null);
check("iso duration", M.isoDurationMs("PT29M41S"), 1781000);
check("iso duration with hours", M.isoDurationMs("PT1H2M3S"), 3723000);
check("iso duration rejects junk", M.isoDurationMs("29:41"), null);
check("iso duration rejects empty", M.isoDurationMs(""), null);

// The row's screen-reader twin of the timestamp is English-only. Reading it instead
// of the timestamp would break every other locale, so it must never be picked up.
page({});
check("modern row text skips the a11y twin",
  M.rowText(modernRow("0:05", "the words")), "the words");
check("modern row start", M.rowStartMs(modernRow("0:05", "the words")), 5000);
check("legacy row text", M.rowText(legacyRow("0:05", "the words")), "the words");
check("legacy row start", M.rowStartMs(legacyRow("0:05", "the words")), 5000);

// ------------------------------------------------------------ the whole job ----
(async () => {
  let state = page({ rows: [modernRow("0:00", "one"), modernRow("0:05", "two")] });
  let r = await M.collectPageTranscript();
  check("status names the source and the size", r.status, "ok-panel (2 cues)");
  check("cues come back in order",
    JSON.stringify(r.cues),
    JSON.stringify([{ start_ms: 0, text: "one" }, { start_ms: 5000, text: "two" }]));
  check("title", r.title, "T");
  check("channel", r.channel, "C");
  check("duration from the page's own metadata", r.duration_ms, 601000);
  check("the panel was opened", state.opened, 1);
  check("...and put back afterwards", state.closed, 1);

  // Someone who already had the transcript open keeps it open.
  state = page({ rows: [modernRow("0:00", "one")] });
  global.document.querySelector("ytd-video-description-transcript-section-renderer button").click();
  state.opened = 0;
  r = await M.collectPageTranscript();
  check("an already-open panel still reads", r.status, "ok-panel (1 cues)");
  check("...is not re-opened", state.opened, 0);
  check("...and is left as the viewer had it", state.closed, 0);

  state = page({ rows: [legacyRow("0:00", "one"), legacyRow("1:00", "two")] });
  r = await M.collectPageTranscript();
  check("the older renderer is read too", r.status, "ok-panel (2 cues)");
  check("...with its timestamps", r.cues[1].start_ms, 60000);

  // The failure this was built for: the panel opens and never fills, because
  // YouTube refused its own get_transcript call.
  state = page({ rows: [modernRow("0:00", "one")], fillsOnClick: false });
  r = await M.collectPageTranscript();
  check("a panel that never fills says so", r.status, "panel-empty");
  check("...but still reports the metadata", r.title, "T");
  check("...and does not leave a panel open", state.closed, 1);

  r = await (page({ button: false }), M.collectPageTranscript());
  check("a video with no transcript offered says so", r.status, "no-transcript-button");

  page({ rows: [modernRow("0:00", "one")], duration: null });
  r = await M.collectPageTranscript();
  check("no duration on the page is left for the server to infer",
    r.duration_ms, undefined);

  // A row the page renders without words is not a cue.
  page({ rows: [modernRow("0:00", ""), modernRow("0:05", "two")] });
  r = await M.collectPageTranscript();
  check("an empty row is dropped", r.status, "ok-panel (1 cues)");

  page({});
  global.document.querySelector = () => { throw new TypeError("boom"); };
  r = await M.collectPageTranscript();
  check("an unexpected failure is named, not swallowed", r.status, "error-TypeError");

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok");
})();
