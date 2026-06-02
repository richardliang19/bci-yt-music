# BCI × YouTube Music — 腦波控制音樂播放系統

> 期末專題 · 基於 BCI 腦波感測之情境式音樂切換與控制系統

戴上 BrainLink Lite 腦波頭環 → 用「**連續快速眨眼幾秒**」直接操作 YouTube Music 網頁版：

| 連續眨眼持續時間 | 動作 |
|---|---|
| < 1 秒 | 自動忽略（過濾自然反射） |
| 1.0 ~ 2.5 秒 | 播放 / 暫停 |
| 2.5 ~ 5.0 秒 | 下一首 |
| 5.0 ~ 8.0 秒 | 重播本曲 |
| > 8 秒 | 自動忽略（防訊號黏住） |

> ⚠️ 是「**快速反覆眨眼**」（像有東西飛來眼睛要連眨擋），不是「閉眼不動」。閉眼不動腦波是 alpha 波會被歸成 Relax 而非 Blink。

模型：MLP（更新版資料集 LOSO 最佳），16 受試者 LOSO 驗證 **87.9% ± 11.6%**。

---

## 系統架構

```
BrainLink Lite (Fp1, 512 Hz, BT)
    ↓
Python 後端  (bci_server.py)
    pyserial(TGAM) 串流 → 4s 滑動視窗 → 12 維特徵
    → RobustScaler → MLP 推論
    → hysteresis 眨眼事件偵測
    → WebSocket 廣播 JSON
    ↓ ws://localhost:8765
Chrome 擴充  (bci-ytm-extension/)
    注入 music.youtube.com 頁面
    → 浮動 overlay (機率條 + 眨眼計數)
    → 收到 action 訊息 → click 頁面播放鈕
```

兩半完全解耦：Python 不知道音樂在播什麼，擴充不知道腦波怎麼來。

---

## AI 教練（OpenAI，選用）

overlay 內建「🧠 AI 教練」面板，由後端呼叫 OpenAI（gpt-4o-mini）：
- **即時解讀**：每 20 秒把最近腦波狀態趨勢丟給 LLM，回一句中文回饋（「你已專注 8 分鐘，繼續保持」）
- **Session 報告**：點「產生報告」→ LLM 分析整段 session → 專注度評分 + 總結/觀察/建議 + 下次歌單方向

**啟用方式**（設環境變數即可，沒設就自動停用，其他功能照常）：
```powershell
$env:OPENAI_API_KEY = "sk-..."
python -X utf8 -u bci_server.py --source brainlink:COM3 --insight-interval 20
```

API key 只讀環境變數，**不寫進任何程式碼、不上 git**。OpenAI 呼叫全在 Python 後端
（避免 key 暴露在瀏覽器擴充原始碼）。成本：gpt-4o-mini 每次解讀/報告約 NT$0.1-1。

---

## 快速開始（給組員）

### 1. 安裝環境
```powershell
pip install -r requirements.txt --user
```

### 2. 載入 Chrome 擴充
1. `chrome://extensions` → 開啟「開發人員模式」
2. 「載入未封裝項目」→ 選 `bci-ytm-extension/` 資料夾
3. 釘選擴充 icon 到工具列

### 3. 不接裝置先測 pipeline
```powershell
# (a) 假資料測 overlay 動起來
python -X utf8 -u bci_server.py --source dummy

# (b) 用 sample_data/ 裡的真實眨眼錄音回放
python -X utf8 -u bci_server.py --source file:sample_data/sample_blink.txt
```
開 https://music.youtube.com → 右下角應出現 BCI overlay，連線燈轉綠。
看到 console 印「· 進入連續眨眼狀態」+「↪ 連續眨眼 X.XXs → 忽略（太長，可能訊號黏住）」就是 pipeline 通了。
（sample_blink.txt 是連續眨眼 20 秒的錄音，model duration 會 > 8s 落在「忽略」桶）

### 4. 接 BrainLink Lite 真機
```powershell
# 先找出 COM port
python -X utf8 -u probe_brainlink.py

# 跑即時系統（COM3 換成你的 port）
python -X utf8 -u bci_server.py --source brainlink:COM3
```

### 5. 操作
**做動作**：連續快速眨眼 N 秒 → 停下來 → 系統依持續時間執行對應動作（1-2.5s 暫停、2.5-5s 下一首、5-8s 重播本曲）。不是閉眼不動！

更詳細請看 [bci-ytm-extension/README.md](bci-ytm-extension/README.md)。

---

## 檔案說明

### 即時推論（運行時用）
| 檔案 | 用途 |
|---|---|
| `bci_server.py` | 主程式：腦波 → 推論 → WebSocket 廣播 |
| `signal_sources.py` | 訊號輸入抽象（dummy / file / folder / brainlink）|
| `llm_coach.py` | OpenAI 即時解讀 + session 報告（選用，需 OPENAI_API_KEY）|
| `probe_brainlink.py` | BrainLink 連線測試 |
| `bci-ytm-extension/` | Chrome 擴充功能完整源碼 |
| `bci_model.pkl` | 訓練好的 MLP 模型（0.2 MB）|
| `requirements.txt` | Python 套件清單 |
| `gui_preview.html` | 擴充功能 GUI 樣子預覽（不需要 server）|
| `sample_data/` | 4 個範例 .txt（沒裝置時測 pipeline 用）|

### 訓練 / 分析（要重新訓練模型才用）
| 檔案 | 用途 |
|---|---|
| `train_compare.py` | 訓練 5 個模型 LOSO 比較 |
| `save_best_model.py` | 用 16 位資料訓練 MLP，存成 .pkl |
| `diagnose_all_update.py` | 全受試者訊號品質診斷（找出該剃除哪幾位）|
| `make_figures.py` | 產生簡報用所有圖表（11 張，matplotlib）|
| `make_report.py` | 產生 HTML 進度報告 |

### 訓練結果（小檔，提供參考）
| 檔案 | 內容 |
|---|---|
| `model_comparison_detail.csv` | 各模型對各受試者的 LOSO 準確率 |
| `best_model_confusion_matrix.csv` | 最佳模型（MLP）混淆矩陣 |

---

## 資料集

更新版資料集 `bci_dataset_114-2_update/`（內含 `bci_dataset_114-2_any/S01..S18`）
**沒有放在 repo 裡**（太大）。請從同學共享連結下載放到 repo 根目錄。
要重新訓練模型才需要這個資料夾；只是要跑 demo 用 `sample_data/` 就夠了。

`train_compare.py` 的 `Config.DATASET_PATH` 預設指向
`bci_dataset_114-2_update/bci_dataset_114-2_any`。

結構：
```
bci_dataset_114-2_update/
└── bci_dataset_114-2_any/
    ├── S01/
    │   ├── S01_1_1.txt   ← S01 的 Relax 第 1 試次
    │   ├── S01_2_1.txt   ← Focus
    │   └── S01_3_1.txt   ← Blink
    ├── S02/
    └── ... (S01-S18)
```
每檔 = 20 秒、512 Hz、單通道 Fp1，共 10240 個浮點數。

---

## 重新訓練流程

```powershell
# 1. 確認資料集放在 bci_dataset_114-2_update/bci_dataset_114-2_any/
# 2. 跑全受試者品質診斷，找出該排除誰
python -X utf8 -u diagnose_all_update.py

# 3. 跑 LOSO 比較（約 10-15 分鐘）
python -X utf8 -u train_compare.py

# 4. 用全部資料訓練最終 MLP + 存模型
python -X utf8 -u save_best_model.py

# 5.（選做）產生簡報用圖表
python -X utf8 -u make_figures.py
```

要剃除某些受試者：改 `train_compare.py` 的 `Config.EXCLUDED_SUBJECTS`。

---

## 模型表現（更新版資料集，n=16）

| 模型 | 平均準確率 | 標準差 |
|---|---|---|
| **MLP** | **0.879** | **±0.116** |
| SVM (Linear) | 0.876 | ±0.110 |
| Ensemble (RF+SVM+LDA) | 0.869 | ±0.116 |
| RandomForest | 0.868 | ±0.122 |
| LDA | 0.851 | ±0.105 |

LOSO Cross-Validation，剃除 S13（準確率=亂猜 0.33）與 S14（<0.6，三任務不可分）。
> 註：更新版資料集較舊版整體提升 4~9%；最佳模型由 Ensemble 變成 MLP（且檔案從 45 MB 縮到 0.2 MB）。
> S01/S06/S09 約 0.67 是因為其 Relax/Focus 腦波本身難分（Blink 仍正常），不影響眨眼控制。

---

## 開發筆記

### 為什麼用「連續眨眼時長」而不是「數眨眼次數」？
模型的 4 秒視窗解析度太粗，分不出「眨 1 下 / 2 下 / 3 下」（全看成一段 Blink 狀態）。
但它很穩定地能判斷「現在是不是處於連續眨眼狀態」。所以改成用 **hysteresis state machine**
偵測「進入/離開連續眨眼」，依持續秒數分桶觸發動作（<1s 忽略、1-2.5s 暫停、2.5-5s 下一首、5-8s 重播本曲）。邏輯在 `bci_server.py` 的 `infer_loop()`。

### 為什麼眨眼計數不用 raw 尖峰偵測？
曾試過直接抓 raw 振幅尖峰，但一次眨眼有「閉+張」雙峰會被算成 2 下。改用模型 Blink
probability + 時長分桶後，使用者只要「連續眨一段時間」就好，不必精準控制次數。
（`RawBlinkDetector` 仍保留在 `signal_sources.py` 作為備案，目前未使用。）

### 為什麼部署 MLP？
更新版資料集上 MLP LOSO 最高（0.879），且推論最快、檔案最小（0.2 MB vs Ensemble 45 MB）。
舊資料集時是 Ensemble 略勝，換資料後 MLP 反超。

---

## 待辦

- [ ] BrainLink Lite 實機 BrainFlow 連線驗證
- [ ] 真人配戴情境下參數微調（`--enter-thresh` / `--burst-end-gap`）
- [ ] YT Music DOM selector 實機驗證（YT 偶爾改 class）
- [ ] 長時間穩定性測試（30 分鐘以上）
- [ ] （選做）依專注度自動切歌單功能

---

## 組員協作

請在自己的 branch 開發，PR 回 main。模型相關的改動（特徵、超參數）請附上重新訓練的 `result_v?.txt` 與 confusion matrix。
