"""
Lightweight music web lookup for BCI reports.

This intentionally uses only the Python standard library. Results are cached
locally so the same track is not queried repeatedly during demos.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


CACHE_PATH = Path(__file__).with_name("music_search_cache.json")
CACHE_TTL_SEC = 30 * 24 * 60 * 60
USER_AGENT = "BCI-YT-Music-Student-Project/1.0"


def is_enabled() -> bool:
    return os.environ.get("BCI_MUSIC_WEB_SEARCH", "1").lower() not in {
        "0", "false", "no", "off"
    }


def enrich_tracks(tracks: list[dict], limit: int = 8) -> list[dict]:
    if not is_enabled() or not tracks:
        return tracks

    enriched = []
    for track in tracks[:limit]:
        row = dict(track)
        row["web"] = lookup_track(
            row.get("title", ""),
            row.get("artist", ""),
            row.get("album", ""),
        )
        enriched.append(row)
    enriched.extend(tracks[limit:])
    return enriched


def lookup_track(title: str, artist: str = "", album: str = "") -> dict:
    title = _clean(title)
    artist = _clean(artist)
    album = _clean(album)
    if not title:
        return {"status": "missing_title"}

    key = _cache_key(title, artist, album)
    cache = _load_cache()
    cached = cache.get(key)
    if cached and time.time() - cached.get("ts", 0) <= CACHE_TTL_SEC:
        return cached.get("data", {})

    query = f'"{title}" "{artist}" music genre mood BPM instrumental'.strip()
    data = {
        "query": query,
        "sources": [],
        "terms": {},
        "status": "ok",
    }
    try:
        text = _fetch_duckduckgo(query)
        results = _parse_duckduckgo(text)
        if results:
            data["sources"] = results[:3]
            data["terms"] = _extract_terms(" ".join(
                f"{r.get('title', '')} {r.get('snippet', '')}" for r in results[:5]
            ))
        else:
            data["status"] = "no_results"
    except Exception as exc:
        data = {
            "query": query,
            "sources": [],
            "terms": {},
            "status": f"error: {type(exc).__name__}",
        }

    cache[key] = {"ts": time.time(), "data": data}
    _save_cache(cache)
    return data


def _fetch_duckduckgo(query: str) -> str:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_duckduckgo(text: str) -> list[dict]:
    results = []
    lite_links = list(re.finditer(
        r"<a(?=[^>]+class='result-link')[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        text,
        re.S,
    ))
    if lite_links:
        for idx, match in enumerate(lite_links[:8]):
            next_start = lite_links[idx + 1].start() if idx + 1 < len(lite_links) else len(text)
            block = text[match.end():next_start]
            snippet_m = re.search(r"class='result-snippet'[^>]*>(.*?)</td>", block, re.S)
            results.append({
                "title": _strip_html(match.group(2)),
                "snippet": _strip_html(snippet_m.group(1)) if snippet_m else "",
                "url": _decode_duckduckgo_url(match.group(1)),
            })
        return results

    blocks = re.findall(r'<div class="result__body">(.*?)</div>\s*</div>', text, re.S)
    if not blocks:
        blocks = re.findall(r'<a rel="nofollow" class="result__a".*?</a>.*?(?:<a class="result__snippet".*?</a>)?', text, re.S)
    for block in blocks[:8]:
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        url_m = re.search(r'class="result__a"[^>]*href="([^"]+)"', block, re.S)
        title = _strip_html(title_m.group(1)) if title_m else ""
        snippet = _strip_html(snippet_m.group(1)) if snippet_m else ""
        url = html.unescape(url_m.group(1)) if url_m else ""
        if title or snippet:
            results.append({"title": title, "snippet": snippet, "url": url})
    return results


def _decode_duckduckgo_url(url: str) -> str:
    url = html.unescape(url)
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return qs["uddg"][0]
    return url


def _extract_terms(text: str) -> dict:
    text_l = text.lower()
    vocab = {
        "genre": [
            "lo-fi", "lofi", "classical", "jazz", "ambient", "electronic",
            "edm", "pop", "rock", "hip hop", "rap", "r&b", "indie",
            "soundtrack", "orchestral", "piano", "acoustic", "instrumental",
            "chill", "study music", "anime", "game music",
        ],
        "mood": [
            "calm", "relaxing", "chill", "upbeat", "energetic", "sad",
            "melancholic", "dreamy", "dark", "peaceful", "focus", "study",
        ],
        "audio_traits": [
            "instrumental", "vocal", "vocals", "lyrics", "piano", "guitar",
            "synth", "beat", "bpm", "tempo", "ambient",
        ],
    }
    found = {}
    for group, terms in vocab.items():
        hits = []
        for term in terms:
            if term in text_l and term not in hits:
                hits.append(term)
        if hits:
            found[group] = hits[:6]
    return found


def _strip_html(value: str) -> str:
    value = re.sub(r"<.*?>", " ", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def _clean(value: str, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _cache_key(title: str, artist: str, album: str) -> str:
    raw = f"{title}|{artist}|{album}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
