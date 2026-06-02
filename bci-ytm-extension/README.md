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
| 5.0 – 8.0 秒 | 重播本曲 | YT Music 限制無法可靠跳上一首，定位為重播 |
| > 8 秒 | （忽略）| 防訊號黏住或沒及時停 |

> **重要**：是「**連續快速眨眼**」（像有東西飛來眼睛要連眨擋住），不是「閉眼不動」。
> 閉眼不動腦波會出 alpha 波被歸到 Relax，不會觸發。
>
> 你停止眨眼時動作才執行（停止後約 1-2 秒）。Overlay 會即時顯示你目前在哪個 bucket。

## 情境模式（依腦波狀態自動切歌單）

overlay 內「🎵 情境模式」面板，兩種分支可即時切換，都在擴充內執行
（讀後端的 Relax/Focus 機率串流判斷，不需改後端）：

| 模式 | 行為 |
|---|---|
| **過濾** | 你選一個目標狀態（專注/放鬆）。播放中的歌若持續 8 秒沒讓你進入目標狀態（目標機率 < 40%）→ 自動跳下一首。適合「匯入一個混雜歌單，自動濾掉不對味的歌」|
| **歌單庫** | 偵測到你穩定進入某情境（專注或放鬆持續 20 秒）→ 自動切到該情境的歌單（隨機挑一個）|
| **手動** | 「▶ 專注歌單 / ▶ 放鬆歌單」按鈕，隨時手動切（demo 時最可控）|

**設定歌單**：編輯 `content.js` 最上面的 `PLAYLISTS`，把你的 YT Music
歌單連結貼進 `focus` / `relax` 陣列（每個情境可放多個，歌單庫模式會隨機挑）：
```js
const PLAYLISTS = {
  focus: ["https://music.youtube.com/playlist?list=你的專注歌單ID"],
  relax: ["https://music.youtube.com/playlist?list=你的放鬆歌單ID"],
};
```
切歌時機參數（持續秒數、冷卻）在 `content.js` 的 `SCENARIO` 物件可調。
眨眼當下的訊框不納入狀態判斷，避免眼動污染專注/放鬆判讀。

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
