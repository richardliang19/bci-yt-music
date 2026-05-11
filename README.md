# BCI × YouTube Music — 腦波控制音樂播放系統

> 期末專題 · 基於 BCI 腦波感測之情境式音樂切換與控制系統

戴上 BrainLink Lite 腦波頭環 → 用「**連續快速眨眼幾秒**」直接操作 YouTube Music 網頁版：

| 連續眨眼持續時間 | 動作 |
|---|---|
| < 1 秒 | 自動忽略（過濾自然反射） |
| 1.0 ~ 2.5 秒 | 播放 / 暫停 |
| 2.5 ~ 5.0 秒 | 下一首 |
| 5.0 ~ 8.0 秒 | 上一首 |
| > 8 秒 | 自動忽略（防訊號黏住） |

> ⚠️ 是「**快速反覆眨眼**」（像有東西飛來眼睛要連眨擋），不是「閉眼不動」。閉眼不動腦波是 alpha 波會被歸成 Relax 而非 Blink。

模型：Ensemble (RandomForest + SVM + LDA)，16 受試者 LOSO 驗證 **83.3% ± 14.0%**。

---

## 系統架構

```
BrainLink Lite (Fp1, 512 Hz, BT)
    ↓
Python 後端  (bci_server.py)
    BrainFlow 串流 → 4s 滑動視窗 → 12 維特徵
    → RobustScaler → Ensemble 推論
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
**做動作**：連續快速眨眼 N 秒 → 停下來 → 系統依持續時間執行對應動作（1-2.5s 暫停、2.5-5s 下一首、5-8s 上一首）。不是閉眼不動！

更詳細請看 [bci-ytm-extension/README.md](bci-ytm-extension/README.md)。

---

## 檔案說明

### 即時推論（運行時用）
| 檔案 | 用途 |
|---|---|
| `bci_server.py` | 主程式：腦波 → 推論 → WebSocket 廣播 |
| `signal_sources.py` | 訊號輸入抽象（dummy / file / folder / brainflow）|
| `probe_brainlink.py` | BrainLink 連線測試 |
| `bci-ytm-extension/` | Chrome 擴充功能完整源碼 |
| `bci_model.pkl` | 訓練好的 Ensemble 模型（45 MB）|
| `requirements.txt` | Python 套件清單 |
| `gui_preview.html` | 擴充功能 GUI 樣子預覽（不需要 server）|
| `sample_data/` | 4 個範例 .txt（沒裝置時測 pipeline 用）|

### 訓練 / 分析（要重新訓練模型才用）
| 檔案 | 用途 |
|---|---|
| `train_compare.py` | 訓練 5 個模型 LOSO 比較 |
| `save_best_model.py` | 用 16 位資料訓練 Ensemble，存成 .pkl |
| `diagnose_subjects.py` | 受試者訊號品質診斷（找出該剃除哪幾位）|
| `make_figures.py` | 產生簡報用所有圖表（matplotlib）|
| `make_report.py` | 產生 HTML 進度報告 |

### 訓練結果（小檔，提供參考）
| 檔案 | 內容 |
|---|---|
| `model_comparison_detail.csv` | 各模型對各受試者的 LOSO 準確率 |
| `best_model_confusion_matrix.csv` | Ensemble 混淆矩陣 |

---

## 資料集

`bci_dataset_114-2/` **沒有放在 repo 裡**（太大，160 MB+）。請從同學共享連結下載放到 repo 根目錄。
要重新訓練模型才需要這個資料夾；只是要跑 demo 用 `sample_data/` 就夠了。

結構：
```
bci_dataset_114-2/
├── S01/
│   ├── S01_1_1.txt   ← S01 的 Relax 第 1 試次
│   ├── S01_1_2.txt
│   ├── ...
│   ├── S01_2_1.txt   ← Focus
│   └── S01_3_1.txt   ← Blink
├── S02/
└── ... (S01-S18)
```
每檔 = 20 秒、512 Hz、單通道 Fp1，共 10240 個浮點數。

---

## 重新訓練流程

```powershell
# 1. 確認資料集放在 bci_dataset_114-2/
# 2. 跑 LOSO 比較（約 10-15 分鐘）
python -X utf8 -u train_compare.py

# 3. 用全部資料訓練最終 Ensemble + 存模型
python -X utf8 -u save_best_model.py

# 4.（選做）產生簡報用圖表
python -X utf8 -u make_figures.py
```

要剃除某些受試者：改 `train_compare.py` 第 26 行 `EXCLUDED_SUBJECTS`。

---

## 模型表現

| 模型 | 平均準確率 | 標準差 |
|---|---|---|
| MLP | 0.830 | ±0.132 |
| SVM (Linear) | 0.810 | ±0.145 |
| LDA | 0.810 | ±0.155 |
| RandomForest | 0.831 | ±0.128 |
| **Ensemble (RF+SVM+LDA)** | **0.833** | **±0.140** |

LOSO Cross-Validation，n=16（剃除 S08 訊號分不開、S10 訊號失真）。

---

## 開發筆記

### 為什麼 1 次眨眼會被忽略？
人平均每幾秒就會自然眨眼一次。如果單次眨眼就能觸發動作，使用者根本沒辦法正常看影片。所以系統用 **hysteresis state machine** 把連續的 Blink 預測合成「1 個眨眼事件」，並要求 **至少 2 個事件** 才執行動作。詳細邏輯在 `bci_server.py` 的 `infer_loop()`。

### 為什麼有 1.5 秒延遲？
要區分「2 次 / 3 次 / 4 次」眨眼，系統必須等使用者「真的眨完了」才能算。`--burst-end-gap 1.5` 表示等 1.5 秒沒新眨眼才結算。可調短（更靈敏但容易把 2 次誤判 3 次）或調長。

### 為什麼用 Ensemble 不直接用最快的 LDA？
單模型差距 1-2% 看似不重要，但 Ensemble 在「最弱受試者」上的崩潰沒那麼嚴重（標準差最低之一）。實機 demo 時偶發性失敗會很尷尬，所以選穩定優先。

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
