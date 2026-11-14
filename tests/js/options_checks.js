// ALL of extension/options.js (after channels.js, as options.html loads them) run
// against the fake DOM and a fake `chrome`. Covers the "Automatic summaries"
// section: the checkbox's permission request (made inside the click, before any
// await), refusal, unsupported browsers, revoked access, and the channel list --
// which this page never writes, only asks the worker to change.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const D = require(path.join(__dirname, "fakedom.js"));
const OPTIONS = fs.readFileSync(process.argv[2], "utf8");
const CHANNELS = fs.readFileSync(process.argv[3], "utf8");

const failures = [];
const CHECKS = { n: 0 };
const check = (name, got, want) => {
  CHECKS.n++;
  const ok = typeof want === "function" ? want(got) : got === want;
  if (!ok) failures.push(`${name} -> ${JSON.stringify(got)}`);
};
const tick = () => new Promise((r) => setImmediate(r));
const flush = async (n = 12) => { for (let i = 0; i < n; i++) await tick(); };
const ORIGIN = "https://www.youtube.com/*";

function page() {
  const doc = new D.Document();
  const E = (tag, attrs, kids) => D.el(doc, tag, attrs, kids);
  doc.body.append(
    E("input", { id: "serverUrl" }), E("input", { id: "token" }),
    E("select", { id: "voice", __allowInnerHTML: true }),   // fillVoices writes <option>s
    E("button", { id: "preview" }), E("audio", { id: "sample" }),
    E("span", { id: "previewStatus" }), E("select", { id: "model" }),
    E("span", { id: "modelNote" }), E("select", { id: "segmentRatio" }),
    E("select", { id: "whisper" }), E("select", { id: "closeAction" }),
    E("button", { id: "save" }), E("span", { id: "status" }),
    E("section", { id: "auto" }, [
      E("h3", { id: "autoHeading", tabindex: "-1" }, "Automatic summaries"),
      E("p", { id: "autoUnsupported", hidden: "" }, "Automatic summaries aren't supported in this browser."),
      E("div", { id: "autoControls" }, [
        E("div", { id: "autoToggle" }, [E("input", { id: "autoRun", type: "checkbox" })]),
        E("p", { id: "autoStatus", role: "status", "aria-live": "polite" }),
        E("ul", { id: "autoChannels" }),
        E("p", { id: "autoEmpty", hidden: "" }),
      ]),
    ]),
  );
  return doc;
}

function options({ stored = {}, granted = false, permissions = true, scripting = true,
                   request } = {}) {
  const o = { stored: { ...stored }, granted, sets: [], requests: [], removes: [],
              messages: [], requestSawDisabled: null };
  const doc = page();
  o.doc = doc;
  const changed = [], added = [], removed = [];
  o.fireChanged = (changes) => changed.forEach((fn) => fn(changes, "local"));
  o.fireAdded = () => added.forEach((fn) => fn({ origins: [ORIGIN] }));
  o.fireRemoved = () => removed.forEach((fn) => fn({ origins: [ORIGIN] }));
  o.reply = (msg) => {
    // The worker, as far as this page can see it: it removes, then answers.
    if (msg.type === "channels:remove") {
      const next = (o.stored.autoChannels || []).filter((c) => c.key !== msg.key);
      o.stored.autoChannels = next;
      return { ok: true, result: "removed" };
    }
    return { ok: false };
  };
  const api = {
    runtime: {
      sendMessage: (msg) => { o.messages.push(msg); return Promise.resolve(o.reply(msg)); },
    },
    storage: {
      local: {
        async get(defaults) {
          const out = {};
          for (const [k, v] of Object.entries(defaults || {})) {
            out[k] = Object.prototype.hasOwnProperty.call(o.stored, k)
              ? JSON.parse(JSON.stringify(o.stored[k])) : v;
          }
          return out;
        },
        async set(obj) {
          o.sets.push(JSON.parse(JSON.stringify(obj)));
          Object.assign(o.stored, JSON.parse(JSON.stringify(obj)));
        },
      },
      onChanged: { addListener: (fn) => changed.push(fn) },
    },
  };
  if (permissions) {
    api.permissions = {
      contains: async ({ origins }) => o.granted && origins[0] === ORIGIN,
      request: (req) => {
        o.requests.push(req);
        o.requestSawDisabled = doc.getElementById("autoRun").disabled;
        return request ? request(req) : Promise.resolve(true);
      },
      remove: async (req) => { o.removes.push(req); o.granted = false; return true; },
      onAdded: { addListener: (fn) => added.push(fn) },
      onRemoved: { addListener: (fn) => removed.push(fn) },
    };
  }
  if (scripting) api.scripting = { registerContentScripts: async () => {} };
  const ctx = {
    chrome: api, document: doc, URL, console: { log() {}, warn() {}, error() {}, info() {} },
    setTimeout: (fn) => 0, clearTimeout() {},
    fetch: async () => { throw new TypeError("Failed to fetch"); },
  };
  vm.createContext(ctx);
  vm.runInContext(CHANNELS, ctx);
  vm.runInContext(OPTIONS, ctx);
  o.ctx = ctx;
  o.$ = (id) => doc.getElementById(id);
  o.status = () => o.$("autoStatus").textContent;
  o.rows = () => o.$("autoChannels").querySelectorAll("li");
  o.removeButtons = () => o.$("autoChannels").querySelectorAll("button");
  return o;
}

// A real click on a checkbox: the browser flips `checked` first, then dispatches.
function clickCheckbox(box) {
  box.checked = !box.checked;
  box.dispatchEvent(D.event("click"));
}

(async () => {
  // ------------------------------------------------------------- toggle ----
  {
    const o = options();
    await flush();
    const box = o.$("autoRun");
    check("starts unticked", box.checked, false);
    let gate;
    o.ctx.chrome.permissions.request = (req) => {
      o.requests.push(req);
      o.requestSawDisabled = box.disabled;
      return new Promise((r) => { gate = r; });
    };
    clickCheckbox(box);
    // Synchronous: before the dispatch returned, the request was already made.
    check("the permission is requested inside the click, before any await", o.requests.length, 1);
    check("...for exactly the YouTube origin",
      JSON.stringify(o.requests[0]), JSON.stringify({ origins: [ORIGIN] }));
    check("...with the box disabled while it's asked", o.requestSawDisabled, true);
    check("...and unticked until the answer", box.checked, false);
    gate(true);
    await flush();
    check("granted: ticked", box.checked, true);
    check("...re-enabled", box.disabled, false);
    check("...autoRun saved", o.stored.autoRun, true);
    check("...and announced", o.status(), "On. Open or reload a YouTube tab to start.");

    // Unticking hands the access back.
    o.granted = true;
    clickCheckbox(box);
    await flush();
    check("unticked: autoRun false", o.stored.autoRun, false);
    check("...and announced", o.status(), "Off.");
    check("...and the permission removed",
      JSON.stringify(o.removes[0]), JSON.stringify({ origins: [ORIGIN] }));
    check("...no new request", o.requests.length, 1);
  }
  {
    const o = options({ request: async () => false });
    await flush();
    clickCheckbox(o.$("autoRun"));
    await flush();
    check("refused: stays unticked", o.$("autoRun").checked, false);
    check("...autoRun not saved", o.stored.autoRun, undefined);
    check("...says so", o.status(),
      "Automatic summaries need access to youtube.com, which wasn't given. Nothing changed.");
    check("...and the box is usable again", o.$("autoRun").disabled, false);
  }
  {
    // The origin can't be requested at all: that's "not supported", not a refusal.
    const o = options({ request: () => Promise.reject(new Error("not requestable")) });
    await flush();
    clickCheckbox(o.$("autoRun"));
    await flush();
    check("a rejected request shows 'not supported'", o.$("autoUnsupported").hidden, false);
    check("...and hides the switch", o.$("autoToggle").hidden, true);
    check("...but not the list, which stays editable", o.$("autoControls").hidden, false);
    check("...not the refused line", o.status().includes("wasn't given"), false);
  }
  {
    const o = options({ request: () => { throw new Error("sync throw"); } });
    await flush();
    clickCheckbox(o.$("autoRun"));
    await flush();
    check("a request that throws is 'not supported' too", o.$("autoUnsupported").hidden, false);
  }
  for (const [label, opts] of [["no permissions API", { permissions: false }],
                               ["no registerContentScripts", { scripting: false }]]) {
    const o = options(opts);
    await flush();
    check(`${label}: 'not supported' shown`, o.$("autoUnsupported").hidden, false);
    check(`${label}: the switch hidden`, o.$("autoToggle").hidden, true);
    check(`${label}: the list still shown`, o.$("autoControls").hidden, false);
  }
  {
    // Access revoked in chrome://extensions: the option is still on, the access gone.
    const o = options({ stored: { autoRun: true }, granted: false });
    await flush();
    check("revoked: shown unticked", o.$("autoRun").checked, false);
    check("...with the reason", o.status(),
      "Automatic summaries are paused: TL;DW no longer has access to youtube.com. "
      + "Tick the box to allow it again.");
    o.granted = true;
    o.fireAdded();
    await flush();
    check("access granted while open: ticked straight away", o.$("autoRun").checked, true);
    check("...and the paused line is gone", o.status(), "");
    o.granted = false;
    o.fireRemoved();
    await flush();
    check("access removed while open: unticked straight away", o.$("autoRun").checked, false);
  }
  {
    const o = options({ stored: { autoRun: true }, granted: true });
    await flush();
    check("on and granted: ticked", o.$("autoRun").checked, true);
    check("...no revoked line", o.status(), "");
  }

  // --------------------------------------------------------- the list ----
  const EVIL = "<img src=x onerror=alert(1)>";
  const LIST = [{ key: "@a", name: "Alpha" }, { key: "@b", name: EVIL },
                { key: "channel/UCuVPpxrm2VAgpH3Ktln4HXg", name: "Gamma" }];
  {
    const o = options({ stored: { autoChannels: LIST } });
    await flush();
    check("renders every channel", o.rows().length, 3);
    check("...name and key", o.rows()[0].textContent, "Alpha@a" + "Remove");
    check("...a name with markup is text", o.rows()[1].querySelector(".cname").textContent.startsWith(EVIL), true);
    check("...and creates no element", o.doc.querySelectorAll("img").length, 0);
    check("Remove buttons are labelled per channel",
      o.removeButtons()[1].getAttribute("aria-label"), "Remove " + EVIL);
    check("the empty state is hidden", o.$("autoEmpty").hidden, true);
    const statusNode = o.$("autoStatus");

    const pressed = o.removeButtons()[1];
    pressed.dispatchEvent(D.event("click"));
    check("Remove is off while the worker is asked", pressed.disabled, true);
    await flush();
    check("Remove asks the worker, for that key only",
      JSON.stringify(o.messages), JSON.stringify([{ type: "channels:remove", key: "@b" }]));
    check("...the list is redrawn without it", o.rows().length, 2);
    check("...focus moves to the next Remove",
      o.doc.activeElement && o.doc.activeElement.getAttribute("data-key"),
      "channel/UCuVPpxrm2VAgpH3Ktln4HXg");
    check("...and it's announced", o.status(), "Removed " + EVIL + ".");
    check("the status element is the same node", o.$("autoStatus"), statusNode);

    // Removing the last row moves focus to the row before it.
    o.removeButtons()[1].dispatchEvent(D.event("click"));
    await flush();
    check("removing the last row focuses the one before",
      o.doc.activeElement && o.doc.activeElement.getAttribute("data-key"), "@a");
    o.removeButtons()[0].dispatchEvent(D.event("click"));
    await flush();
    check("removing the only row focuses the heading", o.doc.activeElement, o.$("autoHeading"));
    check("...and shows the empty state", o.$("autoEmpty").hidden, false);
  }
  {
    const o = options({ stored: { autoChannels: LIST } });
    await flush();
    o.reply = () => ({ ok: false });
    o.removeButtons()[0].dispatchEvent(D.event("click"));
    await flush();
    check("a failed remove says so", o.status(), "Couldn't remove Alpha. Try again.");
    check("...and leaves the list", o.rows().length, 3);
    check("...with its Remove usable again", o.removeButtons()[0].disabled, false);
    o.ctx.chrome.runtime.sendMessage = () => Promise.reject(new Error("worker gone"));
    o.removeButtons()[0].dispatchEvent(D.event("click"));
    await flush();
    check("a rejected message is a failed remove", o.status(), "Couldn't remove Alpha. Try again.");
  }
  {
    const o = options({ stored: { autoChannels: [] } });
    await flush();
    check("empty: no rows", o.rows().length, 0);
    check("empty: the empty state shows", o.$("autoEmpty").hidden, false);
    // The worker added one (from the panel): redrawn from the change.
    o.fireChanged({ autoChannels: { newValue: [{ key: "@new", name: "New" }] } });
    check("redrawn on storage.onChanged", o.rows().length, 1);
    check("...and the empty state hides", o.$("autoEmpty").hidden, true);
    o.fireChanged({ autoChannels: { newValue: "corrupt" } });
    check("a corrupt value redraws as empty, not a crash", o.rows().length, 0);
  }
  {
    // Focus survives a redraw that arrives while the keyboard is on a Remove button.
    const o = options({ stored: { autoChannels: LIST } });
    await flush();
    o.removeButtons()[2].focus();
    o.fireChanged({ autoChannels: { newValue: [...LIST, { key: "@d", name: "Delta" }] } });
    check("a redraw keeps focus on the same channel's Remove",
      o.doc.activeElement && o.doc.activeElement.getAttribute("data-key"),
      "channel/UCuVPpxrm2VAgpH3Ktln4HXg");
  }
  {
    // The rest of the page's Save never touches the automatic-summary keys.
    const o = options({ stored: { autoRun: true, autoChannels: LIST }, granted: true });
    await flush();
    o.$("token").value = "tok";
    o.$("save").dispatchEvent(D.event("click"));
    await flush();
    const saved = o.sets.find((s) => "token" in s);
    check("Save saves the form", !!saved, true);
    check("...without autoRun", saved && "autoRun" in saved, false);
    check("...without autoChannels", saved && "autoChannels" in saved, false);
    clickCheckbox(o.$("autoRun"));   // untick
    await flush();
    check("nothing on this page ever writes autoChannels",
      o.sets.some((s) => "autoChannels" in s), false);
  }

  if (failures.length) {
    console.error("FAILURES:\n" + failures.join("\n"));
    process.exit(1);
  }
  console.log("ok " + CHECKS.n + " checks");
})().catch((e) => { console.error("CRASH: " + (e && e.stack || e)); process.exit(1); });
