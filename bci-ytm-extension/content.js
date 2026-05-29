// ──────────────────────────────────────────────────────────
// BCI YT Music Controller — content script
// 1. 連 ws://localhost:8765 收 BCI 後端訊息
// 2. 注入浮動 overlay 顯示機率條 + 連續眨眼持續時間
// 3. 收到 action → 直接 click YT Music 頁面播放鈕
// 動作對應（依「連續快速眨眼」的持續時間）：
//   <1s = 忽略   1-2.5s = 播放/暫停   2.5-5s = 下一首   5-8s = 上一首
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
let reportModalEl = null;
let recentEvents = [];
let llmAvailable = false;

// 預設 bucket，後端 status 訊息會覆蓋
let buckets = [1.0, 2.5, 5.0, 8.0];

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

    <div class="bci-hold-section">
      <div class="bci-hold-row">
        <span class="bci-label">連續眨眼時長</span>
        <span class="bci-hold-time" id="bci-hold-time">0.0s</span>
      </div>
      <div class="bci-bucket-bar" id="bci-bucket-bar">
        <div class="bci-bucket bci-bucket-ignore" data-bucket="0"><span>忽略</span></div>
        <div class="bci-bucket bci-bucket-pp"     data-bucket="1"><span>▶❚❚</span></div>
        <div class="bci-bucket bci-bucket-next"   data-bucket="2"><span>⏭</span></div>
        <div class="bci-bucket bci-bucket-prev"   data-bucket="3"><span>⏮</span></div>
        <div class="bci-bucket bci-bucket-ignore" data-bucket="4"><span>忽略</span></div>
        <div class="bci-bucket-marker" id="bci-bucket-marker"></div>
      </div>
      <div class="bci-bucket-labels" id="bci-bucket-labels">
        <span>0</span><span>1.0</span><span>2.5</span><span>5.0</span><span>8.0s</span>
      </div>
    </div>

    <div class="bci-events"></div>

    <div class="bci-ai-section" id="bci-ai-section">
      <div class="bci-ai-head">
        <span class="bci-ai-title">🧠 AI 教練</span>
        <button class="bci-ai-btn" id="bci-report-btn" disabled>產生報告</button>
      </div>
      <div class="bci-ai-insight" id="bci-ai-insight">等待 AI 即時解讀…</div>
    </div>
  `;
  document.body.appendChild(overlayEl);
  if (!config.enabled) overlayEl.classList.add("bci-paused");

  // 報告按鈕
  overlayEl.querySelector("#bci-report-btn").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "request_report" }));
      showReportModal({ loading: true });
    }
  });

  toastEl = document.createElement("div");
  toastEl.id = "bci-toast";
  document.body.appendChild(toastEl);

  // 報告 modal（預設隱藏）
  reportModalEl = document.createElement("div");
  reportModalEl.id = "bci-report-modal";
  reportModalEl.style.display = "none";
  document.body.appendChild(reportModalEl);
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
  if (msg.type === "action") doAction(msg.action, msg.hold_duration);
  if (msg.type === "status") {
    setStatus(msg.connected, msg.connected ? "推論中" : "後端停止");
    if (msg.buckets && msg.buckets.length === 4) {
      buckets = msg.buckets;
      updateBucketLabels();
    }
  }
  if (msg.type === "llm_status") setLlmAvailable(msg.available);
  if (msg.type === "llm_insight") showInsight(msg.text);
  if (msg.type === "llm_report") showReportModal(msg);
}

function setLlmAvailable(available) {
  llmAvailable = available;
  const btn = document.getElementById("bci-report-btn");
  const insightEl = document.getElementById("bci-ai-insight");
  if (btn) btn.disabled = !available;
  if (insightEl && !available) {
    insightEl.textContent = "AI 教練未啟用（後端未設 OPENAI_API_KEY）";
    insightEl.classList.add("bci-ai-off");
  }
}

function showInsight(text) {
  const el = document.getElementById("bci-ai-insight");
  if (!el) return;
  el.textContent = "💬 " + text;
  el.classList.remove("bci-ai-off");
  el.classList.add("bci-ai-flash");
  setTimeout(() => el.classList.remove("bci-ai-flash"), 600);
}

function showReportModal(msg) {
  if (!reportModalEl) return;
  reportModalEl.style.display = "flex";
  if (msg.loading) {
    reportModalEl.innerHTML = `
      <div class="bci-report-card">
        <div class="bci-report-loading">🧠 AI 正在分析這次的腦波 session…</div>
      </div>`;
    return;
  }
  if (msg.error) {
    reportModalEl.innerHTML = `
      <div class="bci-report-card">
        <button class="bci-report-close">×</button>
        <div class="bci-report-err">${msg.error}</div>
      </div>`;
  } else {
    const s = msg.stats || {};
    const score = msg.focus_score ?? "—";
    reportModalEl.innerHTML = `
      <div class="bci-report-card">
        <button class="bci-report-close">×</button>
        <div class="bci-report-title">🧠 AI 專注力報告</div>
        <div class="bci-report-score">
          <div class="bci-score-num">${score}</div>
          <div class="bci-score-label">專注度評分 / 100</div>
        </div>
        <div class="bci-report-row"><b>總結</b>${msg.summary || ""}</div>
        <div class="bci-report-row"><b>觀察</b>${msg.observation || ""}</div>
        <div class="bci-report-row bci-report-sug"><b>建議</b>${msg.suggestion || ""}</div>
        <div class="bci-report-meta">
          時長 ${s.duration_min ?? "?"} 分 ·
          Focus ${Math.round((s.focus_pct ?? 0)*100)}% ·
          操作 ▶${s.n_play_pause ?? 0} ⏭${s.n_next ?? 0} ⏮${s.n_prev ?? 0}
        </div>
      </div>`;
  }
  const close = reportModalEl.querySelector(".bci-report-close");
  if (close) close.addEventListener("click", () => { reportModalEl.style.display = "none"; });
}

function updateBucketLabels() {
  const labels = document.getElementById("bci-bucket-labels");
  if (!labels) return;
  labels.innerHTML = `<span>0</span>` + buckets.map((b, i) =>
    `<span>${b.toFixed(1)}${i === 3 ? 's' : ''}</span>`
  ).join("");
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

  const d = msg.hold_duration || 0;
  const isHolding = msg.is_blinking;

  // 時間數字
  const timeEl = document.getElementById("bci-hold-time");
  timeEl.textContent = `${d.toFixed(1)}s`;
  timeEl.style.color = isHolding ? "#1ed760" : "#888";

  // bucket marker 位置（用 % 對映到時間軸 0..bucket_max）
  const marker = document.getElementById("bci-bucket-marker");
  const max = buckets[3];
  const pct = Math.min(d / max, 1.0) * 100;
  marker.style.left = `${pct}%`;
  marker.style.opacity = isHolding ? "1" : "0";

  // 高亮目前在哪個 bucket
  const buckEls = overlayEl.querySelectorAll(".bci-bucket");
  let activeIdx = -1;
  if (isHolding) {
    if      (d < buckets[0]) activeIdx = 0;
    else if (d < buckets[1]) activeIdx = 1;
    else if (d < buckets[2]) activeIdx = 2;
    else if (d < buckets[3]) activeIdx = 3;
    else                     activeIdx = 4;
  }
  buckEls.forEach((el, i) => {
    el.classList.toggle("bci-bucket-active", i === activeIdx);
  });
}

function doAction(action, holdDuration) {
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
  const dStr = holdDuration ? `${holdDuration.toFixed(1)}s` : "";
  if (clicked) {
    showToast(`✓ ${niceName}（${dStr}）`);
    pushEvent(niceName, dStr);
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

function pushEvent(action, dStr) {
  const ts = new Date().toLocaleTimeString("zh-TW", { hour12: false });
  recentEvents.unshift({ ts, action, dStr });
  recentEvents = recentEvents.slice(0, 3);
  const box = overlayEl?.querySelector(".bci-events");
  if (!box) return;
  box.innerHTML = recentEvents
    .map(e => `<div class="bci-event">${e.ts} <span class="bci-action">${e.action}</span> <span class="bci-count">${e.dStr}</span></div>`)
    .join("");
}
