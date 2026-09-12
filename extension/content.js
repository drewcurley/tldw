// On-page modal for the TL;DW summary. Injected on toolbar click; talks to the
// background worker via runtime messages. Renders inside a shadow root so YouTube's
// styles can't leak in (and ours can't leak out). All model text is escaped.

(() => {
  if (window.__tldwInit) return;
  window.__tldwInit = true;

  const api = globalThis.browser ?? globalThis.chrome;  // Firefox/Chrome shim

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

  // Audio (text-to-speech) + segment-skip state. `busy` guards either operation.
  let audioPort = null, audioPing = null, audioSafety = null;
  let segPort = null, segPing = null, segSafety = null;
  let busy = false;
  let lastPayload = null;
  let lastAudio = null;       // { videoId, dataUrl } — restore the player on re-open
  // Streaming TTS: mp3 blocks are appended to a MediaSource so playback starts on the
  // first block instead of waiting for the whole script. Firefox's MSE has no
  // audio/mpeg, so `stream` stays null there and the buffered dataUrl path is used.
  let stream = null;          // { ms, buf, url, audio, pending, ended }
  // Follow-up Q&A. History is [{role, content}] and lives for the page session, so
  // re-opening the panel (or jumping to a cited moment) doesn't lose the thread.
  let chat = null;            // { videoId, history } once a conversation has started
  let askPort = null, askPing = null, askSafety = null, asking = false;
  // What closing the panel should do to work that's still running. Read once and
  // kept in sync, because close() has to decide synchronously.
  let closeAction = "continue";          // "continue" | "abort"
  let backgrounded = false;              // panel closed while a summary was running
  let currentVideoId = null;
  let lastProgress = null;               // last progress event of the running summary
  api.storage.local.get({ closeAction: "continue" })
    .then((s) => { closeAction = s.closeAction === "abort" ? "abort" : "continue"; })
    .catch(() => {});
  if (api.storage.onChanged) {
    api.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.closeAction) {
        closeAction = changes.closeAction.newValue === "abort" ? "abort" : "continue";
      }
    });
  }

  // The one decision close() makes, kept pure so it can be tested directly.
  function closePolicy(opts, pref) {
    return {
      keepSeg: !!(opts && opts.keepSeg === true),
      background: pref !== "abort",
    };
  }
  let askLive = null;         // { text, bubble } while an answer is streaming
  let lastSegments = null;    // { videoId, segments } — re-skip without re-fetching
  // Skip-playback engine state.
  let skipVideo = null, skipSegs = null, skipIdx = 0, skipHandler = null;
  let segsComplete = false;  // true once all segments are known (segments or segmentsDone)
  let skipPaused = false;    // true when paused at end of known clips, waiting for more
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

  // Safe minimal markdown: escape first, then **bold**, *italic*, "- " bullet lists
  // and blank-line paragraphs. Answers lean on emphasis and bullets, so both render.
  const inlineMd = (t) => t
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")          // bold first
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");         // then italic

  function renderSummary(md) {
    return esc(md)
      .split(/\n{2,}/)
      .filter((b) => b.trim())
      .map((block) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-*]\s+/.test(l))) {
          return "<ul>" + lines
            .map((l) => "<li>" + inlineMd(l.replace(/^\s*[-*]\s+/, "")) + "</li>")
            .join("") + "</ul>";
        }
        return "<p>" + inlineMd(block).replace(/\n/g, "<br>") + "</p>";
      })
      .join("");
  }

  function mount() {
    // unmount(), NOT close(): close() decides what to do with in-flight work, and
    // re-mounting is not a decision about that. It used to call close(), so
    // re-opening the panel mid-run immediately re-flagged the run as backgrounded
    // and the result was stashed instead of rendered into the modal you just opened.
    unmount();
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
          margin: 4px 0 6px; }
        .audiorow select { font: inherit; font-size: 13px; padding: 4px 6px;
          border-radius: 7px; border: 1px solid rgba(128,128,128,.4);
          background: transparent; color: inherit; }
        .audioprogress { display: flex; align-items: center; gap: 8px; margin: 0 0 10px;
          min-height: 14px; }
        .audiostatus { font-size: 12px; color: #888; }
        .audiostatus.audioerr { color: #c0392b; }
        .audioslot audio { width: 100%; margin-bottom: 12px; }
        .stopping { border-color: #c00; color: #c00; }
        .askwrap { margin-top: 20px; border-top: 1px solid rgba(128,128,128,.25);
          padding-top: 14px; }
        .asktoggle { font-size: 13px; }
        .asklog { display: flex; flex-direction: column; gap: 10px; margin: 12px 0; }
        .asklog:empty { margin: 0; }
        .msg { max-width: 92%; padding: 8px 12px; border-radius: 12px; font-size: 14px; }
        .msg p { margin: 0 0 8px; } .msg p:last-child { margin: 0; }
        .msg ul, .body ul { margin: 6px 0; padding-left: 20px; }
        .msg li, .body li { margin: 2px 0; }
        .msg.you { align-self: flex-end; background: rgba(128,128,128,.16);
          border-bottom-right-radius: 4px; }
        .msg.bot { align-self: flex-start; background: rgba(128,128,128,.08);
          border-bottom-left-radius: 4px; }
        .msg.bot.pending::after { content: "▋"; opacity: .5; }
        .ts { color: #c00; cursor: pointer; font-variant-numeric: tabular-nums;
          text-decoration: underline dotted; text-underline-offset: 2px; }
        @media (prefers-color-scheme: dark) { .ts { color: #ff7b72; } }
        .askrow { display: flex; gap: 8px; align-items: flex-end; }
        .askinput { flex: 1; min-width: 0; font: inherit; font-size: 14px;
          padding: 8px 10px; border-radius: 9px; resize: none; max-height: 140px;
          border: 1px solid rgba(128,128,128,.4); background: transparent;
          color: inherit; }
        .askstatus { font-size: 12px; color: #888; min-height: 14px; margin-top: 6px; }
        .askstatus.askerr { color: #c0392b; }
        .vpreview { padding: 4px 9px; font-size: 13px; line-height: 1.2; }
        .vpreview[disabled] { opacity: .55; cursor: default; }
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
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", shieldKey, true);
    window.addEventListener("keypress", shieldKey, true);
    return root.querySelector(".content");
  }

  // Is this key event coming from inside our modal? Events crossing a shadow
  // boundary are retargeted, so the page sees our host <div> as the target — which
  // is precisely why YouTube's "is the user typing?" check doesn't recognize our
  // textarea and applies its shortcuts to it.
  function fromModal(e) {
    const path = e.composedPath ? e.composedPath() : [];
    return path.length ? path.indexOf(host) !== -1 : !!(host && host.contains(e.target));
  }

  // Swallow every key typed inside the modal before the page can act on it.
  //
  // This has to run on `window` in the capture phase. YouTube binds space and the
  // arrows as capture-phase listeners on `document` (they're scroll keys, so it
  // grabs them early), which run BEFORE the event reaches our input — a
  // stopPropagation() from the input's own handler is too late, so every space you
  // type pauses the video. Letter shortcuts like `m` bubble, so those were already
  // being stopped; the two together are why the behaviour looked inconsistent.
  // Capture order is window -> document, so this is the one place ahead of both.
  function shieldKey(e) {
    if (host && fromModal(e)) e.stopPropagation();
  }

  function onKey(e) {
    if (!host) return;
    const mine = fromModal(e);
    if (mine) e.stopPropagation();
    if (e.key === "Escape") { e.stopPropagation(); close(); return; }

    // The chat box's own keydown handler can't run any more (this shield stops the
    // event before it gets there), so Enter-to-send lives here.
    const target = (e.composedPath && e.composedPath()[0]) || e.target;
    const typing = mine && target &&
      (target.tagName === "TEXTAREA" || target.tagName === "INPUT");
    if (typing) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitQuestion(); }
      return;                          // never trap Tab away from a text box
    }
    if (e.key === "Tab") {
      const f = root.querySelectorAll(
        "button:not([hidden]):not([disabled]), select:not([disabled]), " +
        "textarea:not([disabled]), audio");
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && root.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && root.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function close(opts) {
    const policy = closePolicy(opts, closeAction);
    if (stageTimer) { clearTimeout(stageTimer); stageTimer = null; }
    // creepTimer is deliberately NOT cleared on the background path below: it only
    // advances progressPct and calls applyWidth(), which is a no-op while unmounted,
    // so the bar goes on tracking real elapsed time instead of freezing at the
    // moment you closed the panel. clearTimers() still retires it on completion.
    if (!policy.background && creepTimer) { clearInterval(creepTimer); creepTimer = null; }

    if (policy.background) {
      // Leave every in-flight port — and its keepalive ping — connected. That ping
      // is the ONLY thing keeping the MV3 worker alive; drop it and Chrome suspends
      // the worker mid-fetch, which is exactly what used to kill a summary when you
      // clicked away. The result lands in the worker's cache either way.
      if (requestActive) backgrounded = true;
      resetStream();                  // drop the MediaSource; the finished clip is
                                      // still cached for an instant re-Listen
      unmount();
      return;
    }

    // Abort: stop the work, explicitly where a bare disconnect wouldn't (speak and
    // ask both keep going through a plain disconnect by design).
    if (busy && audioPort) { try { audioPort.postMessage({ type: "stopSpeak" }); } catch (_) {} }
    if (asking && askPort) { try { askPort.postMessage({ type: "stopAsk" }); } catch (_) {} }
    clearTimers();
    teardownAudio();
    teardownAsk();
    // ...and any in-flight segment fetch (NOT the skip engine, which runs after the
    // modal closes). keepSeg is the exception: streaming skip playback closes the
    // modal on the FIRST clip and still needs the port — and its keepalive ping —
    // for the clips Claude hasn't found yet.
    if (!policy.keepSeg) teardownSeg();
    backgrounded = false;
    requestActive = false;            // suppress late port errors after a manual close
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    unmount();
  }

  function unmount() {
    window.removeEventListener("keydown", onKey, true);
    window.removeEventListener("keyup", shieldKey, true);
    window.removeEventListener("keypress", shieldKey, true);
    if (host && host.parentNode) host.parentNode.removeChild(host);
    host = root = null;
    if (askLive) askLive.bubble = null;   // don't render deltas into a detached node
    if (lastFocused && lastFocused.focus) { try { lastFocused.focus(); } catch (_) {} }
  }

  function startSummarize(url, videoId) {
    // A new run abandons whatever the last one left connected (mount() no longer
    // does this, and it must not — see the comment there).
    clearTimers();
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    lastProgress = null;
    showLoading();
    requestActive = true;
    currentVideoId = videoId;
    backgrounded = false;
    port = api.runtime.connect({ name: "tldw" });
    port.onMessage.addListener((m) => {
      if (!requestActive) return;
      if (m.type === "progress") {
        lastProgress = m;          // replayed if the panel is re-opened mid-run
        updateProgress(m.message, m.percent, m.creep);
        return;
      }
      requestActive = false;
      if (m.type === "result") {
        if (backgrounded) { lastPayload = m.payload; endBackground("TL;DW summary ready"); }
        else showResult(m.payload, m.cached);
      } else if (m.type === "error") {
        if (backgrounded) endBackground(m.error, true);
        else showError(m.error);
      }
    });
    port.onDisconnect.addListener(() => {
      if (!requestActive) return;
      requestActive = false;
      const msg = "Lost connection to the extension worker. Click TL;DW to try again.";
      if (backgrounded) endBackground(msg, true);
      else showError(msg);
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
      const msg = "This is taking too long. Make sure `tldw serve` is running, then try again.";
      if (backgrounded) endBackground(msg, true);
      else showError(msg);
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

  // A summary that finished while the panel was closed. Don't yank the modal back
  // open over whatever they're watching — stash it, say so, and let them choose.
  function endBackground(message, isError) {
    clearTimers();
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    backgrounded = false;
    requestActive = false;
    toast(message, isError);
  }

  function toast(message, isError) {
    const old = document.getElementById("tldw-toast");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    const el = document.createElement("div");
    el.id = "tldw-toast";
    el.setAttribute("role", "status");
    el.style.cssText =
      "position:fixed;z-index:2147483647;bottom:84px;right:24px;max-width:320px;" +
      "background:" + (isError ? "#4a1f1f" : "#1e1f24") + ";color:#e9e9ea;" +
      "font:14px system-ui,-apple-system,sans-serif;padding:10px 14px;border-radius:10px;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.45);cursor:pointer;line-height:1.4;";
    el.textContent = message + (isError ? "" : " — click to read");
    const dismiss = () => { if (el.parentNode) el.parentNode.removeChild(el); };
    el.onclick = () => {
      dismiss();
      if (!isError && lastPayload) showResult(lastPayload, true);
    };
    document.body.appendChild(el);
    setTimeout(dismiss, isError ? 12000 : 20000);
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
        <button class="playkey" aria-label="Play just the key moments in the YouTube player">⏭ Play key moments</button>
        <button class="listen" aria-label="Generate spoken audio of this summary">🔊 Listen to summary</button>
        <select class="voice" aria-label="Voice"></select>
        <button class="vpreview" title="Hear a sample of this voice"
          aria-label="Preview the selected voice">▶ Preview</button>
      </div>
      <div class="audioprogress">
        <svg class="circ" viewBox="0 0 36 36" width="20" height="20" aria-hidden="true">
          <circle class="circ-bg" cx="18" cy="18" r="15.5"></circle>
          <circle class="circ-fg" cx="18" cy="18" r="15.5"></circle>
        </svg>
        <span class="audiostatus" aria-live="polite"></span>
      </div>
      <div class="audioslot"></div>
      ${points ? `<h2>Key points</h2><ul class="points">${points}</ul>` : ""}
      <h2>Summary</h2><div class="body">${renderSummary(p.summary_md || "")}</div>
      ${p.rationale ? `<div class="rationale">${esc(p.rationale)}</div>` : ""}
      <div class="askwrap">
        <button class="asktoggle" aria-expanded="false">💬 Ask about this video</button>
        <div class="askpanel" hidden>
          <div class="asklog" role="log" aria-live="polite" aria-label="Conversation"></div>
          <div class="askrow">
            <textarea class="askinput" rows="1" aria-label="Ask a question about this video"
              placeholder="Ask a question about this video…"></textarea>
            <button class="asksend">Send</button>
          </div>
          <div class="askstatus" aria-live="polite"></div>
        </div>
      </div>`;
    const copy = root.querySelector(".copy");
    copy.hidden = false;
    copy.onclick = () => {
      navigator.clipboard.writeText(toMarkdown(p)).then(() => {
        copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1500);
      });
    };
    setupVoiceSelect();
    root.querySelector(".listen").onclick = requestAudio;
    root.querySelector(".vpreview").onclick = previewVoice;
    setupAsk(p);
    // Audio or key-moment work may still be running from before the panel was
    // closed. `busy` blocks a fresh request, so reflect that instead of rendering
    // buttons that silently do nothing when clicked.
    if (busy) {
      const play = root.querySelector(".playkey");
      const sel = root.querySelector(".voice");
      const prev = root.querySelector(".vpreview");
      if (play) play.disabled = true;
      if (sel) sel.disabled = true;
      if (prev) prev.disabled = true;
      if (audioPort) {
        setListenState("generating");          // still synthesizing — Stop works
        updateAudioStatus("Still generating audio…");
      } else {
        const listen = root.querySelector(".listen");
        if (listen) listen.disabled = true;
        updateAudioStatus("Still finding key moments…");
      }
    }
    root.querySelector(".playkey").onclick = requestSegments;
    // Restore a previously generated clip for this video this session.
    if (lastAudio && lastAudio.videoId === p.video_id && lastAudio.dataUrl) {
      renderAudio(lastAudio.dataUrl, true);
    }
  }

  function setupVoiceSelect() {
    const sel = root.querySelector(".voice");
    sel.innerHTML = VOICE_OPTIONS.map(
      (v) => `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("");
    // Promise form works in both Firefox (browser.*) and Chrome MV3 (chrome.*).
    api.storage.local.get({ voice: "amy" }).then((s) => {
      if (sel && [...sel.options].some((o) => o.value === s.voice)) sel.value = s.voice;
    }).catch(() => {});
  }

  // The Listen button has three states: idle, generating (a live Stop), and done
  // (Regenerate). One function owns label/handler/styling so nothing else clobbers
  // it mid-flight — renderAudio used to overwrite the label from under it.
  function setListenState(state) {
    const btn = root && root.querySelector(".listen");
    if (!btn) return;
    const stopping = state === "generating";
    btn.textContent = stopping ? "⏹ Stop"
      : state === "done" ? "🔊 Regenerate" : "🔊 Listen to summary";
    btn.setAttribute("aria-label", stopping
      ? "Stop generating audio and choose a different voice"
      : "Generate spoken audio of this summary");
    btn.classList.toggle("stopping", stopping);
    btn.disabled = false;
    btn.onclick = stopping ? abortAudio : requestAudio;
  }

  function listenIdleState() {
    // "Regenerate" only makes sense once a clip is actually sitting in the slot.
    return root && root.querySelector(".audioslot audio") ? "done" : "idle";
  }

  function requestAudio() {
    if (busy || !lastPayload) return;            // re-entrancy guard
    busy = true;
    const sel = root.querySelector(".voice");
    const status = root.querySelector(".audiostatus");
    const prev = root.querySelector(".vpreview");
    status.classList.remove("audioerr");
    setListenState("generating");
    if (sel) sel.disabled = true;
    if (prev) prev.disabled = true;
    status.textContent = "Starting…";
    setCircle(null);                                    // indeterminate until first %
    audioPort = api.runtime.connect({ name: "tldw" });
    audioPing = setInterval(() => { try { audioPort.postMessage({ type: "ping" }); } catch (_) {} }, 20000);
    audioPort.onMessage.addListener((m) => {
      if (!busy) return;
      bumpAudioSafety();                       // any traffic means we're not stuck
      if (m.type === "speakProgress") { updateAudioStatus(m.message, m.percent); return; }
      if (m.type === "audioChunk") { pushAudioChunk(m.b64, m.first); return; }
      if (m.type === "audioEnd") {
        teardownAudio(); finishAudioUI();
        lastAudio = { videoId: lastPayload && lastPayload.video_id, dataUrl: m.dataUrl };
        if (stream) endAudioStream();          // already playing — just close the buffer
        else renderAudio(m.dataUrl);           // no MSE here; play the finished clip
      } else if (m.type === "audio") {
        teardownAudio(); finishAudioUI(); renderAudio(m.dataUrl);
      } else if (m.type === "speakError") {
        teardownAudio(); discardStream(); finishAudioUI(); showAudioError(m.error);
      }
    });
    audioPort.onDisconnect.addListener(() => {
      if (!busy) return;
      teardownAudio(); finishAudioUI();
      showAudioError("Lost connection to the worker. Try again.");
    });
    resetStream();
    bumpAudioSafety();
    audioPort.postMessage({
      type: "speak", videoId: lastPayload.video_id,
      voice: sel ? sel.value : "amy", payload: lastPayload,
      stream: mseSupported(),        // no MSE (Firefox) -> ask for one buffered clip
    });
  }

  // Stop: drop the request, the partial clip, and the lock on the voice picker, so
  // a wrong-sounding voice can be swapped without waiting the synthesis out.
  function abortAudio() {
    if (!busy) return;
    // Say so explicitly — the worker deliberately keeps synthesizing through a bare
    // disconnect (closing the panel), so only this message cancels the request.
    try { audioPort.postMessage({ type: "stopSpeak" }); } catch (_) {}
    teardownAudio();
    discardStream();
    finishAudioUI();
    const status = root && root.querySelector(".audiostatus");
    if (status) status.textContent = "Stopped. Pick another voice and try again.";
  }

  // Throw away whatever streamed in, and put back the last finished clip (if this
  // was a Regenerate that got stopped) rather than leaving an empty slot.
  function discardStream() {
    if (stream && stream.audio) { try { stream.audio.pause(); } catch (_) {} }
    resetStream();
    const slot = root && root.querySelector(".audioslot");
    if (slot) slot.innerHTML = "";
    if (lastAudio && lastPayload && lastAudio.videoId === lastPayload.video_id) {
      renderAudio(lastAudio.dataUrl, true);
    }
  }

  // Idle watchdog: re-armed on every message, so a long-but-progressing stream is
  // never cut off while genuine silence still ends the request.
  function bumpAudioSafety() {
    if (audioSafety) clearTimeout(audioSafety);
    audioSafety = setTimeout(() => {
      if (!busy) return;
      teardownAudio(); finishAudioUI(); resetStream();
      showAudioError("Audio is taking too long. Make sure `tldw serve` is running.");
    }, 185000);
  }

  // --- Streaming playback (MediaSource) ---------------------------------------

  function mseSupported() {
    return typeof MediaSource !== "undefined" &&
      typeof MediaSource.isTypeSupported === "function" &&
      MediaSource.isTypeSupported("audio/mpeg");
  }

  function resetStream() {
    if (stream && stream.url) URL.revokeObjectURL(stream.url);
    stream = null;
  }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8;
  }

  function pushAudioChunk(b64, first) {
    if (first) {
      resetStream();
      if (!mseSupported()) return;             // Firefox: wait for the finished mp3
      const ms = new MediaSource();
      const url = URL.createObjectURL(ms);
      stream = { ms, buf: null, url, audio: renderAudio(url), pending: [],
                 ended: false, started: false };
      ms.addEventListener("sourceopen", () => {
        if (!stream || stream.ms !== ms) return;
        try {
          stream.buf = ms.addSourceBuffer("audio/mpeg");
        } catch (_) {                          // codec refused after all — fall back
          resetStream();
          return;
        }
        stream.buf.addEventListener("updateend", pumpStream);
        pumpStream();
      }, { once: true });
    }
    if (!stream) return;
    stream.pending.push(b64ToBytes(b64));
    pumpStream();
  }

  function pumpStream() {
    if (!stream || !stream.buf || stream.buf.updating) return;
    if (stream.pending.length) {
      try {
        stream.buf.appendBuffer(stream.pending.shift());
      } catch (_) { return; }                  // quota/closed — leave what we have
      // The Listen click is the user gesture this playback belongs to.
      if (stream.audio && stream.audio.paused && !stream.started) {
        stream.started = true;
        stream.audio.play().catch(() => {});   // blocked autoplay: controls are there
      }
      return;
    }
    if (stream.ended && stream.ms.readyState === "open") {
      try { stream.ms.endOfStream(); } catch (_) {}
    }
  }

  function endAudioStream() {
    if (!stream) return;
    stream.ended = true;
    pumpStream();
  }

  // --- Follow-up Q&A ------------------------------------------------------------

  function setupAsk(p) {
    const toggle = root.querySelector(".asktoggle");
    const panel = root.querySelector(".askpanel");
    const input = root.querySelector(".askinput");
    const send = root.querySelector(".asksend");
    const log = root.querySelector(".asklog");

    if (!chat || chat.videoId !== p.video_id) chat = { videoId: p.video_id, history: [] };
    if (chat.history.length || asking) {              // re-opened mid-conversation
      chat.history.forEach((m) => addMessage(m.role === "user" ? "you" : "bot", m.content));
      if (asking && askLive) {
        // An answer kept streaming while the panel was closed — re-attach it.
        askLive.bubble = addMessage("bot", askLive.text);
        askLive.bubble.classList.add("pending");
        setAskState("answering");
      }
      openAsk();
    }

    toggle.onclick = () => (panel.hidden ? openAsk(true) : closeAsk());
    send.onclick = () => (asking ? stopAsk() : submitQuestion());
    input.addEventListener("input", () => {          // grow with the question
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 140) + "px";
    });
    log.addEventListener("click", (e) => {
      const ts = e.target.closest && e.target.closest(".ts");
      if (ts) seekTo(Number(ts.dataset.t));
    });

    function openAsk(focus) {
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      toggle.textContent = "💬 Hide questions";
      if (focus) input.focus();
    }
    function closeAsk() {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      toggle.textContent = "💬 Ask about this video";
    }
  }

  function addMessage(who, text) {
    const log = root && root.querySelector(".asklog");
    if (!log) return null;
    const el = document.createElement("div");
    el.className = "msg " + who;
    el.innerHTML = who === "bot" ? renderAnswer(text) : renderSummary(text);
    log.appendChild(el);
    el.scrollIntoView({ block: "nearest" });
    return el;
  }

  // Markdown, then turn [12:34] citations into seek links. renderSummary escapes
  // first and only emits <p>/<strong>/<br>, but split on tags anyway so a future
  // renderer that emits attributes can't have one rewritten from inside.
  function renderAnswer(text) {
    return renderSummary(text)
      .split(/(<[^>]*>)/)
      .map((part, i) => (i % 2 ? part : part.replace(
        /\[(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\]/g,
        (m, a, b, c) => {
          const secs = c ? (+a) * 3600 + (+b) * 60 + (+c) : (+a) * 60 + (+b);
          const label = c ? `${a}:${b}:${c}` : `${a}:${b}`;
          return `<a class="ts" data-t="${secs}" title="Jump to ${label} in the video"` +
                 ` role="button" tabindex="0">${label}</a>`;
        })))
      .join("");
  }

  function seekTo(seconds) {
    const video = document.querySelector("video.html5-main-video")
      || document.querySelector("video");
    if (!video || !isFinite(seconds)) return;
    close();                            // get the modal out of the way to watch
    try { video.currentTime = seconds; video.play(); } catch (_) {}
  }

  function submitQuestion() {
    if (asking || busy || !lastPayload) return;
    const input = root.querySelector(".askinput");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    input.style.height = "auto";
    addMessage("you", question);
    chat.history.push({ role: "user", content: question });

    asking = true;
    setAskState("answering");
    const bubble = addMessage("bot", "");
    bubble.classList.add("pending");
    askLive = { text: "", bubble };

    askPort = api.runtime.connect({ name: "tldw" });
    askPing = setInterval(() => { try { askPort.postMessage({ type: "ping" }); } catch (_) {} }, 20000);
    askPort.onMessage.addListener((m) => {
      if (!asking) return;
      bumpAskSafety();
      if (m.type === "askProgress") { setAskStatus(m.message); return; }
      if (m.type === "askDelta") {
        askLive.text += m.text;
        if (askLive.bubble) {            // null while the panel is closed
          askLive.bubble.innerHTML = renderAnswer(askLive.text);
          askLive.bubble.scrollIntoView({ block: "nearest" });
        }
        return;
      }
      if (m.type === "askDone") {
        finishBubble();
        chat.history.push({ role: "assistant", content: askLive.text });
        askLive = null;
        teardownAsk(); setAskState("idle"); setAskStatus("");
      } else if (m.type === "askError") {
        // Keep a partial answer if one streamed in; drop an empty bubble.
        if (askLive.text) chat.history.push({ role: "assistant", content: askLive.text });
        else if (askLive.bubble) askLive.bubble.remove();
        finishBubble();
        askLive = null;
        teardownAsk(); setAskState("idle"); setAskStatus(m.error, true);
      }
    });
    askPort.onDisconnect.addListener(() => {
      if (!asking) return;
      finishBubble();
      askLive = null;
      teardownAsk(); setAskState("idle");
      setAskStatus("Lost connection to the worker. Try again.", true);
    });
    bumpAskSafety();
    askPort.postMessage({
      type: "ask", url: lastPayload.source_url,
      question, history: chat.history.slice(0, -1),   // the new question travels alone
    });
  }

  function finishBubble() {
    const b = askLive && askLive.bubble;
    if (b) b.classList.remove("pending");
  }

  function stopAsk() {
    if (!asking) return;
    try { askPort.postMessage({ type: "stopAsk" }); } catch (_) {}
    if (askLive) {
      finishBubble();
      // Keep a partial answer, drop an empty bubble.
      if (askLive.text) chat.history.push({ role: "assistant", content: askLive.text });
      else if (askLive.bubble) askLive.bubble.remove();
      askLive = null;
    }
    teardownAsk(); setAskState("idle"); setAskStatus("Stopped.");
  }

  function teardownAsk() {
    asking = false;
    if (askPing) { clearInterval(askPing); askPing = null; }
    if (askSafety) { clearTimeout(askSafety); askSafety = null; }
    if (askPort) { try { askPort.disconnect(); } catch (_) {} askPort = null; }
  }

  function bumpAskSafety() {
    if (askSafety) clearTimeout(askSafety);
    askSafety = setTimeout(() => {
      if (!asking) return;
      teardownAsk(); setAskState("idle");
      setAskStatus("That question is taking too long. Is `tldw serve` running?", true);
    }, 300000);
  }

  function setAskState(state) {
    const send = root && root.querySelector(".asksend");
    const input = root && root.querySelector(".askinput");
    if (!send) return;
    const answering = state === "answering";
    send.textContent = answering ? "⏹ Stop" : "Send";
    send.classList.toggle("stopping", answering);
    if (input) input.disabled = answering;
  }

  function setAskStatus(msg, isError) {
    const el = root && root.querySelector(".askstatus");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("askerr", !!isError);
  }

  // --- Voice preview ------------------------------------------------------------

  function previewVoice() {
    const btn = root && root.querySelector(".vpreview");
    const sel = root && root.querySelector(".voice");
    if (!btn || !sel || btn.disabled) return;
    const voice = sel.value;
    btn.disabled = true;
    btn.textContent = "…";
    const port = api.runtime.connect({ name: "tldw" });
    const done = (label) => {
      try { port.disconnect(); } catch (_) {}
      btn.disabled = false;
      btn.textContent = label;
    };
    port.onMessage.addListener((m) => {
      if (m.type === "preview" && m.voice === voice) {
        done("▶ Preview");
        const a = new Audio(m.dataUrl);
        a.play().catch(() => {});
      } else if (m.type === "previewError") {
        done("▶ Preview");
        showAudioError(m.error);
      }
    });
    port.onDisconnect.addListener(() => { if (btn.disabled) done("▶ Preview"); });
    port.postMessage({ type: "preview", voice });
  }

  function teardownAudio() {
    busy = false;
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
    const play = root && root.querySelector(".playkey");
    const sel = root && root.querySelector(".voice");
    const prev = root && root.querySelector(".vpreview");
    const status = root && root.querySelector(".audiostatus");
    setListenState(listenIdleState());
    if (play) play.disabled = false;
    if (sel) sel.disabled = false;
    if (prev) { prev.disabled = false; prev.textContent = "▶ Preview"; }
    if (status) status.textContent = "";
    hideCircle();
  }

  // --- Play key moments: fetch segment timestamps, then skip the YouTube player ---

  function requestSegments() {
    if (busy) return;
    const vid = lastPayload && lastPayload.video_id;
    if (lastSegments && lastSegments.videoId === vid && lastSegments.segments.length) {
      startSkip(lastSegments.segments);   // cached this session — skip the round-trip
      return;
    }
    busy = true;
    const listen = root.querySelector(".listen");
    const play = root.querySelector(".playkey");
    const sel = root.querySelector(".voice");
    const status = root.querySelector(".audiostatus");
    status.classList.remove("audioerr");
    const prev = root.querySelector(".vpreview");
    if (listen) listen.disabled = true;
    if (play) play.disabled = true;
    if (sel) sel.disabled = true;
    if (prev) prev.disabled = true;
    status.textContent = "Finding key moments…";
    setCircle(null);
    segPort = api.runtime.connect({ name: "tldw" });
    segPing = setInterval(() => { try { segPort.postMessage({ type: "ping" }); } catch (_) {} }, 20000);
    segPort.onMessage.addListener((m) => {
      if (!segPort) return;        // torn down — a late message from a dead request.
                                   // (Not `busy`: streaming skip playback releases the
                                   // panel's UI lock while clips are still arriving.)
      if (m.type === "segProgress") { updateAudioStatus(m.message, m.percent); return; }
      if (m.type === "segmentAdded") {
        if (!skipSegs) {
          startSkipStreaming(m.segment);  // start immediately on first clip
        } else {
          addSegmentToSkip(m.segment);   // append dynamically while playing
        }
        return;
      }
      if (m.type === "segmentsDone") {
        segsComplete = true;
        teardownSeg(); finishAudioUI();
        const allSegs = skipSegs ? skipSegs.slice() : [];
        if (!skipSegs) {
          showAudioError("No key moments found.");
        } else if (skipPaused) {
          // Caught up to end of stream with no more clips — finish
          try { skipVideo && skipVideo.pause(); } catch (_) {}
          finishSkip();
        }
        lastSegments = { videoId: lastPayload && lastPayload.video_id, segments: allSegs };
        return;
      }
      if (m.type === "segments") {
        // Prefetch/cache/batch path: all clips arrive at once
        lastSegments = { videoId: lastPayload && lastPayload.video_id, segments: m.segments };
        teardownSeg(); finishAudioUI(); startSkip(m.segments);
      } else if (m.type === "segError") { teardownSeg(); finishAudioUI(); showAudioError(m.error); }
    });
    segPort.onDisconnect.addListener(() => {
      if (!segPort) return;        // our own teardown, not a worker that went away
      teardownSeg(); finishAudioUI();
      showAudioError("Lost connection to the worker. Try again.");
    });
    segSafety = setTimeout(() => {
      if (!busy) return;
      teardownSeg(); finishAudioUI();
      showAudioError("Finding key moments is taking too long. Is `tldw serve` running?");
    }, 320000);
    segPort.postMessage({
      type: "getSegments",
      url: (lastPayload && lastPayload.source_url) || location.href,
    });
  }

  function teardownSeg() {
    busy = false;
    if (segPing) { clearInterval(segPing); segPing = null; }
    if (segSafety) { clearTimeout(segSafety); segSafety = null; }
    if (segPort) { try { segPort.disconnect(); } catch (_) {} segPort = null; }
  }

  function startSkip(segments) {
    const video = document.querySelector("video.html5-main-video")
      || document.querySelector("video");
    if (!video || !segments || !segments.length) {
      showAudioError("Couldn't find the YouTube player to control.");
      return;
    }
    stopSkip();                                   // clear any prior session
    skipVideo = video;
    skipSegs = segments.slice().sort((a, b) => a.start - b.start);
    skipIdx = 0;
    segsComplete = true;      // the whole list is in hand (cache/prefetch/batch path),
                              // so the last clip must finish rather than wait for more
    close();                                       // close the modal so you can watch
    skipHandler = onSkipTick;
    video.addEventListener("timeupdate", skipHandler);
    showPill();
    try { video.currentTime = skipSegs[0].start; video.play(); } catch (_) {}
    updatePill();
  }

  // Start playback immediately with the first streaming segment, without closing segPort.
  function startSkipStreaming(firstSeg) {
    const video = document.querySelector("video.html5-main-video")
      || document.querySelector("video");
    if (!video) { showAudioError("Couldn't find the YouTube player to control."); return; }
    // Clear any prior skip session without touching segPort (still needed for more segments)
    if (skipVideo && skipHandler) skipVideo.removeEventListener("timeupdate", skipHandler);
    skipVideo = video;
    skipSegs = [firstSeg];
    skipIdx = 0;
    segsComplete = false;
    skipPaused = false;
    removePill();
    close({ keepSeg: true });
    busy = false;                  // modal is gone; don't leave the panel's UI locked
    skipHandler = onSkipTick;
    video.addEventListener("timeupdate", skipHandler);
    showPill();
    try { video.currentTime = skipSegs[0].start; video.play(); } catch (_) {}
    updatePill();
  }

  // Insert a new segment in sorted order and resume if paused waiting for the next clip.
  function addSegmentToSkip(seg) {
    if (!skipSegs) return;
    const insertAt = skipSegs.findIndex(s => s.start > seg.start);
    if (insertAt === -1) {
      skipSegs.push(seg);
    } else {
      skipSegs.splice(insertAt, 0, seg);
      if (insertAt <= skipIdx) skipIdx++;  // preserve current position
    }
    updatePill();
    if (skipPaused) {
      const nextIdx = skipIdx + 1;
      if (nextIdx < skipSegs.length) {
        skipPaused = false;
        skipIdx = nextIdx;
        try { skipVideo.currentTime = skipSegs[skipIdx].start; skipVideo.play(); } catch (_) {}
        updatePill();
      }
    }
  }

  function onSkipTick() {
    if (!skipVideo || !skipSegs) return;
    const seg = skipSegs[skipIdx];
    if (!seg) return;
    if (skipVideo.currentTime >= seg.end - 0.05) {  // reached this segment's end
      if (skipIdx + 1 < skipSegs.length) {
        skipIdx += 1;
        try { skipVideo.currentTime = skipSegs[skipIdx].start; } catch (_) {}
        updatePill();
      } else if (!segsComplete) {
        // More clips are still streaming — pause and wait for the next one
        skipPaused = true;
        try { skipVideo.pause(); } catch (_) {}
        updatePill();
      } else {
        try { skipVideo.pause(); } catch (_) {}
        finishSkip();
      }
    }
  }

  function stopSkip() {
    if (skipVideo && skipHandler) skipVideo.removeEventListener("timeupdate", skipHandler);
    skipHandler = skipVideo = skipSegs = null;
    skipIdx = 0;
    segsComplete = false;
    skipPaused = false;
    removePill();
    if (segPort) { teardownSeg(); }  // stop any in-flight streaming segment fetch
  }

  function finishSkip() {
    if (skipVideo && skipHandler) skipVideo.removeEventListener("timeupdate", skipHandler);
    skipHandler = skipVideo = skipSegs = null;
    skipIdx = 0;
    segsComplete = false;
    skipPaused = false;
    const pill = document.getElementById("tldw-pill");
    if (pill) {
      const label = pill.querySelector(".pill-label");
      if (label) label.textContent = "✓ Key moments done";
      setTimeout(removePill, 4000);
    }
  }

  function fmtTime(s) {
    const t = Math.floor(s);
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const sec = t % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function skipPrev() {
    if (!skipSegs || skipIdx <= 0) return;
    skipIdx -= 1;
    try { skipVideo.currentTime = skipSegs[skipIdx].start; skipVideo.play(); } catch (_) {}
    updatePill();
  }

  function skipNext() {
    if (!skipSegs || skipIdx >= skipSegs.length - 1) return;
    skipIdx += 1;
    try { skipVideo.currentTime = skipSegs[skipIdx].start; skipVideo.play(); } catch (_) {}
    updatePill();
  }

  function showPill() {
    removePill();
    const pill = document.createElement("div");
    pill.id = "tldw-pill";
    pill.style.cssText =
      "position:fixed;z-index:2147483647;bottom:84px;left:50%;transform:translateX(-50%);" +
      "background:#1e1f24;color:#e9e9ea;font:14px system-ui,-apple-system,sans-serif;" +
      "padding:8px 14px;border-radius:999px;box-shadow:0 6px 24px rgba(0,0,0,.45);" +
      "display:flex;align-items:center;gap:10px;";

    function navBtn(text, ariaLabel, fn) {
      const btn = document.createElement("button");
      btn.textContent = text;
      btn.setAttribute("aria-label", ariaLabel);
      btn.style.cssText =
        "all:unset;cursor:pointer;padding:0 4px;color:#e9e9ea;font-size:18px;line-height:1;";
      btn.onclick = fn;
      return btn;
    }

    const prev = navBtn("‹", "Previous key moment", skipPrev);
    prev.id = "tldw-pill-prev";
    const label = document.createElement("span");
    label.className = "pill-label";
    label.style.cssText = "white-space:nowrap;";
    const next = navBtn("›", "Next key moment", skipNext);
    next.id = "tldw-pill-next";
    const stop = navBtn("✕", "Stop skipping", stopSkip);
    stop.style.cssText += "font-size:14px;margin-left:2px;";

    pill.appendChild(prev);
    pill.appendChild(label);
    pill.appendChild(next);
    pill.appendChild(stop);
    (document.fullscreenElement || document.body).appendChild(pill);
  }

  function updatePill() {
    const pill = document.getElementById("tldw-pill");
    if (!pill || !skipSegs) return;
    const label = pill.querySelector(".pill-label");
    if (label) {
      if (skipPaused) {
        label.textContent = `⏳ Loading next clip... (${skipIdx + 1}/${skipSegs.length}+)`;
      } else {
        const seg = skipSegs[skipIdx];
        const total = `${skipSegs.length}${segsComplete ? "" : "+"}`;
        label.textContent = `⏭ Clip ${skipIdx + 1}/${total} · ${fmtTime(seg.start)}–${fmtTime(seg.end)}`;
      }
    }
    const prev = document.getElementById("tldw-pill-prev");
    if (prev) prev.style.opacity = skipIdx <= 0 ? "0.3" : "1";
    const next = document.getElementById("tldw-pill-next");
    if (next) next.style.opacity = skipIdx >= skipSegs.length - 1 ? "0.3" : "1";
  }

  function removePill() {
    const pill = document.getElementById("tldw-pill");
    if (pill && pill.parentNode) pill.parentNode.removeChild(pill);
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

  function renderAudio(src, restore) {
    const slot = root && root.querySelector(".audioslot");
    if (!slot) return null;
    slot.innerHTML = "";                                 // replace, never stack
    const a = document.createElement("audio");
    a.controls = true; a.src = src;
    a.setAttribute("aria-label", "Spoken summary");
    slot.appendChild(a);
    if (!busy) setListenState("done");        // mid-stream the button is still Stop
    if (!restore) {
      // A blob: URL is a live MediaSource, not a clip worth restoring later.
      if (src.startsWith("data:")) {
        lastAudio = { videoId: lastPayload && lastPayload.video_id, dataUrl: src };
      }
      a.focus();                                         // don't steal focus on re-open
    }
    return a;
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

  api.runtime.onMessage.addListener((msg) => {
    if (msg.type === "TLDW_INVOKE") {
      // Re-open instantly if we already summarized this video this page-session.
      if (lastPayload && lastPayload.video_id === msg.videoId) showResult(lastPayload, true);
      else if (requestActive && currentVideoId === msg.videoId) {
        // Re-attach to the run already in flight. Restore where it had got to:
        // the long Claude step sends ONE progress event and then goes quiet for a
        // minute, so without this the bar sits at "Starting… 0%" until the result.
        backgrounded = false;
        // showLoading() resets progressPct, and replaying lastProgress would only
        // restore the server's last reported figure (15% for the Claude step) —
        // rewinding whatever the creep had reached since. Carry it across.
        const carried = progressPct;
        showLoading();
        progressPct = carried;
        if (lastProgress) {
          updateProgress(lastProgress.message, lastProgress.percent, lastProgress.creep);
        }
        applyWidth();
      } else startSummarize(msg.url, msg.videoId);
    } else if (msg.type === "TLDW_ERROR") showError(msg.error);
  });
})();
