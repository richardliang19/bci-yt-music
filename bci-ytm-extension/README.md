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
cd c:/Users/chile/Desktop/BME/final/bci-yt-music
python -X utf8 -u bci_server.py --source brainlink:COM3
```
（沒接裝置可先用 `--source dummy` 測 UI）

### 步驟 2 — 開 YouTube Music 並隨便播一首歌
https://music.youtube.com

### 步驟 3 — 看右下角
應該會出現浮動 BCI overlay：
- 連線燈轉綠 = 後端連上
- Relax / Focus / Blink 三條機率即時跳動
- 「連續眨眼時長」進度條：你開始眨時計時，停下時依停在哪個 bucket 觸發動作
- 觸發時跳出綠色 toast、自動點 YT Music 的對應按鈕

### 動作對應（依持續秒數）
| 持續時間 | 動作 | 備註 |
|---|---|---|
| < 1 秒 | （忽略）| 過濾自然眨眼反射 |
| 1.0 – 2.5 秒 | 播放 / 暫停 | 最常用動作 |
| 2.5 – 5.0 秒 | 下一首 | |
| 5.0 – 8.0 秒 | 上一首 | |
| > 8 秒 | （忽略）| 防訊號黏住或沒及時停 |

> **重要**：是「**連續快速眨眼**」（像有東西飛來眼睛要連眨擋住），不是「閉眼不動」。
> 閉眼不動腦波會出 alpha 波被歸到 Relax，不會觸發。
>
> 你停止眨眼時動作才執行（停止後約 1-2 秒）。Overlay 會即時顯示你目前在哪個 bucket。

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
| 觸發太敏感 | 後端加 `--enter-thresh 0.7`（要更明顯的眨眼才進入狀態） |
| Bucket 邊界對不上你習慣 | 調 `--bucket-1/2/3/max`，例如 `--bucket-1 1.5 --bucket-2 3.0` |
