const api = globalThis.browser ?? globalThis.chrome;  // promises in both Firefox & Chrome

const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "", voice: "amy",
                   closeAction: "continue", model: "opus",
                   segmentRatio: "auto", whisper: "ask" };
// Keep in step with the server's WHISPER_POLICIES; it refuses anything else.
const WHISPER_POLICIES = ["off", "ask", "always"];
const RATIOS = ["auto", "0.25", "0.4", "0.6"];
const MODELS = ["opus", "sonnet"];     // keep in step with the server's allowlist

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

// --- T-020 options: cover listed words (BEGIN) -----------------------------------
// Pure, so tests/js/options_mute_checks.js can run it. The normaliser and the
// defaults come from mute_words.js (loaded first by options.html), the same single
// copy content.js uses.
const MUTE_SOUND_CHOICES = ["bleep", "mute", "moo"];

function muteSoundValue(v) {
  return MUTE_SOUND_CHOICES.includes(v) ? v : "bleep";
}

// What the textarea shows: the stored list, or the default when never set.
function muteWordsText(stored, defaults) {
  if (typeof stored === "string") return stored;
  return Array.isArray(defaults) ? defaults.join("\n") : "";
}

// What Save does with the textarea: an empty box saves "" (off); a list equal to
// the default removes the key, so a later change to the shipped default reaches
// this user; anything else saves the normalised list.
function muteWordsSave(text, defaults, normalise) {
  const mine = normalise(text);
  if (!mine.length) return { set: "" };
  const def = normalise(Array.isArray(defaults) ? defaults.join("\n") : "");
  if (mine.join("\n") === def.join("\n")) return { remove: true };
  return { set: mine.join("\n") };
}
// --- T-020 options: cover listed words (END) -------------------------------------

function fillVoices(list, selected) {
  const sel = document.getElementById("voice");
  sel.innerHTML = list.map((v) => `<option value="${v.id}">${v.label}</option>`).join("");
  if (list.some((v) => v.id === selected)) sel.value = selected;
}

async function load() {
  const s = await api.storage.local.get(DEFAULTS);
  document.getElementById("serverUrl").value = s.serverUrl || DEFAULTS.serverUrl;
  document.getElementById("token").value = s.token || "";
  document.getElementById("closeAction").value =
    s.closeAction === "abort" ? "abort" : DEFAULTS.closeAction;
  document.getElementById("whisper").value =
    WHISPER_POLICIES.includes(s.whisper) ? s.whisper : DEFAULTS.whisper;
  document.getElementById("segmentRatio").value =
    RATIOS.includes(String(s.segmentRatio)) ? String(s.segmentRatio)
      : DEFAULTS.segmentRatio;
  document.getElementById("model").value =
    MODELS.includes(s.model) ? s.model : DEFAULTS.model;
  fillVoices(VOICE_FALLBACK, s.voice);
  const mute = await api.storage.local.get(["muteWords", "muteSound"]);
  document.getElementById("muteWords").value =
    muteWordsText(mute.muteWords, globalThis.TLDW_MUTE_DEFAULTS);
  document.getElementById("muteSound").value = muteSoundValue(mute.muteSound);
  // The model picker is Claude-CLI specific. Ask the server what it's actually
  // talking to rather than assuming, so a bring-your-own-model setup doesn't show
  // a control that does nothing.
  try {
    const base = (s.serverUrl || DEFAULTS.serverUrl).replace(/\/+$/, "");
    const health = await (await fetch(base + "/health")).json();
    const backend = health.backend || {};
    const sel = document.getElementById("model");
    const note = document.getElementById("modelNote");
    if (backend.kind && backend.kind !== "claude") {
      sel.disabled = true;
      if (note) {
        note.textContent =
          " Your server is configured to use a custom model command, so this "
          + "setting doesn't apply. Change the model where that command is "
          + "configured (tldw config llm_cmd ... or TLDW_LLM_CMD).";
      }
    }
  } catch (_) {}

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

// Restore refills the box and writes nothing; Save then removes the key.
document.getElementById("muteRestore").addEventListener("click", () => {
  document.getElementById("muteWords").value =
    muteWordsText(undefined, globalThis.TLDW_MUTE_DEFAULTS);
});

document.getElementById("save").addEventListener("click", async () => {
  const serverUrl = document.getElementById("serverUrl").value.trim() || DEFAULTS.serverUrl;
  const token = document.getElementById("token").value.trim();
  const voice = document.getElementById("voice").value || DEFAULTS.voice;
  const closeAction =
    document.getElementById("closeAction").value === "abort" ? "abort" : "continue";
  const model = MODELS.includes(document.getElementById("model").value)
    ? document.getElementById("model").value : DEFAULTS.model;
  const picked = document.getElementById("segmentRatio").value;
  const segmentRatio = RATIOS.includes(picked) ? picked : DEFAULTS.segmentRatio;
  const whisperPick = document.getElementById("whisper").value;
  const whisper = WHISPER_POLICIES.includes(whisperPick) ? whisperPick
                                                         : DEFAULTS.whisper;
  const muteSound = muteSoundValue(document.getElementById("muteSound").value);
  await api.storage.local.set({ serverUrl, token, voice, closeAction, model,
                                segmentRatio, whisper, muteSound });
  const words = muteWordsSave(document.getElementById("muteWords").value,
    globalThis.TLDW_MUTE_DEFAULTS, globalThis.TLDW_normaliseMuteList);
  if (words.remove) await api.storage.local.remove("muteWords");
  else await api.storage.local.set({ muteWords: words.set });
  const status = document.getElementById("status");
  // Cover listed words picks up muteSound and muteWords while on (rev 5.6), and
  // this tab is where the user is when that happens.
  status.textContent = "Saved. Videos where Cover listed words is on use the new settings right away.";
  setTimeout(() => (status.textContent = ""), 4000);
});

load();
