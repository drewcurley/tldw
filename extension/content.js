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
  let pingTimer = null;
  let creepTimer = null;
  let progressPct = 0;
  let requestActive = false;
  const CREEP_CEILING = 96;  // never park at 99; result snaps it to done

  // Audio (text-to-speech) state.
  let audioPort = null, audioPing = null, audioSafety = null;
  let audioActive = false;
  let lastPayload = null;
  // Curated voices (kept in sync with server audio.VOICES; server validates anyway).
  const VOICE_OPTIONS = [
    { id: "amy", label: "Amy — female (US)" },
    { id: "lessac", label: "Lessac — female (US)" },
    { id: "kristin", label: "Kristin — female (US)" },
    { id: "ljspeech", label: "LJSpeech — female (US)" },
    { id: "ryan", label: "Ryan — male (US)" },
    { id: "joe", label: "Joe — male (US)" },
    { id: "john", label: "John — male (US)" },
    { id: "norman", label: "Norman — male (US)" },
    { id: "cori", label: "Cori — female (UK)" },
    { id: "jenny", label: "Jenny — female (UK)" },
    { id: "alba", label: "Alba — female (UK, Scottish)" },
    { id: "alan", label: "Alan — male (UK)" },
    { id: "northern", label: "Northern — male (UK)" },
  ];

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
        .bar { height: 7px; background: rgba(128,128,128,.2); border-radius: 4px;
          overflow: hidden; margin-top: 18px; }
        .bar .fill { height: 100%; width: 0%; background: #c00; border-radius: 4px;
          transition: width .5s ease; }
        .pct { font-size: 12px; color: #888; margin-top: 6px; text-align: right; }
        .err { color: #c0392b; } .err code { background: rgba(128,128,128,.15);
          padding: 1px 6px; border-radius: 5px; }
        .audiorow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin: 4px 0 14px; }
        .audiorow select { font: inherit; font-size: 13px; padding: 4px 6px;
          border-radius: 7px; border: 1px solid rgba(128,128,128,.4);
          background: transparent; color: inherit; }
        .audiostatus { font-size: 12px; color: #888; }
        .audiostatus.audioerr { color: #c0392b; }
        .audioslot audio { width: 100%; margin-bottom: 12px; }
        .circ { flex: 0 0 auto; display: none; }
        .circ.on { display: inline-block; }
        .circ.indet { animation: circspin 0.9s linear infinite; }
        .circ-bg { fill: none; stroke: rgba(128,128,128,.25); stroke-width: 4; }
        .circ-fg { fill: none; stroke: #c00; stroke-width: 4; stroke-linecap: round;
          transform: rotate(-90deg); transform-origin: 50% 50%;
          stroke-dasharray: 97.4; stroke-dashoffset: 97.4;
          transition: stroke-dashoffset .4s ease; }
        .circ.indet .circ-fg { stroke-dasharray: 24 74; stroke-dashoffset: 0; transition: none; }
        @keyframes circspin { to { transform: rotate(360deg); } }
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
      const f = root.querySelectorAll(
        "button:not([hidden]):not([disabled]), select:not([disabled]), audio");
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && root.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && root.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function close() {
    clearTimers();
    teardownAudio();                  // tear down any in-flight TTS request + ping
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
      if (m.type === "progress") { updateProgress(m.message, m.percent, m.creep); return; }
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
    // Heartbeat: the page never suspends, so pinging every 20s keeps the MV3 service
    // worker alive through a long (60s+) summarize that would otherwise be killed.
    pingTimer = setInterval(() => {
      try { port.postMessage({ type: "ping" }); } catch (_) {}
    }, 20000);
    safetyTimer = setTimeout(() => {
      if (!requestActive) return;
      requestActive = false;
      showError("This is taking too long. Make sure `tldw serve` is running, then try again.");
    }, 160000);
  }

  function showLoading() {
    const c = mount();
    progressPct = 0;
    c.innerHTML = `
      <div class="status">
        <div><span class="spinner"></span><span class="msg">Starting…</span></div>
        <div class="bar"><div class="fill"></div></div>
        <div class="pct">0%</div>
      </div>`;
  }

  function updateProgress(msg, pct, creep) {
    const el = root && root.querySelector(".status .msg");
    if (el) el.textContent = msg;
    if (typeof pct === "number") setProgress(pct, !!creep);
  }

  function setProgress(target, creep) {
    progressPct = Math.max(progressPct, target);
    applyWidth();
    // Only the long step (Claude) eases forward; quick early steps just jump.
    if (creep) startCreep();
  }

  function applyWidth() {
    if (!root) return;
    const fill = root.querySelector(".bar .fill");
    const pct = root.querySelector(".pct");
    if (fill) fill.style.width = progressPct.toFixed(1) + "%";
    if (pct) pct.textContent = Math.round(progressPct) + "%";
  }

  function startCreep() {
    if (creepTimer) return;
    // Linear ~1%/s (no front-loading -> no false optimism); only the last sliver
    // eases. If Claude finishes early the result snaps ahead (pleasant surprise).
    creepTimer = setInterval(() => {
      if (progressPct < 88) progressPct += 0.6;
      else if (progressPct < CREEP_CEILING) progressPct += (CREEP_CEILING - progressPct) * 0.05;
      applyWidth();
    }, 600);
  }

  function clearTimers() {
    if (stageTimer) { clearTimeout(stageTimer); stageTimer = null; }
    if (safetyTimer) { clearTimeout(safetyTimer); safetyTimer = null; }
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
    if (creepTimer) { clearInterval(creepTimer); creepTimer = null; }
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
    lastPayload = p;
    root.querySelector("#tldw-h").textContent = "TL;DW" + (cached ? " (cached)" : "");
    const c = root.querySelector(".content");
    const points = (p.key_points || []).map((k) => `<li>${esc(k)}</li>`).join("");
    c.innerHTML = `
      <div class="meta">${esc(p.channel)} · ${esc(p.original_length)} → ~${esc(p.length_label)} read ·
        <a href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">original</a></div>
      <h1 style="margin-bottom:10px">${esc(p.title)}</h1>
      <div class="audiorow">
        <button class="listen" aria-label="Generate spoken audio of this summary">🔊 Listen to summary</button>
        <select class="voice" aria-label="Voice"></select>
        <svg class="circ" viewBox="0 0 36 36" width="20" height="20" aria-hidden="true">
          <circle class="circ-bg" cx="18" cy="18" r="15.5"></circle>
          <circle class="circ-fg" cx="18" cy="18" r="15.5"></circle>
        </svg>
        <span class="audiostatus" aria-live="polite"></span>
      </div>
      <div class="audioslot"></div>
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
    setupVoiceSelect();
    root.querySelector(".listen").onclick = requestAudio;
  }

  function setupVoiceSelect() {
    const sel = root.querySelector(".voice");
    sel.innerHTML = VOICE_OPTIONS.map(
      (v) => `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("");
    try {
      chrome.storage.local.get({ voice: "amy" }, (s) => {
        if (sel && [...sel.options].some((o) => o.value === s.voice)) sel.value = s.voice;
      });
    } catch (_) {}
  }

  function requestAudio() {
    if (audioActive || !lastPayload) return;            // re-entrancy guard
    audioActive = true;
    const btn = root.querySelector(".listen");
    const sel = root.querySelector(".voice");
    const status = root.querySelector(".audiostatus");
    status.classList.remove("audioerr");
    btn.disabled = true; if (sel) sel.disabled = true;
    status.textContent = "Starting…";
    setCircle(null);                                    // indeterminate until first %
    audioPort = chrome.runtime.connect({ name: "tldw" });
    audioPing = setInterval(() => { try { audioPort.postMessage({ type: "ping" }); } catch (_) {} }, 20000);
    audioPort.onMessage.addListener((m) => {
      if (!audioActive) return;
      if (m.type === "speakProgress") { updateAudioStatus(m.message, m.percent); return; }
      if (m.type === "audio") { teardownAudio(); finishAudioUI(); renderAudio(m.dataUrl); }
      else if (m.type === "speakError") { teardownAudio(); finishAudioUI(); showAudioError(m.error); }
    });
    audioPort.onDisconnect.addListener(() => {
      if (!audioActive) return;
      teardownAudio(); finishAudioUI();
      showAudioError("Lost connection to the worker. Try again.");
    });
    audioSafety = setTimeout(() => {
      if (!audioActive) return;
      teardownAudio(); finishAudioUI();
      showAudioError("Audio is taking too long. Make sure `tldw serve` is running.");
    }, 185000);
    audioPort.postMessage({
      type: "speak", videoId: lastPayload.video_id,
      voice: sel ? sel.value : "amy", payload: lastPayload,
    });
  }

  function teardownAudio() {
    audioActive = false;
    if (audioPing) { clearInterval(audioPing); audioPing = null; }
    if (audioSafety) { clearTimeout(audioSafety); audioSafety = null; }
    if (audioPort) { try { audioPort.disconnect(); } catch (_) {} audioPort = null; }
  }

  function updateAudioStatus(msg, percent) {
    const status = root && root.querySelector(".audiostatus");
    if (status) { status.classList.remove("audioerr"); status.textContent = msg; }
    setCircle(typeof percent === "number" ? percent : null);
  }

  function finishAudioUI() {
    const btn = root && root.querySelector(".listen");
    const sel = root && root.querySelector(".voice");
    const status = root && root.querySelector(".audiostatus");
    if (btn) btn.disabled = false;
    if (sel) sel.disabled = false;
    if (status) status.textContent = "";
    hideCircle();
  }

  function setCircle(percent) {
    const svg = root && root.querySelector(".circ");
    const fg = root && root.querySelector(".circ-fg");
    if (!svg || !fg) return;
    svg.classList.add("on");                            // shown only during a request
    if (typeof percent === "number") {
      svg.classList.remove("indet");
      const C = 97.4;  // 2π·15.5
      const p = Math.max(0, Math.min(100, percent));
      fg.style.strokeDashoffset = (C * (1 - p / 100)).toFixed(1);
    } else {
      svg.classList.add("indet");  // spin while we have no real percentage
    }
  }

  function hideCircle() {
    const svg = root && root.querySelector(".circ");
    if (svg) svg.classList.remove("on", "indet");       // hidden until next request
  }

  function renderAudio(dataUrl) {
    const slot = root && root.querySelector(".audioslot");
    if (!slot) return;
    slot.innerHTML = "";                                 // replace, never stack
    const a = document.createElement("audio");
    a.controls = true; a.src = dataUrl;
    a.setAttribute("aria-label", "Spoken summary");
    slot.appendChild(a);
    const btn = root.querySelector(".listen");
    if (btn) btn.textContent = "🔊 Regenerate";
    a.focus();
  }

  function showAudioError(msg) {
    const status = root && root.querySelector(".audiostatus");
    if (status) { status.textContent = msg; status.classList.add("audioerr"); }
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
