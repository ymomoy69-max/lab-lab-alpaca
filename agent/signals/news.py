"""News sentiment — multi-headline aggregate with recency decay."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

BULL = ("surge", "beat", "upgrade", "record", "growth", "strong", "rally", "buy", "outperform")
BEAR = ("miss", "downgrade", "cut", "weak", "lawsuit", "decline", "sell", "warning", "layoff")


def _headlines(raw: Any) -> list[dict]:
    if isinstance(raw, dict):
        for k in ("news", "articles", "items", "data"):
            v = raw.get(k)
            if isinstance(v, list):
                return v
    if isinstance(raw, list):
        return raw
    return []


def _recency_weight(item: dict, idx: int) -> float:
    """Newer headlines weigh more; index 0 is freshest from API."""
    ts = item.get("created_at") or item.get("updated_at")
    if ts:
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
            return max(0.3, 1.0 - age_h / 72)
        except ValueError:
            pass
    return max(0.4, 1.0 - idx * 0.12)


def keyword_sentiment(text: str) -> tuple[str, float, float]:
    t = text.lower()
    b = sum(1 for w in BULL if w in t)
    s = sum(1 for w in BEAR if w in t)
    if b > s:
        return "bullish", min(0.95, 0.55 + 0.08 * b), 0.55 + 0.05 * b
    if s > b:
        return "bearish", max(-0.95, -0.55 - 0.08 * s), 0.55 + 0.05 * s
    return "neutral", 0.0, 0.4


def llm_sentiment(symbol: str, headline: str, settings) -> tuple[str, float, float, str]:
    prompt = (
        f"Analyze this headline for {symbol}. Return JSON only: "
        '{"label":"bullish|bearish|neutral","score":-1 to 1,"confidence":0 to 1,"reasoning":"..."}\n'
        f"Headline: {headline}"
    )
    if settings.gemini_key:
        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            data = json.loads(resp.text)
            return (
                data.get("label", "neutral"),
                float(data.get("score", 0)),
                float(data.get("confidence", 0.5)),
                data.get("reasoning", ""),
            )
        except Exception:
            pass
    if settings.openai_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return (
                data.get("label", "neutral"),
                float(data.get("score", 0)),
                float(data.get("confidence", 0.5)),
                data.get("reasoning", ""),
            )
        except Exception:
            pass
    label, score, conf = keyword_sentiment(headline)
    return label, score, conf, "keyword fallback"


def analyze_symbol(symbol: str, news_raw: Any, settings, *, max_headlines: int = 8) -> dict[str, Any]:
    items = _headlines(news_raw)[:max_headlines]
    if not items:
        return {
            "symbol": symbol,
            "label": "neutral",
            "score": 0.0,
            "confidence": 0.35,
            "headline": "",
            "headline_count": 0,
            "reasoning": "no news",
        }

    weighted_score = 0.0
    weight_sum = 0.0
    labels = {"bullish": 0, "bearish": 0, "neutral": 0}
    snippets: list[str] = []

    for idx, item in enumerate(items):
        headline = str(item.get("headline") or item.get("title") or "")
        if not headline:
            continue
        label, score, conf, reasoning = llm_sentiment(symbol, headline, settings)
        w = _recency_weight(item, idx) * conf
        weighted_score += score * w
        weight_sum += w
        labels[label] = labels.get(label, 0) + 1
        snippets.append(headline[:80])

    if weight_sum <= 0:
        label, score, conf = "neutral", 0.0, 0.35
    else:
        score = weighted_score / weight_sum
        if score > 0.15:
            label = "bullish"
        elif score < -0.15:
            label = "bearish"
        else:
            label = "neutral"
        conf = min(0.95, 0.45 + weight_sum / max(len(items), 1) * 0.15)

    top = str(items[0].get("headline") or items[0].get("title") or "")
    return {
        "symbol": symbol,
        "label": label,
        "score": round(score, 3),
        "confidence": round(conf, 3),
        "headline": top[:200],
        "headline_count": len(snippets),
        "label_counts": labels,
        "reasoning": f"aggregate {len(snippets)} headlines · {label}",
    }
