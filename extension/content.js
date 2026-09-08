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
          margin: 4px 0 6px; }
        .audiorow select { font: inherit; font-size: 13px; padding: 4px 6px;
          border-radius: 7px; border: 1px solid rgba(128,128,128,.4);
          background: transparent; color: inherit; }
        .audioprogress { display: flex; align-items: center; gap: 8px; margin: 0 0 10px;
          min-height: 14px; }
        .audiostatus { font-size: 12px; color: #888; }
        .audiostatus.audioerr { color: #c0392b; }
        .audioslot audio { width: 100%; margin-bottom: 12px; }
        .listen.stopping { border-color: #c00; color: #c00; }
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
    teardownSeg();                    // and any in-flight segment fetch (NOT the
                                      // skip engine, which runs after the modal closes)
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
    port = api.runtime.connect({ name: "tldw" });
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
    root.querySelector(".vpreview").onclick = previewVoice;
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
      if (!busy) return;
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
      if (!busy) return;
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
    close();
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
      else startSummarize(msg.url, msg.videoId);
    } else if (msg.type === "TLDW_ERROR") showError(msg.error);
  });
})();
