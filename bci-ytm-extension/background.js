// 服務工作者：第一次安裝時把預設值灌入 storage
const DEFAULTS = { wsUrl: "ws://localhost:8765", enabled: true };

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(DEFAULTS, (existing) => {
    chrome.storage.local.set({ ...DEFAULTS, ...existing });
  });
});
