"""Multi-agent debate — AlphaSwarm Bull / Bear / PM consensus."""
from __future__ import annotations

from typing import Any


def run_debate(
    technical: dict,
    news: dict,
    vol: dict,
    settings,
    *,
    history: dict | None = None,
) -> dict[str, Any]:
    symbol = technical.get("symbol", "?")
    if settings.gemini_key or settings.openai_key:
        try:
            debate = _llm_debate(technical, news, vol, settings, history=history)
        except Exception:
            debate = _rule_debate(technical, news, vol, history=history)
    else:
        debate = _rule_debate(technical, news, vol, history=history)

    if history and history.get("ok"):
        from .feedback import apply_history_to_debate

        debate = apply_history_to_debate(debate, history)
    return debate


def _rule_debate(tech: dict, news: dict, vol: dict, history: dict | None = None) -> dict[str, Any]:
    if float(news.get("score") or 0) <= -0.55:
        return {
            "action": "HOLD",
            "confidence": 40.0,
            "summary": "macro VETO — extreme bearish news",
            "transcript": [{"agent": "PM", "text": "VETO active"}],
        }
    score = tech.get("quant_score", 50)
    nscore = news.get("score", 0) * 30
    combined = score + nscore
    if news.get("label") == "bullish":
        combined += 8
    elif news.get("label") == "bearish":
        combined -= 8

    if combined >= 62:
        action, conf = "BUY", min(95, combined)
    elif combined <= 38:
        action, conf = "SELL", min(95, 100 - combined)
    else:
        action, conf = "HOLD", 50.0

    p_side = tech.get("pattern_side") if tech.get("pattern") else None
    p_vol = bool(tech.get("pattern_volume_ok"))
    pcr_zone = tech.get("pcr_zone")
    pcr_bias = tech.get("pcr_bias")
    confluence_up = p_side == "up" and pcr_bias == "BUY"
    confluence_dn = p_side == "down" and pcr_bias == "SELL"
    conflict = (p_side == "up" and pcr_zone == "resistance") or (p_side == "down" and pcr_zone == "support")

    bo_side = tech.get("breakout_side") if tech.get("breakout") else None
    if confluence_up:
        action, conf = "BUY", max(conf if action == "BUY" else 70.0, 76.0)
    elif confluence_dn:
        action, conf = "SELL", max(conf if action == "SELL" else 70.0, 76.0)
    elif conflict:
        conf = max(40.0, conf - 10)
    elif bo_side == "up":
        action, conf = "BUY", max(conf if action == "BUY" else 68.0, 72.0)
    elif bo_side == "down":
        action, conf = "SELL", max(conf if action == "SELL" else 68.0, 72.0)
    elif p_side == "up" and p_vol:
        action, conf = "BUY", max(conf if action == "BUY" else 64.0, 70.0)
    elif p_side == "down" and p_vol:
        action, conf = "SELL", max(conf if action == "SELL" else 64.0, 70.0)
    elif pcr_bias == "BUY" and action == "HOLD":
        action, conf = "BUY", max(conf, 66.0)
    elif pcr_bias == "SELL" and action == "HOLD":
        action, conf = "SELL", max(conf, 66.0)
    elif vol.get("regime") == "cheap" and action == "HOLD":
        action, conf = "VOL", 60.0
    elif vol.get("regime") == "rich" and action == "HOLD":
        action, conf = "SELL_VOL", 62.0

    bias = tech.get("indicator_bias")
    if action == "BUY" and bias == "down":
        conf = max(38.0, conf - 12)
    elif action == "SELL" and bias == "up":
        conf = max(38.0, conf - 12)
    elif action == "BUY" and bias == "up":
        conf = min(95.0, conf + 5)
    elif action == "SELL" and bias == "down":
        conf = min(95.0, conf + 5)

    if history and history.get("ok"):
        hist_bias = history.get("regime_bias")
        if action == "HOLD" and hist_bias in ("BUY", "SELL", "VOL", "SELL_VOL"):
            action, conf = hist_bias, max(conf, 58.0)
        transcript_extra = history.get("summary", "")
    else:
        transcript_extra = ""

    pa_side = tech.get("pa_side")
    if action == "BUY" and tech.get("pa_hostile_buy"):
        action, conf = "HOLD", min(conf, 40.0)
    elif action == "SELL" and tech.get("pa_hostile_sell"):
        action, conf = "HOLD", min(conf, 40.0)
    elif action == "BUY" and pa_side == "up":
        conf = min(95.0, conf + 8)
    elif action == "SELL" and pa_side == "down":
        conf = min(95.0, conf + 8)
    elif action == "BUY" and pa_side == "down":
        conf = max(38.0, conf - 16)
    elif action == "SELL" and pa_side == "up":
        conf = max(38.0, conf - 16)

    scalp = tech.get("scalp") or {}
    if scalp.get("ok"):
        if scalp.get("side") == "up":
            if action != "BUY":
                action, conf = "BUY", max(conf if action == "BUY" else 68.0, 72.0)
            else:
                conf = min(95.0, conf + 10)
        elif scalp.get("side") == "down":
            if action != "SELL":
                action, conf = "SELL", max(conf if action == "SELL" else 68.0, 72.0)
            else:
                conf = min(95.0, conf + 10)

    transcript = [
        {
            "agent": "Tape",
            "text": tech.get("pa_summary") or "price action n/a",
        },
        {
            "agent": "Bull",
            "text": f"Quant {tech.get('quant_score')} trend {tech.get('trend')}"
            + (f" · {tech.get('breakout_summary')}" if tech.get("breakout") else "")
            + (f" · {tech.get('pattern_summary')}" if tech.get("pattern") else ""),
        },
        {
            "agent": "Bear",
            "text": f"News {news.get('label')} IV/RV {vol.get('iv_rv_ratio')}"
            + (f" · {tech.get('pcr_summary')}" if tech.get("pcr_summary") else ""),
        },
        {
            "agent": "Quant",
            "text": tech.get("indicator_summary") or "indicators n/a",
        },
        {
            "agent": "Historian",
            "text": transcript_extra or "insufficient history",
        },
        {"agent": "PM", "text": f"Consensus {action} @ {conf:.0f}%"},
    ]
    return {
        "action": action,
        "confidence": conf,
        "summary": transcript[-1]["text"],
        "transcript": transcript,
    }


def _llm_debate(tech: dict, news: dict, vol: dict, settings, *, history: dict | None = None) -> dict[str, Any]:
    import json

    hist = json.dumps(history or {"ok": False})
    prompt = f"""You are a 3-agent committee (Bull, Bear, PM) for {tech.get('symbol')}.
Technical: {json.dumps(tech)}
News: {json.dumps(news)}
Volatility: {json.dumps(vol)}
Historical regime: {hist}
Chart patterns (morning/evening star, double top/bottom, H&S, doji) and put/call ratio are on Technical when present.
PCR ≤ 0.5 = resistance (bearish). PCR ≥ 1.5 = support (bullish). Volume-confirmed patterns outweigh low-volume dojis.
Price action (structure, wicks, close location, failed breaks) is authoritative: do not BUY into sell-side rejection or a downtrend CHOCH, and do not SELL into buy-side rejection or an uptrend CHOCH.
Use EMA stack (5 weekly / 20 monthly / 63 quarterly), MACD, RSI, stochastic, Bollinger %B, and Elliott-style swing phase. Do not BUY into RSI>75 at the upper band or SELL into RSI<25 at the lower band. Do not fight the quarterly EMA without a breakout.
Return JSON: {{"action":"BUY|SELL|HOLD|VOL|SELL_VOL","confidence":0-100,"summary":"...","transcript":[{{"agent":"...","text":"..."}}]}}"""
    if settings.gemini_key:
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(resp.text)
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content or "{}")
