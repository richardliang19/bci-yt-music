"""
BCI WebSocket 伺服器
─────────────────────────────────────────────────────────────
腦波 → 模型推論 → 廣播 JSON 給 Chrome 擴充
不負責 UI、不操作播放器，只是訊息源。

眨眼邏輯（重要）：
  ── 用 hysteresis state machine 偵測「眨眼事件」(blink event)
     enter 門檻 0.55，exit 門檻 0.40 → 一次完整眨眼動作算「1 個事件」
  ── 必須 ≥ 2 個事件才觸發動作（單次眨眼 = 自然反射，自動忽略）
  ── 等最後一個事件後 BURST_END_GAP 秒沒新眨眼，才確認 burst 完成並執行
  ── 對應：2 次=播放/暫停、3 次=下一首、4 次=上一首

訊息格式：
  {"type":"status","connected":true,"source":"...","fs":512}
  {"type":"proba","ts":...,"relax":..,"focus":..,"blink":..,
   "pred":"...","blink_events":N,"is_blinking":bool}
  {"type":"action","ts":...,"action":"play_pause|next|prev","blink_count":N}

執行：
  python -X utf8 -u bci_server.py --source dummy
  python -X utf8 -u bci_server.py --source file:bci_dataset_114-2/S15/S15_3_5.txt
  python -X utf8 -u bci_server.py --source brainflow:COM3
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
from signal_sources import make_source


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


# ── 動作對應表 ────────────────────────────────────────────────────────────────
def action_for_blinks(n):
    """眨眼次數 → 動作（None 表示不觸發）"""
    if n < 2:
        return None              # 單次眨眼 = 自然反射，忽略
    if n == 2:
        return "play_pause"
    if n == 3:
        return "next"
    if n >= 4:
        return "prev"
    return None


# ── 推論主迴圈 ────────────────────────────────────────────────────────────────
async def infer_loop(args):
    global LAST_STATUS

    print(f"載入模型：{args.model}")
    bundle = joblib.load(args.model)
    model = bundle["model"]
    scaler = bundle["scaler"]
    labels = bundle["labels"]                     # {0:"Relax",1:"Focus",2:"Blink"}
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
    blink_events = deque()      # 完成的眨眼事件時間戳

    # 眨眼狀態機
    is_blinking = False
    blink_start_t = None

    last_pred_t = 0.0
    last_action_t = 0.0

    print(f"開始推論（每 {args.step}s 一次）")
    print(f"眨眼門檻：enter={args.enter_thresh}  exit={args.exit_thresh}  "
          f"min duration={args.min_blink_duration}s")
    print(f"Burst 視窗：{args.burst_window}s  收尾延遲：{args.burst_end_gap}s  "
          f"動作冷卻：{args.action_cooldown}s")
    print(f"動作對應：1次=忽略  2次=播放/暫停  3次=下一首  4+次=上一首")
    print("=" * 60)

    try:
        while True:
            new, eof = src.read_new()
            if len(new):
                buf.extend(new.tolist())
            if eof and len(buf) < win_n:
                print("[infer] 來源結束")
                break

            now = time.time()
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

                # ── Hysteresis 眨眼事件偵測 ────────────────────────
                if is_blinking:
                    if blink_p < args.exit_thresh:
                        duration = now - blink_start_t
                        if duration >= args.min_blink_duration:
                            blink_events.append(now)
                            print(f"  · 偵測到眨眼事件 #{len(blink_events)}"
                                  f"（持續 {duration:.2f}s）")
                        is_blinking = False
                        blink_start_t = None
                else:
                    if blink_p >= args.enter_thresh:
                        is_blinking = True
                        blink_start_t = now

                # 清掉視窗外的舊事件
                while blink_events and (now - blink_events[0]) > args.burst_window:
                    blink_events.popleft()

                # ── 廣播當前狀態 ─────────────────────────────────
                msg = {
                    "type": "proba", "ts": now,
                    "relax": float(avg[0]), "focus": float(avg[1]), "blink": blink_p,
                    "pred": labels[pred],
                    "blink_events": len(blink_events),
                    "is_blinking": is_blinking,
                    "source": args.source,
                }
                await broadcast(msg)
                print(f"[{time.strftime('%H:%M:%S')}] R={avg[0]:.2f} F={avg[1]:.2f} "
                      f"B={blink_p:.2f} → {labels[pred]} "
                      f"events={len(blink_events)} {'⚡' if is_blinking else ''}")

                # ── Burst 收尾：沒新眨眼超過 BURST_END_GAP → 結算 ─
                if (blink_events and not is_blinking
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
                    proba_hist.clear()

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
                    help="dummy | brainflow[:COM3] | file:<path> | folder:<path>")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--step", type=float, default=0.5,
                    help="每幾秒推論一次（預設 0.5）")
    ap.add_argument("--smooth", type=int, default=5,
                    help="平滑視窗大小（預設 5）")

    # 眨眼狀態機參數
    ap.add_argument("--enter-thresh", type=float, default=0.55,
                    help="進入眨眼狀態的機率門檻（預設 0.55）")
    ap.add_argument("--exit-thresh", type=float, default=0.40,
                    help="離開眨眼狀態的機率門檻（預設 0.40，hysteresis 防抖）")
    ap.add_argument("--min-blink-duration", type=float, default=0.4,
                    help="一次眨眼至少要持續幾秒才算數（預設 0.4，過濾雜訊尖峰）")

    # Burst 參數
    ap.add_argument("--burst-window", type=float, default=3.0,
                    help="Burst 最大視窗秒數（預設 3.0）")
    ap.add_argument("--burst-end-gap", type=float, default=1.5,
                    help="最後一個眨眼後等多久才確認 burst 完成（預設 1.5；"
                         "代表每次動作會延遲 1.5s，但允許眨眼之間有 1.5s 間隔）")
    ap.add_argument("--action-cooldown", type=float, default=1.5,
                    help="動作觸發後冷卻秒數，避免重複（預設 1.5）")

    args = ap.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n伺服器停止")


if __name__ == "__main__":
    main()
