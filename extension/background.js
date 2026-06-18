// Service worker. The content script opens a long-lived Port and the work happens
// while that port is connected — this keeps the worker alive through the ~20-60s
// summarize (a plain awaited fetch in the click handler would be suspended ~30s in
// and silently never deliver a result). A hard timeout guarantees an answer.

const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "" };
const CLIENT_TIMEOUT_MS = 150000;
const cache = new Map(); // videoId -> payload (best-effort)

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
  const s = await chrome.storage.local.get(DEFAULTS);
  return { serverUrl: s.serverUrl || DEFAULTS.serverUrl, token: s.token || "" };
}

function send(tabId, msg) {
  chrome.tabs.sendMessage(tabId, msg).catch(() => {});
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  const videoId = tab.url && videoIdFromUrl(tab.url);
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  if (!videoId) {
    send(tab.id, { type: "TLDW_ERROR",
      error: "Open a YouTube video (a /watch page) first, then click TL;DW." });
    return;
  }
  send(tab.id, { type: "TLDW_INVOKE", url: tab.url, videoId });
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "tldw") return;
  port.onMessage.addListener(async (msg) => {
    if (!msg || msg.type !== "summarize") return;
    const { url, videoId } = msg;
    if (videoId && cache.has(videoId)) {
      safePost(port, { type: "result", payload: cache.get(videoId), cached: true });
      return;
    }
    const { serverUrl, token } = await getSettings();
    if (!token) {
      safePost(port, { type: "error",
        error: "No server token set. Open the extension's Options and paste the token from `tldw serve`." });
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), CLIENT_TIMEOUT_MS);
    let gotTerminal = false;
    try {
      const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/summarize/stream", {
        method: "POST", signal: ctrl.signal,
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
        body: JSON.stringify({ url }),
      });
      if (!resp.ok) {
        let detail = "";
        try { detail = (await resp.json()).error || ""; } catch (_) {}
        safePost(port, { type: "error", error: httpError(resp.status, detail) });
        return;
      }
      // Stream of NDJSON events: {type:progress|result|error}, one per line.
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
            safePost(port, { type: "progress", message: ev.message });
          } else if (ev.type === "result") {
            gotTerminal = true;
            if (videoId) cache.set(videoId, ev);
            safePost(port, { type: "result", payload: ev });
          } else if (ev.type === "error") {
            gotTerminal = true;
            safePost(port, { type: "error", error: httpError(ev.status, ev.error) });
          }
        }
      }
      if (!gotTerminal) {
        safePost(port, { type: "error",
          error: "The summary stream ended unexpectedly. Try again." });
      }
    } catch (e) {
      if (e.name === "AbortError") {
        safePost(port, { type: "error",
          error: "Summarizing timed out (over 150s). Try again, or use the tldw CLI for long videos." });
      } else {
        safePost(port, { type: "error",
          error: "Can't reach the tldw server. Is it running? In a terminal:  tldw serve" });
      }
    } finally {
      clearTimeout(timer);
    }
  });
});

function safePost(port, msg) {
  try { port.postMessage(msg); } catch (_) {}
}

function httpError(status, detail) {
  if (status === 401) return "Token mismatch. Re-check the token in the extension's Options.";
  if (status === 413) return detail || "This transcript is too long for the browser; use the tldw CLI.";
  if (status === 422) return detail || "This video has no captions/transcript to summarize.";
  if (status === 429) return "Server is busy with another summary. Try again in a moment.";
  if (status === 502) return "Claude summarization failed. Make sure `claude` is logged in, then retry.";
  if (status === 504) return "Summarizing timed out on the server. Try again, or use the CLI for long videos.";
  return detail || ("Server error (" + status + ").");
}
