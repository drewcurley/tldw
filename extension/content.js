// On-page modal for the TL;DW summary. Injected on toolbar click; talks to the
// background worker via runtime messages. Renders inside a shadow root so YouTube's
// styles can't leak in (and ours can't leak out). All model text is escaped.

(() => {
  if (window.__tldwInit) return;
  window.__tldwInit = true;

  let host = null;
  let root = null;
  let lastFocused = null;
  let stageTimer = null;
  let port = null;
  let safetyTimer = null;
  let requestActive = false;

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Safe minimal markdown: escape first, then **bold** and blank-line paragraphs.
  function renderSummary(md) {
    return esc(md)
      .split(/\n{2,}/)
      .map((p) => "<p>" + p.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\n/g, "<br>") + "</p>")
      .join("");
  }

  function mount() {
    close();
    lastFocused = document.activeElement;
    host = document.createElement("div");
    host.id = "tldw-host";
    root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .backdrop { position: fixed; inset: 0; z-index: 2147483647;
          background: rgba(0,0,0,.55); display: flex; align-items: center;
          justify-content: center; font-family: system-ui, -apple-system, sans-serif; }
        .panel { background: #fff; color: #111; width: min(680px, 92vw);
          max-height: 86vh; overflow: auto; border-radius: 14px; padding: 22px 26px;
          box-shadow: 0 20px 60px rgba(0,0,0,.4); line-height: 1.5; }
        @media (prefers-color-scheme: dark) {
          .panel { background: #1e1f24; color: #e9e9ea; }
          .meta, .rationale { color: #a8a8ad; }
          .points li::marker { color: #8ab4f8; }
          a { color: #8ab4f8; }
        }
        h1 { font-size: 19px; margin: 0 6px 2px 0; }
        .meta { font-size: 13px; color: #666; margin-bottom: 14px; }
        h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .04em;
          opacity: .8; margin: 18px 0 8px; }
        .points { margin: 0; padding-left: 20px; }
        .points li { margin: 5px 0; }
        .body p { margin: 0 0 12px; }
        .rationale { font-size: 12px; font-style: italic; color: #777;
          border-top: 1px solid rgba(128,128,128,.25); padding-top: 10px; margin-top: 14px; }
        .row { display: flex; align-items: center; justify-content: space-between;
          gap: 12px; margin-bottom: 6px; }
        .btns { display: flex; gap: 8px; }
        button { font: inherit; font-size: 13px; cursor: pointer; border-radius: 8px;
          border: 1px solid rgba(128,128,128,.4); background: transparent;
          color: inherit; padding: 5px 12px; }
        button.primary { background: #c00; color: #fff; border-color: #c00; }
        .spinner { width: 18px; height: 18px; border: 2px solid rgba(128,128,128,.3);
          border-top-color: #c00; border-radius: 50%; animation: spin 1s linear infinite;
          display: inline-block; vertical-align: middle; margin-right: 8px; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .status { font-size: 15px; padding: 26px 4px; }
        .err { color: #c0392b; } .err code { background: rgba(128,128,128,.15);
          padding: 1px 6px; border-radius: 5px; }
      </style>
      <div class="backdrop" part="backdrop">
        <div class="panel" role="dialog" aria-modal="true" aria-labelledby="tldw-h" tabindex="-1">
          <div class="row">
            <h1 id="tldw-h">TL;DW</h1>
            <div class="btns">
              <button class="copy" hidden>Copy</button>
              <button class="close" aria-label="Close">✕</button>
            </div>
          </div>
          <div class="content"></div>
        </div>
      </div>`;
    (document.fullscreenElement || document.body).appendChild(host);

    root.querySelector(".close").addEventListener("click", close);
    root.querySelector(".backdrop").addEventListener("mousedown", (e) => {
      if (e.target === e.currentTarget) close();
    });
    root.querySelector(".panel").focus();
    document.addEventListener("keydown", onKey, true);
    return root.querySelector(".content");
  }

  function onKey(e) {
    if (!host) return;
    if (e.key === "Escape") { e.stopPropagation(); close(); return; }
    if (e.key === "Tab") {
      const f = root.querySelectorAll("button:not([hidden])");
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && root.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && root.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function close() {
    if (stageTimer) { clearTimeout(stageTimer); stageTimer = null; }
    if (safetyTimer) { clearTimeout(safetyTimer); safetyTimer = null; }
    requestActive = false;            // suppress late port errors after a manual close
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    document.removeEventListener("keydown", onKey, true);
    if (host && host.parentNode) host.parentNode.removeChild(host);
    host = root = null;
    if (lastFocused && lastFocused.focus) { try { lastFocused.focus(); } catch (_) {} }
  }

  function startSummarize(url, videoId) {
    showLoading();
    requestActive = true;
    port = chrome.runtime.connect({ name: "tldw" });
    port.onMessage.addListener((m) => {
      if (!requestActive) return;
      requestActive = false;
      if (m.type === "result") showResult(m.payload, m.cached);
      else if (m.type === "error") showError(m.error);
    });
    port.onDisconnect.addListener(() => {
      if (!requestActive) return;
      requestActive = false;
      showError("Lost connection to the extension worker. Click TL;DW to try again.");
    });
    port.postMessage({ type: "summarize", url, videoId });
    safetyTimer = setTimeout(() => {
      if (!requestActive) return;
      requestActive = false;
      showError("This is taking too long. Make sure `tldw serve` is running, then try again.");
    }, 160000);
  }

  function showLoading() {
    const c = mount();
    c.innerHTML = `<div class="status"><span class="spinner"></span><span class="msg">Fetching transcript…</span></div>`;
    stageTimer = setTimeout(() => {
      const m = root && root.querySelector(".status .msg");
      if (m) m.textContent = "Summarizing with Claude (this usually takes ~20s)…";
    }, 3000);
  }

  function clearTimers() {
    if (stageTimer) { clearTimeout(stageTimer); stageTimer = null; }
    if (safetyTimer) { clearTimeout(safetyTimer); safetyTimer = null; }
  }

  function showError(msg) {
    if (!host) mount();
    clearTimers();
    const c = root.querySelector(".content");
    c.innerHTML = `<div class="status err">${esc(msg).replace(/`([^`]+)`/g, "<code>$1</code>")}</div>`;
  }

  function showResult(p, cached) {
    if (!host) mount();
    clearTimers();
    root.querySelector("#tldw-h").textContent = "TL;DW" + (cached ? " (cached)" : "");
    const c = root.querySelector(".content");
    const points = (p.key_points || []).map((k) => `<li>${esc(k)}</li>`).join("");
    c.innerHTML = `
      <div class="meta">${esc(p.channel)} · ${esc(p.original_length)} → ~${esc(p.length_label)} read ·
        <a href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">original</a></div>
      <h1 style="margin-bottom:10px">${esc(p.title)}</h1>
      ${points ? `<h2>Key points</h2><ul class="points">${points}</ul>` : ""}
      <h2>Summary</h2><div class="body">${renderSummary(p.summary_md || "")}</div>
      ${p.rationale ? `<div class="rationale">${esc(p.rationale)}</div>` : ""}`;
    const copy = root.querySelector(".copy");
    copy.hidden = false;
    copy.onclick = () => {
      navigator.clipboard.writeText(toMarkdown(p)).then(() => {
        copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1500);
      });
    };
  }

  function toMarkdown(p) {
    const kp = (p.key_points || []).map((k) => "- " + k).join("\n");
    return `# ${p.title}\n${p.channel} · ${p.original_length} → ~${p.length_label} read\n` +
      `Source: ${p.source_url}\n\n## Key points\n${kp}\n\n## Summary\n${p.summary_md}\n`;
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "TLDW_INVOKE") startSummarize(msg.url, msg.videoId);
    else if (msg.type === "TLDW_ERROR") showError(msg.error);
  });
})();
