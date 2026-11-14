// The shared helpers for automatic summaries. Chrome's service worker loads them
// here; Firefox's event page has no importScripts and lists channels.js before this
// file instead. Guarded twice: a missing or broken channels.js must never take the
// worker down -- manual summaries don't need it, and every auto feature checks for
// it and fails closed.
if (typeof importScripts === "function") {
  try { importScripts("channels.js"); } catch (e) {
    console.warn("[TL;DW auto] channels.js failed to load:", e);
  }
}

// Service worker. The content script opens a long-lived Port and the work happens
// while that port is connected — this keeps the worker alive through the ~20-60s
// summarize (a plain awaited fetch in the click handler would be suspended ~30s in
// and silently never deliver a result). A hard timeout guarantees an answer.

// Cross-browser: Firefox exposes promise-based `browser.*`; Chrome uses `chrome.*`
// (also promise-based in MV3). Either way, `api` gives us promises.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "", voice: "amy",
                   model: "opus", segmentRatio: "auto", whisper: "ask" };
// Keep in step with the server's claude_client.MODELS; it refuses anything else.
const MODELS = ["opus", "sonnet"];
// Keep in step with the server's WHISPER_POLICIES; it refuses anything else.
const WHISPER_POLICIES = ["off", "ask", "always"];
const CLIENT_TIMEOUT_MS = 150000;
// Transcribing a video's audio runs at roughly 5.5x realtime, so the two-and-a-half
// minutes above would cap it at a ~14-minute video -- it would abort mid-run, after
// the estimate had already been accepted. This budget is for that path only.
const TRANSCRIBE_TIMEOUT_MS = 2400000;  // 40 min: a 2-hour video at the server ceiling
const SEG_TIMEOUT_MS = 320000;          // segment selection can take 2-3 min (Claude JSON)
const ASK_TIMEOUT_MS = 300000;          // one answer over an already-fetched transcript
const SPEAK_TIMEOUT_MS = 600000;        // long summaries synthesize for minutes;
                                        // playback starts in ~1s regardless
const cache = new Map();                // videoId -> summary payload
const audioCache = new Map();           // `${videoId}|${voice}` -> data: URL

function videoIdFromUrl(url) {
  try {
    const u = new URL(url);
    if (u.hostname.endsWith("youtube.com") && u.pathname === "/watch") {
      const v = u.searchParams.get("v");
      return v && /^[A-Za-z0-9_-]{11}$/.test(v) ? v : null;
    }
    if (u.hostname === "youtu.be") {
      const v = u.pathname.slice(1).split("/")[0];
      return /^[A-Za-z0-9_-]{11}$/.test(v) ? v : null;
    }
  } catch (_) {}
  return null;
}

async function getSettings() {
  const s = await api.storage.local.get(DEFAULTS);
  // "auto" means no ratio at all — the model judges each video.
  const ratio = parseFloat(s.segmentRatio);
  return { serverUrl: s.serverUrl || DEFAULTS.serverUrl, token: s.token || "",
           model: MODELS.includes(s.model) ? s.model : DEFAULTS.model,
           whisper: WHISPER_POLICIES.includes(s.whisper) ? s.whisper
                                                         : DEFAULTS.whisper,
           segmentRatio: ratio > 0 && ratio <= 1 ? ratio : null };
}

function send(tabId, msg) {
  api.tabs.sendMessage(tabId, msg).catch(() => {});
}

api.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  const videoId = tab.url && videoIdFromUrl(tab.url);
  try {
    await api.scripting.executeScript({ target: { tabId: tab.id },
                                       files: ["channels.js", "content.js"] });
  } catch (e) {
    // Never let the automatic-summary helpers take the click down with them:
    // content.js alone works, with those features off.
    console.warn("[TL;DW auto] injecting channels.js failed; continuing without it:", e);
    await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  }
  if (!videoId) {
    send(tab.id, { type: "TLDW_ERROR",
      error: "Open a YouTube video (a /watch page) first, then click TL;DW." });
    return;
  }
  send(tab.id, { type: "TLDW_INVOKE", url: tab.url, videoId });
});

api.runtime.onConnect.addListener((port) => {
  if (port.name !== "tldw") return;
  port.onMessage.addListener((msg) => {
    if (!msg) return;
    if (msg.type === "summarize") handleSummarize(port, msg);
    else if (msg.type === "probeCache") handleProbe(port, msg);
    else if (msg.type === "speak") handleSpeak(port, msg);
    else if (msg.type === "getSegments") handleSegments(port, msg);
    else if (msg.type === "preview") handlePreview(port, msg);
    else if (msg.type === "ask") handleAsk(port, msg);
    else if (msg.type === "stopTranscribe") handleStopTranscribe(msg);
    // "ping" is ignored; receiving it just keeps the worker alive
  });
});

const PROBE_TIMEOUT_MS = 5000;

// Is there already a summary for this video? Answered from this worker's own
// cache, then the server's -- without reading the page, which is the slow part.
// Every failure is a miss: the panel then does exactly what it always did. An
// older server answers 404, which is a miss too, so a new extension can never
// skip the page's transcript against a server that would then go to YouTube.
async function handleProbe(port, msg) {
  const { url, videoId } = msg;
  if (videoId && cache.has(videoId)) {
    safePost(port, { type: "probe", hit: true, payload: cache.get(videoId) });
    return;
  }
  const { serverUrl, token, model, segmentRatio } = await getSettings();
  if (!token) {
    // Said now, rather than after reading the page for a request that can't run.
    safePost(port, { type: "error",
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/summarize/cached", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ url, model, segment_ratio: segmentRatio }),
    });
    if (resp.ok) {
      const ev = await resp.json();
      if (ev && ev.type === "result") {
        if (videoId) cache.set(videoId, ev);
        safePost(port, { type: "probe", hit: true, payload: ev });
        return;
      }
    }
  } catch (_) {
    // Unreachable, timed out, not JSON: all of them just mean "read the page".
  } finally {
    clearTimeout(timer);
  }
  safePost(port, { type: "probe", hit: false });
}

async function handleSummarize(port, msg) {
  const { url, videoId, refresh, page, transcribe } = msg;
  if (videoId && cache.has(videoId) && !refresh) {
    safePost(port, { type: "result", payload: cache.get(videoId), cached: true });
    return;
  }
  const { serverUrl, token, model, segmentRatio, whisper } = await getSettings();
  if (!token) {
    safePost(port, { type: "error",
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  // An automatic run spends the owner's plan without a click, so it is counted
  // here, where the page can't reach -- after the free answers above (the worker's
  // own cache, a missing token) and immediately before the request. Only exactly
  // `true` counts as automatic; anything else is a click.
  if (msg.auto === true) {
    const verdict = await takeAutoBudget(Date.now());
    if (!verdict.ok) {
      console.info("[TL;DW auto] paused:", verdict.limit, "limit reached");
      safePost(port, { type: "autoPaused", limit: verdict.limit });
      return;
    }
  }
  const ctrl = new AbortController();
  // Accepting the offer means agreeing to wait minutes, so this request gets the
  // budget that implies rather than the one sized for a model call.
  const budget = transcribe || whisper === "always"
    ? TRANSCRIBE_TIMEOUT_MS : CLIENT_TIMEOUT_MS;
  const timer = setTimeout(() => ctrl.abort(), budget);
  let gotTerminal = false;
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/summarize/stream", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ url, model, segment_ratio: segmentRatio,
                             refresh: !!refresh, page, whisper,
                             transcribe: transcribe === true }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      safePost(port, { type: "error", error: httpError(resp.status, detail) });
      return;
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.type === "progress") {
          safePost(port, { type: "progress", message: ev.message,
            percent: ev.percent, creep: ev.creep });
        } else if (ev.type === "meta") {
          // The video's details, before the summary exists: enough to draw it.
          safePost(port, { type: "meta", meta: {
            video_id: ev.video_id, title: ev.title, channel: ev.channel,
            source_url: ev.source_url, original_length: ev.original_length } });
        } else if (ev.type === "transcribing") {
          safePost(port, { type: "transcribing", videoId: ev.video_id,
                           estimateS: ev.estimate_s });
        } else if (ev.type === "needs_transcription") {
          // The video has no captions; the server is waiting to be told whether to
          // transcribe the audio. Terminal for this request -- accepting sends a
          // new one.
          gotTerminal = true;
          safePost(port, { type: "needsTranscription", offer: {
            video_id: ev.video_id, title: ev.title, channel: ev.channel,
            duration_ms: ev.duration_ms, estimate_s: ev.estimate_s,
            model: ev.model, installed: ev.installed,
            install_hint: ev.install_hint } });
        } else if (ev.type === "partial") {
          safePost(port, { type: "partial", kind: ev.kind, text: ev.text });
        } else if (ev.type === "result") {
          gotTerminal = true;
          if (videoId) cache.set(videoId, ev);
          // ev.cached marks a summary the server had already produced.
          safePost(port, { type: "result", payload: ev, cached: !!ev.cached });
        } else if (ev.type === "error") {
          gotTerminal = true;
          safePost(port, { type: "error", error: httpError(ev.status, ev.error) });
        }
      }
    }
    if (!gotTerminal) {
      safePost(port, { type: "error", error: "The summary stream ended unexpectedly. Try again." });
    }
  } catch (e) {
    if (e.name === "AbortError") {
      safePost(port, { type: "error", error: budget === TRANSCRIBE_TIMEOUT_MS
        ? "Transcribing timed out. The transcript may still finish on the server — try again in a few minutes."
        : "Summarizing timed out (over 150s). Try again, or use the tldw CLI for long videos." });
    } else {
      safePost(port, { type: "error",
        error: "Can't reach the tldw server. Is it running? In a terminal:  tldw serve" });
    }
  } finally {
    clearTimeout(timer);
  }
}

async function handleStopTranscribe(msg) {
  const { serverUrl, token } = await getSettings();
  if (!token || !msg.url) return;
  try {
    await fetch(serverUrl.replace(/\/+$/, "") + "/transcribe/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ url: msg.url }),
    });
  } catch (_) {
    // Best effort. The run also stops on its own budget.
  }
}

async function handleSpeak(port, msg) {
  const { videoId, voice, payload } = msg;
  const key = (videoId || "") + "|" + voice;
  if (audioCache.has(key)) {
    safePost(port, { type: "audio", dataUrl: audioCache.get(key), cached: true });
    return;
  }
  const { serverUrl, token } = await getSettings();
  if (!token) {
    safePost(port, { type: "speakError",
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), SPEAK_TIMEOUT_MS);
  // Only an explicit Stop cancels the request. A plain disconnect must NOT: closing
  // the panel to pause the video is normal, and letting synthesis finish puts the
  // clip in audioCache so re-opening and pressing Listen is instant.
  let stopped = false;
  port.onMessage.addListener((m) => {
    if (m && m.type === "stopSpeak") { stopped = true; ctrl.abort(); }
  });
  let gotTerminal = false;
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/speak/stream", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({
        title: payload.title, channel: payload.channel,
        key_points: payload.key_points, summary: payload.summary_md, voice,
        stream_audio: msg.stream !== false,   // incremental mp3 blocks where MSE works
      }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      safePost(port, { type: "speakError", error: speakError(resp.status, detail) });
      return;
    }
    // NDJSON: {type:progress} steps, then either a run of {type:audio_chunk} blocks
    // ending in {type:audio_end}, or (older server) one final {type:audio}.
    const parts = [];
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.type === "progress") {
          safePost(port, { type: "speakProgress", message: ev.message, percent: ev.percent });
        } else if (ev.type === "audio_chunk") {
          // Hand it straight to the page so playback can start, and keep a copy so
          // the finished clip still lands in the session cache.
          safePost(port, { type: "audioChunk", b64: ev.mp3_base64, first: !parts.length });
          parts.push(ev.mp3_base64);
        } else if (ev.type === "audio_end") {
          gotTerminal = true;
          const dataUrl = "data:audio/mpeg;base64," + joinBase64(parts);
          audioCache.set(key, dataUrl);
          safePost(port, { type: "audioEnd", dataUrl });
        } else if (ev.type === "audio") {
          gotTerminal = true;
          const dataUrl = "data:audio/mpeg;base64," + ev.mp3_base64;
          audioCache.set(key, dataUrl);
          safePost(port, { type: "audio", dataUrl });
        } else if (ev.type === "error") {
          gotTerminal = true;
          safePost(port, { type: "speakError", error: speakError(ev.status, ev.error) });
        }
      }
    }
    if (!gotTerminal && !stopped) {
      safePost(port, { type: "speakError", error: "Audio stream ended unexpectedly. Try again." });
    }
  } catch (e) {
    if (stopped) {
      // Deliberate abort — the page has already reset itself.
    } else if (e.name === "AbortError") {
      safePost(port, { type: "speakError",
        error: "Audio generation timed out. Try again (first use downloads the voice)." });
    } else {
      safePost(port, { type: "speakError",
        error: "Can't reach the tldw server. Is it running?  tldw serve" });
    }
  } finally {
    clearTimeout(timer);
  }
}

// Concatenating mp3 blocks has to happen on the bytes, not the base64 text: each
// block's base64 is independently padded, so joining the strings would corrupt it.
function joinBase64(parts) {
  if (parts.length === 1) return parts[0];
  let len = 0;
  const bufs = parts.map((b64) => {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    len += u8.length;
    return u8;
  });
  const all = new Uint8Array(len);
  let at = 0;
  for (const b of bufs) { all.set(b, at); at += b.length; }
  return bytesToBase64(all);
}

function bytesToBase64(u8) {
  let bin = "";
  for (let i = 0; i < u8.length; i += 0x8000) {   // chunked: btoa chokes on huge spreads
    bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

// Follow-up Q&A. The transcript never leaves the server; this carries only the
// question and the conversation so far, and streams the answer back in deltas.
async function handleAsk(port, msg) {
  const { url, question, history } = msg;
  const { serverUrl, token, model, segmentRatio } = await getSettings();
  if (!token) {
    safePost(port, { type: "askError",
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ASK_TIMEOUT_MS);
  // Same rule as speak: only an explicit stop cancels. A bare disconnect (closing
  // the panel mid-answer) lets it finish.
  let stopped = false;
  port.onMessage.addListener((m) => {
    if (m && m.type === "stopAsk") { stopped = true; ctrl.abort(); }
  });
  let gotTerminal = false;
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/ask/stream", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ url, question, history, model }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      safePost(port, { type: "askError", error: askError(resp.status, detail) });
      return;
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.type === "progress") {
          safePost(port, { type: "askProgress", message: ev.message });
        } else if (ev.type === "delta") {
          safePost(port, { type: "askDelta", text: ev.text });
        } else if (ev.type === "answer_done") {
          gotTerminal = true;
          safePost(port, { type: "askDone" });
        } else if (ev.type === "error") {
          gotTerminal = true;
          safePost(port, { type: "askError", error: askError(ev.status, ev.error) });
        }
      }
    }
    if (!gotTerminal && !stopped) {
      safePost(port, { type: "askError", error: "The answer stream ended unexpectedly. Try again." });
    }
  } catch (e) {
    if (stopped) {
      // Deliberate stop — the page has already reset itself.
    } else if (e.name === "AbortError") {
      safePost(port, { type: "askError", error: "That question took too long. Try again." });
    } else {
      safePost(port, { type: "askError",
        error: "Can't reach the tldw server. Is it running?  tldw serve" });
    }
  } finally {
    clearTimeout(timer);
  }
}

function askError(status, detail) {
  if (status === 401) return "Token mismatch. Re-check the token in the extension's Options.";
  if (status === 429) return detail || "Server busy, try again in a moment.";
  if (status === 413) return "That question is too long.";
  if (status === 422) return detail || "This video has no transcript to answer from.";
  if (status === 504) return "That question took too long. Try again.";
  return detail || ("Couldn't answer (server error " + status + ").");
}

const previewCache = new Map();         // voice -> data: URL

async function handlePreview(port, msg) {
  const voice = msg.voice;
  if (previewCache.has(voice)) {
    safePost(port, { type: "preview", voice, dataUrl: previewCache.get(voice) });
    return;
  }
  const { serverUrl, token } = await getSettings();
  if (!token) {
    safePost(port, { type: "previewError", voice,
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), SPEAK_TIMEOUT_MS);
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/preview", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ voice }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      safePost(port, { type: "previewError", voice, error: speakError(resp.status, detail) });
      return;
    }
    const buf = new Uint8Array(await resp.arrayBuffer());
    const dataUrl = "data:audio/mpeg;base64," + bytesToBase64(buf);
    previewCache.set(voice, dataUrl);
    safePost(port, { type: "preview", voice, dataUrl });
  } catch (e) {
    safePost(port, { type: "previewError", voice,
      error: e.name === "AbortError"
        ? "Voice preview timed out (first use downloads the voice)."
        : "Can't reach the tldw server. Is it running?  tldw serve" });
  } finally {
    clearTimeout(timer);
  }
}

function speakError(status, detail) {
  if (status === 401) return "Token mismatch. Re-check the token in the extension's Options.";
  if (status === 429) return detail || "Server busy, try again in a moment.";
  if (status === 503) return detail || "Audio isn't available — Piper TTS isn't installed on the server.";
  if (status === 504) return "Audio generation timed out. Try again.";
  return detail || ("Audio failed (server error " + status + ").");
}

async function handleSegments(port, msg) {
  const { url } = msg;
  const { serverUrl, token, model, segmentRatio } = await getSettings();
  if (!token) {
    safePost(port, { type: "segError",
      error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
    return;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), SEG_TIMEOUT_MS);
  let gotTerminal = false;
  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/segments/stream", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ url, model, ratio: segmentRatio }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      safePost(port, { type: "segError", error: httpError(resp.status, detail) });
      return;
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.type === "progress") {
          safePost(port, { type: "segProgress", message: ev.message, percent: ev.percent });
        } else if (ev.type === "segment_added") {
          safePost(port, { type: "segmentAdded", segment: ev.segment });
        } else if (ev.type === "segments_done") {
          gotTerminal = true;
          safePost(port, { type: "segmentsDone", title: ev.title,
            channel: ev.channel, source_url: ev.source_url });
        } else if (ev.type === "segments") {
          gotTerminal = true;
          safePost(port, { type: "segments", segments: ev.segments, title: ev.title });
        } else if (ev.type === "error") {
          gotTerminal = true;
          safePost(port, { type: "segError", error: httpError(ev.status, ev.error) });
        }
      }
    }
    if (!gotTerminal) {
      safePost(port, { type: "segError", error: "The segment stream ended unexpectedly. Try again." });
    }
  } catch (e) {
    if (e.name === "AbortError") {
      safePost(port, { type: "segError", error: "Finding key moments timed out. Try again." });
    } else {
      safePost(port, { type: "segError", error: "Can't reach the tldw server. Is it running?  tldw serve" });
    }
  } finally {
    clearTimeout(timer);
  }
}

function safePost(port, msg) {
  try { port.postMessage(msg); } catch (_) {}
}

function httpError(status, detail) {
  if (status === 401) return "Token mismatch. Re-check the token in the extension's Options.";
  if (status === 413) return detail || "This transcript is too long for the browser; use the tldw CLI.";
  if (status === 422) return detail || "This video has no captions/transcript to summarize.";
  // The server says which of its limits was hit (summaries/questions, or reading
  // aloud); an older server's detail ("busy, try again shortly") reads fine too.
  if (status === 429) return detail || "Server busy, try again in a moment.";
  if (status === 502) return "The model call failed. If you're on the Claude CLI, make sure `claude` is logged in; otherwise check your configured model command.";
  if (status === 504) return "Summarizing timed out on the server. Try again, or use the CLI for long videos.";
  return detail || ("Server error (" + status + ").");
}


// --- automatic summaries ----------------------------------------------------------
//
// Everything below is a no-op until the owner ticks the option, which also grants
// the optional youtube.com permission. With either missing, nothing is registered.

const AUTO_SCRIPT_ID = "tldw-auto";
const channelsLib = () => {
  const ch = globalThis.TLDW_CHANNELS;
  return ch && typeof ch.channelKey === "function" ? ch : null;
};

// One sync at a time: turning the option on fires permissions.onAdded and
// storage.onChanged together, and two unserialized syncs would both register.
let syncChain = Promise.resolve();

function syncAutoScript() {
  syncChain = syncChain.then(doSync, doSync).catch((e) => {
    console.warn("[TL;DW auto] sync failed:", e);
  });
  return syncChain;
}

async function registeredAuto() {
  const list = await api.scripting.getRegisteredContentScripts({ ids: [AUTO_SCRIPT_ID] });
  return Array.isArray(list) && list.some((s) => s.id === AUTO_SCRIPT_ID);
}

async function unregisterAuto() {
  try {
    await api.scripting.unregisterContentScripts({ ids: [AUTO_SCRIPT_ID] });
    console.info("[TL;DW auto] unregister");
  } catch (e) {
    if (!/nonexistent|does not exist|no.*script/i.test(String(e && e.message || e))) throw e;
  }
}

// Registered exactly when the option is on, the permission is held, and the
// shared helpers loaded. Both conditions are read fresh on every run.
async function doSync() {
  if (!api.scripting || !api.scripting.registerContentScripts) return;
  const ch = channelsLib();
  const { autoRun } = await api.storage.local.get({ autoRun: false });
  const granted = !!ch && await api.permissions.contains({ origins: [ch.ORIGIN] });
  const want = autoRun === true && granted;
  const have = await registeredAuto();
  if (want === have) return;
  if (!want) { await unregisterAuto(); return; }
  try {
    await api.scripting.registerContentScripts([{
      id: AUTO_SCRIPT_ID, matches: [ch.ORIGIN], js: ["channels.js", "content.js"],
      runAt: "document_idle", allFrames: false, persistAcrossSessions: true,
    }]);
    console.info("[TL;DW auto] register");
  } catch (e) {
    if (/duplicate/i.test(String(e && e.message || e))) return;
    // Anything else: say so and leave nothing registered.
    console.warn("[TL;DW auto] register failed:", e);
    try { await unregisterAuto(); } catch (_) {}
  }
}

// After an install or update, re-register so the script names this build's files.
api.runtime.onInstalled.addListener(() => {
  syncChain = syncChain.then(unregisterAuto, unregisterAuto).catch(() => {});
  syncAutoScript();
});
api.runtime.onStartup.addListener(() => { syncAutoScript(); });
api.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.autoRun) syncAutoScript();
});
api.permissions.onAdded.addListener(() => { syncAutoScript(); });
api.permissions.onRemoved.addListener(() => { syncAutoScript(); });

// The budget: start times of automatic runs, kept in storage.local so "40 a day"
// survives a browser restart. One check-and-record at a time, so two tabs that
// finish their dwell together can't both read 9. A storage failure pauses rather
// than lets runs through.
let budgetChain = Promise.resolve();

function takeAutoBudget(now) {
  const step = budgetChain.then(async () => {
    const ch = channelsLib();
    if (!ch) return { ok: false, limit: "hour" };
    const { autoRunLog } = await api.storage.local.get({ autoRunLog: [] });
    const log = ch.sanitizeRunLog(autoRunLog, now);
    const verdict = ch.budgetDecision(log, now);
    if (verdict.ok) {
      log.push(now);
      await api.storage.local.set({ autoRunLog: ch.sanitizeRunLog(log, now) });
    }
    return verdict;
  }).catch((e) => {
    console.warn("[TL;DW auto] budget unavailable, pausing:", e);
    return { ok: false, limit: "hour" };
  });
  budgetChain = step;
  return step;
}

// The channel list has one writer: this worker. The panel and the options page
// send intents; each runs alone, on what is in storage at that moment.
let channelChain = Promise.resolve();

function changeChannels(fn) {
  const step = channelChain.then(async () => {
    const ch = channelsLib();
    if (!ch) return { ok: false };
    const { autoChannels } = await api.storage.local.get({ autoChannels: [] });
    return fn(ch, ch.sanitizeChannels(autoChannels));
  }).catch((e) => {
    console.warn("[TL;DW auto] channel list change failed:", e);
    return { ok: false };
  });
  channelChain = step;
  return step;
}

// Writes back the *sanitized* list: an entry today's rules reject (a key form from
// a newer version, after a downgrade) is dropped on the next add or remove. Unknown
// fields on accepted entries are kept.
function addChannel(msg) {
  return changeChannels(async (ch, list) => {
    if (!ch.isValidKey(msg.key) || typeof msg.name !== "string") return { ok: false };
    if (list.some((c) => c.key === msg.key)) return { ok: true, result: "exists" };
    if (list.length >= ch.MAX_CHANNELS) return { ok: true, result: "full" };
    const entry = { key: msg.key, name: String(msg.name).trim().slice(0, ch.MAX_NAME),
                    addedAt: Date.now() };
    if (!entry.name) return { ok: false };
    if (typeof msg.href === "string" && msg.href.length <= 300) entry.href = msg.href;
    await api.storage.local.set({ autoChannels: [...list, entry] });
    return { ok: true, result: "ok" };
  });
}

function removeChannel(msg) {
  return changeChannels(async (_ch, list) => {
    const next = list.filter((c) => c.key !== msg.key);
    if (next.length !== list.length) await api.storage.local.set({ autoChannels: next });
    return { ok: true, result: "removed" };
  });
}

// Not async: a listener that returns a Promise closes the channel early on older
// Chrome. sendResponse + `return true` for the three messages handled here, and
// nothing at all for anything else -- or from anyone else.
api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!sender || sender.id !== api.runtime.id || !msg) return undefined;
  if (msg.type === "channels:add") {
    addChannel(msg).then(sendResponse);
    return true;
  }
  if (msg.type === "channels:remove") {
    removeChannel(msg).then(sendResponse);
    return true;
  }
  if (msg.type === "openOptions") {
    Promise.resolve(api.runtime.openOptionsPage()).then(
      () => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  }
  return undefined;
});
