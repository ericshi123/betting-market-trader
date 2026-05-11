import os
import re
from dotenv import load_dotenv

from src.enricher import enrich_market

load_dotenv()

_client = None

_SYSTEM_PROMPT = (
    "You are a calibrated probability forecaster for prediction markets. "
    "Estimate the probability a given market resolves YES by reasoning carefully about: "
    "base rates for similar events, current evidence, resolution criteria, and time remaining. "
    "Calibration rule: prediction markets aggregate informed traders. "
    "Your estimate should not systematically differ from the market price without a strong specific reason. "
    "Avoid defaulting to extreme values (near 0 or near 1) — if you are uncertain, stay closer to the market price. "
    "Only diverge significantly when you have a concrete, articulable reason. "
    "Be concise. Output in the exact format requested — nothing else after SOURCE."
)


def _get_client():
    global _client
    if _client is None:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set in .env")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def estimate_probability(market: dict) -> dict:
    """
    Ask Claude to estimate the YES resolution probability for a market.
    Returns dict with: model_prob (float|None), confidence (str), rationale (str), reasoning (str).
    """
    market = enrich_market(market)
    client = _get_client()

    question = market.get("question", "")
    end_date = market.get("end_date", "unknown")
    yes_price = market.get("yes_price")
    description = market.get("description", "")
    volume = market.get("volume") or 0

    market_pct = f"{yes_price * 100:.1f}%" if yes_price is not None else "unknown"
    desc_block = f"\nDescription: {description}" if description else ""

    search_context = market.get("search_context", "")
    context_block = f"\n\nRecent context:\n{search_context}" if search_context else ""

    user_msg = f"""Predict the probability this prediction market resolves YES.

Question: {question}
Resolution date: {end_date}
Current market-implied probability (Yes): {market_pct}
Total volume traded: ${volume:,.0f}{desc_block}{context_block}

Reason step-by-step, then give your estimate.

Respond in EXACTLY this format (no extra text after SOURCE):
REASONING: [2-3 sentences]
PROBABILITY: [0.00–1.00]
CONFIDENCE: [low/medium/high]
RATIONALE: [one sentence — your single strongest reason]
SOURCE: [one of: sports, politics, crypto, finance, other]"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
        return _parse(resp.content[0].text)
    except Exception as exc:
        return {
            "model_prob": None,
            "confidence": "low",
            "rationale": f"API error: {str(exc)[:120]}",
            "reasoning": "",
            "error": str(exc),
        }


_VALID_SOURCES = {"sports", "politics", "crypto", "finance", "other"}


def _parse(text: str) -> dict:
    prob_m = re.search(r"PROBABILITY:\s*([0-9]*\.?[0-9]+)", text)
    conf_m = re.search(r"CONFIDENCE:\s*(low|medium|high)", text, re.IGNORECASE)
    rat_m = re.search(r"RATIONALE:\s*(.+?)(?=\nSOURCE:|\Z)", text, re.DOTALL)
    reason_m = re.search(r"REASONING:\s*(.+?)(?=PROBABILITY:)", text, re.DOTALL)
    source_m = re.search(r"SOURCE:\s*(\w+)", text, re.IGNORECASE)

    model_prob = None
    if prob_m:
        model_prob = max(0.0, min(1.0, float(prob_m.group(1))))

    confidence = conf_m.group(1).lower() if conf_m else "low"
    rationale = rat_m.group(1).strip() if rat_m else text.strip()[:200]
    reasoning = reason_m.group(1).strip() if reason_m else ""

    raw_source = source_m.group(1).lower() if source_m else "other"
    market_source = raw_source if raw_source in _VALID_SOURCES else "other"

    return {
        "model_prob": model_prob,
        "confidence": confidence,
        "rationale": rationale,
        "reasoning": reasoning,
        "market_source": market_source,
    }
