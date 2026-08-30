"""WhatsApp / SMS notifier using Twilio's Content API.

Every send goes through an `actions` row so we can prove -- with a SQL
timestamp query -- that the mid-call WhatsApp fired BEFORE the call ended.
That comparison is the 15-point evidence checkpoint from plan section 6.

Design decisions worth naming:

- **DB-level idempotency.** The mid-call action has a partial unique index
  (`ux_actions_midcall_once`) so even if the classifier flips HOT twice
  in a call, only one WhatsApp fires. The Python catches the
  UniqueViolation and returns silently.

- **Fire-and-forget from the audio path.** Callers wrap send_* in
  asyncio.create_task. Never awaited on a user turn.

- **actions.sent_at populated ONLY on Twilio success.** A pending row
  with sent_at IS NULL and status='failed' is what the post-call retry
  queue reads.

Public API (all coroutines):
    send_whatsapp_midcall(call_id, to_phone, name=None, summary=None) -> str | None
    send_whatsapp_followup_context(...)  -- phase 6
    send_whatsapp_followup_resume(...)   -- phase 6
    send_whatsapp_callback_confirmed(...)-- phase 5
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Dict, Optional

import psycopg
from twilio.rest import Client as TwilioClient

logger = logging.getLogger("elevatebox.notifier")


# ------------------------------------------------------------- template SIDs

def _template_sid(key: str) -> str:
    sid = os.environ.get(key)
    if not sid:
        raise RuntimeError(f"{key} not set in env")
    return sid


# ------------------------------------------------------------- Twilio client

_twilio: Optional[TwilioClient] = None


def _twilio_client() -> TwilioClient:
    global _twilio
    if _twilio is None:
        _twilio = TwilioClient(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
    return _twilio


def _whatsapp_from() -> str:
    return os.environ["TWILIO_WHATSAPP_FROM"]  # e.g. whatsapp:+919875389511


# --------------------------------------------------- DB helpers (sync)

def _insert_pending_action_sync(call_id: int, action_type: str, payload: dict) -> Optional[int]:
    """Insert a pending action row. Returns the new id, or None if the
    partial unique index rejected it (i.e. already fired)."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO actions (call_id, type, status, payload)
                    VALUES (%s, %s, 'pending', %s)
                    RETURNING id
                    """,
                    (call_id, action_type, json.dumps(payload)),
                )
                return int(cur.fetchone()[0])
            except psycopg.errors.UniqueViolation:
                return None


def _mark_sent_sync(action_id: int, message_sid: str) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE actions
                SET status = 'sent',
                    sent_at = now(),
                    attempts = attempts + 1,
                    payload = payload || jsonb_build_object('message_sid', %s::text)
                WHERE id = %s
                """,
                (message_sid, action_id),
            )


def _mark_failed_sync(action_id: int, err: str) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE actions
                SET status = 'failed', attempts = attempts + 1, last_error = %s
                WHERE id = %s
                """,
                (err[:500], action_id),
            )


# ---------------------------------------------- mid-call WhatsApp (15 pts)

async def send_whatsapp_midcall(
    *,
    call_id: int,
    to_phone: str,
    name: Optional[str] = None,
    summary: Optional[str] = None,
) -> Optional[str]:
    """Fire `hot_lead_midcall` template. Returns Twilio message SID or None
    (None means either an idempotent skip or a genuine failure -- check the
    actions row for the difference)."""

    # Template body has {{1}} = name and {{2}} = summary of what was discussed.
    template_sid = _template_sid("WA_TEMPLATE_HOT_LEAD_MIDCALL")
    variables = {
        "1": (name or "there").strip(),
        "2": (summary or "your interest in an e-commerce website").strip(),
    }
    payload = {
        "to": to_phone,
        "template": "hot_lead_midcall",
        "template_sid": template_sid,
        "variables": variables,
    }

    # 1. Try to claim the "we're firing it" slot in Neon. Partial unique
    #    index refuses if there's already a whatsapp_midcall for this call.
    action_id = await asyncio.to_thread(
        _insert_pending_action_sync, call_id, "whatsapp_midcall", payload
    )
    if action_id is None:
        logger.info("whatsapp_midcall already fired for call_id=%s, skipping", call_id)
        return None

    logger.info("whatsapp_midcall firing: call_id=%s to=%s vars=%s",
                call_id, to_phone, variables)

    # 2. Call Twilio.
    def _do_twilio_send() -> str:
        msg = _twilio_client().messages.create(
            content_sid=template_sid,
            content_variables=json.dumps(variables),
            from_=_whatsapp_from(),
            to=f"whatsapp:{to_phone}",
        )
        return str(msg.sid)

    try:
        message_sid = await asyncio.to_thread(_do_twilio_send)
    except Exception as e:
        logger.exception("whatsapp_midcall SEND FAILED for call_id=%s: %s", call_id, e)
        await asyncio.to_thread(_mark_failed_sync, action_id, f"{type(e).__name__}: {e}")
        return None

    # 3. Mark sent with the Twilio SID.
    await asyncio.to_thread(_mark_sent_sync, action_id, message_sid)
    logger.info("whatsapp_midcall SENT: call_id=%s message_sid=%s", call_id, message_sid)
    return message_sid


# ---------------------------------------------- post-call follow-ups (10 pts)

async def _send_followup_template(
    *,
    action_type: str,
    call_id: int,
    to_phone: str,
    template_sid: str,
    variables: Dict[str, str],
) -> Optional[str]:
    """Shared send-and-persist logic for the two follow-up templates.

    Unlike whatsapp_midcall we do NOT insert with an idempotency
    constraint here -- if a Twilio send truly fails the row is left at
    status='failed' with last_error and a later retry pass can decide
    whether to try again. Two separate rows per call (one context, one
    resume) is the intended state.
    """
    payload = {
        "to": to_phone,
        "template": action_type.removeprefix("whatsapp_"),
        "template_sid": template_sid,
        "variables": variables,
    }

    def _insert_pending() -> int:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO actions (call_id, type, status, payload)
                    VALUES (%s, %s, 'pending', %s)
                    RETURNING id
                    """,
                    (call_id, action_type, json.dumps(payload)),
                )
                return int(cur.fetchone()[0])

    action_id = await asyncio.to_thread(_insert_pending)

    def _do_send() -> str:
        msg = _twilio_client().messages.create(
            content_sid=template_sid,
            content_variables=json.dumps(variables),
            from_=_whatsapp_from(),
            to=f"whatsapp:{to_phone}",
        )
        return str(msg.sid)

    try:
        message_sid = await asyncio.to_thread(_do_send)
    except Exception as e:
        logger.exception("%s SEND FAILED for call_id=%s: %s", action_type, call_id, e)
        await asyncio.to_thread(_mark_failed_sync, action_id, f"{type(e).__name__}: {e}")
        return None

    await asyncio.to_thread(_mark_sent_sync, action_id, message_sid)
    logger.info("%s SENT: call_id=%s message_sid=%s", action_type, call_id, message_sid)
    return message_sid


async def send_whatsapp_followup_context(
    *,
    call_id: int,
    to_phone: str,
    variables: Dict[str, str],
) -> Optional[str]:
    """Fire the `followup_context` template (image header = architecture)."""
    return await _send_followup_template(
        action_type="whatsapp_followup_context",
        call_id=call_id,
        to_phone=to_phone,
        template_sid=_template_sid("WA_TEMPLATE_FOLLOWUP_CONTEXT"),
        variables=variables,
    )


async def send_whatsapp_followup_resume(
    *,
    call_id: int,
    to_phone: str,
    variables: Dict[str, str],
) -> Optional[str]:
    """Fire the `followup_resume` template (document header = resume PDF)."""
    return await _send_followup_template(
        action_type="whatsapp_followup_resume",
        call_id=call_id,
        to_phone=to_phone,
        template_sid=_template_sid("WA_TEMPLATE_FOLLOWUP_RESUME"),
        variables=variables,
    )
