const DEFAULTS = { serverUrl: "http://127.0.0.1:8765", token: "" };

async function load() {
  const s = await chrome.storage.local.get(DEFAULTS);
  document.getElementById("serverUrl").value = s.serverUrl || DEFAULTS.serverUrl;
  document.getElementById("token").value = s.token || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const serverUrl = document.getElementById("serverUrl").value.trim() || DEFAULTS.serverUrl;
  const token = document.getElementById("token").value.trim();
  await chrome.storage.local.set({ serverUrl, token });
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1500);
});

load();
