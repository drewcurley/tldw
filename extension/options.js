const api = globalThis.browser ?? globalThis.chrome;  // promises in both Firefox & Chrome

const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "", voice: "amy" };

// Fallback list if the server isn't reachable (kept in sync with audio.VOICES).
const VOICE_FALLBACK = [
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

function fillVoices(list, selected) {
  const sel = document.getElementById("voice");
  sel.innerHTML = list.map((v) => `<option value="${v.id}">${v.label}</option>`).join("");
  if (list.some((v) => v.id === selected)) sel.value = selected;
}

async function load() {
  const s = await api.storage.local.get(DEFAULTS);
  document.getElementById("serverUrl").value = s.serverUrl || DEFAULTS.serverUrl;
  document.getElementById("token").value = s.token || "";
  fillVoices(VOICE_FALLBACK, s.voice);
  // Prefer the live server list so new voices show up without an extension update.
  try {
    const resp = await fetch((s.serverUrl || DEFAULTS.serverUrl).replace(/\/+$/, "") + "/voices");
    if (resp.ok) {
      const data = await resp.json();
      if (Array.isArray(data.voices) && data.voices.length) fillVoices(data.voices, s.voice);
    }
  } catch (_) {}
}

// Preview plays a short sample of the highlighted voice. The options page is an
// extension origin, so it can call the server directly with whatever token is in the
// field — no need to save first just to audition a voice.
const previewCache = new Map();          // voice -> object URL

async function preview() {
  const btn = document.getElementById("preview");
  const note = document.getElementById("previewStatus");
  const sample = document.getElementById("sample");
  const voice = document.getElementById("voice").value;
  const token = document.getElementById("token").value.trim();
  note.textContent = "";
  if (!token) { note.textContent = "Paste the token first."; return; }
  if (previewCache.has(voice)) { sample.src = previewCache.get(voice); sample.play(); return; }
  const serverUrl = (document.getElementById("serverUrl").value.trim()
    || DEFAULTS.serverUrl).replace(/\/+$/, "");
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const resp = await fetch(serverUrl + "/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ voice }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).error || ""; } catch (_) {}
      note.textContent = resp.status === 401
        ? "Token mismatch — check the token from `tldw serve`."
        : (detail || `Preview failed (${resp.status}).`);
      return;
    }
    const url = URL.createObjectURL(await resp.blob());
    previewCache.set(voice, url);
    sample.src = url;
    sample.play().catch(() => {});
  } catch (_) {
    note.textContent = "Can't reach the server. Is `tldw serve` running?";
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Preview";
  }
}

document.getElementById("preview").addEventListener("click", preview);

document.getElementById("save").addEventListener("click", async () => {
  const serverUrl = document.getElementById("serverUrl").value.trim() || DEFAULTS.serverUrl;
  const token = document.getElementById("token").value.trim();
  const voice = document.getElementById("voice").value || DEFAULTS.voice;
  await api.storage.local.set({ serverUrl, token, voice });
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1500);
});

load();
