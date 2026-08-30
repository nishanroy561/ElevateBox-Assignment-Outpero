"""Post-call follow-up WhatsApp orchestration.

Fires two templates back-to-back the moment the call session closes:

    1. followup_context (image header = architecture.png on nishanroy.me)
       Body carries context {{2}}-{{4}} from the last classification,
       a per-state next-step line, and the operator mobile.

    2. followup_resume  (document header = resume.pdf on nishanroy.me)
       Body says "here's my resume as promised, mobile below".

Design notes:

- **Per-call**, not per-lead. If the same lead calls back later they get
  a second follow-up -- that is the desired behaviour for the scoring
  rubric ("references what I actually said" applies to THIS call, not
  a merged history).

- **Skips callback re-dials.** Rows in `calls` with is_callback_of IS
  NOT NULL are follow-ups TO an earlier call; sending a second follow-up
  would double the WhatsApps a lead gets after they name a callback.

- **Skips calls with no classification.** If the classifier never
  produced a row (very short call, LLM parse failed on every turn), we
  have nothing meaningful to say and send nothing. The evaluator will
  see a call attempt in `calls` but no follow-up -- honest partial
  behaviour rather than a generic "thanks for your interest" template.

- **Uses classifier fields verbatim**, not another LLM pass. The
  classifier's `products` / `budget` / `timeline` were already extracted
  from the transcript with an `evidence_quote` guardrail; re-summarising
  through another model would only add hallucination risk. The template
  body reads like a person because the variables ARE things the person
  said.

Public API:
    send_followup(call_id, phone) -> None
        Called from agent/worker.py's shutdown callback. Never awaited
        on the audio path (session is already closed by the time this
        runs, so latency does not affect the caller).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional, Dict

import psycopg

from services import notifier

logger = logging.getLogger("elevatebox.followup")

# --------------------------------------------------------- next-step lines
# One canned line per classifier state. Concrete, action-oriented, no
# "we appreciate your business" filler. If we ever want to personalise
# per-call, we replace these with a small LLM pass -- but the template
# body's specificity comes from {{2}}-{{4}}, not from {{5}}.
_STATE_NEXT_STEP = {
    "HOT":  "I'll send a scoped proposal by tomorrow evening.",
    "WARM": "Let's touch base again next week -- I'll follow up.",
    "COLD": "If you want to explore this later, my number is above.",
}


# --------------------------------------------------------------- helpers

def _clean(value: Optional[str], fallback: str, *, max_len: int = 180) -> str:
    """Take a classifier field, produce a clean template-variable string.
    Twilio Content variables are silently truncated somewhere around 250
    chars in practice; keep ourselves comfortably under."""
    if value is None:
        return fallback
    text = str(value).strip().strip("'").strip('"').strip()
    if not text:
        return fallback
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _is_callback_redial_sync(call_id: int) -> bool:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_callback_of FROM calls WHERE id = %s", (call_id,))
            row = cur.fetchone()
    return bool(row and row[0] is not None)


def _read_latest_classification_sync(call_id: int) -> Optional[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state, confidence, budget, products, timeline,
                       features, barrier, evidence_quote
                FROM classifications
                WHERE call_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (call_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "state":          row[0],
        "confidence":     float(row[1]) if row[1] is not None else 0.0,
        "budget":         row[2],
        "products":       row[3],
        "timeline":       row[4],
        "features":       list(row[5] or []),
        "barrier":        row[6],
        "evidence_quote": row[7],
    }


# ----------------------------------------------------- public entrypoint

async def send_followup(call_id: int, phone: str) -> None:
    """Fire the follow-up pair after a call finishes.

    Never raises; failures are logged and swallowed so a broken follow-up
    can't crash the worker's shutdown path. Individual send failures are
    persisted to the actions row with status='failed' for post-mortem.
    """
    # --- skip callback re-dials (they'd double the follow-ups the lead gets)
    try:
        is_redial = await asyncio.to_thread(_is_callback_redial_sync, call_id)
    except Exception as e:
        logger.warning("could not check is_callback_of for call_id=%s: %s", call_id, e)
        is_redial = False
    if is_redial:
        logger.info("call_id=%s is a callback re-dial -- skipping followup", call_id)
        return

    # --- need a classification to have anything meaningful to send
    try:
        cls = await asyncio.to_thread(_read_latest_classification_sync, call_id)
    except Exception as e:
        logger.warning("could not read classification for call_id=%s: %s", call_id, e)
        cls = None
    if cls is None:
        logger.info("no classification for call_id=%s -- skipping followup", call_id)
        return

    my_mobile = os.getenv("MY_MOBILE", "").strip() or "the number I called from"

    # --- build the six followup_context variables from the classification
    context_vars: Dict[str, str] = {
        "1": "there",                                    # {{1}} name
        "2": _clean(cls["products"], "your business"),    # {{2}} what they mentioned
        "3": _clean(cls["budget"],   "we did not get to budget yet"),  # {{3}}
        "4": _clean(cls["timeline"], "flexible"),         # {{4}}
        "5": _STATE_NEXT_STEP.get(cls["state"], "I'll follow up with details soon."),  # {{5}}
        "6": my_mobile,                                   # {{6}} mobile
    }

    # --- followup_resume: simpler, just name + mobile
    resume_vars: Dict[str, str] = {
        "1": "there",
        "2": my_mobile,
    }

    logger.info(
        "sending followup for call_id=%s state=%s products=%r budget=%r timeline=%r",
        call_id, cls["state"], context_vars["2"], context_vars["3"], context_vars["4"],
    )

    # --- fire context first (has the architecture image), then resume.
    # A one-second sleep between them keeps them arriving in the intended
    # visual order in the caller's WhatsApp.
    try:
        await notifier.send_whatsapp_followup_context(
            call_id=call_id, to_phone=phone, variables=context_vars,
        )
    except Exception as e:
        logger.exception("followup_context send raised: %s", e)

    await asyncio.sleep(1.0)

    try:
        await notifier.send_whatsapp_followup_resume(
            call_id=call_id, to_phone=phone, variables=resume_vars,
        )
    except Exception as e:
        logger.exception("followup_resume send raised: %s", e)

    logger.info("followup complete for call_id=%s", call_id)
