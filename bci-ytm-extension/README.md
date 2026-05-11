# BCI YT Music Controller — Chrome 擴充

搭配 `bci_server.py` 後端使用，把腦波（眨眼）變成 YouTube Music 網頁版的播放控制。

## 安裝

1. 開 Chrome / Edge → `chrome://extensions`
2. 右上角開啟「開發人員模式」
3. 點「載入未封裝項目」→ 選這個資料夾（`bci-ytm-extension/`）
4. 擴充清單會出現「BCI YT Music Controller」

## 使用

### 步驟 1 — 啟動 Python 後端
```
cd c:/Users/chile/Desktop/BME/final
python -X utf8 -u bci_server.py --source brainflow
```
（沒接裝置可先用 `--source dummy` 測 UI）

### 步驟 2 — 開 YouTube Music 並隨便播一首歌
https://music.youtube.com

### 步驟 3 — 看右下角
應該會出現浮動 BCI overlay：
- 連線燈轉綠 = 後端連上
- Relax / Focus / Blink 三條機率即時跳動
- 連續眨眼會點亮下方紅點
- 觸發動作時跳出綠色 toast，並自動點 YT Music 的播放鈕

### 動作對應
| 連續眨眼次數 | 動作 | 備註 |
|---|---|---|
| 1 | （忽略）| 自動過濾，避免被自然反射觸發 |
| 2 | 播放 / 暫停 | 最常用動作 |
| 3 | 下一首 | |
| 4 | 上一首 | |

> 系統會等你「眨完最後一下後 0.8 秒」沒新眨眼，才執行動作 — 所以 1/2/3/4 次絕對不會混淆。

## 設定

點瀏覽器右上角擴充功能 icon（可能要先到 `chrome://extensions` 把它釘選）：
- WebSocket URL：預設 `ws://localhost:8765`，後端改 port 才需要動
- 啟用動作觸發：關掉就只看 overlay 不會自動按按鈕（demo 時方便）

## 排錯

| 症狀 | 排查 |
|---|---|
| Overlay 沒出現 | F12 → Console 看有沒有錯，確認網址是 `music.youtube.com` |
| 連線燈紅 | `bci_server.py` 沒跑、或 port 不對、或防火牆擋 8765 |
| 按鈕沒按到（toast 顯示「找不到 X 按鈕」）| YT Music DOM 改了，F12 → Elements 找新 selector，改 `content.js` `selectors` |
| 觸發太敏感 | 後端加 `--blink-conf 0.7` 或 `--burst-window 1.5` |
