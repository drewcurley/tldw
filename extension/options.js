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
          + "configured (tldw config set llm_cmd ... or TLDW_LLM_CMD).";
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
  await api.storage.local.set({ serverUrl, token, voice, closeAction, model,
                                segmentRatio, whisper });
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1500);
});

load();

// --- Automatic summaries ------------------------------------------------------------
//
// Saved as it changes, not by Save: turning it on asks Chrome for youtube.com
// access, and that request has to come from the click itself. The channel list is
// never written here -- the worker is its one writer; this page sends intents and
// redraws from storage.

const autoLib = () => {
  const ch = globalThis.TLDW_CHANNELS;
  return ch && typeof ch.channelKey === "function" ? ch : null;
};

function autoSupported() {
  return !!(autoLib() && api.permissions && typeof api.permissions.request === "function"
    && api.scripting && typeof api.scripting.registerContentScripts === "function");
}

function autoSay(text) {
  document.getElementById("autoStatus").textContent = text;
}

// Without the shared helpers there's nothing to show. Without the permission API,
// only the switch goes: the list can still be read and pruned.
function showAutoUnsupported() {
  document.getElementById("autoUnsupported").hidden = false;
  if (autoLib()) document.getElementById("autoToggle").hidden = true;
  else document.getElementById("autoControls").hidden = true;
}

// Ticked only when the option is on AND the access is still there: access revoked
// in chrome://extensions shows as unticked, with the reason.
async function refreshAutoToggle() {
  const box = document.getElementById("autoRun");
  const ch = autoLib();
  const { autoRun } = await api.storage.local.get({ autoRun: false });
  let granted = false;
  try { granted = await api.permissions.contains({ origins: [ch.ORIGIN] }); } catch (_) {}
  box.checked = autoRun === true && granted;
  const revoked = "Automatic summaries are paused: TL;DW no longer has access to "
    + "youtube.com. Tick the box to allow it again.";
  if (autoRun === true && !granted) autoSay(revoked);
  else if (document.getElementById("autoStatus").textContent === revoked) autoSay("");
}

// `click`, not `change`: Firefox honours a permission request only when it's made
// synchronously in a user-action handler. By the time this runs `checked` has
// already flipped; it is held off until the answer is in.
function onAutoToggle(e) {
  const box = e.currentTarget || document.getElementById("autoRun");
  const ch = autoLib();
  if (box.checked) {
    box.checked = false;
    box.disabled = true;
    let request;
    try {
      request = api.permissions.request({ origins: [ch.ORIGIN] });   // before any await
    } catch (err) {
      request = Promise.reject(err);
    }
    Promise.resolve(request).then(async (granted) => {
      if (granted) {
        await api.storage.local.set({ autoRun: true });
        box.checked = true;
        autoSay("On. Open or reload a YouTube tab to start.");
      } else {
        box.checked = false;
        autoSay("Automatic summaries need access to youtube.com, which wasn't given. "
          + "Nothing changed.");
      }
    }, () => {
      // The origin can't be requested here at all: not a refusal, no support.
      showAutoUnsupported();
    }).catch(() => {}).then(() => { box.disabled = false; });
    return;
  }
  Promise.resolve(api.storage.local.set({ autoRun: false })).catch(() => {});
  Promise.resolve(api.permissions.remove({ origins: [ch.ORIGIN] })).catch(() => {});
  autoSay("Off.");
}

// The list, as text. Focus survives a redraw: whichever Remove button had it keeps
// it (by key), so a storage update arriving mid-keyboard-use doesn't drop it.
function renderChannels(list) {
  const ul = document.getElementById("autoChannels");
  const active = document.activeElement;
  const focusedKey = active && active.getAttribute && ul.contains(active)
    ? active.getAttribute("data-key") : null;
  while (ul.firstChild) ul.removeChild(ul.firstChild);
  list.forEach((c, i) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "cname";
    name.textContent = c.name;
    const key = document.createElement("span");
    key.className = "ckey";
    key.textContent = c.key;
    name.appendChild(key);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", "Remove " + c.name);
    remove.setAttribute("data-key", c.key);
    remove.addEventListener("click", () => removeAutoChannel(c, i, remove));
    li.appendChild(name);
    li.appendChild(remove);
    ul.appendChild(li);
  });
  document.getElementById("autoEmpty").hidden = list.length > 0;
  if (focusedKey) {
    const again = Array.from(ul.querySelectorAll("button"))
      .find((b) => b.getAttribute("data-key") === focusedKey);
    if (again) again.focus();
  }
}

async function drawChannelsFromStorage() {
  const { autoChannels } = await api.storage.local.get({ autoChannels: [] });
  const list = autoLib().sanitizeChannels(autoChannels);
  renderChannels(list);
  return list;
}

function removeAutoChannel(channel, index, button) {
  // One request per press: the button is off until the worker answers.
  if (button) button.disabled = true;
  const failed = () => {
    if (button) button.disabled = false;
    autoSay("Couldn't remove " + channel.name + ". Try again.");
  };
  let reply;
  try {
    reply = api.runtime.sendMessage({ type: "channels:remove", key: channel.key });
  } catch (_) { failed(); return Promise.resolve(); }
  return Promise.resolve(reply).then(async (r) => {
    if (!r || !r.ok) { failed(); return; }
    const list = await drawChannelsFromStorage();
    // Focus to where the removed row was: the next Remove, or the heading.
    const buttons = document.querySelectorAll("#autoChannels button");
    if (!list.length || !buttons.length) document.getElementById("autoHeading").focus();
    else buttons[Math.min(index, buttons.length - 1)].focus();
    autoSay("Removed " + channel.name + ".");
  }, failed);
}

function initAuto() {
  const lib = autoLib();
  if (!lib) { showAutoUnsupported(); return; }
  const supported = autoSupported();
  if (!supported) showAutoUnsupported();
  if (supported) {
    document.getElementById("autoRun").addEventListener("click", onAutoToggle);
    refreshAutoToggle().catch(() => {});
  }
  drawChannelsFromStorage().catch(() => {});
  api.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.autoChannels) renderChannels(lib.sanitizeChannels(changes.autoChannels.newValue));
    if (changes.autoRun && supported) refreshAutoToggle().catch(() => {});
  });
  if (!supported) return;
  // Access granted or revoked while this page is open shows straight away.
  if (api.permissions.onAdded) api.permissions.onAdded.addListener(() => { refreshAutoToggle().catch(() => {}); });
  if (api.permissions.onRemoved) api.permissions.onRemoved.addListener(() => { refreshAutoToggle().catch(() => {}); });
}

initAuto();
