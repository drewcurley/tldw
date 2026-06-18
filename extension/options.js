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
  const s = await chrome.storage.local.get(DEFAULTS);
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

document.getElementById("save").addEventListener("click", async () => {
  const serverUrl = document.getElementById("serverUrl").value.trim() || DEFAULTS.serverUrl;
  const token = document.getElementById("token").value.trim();
  const voice = document.getElementById("voice").value || DEFAULTS.voice;
  await chrome.storage.local.set({ serverUrl, token, voice });
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1500);
});

load();
