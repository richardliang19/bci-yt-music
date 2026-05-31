"""
LLM 腦波教練（OpenAI）
─────────────────────────────────────────────────────────────
給 bci_server.py 用。提供兩個 async 函式：
  - insight(window_stats)  → 即時一句話狀態解讀
  - report(session_stats)  → session 結束後的完整個人化報告

API key 從環境變數 OPENAI_API_KEY 讀（絕不寫死在程式碼）。
若沒裝 openai 或沒設 key，會回傳 None，主系統照常運作（LLM 功能自動停用）。

成本：預設 gpt-4o-mini（便宜），insight 每次約幾百 token，report 約 1-2K token。
"""

import os
import json

MODEL = os.environ.get("BCI_LLM_MODEL", "gpt-4o-mini")

_client = None
_disabled_reason = None


def _get_client():
    global _client, _disabled_reason
    if _client is not None:
        return _client
    if _disabled_reason is not None:
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        _disabled_reason = "未安裝 openai（pip install openai）"
        print(f"[LLM] 停用：{_disabled_reason}")
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        _disabled_reason = "未設定 OPENAI_API_KEY 環境變數"
        print(f"[LLM] 停用：{_disabled_reason}")
        return None
    _client = AsyncOpenAI()
    print(f"[LLM] 已啟用（model={MODEL}）")
    return _client


def is_available():
    return _get_client() is not None


# ── 即時狀態解讀 ──────────────────────────────────────────────────────────────
async def insight(window_stats: dict) -> str | None:
    """
    window_stats 例：
      {"window_sec": 30, "relax_pct": 0.2, "focus_pct": 0.65, "blink_pct": 0.15,
       "dominant": "Focus", "focus_trend": "上升", "actions": ["play_pause"]}
    回傳一句繁體中文解讀，失敗回 None。
    """
    client = _get_client()
    if client is None:
        return None
    prompt = (
        "你是一個腦波專注力教練。根據以下最近 EEG 狀態統計，用「一句」繁體中文"
        "給使用者即時回饋（≤30 字，自然口語、可帶鼓勵或提醒，不要列點、不要前綴）。\n\n"
        f"資料：{json.dumps(window_stats, ensure_ascii=False)}"
    )
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM] insight 失敗：{e}")
        return None


# ── Session 結束報告 ──────────────────────────────────────────────────────────
async def report(session_stats: dict) -> dict | None:
    """
    session_stats 例：
      {"duration_min": 12.5, "relax_pct": .., "focus_pct": .., "blink_pct": ..,
       "timeline": [{"min":0,"dominant":"Focus"},...],
       "music": [{"title":..., "artist":..., "focus_pct":..., "relax_pct":..., "web":...}, ...],
       "n_play_pause":2, "n_next":3, "n_prev":1}
    回傳 dict：{"summary":..., "observation":..., "music_observation":..., "suggestion":..., "focus_score":int}
    失敗回 None。
    """
    client = _get_client()
    if client is None:
        return None
    prompt = (
        "你是一個腦波專注力教練。根據以下整段使用 session 的 EEG 統計，"
        "輸出 JSON（繁體中文），欄位：\n"
        '  "summary": 一句總結這次表現,\n'
        '  "observation": 一句具體觀察（哪段專注度高/低、跳歌頻率等）,\n'
        '  "music_observation": 2句，根據 music 欄位的 EEG 平均值與 web.sources/web.terms，具體比較哪些歌曲、歌手或音樂特徵較偏專注/放鬆；若 web.status 不是 ok 或資料不足請明說,\n'
        '  "suggestion": 一句下次的具體建議（含可嘗試的音樂特徵如曲風、節奏、人聲/純音樂，但不要宣稱因果）,\n'
        '  "focus_score": 0-100 整數專注度評分\n'
        "只輸出 JSON，不要其他文字。\n\n"
        f"資料：{json.dumps(session_stats, ensure_ascii=False)}"
    )
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.6,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[LLM] report 失敗：{e}")
        return None
