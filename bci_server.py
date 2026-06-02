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
       <2.0s     → ignored（生理性眨眼）
       2.0-4.0s  → play_pause
       4.0-6.5s  → next
       6.5-9.0s  → prev
       >9.0s     → ignored（訊號黏住或沒及時停動作）

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
import traceback
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
import music_search


CLIENTS = set()
LAST_STATUS = None
CALIBRATION_REQUESTED = False

# session 累積資料（給 LLM 用）
SESSION = {
    "start": None,
    "samples": [],   # 每次推論一筆 (t, relax, focus, blink, pred)
    "actions": [],   # 觸發動作 (t, action, hold_duration)
    "track_events": [],  # 歌曲切換/播放狀態 (t, title, artist, album, playing)
    "current_track": None,
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
    global CALIBRATION_REQUESTED
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
            if msg.get("type") == "request_calibration":
                CALIBRATION_REQUESTED = True
                await broadcast({
                    "type": "calibration",
                    "state": "queued",
                    "message": "校正即將開始",
                })
            if msg.get("type") == "track_update":
                _record_track_update(msg)
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[WS] client 斷開，剩 {len(CLIENTS)} 個")


# ── LLM 整合 ──────────────────────────────────────────────────────────────────
def _clean_track_text(value, limit=120):
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _track_key(track):
    if not track:
        return None
    return (
        track.get("title", ""),
        track.get("artist", ""),
        track.get("album", ""),
        bool(track.get("playing", True)),
    )


def _record_track_update(msg):
    title = _clean_track_text(msg.get("title"))
    if not title:
        return
    track = {
        "t": time.time(),
        "title": title,
        "artist": _clean_track_text(msg.get("artist")),
        "album": _clean_track_text(msg.get("album")),
        "playing": bool(msg.get("playing", True)),
    }
    if _track_key(track) == _track_key(SESSION.get("current_track")):
        return
    SESSION["current_track"] = track
    SESSION["track_events"].append(track)
    print(f"[music] {track['title']} - {track['artist']} playing={track['playing']}")


def _track_session_stats(samples):
    events = SESSION.get("track_events") or []
    if not events or not samples:
        return []
    session_start = SESSION["start"] or samples[0][0]
    session_end = samples[-1][0]
    rows = []
    for i, ev in enumerate(events):
        if not ev.get("playing", True):
            continue
        seg_start = max(float(ev["t"]), session_start)
        seg_end = session_end
        if i + 1 < len(events):
            seg_end = min(float(events[i + 1]["t"]), session_end)
        if seg_end <= seg_start:
            continue
        seg_samples = [s for s in samples if seg_start <= s[0] < seg_end]
        if len(seg_samples) < 4:
            continue
        arr = np.array([[s[1], s[2], s[3]] for s in seg_samples])
        mean = arr.mean(axis=0)
        rows.append({
            "title": ev.get("title", ""),
            "artist": ev.get("artist", ""),
            "album": ev.get("album", ""),
            "duration_sec": round(seg_end - seg_start, 1),
            "relax_pct": round(float(mean[0]), 2),
            "focus_pct": round(float(mean[1]), 2),
            "blink_pct": round(float(mean[2]), 2),
            "dominant": ["Relax", "Focus", "Blink"][int(np.argmax(mean))],
        })
    rows.sort(key=lambda x: x["duration_sec"], reverse=True)
    return rows[:12]


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
        "current_track": SESSION.get("current_track"),
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
    music = _track_session_stats(samples)
    top_focus_music = sorted(music, key=lambda x: x["focus_pct"], reverse=True)[:5]
    top_relax_music = sorted(music, key=lambda x: x["relax_pct"], reverse=True)[:5]
    return {
        "duration_min": round(dur_min, 1),
        "relax_pct": round(float(mean[0]), 2),
        "focus_pct": round(float(mean[1]), 2),
        "blink_pct": round(float(mean[2]), 2),
        "timeline": timeline,
        "n_play_pause": acts.count("play_pause"),
        "n_next": acts.count("next"),
        "n_prev": acts.count("prev"),
        "music": music,
        "top_focus_music": top_focus_music,
        "top_relax_music": top_relax_music,
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
    if stats.get("music") and music_search.is_enabled():
        print("  [AI] 查詢歌曲背景資訊中…")
        stats["music"] = await asyncio.to_thread(music_search.enrich_tracks, stats["music"])
        stats["top_focus_music"] = sorted(
            stats["music"], key=lambda x: x["focus_pct"], reverse=True)[:5]
        stats["top_relax_music"] = sorted(
            stats["music"], key=lambda x: x["relax_pct"], reverse=True)[:5]
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


class RawDurationDetector:
    """Detect continuous blink duration from raw Fp1 peak-to-peak activity."""

    def __init__(self, fs, window_sec=0.25, p2p_thresh=200.0,
                 merge_gap_sec=0.5, min_segment_sec=0.5):
        self.fs = fs
        self.window_sec = window_sec
        self.window_n = max(1, int(round(window_sec * fs)))
        self.p2p_thresh = p2p_thresh
        self.max_inactive_windows = max(0, int(round(merge_gap_sec / window_sec)))
        self.min_segment_sec = min_segment_sec

        self.buf = []
        self.is_active = False
        self.start_t = None
        self.last_active_end_t = None
        self.inactive_windows = 0
        self.peak_p2p = 0.0
        self.last_p2p = 0.0

    def update(self, samples, now_t):
        """Process new samples and return completed segments."""
        if len(samples) == 0:
            return []

        self.buf.extend(float(x) for x in samples)
        events = []

        while len(self.buf) >= self.window_n:
            window = np.asarray(self.buf[:self.window_n], dtype=np.float64)
            del self.buf[:self.window_n]

            # Estimate the wall-clock time represented by this completed window.
            remaining = len(self.buf)
            window_end_t = now_t - (remaining / self.fs)
            window_start_t = window_end_t - self.window_sec
            p2p = float(np.max(window) - np.min(window))
            self.last_p2p = p2p
            active = p2p >= self.p2p_thresh

            if active:
                if not self.is_active:
                    self.is_active = True
                    self.start_t = window_start_t
                    self.peak_p2p = p2p
                self.last_active_end_t = window_end_t
                self.peak_p2p = max(self.peak_p2p, p2p)
                self.inactive_windows = 0
            elif self.is_active:
                self.inactive_windows += 1
                if self.inactive_windows > self.max_inactive_windows:
                    event = self._finish_segment()
                    if event is not None:
                        events.append(event)

        return events

    def _finish_segment(self):
        start_t = self.start_t
        end_t = self.last_active_end_t
        peak_p2p = self.peak_p2p
        self.is_active = False
        self.start_t = None
        self.last_active_end_t = None
        self.inactive_windows = 0
        self.peak_p2p = 0.0

        if start_t is None or end_t is None:
            return None
        duration = end_t - start_t
        if duration < self.min_segment_sec:
            return None
        return {
            "start_t": start_t,
            "end_t": end_t,
            "duration": duration,
            "peak_p2p": peak_p2p,
        }

    def finish_open_segment(self, now_t):
        if not self.is_active:
            return None
        if self.last_active_end_t is None:
            self.last_active_end_t = now_t
        return self._finish_segment()

    def hold_duration(self, now_t):
        if not self.is_active or self.start_t is None:
            return 0.0
        return max(0.0, now_t - self.start_t)


class RawCalibrationCollector:
    """Collect raw p2p windows during a short personal blink calibration."""

    def __init__(self, fs, window_sec=0.25):
        self.fs = fs
        self.window_sec = window_sec
        self.window_n = max(1, int(round(window_sec * fs)))
        self.buf = []
        self.p2ps = []

    def update(self, samples):
        self.buf.extend(float(x) for x in samples)
        while len(self.buf) >= self.window_n:
            window = np.asarray(self.buf[:self.window_n], dtype=np.float64)
            del self.buf[:self.window_n]
            self.p2ps.append(float(np.max(window) - np.min(window)))

    def percentile(self, q, default=0.0):
        if len(self.p2ps) < 4:
            return default
        arr = np.asarray(self.p2ps, dtype=np.float64)
        return float(np.percentile(arr, q))

    def threshold(self, min_thresh, fallback_thresh, noise_p2ps=None, noise_factor=1.5):
        active_level = self.percentile(75, fallback_thresh)
        noise_threshold = fallback_thresh
        if noise_p2ps is not None and len(noise_p2ps) >= 4:
            noise_level = float(np.percentile(np.asarray(noise_p2ps, dtype=np.float64), 95))
            noise_threshold = max(fallback_thresh, noise_level * noise_factor)

        blink_threshold = fallback_thresh
        if active_level >= noise_threshold * 1.15:
            blink_threshold = max(min_thresh, active_level * 0.75)

        return max(min_thresh, fallback_thresh, noise_threshold, blink_threshold)


async def infer_loop(args):
    global LAST_STATUS, CALIBRATION_REQUESTED

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
                   "buckets": [args.bucket_1, args.bucket_2, args.bucket_3, args.bucket_max],
                   "control_mode": args.control_mode,
                   "raw": {
                       "window_sec": args.raw_window_sec,
                       "p2p_thresh": args.raw_p2p_thresh,
                       "merge_gap_sec": args.raw_merge_gap_sec,
                       "min_segment_sec": args.raw_min_segment_sec,
                       "noise_calibration_sec": args.noise_calibration_sec,
                       "calibration_sec": args.calibration_sec,
                   }}
    await broadcast(LAST_STATUS)

    buf = deque(maxlen=win_n)
    proba_hist = deque(maxlen=args.smooth)
    raw_detector = None
    pending_raw_events = []
    live_source = not args.source.startswith("file:")
    last_sample_t = time.time()
    last_no_data_log_t = 0.0
    calibration = None
    calibration_noise_p2ps = []
    calibration_stage = None
    calibration_started_t = None
    calibration_done = True
    manual_calibration = None
    manual_noise_p2ps = []
    manual_stage = None
    manual_started_t = None
    if args.control_mode == "raw":
        raw_detector = RawDurationDetector(
            fs,
            window_sec=args.raw_window_sec,
            p2p_thresh=args.raw_p2p_thresh,
            merge_gap_sec=args.raw_merge_gap_sec,
            min_segment_sec=args.raw_min_segment_sec,
        )
        if args.calibrate_control:
            CALIBRATION_REQUESTED = True

    # Hysteresis state machine
    is_blinking = False
    blink_start_t = None
    blink_last_active_t = None   # 最後一次 blink_p 仍 >= enter 的時刻（真正眨眼結束點）
    last_pred_t = 0.0
    last_action_t = 0.0
    last_insight_t = 0.0

    # 重置 session
    SESSION["start"] = None
    SESSION["samples"] = []
    SESSION["actions"] = []
    SESSION["track_events"] = []
    SESSION["current_track"] = None

    print(f"開始推論（每 {args.step}s 一次，平滑 {args.smooth}）")
    print(f"AI 教練：{'啟用' if llm_coach.is_available() else '停用（沒裝 openai 或沒設 OPENAI_API_KEY）'}"
          f"  即時解讀間隔 {args.insight_interval}s")
    print(f"Music web search: {'enabled' if music_search.is_enabled() else 'disabled'}")
    print(f"門檻：enter={args.enter_thresh}  exit={args.exit_thresh}")
    print(f"Bucket：忽略 < {args.bucket_1}s ≤ play_pause < {args.bucket_2}s "
          f"≤ next < {args.bucket_3}s ≤ prev < {args.bucket_max}s ≤ 忽略")
    print(f"提示：是「連續快速眨眼」N 秒（不是閉眼不動）。")
    print(f"Control mode: {args.control_mode}")
    if raw_detector is not None:
        print(f"Raw duration: window={args.raw_window_sec}s  p2p>={args.raw_p2p_thresh}  "
              f"merge_gap={args.raw_merge_gap_sec}s  min_segment={args.raw_min_segment_sec}s")
        if args.calibrate_control:
            print("Calibration: queued")
    print("=" * 60)

    try:
        while True:
            read_t = time.time()
            new, eof = src.read_new()
            if len(new):
                last_sample_t = read_t
            elif live_source and read_t - last_sample_t > 3.0 and read_t - last_no_data_log_t > 3.0:
                last_no_data_log_t = read_t
                print("[infer] waiting for raw samples from source...")
            if raw_detector is not None and CALIBRATION_REQUESTED:
                CALIBRATION_REQUESTED = False
                raw_detector = RawDurationDetector(
                    fs,
                    window_sec=args.raw_window_sec,
                    p2p_thresh=raw_detector.p2p_thresh,
                    merge_gap_sec=args.raw_merge_gap_sec,
                    min_segment_sec=args.raw_min_segment_sec,
                )
                pending_raw_events = []
                manual_stage = "noise"
                manual_calibration = RawCalibrationCollector(fs, window_sec=args.raw_window_sec)
                manual_noise_p2ps = []
                manual_started_t = read_t
                LAST_STATUS["calibration"] = {
                    "state": "noise",
                    "duration": args.noise_calibration_sec,
                    "message": "請保持穩定、不眨眼 3 秒，先量背景雜訊",
                }
                await broadcast({"type": "calibration", **LAST_STATUS["calibration"]})
            if (not calibration_done) and calibration_started_t is None and not CLIENTS:
                await asyncio.sleep(0.05)
                continue
            if (not calibration_done) and calibration_started_t is None:
                calibration_started_t = read_t
                LAST_STATUS["calibration"] = {
                    "state": "start",
                    "duration": args.calibration_sec,
                    "message": "請連續快速眨眼 5 秒進行個人化校正",
                }
                await broadcast({
                    "type": "calibration",
                    "state": "start",
                    "duration": args.calibration_sec,
                    "message": "請連續快速眨眼 5 秒進行個人化校正",
                })
            if len(new):
                buf.extend(new.tolist())
                if manual_calibration is not None and manual_stage is not None:
                    manual_calibration.update(new)
                elif calibration is not None and not calibration_done:
                    calibration.update(new)
                elif raw_detector is not None:
                    pending_raw_events.extend(raw_detector.update(new, read_t))
            if manual_calibration is not None and manual_stage is not None and manual_started_t is not None:
                stage_duration = args.noise_calibration_sec if manual_stage == "noise" else args.calibration_sec
                elapsed_manual = read_t - manual_started_t
                remaining_manual = max(0.0, stage_duration - elapsed_manual)
                min_windows = max(4, int(stage_duration / args.raw_window_sec * 0.5))
                enough_calibration_data = len(manual_calibration.p2ps) >= min_windows
                if elapsed_manual >= stage_duration and not enough_calibration_data:
                    LAST_STATUS["calibration"] = {
                        "state": manual_stage,
                        "remaining": 0.0,
                        "duration": stage_duration,
                        "message": "等待 BrainLink raw 訊號，請確認配戴與連線",
                    }
                    await broadcast({"type": "calibration", **LAST_STATUS["calibration"]})
                elif elapsed_manual >= stage_duration and manual_stage == "noise":
                    manual_noise_p2ps = list(manual_calibration.p2ps)
                    manual_stage = "blink"
                    manual_calibration = RawCalibrationCollector(fs, window_sec=args.raw_window_sec)
                    manual_started_t = read_t
                    LAST_STATUS["calibration"] = {
                        "state": "blink",
                        "duration": args.calibration_sec,
                        "message": "請連續快速眨眼 5 秒進行個人化校正",
                    }
                    await broadcast({"type": "calibration", **LAST_STATUS["calibration"]})
                elif elapsed_manual >= stage_duration:
                    raw_detector.p2p_thresh = manual_calibration.threshold(
                        args.raw_min_p2p_thresh,
                        args.raw_p2p_thresh,
                        noise_p2ps=manual_noise_p2ps,
                        noise_factor=args.raw_noise_factor,
                    )
                    LAST_STATUS["raw"]["p2p_thresh"] = raw_detector.p2p_thresh
                    LAST_STATUS["calibration"] = {
                        "state": "done",
                        "threshold": raw_detector.p2p_thresh,
                        "message": "校正完成，可以開始用眨眼控制",
                    }
                    manual_calibration = None
                    manual_stage = None
                    manual_started_t = None
                    await broadcast({"type": "calibration", **LAST_STATUS["calibration"]})
                    print(f"Manual calibration done: raw p2p threshold={raw_detector.p2p_thresh:.1f}")
                elif int(elapsed_manual * 2) != int((elapsed_manual - 0.02) * 2):
                    LAST_STATUS["calibration"] = {
                        "state": manual_stage,
                        "remaining": remaining_manual,
                        "duration": stage_duration,
                    }
                    await broadcast({"type": "calibration", **LAST_STATUS["calibration"]})
            if calibration is not None and not calibration_done and calibration_started_t is not None:
                elapsed_cal = read_t - calibration_started_t
                remaining_cal = max(0.0, args.calibration_sec - elapsed_cal)
                if elapsed_cal >= args.calibration_sec:
                    raw_detector.p2p_thresh = calibration.threshold(
                        args.raw_min_p2p_thresh, args.raw_p2p_thresh)
                    LAST_STATUS["raw"]["p2p_thresh"] = raw_detector.p2p_thresh
                    LAST_STATUS["calibration"] = {
                        "state": "done",
                        "threshold": raw_detector.p2p_thresh,
                        "message": "校正完成，可以開始用眨眼控制",
                    }
                    calibration_done = True
                    calibration = None
                    await broadcast({
                        "type": "calibration",
                        "state": "done",
                        "threshold": raw_detector.p2p_thresh,
                        "message": "校正完成，可以開始用眨眼控制",
                    })
                    print(f"Calibration done: raw p2p threshold={raw_detector.p2p_thresh:.1f}")
                elif int(elapsed_cal * 2) != int((elapsed_cal - 0.02) * 2):
                    LAST_STATUS["calibration"] = {
                        "state": "running",
                        "remaining": remaining_cal,
                        "duration": args.calibration_sec,
                    }
                    await broadcast({
                        "type": "calibration",
                        "state": "running",
                        "remaining": remaining_cal,
                        "duration": args.calibration_sec,
                    })
            if eof and raw_detector is not None and raw_detector.is_active:
                event = raw_detector.finish_open_segment(time.time())
                if event is not None:
                    pending_raw_events.append(event)
            if eof and live_source:
                print("[infer] live source reported eof; keeping server alive")
                eof = False
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
                if args.control_mode == "model" and is_blinking:
                    # 持續追蹤「最後一次仍在眨眼」的時刻，作為真正的眨眼結束點
                    if blink_p >= args.enter_thresh:
                        blink_last_active_t = now
                    if blink_p < args.exit_thresh:
                        # 用 last_active 而非 exit 偵測點計時，扣掉平滑造成的尾巴延遲
                        # → 與綠色進度條停止時顯示的秒數一致
                        end_t = blink_last_active_t if blink_last_active_t is not None else now
                        duration = max(0.0, end_t - blink_start_t)
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
                        blink_last_active_t = None
                elif args.control_mode == "model":
                    if blink_p >= args.enter_thresh:
                        is_blinking = True
                        blink_start_t = now
                        blink_last_active_t = now
                        print(f"  · 進入連續眨眼狀態（B={blink_p:.2f}）")

                # Hold duration（仍在連續眨眼狀態時即時更新）
                if raw_detector is not None:
                    while pending_raw_events:
                        event = pending_raw_events.pop(0)
                        duration = event["duration"]
                        action = action_for_duration(
                            duration, args.bucket_1, args.bucket_2,
                            args.bucket_3, args.bucket_max)
                        if action is None:
                            reason = "too short" if duration < args.bucket_1 else "too long"
                            print(f"  raw blink {duration:.2f}s peak_p2p={event['peak_p2p']:.0f} -> ignored ({reason})")
                        elif (now - last_action_t) >= args.action_cooldown:
                            fired_action = action
                            fired_duration = duration
                            print(f"  raw action: {action} (blink {duration:.2f}s, peak_p2p={event['peak_p2p']:.0f})")
                            last_action_t = now
                        else:
                            print(f"  raw blink {duration:.2f}s -> ignored (cooldown)")

                if raw_detector is not None:
                    control_is_blinking = raw_detector.is_active
                    hold_duration = raw_detector.hold_duration(now)
                else:
                    control_is_blinking = is_blinking
                    if is_blinking:
                        # 綠色進度條用「最後仍在眨眼」的時刻計時，與最終判定一致；
                        # blink_p 已掉到 enter 以下（平滑尾巴）時凍結，不再往前長
                        end_t = blink_last_active_t if blink_last_active_t is not None else now
                        hold_duration = max(0.0, end_t - blink_start_t)
                    else:
                        hold_duration = 0.0

                # 廣播
                msg = {
                    "type": "proba", "ts": now,
                    "relax": float(avg[0]), "focus": float(avg[1]), "blink": blink_p,
                    "pred": labels[pred],
                    "is_blinking": control_is_blinking,
                    "hold_duration": hold_duration,
                    "source": args.source,
                    "control_mode": args.control_mode,
                    "calibrating": (not calibration_done) or (manual_stage is not None),
                }
                if raw_detector is not None:
                    msg["raw_p2p"] = raw_detector.last_p2p
                    msg["raw_p2p_thresh"] = raw_detector.p2p_thresh
                await broadcast(msg)

                if fired_action is not None:
                    await broadcast({
                        "type": "action", "ts": now,
                        "action": fired_action,
                        "hold_duration": fired_duration,
                        "control_mode": args.control_mode,
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

                hold_str = f"hold={hold_duration:.1f}s" if control_is_blinking else ""
                print(f"[{time.strftime('%H:%M:%S')}] R={avg[0]:.2f} F={avg[1]:.2f} "
                      f"B={blink_p:.2f} → {labels[pred]} {hold_str}")

            await asyncio.sleep(0.02)
    except asyncio.CancelledError:
        pass
    except Exception:
        print("[infer] unexpected error:")
        traceback.print_exc()
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
    ap.add_argument("--control-mode", choices=["raw", "model"], default="raw",
                    help="raw uses Fp1 peak-to-peak duration for controls; model uses Blink probability")
    ap.add_argument("--raw-window-sec", type=float, default=0.25,
                    help="raw control: peak-to-peak window length in seconds")
    ap.add_argument("--raw-p2p-thresh", type=float, default=200.0,
                    help="raw control: window peak-to-peak threshold")
    ap.add_argument("--raw-min-p2p-thresh", type=float, default=120.0,
                    help="raw calibration: minimum allowed personalized p2p threshold")
    ap.add_argument("--raw-merge-gap-sec", type=float, default=0.5,
                    help="raw control: merge active windows separated by this much silence")
    ap.add_argument("--raw-min-segment-sec", type=float, default=0.5,
                    help="raw control: ignore shorter raw activity segments")
    ap.add_argument("--calibrate-control", action=argparse.BooleanOptionalAction, default=False,
                    help="run a short personal raw blink calibration before enabling controls")
    ap.add_argument("--noise-calibration-sec", type=float, default=3.0,
                    help="manual calibration noise-floor duration in seconds")
    ap.add_argument("--raw-noise-factor", type=float, default=1.5,
                    help="manual calibration: threshold is at least noise p95 times this factor")
    ap.add_argument("--calibration-sec", type=float, default=5.0,
                    help="personal calibration duration in seconds")

    ap.add_argument("--enter-thresh", type=float, default=0.55,
                    help="進入連續眨眼狀態的 Blink 機率門檻（預設 0.55）")
    ap.add_argument("--exit-thresh", type=float, default=0.40,
                    help="離開連續眨眼狀態的門檻（hysteresis 防抖，預設 0.40）")

    # Duration bucket 邊界
    ap.add_argument("--bucket-1", type=float, default=2.0,
                    help="忽略 / play_pause 邊界秒數（預設 2.0）")
    ap.add_argument("--bucket-2", type=float, default=4.0,
                    help="play_pause / next 邊界（預設 4.0）")
    ap.add_argument("--bucket-3", type=float, default=6.5,
                    help="next / prev 邊界（預設 6.5）")
    ap.add_argument("--bucket-max", type=float, default=9.0,
                    help="prev / 忽略（訊號黏住）邊界（預設 9.0）")

    ap.add_argument("--action-cooldown", type=float, default=2.5,
                    help="動作觸發後冷卻秒數（預設 2.5）")

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
