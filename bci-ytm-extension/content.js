// ──────────────────────────────────────────────────────────
// BCI YT Music Controller — content script
// 1. 連 ws://localhost:8765 收 BCI 後端訊息
// 2. 注入浮動 overlay 顯示機率條 + 連續眨眼持續時間
// 3. 收到 action → 直接 click YT Music 頁面播放鈕
// 動作對應（依「連續快速眨眼」的持續時間）：
//   <1s = 忽略   1-2.5s = 播放/暫停   2.5-5s = 下一首   5-8s = 上一首
//
// 情境模式（兩個分支，皆在擴充內執行，讀後端 proba 串流判斷）：
//   過濾模式：目標狀態(專注/放鬆)持續偏離 → 自動跳下一首
//   歌單庫模式：偵測到穩定情境 → 自動切到該情境歌單（隨機挑）
//   兩者都可用下方手動按鈕直接切歌單。歌單 URL 在 PLAYLISTS 設定。
// ──────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  wsUrl: "ws://localhost:8765",
  enabled: true,
};

// ── 情境式歌單設定（在這裡填你的 YT Music 歌單連結）────────────
// 每個情境可放多個歌單 URL，歌單庫模式會隨機挑一個。
const PLAYLISTS = {
  focus: [
    // 例： "https://music.youtube.com/playlist?list=PLxxxxxxxx",
    "https://music.youtube.com/playlist?list=RDCLAK5uy_kmPRjHDECIcGm4Dy9bdF7AmRR_5T6QHfg",
  ],
  relax: [
    "https://music.youtube.com/playlist?list=RDCLAK5uy_kLWIr9gv1XLlPbaDS965-Db4TrBoUTxQ8",
  ],
};

// 情境模式參數（秒）
const SCENARIO = {
  mismatchSec: 8,    // 過濾模式：偏離目標狀態持續幾秒就跳下一首
  switchSec: 20,     // 歌單庫模式：狀態穩定幾秒就切該情境歌單
  cooldownSec: 15,   // 切歌單/跳歌後冷卻，避免狂切
};

let config = { ...DEFAULT_CONFIG };
let ws = null;
let reconnectTimer = null;
let overlayEl = null;
let toastEl = null;
let reportModalEl = null;
let recentEvents = [];
let llmAvailable = false;

// 情境模式狀態
let scenarioMode = "off";       // "off" | "filter" | "library"
let targetState = "focus";      // "focus" | "relax"
let probaWindow = [];           // 最近 proba 樣本 [{t, relax, focus}]
let lastScenarioAction = 0;     // 上次切歌/跳歌時間戳(ms)
let lastLibrarySwitch = "";     // 上次切到的情境，避免重複切

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

    <div class="bci-scenario-section">
      <div class="bci-scenario-head">
        <span class="bci-scenario-title">🎵 情境模式</span>
        <span class="bci-scenario-status" id="bci-scenario-status">關閉</span>
      </div>
      <div class="bci-seg" id="bci-mode-seg">
        <button data-mode="off" class="bci-seg-btn bci-seg-on">關閉</button>
        <button data-mode="filter" class="bci-seg-btn">過濾</button>
        <button data-mode="library" class="bci-seg-btn">歌單庫</button>
      </div>
      <div class="bci-seg" id="bci-target-seg">
        <span class="bci-seg-label">目標</span>
        <button data-target="focus" class="bci-seg-btn bci-seg-on">專注</button>
        <button data-target="relax" class="bci-seg-btn">放鬆</button>
      </div>
      <div class="bci-scenario-manual">
        <button class="bci-manual-btn" data-pl="focus">▶ 專注歌單</button>
        <button class="bci-manual-btn" data-pl="relax">▶ 放鬆歌單</button>
      </div>
    </div>

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

  // 情境模式切換
  overlayEl.querySelectorAll("#bci-mode-seg .bci-seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      scenarioMode = b.dataset.mode;
      probaWindow = []; lastLibrarySwitch = ""; lastScenarioAction = 0;
      overlayEl.querySelectorAll("#bci-mode-seg .bci-seg-btn")
        .forEach((x) => x.classList.toggle("bci-seg-on", x === b));
      const st = document.getElementById("bci-scenario-status");
      st.textContent = scenarioMode === "off" ? "關閉"
        : scenarioMode === "filter" ? "過濾模式啟動" : "歌單庫模式啟動";
    });
  });
  // 目標狀態切換
  overlayEl.querySelectorAll("#bci-target-seg .bci-seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      targetState = b.dataset.target;
      probaWindow = [];
      overlayEl.querySelectorAll("#bci-target-seg .bci-seg-btn")
        .forEach((x) => x.classList.toggle("bci-seg-on", x === b));
    });
  });
  // 手動切歌單按鈕
  overlayEl.querySelectorAll(".bci-manual-btn").forEach((b) => {
    b.addEventListener("click", () => switchToPlaylist(b.dataset.pl));
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

  // ── 情境模式：累積狀態並評估 ───────────────────────────
  evaluateScenario(msg);
}

// ── 情境式歌單切換邏輯 ──────────────────────────────────────
function evaluateScenario(msg) {
  if (scenarioMode === "off") return;
  // 眨眼當下不納入狀態判斷（眨眼會污染 Focus/Relax 判讀）
  if (msg.is_blinking) return;

  const now = Date.now();
  probaWindow.push({ t: now, relax: msg.relax, focus: msg.focus });
  probaWindow = probaWindow.filter((s) => now - s.t <= 30000);

  const statusEl = document.getElementById("bci-scenario-status");
  const cooldownLeft = SCENARIO.cooldownSec * 1000 - (now - lastScenarioAction);
  if (lastScenarioAction && cooldownLeft > 0) {
    if (statusEl) statusEl.textContent = `冷卻中…${Math.ceil(cooldownLeft / 1000)}s`;
    return;
  }

  if (scenarioMode === "filter") {
    const winMs = SCENARIO.mismatchSec * 1000;
    const recent = probaWindow.filter((s) => now - s.t <= winMs);
    if (recent.length < 4 || (now - recent[0].t) < winMs * 0.8) {
      if (statusEl) statusEl.textContent = "觀察中…";
      return;
    }
    const avgTarget = recent.reduce((a, s) => a + s[targetState], 0) / recent.length;
    const tName = targetState === "focus" ? "專注" : "放鬆";
    if (avgTarget < 0.4) {
      doAction("next");
      showToast(`🎯 這首沒讓你${tName} → 跳下一首`);
      pushEvent(`過濾跳歌`, `${(avgTarget*100).toFixed(0)}%`);
      lastScenarioAction = now;
      probaWindow = [];
      if (statusEl) statusEl.textContent = `已跳歌（${tName}度 ${(avgTarget*100).toFixed(0)}%）`;
    } else if (statusEl) {
      statusEl.textContent = `${tName}度 ${(avgTarget*100).toFixed(0)}%（保留）`;
    }
  } else if (scenarioMode === "library") {
    const winMs = SCENARIO.switchSec * 1000;
    const recent = probaWindow.filter((s) => now - s.t <= winMs);
    if (recent.length < 6 || (now - recent[0].t) < winMs * 0.8) {
      if (statusEl) statusEl.textContent = "判讀情境中…";
      return;
    }
    const avgFocus = recent.reduce((a, s) => a + s.focus, 0) / recent.length;
    const avgRelax = recent.reduce((a, s) => a + s.relax, 0) / recent.length;
    let detected = null;
    if (avgFocus > 0.55) detected = "focus";
    else if (avgRelax > 0.55) detected = "relax";
    if (detected && detected !== lastLibrarySwitch) {
      switchToPlaylist(detected);
      lastLibrarySwitch = detected;
      lastScenarioAction = now;
      probaWindow = [];
    } else if (statusEl) {
      const dn = detected === "focus" ? "專注" : detected === "relax" ? "放鬆" : "判讀中";
      statusEl.textContent = `情境：${dn}`;
    }
  }
}

function switchToPlaylist(state) {
  const list = PLAYLISTS[state] || [];
  const name = state === "focus" ? "專注" : "放鬆";
  if (list.length === 0) {
    showToast(`⚠ 沒設定${name}歌單（content.js PLAYLISTS）`);
    return;
  }
  const url = list[Math.floor(Math.random() * list.length)];
  showToast(`🎵 切換${name}歌單`);
  pushEvent(`切${name}歌單`, "");
  window.location.href = url;  // 同分頁導航，會自動開始播放
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
