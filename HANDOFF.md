# BCI YouTube Music 專案交接文件

這份文件給組員接手操作 demo 用。請先照「快速啟動」跑起來，再看後面的判斷邏輯與測試方式。

## 1. 專案目前做什麼

這個系統用 BrainLink Lite 的 EEG 訊號控制 YouTube Music，並用 AI 教練整理使用者在不同歌曲下的專注/放鬆狀態。

主要分成三塊：

- `bci_server.py`：Python 後端，讀 EEG、做模型推論、偵測眨眼控制、提供 WebSocket。
- `bci-ytm-extension/`：Chrome 插件，注入 YouTube Music 頁面，顯示 overlay、按播放控制鍵、回傳歌曲資訊。
- `llm_coach.py` + `music_search.py`：AI 教練，整理 EEG session 報告，並查詢歌曲背景資訊輔助分析。

資料流：

```text
BrainLink / dummy / file
        ↓
bci_server.py
  - MLP 模型輸出 Relax / Focus / Blink
  - raw EEG peak-to-peak 判斷眨眼控制長度
  - WebSocket ws://localhost:8765
        ↓
Chrome extension on music.youtube.com
  - overlay 顯示狀態
  - 操作播放/暫停、下一首、上一首
  - 回傳目前歌曲資訊給後端
        ↓
AI 教練報告
  - EEG 統計
  - 每首歌期間 Focus/Relax
  - 簡單 web search 音樂背景分析
```

## 2. 快速啟動

在 PowerShell：

```powershell
cd <你下載或解壓後的專案路徑>\bci-yt-music
python -m pip install -r requirements.txt
python -X utf8 -u bci_server.py --source brainlink:COM3
```

如果還沒接 BrainLink，要先測 UI：

```powershell
python -X utf8 -u bci_server.py --source dummy
```

如果 COM port 不確定，可以先跑：

```powershell
python -X utf8 -u probe_brainlink.py
```

後端正常啟動會看到：

```text
[WS] 監聽 ws://localhost:8765
載入模型：bci_model.pkl
Control mode: raw
Music web search: enabled
```

## 3. 載入 Chrome 插件

1. 打開 Chrome。
2. 進入 `chrome://extensions`。
3. 右上角開啟「開發人員模式」。
4. 點「載入未封裝項目」。
5. 選這個資料夾：

```text
<你下載或解壓後的專案路徑>\bci-yt-music\bci-ytm-extension
```

6. 打開 `https://music.youtube.com`。
7. 頁面右下角會出現 `BCI Controller` overlay。

注意：只要改過 `content.js` 或 `overlay.css`，都要回 `chrome://extensions` 按插件的重新載入，然後重新整理 YouTube Music 頁面。

overlay 可以收合：按右上角的 `-` 收合，按 `+` 展開。

## 4. 目前眨眼控制邏輯

目前控制模式預設是 `raw`，不是直接用 MLP 的 Blink 機率。

原因：實測這批連續眨眼 sec 檔時，MLP 的 Blink 機率偏低，最高大約只有 0.02 左右，不適合拿來分辨眨眼秒數。因此目前用原始 EEG 的 peak-to-peak 強度偵測連續快速眨眼。

raw 判斷方式：

- 每 `0.25s` 取一段 raw EEG。
- 計算該段訊號的 `peak-to-peak = max - min`。
- 若高於門檻，視為眨眼活動。
- 中間短暫低於門檻會用 `merge_gap=0.5s` 合併，避免連續快速眨眼被切成很多段。
- 一段活動結束後，依持續秒數決定動作。

目前預設 bucket：

```text
< 2.0s       忽略
2.0-4.0s     播放/暫停
4.0-6.5s     下一首
6.5-9.0s     上一首
> 9.0s       忽略
```

動作觸發後有 `2.5s` cooldown，避免連續誤觸。

如果要臨時改秒數，不用改程式，可以啟動後端時加參數，例如：

```powershell
python -X utf8 -u bci_server.py --source brainlink:COM3 --bucket-1 2.5 --bucket-2 4.5 --bucket-3 7 --bucket-max 10
```

## 5. 個人化校正邏輯

插件上有「手動校正」按鈕。

校正只校正「raw 眨眼控制門檻」，不會重新訓練 MLP，也不會校正 Focus/Relax。

流程：

1. 按「手動校正」。
2. 第一階段：背景雜訊 3 秒。
   - 使用者保持穩定、不眨眼。
   - 後端收集背景 peak-to-peak。
3. 第二階段：眨眼校正 5 秒。
   - 使用者連續快速眨眼。
   - 後端收集眨眼 peak-to-peak。
4. 算出個人化門檻。

門檻概念：

```text
背景門檻 = 背景 p95 * 1.5
眨眼強度 = 眨眼階段 p75
若眨眼強度明顯高於背景，使用 眨眼 p75 * 0.75
最後門檻 = max(120, 預設200, 背景門檻, 眨眼門檻)
```

如果校正期間沒有收到足夠 raw samples，UI 會顯示等待 BrainLink raw 訊號，不會假裝校正成功。

## 6. 混合歌單過濾邏輯

組內目前決定採用「混合歌單過濾」，不是預建立多個歌單隨機切換。

使用方式：

1. 在 YouTube Music 播放一個混合歌單。
2. 插件選目標：`專注` 或 `放鬆`。
3. 開啟 `過濾`。
4. 系統會觀察最近一段時間的 Focus/Relax 機率。
5. 如果歌曲長時間不符合目標，就自動下一首。

目前參數在 `content.js`：

```js
mismatchSec: 20,      // 觀察最近 20 秒
targetFloor: 0.40,    // 目標狀態低於 40% 視為不符合
mismatchRatio: 0.70,  // 70% 樣本不符合才跳下一首
cooldownSec: 20,      // 跳歌後冷卻 20 秒
```

例子：

- 目標是 `專注`
- 最近 20 秒中，70% 以上的有效樣本 `Focus < 40%`
- 則自動跳下一首

校正中與眨眼控制中，不會納入過濾判斷，避免眨眼訊號污染 Focus/Relax。

## 7. AI 教練與歌曲分析

AI 教練需要 `OPENAI_API_KEY`。

功能：

- 即時解讀：每隔一段時間給一句短回饋。
- Session 報告：按「產生報告」後，整理整段 Focus/Relax/Blink。
- 音樂分析：插件會回傳目前歌曲資訊，後端統計每首歌播放期間的 Focus/Relax。
- web search：產生報告時會簡單搜尋歌曲背景，輔助 AI 歸納曲風、氛圍、人聲/純音樂等特徵。

後端看到這行代表 AI 有啟用：

```text
[LLM] 已啟用
AI 教練：啟用
Music web search: enabled
```

如果不想使用音樂 web search：

```powershell
$env:BCI_MUSIC_WEB_SEARCH = "0"
```

要開回來：

```powershell
$env:BCI_MUSIC_WEB_SEARCH = "1"
```

注意：web search 會把歌名與歌手送去搜尋，所以正式報告或 demo 要把這點當成隱私說明。

## 8. API key 要怎麼給組員

最推薦：每個組員自己建立自己的 OpenAI API key。

如果真的要共用同一把 key：

- 不要寫進程式碼。
- 不要貼進 GitHub、報告截圖、簡報截圖。
- 用私訊或密碼管理工具交給組員。
- demo 結束後建議 rotate 或刪掉那把 key。
- 組員只需要在自己的 PowerShell 設環境變數。

臨時設定，只對目前 PowerShell 視窗有效：

```powershell
$env:OPENAI_API_KEY = "sk-你的key"
python -X utf8 -u bci_server.py --source brainlink:COM3
```

永久設定到 Windows 使用者環境變數：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-你的key", "User")
```

永久設定後要重開 PowerShell，再跑後端。

檢查是否設定成功：

```powershell
python -c "import os; print('OK' if os.getenv('OPENAI_API_KEY') else 'MISSING')"
```

不要建立像 `key.txt`、`.env` 然後傳來傳去，除非有確認 `.gitignore` 且不會被截圖或提交。

## 9. Demo 建議流程

### A. 插件與後端連線

```powershell
cd <你下載或解壓後的專案路徑>\bci-yt-music
$env:OPENAI_API_KEY = "sk-你的key"
python -X utf8 -u bci_server.py --source brainlink:COM3
```

Chrome 打開 YouTube Music，確認 overlay 顯示「推論中」。

### B. 手動校正

1. 戴好 BrainLink。
2. 點 overlay 的「手動校正」。
3. 前 3 秒保持穩定、不眨眼。
4. 後 5 秒連續快速眨眼。
5. terminal 應看到：

```text
Manual calibration done: raw p2p threshold=...
```

### C. 眨眼控制

測試：

- 短於 2 秒：應忽略。
- 2 到 4 秒：播放/暫停。
- 4 到 6.5 秒：下一首。
- 6.5 到 9 秒：上一首。

### D. 混合歌單過濾

1. 播放一個混合歌單。
2. 選目標 `專注` 或 `放鬆`。
3. 點 `過濾`。
4. 系統會根據最近 20 秒的比例決定是否跳下一首。

### E. AI 報告

1. 至少聽 3 到 5 首。
2. 每首最好聽 30 秒以上。
3. 點「產生報告」。
4. 報告會顯示總結、觀察、音樂分析、建議與分數。

## 10. 常見問題

### 後端顯示 waiting for raw samples

代表 COM port 連上了，但 BrainLink 沒有穩定送 raw EEG。

檢查：

- BrainLink 是否戴好。
- 電極接觸是否穩。
- COM port 是否正確。
- 是否有其他程式正在佔用 COM3。

### LLM 顯示未啟用

通常是沒設 `OPENAI_API_KEY`，或沒安裝 `openai`。

```powershell
python -m pip install openai
$env:OPENAI_API_KEY = "sk-你的key"
```

### 插件沒有更新

改過插件檔案後，要做兩件事：

1. `chrome://extensions` 重新載入插件。
2. 重新整理 YouTube Music 頁面。

### AI 報告沒有音樂分析

可能原因：

- 沒在 YouTube Music 頁面。
- 歌曲播放時間太短。
- 插件沒有抓到歌名。
- 沒有重新載入插件。
- web search 被關閉。

後端如果有抓到歌，會印：

```text
[music] Song Title - Artist playing=True
```

## 11. 目前重要檔案

```text
bci_server.py                 後端主程式
signal_sources.py             BrainLink/file/dummy/folder 訊號來源
bci_model.pkl                 已訓練 MLP 模型
llm_coach.py                  OpenAI AI 教練
music_search.py               歌曲背景 web search
bci-ytm-extension/content.js  插件主邏輯
bci-ytm-extension/overlay.css 插件 overlay 樣式
bci-ytm-extension/manifest.json Chrome 插件設定
requirements.txt              Python 依賴
```
