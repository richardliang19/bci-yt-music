// ──────────────────────────────────────────────────────────
// BCI YT Music Controller — content script
// 1. 連 ws://localhost:8765 收 BCI 後端訊息
// 2. 注入浮動 overlay 顯示機率條 + 連續眨眼持續時間
// 3. 收到 action → 直接 click YT Music 頁面播放鈕
// 動作對應（依「連續快速眨眼」的持續時間）：
//   <2s = 忽略   2-4s = 播放/暫停   4-6.5s = 下一首   6.5-9s = 上一首
//
// 混合歌單過濾：一段時間內多數樣本偏離目標狀態(專注/放鬆) → 自動跳下一首
// ──────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  wsUrl: "ws://localhost:8765",
  enabled: true,
};

// 混合歌單過濾參數（秒）
const SCENARIO = {
  mismatchSec: 20,      // 觀察最近幾秒的有效樣本
  targetFloor: 0.40,    // 目標狀態低於此機率視為不符合
  mismatchRatio: 0.70,  // 不符合樣本比例達到此值才跳下一首
  cooldownSec: 20,      // 跳歌後冷卻，避免狂切
};

let config = { ...DEFAULT_CONFIG };
let ws = null;
let reconnectTimer = null;
let overlayEl = null;
let toastEl = null;
let reportModalEl = null;
let recentEvents = [];
let llmAvailable = false;
let audioCtx = null;
let isCalibrating = false;
let trackTimer = null;
let lastTrackKey = "";
let overlayCollapsed = localStorage.getItem("bciOverlayCollapsed") === "1";

// 混合歌單過濾狀態
let scenarioMode = "off";       // "off" | "filter"
let targetState = "focus";      // "focus" | "relax"
let probaWindow = [];           // 最近 proba 樣本 [{t, relax, focus}]
let lastScenarioAction = 0;     // 上次跳歌時間戳(ms)

// 預設 bucket，後端 status 訊息會覆蓋
let buckets = [2.0, 4.0, 6.5, 9.0];

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
    playCue("mode");
    overlayEl?.classList.toggle("bci-paused", !config.enabled);
  }
});

function init() {
  buildOverlay();
  connect();
  if (!trackTimer) trackTimer = setInterval(() => sendTrackUpdate(false), 5000);
}

function buildOverlay() {
  if (document.getElementById("bci-overlay")) return;
  overlayEl = document.createElement("div");
  overlayEl.id = "bci-overlay";
  overlayEl.innerHTML = `
    <header>
      <span class="bci-title">BCI Controller</span>
      <span class="bci-status">
        <span class="bci-led bci-off"></span>
        <span class="bci-status-text">未連線</span>
      </span>
      <button class="bci-collapse-btn" id="bci-collapse-btn" title="收合/展開">−</button>
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

    <div class="bci-calibration" id="bci-calibration" style="display:none">
      <div class="bci-cal-title">個人化校正</div>
      <div class="bci-cal-text" id="bci-cal-text">請連續快速眨眼 5 秒</div>
      <div class="bci-cal-track"><div class="bci-cal-fill" id="bci-cal-fill"></div></div>
    </div>

    <div class="bci-hold-section">
      <div class="bci-hold-row">
        <span class="bci-label">連續眨眼時長</span>
        <span class="bci-hold-time" id="bci-hold-time">0.0s</span>
      </div>
      <button class="bci-calibrate-btn" id="bci-calibrate-btn">手動校正</button>
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
        <span class="bci-scenario-title">🎵 混合歌單過濾</span>
        <span class="bci-scenario-status" id="bci-scenario-status">關閉</span>
      </div>
      <div class="bci-seg" id="bci-mode-seg">
        <button data-mode="off" class="bci-seg-btn bci-seg-on">關閉</button>
        <button data-mode="filter" class="bci-seg-btn">過濾</button>
      </div>
      <div class="bci-seg" id="bci-target-seg">
        <span class="bci-seg-label">目標</span>
        <button data-target="focus" class="bci-seg-btn bci-seg-on">專注</button>
        <button data-target="relax" class="bci-seg-btn">放鬆</button>
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
  setOverlayCollapsed(overlayCollapsed);

  // 報告按鈕
  overlayEl.querySelector("#bci-collapse-btn").addEventListener("click", () => {
    setOverlayCollapsed(!overlayCollapsed);
  });

  overlayEl.querySelector("#bci-report-btn").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "request_report" }));
      showReportModal({ loading: true });
    }
  });

  // 情境模式切換
  overlayEl.querySelector("#bci-calibrate-btn").addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      isCalibrating = true;
      playCue("calibration");
      ws.send(JSON.stringify({ type: "request_calibration" }));
    } else {
      showToast("後端未連線，無法校正");
      playCue("error");
    }
  });

  overlayEl.querySelectorAll("#bci-mode-seg .bci-seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      scenarioMode = b.dataset.mode;
      probaWindow = []; lastScenarioAction = 0;
      playCue("mode");
      overlayEl.querySelectorAll("#bci-mode-seg .bci-seg-btn")
        .forEach((x) => x.classList.toggle("bci-seg-on", x === b));
      const st = document.getElementById("bci-scenario-status");
      st.textContent = scenarioMode === "off" ? "關閉" : "過濾模式啟動";
    });
  });
  // 目標狀態切換
  overlayEl.querySelectorAll("#bci-target-seg .bci-seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      targetState = b.dataset.target;
      probaWindow = [];
      playCue("mode");
      overlayEl.querySelectorAll("#bci-target-seg .bci-seg-btn")
        .forEach((x) => x.classList.toggle("bci-seg-on", x === b));
    });
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
  ws.onopen    = () => {
    setStatus(true, "已連線");
    sendTrackUpdate(true);
  };
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

function setOverlayCollapsed(collapsed) {
  overlayCollapsed = collapsed;
  localStorage.setItem("bciOverlayCollapsed", collapsed ? "1" : "0");
  overlayEl?.classList.toggle("bci-collapsed", collapsed);
  const btn = document.getElementById("bci-collapse-btn");
  if (btn) {
    btn.textContent = collapsed ? "+" : "−";
    btn.title = collapsed ? "展開 BCI 面板" : "收合 BCI 面板";
  }
}

function readText(selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    const text = el?.textContent?.trim();
    if (text) return text.replace(/\s+/g, " ");
  }
  return "";
}

function getCurrentTrackInfo() {
  const title = readText([
    "ytmusic-player-bar .title",
    ".title.ytmusic-player-bar",
    "yt-formatted-string.title",
  ]);
  const byline = readText([
    "ytmusic-player-bar .byline",
    ".byline.ytmusic-player-bar",
    "yt-formatted-string.byline",
    "ytmusic-player-bar .subtitle",
  ]);
  const parts = byline.split(/[•·]/).map((x) => x.trim()).filter(Boolean);
  const playButton = document.querySelector("#play-pause-button, .play-pause-button.ytmusic-player-bar");
  const label = `${playButton?.getAttribute("aria-label") || ""} ${playButton?.getAttribute("title") || ""}`;
  return {
    title,
    artist: parts[0] || byline,
    album: parts.slice(1).join(" • "),
    playing: /pause|暫停/i.test(label) || !/play|播放/i.test(label),
  };
}

function sendTrackUpdate(force) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const track = getCurrentTrackInfo();
  if (!track.title) return;
  const key = `${track.title}|${track.artist}|${track.album}|${track.playing}`;
  if (!force && key === lastTrackKey) return;
  lastTrackKey = key;
  ws.send(JSON.stringify({ type: "track_update", ...track }));
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
    if (msg.calibration) handleCalibration(msg.calibration);
  }
  if (msg.type === "calibration") handleCalibration(msg);
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

function handleCalibration(msg) {
  const box = document.getElementById("bci-calibration");
  const text = document.getElementById("bci-cal-text");
  const fill = document.getElementById("bci-cal-fill");
  if (!box || !text || !fill) return;

  if ((msg.state === "noise" || msg.state === "blink") && "remaining" in msg) {
    isCalibrating = true;
    box.style.display = "block";
    const duration = msg.duration || 5;
    const remaining = Math.max(0, msg.remaining || 0);
    const pct = Math.max(0, Math.min(100, ((duration - remaining) / duration) * 100));
    text.textContent = msg.message || (msg.state === "noise"
      ? `保持穩定、不眨眼，剩下 ${remaining.toFixed(1)}s`
      : `連續快速眨眼，剩下 ${remaining.toFixed(1)}s`);
    fill.style.width = `${pct.toFixed(0)}%`;
    return;
  }

  if (msg.state === "queued") {
    isCalibrating = true;
    box.style.display = "block";
    text.textContent = msg.message || "校正即將開始";
    fill.style.width = "0%";
  } else if (msg.state === "start" || msg.state === "noise" || msg.state === "blink") {
    isCalibrating = true;
    box.style.display = "block";
    text.textContent = msg.message || "請連續快速眨眼 5 秒";
    fill.style.width = "0%";
    playCue("calibration");
  } else if (msg.state === "running") {
    isCalibrating = true;
    box.style.display = "block";
    const duration = msg.duration || 5;
    const remaining = Math.max(0, msg.remaining || 0);
    const pct = Math.max(0, Math.min(100, ((duration - remaining) / duration) * 100));
    text.textContent = `請連續快速眨眼，剩下 ${remaining.toFixed(1)}s`;
    fill.style.width = `${pct.toFixed(0)}%`;
  } else if (msg.state === "done") {
    isCalibrating = false;
    box.style.display = "block";
    text.textContent = msg.message || "校正完成，可以開始控制";
    fill.style.width = "100%";
    playCue("done");
    setTimeout(() => {
      if (!isCalibrating) box.style.display = "none";
    }, 2200);
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
        <div class="bci-report-row"><b>音樂</b>${msg.music_observation || "本次歌曲資料不足，先多聽幾首再分析。"}</div>
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
  if (msg.calibrating) isCalibrating = true;
  const bars = overlayEl.querySelectorAll(".bci-bar-fill");
  const pcts = overlayEl.querySelectorAll(".bci-bar-pct");
  const blinkDisplay = msg.control_mode === "raw"
    ? Math.min((msg.raw_p2p || 0) / (msg.raw_p2p_thresh || 200), 1)
    : msg.blink;
  const vals = [msg.relax, msg.focus, blinkDisplay];
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

// ── 混合歌單過濾邏輯 ──────────────────────────────────────
function evaluateScenario(msg) {
  if (scenarioMode === "off") return;
  if (msg.calibrating || isCalibrating) return;
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

  const winMs = SCENARIO.mismatchSec * 1000;
  const recent = probaWindow.filter((s) => now - s.t <= winMs);
  if (recent.length < 4 || (now - recent[0].t) < winMs * 0.8) {
    if (statusEl) statusEl.textContent = "觀察中…";
    return;
  }
  const badCount = recent.filter((s) => s[targetState] < SCENARIO.targetFloor).length;
  const mismatchRatio = badCount / recent.length;
  const avgTarget = recent.reduce((a, s) => a + s[targetState], 0) / recent.length;
  const tName = targetState === "focus" ? "專注" : "放鬆";
  if (mismatchRatio >= SCENARIO.mismatchRatio) {
    doAction("next");
    showToast(`這首未達${tName}目標，跳下一首`);
    pushEvent(`過濾跳歌`, `不符${(mismatchRatio*100).toFixed(0)}%`);
    lastScenarioAction = now;
    probaWindow = [];
    if (statusEl) {
      statusEl.textContent = `已跳歌（不符 ${(mismatchRatio*100).toFixed(0)}%，${tName}均值 ${(avgTarget*100).toFixed(0)}%）`;
    }
  } else if (statusEl) {
    statusEl.textContent = `${tName}均值 ${(avgTarget*100).toFixed(0)}%，不符 ${(mismatchRatio*100).toFixed(0)}%（保留）`;
  }
}

function doAction(action, holdDuration) {
  if (!config.enabled) {
    showToast(`已停用 → 略過 ${ACTION_NAMES[action] || action}`);
    return;
  }
  // 只在 YT Music 底部播放列(.ytmusic-player-bar)範圍內找按鈕，
  // 避免抓到頁面其他地方的同名元素。每個動作用「class + aria-label」雙重定位，
  // 並驗證抓到的不是別的鈕（修 prev 誤觸 play/pause）。
  const playerBar = document.querySelector("ytmusic-player-bar") || document;

  // aria-label 關鍵字（YT Music 各語系皆含這些英文片段；中文介面通常仍保留英文 aria）
  const ARIA = {
    play_pause: /play|pause|播放|暫停/i,
    next:       /next/i,
    prev:       /previous|prev/i,
  };
  // 明確的 class（最優先），抓不到再退回 aria-label
  const CLASS = {
    play_pause: "#play-pause-button, .play-pause-button",
    next:       ".next-button",
    prev:       ".previous-button",
  };

  function findButton(act) {
    // 1) 先試明確 class（限定在播放列內）
    const byClass = playerBar.querySelector(CLASS[act]);
    if (byClass) return byClass;
    // 2) 退回 aria-label：掃播放列內所有按鈕，比對 aria-label
    const btns = playerBar.querySelectorAll(
      'button, tp-yt-paper-icon-button, yt-button-shape, [role="button"]');
    for (const b of btns) {
      const label = (b.getAttribute("aria-label") || b.getAttribute("title") || "").trim();
      if (!label) continue;
      // prev 一定要先排除 next（"Next" 不含 "previous"，但保險起見嚴格比對）
      if (act === "prev" && ARIA.prev.test(label) && !ARIA.next.test(label)) return b;
      if (act === "next" && ARIA.next.test(label) && !ARIA.prev.test(label)) return b;
      if (act === "play_pause" && ARIA.play_pause.test(label)) return b;
    }
    return null;
  }

  const el = findButton(action);
  let clicked = null;
  if (el) {
    // 防呆：確認抓到的元素 aria-label 不是別的動作（特別擋 prev→play/pause）
    const lbl = (el.getAttribute("aria-label") || el.getAttribute("title") || "").toLowerCase();
    const wrong =
      (action === "prev" && (/play|pause/.test(lbl) || ARIA.next.test(lbl))) ||
      (action === "next" && (/play|pause/.test(lbl) || ARIA.prev.test(lbl)));
    if (wrong) {
      console.warn("[BCI] 防呆攔截：", action, "抓到的元素 aria-label=", lbl, "→ 不點擊");
    } else {
      el.click();
      clicked = action;
    }
  }
  const niceName = ACTION_NAMES[action] || action;
  const dStr = holdDuration ? `${holdDuration.toFixed(1)}s` : "";
  if (clicked) {
    playCue(action);
    showToast(`✓ ${niceName}（${dStr}）`);
    pushEvent(niceName, dStr);
    setTimeout(() => sendTrackUpdate(true), 1200);
  } else {
    playCue("error");
    showToast(`✗ 找不到 ${niceName} 按鈕`);
    console.warn("[BCI] 找不到按鈕", action);
  }
}

function playCue(kind) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const now = audioCtx.currentTime;
    const seq = {
      play_pause: [[220, 0.00, 0.10], [330, 0.10, 0.12]],
      next: [[520, 0.00, 0.08], [780, 0.08, 0.10]],
      prev: [[780, 0.00, 0.08], [520, 0.08, 0.10]],
      mode: [[440, 0.00, 0.07]],
      calibration: [[330, 0.00, 0.08], [440, 0.10, 0.08]],
      done: [[440, 0.00, 0.08], [660, 0.09, 0.10], [880, 0.20, 0.10]],
      error: [[180, 0.00, 0.16]],
    }[kind] || [[440, 0.00, 0.08]];

    seq.forEach(([freq, offset, dur]) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.08, now + offset + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + dur);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(now + offset);
      osc.stop(now + offset + dur + 0.02);
    });
  } catch (err) {
    console.debug("[BCI] audio cue unavailable", err);
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
