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
const CHECKS = { n: 0 };
const check = (name, actual, expected) => {
  CHECKS.n++;
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
  setAttribute(name, value) { this.attrs[name] = String(value); }
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
const RAW = grab("  const CLOCK = ", "\n  function startSummarize");
const CODE = RAW
  .replace(/PANEL_WAIT_MS = \d+/, "PANEL_WAIT_MS = 500")
  .replace(/PANEL_SETTLE_MS = \d+/, "PANEL_SETTLE_MS = 400")
  .replace(/PANEL_POLL_MS = \d+/, "PANEL_POLL_MS = 20")
  .replace(/PAGE_SYNC_MS = \d+/, "PAGE_SYNC_MS = 300")
  // Every sleep is counted, so "no wait when already synced" is asserted by count,
  // not by a timing that a slow machine could make flaky.
  .replace("const sleep = (ms) => new Promise((r) => setTimeout(r, ms));",
           "const sleep = (ms) => { SLEEPS.n++; return new Promise((r) => setTimeout(r, ms)); };");
// A rename would make a shrink silently miss and the suite wait the real 5s per case.
for (const shrunk of ["PANEL_WAIT_MS = 500", "PANEL_SETTLE_MS = 400",
                      "PANEL_POLL_MS = 20", "PAGE_SYNC_MS = 300", "SLEEPS.n++"]) {
  if (!CODE.includes(shrunk)) throw new Error("harness shrink did not match: " + shrunk);
}
const SLEEPS = { n: 0 };
const M = {};
eval(CODE + "\nObject.assign(M, { clockToMs, isoDurationMs, readTranscriptRows,"
  + " pageMeta, collectPageTranscript, rowText, rowStartMs, watchMetadataFor,"
  + " pageSync, PAGE_V });");

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
const VID = "aaaaaaaaaaa";      // the video every synced page is about
const OTHER = "bbbbbbbbbbb";

// `videoId` decides what the page is "about": the live metadata block carries it.
// `identifier` is what the <head> tags are about -- the first video loaded, which
// after an in-page navigation is a different one. `blocks` lists extra
// ytd-watch-metadata elements (a stale one first, when given).
function page({ rows = [], duration = "PT10M1S", button = true,
                fillsOnClick = true, title = "T", channel = "C",
                videoId = VID, identifier = VID, blocks = null,
                liveTitle = "Live T", liveChannel = "Live C" } = {}) {
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
  const block = (vid, t, c) => new N({ tag: "ytd-watch-metadata",
    attrs: vid === null ? {} : { "video-id": vid }, children: [
      new N({ id: "title", children: [new N({ tag: "h1", text: t })] }),
      new N({ id: "owner", children: [new N({ tag: "ytd-channel-name", children: [
        new N({ tag: "a", attrs: { href: "/@x" }, text: c })] })] }),
    ] });
  const metaBlocks = blocks ? blocks.map((b) => block(b.vid, b.title, b.channel))
    : videoId === null ? [] : [block(videoId, liveTitle, liveChannel)];
  state.blocks = metaBlocks;
  state.panelBody = panelBody;
  const kids = [
    ...metaBlocks,
    ...(identifier ? [new N({ tag: "meta",
      attrs: { itemprop: "identifier", content: identifier } })] : []),
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
const okPanel = (n) => (st) => new RegExp("^ok-panel \\(" + n + " cues, synced \\d+ms\\)$").test(st);
const flip = (block, vid) => block.setAttribute("video-id", vid);

(async () => {
  let state = page({ rows: [modernRow("0:00", "one"), modernRow("0:05", "two")] });
  let r = await M.collectPageTranscript(VID);
  check("status names the source, the size and the sync time", r.status, okPanel(2));
  check("cues come back in order",
    JSON.stringify(r.cues),
    JSON.stringify([{ start_ms: 0, text: "one" }, { start_ms: 5000, text: "two" }]));
  check("title", r.title, "T");
  check("channel", r.channel, "C");
  check("duration from the page's own metadata", r.duration_ms, 601000);
  check("the panel was opened", state.opened, 1);
  check("...and put back afterwards", state.closed, 1);
  check("the page object says which extension sent it", r.page_v, 2);

  // Someone who already had the transcript open keeps it open.
  state = page({ rows: [modernRow("0:00", "one")] });
  global.document.querySelector("ytd-video-description-transcript-section-renderer button").click();
  state.opened = 0;
  r = await M.collectPageTranscript(VID);
  check("an already-open panel still reads", r.status, okPanel(1));
  check("...is not re-opened", state.opened, 0);
  check("...and is left as the viewer had it", state.closed, 0);

  state = page({ rows: [legacyRow("0:00", "one"), legacyRow("1:00", "two")] });
  r = await M.collectPageTranscript(VID);
  check("the older renderer is read too", r.status, okPanel(2));
  check("...with its timestamps", r.cues && r.cues[1].start_ms, 60000);

  // The failure this was built for: the panel opens and never fills, because
  // YouTube refused its own get_transcript call.
  state = page({ rows: [modernRow("0:00", "one")], fillsOnClick: false });
  r = await M.collectPageTranscript(VID);
  check("a panel that never fills says so", r.status, "panel-empty");
  check("...but still reports the metadata", r.title, "T");
  check("...and does not leave a panel open", state.closed, 1);
  check("...and still says which extension sent it", r.page_v, 2);

  r = await (page({ button: false }), M.collectPageTranscript(VID));
  check("a video with no transcript offered says so", r.status, "no-transcript-button");
  check("...with page_v", r.page_v, 2);

  page({ rows: [modernRow("0:00", "one")], duration: null });
  r = await M.collectPageTranscript(VID);
  check("no duration on the page is left for the server to infer",
    r.duration_ms, undefined);

  // A row the page renders without words is not a cue.
  page({ rows: [modernRow("0:00", ""), modernRow("0:05", "two")] });
  r = await M.collectPageTranscript(VID);
  check("an empty row is dropped", r.status, okPanel(1));

  page({});
  global.document.querySelector = () => { throw new TypeError("boom"); };
  global.document.querySelectorAll = () => { throw new TypeError("boom"); };
  r = await M.collectPageTranscript(VID);
  check("an unexpected failure is named, not swallowed", r.status, "error-TypeError");
  check("...with page_v", r.page_v, 2);

  // ----------------------------------------- after an in-page navigation ----
  // Measured on YouTube: the <head> tags still describe the first video loaded,
  // and the live ytd-watch-metadata block is the only thing that tracks the
  // current one.
  page({ identifier: OTHER, title: "Me at the zoo", channel: "jawed",
         duration: "PT0M19S",
         blocks: [{ vid: OTHER, title: "Stale", channel: "Stale C" },
                  { vid: VID, title: "Corpse Bride", channel: "Movies" }] });
  let meta = M.pageMeta(global.document, VID);
  check("stale tags: the title comes from this video's own block", meta.title, "Corpse Bride");
  check("stale tags: ...and the channel", meta.channel, "Movies");
  check("stale tags: no duration rather than the first video's",
    meta.duration_ms, undefined);
  check("fresh tags are read as before",
    JSON.stringify(M.pageMeta((page({}), global.document), VID)),
    JSON.stringify({ title: "T", channel: "C", duration_ms: 601000 }));
  check("neither tags nor a block for this video: nothing",
    JSON.stringify(M.pageMeta((page({ identifier: OTHER, videoId: OTHER }),
                               global.document), VID)), "{}");
  page({ identifier: OTHER, liveChannel: "<img src=x onerror=alert(1)>" });
  check("a channel name with markup comes back as the text it is",
    M.pageMeta(global.document, VID).channel, "<img src=x onerror=alert(1)>");

  page({});
  check("watchMetadataFor finds this video's block",
    !!M.watchMetadataFor(VID, global.document), true);
  page({ blocks: [{ vid: "not an id!!", title: "x", channel: "y" }] });
  check("an id that isn't one finds nothing, even with a matching attribute",
    M.watchMetadataFor("not an id!!", global.document), null);
  page({ blocks: [{ vid: OTHER, title: "x", channel: "y" }] });
  check("the first block is not taken when it's another video's",
    M.watchMetadataFor(VID, global.document), null);

  check("pageSync: synced", (page({}), M.pageSync(VID)), "synced");
  check("pageSync: a layout without the block, on a full load of this video",
    (page({ videoId: null }), M.pageSync(VID)), "fresh-load");
  check("pageSync: the block is another video's",
    (page({ videoId: OTHER }), M.pageSync(VID)), "waiting");
  check("pageSync: no id is never synced", (page({}), M.pageSync(undefined)), "waiting");
  check("pageSync: a bad id is never synced", (page({}), M.pageSync("x")), "waiting");

  // The page catches up while we wait: A's rows are still in the panel and the
  // block is still A's. YouTube clears the rows in the same moment the block flips.
  state = page({ videoId: OTHER, identifier: OTHER,
                 rows: [modernRow("0:00", "new one"), modernRow("0:05", "new two")],
                 liveTitle: "New title", liveChannel: "New channel" });
  const staleRow = modernRow("0:00", "STALE previous video");
  state.panelBody.add(staleRow);
  setTimeout(() => {
    state.panelBody.remove(staleRow);
    flip(state.blocks[0], VID);
  }, 60);
  r = await M.collectPageTranscript(VID);
  check("delayed sync: reads only the new video's rows",
    JSON.stringify((r.cues || []).map((c) => c.text)), JSON.stringify(["new one", "new two"]));
  check("delayed sync: the metadata is read after the wait, not before",
    r.title, "New title");
  check("delayed sync: and the panel was opened for it", state.opened, 1);

  state = page({ videoId: OTHER, identifier: OTHER, rows: [modernRow("0:00", "x")] });
  state.panelBody.add(modernRow("0:00", "STALE"));
  r = await M.collectPageTranscript(VID);
  check("never syncs: the server is left to fetch it", r.status, "page-not-synced");
  check("never syncs: nothing clicked", state.opened, 0);
  check("never syncs: no stale cues sent", r.cues, undefined);
  check("never syncs: page_v still sent", r.page_v, 2);

  state = page({ videoId: null, rows: [modernRow("0:00", "one")] });
  SLEEPS.n = 0;
  let freshSleeps = -1;
  const freshTrig = global.document.querySelector(
    "ytd-video-description-transcript-section-renderer button");
  const freshClick = freshTrig.onclick;
  freshTrig.onclick = () => { freshSleeps = SLEEPS.n; freshClick(); };
  r = await M.collectPageTranscript(VID);
  check("fresh-load: read as before", r.status, okPanel(1));
  check("fresh-load: no wait at all before the click", freshSleeps, 0);

  // Back to the first video after a full load: <head> is about A again, but the
  // block -- and the rows -- are still B's. A block exists, so this must wait.
  state = page({ videoId: OTHER, identifier: VID, rows: [modernRow("0:00", "A's own")] });
  const bRow = modernRow("0:00", "B's row");
  state.panelBody.add(bRow);
  setTimeout(() => { state.panelBody.remove(bRow); flip(state.blocks[0], VID); }, 60);
  r = await M.collectPageTranscript(VID);
  check("back-navigation: not mistaken for a fresh load",
    JSON.stringify((r.cues || []).map((c) => c.text)), JSON.stringify(["A's own"]));

  page({ rows: [modernRow("0:00", "one")] });
  r = await M.collectPageTranscript(undefined);
  check("no video id: never reads", r.status, "page-not-synced");
  r = await M.collectPageTranscript("bad");
  check("a bad video id: never reads", r.status, "page-not-synced");

  state = page({ rows: [modernRow("0:00", "one")] });
  SLEEPS.n = 0;
  let sleepsAtClick = -1;
  const trig = global.document.querySelector(
    "ytd-video-description-transcript-section-renderer button");
  const realClick = trig.onclick;
  trig.onclick = () => { sleepsAtClick = SLEEPS.n; realClick(); };
  await M.collectPageTranscript(VID);
  check("already synced: no wait at all before the click", sleepsAtClick, 0);

  state = page({ videoId: OTHER, rows: [modernRow("0:00", "one")] });
  r = await M.collectPageTranscript(VID, () => false);
  check("cancelled while waiting: says so", r.status, "cancelled");
  check("cancelled while waiting: nothing clicked", state.opened, 0);

  state = page({ rows: [modernRow("0:00", "one")] });
  r = await M.collectPageTranscript(VID, () => false);
  check("cancelled just before the click: says so", r.status, "cancelled");
  check("cancelled just before the click: the panel is not opened", state.opened, 0);

  state = page({ rows: [modernRow("0:00", "one")] });
  r = await M.collectPageTranscript(VID, () => true);
  check("a current run reads normally", r.status, okPanel(1));

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok " + CHECKS.n + " checks");
})();
