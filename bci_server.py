"""
BCI WebSocket 伺服器
─────────────────────────────────────────────────────────────
腦波 → 模型推論 → 廣播 JSON 給 Chrome 擴充
不負責 UI、不操作播放器，只是訊息源。

判讀邏輯（model-based duration buckets）：
  ── 模型輸出 Blink probability，hysteresis state machine 偵測
     「進入 / 離開」連續眨眼狀態
  ── 一段連續眨眼結束時，計算 hold duration = exit_t - enter_t
  ── 依 duration 對應動作：
       <1.0s     → ignored（生理性眨眼）
       1.0-2.5s  → play_pause
       2.5-5.0s  → next
       5.0-8.0s  → prev
       >8.0s     → ignored（訊號黏住或沒及時停動作）

「連續眨眼」是指快速反覆眨眼（不是閉眼不動 — 那會出 alpha 波被歸成 Relax）。

訊息格式：
  {"type":"status","connected":true,"source":"...","fs":512}
  {"type":"proba","ts":...,"relax":..,"focus":..,"blink":..,"pred":"...",
   "is_blinking":bool,"hold_duration":float}
  {"type":"action","ts":...,"action":"play_pause|next|prev","hold_duration":float}

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
from signal_sources import make_source
import llm_coach


CLIENTS = set()
LAST_STATUS = None

# session 累積資料（給 LLM 用）
SESSION = {
    "start": None,
    "samples": [],   # 每次推論一筆 (t, relax, focus, blink, pred)
    "actions": [],   # 觸發動作 (t, action, hold_duration)
}


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
        # 告知前端 LLM 是否可用
        await ws.send(json.dumps(
            {"type": "llm_status", "available": llm_coach.is_available()},
            ensure_ascii=False))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "request_report":
                asyncio.create_task(generate_report())
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[WS] client 斷開，剩 {len(CLIENTS)} 個")


# ── LLM 整合 ──────────────────────────────────────────────────────────────────
def _window_stats(window_sec=30.0):
    """取最近 window_sec 秒的統計，給 insight 用"""
    if not SESSION["samples"]:
        return None
    now = SESSION["samples"][-1][0]
    win = [s for s in SESSION["samples"] if now - s[0] <= window_sec]
    if len(win) < 4:
        return None
    arr = np.array([[s[1], s[2], s[3]] for s in win])
    mean = arr.mean(axis=0)
    # focus 趨勢：前半 vs 後半
    half = len(win) // 2
    f_first = np.mean([s[2] for s in win[:half]])
    f_last = np.mean([s[2] for s in win[half:]])
    trend = "上升" if f_last > f_first + 0.05 else ("下降" if f_last < f_first - 0.05 else "持平")
    recent_actions = [a[1] for a in SESSION["actions"] if now - a[0] <= window_sec]
    labels = ["Relax", "Focus", "Blink"]
    return {
        "window_sec": window_sec,
        "relax_pct": round(float(mean[0]), 2),
        "focus_pct": round(float(mean[1]), 2),
        "blink_pct": round(float(mean[2]), 2),
        "dominant": labels[int(np.argmax(mean))],
        "focus_trend": trend,
        "actions": recent_actions,
    }


def _session_stats():
    """整段 session 統計，給 report 用"""
    if not SESSION["samples"] or SESSION["start"] is None:
        return None
    samples = SESSION["samples"]
    dur_min = (samples[-1][0] - SESSION["start"]) / 60.0
    arr = np.array([[s[1], s[2], s[3]] for s in samples])
    mean = arr.mean(axis=0)
    labels = ["Relax", "Focus", "Blink"]
    # 每分鐘主導狀態 timeline
    timeline = []
    bucket = {}
    for t, r, f, b, pred in samples:
        m = int((t - SESSION["start"]) // 60)
        bucket.setdefault(m, []).append((r, f, b))
    for m in sorted(bucket):
        bm = np.array(bucket[m]).mean(axis=0)
        timeline.append({"min": m, "dominant": labels[int(np.argmax(bm))],
                         "focus": round(float(bm[1]), 2)})
    acts = [a[1] for a in SESSION["actions"]]
    return {
        "duration_min": round(dur_min, 1),
        "relax_pct": round(float(mean[0]), 2),
        "focus_pct": round(float(mean[1]), 2),
        "blink_pct": round(float(mean[2]), 2),
        "timeline": timeline,
        "n_play_pause": acts.count("play_pause"),
        "n_next": acts.count("next"),
        "n_prev": acts.count("prev"),
    }


async def generate_insight():
    stats = _window_stats()
    if stats is None:
        return
    text = await llm_coach.insight(stats)
    if text:
        await broadcast({"type": "llm_insight", "ts": SESSION["samples"][-1][0],
                         "text": text})
        print(f"  [AI] {text}")


async def generate_report():
    stats = _session_stats()
    if stats is None:
        await broadcast({"type": "llm_report", "error": "資料不足，先用一陣子再產生報告"})
        return
    print("  [AI] 產生 session 報告中…")
    rep = await llm_coach.report(stats)
    if rep:
        rep["type"] = "llm_report"
        rep["stats"] = stats
        await broadcast(rep)
        print(f"  [AI] 報告完成（focus_score={rep.get('focus_score')}）")
    else:
        await broadcast({"type": "llm_report", "error": "LLM 不可用或呼叫失敗"})


def action_for_duration(d, b1, b2, b3, bmax):
    """
    持續秒數 → 動作（None 表示忽略）
      d < b1     → None（生理性眨眼）
      b1..b2     → play_pause
      b2..b3     → next
      b3..bmax   → prev
      d >= bmax  → None（訊號黏住）
    """
    if d < b1 or d >= bmax:
        return None
    if d < b2:
        return "play_pause"
    if d < b3:
        return "next"
    return "prev"


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
                   "source": args.source, "fs": fs,
                   "buckets": [args.bucket_1, args.bucket_2, args.bucket_3, args.bucket_max]}
    await broadcast(LAST_STATUS)

    buf = deque(maxlen=win_n)
    proba_hist = deque(maxlen=args.smooth)

    # Hysteresis state machine
    is_blinking = False
    blink_start_t = None
    last_pred_t = 0.0
    last_action_t = 0.0
    last_insight_t = 0.0

    # 重置 session
    SESSION["start"] = None
    SESSION["samples"] = []
    SESSION["actions"] = []

    print(f"開始推論（每 {args.step}s 一次，平滑 {args.smooth}）")
    print(f"AI 教練：{'啟用' if llm_coach.is_available() else '停用（沒裝 openai 或沒設 OPENAI_API_KEY）'}"
          f"  即時解讀間隔 {args.insight_interval}s")
    print(f"門檻：enter={args.enter_thresh}  exit={args.exit_thresh}")
    print(f"Bucket：忽略 < {args.bucket_1}s ≤ play_pause < {args.bucket_2}s "
          f"≤ next < {args.bucket_3}s ≤ prev < {args.bucket_max}s ≤ 忽略")
    print(f"提示：是「連續快速眨眼」N 秒（不是閉眼不動）。")
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

                # ── Hysteresis state machine ────────────────────
                fired_action = None
                fired_duration = None
                if is_blinking:
                    if blink_p < args.exit_thresh:
                        duration = now - blink_start_t
                        action = action_for_duration(
                            duration, args.bucket_1, args.bucket_2,
                            args.bucket_3, args.bucket_max)
                        if action is None:
                            if duration < args.bucket_1:
                                reason = "太短，當生理性眨眼"
                            else:
                                reason = "太長，可能訊號黏住"
                            print(f"  ↪ 連續眨眼 {duration:.2f}s → 忽略（{reason}）")
                        elif (now - last_action_t) >= args.action_cooldown:
                            fired_action = action
                            fired_duration = duration
                            print(f"  ▶ 觸發動作：{action}（連續眨眼 {duration:.2f}s）")
                            last_action_t = now
                        else:
                            print(f"  ↪ 連續眨眼 {duration:.2f}s → 忽略（動作冷卻中）")
                        is_blinking = False
                        blink_start_t = None
                else:
                    if blink_p >= args.enter_thresh:
                        is_blinking = True
                        blink_start_t = now
                        print(f"  · 進入連續眨眼狀態（B={blink_p:.2f}）")

                # Hold duration（仍在連續眨眼狀態時即時更新）
                hold_duration = (now - blink_start_t) if is_blinking else 0.0

                # 廣播
                msg = {
                    "type": "proba", "ts": now,
                    "relax": float(avg[0]), "focus": float(avg[1]), "blink": blink_p,
                    "pred": labels[pred],
                    "is_blinking": is_blinking,
                    "hold_duration": hold_duration,
                    "source": args.source,
                }
                await broadcast(msg)

                if fired_action is not None:
                    await broadcast({
                        "type": "action", "ts": now,
                        "action": fired_action,
                        "hold_duration": fired_duration,
                    })

                # ── 累積 session 資料（給 AI 教練）─────────────
                if SESSION["start"] is None:
                    SESSION["start"] = now
                SESSION["samples"].append((now, float(avg[0]), float(avg[1]), blink_p, pred))
                if fired_action is not None:
                    SESSION["actions"].append((now, fired_action, fired_duration))

                # 定時即時解讀（fire-and-forget，不阻塞推論）
                if (llm_coach.is_available()
                        and (now - last_insight_t) >= args.insight_interval
                        and (now - SESSION["start"]) >= args.insight_interval):
                    last_insight_t = now
                    asyncio.create_task(generate_insight())

                hold_str = f"hold={hold_duration:.1f}s" if is_blinking else ""
                print(f"[{time.strftime('%H:%M:%S')}] R={avg[0]:.2f} F={avg[1]:.2f} "
                      f"B={blink_p:.2f} → {labels[pred]} {hold_str}")

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
                    help="模型推論間隔秒（預設 0.5）")
    ap.add_argument("--smooth", type=int, default=2,
                    help="模型機率平滑視窗（預設 2，越大越平穩 delay 越久）")

    # Hysteresis 門檻
    ap.add_argument("--enter-thresh", type=float, default=0.55,
                    help="進入連續眨眼狀態的 Blink 機率門檻（預設 0.55）")
    ap.add_argument("--exit-thresh", type=float, default=0.40,
                    help="離開連續眨眼狀態的門檻（hysteresis 防抖，預設 0.40）")

    # Duration bucket 邊界
    ap.add_argument("--bucket-1", type=float, default=1.0,
                    help="忽略 / play_pause 邊界秒數（預設 1.0）")
    ap.add_argument("--bucket-2", type=float, default=2.5,
                    help="play_pause / next 邊界（預設 2.5）")
    ap.add_argument("--bucket-3", type=float, default=5.0,
                    help="next / prev 邊界（預設 5.0）")
    ap.add_argument("--bucket-max", type=float, default=8.0,
                    help="prev / 忽略（訊號黏住）邊界（預設 8.0）")

    ap.add_argument("--action-cooldown", type=float, default=1.5,
                    help="動作觸發後冷卻秒數（預設 1.5）")

    # AI 教練
    ap.add_argument("--insight-interval", type=float, default=20.0,
                    help="AI 即時解讀間隔秒（預設 20；需設 OPENAI_API_KEY）")

    args = ap.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n伺服器停止")


if __name__ == "__main__":
    main()
