// Service worker: on toolbar click, ensure the modal UI is on the page, then call
// the local tldw server and stream the result back to the content script.

const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "" };
const cache = new Map(); // videoId -> payload (best-effort, lives while SW alive)

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

async function ensureContent(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
}

function send(tabId, msg) {
  chrome.tabs.sendMessage(tabId, msg).catch(() => {});
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  const videoId = tab.url && videoIdFromUrl(tab.url);
  await ensureContent(tab.id);
  if (!videoId) {
    send(tab.id, { type: "TLDW_ERROR",
      error: "Open a YouTube video (a /watch page) first, then click TL;DW." });
    return;
  }
  send(tab.id, { type: "TLDW_LOADING", videoId });

  if (cache.has(videoId)) {
    send(tab.id, { type: "TLDW_RESULT", payload: cache.get(videoId), cached: true });
    return;
  }

  const { serverUrl, token } = await getSettings();
  if (!token) {
    send(tab.id, { type: "TLDW_ERROR",
      error: "No server token set. Open the extension's Options and paste the token printed by `tldw serve`." });
    return;
  }

  try {
    const resp = await fetch(serverUrl.replace(/\/+$/, "") + "/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ url: tab.url }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      send(tab.id, { type: "TLDW_ERROR", error: httpError(resp.status, detail) });
      return;
    }
    const payload = await resp.json();
    cache.set(videoId, payload);
    send(tab.id, { type: "TLDW_RESULT", payload });
  } catch (e) {
    // fetch threw -> server unreachable / CORS / offline
    send(tab.id, { type: "TLDW_ERROR",
      error: "Can't reach the tldw server. Is it running? Start it in a terminal with:  tldw serve" });
  }
});

function httpError(status, detail) {
  if (status === 401) return "Token mismatch. Re-check the token in the extension's Options.";
  if (status === 413) return detail || "This transcript is too long for the browser; use the tldw CLI.";
  if (status === 422) return detail || "This video has no captions/transcript to summarize.";
  if (status === 429) return "Server is busy with another summary. Try again in a moment.";
  if (status === 502) return "Claude summarization failed. Make sure `claude` is logged in, then retry.";
  if (status === 504) return "Summarizing timed out. Try again, or use the CLI for long videos.";
  return detail || ("Server error (" + status + ").");
}
