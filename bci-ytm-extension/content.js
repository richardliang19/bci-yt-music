// ──────────────────────────────────────────────────────────
// BCI YT Music Controller — content script
// 1. 連 ws://localhost:8765 收 BCI 後端訊息
// 2. 注入浮動 overlay 顯示機率條 + 眨眼事件計數
// 3. 收到 action → 直接 click YT Music 頁面播放鈕
// 動作對應：2 次=播放/暫停  3 次=下一首  4 次=上一首
//          （單次眨眼 = 自然反射，後端會自動忽略）
// ──────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  wsUrl: "ws://localhost:8765",
  enabled: true,
};

let config = { ...DEFAULT_CONFIG };
let ws = null;
let reconnectTimer = null;
let overlayEl = null;
let toastEl = null;
let recentEvents = [];

const ACTION_NAMES = {
  play_pause: "播放/暫停",
  next:       "下一首",
  prev:       "上一首",
};

chrome.storage.local.get(DEFAULT_CONFIG, (loaded) => {
  config = { ...DEFAULT_CONFIG, ...loaded };
  init();
});

chrome.storage.onChanged.addListener((changes) => {
  for (const [k, v] of Object.entries(changes)) config[k] = v.newValue;
  if (changes.wsUrl && ws) { try { ws.close(); } catch (_) {} }
  if ("enabled" in changes) {
    overlayEl?.classList.toggle("bci-paused", !config.enabled);
  }
});

function init() {
  buildOverlay();
  connect();
}

function buildOverlay() {
  if (document.getElementById("bci-overlay")) return;
  overlayEl = document.createElement("div");
  overlayEl.id = "bci-overlay";
  overlayEl.innerHTML = `
    <header>
      <span>BCI Controller</span>
      <span class="bci-status">
        <span class="bci-led bci-off"></span>
        <span class="bci-status-text">未連線</span>
      </span>
    </header>

    <div class="bci-bar-row">
      <span class="bci-bar-label">Relax</span>
      <div class="bci-bar-track"><div class="bci-bar-fill bci-relax"></div></div>
      <span class="bci-bar-pct">0%</span>
    </div>
    <div class="bci-bar-row">
      <span class="bci-bar-label">Focus</span>
      <div class="bci-bar-track"><div class="bci-bar-fill bci-focus"></div></div>
      <span class="bci-bar-pct">0%</span>
    </div>
    <div class="bci-bar-row">
      <span class="bci-bar-label">Blink</span>
      <div class="bci-bar-track"><div class="bci-bar-fill bci-blink"></div></div>
      <span class="bci-bar-pct">0%</span>
    </div>

    <div class="bci-blink-counter">
      <div class="bci-blink-row">
        <span class="bci-label">眨眼事件</span>
        <span class="bci-blink-status" id="bci-burst-status">— 等待中 —</span>
      </div>
      <div class="bci-blink-dots">
        <div class="bci-blink-dot" data-n="1"><span class="bci-dot-tip">忽略</span></div>
        <div class="bci-blink-dot" data-n="2"><span class="bci-dot-tip">▶❚❚</span></div>
        <div class="bci-blink-dot" data-n="3"><span class="bci-dot-tip">⏭</span></div>
        <div class="bci-blink-dot" data-n="4"><span class="bci-dot-tip">⏮</span></div>
      </div>
    </div>

    <div class="bci-events"></div>
  `;
  document.body.appendChild(overlayEl);
  if (!config.enabled) overlayEl.classList.add("bci-paused");

  toastEl = document.createElement("div");
  toastEl.id = "bci-toast";
  document.body.appendChild(toastEl);
}

function connect() {
  clearTimeout(reconnectTimer);
  try {
    ws = new WebSocket(config.wsUrl);
  } catch (e) {
    setStatus(false, "URL 錯誤");
    reconnectTimer = setTimeout(connect, 3000);
    return;
  }
  ws.onopen    = () => setStatus(true, "已連線");
  ws.onmessage = (e) => {
    try { handleMessage(JSON.parse(e.data)); }
    catch (err) { console.warn("[BCI] 解析訊息失敗", err); }
  };
  ws.onerror   = () => setStatus(false, "錯誤");
  ws.onclose   = () => {
    setStatus(false, "斷線重連中…");
    reconnectTimer = setTimeout(connect, 2000);
  };
}

function setStatus(on, text) {
  if (!overlayEl) return;
  const led = overlayEl.querySelector(".bci-led");
  const txt = overlayEl.querySelector(".bci-status-text");
  led.classList.toggle("bci-on", on);
  led.classList.toggle("bci-off", !on);
  txt.textContent = text;
}

function handleMessage(msg) {
  if (!msg || !msg.type) return;
  if (msg.type === "proba")  updateProba(msg);
  if (msg.type === "action") doAction(msg.action, msg.blink_count);
  if (msg.type === "status") setStatus(msg.connected, msg.connected ? "推論中" : "後端停止");
}

function updateProba(msg) {
  if (!overlayEl) return;
  const bars = overlayEl.querySelectorAll(".bci-bar-fill");
  const pcts = overlayEl.querySelectorAll(".bci-bar-pct");
  const vals = [msg.relax, msg.focus, msg.blink];
  vals.forEach((v, i) => {
    bars[i].style.width = `${(v * 100).toFixed(0)}%`;
    pcts[i].textContent = `${(v * 100).toFixed(0)}%`;
  });

  const dots = overlayEl.querySelectorAll(".bci-blink-dot");
  const n = msg.blink_events || 0;
  dots.forEach((d, i) => {
    d.classList.remove("bci-on", "bci-warn", "bci-good");
    if (i < n) {
      // 第 1 顆 = 警告色（會被忽略），第 2-4 顆 = 綠色（會觸發動作）
      d.classList.add(i === 0 ? "bci-warn" : "bci-good", "bci-on");
    }
  });

  const status = overlayEl.querySelector("#bci-burst-status");
  if (msg.is_blinking) {
    status.textContent = "⚡ 偵測中…";
    status.style.color = "#ff7070";
  } else if (n === 0) {
    status.textContent = "— 等待中 —";
    status.style.color = "#888";
  } else if (n === 1) {
    status.textContent = "需 ≥ 2 次才觸發";
    status.style.color = "#f5a623";
  } else {
    const action = ({2: "播放/暫停", 3: "下一首", 4: "上一首"})[Math.min(n, 4)];
    status.textContent = `將觸發：${action}`;
    status.style.color = "#1ed760";
  }
}

function doAction(action, count) {
  if (!config.enabled) {
    showToast(`已停用 → 略過 ${ACTION_NAMES[action] || action}`);
    return;
  }
  const selectors = {
    play_pause: ['#play-pause-button', 'tp-yt-paper-icon-button.play-pause-button',
                 '.play-pause-button.ytmusic-player-bar'],
    next:       ['.next-button.ytmusic-player-bar', 'tp-yt-paper-icon-button.next-button',
                 'yt-button-shape[aria-label*="Next" i] button'],
    prev:       ['.previous-button.ytmusic-player-bar', 'tp-yt-paper-icon-button.previous-button',
                 'yt-button-shape[aria-label*="Previous" i] button'],
  };
  const list = selectors[action];
  if (!list) return;
  let clicked = null;
  for (const sel of list) {
    const el = document.querySelector(sel);
    if (el) { el.click(); clicked = sel; break; }
  }
  const niceName = ACTION_NAMES[action] || action;
  if (clicked) {
    showToast(`✓ ${niceName}（${count} 次眨眼）`);
    pushEvent(niceName, count);
  } else {
    showToast(`✗ 找不到 ${niceName} 按鈕`);
    console.warn("[BCI] 找不到按鈕", action, list);
  }
}

function showToast(text) {
  if (!toastEl) return;
  toastEl.textContent = text;
  toastEl.classList.add("bci-show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.remove("bci-show"), 1500);
}

function pushEvent(action, count) {
  const ts = new Date().toLocaleTimeString("zh-TW", { hour12: false });
  recentEvents.unshift({ ts, action, count });
  recentEvents = recentEvents.slice(0, 3);
  const box = overlayEl?.querySelector(".bci-events");
  if (!box) return;
  box.innerHTML = recentEvents
    .map(e => `<div class="bci-event">${e.ts} <span class="bci-action">${e.action}</span> <span class="bci-count">×${e.count}</span></div>`)
    .join("");
}
