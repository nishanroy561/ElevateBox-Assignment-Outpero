"""Lead classifier -- Hot / Warm / Cold from the running transcript.

Runs after every user turn in a background task. Same structured-output
approach the phase 3.5 gate proved: strict JSON-only system prompt plus a
tolerant extractor that survives stray markdown fences.

The classifier is stateless; every call re-reads the transcript from Neon.
That keeps the worker's per-call state to nothing more than a "last known
state" flag for the mid-call fire.

Public API:
    classify_running_call(call_id) -> Classification | None
        Reads the transcript from `turns`, calls the LLM, writes the
        result to `classifications`, returns the parsed object.

Called from agent/worker.py inside asyncio.create_task, so it never
blocks the audio loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import psycopg
from openai import AsyncOpenAI

logger = logging.getLogger("elevatebox.classifier")

# ---------------------------------------------------------------- system prompt
# Deliberately strict. Sarvam's OpenAI-compat endpoint accepts response_format
# but not every model honours it; the belt-and-braces prompt guarantees clean
# JSON either way. Text mirrors scripts/sarvam_gate.py (proven 10/10 on the
# phase 3.5 gate).
_CLASSIFY_SYSTEM = """You classify sales leads for a voice agent selling
e-commerce website development. Read the transcript below and score the lead.

- HOT: asks price or timeline, "how soon can you start", names a real
  budget figure, wants to see previous work, agrees to a next step, gives
  a specific product/SKU count with intent to build.
- WARM: real need plus a named barrier -- "budget not right now", "someone
  else decides", "busy season", "send me the details so I can compare".
- COLD: no product, no budget, no timeline, purely curious, "just browsing".

OUTPUT FORMAT -- STRICT:
Respond with a SINGLE JSON object and NOTHING ELSE. No prose before or
after. No markdown code fences. Begin your response with { and end it
with }. The object must have exactly these keys:

{
  "state": "HOT" or "WARM" or "COLD",
  "confidence": a number between 0.0 and 1.0,
  "budget": string or null,
  "products": string or null,
  "timeline": string or null,
  "features": array of strings (empty array is fine, use [] not null),
  "barrier": string or null,
  "evidence_quote": a verbatim short substring from the transcript that
                    justifies the state (never empty; if unclear, pick
                    the strongest signal you can find)
}
"""


# ---------------------------------------------------------------------- model

@dataclass
class Classification:
    state: str            # HOT | WARM | COLD
    confidence: float
    budget: Optional[str] = None
    products: Optional[str] = None
    timeline: Optional[str] = None
    features: List[str] = field(default_factory=list)
    barrier: Optional[str] = None
    evidence_quote: str = ""


# ---------------------------------------------- tolerant JSON extraction

def _extract_json(text: str) -> dict:
    """Strip markdown fences, grab the outermost {...} if there is prose
    around it, then parse. Raises json.JSONDecodeError on total failure."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        text = text[i:j + 1]
    return json.loads(text)


# ------------------------------------------------------------- LLM call

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["SARVAM_API_KEY"],
            base_url="https://api.sarvam.ai/v1",
        )
    return _client


async def _classify_text(transcript_text: str) -> Optional[Classification]:
    """Actually call the model. Returns None on any failure -- caller decides
    whether to log a placeholder classification."""
    if not transcript_text.strip():
        return None
    client = _get_client()
    try:
        r = await client.chat.completions.create(
            model=os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b-conversations"),
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user",   "content": transcript_text},
            ],
            max_tokens=400,
            temperature=0.1,
        )
        content = r.choices[0].message.content or ""
        data = _extract_json(content)
        state = data["state"]
        if state not in ("HOT", "WARM", "COLD"):
            raise ValueError(f"bad state: {state!r}")
        features = data.get("features") or []
        if not isinstance(features, list):
            features = []
        return Classification(
            state=state,
            confidence=float(data.get("confidence", 0.5)),
            budget=data.get("budget"),
            products=data.get("products"),
            timeline=data.get("timeline"),
            features=[str(f) for f in features],
            barrier=data.get("barrier"),
            evidence_quote=str(data.get("evidence_quote") or "").strip() or "(no explicit quote)",
        )
    except Exception as e:
        logger.warning("classify failed: %s", e)
        return None


# --------------------------------------- transcript formatting

def _format_transcript(turns: List[Tuple[str, str]]) -> Tuple[str, Optional[int]]:
    """Format an in-memory list of (role, text) tuples into the transcript
    string the classifier expects. Also returns the seq of the last user
    turn (its index in the list) so persist_classification knows which
    turn triggered this run.

    Skips empty or None text so the model does not see blank USER: lines.
    """
    lines = []
    last_user_seq: Optional[int] = None
    for i, (role, text) in enumerate(turns):
        if not text or not text.strip():
            continue
        prefix = "USER" if role == "user" else "AGENT"
        lines.append(f"{prefix}: {text.strip()}")
        if role == "user":
            last_user_seq = i
    return "\n".join(lines), last_user_seq


# ---------------------------------------------------- persist to Neon

def _persist_sync(call_id: int, turn_seq: int, c: Classification) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO classifications
                    (call_id, turn_seq, state, confidence,
                     budget, products, timeline, features, barrier, evidence_quote)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (call_id, turn_seq, c.state, c.confidence,
                 c.budget, c.products, c.timeline, c.features,
                 c.barrier, c.evidence_quote),
            )


# ---------------------------------------------------- public entrypoint

async def classify_running_call(
    call_id: int,
    turns: List[Tuple[str, str]],
) -> Optional[Classification]:
    """Classify the running transcript given in-memory (role, text) tuples.

    Race-free: does not read from Neon (the writer task may not have
    committed the just-added turn yet -- reading here would sometimes
    return an empty transcript and silently skip). The caller owns the
    running transcript list and passes it in.
    """
    transcript_text, last_user_seq = _format_transcript(turns)
    if not transcript_text:
        logger.debug("classify_running_call: empty transcript for call_id=%s -- skipping", call_id)
        return None
    if last_user_seq is None:
        logger.debug("classify_running_call: no user turns yet for call_id=%s -- skipping", call_id)
        return None
    c = await _classify_text(transcript_text)
    if c is None:
        logger.warning("classify_running_call: model returned None for call_id=%s (transcript len=%d)",
                       call_id, len(transcript_text))
        return None
    try:
        await asyncio.to_thread(_persist_sync, call_id, last_user_seq, c)
    except Exception as e:
        logger.error("classification persist failed for call_id=%s: %s", call_id, e)
    logger.info("classified call_id=%s seq=%s -> %s (conf=%.2f) budget=%r timeline=%r",
                call_id, last_user_seq, c.state, c.confidence, c.budget, c.timeline)
    return c
