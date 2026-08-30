"""Spoken time -> IST datetime.

The caller says "kal subah", "call me tomorrow evening", "next week
sometime", "Tuesday at 10:30" -- we need an ISO-8601 UTC datetime to
persist and to schedule the re-dial against.

Approach: hand it to sarvam-105b-conversations with a very strict prompt.
Rule-based parsing here would need per-language regex tables for
Hindi/Telugu-Roman/English and still miss common phrasings. The LLM
handles all three natively; one API call per callback booking is a
negligible cost for a rare event.

Defaults for vague phrases (from plan.md section 6):
    "tomorrow morning"  -> next day 10:30 IST
    "evening"           -> 18:00 IST
    "next week"         -> next Monday 11:00 IST
    weekend             -> shift to next Monday
    a bare "morning"    -> 10:30 IST today or tomorrow depending on now

The output is always UTC (Postgres timestamptz is what we persist), but
we anchor the model's reasoning in IST because that is how the caller
thinks about the time they named.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger("elevatebox.timeparse")

IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------ model spec

@dataclass
class ParsedCallback:
    scheduled_for_utc: datetime          # what we persist
    scheduled_for_ist: datetime          # what we say back to the caller
    confidence: float                    # 0.0..1.0
    interpretation: str                  # e.g. "tomorrow morning => 2026-08-28 10:30 IST"
    speak_line: str                      # ready-to-say confirmation in same language as phrase


# --------------------------------------------------------- prompt

_SYSTEM_TEMPLATE = """You are the time parser for a voice sales agent
based in Hyderabad (IST). Your job is to convert a spoken phrase into a
concrete future datetime for a callback.

The current datetime in IST is: {now_ist}
(equivalent UTC: {now_utc})

Rules:

- Output must be strictly in the future. If the caller says "morning"
  and it is already 11 AM IST, schedule for tomorrow morning.
- Weekend days (Saturday, Sunday) shift to next Monday at the same
  named time. Real business gets done Monday-Friday.
- Vague-phrase defaults:
    "morning" or equivalent    -> 10:30 IST
    "afternoon"                -> 15:00 IST
    "evening"                  -> 18:00 IST
    "night" or "late"          -> refuse: schedule tomorrow at 10:30 instead
    "tomorrow"    with no time -> tomorrow 10:30 IST
    "next week"   with no day  -> next Monday 11:00 IST
    "later today" with no time -> +2 hours from now, capped 18:00 IST
- Handle Hindi ("kal", "parson", "subah", "sham"), Telugu-Roman
  ("repu", "ellundi", "udayam", "sayantram") and English uniformly.
- The `speak_line` should be one short sentence the agent can SAY OUT
  LOUD in the same language the caller used, confirming the slot.
  Example inputs and speak_lines:
    "kal subah" (Hindi)  -> "Set - kal subah 10:30 baje. Main call karungi."
    "tomorrow morning"    -> "Set - tomorrow at 10:30 in the morning, I will call you then."
    "repu udayam"         -> "Set - repu udayam 10:30 ki. Nenu call chestaanu."

OUTPUT FORMAT -- STRICT:
Return a SINGLE JSON object with these keys (no prose, no markdown fences):
{{
  "scheduled_for_ist": "YYYY-MM-DDTHH:MM:00+05:30",
  "confidence": 0.0..1.0,
  "interpretation": "one-line English trace of how you resolved the phrase",
  "speak_line": "the sentence Priya says to confirm, in the caller's language"
}}

Begin with {{ and end with }}.
"""


# --------------------------------------------------------- client

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["SARVAM_API_KEY"],
            base_url="https://api.sarvam.ai/v1",
        )
    return _client


# -------------------------------------------------- tolerant JSON

def _extract_json(text: str) -> dict:
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


# ---------------------------------------------------- public entry

async def parse_spoken_time(spoken_phrase: str,
                            now: Optional[datetime] = None) -> Optional[ParsedCallback]:
    """Parse a spoken time phrase into a UTC datetime + confirmation line.

    Returns None on parse failure -- caller should fall back to a
    deterministic default (e.g. tomorrow 10:30 IST) rather than crashing.
    """
    if not spoken_phrase or not spoken_phrase.strip():
        return None
    now = now or datetime.now(tz=timezone.utc)
    now_ist = now.astimezone(IST)
    system_prompt = _SYSTEM_TEMPLATE.format(
        now_ist=now_ist.strftime("%A %Y-%m-%d %H:%M:%S %z"),
        now_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    client = _get_client()
    try:
        r = await client.chat.completions.create(
            model=os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b-conversations"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": spoken_phrase.strip()},
            ],
            max_tokens=250,
            temperature=0.1,
        )
        content = r.choices[0].message.content or ""
        data = _extract_json(content)
        ist_dt = datetime.fromisoformat(data["scheduled_for_ist"])
        if ist_dt.tzinfo is None:
            ist_dt = ist_dt.replace(tzinfo=IST)
        utc_dt = ist_dt.astimezone(timezone.utc)
        # If the model returned a past time -- most commonly because the
        # caller named a weekday whose last occurrence was earlier this
        # week -- bump forward one week and re-check. This is idiomatic
        # for phone conversations: "call me Tuesday" always means the
        # next Tuesday, never the one we already had.
        if utc_dt <= now:
            bumped = utc_dt + timedelta(days=7)
            if bumped > now and bumped <= now + timedelta(days=60):
                logger.info("timeparse: past slot %s -> bumped forward one week to %s",
                            utc_dt, bumped)
                utc_dt = bumped
                ist_dt = ist_dt + timedelta(days=7)
            else:
                logger.warning("timeparse produced non-future slot %s (now=%s)", utc_dt, now)
                return None
        if utc_dt > now + timedelta(days=60):
            logger.warning("timeparse produced far-future slot %s", utc_dt)
            return None
        return ParsedCallback(
            scheduled_for_utc=utc_dt,
            scheduled_for_ist=ist_dt,
            confidence=float(data.get("confidence", 0.5)),
            interpretation=str(data.get("interpretation", ""))[:300],
            speak_line=str(data.get("speak_line", "")).strip()
                       or "Set. I'll call you at the time we discussed.",
        )
    except Exception as e:
        logger.warning("timeparse failed for %r: %s", spoken_phrase, e)
        return None


# -------------------------------------------------- fallback

def default_slot(now: Optional[datetime] = None) -> ParsedCallback:
    """Deterministic 'if parsing failed, book tomorrow 10:30 IST anyway'
    fallback. Used by book_callback when the LLM returns None."""
    now = now or datetime.now(tz=timezone.utc)
    ist_now = now.astimezone(IST)
    target_ist = (ist_now + timedelta(days=1)).replace(
        hour=10, minute=30, second=0, microsecond=0
    )
    # If tomorrow is a weekend, push to Monday.
    if target_ist.weekday() >= 5:
        target_ist += timedelta(days=(7 - target_ist.weekday()))
    return ParsedCallback(
        scheduled_for_utc=target_ist.astimezone(timezone.utc),
        scheduled_for_ist=target_ist,
        confidence=0.3,
        interpretation="fallback: tomorrow 10:30 IST (parser did not resolve phrase)",
        speak_line=f"Set - kal subah 10:30 baje, main call karungi.",
    )
