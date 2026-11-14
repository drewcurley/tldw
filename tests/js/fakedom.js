// A small DOM for the extension's node checks. Just enough to run the real code:
// an element tree with attributes, text, the few reflected properties the
// extension uses, focus, selectors, and events that go capture -> target -> bubble
// (through a shadow root to its host) with isTrusted and stopImmediatePropagation.
//
// It deliberately has NO HTML parser. Setting innerHTML throws, unless a node was
// built with allowInnerHTML -- so any code path that writes markup where it should
// be creating elements fails loudly instead of quietly producing an empty node.

function matchPart(n, part) {
  if (!n || !n.tag) return false;
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
    let node = n.parentNode, i = parts.length - 2;
    while (i >= 0) {
      while (node && !matchPart(node, parts[i])) node = node.parentNode;
      if (!node) return false;
      node = node.parentNode;
      i--;
    }
    return true;
  });
}

class FakeEvent {
  constructor(type, { trusted = true, bubbles = true, detail } = {}) {
    this.type = type;
    this.isTrusted = trusted;
    this.bubbles = bubbles;
    this.detail = detail;
    this.defaultPrevented = false;
    this._stop = false;
    this._stopNow = false;
    this.target = null;
    this.currentTarget = null;
    this._path = [];
  }
  stopPropagation() { this._stop = true; }
  stopImmediatePropagation() { this._stop = true; this._stopNow = true; }
  preventDefault() { this.defaultPrevented = true; }
  composedPath() { return this._path.slice(); }
}

class Node {
  constructor(doc) {
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
    this._listeners = [];
  }
  get firstChild() { return this.childNodes[0] || null; }
  get children() { return this.childNodes.filter((c) => c instanceof Element); }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  append(...kids) { for (const k of kids) this.appendChild(typeof k === "string" ? this.ownerDocument.createTextNode(k) : k); }
  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    if (child.parentNode) child.parentNode.removeChild(child);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) throw new Error("insertBefore: reference is not a child");
    child.parentNode = this;
    this.childNodes.splice(i, 0, child);
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i < 0) throw new Error("removeChild: not a child");
    this.childNodes.splice(i, 1);
    child.parentNode = null;
    // As in a browser: taking the focused element out of the tree drops focus --
    // whether it was focused in the document or inside a shadow root.
    const doc = this.ownerDocument;
    if (doc && doc.activeElement && child.contains(doc.activeElement)) {
      if (doc.activeElement.shadowRoot) doc.activeElement.shadowRoot.activeElement = null;
      doc.activeElement = null;
    }
    const sr = rootOf(this);
    if (sr instanceof ShadowRoot && sr.activeElement && child.contains(sr.activeElement)) {
      sr.activeElement = null;
      if (doc && doc.activeElement === sr.host) doc.activeElement = null;
    }
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) {
    for (let x = n; x; x = x.parentNode) if (x === this) return true;
    return false;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) {
    for (const c of this.childNodes.slice()) this.removeChild(c);
    if (v !== "" && v !== null && v !== undefined) this.appendChild(this.ownerDocument.createTextNode(String(v)));
  }
  all() {
    const out = [];
    for (const c of this.childNodes) {
      if (c instanceof Element) { out.push(c); out.push(...c.all()); }
    }
    return out;
  }
  querySelectorAll(sel) { return this.all().filter((n) => matches(n, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementById(id) { return this.all().find((n) => n.id === id) || null; }
  addEventListener(type, fn, opts) {
    const capture = opts === true || !!(opts && opts.capture);
    this._listeners.push({ type, fn, capture });
  }
  removeEventListener(type, fn, opts) {
    const capture = opts === true || !!(opts && opts.capture);
    this._listeners = this._listeners.filter((l) => !(l.type === type && l.fn === fn && l.capture === capture));
  }
  listenerCount(type) { return this._listeners.filter((l) => l.type === type).length; }
  // The path an event takes: up the tree, out of a shadow root to its host.
  _eventParent() { return this.parentNode || (this instanceof ShadowRoot ? this.host : null); }
  dispatchEvent(ev) {
    ev.target = this;
    const path = [];
    for (let n = this; n; n = n._eventParent()) path.push(n);
    if (this.ownerDocument && this.ownerDocument.defaultView && path[path.length - 1] === this.ownerDocument) {
      path.push(this.ownerDocument.defaultView);
    }
    ev._path = path;
    const run = (node, phase) => {
      for (const l of node._listeners.slice()) {
        if (ev._stopNow) return;
        if (l.type !== ev.type) continue;
        if (phase === "capture" && !l.capture) continue;
        if (phase === "bubble" && l.capture) continue;
        ev.currentTarget = node;
        l.fn.call(node, ev);
      }
      if (phase !== "capture" && !ev._stopNow && typeof node["on" + ev.type] === "function") {
        ev.currentTarget = node;
        node["on" + ev.type].call(node, ev);
      }
    };
    for (let i = path.length - 1; i > 0 && !ev._stop; i--) run(path[i], "capture");
    if (!ev._stop) run(path[0], "target");
    if (ev.bubbles) for (let i = 1; i < path.length && !ev._stop; i++) run(path[i], "bubble");
    return !ev.defaultPrevented;
  }
}

class Text extends Node {
  constructor(doc, text) { super(doc); this.data = String(text); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

const REFLECTED_BOOL = ["hidden", "disabled"];

class Element extends Node {
  constructor(doc, tag, { allowInnerHTML = false } = {}) {
    super(doc);
    this.tag = String(tag).toLowerCase();
    this.attrs = {};
    this.allowInnerHTML = allowInnerHTML;
    this.innerHTMLWrites = [];
    this.style = { cssText: "" };
    this.checked = false;
    this.value = "";
    this.shadowRoot = null;
  }
  get tagName() { return this.tag.toUpperCase(); }
  get id() { return this.attrs.id || ""; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); }
  get type() { return this.attrs.type || ""; }
  set type(v) { this.attrs.type = String(v); }
  get title() { return this.attrs.title || ""; }
  set title(v) { this.attrs.title = String(v); }
  getAttribute(n) {
    return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null;
  }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  removeAttribute(n) { delete this.attrs[n]; }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); }
  set innerHTML(html) {
    if (!this.allowInnerHTML) {
      throw new Error("fakedom: innerHTML written on <" + this.tag + "> -- build it with createElement");
    }
    this.innerHTMLWrites.push(String(html));
    for (const c of this.childNodes.slice()) this.removeChild(c);
  }
  get innerHTML() { return this.innerHTMLWrites[this.innerHTMLWrites.length - 1] || ""; }
  // As in a browser, focus() does nothing on an element that is detached, disabled
  // or hidden (here: the `hidden` attribute on it or on an ancestor, across shadow
  // boundaries). Inside a shadow root the document sees the host as focused
  // (retargeting) and the shadow root knows the element.
  focus() {
    const doc = this.ownerDocument;
    if (!doc || !isConnected(this) || this.hasAttribute("disabled")) return;
    // Only what a browser can focus: form controls, links with an href, or anything
    // given a tabindex -- a plain <h3> without one is not.
    const focusable = ["button", "input", "select", "textarea"].includes(this.tag)
      || (this.tag === "a" && this.hasAttribute("href")) || this.hasAttribute("tabindex");
    if (!focusable) return;
    for (let n = this; n; n = n.parentNode instanceof ShadowRoot ? n.parentNode.host : n.parentNode) {
      if (n.hasAttribute && n.hasAttribute("hidden")) return;
    }
    const sr = rootOf(this);
    if (sr instanceof ShadowRoot) {
      sr.activeElement = this;
      doc.activeElement = sr.host;
    } else {
      doc.activeElement = this;
    }
  }
  blur() {
    const doc = this.ownerDocument;
    const sr = rootOf(this);
    if (sr instanceof ShadowRoot && sr.activeElement === this) {
      sr.activeElement = null;
      if (doc && doc.activeElement === sr.host) doc.activeElement = null;
    } else if (doc && doc.activeElement === this) doc.activeElement = null;
  }
  // A programmatic click: never trusted, exactly as in a browser.
  click() { this.dispatchEvent(new FakeEvent("click", { trusted: false })); }
  attachShadow() {
    this.shadowRoot = new ShadowRoot(this.ownerDocument, this);
    return this.shadowRoot;
  }
}
for (const name of REFLECTED_BOOL) {
  Object.defineProperty(Element.prototype, name, {
    get() { return this.hasAttribute(name); },
    set(v) { if (v) this.setAttribute(name, ""); else this.removeAttribute(name); },
  });
}

class ShadowRoot extends Node {
  constructor(doc, host) {
    super(doc);
    this.host = host;
    this.mode = "open";
    this.activeElement = null;
    if (host) host.shadowRoot = this;
  }
}

// The topmost node above n without crossing a shadow boundary.
function rootOf(n) {
  while (n.parentNode) n = n.parentNode;
  return n;
}

// In the document, following shadow roots out through their hosts.
function isConnected(n) {
  for (;;) {
    const r = rootOf(n);
    if (r instanceof Document) return true;
    if (r instanceof ShadowRoot && r.host) { n = r.host; continue; }
    return false;
  }
}

class Document extends Node {
  constructor() {
    super(null);
    this.ownerDocument = this;
    this.tag = "#document";
    this.activeElement = null;
    this.fullscreenElement = null;
    this.visibilityState = "visible";
    this.defaultView = null;
    this.documentElement = this.createElement("html");
    this.appendChild(this.documentElement);
    this.head = this.createElement("head");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
  }
  createElement(tag, opts) { return new Element(this, tag, opts); }
  createTextNode(t) { return new Text(this, t); }
  getAttribute() { return null; }
}

// el("div", { class: "x", id: "y", "video-id": "…" }, [children or text])
function el(doc, tag, attrs = {}, kids = []) {
  const e = doc.createElement(tag, { allowInnerHTML: !!attrs.__allowInnerHTML });
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "__allowInnerHTML") continue;
    if (v === null || v === undefined) continue;
    e.setAttribute(k, v);
  }
  for (const k of [].concat(kids)) {
    if (k === null || k === undefined) continue;
    e.appendChild(typeof k === "string" ? doc.createTextNode(k) : k);
  }
  return e;
}

function event(type, opts) { return new FakeEvent(type, opts); }

module.exports = { Document, Element, ShadowRoot, Text, FakeEvent, event, el, matches };
