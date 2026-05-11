const DEFAULTS = { wsUrl: "ws://localhost:8765", enabled: true };

const $ = (id) => document.getElementById(id);

chrome.storage.local.get(DEFAULTS, (cfg) => {
  $("wsUrl").value   = cfg.wsUrl;
  $("enabled").checked = cfg.enabled;
});

$("enabled").addEventListener("change", (e) => {
  chrome.storage.local.set({ enabled: e.target.checked });
});

$("save").addEventListener("click", () => {
  const wsUrl = $("wsUrl").value.trim() || DEFAULTS.wsUrl;
  chrome.storage.local.set({ wsUrl }, () => {
    const btn = $("save");
    btn.textContent = "✓ 已儲存";
    setTimeout(() => (btn.textContent = "儲存"), 1200);
  });
});
