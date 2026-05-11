"""
BCI WebSocket 伺服器
─────────────────────────────────────────────────────────────
腦波 → 模型推論（顯示用）+ raw 尖峰偵測（數眨眼次數）→ 廣播 JSON

兩條獨立判讀軌道：
  1. ML 模型 (bci_model.pkl)：分類 Relax/Focus/Blink 整體狀態（給 overlay 顯示）
  2. RawBlinkDetector：直接從 raw EEG 抓眼動尖峰，可靠數出眨眼次數
     → 模型對「快速連續眨眼」會看成 1 個事件（4 秒視窗解析度限制），
       raw 偵測能正確分辨每一下，與使用者直覺一致

眨眼數 → 動作：
  1 次 = 忽略（過濾自然反射）
  2 次 = 播放/暫停
  3 次 = 下一首
  4+ 次 = 上一首

訊息格式：
  {"type":"status","connected":true,"source":"...","fs":512}
  {"type":"proba","ts":...,"relax":..,"focus":..,"blink":..,"pred":"...",
   "blink_events":N,"is_blinking":bool}
  {"type":"action","ts":...,"action":"play_pause|next|prev","blink_count":N}

執行：
  python -X utf8 -u bci_server.py --source dummy
  python -X utf8 -u bci_server.py --source file:sample_data/sample_blink.txt
  python -X utf8 -u bci_server.py --source brainlink:COM3
  python -X utf8 -u bci_server.py --source folder:C:/path/to/brainlink_output
"""

import argparse
import asyncio
import json
import time
from collections import deque

import joblib
import numpy as np

try:
    import websockets
except ImportError:
    import sys
    sys.exit("未安裝 websockets，請執行：python -m pip install websockets --user")

from train_compare import extract_features
from signal_sources import make_source, RawBlinkDetector


CLIENTS = set()
LAST_STATUS = None


async def broadcast(msg: dict):
    if not CLIENTS:
        return
    payload = json.dumps(msg, ensure_ascii=False)
    dead = []
    for ws in CLIENTS:
        try:
            await ws.send(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


async def handler(ws):
    CLIENTS.add(ws)
    print(f"[WS] client 連入，目前 {len(CLIENTS)} 個")
    try:
        if LAST_STATUS:
            await ws.send(json.dumps(LAST_STATUS, ensure_ascii=False))
        async for _ in ws:
            pass
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[WS] client 斷開，剩 {len(CLIENTS)} 個")


def action_for_blinks(n):
    if n < 2:
        return None
    if n == 2:
        return "play_pause"
    if n == 3:
        return "next"
    if n >= 4:
        return "prev"
    return None


async def infer_loop(args):
    global LAST_STATUS

    print(f"載入模型：{args.model}")
    bundle = joblib.load(args.model)
    model = bundle["model"]
    scaler = bundle["scaler"]
    labels = bundle["labels"]
    cfg = bundle["config"]
    fs = cfg["sampling_rate"]
    win_n = int(cfg["segment_length"] * fs)
    print(f"模型：{type(model).__name__}　fs={fs}　視窗={cfg['segment_length']}s={win_n}點")

    src = make_source(args.source, fs)
    LAST_STATUS = {"type": "status", "connected": True,
                   "source": args.source, "fs": fs}
    await broadcast(LAST_STATUS)

    buf = deque(maxlen=win_n)
    proba_hist = deque(maxlen=args.smooth)

    # Raw 眨眼偵測器（每進來新樣本就餵進去）
    blink_det = RawBlinkDetector(
        fs,
        threshold_mult=args.peak_thresh_mult,
        refractory_s=args.peak_refractory,
        baseline_window_s=3.0,
        min_amp=args.peak_min_amp,
    )
    blink_events = deque()      # raw detector 回報的尖峰時間戳

    last_pred_t = 0.0
    last_action_t = 0.0

    print(f"開始推論（顯示每 {args.step}s 一次，平滑視窗 {args.smooth}）")
    print(f"Raw 眨眼偵測：threshold = {args.peak_thresh_mult}×MAD（最低 {args.peak_min_amp} µV）"
          f"  refractory = {args.peak_refractory}s")
    print(f"Burst 視窗：{args.burst_window}s  收尾延遲：{args.burst_end_gap}s  "
          f"動作冷卻：{args.action_cooldown}s")
    print(f"動作對應：1次=忽略  2次=播放/暫停  3次=下一首  4+次=上一首")
    print("=" * 60)

    try:
        while True:
            new, eof = src.read_new()
            if len(new):
                buf.extend(new.tolist())
                # 餵 raw 偵測器
                new_events = blink_det.update(new, time.time())
                for t in new_events:
                    blink_events.append(t)
                    print(f"  · 偵測到眨眼尖峰 #{len(blink_events)}"
                          f"  (baseline={blink_det._cached_baseline:.0f},"
                          f" thresh={blink_det._cached_threshold:.0f})")
            if eof and len(buf) < win_n:
                print("[infer] 來源結束")
                break

            now = time.time()

            # 清掉視窗外的舊事件
            while blink_events and (now - blink_events[0]) > args.burst_window:
                blink_events.popleft()

            # 模型推論：每 step 秒一次（純粹給 UI 顯示用）
            if len(buf) >= win_n and (now - last_pred_t) >= args.step:
                last_pred_t = now
                seg = np.asarray(buf, dtype=np.float64)
                seg = seg - seg.mean()
                feats = extract_features([seg])
                feats_s = scaler.transform(feats)
                proba = model.predict_proba(feats_s)[0]
                proba_hist.append(proba)
                avg = np.mean(proba_hist, axis=0)
                blink_p = float(avg[2])
                pred = int(np.argmax(avg))

                msg = {
                    "type": "proba", "ts": now,
                    "relax": float(avg[0]), "focus": float(avg[1]), "blink": blink_p,
                    "pred": labels[pred],
                    "blink_events": len(blink_events),
                    "is_blinking": False,   # 不再用 model 判此狀態
                    "source": args.source,
                }
                await broadcast(msg)
                print(f"[{time.strftime('%H:%M:%S')}] R={avg[0]:.2f} F={avg[1]:.2f} "
                      f"B={blink_p:.2f} → {labels[pred]} events={len(blink_events)}")

            # Burst 結算：最後一個尖峰後 burst-end-gap 秒沒新尖峰 → fire
            if (blink_events
                    and (now - blink_events[-1]) >= args.burst_end_gap
                    and (now - last_action_t) >= args.action_cooldown):
                n = len(blink_events)
                action = action_for_blinks(n)
                if action is None:
                    print(f"  ↪ {n} 次眨眼 → 忽略（生理性眨眼）")
                else:
                    await broadcast({
                        "type": "action", "ts": now,
                        "action": action, "blink_count": n,
                    })
                    print(f"  ▶ 觸發動作：{action}（{n} 次眨眼）")
                    last_action_t = now
                blink_events.clear()

            await asyncio.sleep(0.02)
    except asyncio.CancelledError:
        pass
    finally:
        if hasattr(src, "close"):
            src.close()
        LAST_STATUS = {"type": "status", "connected": False, "source": args.source, "fs": fs}
        await broadcast(LAST_STATUS)
        print("[infer] 結束")


async def main_async(args):
    server = await websockets.serve(handler, args.host, args.port)
    print(f"[WS] 監聽 ws://{args.host}:{args.port}")
    infer_task = asyncio.create_task(infer_loop(args))
    try:
        await infer_task
    finally:
        server.close()
        await server.wait_closed()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bci_model.pkl")
    ap.add_argument("--source", required=True,
                    help="dummy | brainlink[:COM3] | file:<path> | folder:<path>")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--step", type=float, default=0.5,
                    help="模型推論間隔秒（給 UI 顯示用，預設 0.5）")
    ap.add_argument("--smooth", type=int, default=2,
                    help="模型機率平滑視窗（預設 2，越大越平穩但 delay 越久）")

    # Raw 眨眼尖峰偵測器
    ap.add_argument("--peak-thresh-mult", type=float, default=5.0,
                    help="尖峰門檻 = N × MAD（預設 5.0；太敏感→調高，太遲鈍→調低）")
    ap.add_argument("--peak-min-amp", type=float, default=80.0,
                    help="尖峰絕對下限 µV（預設 80，避免低訊號時誤觸）")
    ap.add_argument("--peak-refractory", type=float, default=0.25,
                    help="兩個尖峰最小間隔秒（預設 0.25，避免一下眨眼算成多下）")

    # Burst 結算
    ap.add_argument("--burst-window", type=float, default=3.0,
                    help="Burst 最大視窗秒數（預設 3.0）")
    ap.add_argument("--burst-end-gap", type=float, default=1.0,
                    help="最後一個尖峰後等多久才確認 burst 完成（預設 1.0）")
    ap.add_argument("--action-cooldown", type=float, default=1.5,
                    help="動作觸發後冷卻秒數（預設 1.5）")

    args = ap.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n伺服器停止")


if __name__ == "__main__":
    main()
