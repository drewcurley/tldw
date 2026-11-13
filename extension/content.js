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
  const SPONSOR_URL = "https://github.com/sponsors/drewcurley";
  const SPONSOR_AFTER_VIDEOS = 25;     // ask only once there's real value to point at
  const SPONSOR_MAX_SHOWS = 3;         // then stop on its own, dismissed or not
  let closeAction = "continue";          // "continue" | "abort"
  let backgrounded = false;              // panel closed while a summary was running
  let currentVideoId = null;
  let lastProgress = null;               // last progress event of the running summary
  let partialState = null;               // {meta, points, paragraphs} while it streams
  api.storage.local.get({ closeAction: "continue" })
    .then((s) => { closeAction = s.closeAction === "abort" ? "abort" : "continue"; })
    .catch(() => {});
  // --- settings listener (BEGIN) ------------------------------------------------
  // The only storage.onChanged listener. It is handed every changed key (the
  // token too, architecture.md risk 13), so it reads only the keys it uses and
  // passes on nothing else. onMuteSettings is set once "Cover listed words"
  // exists (rev 5.6); a change before then is a no-op.
  let onMuteSettings = null;
  if (api.storage.onChanged) {
    api.storage.onChanged.addListener((changes, area) => {
      if (area !== "local" || !changes) return;
      if (changes.closeAction) {
        closeAction = changes.closeAction.newValue === "abort" ? "abort" : "continue";
      }
      if ((changes.muteSound || changes.muteWords) && onMuteSettings) {
        const live = {};
        if (changes.muteSound) live.sound = { newValue: changes.muteSound.newValue };
        if (changes.muteWords) live.words = { newValue: changes.muteWords.newValue };
        onMuteSettings(live);
      }
    });
  }
  // --- settings listener (END) --------------------------------------------------

  // The one decision close() makes, kept pure so it can be tested directly.
  function closePolicy(opts, pref) {
    return {
      keepSeg: !!(opts && opts.keepSeg === true),
      background: pref !== "abort",
    };
  }
  // { url } while the server is transcribing this video's audio. The panel can't
  // work this out for itself: on the "always" setting it never asked to transcribe,
  // and after a close/re-open it has no memory of having done so. The server says.
  let txLive = null;
  // An offer that arrived while the panel was closed, shown when they come back.
  let pendingOffer = null;
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
        // Section headings: the summary has always been written with them, and they
        // rendered as a literal "## Title" because nothing handled them here.
        const h = /^(#{1,4})\s+(.+)$/.exec(block.trim());
        if (h && lines.length === 1) {
          const level = Math.min(h[1].length + 2, 6);     // # -> h3, ## -> h4
          return `<h${level} class="shead">${inlineMd(h[2])}</h${level}>`;
        }
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
        /* The install command is the one thing on that screen to act on, so it
           reads as a command rather than as more of the red error text. */
        .installhint { color: #e9e9ea; background: rgba(128,128,128,.18);
          padding: 8px 10px; border-radius: 6px; margin: 6px 0;
          font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
          white-space: pre-wrap; word-break: break-word; user-select: all; }
        .hint { font-size: 12px; color: #888; margin: 4px 0; }
        .txsrc { color: #888; }
        .audiorow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin: 4px 0 6px; }
        .audiorow select { font: inherit; font-size: 13px; padding: 4px 6px;
          border-radius: 7px; border: 1px solid rgba(128,128,128,.4);
          background: transparent; color: inherit; }
        .audioprogress { display: flex; align-items: center; gap: 8px; margin: 0 0 10px;
          min-height: 14px; }
        .audiostatus { font-size: 12px; color: #888; }
        .audiostatus.audioerr { color: #c0392b; }
        .playerrow { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
        .playerrow audio { flex: 1; min-width: 0; }
        .dl { flex: 0 0 auto; padding: 6px 10px; font-size: 15px; line-height: 1.2; }
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
        .shead { font-size: 14px; font-weight: 600; margin: 16px 0 6px;
          text-transform: none; letter-spacing: 0; opacity: 1; }
        .writing { font-size: 13px; color: #888; margin: 10px 0 4px; }
        .sponsor { margin-top: 18px; padding: 12px 14px; border-radius: 10px;
          background: rgba(128,128,128,.1); font-size: 13px; line-height: 1.5; }
        .sponsor .srow { display: flex; gap: 10px; align-items: center;
          margin-top: 10px; flex-wrap: wrap; }
        .sponsor a.sbtn { text-decoration: none; padding: 5px 12px; border-radius: 7px;
          border: 1px solid rgba(128,128,128,.4); color: inherit; font-size: 13px; }
        .sponsor button { font-size: 12px; padding: 4px 8px; }
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
        .covertoggle[aria-pressed="true"] { border-color: #c00; }
        .covertoggle[aria-pressed="true"] .covercheck::before { content: "✓ "; }
        .coverstatus { font-size: 12px; color: #767676; margin: 0 0 10px; }
        @media (prefers-color-scheme: dark) { .coverstatus { color: #888; } }
        .coverstatus:empty { margin: 0; }
        .sr { position: absolute; width: 1px; height: 1px; overflow: hidden;
          clip: rect(0 0 0 0); white-space: nowrap; }
      </style>
      <div class="backdrop" part="backdrop">
        <div class="panel" role="dialog" aria-modal="true" aria-labelledby="tldw-h" tabindex="-1">
          <div class="row">
            <h1 id="tldw-h">TL;DW</h1>
            <div class="btns">
              <button class="regen" hidden title="Summarize again, ignoring the cached summary (keeps the transcript)">↻</button>
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
    // A transcription is the longest-running thing here, so "Stop and discard"
    // has to reach it too. A bare disconnect would leave it saturating the CPU
    // for minutes after the user asked for it to stop.
    if (txLive) { stopTranscribe(txLive.url); txLive = null; }
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

  // --- What the server would otherwise ask YouTube for ---------------------------
  //
  // YouTube no longer serves captions to anything that isn't the player. The
  // timedtext endpoint answers 429 to yt-dlp and to curl, and 200-with-an-empty-body
  // to a fetch made from this page -- it wants a proof-of-origin token we can't mint.
  // Measured, for all of json3/vtt/raw, which is why the previous version of this
  // function could only ever report "captions-empty".
  //
  // The one copy of the captions still within reach is the transcript *panel*: the
  // page loads that for itself, through its own session. So we open the panel, read
  // what it rendered, and put it back the way we found it.
  //
  // Doing this in the content script rather than the worker keeps it inside the
  // page's own origin and session -- no host permission and no MAIN-world injection
  // (the first attempt at this, which the activeTab grant doesn't cover).

  const CLOCK = /^\d{1,2}(?::\d{2}){1,2}$/;
  const SEGMENT_ROWS = "transcript-segment-view-model, ytd-transcript-segment-renderer";
  const PANEL = "ytd-engagement-panel-section-list-renderer";
  const TRANSCRIPT_BUTTON = "ytd-video-description-transcript-section-renderer button";
  // A panel that is going to fill starts doing so in well under a second (measured
  // at ~300ms against a real page). This ceiling is therefore not about patience but
  // about giving up: every second spent here on a video whose panel will never fill
  // is a second added to the summary, because the server's fallback can't start
  // until we've answered.
  const PANEL_WAIT_MS = 8000;      // for the panel's first row to appear
  const PANEL_SETTLE_MS = 5000;    // ...and for the last one, once they're arriving
  const PANEL_POLL_MS = 200;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function clockToMs(text) {
    const parts = String(text).trim().split(":");
    if (parts.length < 2 || parts.length > 3) return null;
    let secs = 0;
    for (const part of parts) {
      if (!/^\d+$/.test(part)) return null;
      secs = secs * 60 + Number(part);
    }
    return secs * 1000;
  }

  function isoDurationMs(iso) {
    const m = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(String(iso || "").trim());
    if (!m) return null;
    const ms = ((+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0)) * 1000;
    return ms > 0 ? ms : null;
  }

  // A panel row is read structurally, not by class name: YouTube's generated class
  // names churn, but "the timestamp is the child that reads like a clock, the words
  // are what's left" has held across both the old renderer and the current one. The
  // row also carries a screen-reader twin of the timestamp ("5 seconds") which is
  // English-only, so it's dropped by class rather than by matching its text.
  function rowStartMs(row) {
    const stamp = row.querySelector(".segment-timestamp");
    if (stamp) return clockToMs(stamp.textContent || "");
    for (const el of row.querySelectorAll("*")) {
      if (el.children.length) continue;
      const text = (el.textContent || "").trim();
      if (CLOCK.test(text)) return clockToMs(text);
    }
    return null;
  }

  function rowText(row) {
    const direct = row.querySelector(".segment-text");
    if (direct) return (direct.textContent || "").trim();
    const parts = [];
    for (const el of row.children) {
      const text = (el.textContent || "").trim();
      if (!text || CLOCK.test(text)) continue;
      if (/a11y/i.test(String(el.className || ""))) continue;
      parts.push(text);
    }
    return parts.join(" ").trim();
  }

  function readTranscriptRows(doc) {
    const cues = [];
    for (const row of (doc || document).querySelectorAll(SEGMENT_ROWS)) {
      const start = rowStartMs(row);
      const text = rowText(row);
      if (start !== null && text) cues.push({ start_ms: start, text });
    }
    return cues;
  }

  // The panel that holds the transcript. Finding it by its rows alone would miss
  // the case this whole thing has to handle -- a panel that opened and never
  // filled -- and we would leave it open on a page we were only supposed to read.
  function transcriptPanel(doc) {
    const panels = Array.prototype.slice.call(
      (doc || document).querySelectorAll(PANEL));
    const named = (p) => /transcript/i.test(p.getAttribute("target-id") || "");
    const open = (p) => (p.getAttribute("visibility") || "").includes("EXPANDED");
    return panels.find((p) => p.querySelector(SEGMENT_ROWS))
      || panels.find((p) => named(p) && open(p))
      || panels.find(named)
      || null;
  }

  // Title, channel and duration are all in the page's own metadata tags, so none of
  // this costs a request. Duration matters: the last cue's timestamp is where the
  // final sentence *starts*, not where the video ends.
  function pageMeta(doc) {
    const d = doc || document;
    const attr = (sel, name) => {
      const el = d.querySelector(sel);
      return el ? (el.getAttribute(name) || "").trim() : "";
    };
    const text = (sel) => {
      const el = d.querySelector(sel);
      return el ? (el.textContent || "").trim() : "";
    };
    const out = {
      title: attr('meta[name="title"]', "content")
        || text("#above-the-fold h1, ytd-watch-metadata h1"),
      channel: attr('span[itemprop="author"] link[itemprop="name"]', "content")
        || text("#owner #channel-name a, ytd-channel-name a"),
    };
    const ms = isoDurationMs(attr('meta[itemprop="duration"]', "content"));
    if (ms) out.duration_ms = ms;
    return out;
  }

  // Wait for the row count to stop growing: the panel streams its segments in, so
  // the first row appearing does not mean the last one has.
  async function settledRows(deadlineMs) {
    let previous = -1;
    let count = readTranscriptRows().length;
    const until = Date.now() + deadlineMs;
    while (count !== previous && Date.now() < until) {
      previous = count;
      await sleep(PANEL_POLL_MS);
      count = readTranscriptRows().length;
    }
    return count;
  }

  // Always resolves. A failure here just means the server fetches instead, and the
  // status says which failure it was -- silence made the first version undiagnosable.
  async function collectPageTranscript() {
    try {
      const meta = pageMeta();
      const already = readTranscriptRows().length > 0;
      if (!already) {
        const button = document.querySelector(TRANSCRIPT_BUTTON);
        if (!button) return { ...meta, status: "no-transcript-button" };
        // The button lives in the collapsed description; it doesn't need to be
        // on screen to be clicked, but the panel it opens takes a moment to fill.
        button.click();
        const until = Date.now() + PANEL_WAIT_MS;
        while (Date.now() < until && !readTranscriptRows().length) {
          await sleep(PANEL_POLL_MS);
        }
      }
      const count = await settledRows(PANEL_SETTLE_MS);
      const cues = readTranscriptRows();
      if (!already) restoreTranscriptPanel();
      if (!count || !cues.length) {
        // The panel opened but never filled: YouTube refused its own get_transcript
        // call. Seen on videos whose captions aren't auto-generated.
        return { ...meta, status: "panel-empty" };
      }
      return { ...meta, cues, status: "ok-panel (" + cues.length + " cues)" };
    } catch (e) {
      return { status: "error-" + ((e && e.name) || "unknown") };
    }
  }

  // Put the panel back. We opened it to read it; leaving it open would rearrange the
  // page around someone who only asked for a summary.
  function restoreTranscriptPanel() {
    try {
      const panel = transcriptPanel();
      const close = panel && panel.querySelector("#visibility-button button");
      if (close) close.click();
    } catch (_) {}
  }

  // What the cache probe's answer means. A hit is a finished summary, delivered
  // exactly as a streamed one would be. Anything else -- a miss, a 404 from an
  // older server, an error -- means "do what we always did".
  function probeOutcome(m) {
    return m && m.hit && m.payload
      ? { type: "result", payload: m.payload, cached: true }
      : null;
  }

  function startSummarize(url, videoId, refresh, transcribe) {
    // A new run abandons whatever the last one left connected (mount() no longer
    // does this, and it must not — see the comment there).
    clearTimers();
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    lastProgress = null;
    partialState = null;
    txLive = null;
    showLoading();
    requestActive = true;
    currentVideoId = videoId;
    backgrounded = false;
    const myPort = port = api.runtime.connect({ name: "tldw" });
    // Read the page's transcript, then ask for a summary: the flow as it has
    // always been, and what every miss falls back to.
    const summarizeFromPage = () => collectPageTranscript().then((page) => {
      // Cancelled -- or superseded by a newer run, for another video -- while we
      // were reading. Checking requestActive alone isn't enough: a newer run sets
      // it again, and this video's transcript would go out on that run's request.
      if (!requestActive || port !== myPort) return;
      myPort.postMessage({ type: "summarize", url, videoId, refresh: !!refresh,
                           page, transcribe: !!transcribe });
    });
    port.onMessage.addListener((m) => {
      if (!requestActive) return;
      if (m.type === "probe") {
        const hit = probeOutcome(m);
        if (!hit) { summarizeFromPage(); return; }
        m = hit;          // falls through to the ordinary result handling below
      }
      if (m.type === "progress") {
        lastProgress = m;          // replayed if the panel is re-opened mid-run
        // Once the summary is on screen the loading bar is gone; don't redraw it.
        if (!partialState) updateProgress(m.message, m.percent, m.creep);
        return;
      }
      if (m.type === "meta" || m.type === "partial") { onPartial(m); return; }
      if (m.type === "transcribing") {
        txLive = { url };
        if (!partialState) addStopControl(url);
        // The ordinary ceiling is sized for a model call and would declare failure
        // part-way through a wait that is behaving exactly as promised.
        if (safetyTimer) clearTimeout(safetyTimer);
        safetyTimer = setTimeout(() => {
          if (!requestActive) return;
          requestActive = false;
          const msg = "Transcribing is taking longer than expected. It may still "
            + "finish on the server — try again in a few minutes.";
          if (backgrounded) endBackground(msg, true);
          else showError(msg);
        }, 2460000);
        return;
      }
      if (m.type === "needsTranscription") {
        requestActive = false;
        partialState = null;
        txLive = null;
        pendingOffer = { offer: m.offer, url, videoId };
        if (backgrounded) {
          // The panel was closed deliberately. Say there's something to decide and
          // let them come back to it, rather than reopening over the video.
          endBackground("TL;DW needs an answer about this video");
          return;
        }
        showTranscribeOffer(m.offer, url, videoId);
        return;
      }
      requestActive = false;
      partialState = null;
      txLive = null;
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
    // Ask whether a summary already exists before reading the page. Reading it
    // opens YouTube's transcript panel -- 0.5 to 13 seconds and a request to
    // YouTube -- and used to happen even for a summary already in the cache. A
    // refresh or an accepted transcription wants new work, so those go straight
    // to the page as before.
    if (refresh || transcribe) summarizeFromPage();
    else port.postMessage({ type: "probeCache", url, videoId });
    // Heartbeat: the page never suspends, so pinging every 20s keeps the MV3 service
    // worker alive through a long (60s+) summarize that would otherwise be killed.
    pingTimer = setInterval(() => {
      try { port.postMessage({ type: "ping" }); } catch (_) {}
    }, 20000);
    // Transcribing audio legitimately runs for many minutes; the ordinary ceiling
    // would fire part-way through a wait the user has already agreed to.
    safetyTimer = setTimeout(() => {
      if (!requestActive) return;
      requestActive = false;
      const msg = "This is taking too long. Make sure `tldw serve` is running, then try again.";
      if (backgrounded) endBackground(msg, true);
      else showError(msg);
    }, transcribe ? 2460000 : 160000);
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

  // Only for transcription: everything else here finishes in under a minute, so a
  // stop button would be noise. An eight-minute job without one is a trap.
  function addStopControl(url) {
    const status = root && root.querySelector(".status");
    if (!status) return;
    const row = document.createElement("div");
    row.className = "audiorow";
    const btn = document.createElement("button");
    btn.className = "stoptx";
    btn.textContent = "⏹ Stop transcribing";
    btn.setAttribute("aria-label", "Stop transcribing this video's audio");
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Stopping…";
      stopTranscribe(url);
    });
    row.appendChild(btn);
    status.appendChild(row);
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
      if (pendingOffer) {
        const p = pendingOffer;
        showTranscribeOffer(p.offer, p.url, p.videoId);
        return;
      }
      if (!isError && lastPayload) showResult(lastPayload, true);
    };
    document.body.appendChild(el);
    setTimeout(dismiss, isError ? 12000 : 20000);
  }

  // "" means the server couldn't size this video, which is different from "quick".
  // Quoting the smallest possible figure for a video of unknown length would be a
  // promise we have no basis for.
  function fmtEstimate(seconds) {
    const secs = Number(seconds) || 0;
    if (secs <= 0) return "";
    // Rounding first would make 30 seconds "about a minute", since JS rounds .5 up.
    if (secs < 60) return "under a minute";
    const mins = Math.round(secs / 60);
    return mins === 1 ? "about a minute" : `about ${mins} minutes`;
  }

  // A video with no captions. The audio can still be transcribed locally, but that
  // costs minutes of CPU, so it is offered rather than simply done.
  function showTranscribeOffer(offer, url, videoId) {
    if (!host) mount();
    clearTimers();
    pendingOffer = null;
    const c = root.querySelector(".content");
    if (!offer || offer.installed === false) {
      const hint = (offer && offer.install_hint) || "pipx inject youtube-tldw faster-whisper";
      c.innerHTML = `
        <div class="status err" role="status" aria-live="polite">
          <p>This video has no subtitles or auto-captions. TL;DW can transcribe the
          audio locally instead, but that needs Whisper, which isn't installed yet.</p>
          <p>Install it, then restart <code>tldw serve</code> and click TL;DW again:</p>
          <pre class="installhint">${esc(hint)}</pre>
          <p>If you installed tldw with pip rather than pipx, use
          <code>pip install faster-whisper</code> instead.</p>
        </div>`;
      return;
    }
    const estimate = fmtEstimate(offer.estimate_s);
    const howLong = estimate
      ? `that takes roughly <strong>${esc(estimate)}</strong>`
      : "its length isn't known, so this could take a while";
    c.innerHTML = `
      <div class="status" role="status" aria-live="polite">
        <p>This video has no subtitles or auto-captions.</p>
        <p>TL;DW can transcribe the audio on your own machine instead. For this
        video ${howLong}, and it will keep a CPU busy while it runs. The
        transcript is kept, so it only happens once.</p>
        <p class="hint">The first transcription also downloads a one-off
        ${esc(offer.model || "speech")} model (a few hundred MB).</p>
        <div class="audiorow">
          <button class="transcribe" aria-label="Transcribe this video's audio locally">Transcribe the audio</button>
        </div>
      </div>`;
    const btn = c.querySelector(".transcribe");
    if (btn) {
      btn.addEventListener("click", () => {
        startSummarize(url, videoId, false, true);
      });
    }
  }

  // Stop a transcription that is already running. Unlike closing the panel -- which
  // deliberately leaves work going -- this is an explicit cancel, and it travels
  // over the live port the worker is already listening on.
  function stopTranscribe(url) {
    try { if (port) port.postMessage({ type: "stopTranscribe", url }); } catch (_) {}
  }

  function showError(msg) {
    if (!host) mount();
    clearTimers();
    const c = root.querySelector(".content");
    c.innerHTML = `<div class="status err">${esc(msg).replace(/`([^`]+)`/g, "<code>$1</code>")}</div>`;
  }

  // The action row, shared by the finished view and the one that fills in while
  // the summary is still being written. Same markup in both, so nothing below it
  // shifts when the result lands on top of whatever you're already reading.
  function audioRowHtml(pending) {
    const off = pending ? ' disabled title="Available once the summary is finished"' : "";
    return `
      <div class="audiorow">
        <button class="playkey"${off} aria-label="Play just the key moments in the YouTube player">⏭ Play key moments</button>
        <button class="listen"${off} aria-label="Generate spoken audio of this summary">🔊 Listen to summary</button>
        <button class="covertoggle"${off} aria-pressed="false"><span class="covercheck" aria-hidden="true"></span>Cover listed words</button>
        <select class="voice"${off} aria-label="Voice"></select>
        <button class="vpreview"${off} title="Hear a sample of this voice"
          aria-label="Preview the selected voice">▶ Preview</button>
      </div>
      <div class="audioprogress">
        <svg class="circ" viewBox="0 0 36 36" width="20" height="20" aria-hidden="true">
          <circle class="circ-bg" cx="18" cy="18" r="15.5"></circle>
          <circle class="circ-fg" cx="18" cy="18" r="15.5"></circle>
        </svg>
        <span class="audiostatus" aria-live="polite"></span>
      </div>
      <div class="coverwrap">
        <div class="coverstatus"></div>
        <div class="coverlive sr" role="status" aria-live="polite"></div>
      </div>
      <div class="audioslot"></div>`;
  }

  // --- The summary as it's written ----------------------------------------------
  //
  // The model spends ~35s generating after the first token arrives; the server now
  // forwards each key point and paragraph as it's finished, so reading can start
  // within seconds instead of waiting on the whole thing.
  function showPartial() {
    const st = partialState;
    if (!st || !st.meta) return;
    if (!host) mount();
    // The loading view's animation timers only; the keepalive ping and the safety
    // timeout belong to the request, which is still running.
    if (stageTimer) { clearTimeout(stageTimer); stageTimer = null; }
    if (creepTimer) { clearInterval(creepTimer); creepTimer = null; }
    const m = st.meta;
    root.querySelector(".content").innerHTML = `
      <div class="meta">${esc(m.channel)} · ${esc(m.original_length)} · writing…</div>
      <h1 style="margin-bottom:10px">${esc(m.title)}</h1>
      ${audioRowHtml(true)}
      <h2>Key points</h2><ul class="points">${st.points.map((k) => `<li>${esc(k)}</li>`).join("")}</ul>
      <h2>Summary</h2><div class="body">${st.paragraphs.map(renderSummary).join("")}</div>
      <div class="writing"><span class="spinner"></span>Writing the summary…</div>`;
  }

  // Append rather than re-render, so the page doesn't flicker or lose a selection
  // on every new paragraph.
  function appendPartial(kind, text) {
    if (kind === "key_point") {
      const ul = root.querySelector(".points");
      if (ul) ul.insertAdjacentHTML("beforeend", `<li>${esc(text)}</li>`);
    } else if (kind === "paragraph") {
      const body = root.querySelector(".body");
      if (body) body.insertAdjacentHTML("beforeend", renderSummary(text));
    }
  }

  function onPartial(m) {
    if (m.type === "meta") {
      partialState = { meta: m.meta, points: [], paragraphs: [] };
    } else if (partialState) {
      const list = m.kind === "key_point" ? partialState.points
        : m.kind === "paragraph" ? partialState.paragraphs : null;
      if (!list) return;
      list.push(m.text);
    } else {
      return;
    }
    if (backgrounded) return;                    // panel closed: keep state, draw later
    if (m.type === "partial" && root && root.querySelector(".writing")) {
      appendPartial(m.kind, m.text);
    } else {
      showPartial();
    }
  }

  function showResult(p, cached) {
    if (!host) mount();
    clearTimers();
    lastPayload = p;
    root.querySelector("#tldw-h").textContent = "TL;DW" + (cached ? " (cached)" : "");
    const c = root.querySelector(".content");
    const points = (p.key_points || []).map((k) => `<li>${esc(k)}</li>`).join("");
    c.innerHTML = `
      <div class="meta">${esc(p.channel)} · ${esc(p.original_length)} → ~${esc(p.length_label)} read${esc(trimmedNote())} ·
        <a href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">original</a>${
          transcribedNote(p) ? ` · <span class="txsrc">${esc(transcribedNote(p))}</span>` : ""}</div>
      <h1 style="margin-bottom:10px">${esc(p.title)}</h1>
      ${audioRowHtml(false)}
      ${points ? `<h2>Key points</h2><ul class="points">${points}</ul>` : ""}
      <h2>Summary</h2><div class="body">${renderSummary(p.summary_md || "")}</div>
      ${p.rationale ? `<div class="rationale">${esc(p.rationale)}</div>` : ""}
      <div class="sponsor" hidden></div>
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
    const again = root.querySelector(".regen");
    if (again) {
      again.hidden = false;
      again.onclick = () => {
        if (busy || requestActive || !lastPayload) return;
        // Deliberately keeps the cached transcript: this re-runs the model, it
        // doesn't re-download the video.
        startSummarize(lastPayload.source_url, lastPayload.video_id, true);
      };
    }
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
    maybeThank(p.stats);
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
    setupCover();
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
        enableDownload(m.dataUrl);             // the mp3 is complete now
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

  // --- One thank-you, at a milestone that means something ------------------------
  //
  // Deliberately not a nag: it needs real mileage behind it, it's dismissible for
  // good, and it retires itself after a few showings even if never dismissed. This
  // is a tool people use daily — interrupting that repeatedly would cost more
  // goodwill than the ask could ever be worth.
  function maybeThank(stats) {
    const box = root && root.querySelector(".sponsor");
    if (!box || !stats || (stats.videos || 0) < SPONSOR_AFTER_VIDEOS) return;
    api.storage.local.get({ sponsorDismissed: false, sponsorShown: 0 }).then((s) => {
      if (s.sponsorDismissed || s.sponsorShown >= SPONSOR_MAX_SHOWS) return;
      const hours = Number(stats.saved_hours || 0);
      const hoursText = hours >= 1
        ? `about ${hours % 1 === 0 ? hours : hours.toFixed(1)} hours`
        : "a good chunk of time";
      box.innerHTML = `
        <div>You've TL;DW'd <strong>${esc(String(stats.videos))} videos</strong> —
          ${esc(hoursText)} you didn't have to watch.</div>
        <div class="srow">
          <a class="sbtn" href="${esc(SPONSOR_URL)}" target="_blank"
             rel="noopener noreferrer">💛 Sponsor on GitHub</a>
          <button class="sdismiss">No thanks — don't ask again</button>
        </div>`;
      box.hidden = false;
      box.querySelector(".sdismiss").onclick = () => {
        box.hidden = true;
        api.storage.local.set({ sponsorDismissed: true }).catch(() => {});
      };
      // Following the link is as good as dismissing it.
      box.querySelector(".sbtn").onclick = () => {
        api.storage.local.set({ sponsorDismissed: true }).catch(() => {});
      };
      api.storage.local.set({ sponsorShown: (s.sponsorShown || 0) + 1 }).catch(() => {});
    }).catch(() => {});
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

  // --- T-020 cover listed words: pure logic (BEGIN) ------------------------------
  // Everything between BEGIN and END is pure (no DOM, no api, no timers), so
  // tests/js/mute_checks.js can lift it out and run it in node. All times are
  // media milliseconds; currentTime × 1000 is converted once, at the edge.
  const MUTE_LINE_LEAD_MS = 200, MUTE_LINE_TAIL_MS = 1000, MUTE_LINE_CAP_MS = 8000;
  const MUTE_WIN_LEAD_MS = 1000, MUTE_WIN_TAIL_MS = 500, MUTE_WIN_MAX_MS = 12000;
  const MUTE_WIN_MAX_COUNT = 200;
  const MUTE_WORD_LEAD_MS = 150, MUTE_WORD_TAIL_MS = 100;
  const MUTE_WORD_MIN_MS = 300, MUTE_WORD_MAX_MS = 2000;
  const MUTE_SOUND_CAP_MS = 1500;
  const MUTE_MARKER_SRC = "\\[\\s*_+\\s*\\]";
  const MUTE_MASKED_RE = /^\p{L}?\*+\p{L}*$/u;
  const MUTE_MARKER_WORD_RE = /^\[?_+\]?$/u;

  // Absent (never set) gives the shipped default; "" is off; else the user's list.
  function resolveMuteList(stored, defaults, normalise) {
    if (typeof normalise !== "function") return [];
    if (stored === undefined || stored === null || typeof stored !== "string") {
      return Array.isArray(defaults) ? normalise(defaults.join("\n")) : [];
    }
    return normalise(stored);
  }

  // One case-insensitive, whole-word alternation (Unicode boundaries), plus
  // YouTube's [ __ ] marker, or null for an empty list (which means off).
  function muteWordsPattern(list) {
    if (!Array.isArray(list)) return null;
    const words = [...new Set(list.map((w) => String(w).trim().toLowerCase())
      .filter((w) => w && w.length <= 64))].slice(0, 200);
    if (!words.length) return null;
    const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
    return new RegExp(
      `(?<![\\p{L}\\p{N}])(?:${esc.join("|")})(?![\\p{L}\\p{N}])|${MUTE_MARKER_SRC}`,
      "giu");
  }

  function muteMatches(text, pattern) {
    if (!pattern) return [];
    return String(text || "").match(pattern) || [];
  }

  // Merge overlapping or touching ranges; a merged range is "line" if any part is.
  function mergeMuteRanges(list) {
    const sorted = list.filter((r) => r.e > r.s).sort((a, b) => a.s - b.s || a.e - b.e);
    const out = [];
    for (const r of sorted) {
      const last = out[out.length - 1];
      if (last && r.s <= last.e) {
        last.e = Math.max(last.e, r.e);
        for (const h of r.hits) if (!last.hits.includes(h)) last.hits.push(h);
        if (r.timed === "line") last.timed = "line";
      } else {
        out.push({ s: r.s, e: r.e, hits: r.hits.slice(), timed: r.timed });
      }
    }
    return out;
  }

  // The active ranges: each hit's word ranges once applied, else its line range.
  function buildMuteRanges(hits) {
    const parts = [];
    for (const h of hits) {
      if (h.applied && h.applied.length) {
        for (const [s, e] of h.applied) parts.push({ s, e, hits: [h.id], timed: "word" });
      } else {
        parts.push({ s: h.line[0], e: h.line[1], hits: [h.id], timed: "line" });
      }
    }
    return mergeMuteRanges(parts);
  }

  function muteTotals(hits, ranges) {
    return {
      n: hits.length,
      k: hits.filter((h) => h.applied && h.applied.length).length,
      totalMs: ranges.reduce((t, r) => t + (r.e - r.s), 0),
    };
  }

  // Caption cues → hits. Each hit starts as a caption-line range.
  function muteRanges(cues, pattern, durationMs) {
    const dur = Number.isFinite(durationMs) && durationMs > 0 ? Math.floor(durationMs) : null;
    const rows = (Array.isArray(cues) ? cues : [])
      .filter((c) => c && typeof c.text === "string" && c.text.trim()
        && Number.isFinite(c.start_ms))
      .map((c) => ({ start: Math.max(0, Math.floor(c.start_ms)), text: c.text }))
      .sort((a, b) => a.start - b.start);
    const hits = [];
    if (pattern) {
      for (let i = 0; i < rows.length; i++) {
        const m = muteMatches(rows[i].text, pattern);
        if (!m.length) continue;
        const cueStart = rows[i].start;
        let next = null;
        for (let j = i + 1; j < rows.length; j++) {
          if (rows[j].start > cueStart) { next = rows[j].start; break; }
        }
        const cap = cueStart + MUTE_LINE_CAP_MS;
        let end = next !== null ? Math.min(next + MUTE_LINE_TAIL_MS, cap) : cap;
        if (dur !== null) end = Math.min(end, dur);
        const start = Math.max(0, cueStart - MUTE_LINE_LEAD_MS);
        if (end <= start) continue;
        let cueEnd = next !== null ? next : cap;
        if (dur !== null) cueEnd = Math.min(cueEnd, dur);
        const markers = m.filter((x) => x.charAt(0) === "[").length;
        hits.push({ id: hits.length, cueStart, cueEnd, kind: markers ? "marker" : "word",
          count: m.length, line: [start, end], words: null, applied: null });
      }
    }
    const ranges = buildMuteRanges(hits);
    return { hits, ranges, ...muteTotals(hits, ranges) };
  }

  // Time windows for Whisper, one per hit, joined while ≤ 12 s, ordered so the
  // ones still ahead of the playhead go first. Every value is a whole number.
  function alignWindows(hits, nowMs, durationMs) {
    const dur = Number.isFinite(durationMs) && durationMs > 0 ? Math.floor(durationMs) : null;
    const clamp = (v) => Math.max(0, dur !== null ? Math.min(v, dur) : v);
    const joined = [];
    for (const h of [...hits].sort((a, b) => a.cueStart - b.cueStart)) {
      const s = clamp(h.cueStart - MUTE_WIN_LEAD_MS);
      let e = clamp(h.cueEnd + MUTE_WIN_TAIL_MS);
      e = Math.min(e, s + MUTE_WIN_MAX_MS);
      if (e <= s) continue;
      const last = joined[joined.length - 1];
      if (last && s <= last.end_ms && Math.max(last.end_ms, e) - last.start_ms <= MUTE_WIN_MAX_MS) {
        last.end_ms = Math.max(last.end_ms, e);
        last.hits.push(h.id);
      } else {
        joined.push({ start_ms: s, end_ms: e, hits: [h.id] });
      }
    }
    const now = Number.isFinite(nowMs) ? nowMs : 0;
    const ahead = joined.filter((w) => w.end_ms >= now);
    const behind = joined.filter((w) => w.end_ms < now);
    return ahead.concat(behind).slice(0, MUTE_WIN_MAX_COUNT);
  }

  function normMuteWord(w) {
    return String(w ?? "").normalize("NFC").trim()
      .replace(/^[^\p{L}\p{N}$@*_]+|[^\p{L}\p{N}$@*_]+$/gu, "");
  }

  // Give each Whisper word in a window to the hit it most likely belongs to: the
  // latest-starting hit in that window whose span (with the window's padding)
  // holds the word's start.
  function assignWindowWords(win, hits, words) {
    const own = hits.filter((h) => win.hits.includes(h.id))
      .sort((a, b) => a.cueStart - b.cueStart);
    const byHit = new Map(own.map((h) => [h.id, []]));
    for (const it of Array.isArray(words) ? words : []) {
      if (!it || !Number.isInteger(it.s)) continue;
      let pick = null;
      for (const h of own) {
        if (it.s >= h.cueStart - MUTE_WIN_LEAD_MS && it.s < h.cueEnd + MUTE_WIN_TAIL_MS) pick = h;
      }
      if (pick) byHit.get(pick.id).push(it);
    }
    return byHit;
  }

  // A hit's word ranges from Whisper's words, or [] to keep the line range. It
  // only refines when Whisper found at least as many listed words as the line has.
  function refineHit(hit, windowWords, pattern, win) {
    const ranges = [];
    for (const it of Array.isArray(windowWords) ? windowWords : []) {
      if (!it || !Number.isInteger(it.s) || !Number.isInteger(it.e) || it.e < it.s) continue;
      const w = normMuteWord(it.w);
      if (!w) continue;
      const listed = muteMatches(w, pattern).length > 0;
      const masked = hit.kind === "marker"
        && (MUTE_MASKED_RE.test(w) || MUTE_MARKER_WORD_RE.test(w));
      if (!listed && !masked) continue;
      if (win && (it.s < win.start_ms || it.e > win.end_ms)) continue;
      if (it.e - it.s >= MUTE_WORD_MAX_MS) continue;
      const s = Math.max(0, it.s - MUTE_WORD_LEAD_MS);
      let e = it.e + MUTE_WORD_TAIL_MS;
      if (e - s < MUTE_WORD_MIN_MS) e = s + MUTE_WORD_MIN_MS;
      ranges.push([s, e]);
    }
    return ranges.length >= hit.count ? ranges : [];
  }

  function rangeAt(ranges, nowMs) {
    for (const r of ranges) if (nowMs >= r.s && nowMs < r.e) return r;
    return null;
  }

  // Apply a hit's refinement, or queue it while the playhead is inside the merged
  // range that holds the hit, so a range never shrinks under the listener.
  function applyRefinement(state, hitId, wordRanges, nowMs) {
    const hit = state.hits.find((h) => h.id === hitId);
    if (!hit) return state;
    hit.words = wordRanges.slice();
    if (!wordRanges.length) return state;
    const cur = rangeAt(state.ranges, nowMs);
    if (cur && cur.hits.includes(hitId)) {
      state.pending = (state.pending || []).filter((p) => p.hitId !== hitId);
      state.pending.push({ hitId, ranges: wordRanges.slice() });
      return state;
    }
    hit.applied = wordRanges.slice();
    state.ranges = buildMuteRanges(state.hits);
    return state;
  }

  // Apply queued refinements whose range the playhead has left.
  function flushRefinements(state, nowMs) {
    if (!state.pending || !state.pending.length) return state;
    const cur = rangeAt(state.ranges, nowMs);
    const keep = [];
    let changed = false;
    for (const p of state.pending) {
      if (cur && cur.hits.includes(p.hitId)) { keep.push(p); continue; }
      const hit = state.hits.find((h) => h.id === p.hitId);
      if (hit) { hit.applied = p.ranges; changed = true; }
    }
    state.pending = keep;
    if (changed) state.ranges = buildMuteRanges(state.hits);
    return state;
  }

  // One decision per tick. input: {nowMs, videoMuted, videoVolume, videoId,
  // adShowing, paused, userSignal}. The feature only ever unmutes a mute it set.
  function muteStep(state, input) {
    const st = { ...state };
    const out = (action, sound) => ({ action, sound, state: st });
    if (input.videoId !== st.videoId) {
      const had = st.soundOn;
      st.soundOn = false;
      return out("teardown", had ? "stop" : "none");
    }
    let action = "none";
    // The user's own mute or unmute wins: a muted value we didn't set, or a
    // mute-button click / m key, hands control back.
    if (st.owned && (input.userSignal === "release" || input.videoMuted !== st.lastSet)) {
      st.owned = false;
      st.released = true;
    }
    if (input.adShowing) {
      if (st.owned) { action = "exit"; st.owned = false; }
      st.cur = null;
      st.quietCur = null;
      st.released = false;
      const had = st.soundOn;
      st.soundOn = false;
      return out(action, had ? "stop" : "none");
    }
    const r = rangeAt(st.ranges, input.nowMs);
    const same = r && st.cur && r.s === st.cur.s && r.e === st.cur.e;
    if (!same) {
      if (st.cur && st.owned) { action = "exit"; st.owned = false; }
      st.released = false;
      st.quietCur = null;     // a new range plays the (new) sound again
      st.cur = r ? { s: r.s, e: r.e } : null;
      if (r) {
        // Already muted by the user: hands off. A mute we held for the range we
        // just left is ours, so a seek from one range into another keeps it.
        if (input.videoMuted && action !== "exit") {
          st.owned = false;
        } else {
          action = "enter";
          st.owned = true;
          st.lastSet = true;
        }
      }
    }
    const want = !!(st.cur && st.owned && st.mode !== "mute" && !st.soundBlocked
      && !st.quietCur && !input.paused && input.videoVolume > 0
      && input.nowMs < st.cur.s + MUTE_SOUND_CAP_MS);
    let sound = "none";
    if (want && !st.soundOn) sound = "start";
    else if (!want && st.soundOn) sound = "stop";
    st.soundOn = want;
    return out(action, sound);
  }

  // A live sound switch (rev 5.6): the new mode, no sound now, and the range in
  // progress, if any, stays silent until the playhead leaves it.
  function muteSwitchSound(state, mode) {
    return { ...state, mode, soundOn: false,
      quietCur: state.cur ? { s: state.cur.s, e: state.cur.e } : null };
  }

  // Wall-clock ms to the next edge (a range start or end, or the sound cap), or
  // null when paused, stopped, or more than a second away.
  function nextBoundaryDelay(state, nowMs, playbackRate, paused) {
    if (paused || !(playbackRate > 0)) return null;
    let edge = Infinity;
    for (const r of state.ranges) {
      if (r.s > nowMs && r.s < edge) edge = r.s;
      if (r.e > nowMs && r.e < edge) edge = r.e;
    }
    if (state.soundOn && state.cur) {
      const cap = state.cur.s + MUTE_SOUND_CAP_MS;
      if (cap > nowMs && cap < edge) edge = cap;
    }
    if (!Number.isFinite(edge)) return null;
    const delay = (edge - nowMs) / playbackRate;
    return delay > 1000 ? null : Math.max(0, delay);
  }

  // How long a sound should play, in real ms, for a range entered at nowMs.
  function soundLengthMs(mode, range, nowMs, playbackRate) {
    const rate = playbackRate > 0 ? playbackRate : 1;
    const mediaMs = Math.min(range.e - nowMs, range.s + MUTE_SOUND_CAP_MS - nowMs);
    if (!(mediaMs > 0)) return 0;
    const realMs = mediaMs / rate;
    if (mode === "moo") return Math.max(250, Math.min(realMs, MUTE_SOUND_CAP_MS));
    return realMs;
  }

  // A sound as data: nodes, connections and automation events (seconds from start).
  // playPlan turns it into Web Audio; nothing here touches the API.
  function soundPlan(mode, lengthMs, volume) {
    const L = Math.max(0, lengthMs) / 1000;
    const vol = Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : 1;
    if (mode === "bleep") {
      return {
        mode, length: L, level: 0.15, volume: 0.15 * vol,
        nodes: [{ id: "osc", kind: "osc", wave: "sine", freq: 1000 }],
        chain: ["osc"],
        events: [
          { node: "env", param: "gain", kind: "set", value: 0, at: 0 },
          { node: "env", param: "gain", kind: "linear", value: 1, at: Math.min(0.005, L) },
          { node: "env", param: "gain", kind: "set", value: 1, at: Math.max(0, L - 0.01) },
          { node: "env", param: "gain", kind: "linear", value: 0, at: L },
        ],
      };
    }
    if (mode === "moo") {
      // Rev 5.5: a voiced "mm-OO-oo". A sawtooth with the rumble cut (hp), two
      // formants (f1 glides, f2 closes toward "oo") and a mouth that opens and
      // closes (lp). Lowpass/highpass Q is in dB in Web Audio: -3.01 is flat.
      const atk = Math.min(0.1, 0.25 * L), rel = Math.min(0.25, 0.35 * L);
      return {
        mode, length: L, level: 0.25, volume: 0.25 * vol,
        nodes: [
          { id: "osc", kind: "osc", wave: "sawtooth", freq: 120 },
          { id: "lfo", kind: "osc", wave: "sine", freq: 4.5, modulates: "osc", depth: 2 },
          { id: "hp", kind: "filter", wave: "highpass", q: -3.01, freq: 120 },
          { id: "f1", kind: "filter", wave: "peaking", q: 3, gain: 9, freq: 300 },
          { id: "f2", kind: "filter", wave: "peaking", q: 4, gain: 5, freq: 1100 },
          { id: "lp", kind: "filter", wave: "lowpass", q: -3.01, freq: 450 },
        ],
        chain: ["osc", "hp", "f1", "f2", "lp"],
        events: [
          { node: "osc", param: "frequency", kind: "set", value: 120, at: 0 },
          { node: "osc", param: "frequency", kind: "exp", value: 155, at: 0.3 * L },
          { node: "osc", param: "frequency", kind: "exp", value: 110, at: L },
          { node: "f1", param: "frequency", kind: "set", value: 300, at: 0 },
          { node: "f1", param: "frequency", kind: "exp", value: 650, at: 0.35 * L },
          { node: "f1", param: "frequency", kind: "exp", value: 450, at: L },
          { node: "f2", param: "frequency", kind: "set", value: 1100, at: 0 },
          { node: "f2", param: "frequency", kind: "exp", value: 800, at: L },
          { node: "lp", param: "frequency", kind: "set", value: 450, at: 0 },
          { node: "lp", param: "frequency", kind: "exp", value: 1600, at: 0.3 * L },
          { node: "lp", param: "frequency", kind: "exp", value: 900, at: L },
          { node: "env", param: "gain", kind: "set", value: 0, at: 0 },
          { node: "env", param: "gain", kind: "linear", value: 1, at: atk },
          { node: "env", param: "gain", kind: "set", value: 1, at: Math.max(atk, L - rel) },
          { node: "env", param: "gain", kind: "linear", value: 0, at: L },
        ],
      };
    }
    return null;   // mute mode plays nothing
  }
  // --- T-020 cover listed words: pure logic (END) --------------------------------

  // --- T-020 cover listed words: wiring (BEGIN) ----------------------------------
  // The session that runs while "Cover listed words" is on. Everything it touches
  // in the page or the browser arrives through `deps`, so
  // tests/js/mute_wiring_checks.js can drive the real code with fakes. It belongs
  // to the page, not the panel: closing the panel leaves it running.
  const MUTE_SOUNDS = ["bleep", "mute", "moo"];
  const MUTE_VERB = { bleep: "Bleeping", mute: "Muting", moo: "Mooing over" };
  const MUTE_PILL_EMOJI = { bleep: "🔔", mute: "🔇", moo: "🐄" };
  const MUTE_STOP_NAME = { bleep: "Stop bleeping", mute: "Stop muting", moo: "Stop mooing" };
  const MUTE_INSTALL_HINT = "pipx inject youtube-tldw faster-whisper";
  const MUTE_PING_MS = 20000;
  const MUTE_RETRY_MS = [5000, 10000, 20000];   // a rebuild's pre-download busy (rev 5.6)
  const MUTE_SAFETY_MS = 660000;         // the server's 600 s budget plus a minute
  const MUTE_PILL_GONE_MS = 1000;        // Stop on the pill: keep its live region this long
  const MUTE_ANNOUNCE_DELAY_MS = 100;    // a live region filled on a later tick
  // T-020 status text: BEGIN
  // Every string here is recorded verbatim in docs/contracts.md. {verb} is
  // Bleeping / Muting / Mooing over; {n} is "1 spot" or "N spots"; {s} is whole
  // seconds of covered audio; {k} and {total} count hits; {detail} and {hint} are
  // the server's own text.
  const MUTE_TEXT = {
    loading: "Reading the captions…",
    emptyList: "No words are listed. Add some in TL;DW's extension options, then try again.",
    noMatches: "None of this video's caption lines contain your listed words, so there's nothing to cover.",
    soundChanged: "Sound changed: now {verbLower} listed words, starting at the next covered spot.",
    listUpdated: "Word list updated: {verb} {n} from the captions (about {s} seconds of audio).",
    liveEmptyList: "No words are listed, so covering is off. Add some in TL;DW's extension options, then turn it on again.",
    liveNoMatches: "None of this video's caption lines contain your listed words, so covering is off.",
    on: "{verb} {n} from the captions (about {s} seconds of audio). Timing them to the word…",
    progress: "Timed to the word: {k} of {total}.",
    doneAll: "{verb} {n}, all timed to the word (about {s} seconds of audio).",
    donePart: "{verb} {n}: {k} timed to the word, the rest by caption line (about {s} seconds of audio).",
    doneNone: "{verb} {n} by caption line: Whisper didn't find the words in the audio.",
    whisperMissing: "{verb} {n} by caption line. To time them to the word, install Whisper for tldw serve: {hint}, then restart tldw serve and turn this off and on again.",
    whisperOff: "{verb} {n} by caption line, because Whisper is turned off. To time them to the word, set 'Videos with no subtitles' in TL;DW's options to either transcribe choice.",
    modelMissing: "{verb} {n} by caption line: Whisper's model isn't downloaded yet. Transcribe one video that has no subtitles to fetch it, then turn this off and on again.",
    busySuffix: " Until then, spots are covered by caption line.",
    olderServer: "{verb} {n} by caption line: this tldw serve can't time words. Update tldw, then restart tldw serve and turn this off and on again.",
    unreachable: "{verb} {n} by caption line: couldn't reach tldw serve. Start it, then turn this off and on again.",
    token: "{verb} {n} by caption line: TL;DW's token for tldw serve is missing or wrong. Paste the token tldw serve prints into TL;DW's options, then turn this off and on again.",
    detail: "{verb} {n} by caption line: {detail}",
    other: "{verb} {n} by caption line: timing them to the word failed. Turn this off and on again to retry.",
    soundBlocked: "The browser blocked the sound, so listed words are muted instead. Turn this off and on again to allow sound.",
    offBleep: "Bleeping is off.",
    offMute: "Muting is off.",
    offMoo: "Mooing is off.",
    noTranscript: "Couldn't read this video's captions, so no words will be covered. Open the video's transcript and try again, or try another video.",
    noPlayer: "Couldn't find the YouTube player. Reload the page, then try again.",
    live: "Covering words isn't available on live streams. Try again after the stream ends.",
    ad: "An ad is playing. Try again once the video starts.",
    adPaused: "An ad is playing, so covering is paused. It picks up again when the video comes back.",
    pillBleep: "Bleeping listed words",
    pillMute: "Muting listed words",
    pillMoo: "Mooing over listed words",
    pillNow: " · now",
  };
  // T-020 status text: END
  // The server's text for a pinned-off Whisper differs from its plain "off"; only
  // the plain one gets our longer wording.
  const MUTE_SERVER_OFF_TEXT = "Whisper is turned off.";

  const muteCap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  function muteFill(template, vars) {
    return template.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  }

  function muteSpots(n) { return n === 1 ? "1 spot" : n + " spots"; }

  function muteVars(mode, totals) {
    const verb = MUTE_VERB[mode] || MUTE_VERB.bleep;
    return { verb, verbLower: verb.toLowerCase(), n: muteSpots(totals.n),
      s: Math.max(1, Math.round(totals.totalMs / 1000)), k: totals.k, total: totals.n };
  }

  // The status for a failed word-timing request, picked by `reason` and then by
  // `status`. Server-supplied text is passed through as plain text, never markup.
  function alignFailureText(m, mode, totals) {
    const v = muteVars(mode, totals);
    const detail = typeof m.error === "string" ? m.error.trim() : "";
    const withDetail = () => muteFill(detail ? MUTE_TEXT.detail : MUTE_TEXT.other, { ...v, detail });
    const reason = m.reason || null;
    if (reason === "whisper_off") {
      return detail && detail !== MUTE_SERVER_OFF_TEXT ? withDetail()
        : muteFill(MUTE_TEXT.whisperOff, v);
    }
    if (reason === "whisper_missing") {
      return muteFill(MUTE_TEXT.whisperMissing, { ...v, hint: m.installHint || MUTE_INSTALL_HINT });
    }
    if (reason === "model_missing") return muteFill(MUTE_TEXT.modelMissing, v);
    if (reason === "busy" || reason === "rate_limited") {
      return (detail || muteFill(MUTE_TEXT.other, v)) + MUTE_TEXT.busySuffix;
    }
    if (reason === "unreachable") return muteFill(MUTE_TEXT.unreachable, v);
    if (reason === "token") return muteFill(MUTE_TEXT.token, v);
    if (reason) return withDetail();   // ffmpeg_missing, too_long, download_failed, timeout, other
    if (m.status === 401) return muteFill(MUTE_TEXT.token, v);
    if (m.status === 404) return muteFill(MUTE_TEXT.olderServer, v);
    if (m.status === 429) return (detail || muteFill(MUTE_TEXT.other, v)) + MUTE_TEXT.busySuffix;
    if (m.status === 503 && m.installHint) {
      return muteFill(MUTE_TEXT.whisperMissing, { ...v, hint: m.installHint });
    }
    return withDetail();
  }

  // The status once the request finished: every hit timed, some, or none.
  function alignDoneText(mode, totals, failed, windows) {
    const v = muteVars(mode, totals);
    if (totals.k >= totals.n) return muteFill(MUTE_TEXT.doneAll, v);
    if (totals.k > 0) return muteFill(MUTE_TEXT.donePart, v);
    if (windows > 0 && failed >= windows) return muteFill(MUTE_TEXT.other, v);
    return muteFill(MUTE_TEXT.doneNone, v);
  }

  function muteOffText(mode) {
    return MUTE_TEXT["off" + muteCap(mode)] || MUTE_TEXT.offBleep;
  }

  // Web Audio for a soundPlan: source(s) -> envelope gain -> volume gain -> out.
  // A volume change touches only the volume gain. stop() is safe to call twice.
  function playPlan(ctx, plan) {
    const t0 = ctx.currentTime;
    const env = ctx.createGain();
    const vol = ctx.createGain();
    vol.gain.value = plan.volume;
    const nodes = { env };
    for (const n of plan.nodes) {
      if (n.kind === "osc") {
        const o = ctx.createOscillator();
        o.type = n.wave;
        o.frequency.value = n.freq;
        nodes[n.id] = o;
      } else if (n.kind === "filter") {
        const f = ctx.createBiquadFilter();
        f.type = n.wave;
        f.Q.value = n.q;
        f.frequency.value = n.freq;
        if (Number.isFinite(n.gain)) f.gain.value = n.gain;
        nodes[n.id] = f;
      }
    }
    const extra = [];
    for (const n of plan.nodes) {
      if (!n.modulates) continue;
      const depth = ctx.createGain();
      depth.gain.value = n.depth;
      nodes[n.id].connect(depth);
      depth.connect(nodes[n.modulates].frequency);
      extra.push(depth);
    }
    let prev = null;
    for (const id of plan.chain) {
      if (prev) prev.connect(nodes[id]);
      prev = nodes[id];
    }
    prev.connect(env);
    env.connect(vol);
    vol.connect(ctx.destination);
    for (const ev of plan.events) {
      const p = nodes[ev.node][ev.param];
      const at = t0 + ev.at;
      if (ev.kind === "set") p.setValueAtTime(ev.value, at);
      else if (ev.kind === "linear") p.linearRampToValueAtTime(ev.value, at);
      else if (ev.kind === "exp") p.exponentialRampToValueAtTime(ev.value, at);
    }
    const oscs = plan.nodes.filter((n) => n.kind === "osc").map((n) => nodes[n.id]);
    const all = [...Object.values(nodes), ...extra, vol];
    let ended = false, stopped = false;
    const disconnect = () => {
      if (ended) return;
      ended = true;
      for (const n of all) { try { n.disconnect(); } catch (_) {} }
    };
    for (const o of oscs) { o.start(t0); o.stop(t0 + plan.length + 0.02); }
    oscs[0].onended = disconnect;
    return {
      get ended() { return ended; },
      setVolume(v) {
        const x = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 1;
        try { vol.gain.setValueAtTime(plan.level * x, ctx.currentTime); } catch (_) {}
      },
      stop() {
        if (stopped || ended) return;
        stopped = true;
        const t = ctx.currentTime;
        const g = env.gain;
        try {
          g.cancelScheduledValues(t);
          g.setValueAtTime(g.value, t);
          g.linearRampToValueAtTime(0, t + 0.01);
        } catch (_) {}
        for (const o of oscs) { try { o.stop(t + 0.012); } catch (_) {} }
      },
    };
  }

  // The pill on the player while covering is on. Its own shadow root, so YouTube's
  // styles can't reach the Stop button; built with textContent only.
  function buildMutePill(doc, mode, onStop) {
    const host = doc.createElement("div");
    host.id = "tldw-mute-pill";
    host.style.cssText = "position:fixed;z-index:2147483647;bottom:136px;left:50%;"
      + "transform:translateX(-50%);";
    const shadow = host.attachShadow({ mode: "open" });
    const style = doc.createElement("style");
    style.textContent = ".pill{display:flex;align-items:center;gap:10px;background:#1e1f24;"
      + "color:#e9e9ea;font:14px system-ui,-apple-system,sans-serif;padding:4px 6px 4px 14px;"
      + "border-radius:999px;box-shadow:0 6px 24px rgba(0,0,0,.45);}"
      + ".stop{font:inherit;min-width:44px;min-height:44px;padding:0 14px;cursor:pointer;"
      + "border-radius:999px;border:1px solid rgba(255,255,255,.35);background:#333;color:#fff;}"
      + ".stop:focus-visible{outline:3px solid #8ab4f8;outline-offset:2px;}"
      + ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);"
      + "white-space:nowrap;}";
    const box = doc.createElement("div");
    box.className = "pill";
    const emoji = doc.createElement("span");
    emoji.setAttribute("aria-hidden", "true");
    emoji.textContent = MUTE_PILL_EMOJI[mode] || MUTE_PILL_EMOJI.bleep;
    const label = doc.createElement("span");
    label.className = "label";
    let base = MUTE_TEXT["pill" + muteCap(mode)] || MUTE_TEXT.pillBleep;
    let now = false;
    label.textContent = base;
    const stop = doc.createElement("button");
    stop.type = "button";
    stop.className = "stop";
    stop.textContent = "Stop";
    stop.setAttribute("aria-label", MUTE_STOP_NAME[mode] || MUTE_STOP_NAME.bleep);
    stop.addEventListener("click", onStop);
    const live = doc.createElement("div");
    live.className = "sr";
    live.setAttribute("role", "status");
    live.setAttribute("aria-live", "polite");
    box.appendChild(emoji);
    box.appendChild(label);
    box.appendChild(stop);
    box.appendChild(live);
    shadow.appendChild(style);
    shadow.appendChild(box);
    return { host, label, live, stop,
      setNow(on) { now = !!on; label.textContent = base + (now ? MUTE_TEXT.pillNow : ""); },
      // A live sound switch (rev 5.6): emoji, label and the Stop button's name.
      setMode(m) {
        emoji.textContent = MUTE_PILL_EMOJI[m] || MUTE_PILL_EMOJI.bleep;
        base = MUTE_TEXT["pill" + muteCap(m)] || MUTE_TEXT.pillBleep;
        stop.setAttribute("aria-label", MUTE_STOP_NAME[m] || MUTE_STOP_NAME.bleep);
        label.textContent = base + (now ? MUTE_TEXT.pillNow : "");
      } };
  }

  // deps: { doc, win, findVideo(), videoId(), pageUrl(), readTranscript(),
  //   getWords(), getSound(), connect(), AudioCtx, defaults(), normalise(), ui(),
  //   setTimeout, clearTimeout, setInterval, clearInterval }
  // ui() returns the open panel's {status(text), announce(text), pressed(on),
  //   busy(on)} or null while the panel is closed.
  function createMuteCover(deps) {
    let S = null;              // the live session, or null
    let lastStatus = "";

    const ui = () => { try { return deps.ui(); } catch (_) { return null; } };

    function say(sess, text, announce) {
      // A status that lands during an ad is kept for after it; the line keeps
      // saying covering is paused until the ad ends.
      if (sess && sess.adSaid && text !== MUTE_TEXT.adPaused) {
        sess.beforeAd = text;
        return;
      }
      lastStatus = text;
      if (sess) sess.status = text;
      const panel = ui();
      if (panel) {
        panel.status(text);
        if (announce) panel.announce(text);
      } else if (announce && sess && sess.pill) {
        const live = sess.pill.live;
        live.textContent = "";
        deps.setTimeout(() => { live.textContent = text; }, MUTE_ANNOUNCE_DELAY_MS);
      }
    }

    function isAd() {
      const player = deps.doc.querySelector("#movie_player");
      return !!(player && player.matches && player.matches(".ad-showing"));
    }

    function durMs(video) {
      const d = video.duration;
      return Number.isFinite(d) ? Math.floor(d * 1000) : null;
    }

    function totals(sess) {
      const t = muteTotals(sess.st.hits, sess.st.ranges);
      t.k = sess.st.hits.filter((h) => h.words && h.words.length).length;
      return t;
    }

    function closeCtx(sess) {
      if (sess.ctx) { try { sess.ctx.close(); } catch (_) {} sess.ctx = null; }
    }

    // A context for bleep or moo. At toggle-on this runs inside the click; on a
    // live switch from mute (rev 5.6) it runs without a gesture, which the page's
    // sticky activation usually allows. One that never runs falls through to
    // startSound's muted-only path. A fresh context clears the old failure flags.
    function openCtx(sess) {
      if (sess.ctx || !deps.AudioCtx) return;
      try {
        const ctx = new deps.AudioCtx();
        sess.ctx = ctx;
        sess.resumeFailed = false;
        sess.blockedSaid = false;
        if (sess.st) sess.st.soundBlocked = false;
        const p = ctx.resume && ctx.resume();
        if (p && typeof p.catch === "function") {
          p.catch(() => { if (sess.ctx === ctx) sess.resumeFailed = true; });
        }
      } catch (_) { sess.ctx = null; }
    }

    function clearRetry(sess) {
      if (sess.retryTimer) { deps.clearTimeout(sess.retryTimer); sess.retryTimer = null; }
    }

    // --- sound -----------------------------------------------------------------
    function startSound(sess, nowMs) {
      const ctx = sess.ctx;
      if (!ctx || ctx.state !== "running" || sess.resumeFailed) {
        sess.st.soundBlocked = true;
        sess.st.soundOn = false;
        if (!sess.blockedSaid) {
          sess.blockedSaid = true;
          say(sess, MUTE_TEXT.soundBlocked, true);
        }
        return;
      }
      const v = sess.video;
      const len = soundLengthMs(sess.mode, sess.st.cur, nowMs, v.playbackRate);
      const plan = soundPlan(sess.mode, len, v.volume);
      if (!plan || len <= 0) return;
      stopSound(sess);
      try { sess.sound = playPlan(ctx, plan); } catch (_) { sess.sound = null; }
    }

    function stopSound(sess) {
      if (sess.sound) { try { sess.sound.stop(); } catch (_) {} sess.sound = null; }
    }

    // --- the tick ----------------------------------------------------------------
    // sig: null (a plain tick), "release" (the user's mute button or m key),
    // "seek" or "timer" (the boundary timer).
    function tick(sess, sig) {
      if (S !== sess || !sess.active) return;
      const v = sess.video;
      if (!v.isConnected) { stop({ quiet: true }); return; }
      const nowMs = Math.max(0, Math.floor(v.currentTime * 1000));
      const ad = isAd();
      flushRefinements(sess.st, nowMs);
      const r = muteStep(sess.st, { nowMs, videoMuted: !!v.muted, videoVolume: v.volume,
        videoId: deps.videoId(), adShowing: ad, paused: !!v.paused,
        userSignal: sig === "release" ? "release" : null });
      sess.st = r.state;
      if (r.action === "teardown") { stop({ quiet: true }); return; }
      if (r.action === "enter") { try { v.muted = true; } catch (_) {} }
      else if (r.action === "exit") { try { v.muted = false; } catch (_) {} }
      if (r.sound === "start") startSound(sess, nowMs);
      else if (r.sound === "stop") {
        // A moo that reached its range's end rings out (about 250 ms at most);
        // anything the user did, a pause, a seek or an ad cuts it.
        const natural = sess.mode === "moo" && sig !== "release" && sig !== "seek"
          && !v.paused && !ad && !sess.st.released;
        if (natural) sess.sound = null;
        else stopSound(sess);
      }
      // An ad mid-session pauses covering; when it ends, the status from before
      // it comes back on the visible line, unannounced.
      if (ad && !sess.adSaid) {
        sess.beforeAd = sess.status;
        sess.adSaid = true;
        say(sess, MUTE_TEXT.adPaused, true);
      } else if (!ad && sess.adSaid) {
        sess.adSaid = false;
        if (sess.beforeAd) say(sess, sess.beforeAd, false);
        sess.beforeAd = "";
      }
      if (sess.pill) sess.pill.setNow(!!(sess.st.cur && sess.st.owned));
      arm(sess, nowMs, sig === "timer");
    }

    // The boundary timer: fires at the next range edge so a short word isn't
    // missed between timeupdate ticks. Re-armed at most twice for the same edge.
    function arm(sess, nowMs, fromTimer) {
      if (sess.timer) { deps.clearTimeout(sess.timer); sess.timer = null; }
      const v = sess.video;
      const d = nextBoundaryDelay(sess.st, nowMs, v.playbackRate, !!v.paused);
      if (d === null) { sess.rearms = 0; sess.edge = null; return; }
      const edge = Math.round(nowMs + d * v.playbackRate);
      if (fromTimer && sess.edge !== null && Math.abs(edge - sess.edge) <= 1) {
        sess.rearms += 1;
        if (sess.rearms > 2) return;      // timeupdate stays the backstop
      } else {
        sess.rearms = 0;
      }
      sess.edge = edge;
      sess.timer = deps.setTimeout(() => {
        sess.timer = null;
        tick(sess, "timer");
      }, d);
    }

    // --- listeners -----------------------------------------------------------------
    function listen(sess, target, type, fn, capture) {
      target.addEventListener(type, fn, capture ? { capture: true } : false);
      sess.listeners.push([target, type, fn, !!capture]);
    }

    function isEditable(e) {
      // composedPath()[0] sees through shadow roots, as onKey does.
      const t = (e.composedPath && e.composedPath()[0]) || e.target;
      if (!t) return false;
      const tag = String(t.tagName || "").toUpperCase();
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!t.isContentEditable;
    }

    function attach(sess) {
      const v = sess.video;
      const onTick = () => tick(sess, null);
      for (const type of ["timeupdate", "play", "pause"]) {
        listen(sess, v, type, onTick);
      }
      // A new rate changes a sound's real-time length: stop it, and the tick
      // starts it again for the time left under the cap.
      listen(sess, v, "ratechange", () => {
        if (sess.sound) { stopSound(sess); sess.st.soundOn = false; }
        tick(sess, null);
      });
      listen(sess, v, "seeked", () => tick(sess, "seek"));
      listen(sess, v, "volumechange", () => {
        if (sess.sound) sess.sound.setVolume(v.volume);
        tick(sess, null);
      });
      const onReload = () => {
        // A mid-roll ad or a quality change reloads the element: give up our
        // mute and sound now, and only end the session if the video changed.
        if (sess.st.owned) { try { v.muted = false; } catch (_) {} }
        sess.st.owned = false;
        sess.st.cur = null;
        sess.st.soundOn = false;
        stopSound(sess);
        if (sess.timer) { deps.clearTimeout(sess.timer); sess.timer = null; }
        if (deps.videoId() !== sess.videoId || !v.isConnected) stop({ quiet: true });
      };
      listen(sess, v, "emptied", onReload);
      listen(sess, v, "loadstart", onReload);
      listen(sess, deps.doc, "keydown", (e) => {
        if (e.key !== "m" && e.key !== "M") return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (isEditable(e)) return;
        tick(sess, "release");
      }, true);
      listen(sess, deps.doc, "click", (e) => {
        const target = e.target;
        if (target && target.closest && target.closest(".ytp-mute-button")) {
          tick(sess, "release");
        }
      }, true);
      listen(sess, deps.doc, "fullscreenchange", () => {
        if (!sess.pill) return;
        const parent = deps.doc.fullscreenElement || deps.doc.body;
        if (sess.pill.host.parentNode !== parent) parent.appendChild(sess.pill.host);
      });
      listen(sess, deps.win, "pagehide", () => {
        if (sess.st.owned) { try { v.muted = false; } catch (_) {} sess.st.owned = false; }
        stop({ quiet: true });
      });
    }

    // --- word timing ---------------------------------------------------------------
    function endAlignPort(sess) {
      if (sess.ping) { deps.clearInterval(sess.ping); sess.ping = null; }
      if (sess.safety) { deps.clearTimeout(sess.safety); sess.safety = null; }
      const port = sess.port;
      sess.port = null;
      if (port) { try { port.disconnect(); } catch (_) {} }
    }

    function alignFailed(sess, m) {
      sess.alignDone = true;
      endAlignPort(sess);
      // A rebuilt session can find the server still busy with the stream it just
      // ended. Retry that, but only a busy that came before any download began
      // (no progress on this port), so the audio is never fetched twice.
      if (sess.rebuilt && m && m.reason === "busy" && !sess.sawProgress
          && sess.retries < MUTE_RETRY_MS.length) {
        const wait = MUTE_RETRY_MS[sess.retries];
        sess.retries += 1;
        clearRetry(sess);
        sess.retryTimer = deps.setTimeout(() => {
          sess.retryTimer = null;
          if (S === sess && sess.active) startAlign(sess);
        }, wait);
        return;
      }
      say(sess, alignFailureText(m, sess.mode, totals(sess)), true);
    }

    function onAlign(sess, port, m) {
      if (S !== sess || sess.port !== port || deps.videoId() !== sess.videoId) return;
      if (m.type === "alignWindow") {
        const win = sess.windows[m.i];
        if (!win || !Array.isArray(m.words)) return;
        const nowMs = Math.max(0, Math.floor(sess.video.currentTime * 1000));
        if (sess.heard) sess.heard.push({ start_ms: win.start_ms, end_ms: win.end_ms, words: m.words });
        const byHit = assignWindowWords(win, sess.st.hits, m.words);
        for (const [hitId, words] of byHit) {
          const hit = sess.st.hits.find((h) => h.id === hitId);
          if (hit) hit.heard = true;
          if (!hit || (hit.words && hit.words.length)) continue;
          applyRefinement(sess.st, hitId, refineHit(hit, words, sess.pattern, win), nowMs);
        }
        const t = totals(sess);
        say(sess, muteFill(MUTE_TEXT.progress, muteVars(sess.mode, t)), false);
        arm(sess, nowMs, false);
      } else if (m.type === "alignDone") {
        sess.alignDone = true;
        endAlignPort(sess);
        say(sess, alignDoneText(sess.mode, totals(sess), Number(m.failed) || 0,
          sess.windows.length), true);
      } else if (m.type === "alignProgress") {
        sess.sawProgress = true;
      } else if (m.type === "alignError") {
        alignFailed(sess, m);
      }
    }

    function startAlign(sess) {
      const nowMs = Math.max(0, Math.floor(sess.video.currentTime * 1000));
      sess.alignDone = false;
      sess.sawProgress = false;
      sess.windows = alignWindows(sess.st.hits.filter((h) => !h.heard), nowMs, durMs(sess.video));
      if (!sess.windows.length) return;
      let port;
      try { port = deps.connect(); } catch (_) { alignFailed(sess, { reason: "other" }); return; }
      sess.port = port;
      sess.ping = deps.setInterval(() => {
        try { port.postMessage({ type: "ping" }); } catch (_) {}
      }, MUTE_PING_MS);
      sess.safety = deps.setTimeout(() => {
        if (S === sess && sess.port === port && !sess.alignDone) {
          alignFailed(sess, { reason: "timeout" });
        }
      }, MUTE_SAFETY_MS);
      port.onMessage.addListener((m) => onAlign(sess, port, m));
      port.onDisconnect.addListener(() => {
        if (S !== sess || sess.port !== port) return;
        if (!sess.alignDone) alignFailed(sess, { reason: "other" });
        else endAlignPort(sess);
      });
      try {
        port.postMessage({ type: "align", url: deps.pageUrl(),
          windows: sess.windows.map((w) => ({ start_ms: w.start_ms, end_ms: w.end_ms })) });
      } catch (_) {
        alignFailed(sess, { reason: "other" });
      }
    }

    // --- on and off ----------------------------------------------------------------
    // Called straight from the toggle's click: the AudioContext is made and
    // resumed before the first await, while the click still counts as a gesture.
    function start() {
      if (S) return;
      const sess = { active: false, stopped: false, listeners: [], ctx: null,
        resumeFailed: false, sound: null, port: null, ping: null, safety: null,
        timer: null, rearms: 0, edge: null, pill: null, windows: [], alignDone: false,
        st: null, mode: "bleep", pattern: null, video: null, videoId: deps.videoId(),
        status: "", blockedSaid: false, adSaid: false, beforeAd: "",
        // rev 5.6: what a live settings change needs. cues and heard stay in this
        // tab's memory only and are dropped on every end.
        cues: null, heard: [], listKey: "", modeSetLive: false, wordsSetLive: false,
        rebuilt: false, retries: 0, retryTimer: null, sawProgress: false };
      // A pill still fading out from a Stop a moment ago goes now.
      const old = deps.doc.getElementById("tldw-mute-pill");
      if (old && old.parentNode) old.parentNode.removeChild(old);
      openCtx(sess);
      const video = deps.findVideo();
      const refuse = (text) => { closeCtx(sess); say(null, text, true); };
      if (!video) { refuse(MUTE_TEXT.noPlayer); return; }
      if (video.duration === Infinity) { refuse(MUTE_TEXT.live); return; }
      if (isAd()) { refuse(MUTE_TEXT.ad); return; }
      sess.video = video;
      S = sess;
      const panel = ui();
      if (panel) { panel.pressed(true); panel.busy(true); }
      say(sess, MUTE_TEXT.loading, true);
      load(sess);
    }

    async function load(sess) {
      let words, sound, t;
      try {
        words = await deps.getWords();
        sound = await deps.getSound();
      } catch (_) { words = {}; sound = {}; }
      if (S !== sess) return;
      // A change that arrived while these reads were pending wins (rev 5.6).
      if (!sess.wordsSetLive) {
        const list = resolveMuteList((words || {}).muteWords, deps.defaults(), deps.normalise());
        sess.pattern = muteWordsPattern(list);
        sess.listKey = list.join("\n");
      }
      if (!sess.modeSetLive) {
        const picked = (sound || {}).muteSound;
        sess.mode = MUTE_SOUNDS.includes(picked) ? picked : "bleep";
      }
      if (sess.mode === "mute") closeCtx(sess);
      if (!sess.pattern) { endEarly(sess, MUTE_TEXT.emptyList); return; }
      try { t = await deps.readTranscript(); } catch (_) { t = null; }
      if (S !== sess) return;
      if (deps.videoId() !== sess.videoId) { stop({ quiet: true }); return; }
      if (!t || !Array.isArray(t.cues) || !t.cues.length) {
        endEarly(sess, MUTE_TEXT.noTranscript);
        return;
      }
      // The list can be emptied while the captions are read.
      if (!sess.pattern) { endEarly(sess, MUTE_TEXT.emptyList); return; }
      sess.cues = t.cues;
      const found = muteRanges(t.cues, sess.pattern, durMs(sess.video));
      if (!found.n) { endEarly(sess, MUTE_TEXT.noMatches); return; }
      sess.st = { videoId: sess.videoId, hits: found.hits, ranges: found.ranges,
        mode: sess.mode, owned: false, lastSet: null, cur: null, soundOn: false,
        soundBlocked: false, released: false, pending: [] };
      sess.active = true;
      attach(sess);
      sess.pill = buildMutePill(deps.doc, sess.mode, () => stop({ fromPill: true }));
      (deps.doc.fullscreenElement || deps.doc.body).appendChild(sess.pill.host);
      const panel = ui();
      if (panel) panel.busy(false);
      say(sess, muteFill(MUTE_TEXT.on, muteVars(sess.mode, totals(sess))), true);
      tick(sess, null);
      startAlign(sess);
    }

    // Nothing to cover: the session ends, and the toggle goes back off.
    function endEarly(sess, text) {
      closeCtx(sess);
      sess.cues = null;
      sess.heard = null;
      clearRetry(sess);
      S = null;
      const panel = ui();
      if (panel) { panel.busy(false); panel.pressed(false); }
      say(null, text, true);
    }

    // opts: {quiet} ends without the "off" announcement (a new video, a reload);
    // {fromPill} moves focus to the player and keeps the pill's live region for a
    // second so the "off" status is heard.
    function stop(opts) {
      const sess = S;
      if (!sess) return;
      const o = opts || {};
      S = null;
      sess.stopped = true;
      sess.active = false;
      stopSound(sess);
      if (sess.st && sess.st.owned && sess.video) {
        try { sess.video.muted = false; } catch (_) {}
        sess.st.owned = false;
      }
      for (const [target, type, fn, capture] of sess.listeners) {
        try { target.removeEventListener(type, fn, capture ? { capture: true } : false); } catch (_) {}
      }
      sess.listeners = [];
      if (sess.timer) { deps.clearTimeout(sess.timer); sess.timer = null; }
      clearRetry(sess);
      endAlignPort(sess);
      closeCtx(sess);
      sess.cues = null;
      sess.heard = null;
      const panel = ui();
      if (panel) { panel.busy(false); panel.pressed(false); }
      const off = muteOffText(sess.mode);
      const pill = sess.pill;
      sess.pill = null;
      if (pill && o.fromPill) {
        const player = deps.doc.querySelector("#movie_player");
        if (player && player.focus) { try { player.focus(); } catch (_) {} }
        lastStatus = off;
        if (panel) { panel.status(off); panel.announce(off); }
        else pill.live.textContent = off;
        deps.setTimeout(() => {
          if (pill.host.parentNode) pill.host.parentNode.removeChild(pill.host);
        }, MUTE_PILL_GONE_MS);
        return;
      }
      if (pill && o.endText) {
        lastStatus = o.endText;
        pill.live.textContent = o.endText;
        deps.setTimeout(() => {
          if (pill.host.parentNode) pill.host.parentNode.removeChild(pill.host);
        }, MUTE_PILL_GONE_MS);
        return;
      }
      if (pill && pill.host.parentNode) pill.host.parentNode.removeChild(pill.host);
      if (!o.quiet) say(null, off, true);
    }

    // --- live settings (rev 5.6) ----------------------------------------------------
    // A sound switch: no sound now, the range in progress stays silent, the next
    // range uses the new sound. Going to mute closes the context; leaving mute
    // opens one.
    function setSound(sess, mode) {
      if (!sess.active) {
        sess.modeSetLive = true;   // load keeps this over a late stored read
        if (mode === sess.mode) return;
        sess.mode = mode;
        if (mode === "mute") closeCtx(sess); else openCtx(sess);
        return;
      }
      if (mode === sess.mode) return;
      stopSound(sess);
      sess.st = muteSwitchSound(sess.st, mode);
      sess.mode = mode;
      if (mode === "mute") closeCtx(sess); else openCtx(sess);
      if (sess.pill) sess.pill.setMode(mode);
      say(sess, muteFill(MUTE_TEXT.soundChanged, muteVars(mode, totals(sess))), true);
      tick(sess, null);
    }

    // A new list while on: re-scan the kept captions in place. Hits inside a
    // window Whisper already heard are refined here; only the rest are sent.
    function rebuild(sess, list) {
      const pattern = muteWordsPattern(list);
      // With the panel closed, the pill says why before it goes (as Stop does).
      const endLive = (text) => {
        if (!ui() && sess.pill) { stop({ quiet: true, endText: text }); return; }
        stop({ quiet: true });
        say(null, text, true);
      };
      if (!pattern) { endLive(MUTE_TEXT.liveEmptyList); return; }
      const found = muteRanges(sess.cues, pattern, durMs(sess.video));
      if (!found.n) { endLive(MUTE_TEXT.liveNoMatches); return; }
      const v = sess.video;
      endAlignPort(sess);
      clearRetry(sess);
      // The boundary timer, re-arms and edge are reset by the tick below (arm),
      // and alignDone by startAlign; only the retry count is ours to reset.
      sess.retries = 0;
      sess.rebuilt = true;
      sess.pattern = pattern;
      sess.listKey = list.join("\n");
      const old = sess.st;
      const nowMs = Math.max(0, Math.floor(v.currentTime * 1000));
      sess.st = { videoId: sess.videoId, hits: found.hits, ranges: found.ranges,
        mode: sess.mode, owned: false, lastSet: null, cur: null, soundOn: false,
        soundBlocked: !!(old && old.soundBlocked), released: false, pending: [] };
      // Each new hit whose own window lies inside one Whisper already returned is
      // refined from that window, its words shared out over every such hit at
      // once, exactly as onAlign does for a window that arrives on the port.
      const dur = durMs(v);
      const byHeard = new Map();
      for (const h of sess.st.hits) {
        const win = alignWindows([h], 0, dur)[0];
        if (!win) continue;
        const heard = (sess.heard || []).find((w) => w.start_ms <= win.start_ms && w.end_ms >= win.end_ms);
        if (!heard) continue;
        h.heard = true;
        if (!byHeard.has(heard)) byHeard.set(heard, []);
        byHeard.get(heard).push(h.id);
      }
      for (const [heard, ids] of byHeard) {
        const win = { start_ms: heard.start_ms, end_ms: heard.end_ms, hits: ids };
        const byHit = assignWindowWords(win, sess.st.hits, heard.words);
        for (const [hitId, words] of byHit) {
          const hit = sess.st.hits.find((h) => h.id === hitId);
          if (hit) applyRefinement(sess.st, hitId, refineHit(hit, words, pattern, win), nowMs);
        }
      }
      // The range under the playhead, if it's unchanged, carries on as it was:
      // the user's own unmute still wins and a sound already playing isn't
      // restarted. Otherwise give up our mute and re-decide in the same task,
      // so the audio never comes back for an instant inside a range.
      const r = rangeAt(sess.st.ranges, nowMs);
      if (old && old.cur && r && r.s === old.cur.s && r.e === old.cur.e) {
        Object.assign(sess.st, { cur: old.cur, owned: old.owned, lastSet: old.lastSet,
          released: old.released, soundOn: old.soundOn, quietCur: old.quietCur || null });
      } else {
        stopSound(sess);
        if (old && old.owned) { try { v.muted = false; } catch (_) {} }
      }
      say(sess, muteFill(MUTE_TEXT.listUpdated, muteVars(sess.mode, totals(sess))), true);
      tick(sess, null);
      if (S !== sess) return;
      startAlign(sess);
      if (!sess.windows.length) {
        sess.alignDone = true;
        say(sess, alignDoneText(sess.mode, totals(sess), 0, 0), false);
      }
    }

    // From the one storage.onChanged listener: { sound?: {newValue}, words?: {newValue} }.
    // A removed key resolves as at toggle-on. Sound goes first, then the list.
    function settingsChanged(ch) {
      const sess = S;
      if (!sess || !ch) return;
      if (ch.sound) {
        const picked = ch.sound.newValue;
        setSound(sess, MUTE_SOUNDS.includes(picked) ? picked : "bleep");
      }
      if (S !== sess || !ch.words) return;
      const list = resolveMuteList(ch.words.newValue, deps.defaults(), deps.normalise());
      if (!sess.active) {
        sess.wordsSetLive = true;
        sess.pattern = muteWordsPattern(list);
        sess.listKey = list.join("\n");
        return;
      }
      if (list.join("\n") === sess.listKey) return;
      rebuild(sess, list);
    }

    return {
      start, stop, settingsChanged,
      isOn: () => !!S,
      isLoading: () => !!S && !S.active,
      status: () => lastStatus,
      _session: () => S,
    };
  }
  // The open panel's side of the session: a visible status line, a live region for
  // the announced statuses, the toggle's pressed state, and busy while reading.
  // `root` is the panel's shadow root (null while closed); `later` is setTimeout.
  function coverPanelUi(root, later) {
    if (!root) return null;
    const btn = root.querySelector(".covertoggle");
    const line = root.querySelector(".coverstatus");
    const live = root.querySelector(".coverlive");
    const row = root.querySelector(".coverwrap");
    if (!btn || !line || !live || !row) return null;
    return {
      status(text) { line.textContent = text; },
      announce(text) {
        live.textContent = "";
        later(() => { live.textContent = text; }, MUTE_ANNOUNCE_DELAY_MS);
      },
      pressed(on) { btn.setAttribute("aria-pressed", on ? "true" : "false"); },
      // aria-disabled, not disabled: the button keeps focus while the captions are
      // read, and a second press stops the session (its onclick checks isOn).
      busy(on) {
        btn.setAttribute("aria-disabled", on ? "true" : "false");
        if (on) row.setAttribute("aria-busy", "true");
        else row.removeAttribute("aria-busy");
      },
    };
  }

  // A re-opened panel takes its toggle state and status from the live session.
  function setupCoverToggle(root, cover, later) {
    const btn = root && root.querySelector(".covertoggle");
    const u = coverPanelUi(root, later);
    if (!btn || !u) return;
    u.pressed(cover.isOn());
    u.busy(cover.isLoading());
    if (cover.isOn()) u.status(cover.status());
    btn.onclick = () => {
      if (cover.isOn()) cover.stop();
      else cover.start();
    };
  }
  // --- T-020 cover listed words: wiring (END) ------------------------------------

  // The page's one session, wired to the real page and browser.
  const muteCover = createMuteCover({
    doc: document,
    win: window,
    findVideo: () => document.querySelector("video.html5-main-video")
      || document.querySelector("video"),
    videoId: () => { try { return new URL(location.href).searchParams.get("v"); }
                     catch (_) { return null; } },
    pageUrl: () => location.href,
    readTranscript: collectPageTranscript,
    // Read at toggle-on; later changes arrive through the one onChanged
    // listener at the top of this file (rev 5.6), never a second one.
    getWords: () => api.storage.local.get("muteWords"),
    getSound: () => api.storage.local.get("muteSound"),
    connect: () => api.runtime.connect({ name: "tldw" }),
    AudioCtx: window.AudioContext || window.webkitAudioContext,
    defaults: () => globalThis.TLDW_MUTE_DEFAULTS,
    normalise: () => globalThis.TLDW_normaliseMuteList,
    ui: coverUi,
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (id) => clearInterval(id),
  });
  onMuteSettings = (live) => muteCover.settingsChanged(live);

  function coverUi() { return coverPanelUi(root, (fn, ms) => setTimeout(fn, ms)); }
  function setupCover() { setupCoverToggle(root, muteCover, (fn, ms) => setTimeout(fn, ms)); }

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
        refreshTrimmedNote();
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

  // " · 8m30s trimmed video (35%)" for the summary header, once the key moments for
  // this video are known. Empty until then — they're picked in the background.
  // The header is already on screen when the clips finish arriving; patch the one
  // line rather than re-rendering the summary under the reader.
  function refreshTrimmedNote() {
    const el = root && root.querySelector(".meta");
    if (!el || !lastPayload) return;
    const note = trimmedNote();
    if (note && !el.textContent.includes("trimmed video")) {
      const link = el.querySelector("a");
      el.insertBefore(document.createTextNode(note + " "), link ? link.previousSibling : null);
    }
  }

  function trimmedNote() {
    const vid = lastPayload && lastPayload.video_id;
    if (!lastSegments || lastSegments.videoId !== vid ||
        !(lastSegments.segments || []).length) return "";
    const v = document.querySelector("video.html5-main-video")
      || document.querySelector("video");
    const src = v && isFinite(v.duration) ? v.duration : 0;
    const { kept, pct } = clipTotals(lastSegments.segments, src);
    return ` · ${fmtDur(kept)} trimmed video` + (pct === null ? "" : ` (${pct}%)`);
  }

  // Total playtime of a set of clips, and how that compares to the whole video.
  function clipTotals(segments, sourceSeconds) {
    const kept = (segments || []).reduce(
      (t, sg) => t + Math.max(0, (sg.end || 0) - (sg.start || 0)), 0);
    const pct = sourceSeconds > 0 ? Math.round((kept / sourceSeconds) * 100) : null;
    return { kept, pct };
  }

  // "8m30s" — compact, for sitting inline next to other text.
  function fmtDur(s) {
    const t = Math.max(0, Math.round(s));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
    if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
    if (m > 0) return sec ? `${m}m${String(sec).padStart(2, "0")}s` : `${m}m`;
    return `${sec}s`;
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
        const src = skipVideo && isFinite(skipVideo.duration) ? skipVideo.duration : 0;
        const { kept, pct } = clipTotals(skipSegs, src);
        // While clips are still arriving the totals are a running tally, not the
        // final answer — say so rather than showing a figure that keeps changing.
        const tally = src
          ? ` · ${segsComplete ? "" : "so far "}${fmtDur(kept)} of ${fmtDur(src)}` +
            (pct === null ? "" : ` (${pct}%)`)
          : "";
        label.textContent =
          `⏭ Clip ${skipIdx + 1}/${total} · ${fmtTime(seg.start)}–${fmtTime(seg.end)}${tally}`;
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

  // Mirrors naming.sanitize_field on the server: drop control and filesystem-illegal
  // characters, protect the " - " joiner the template uses, collapse whitespace,
  // and never end up with a leading/trailing dot or space.
  function sanitizeField(value, maxLen) {
    let out = String(value || "")
      .replace(/[\x00-\x1f<>:"/\\|?*]/g, " ")
      .replace(/ - /g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (out.length > maxLen) out = out.slice(0, maxLen).trim();
    out = out.replace(/^[. ]+/, "").replace(/[. ]+$/, "");
    return out || "untitled";
  }

  function audioFileName(title, channel) {
    return sanitizeField(title, 120) + " - " + sanitizeField(channel, 60) +
      " - tldw version.mp3";
  }

  // Only offered once a COMPLETE mp3 exists — mid-stream the player is backed by a
  // MediaSource, which isn't a file you can hand to the user.
  function enableDownload(dataUrl) {
    const dl = root && root.querySelector(".audioslot .dl");
    if (!dl || !lastPayload || !dataUrl) return;
    const name = audioFileName(lastPayload.title, lastPayload.channel);
    dl.hidden = false;
    dl.title = "Download “" + name + "”";
    dl.onclick = () => {
      // Go through a Blob rather than handing the anchor the data: URL — browsers
      // are far more willing to download blob:, and it can be released after.
      const bytes = b64ToBytes(dataUrl.slice(dataUrl.indexOf(",") + 1));
      const url = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    };
  }

  function renderAudio(src, restore) {
    const slot = root && root.querySelector(".audioslot");
    if (!slot) return null;
    slot.innerHTML = "";                                 // replace, never stack
    const row = document.createElement("div");
    row.className = "playerrow";
    const a = document.createElement("audio");
    a.controls = true; a.src = src;
    a.setAttribute("aria-label", "Spoken summary");
    // Native <audio controls> renders its own shadow UI, so this can't live inside
    // the control bar next to the volume icon — it sits alongside the player.
    const dl = document.createElement("button");
    dl.className = "dl";
    dl.hidden = true;
    dl.textContent = "⬇";
    dl.setAttribute("aria-label", "Download this summary as an mp3");
    row.appendChild(a);
    row.appendChild(dl);
    slot.appendChild(row);
    if (!busy) setListenState("done");        // mid-stream the button is still Stop
    if (src.startsWith("data:")) enableDownload(src);   // a finished clip
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

  // A summary written from a machine transcription of the audio is not the same
  // artifact as one written from the video's own captions -- different accuracy,
  // different failure modes. Whoever reads this later should be told which it was.
  function transcribedNote(p) {
    const source = p && p.transcript_source;
    if (typeof source !== "string" || !source.startsWith("audio:")) return "";
    return "transcribed from audio by Whisper (" + source.slice(6) + ")";
  }

  function toMarkdown(p) {
    const kp = (p.key_points || []).map((k) => "- " + k).join("\n");
    const note = transcribedNote(p);
    return `# ${p.title}\n${p.channel} · ${p.original_length} → ~${p.length_label} read\n` +
      (note ? `_${note}_\n` : "") +
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
        if (partialState && partialState.meta) {
          showPartial();               // everything written while the panel was shut
          return;
        }
        // showLoading() resets progressPct, and replaying lastProgress would only
        // restore the server's last reported figure (15% for the Claude step) —
        // rewinding whatever the creep had reached since. Carry it across.
        const carried = progressPct;
        showLoading();
        progressPct = carried;
        if (lastProgress) {
          updateProgress(lastProgress.message, lastProgress.percent, lastProgress.creep);
        }
        // showLoading() rebuilt .content from scratch; without this an eight-minute
        // job becomes uncancellable simply because the panel was closed once.
        if (txLive) addStopControl(txLive.url);
        applyWidth();
      } else startSummarize(msg.url, msg.videoId);
    } else if (msg.type === "TLDW_ERROR") showError(msg.error);
  });
})();
