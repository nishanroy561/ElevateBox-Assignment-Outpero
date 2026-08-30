"""Outpero webhook handlers for the ElevateBox voice agent.

Outpero POSTs two types of events here:

    POST /webhooks/outpero/midcall
        Fired by Outpero's Custom API action mid-call when HOT intent is
        detected. Sends the mid-call WhatsApp via Twilio.

    POST /webhooks/outpero/postcall
        Fired by Outpero's Post-Call Webhook after the call ends.
        Sends the two follow-up WhatsApps (architecture + resume).

Both endpoints are idempotent -- the DB unique index on actions prevents
duplicate WhatsApp fires even if Outpero retries the webhook.

Outpero payload shape (both endpoints):
{
    "call_id":         "outpero-call-uuid",   # Outpero's own call ID (string)
    "phone":           "+919876543210",        # E.164 caller phone
    "lead_name":       "Ramesh",              # may be null/absent
    "lead_temperature":"HOT",                 # HOT | WARM | COLD (postcall only)
    "products":        "sarees, 60 SKUs",     # captured outcome
    "budget":          "80000",               # captured outcome
    "timeline":        "within a month",      # captured outcome
    "features":        "payments, delivery",  # captured outcome
    "barrier":         null,                  # WARM only
    "next_step":       "WhatsApp sent"        # what was agreed
}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from services import db, notifier
from services.followup import send_followup

logger = logging.getLogger("elevatebox.outpero_webhook")

router = APIRouter(prefix="/webhooks/outpero", tags=["outpero"])


import re

# ---------------------------------------------------------------- helpers

def _normalise_phone(raw: str | None) -> str | None:
    """Extract E.164 phone. Handles both plain phone and test labels like 'Lead\'s phone number+9198...''"""
    if not raw:
        return None
    match = re.search(r"\+?[0-9]{10,14}", str(raw))
    if match:
        phone = match.group(0)
        if not phone.startswith("+"):
            phone = "+" + phone
        return phone
    raw = raw.strip()
    if not raw.startswith("+"):
        raw = "+" + raw
    return raw


def _clean_str(raw: str | None, prefix_to_strip: str = "") -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if prefix_to_strip and s.lower().startswith(prefix_to_strip.lower()):
        s = s[len(prefix_to_strip):].strip()
    return s or None


def _midcall_summary(payload: dict) -> str:
    """Build the {{2}} WhatsApp template variable from Outpero outcomes."""
    parts = []
    if payload.get("products"):
        parts.append(str(payload["products"]))
    if payload.get("budget"):
        parts.append(f"budget {payload['budget']}")
    if payload.get("timeline"):
        parts.append(f"timeline {payload['timeline']}")
    if payload.get("features"):
        parts.append(str(payload["features"]))
    if parts:
        return "; ".join(parts)
    return "your interest in an e-commerce website"


async def _get_or_create_call_id(outpero_call_id: str, phone: str) -> int:
    """Map an Outpero call UUID to a row in our `calls` table.

    Creates the lead + call rows on first sight. Returns the integer
    call_id our notifier/followup services expect.
    """
    # Upsert lead by phone.
    lead_id = await db.upsert_lead(phone=phone, name=None)
    # Upsert call by outpero_call_id stored in twilio_call_sid column
    # (reusing the column -- both are opaque external IDs).
    call_id = await db.upsert_call_by_external_id(
        lead_id=lead_id,
        external_id=outpero_call_id,
        status="answered",
    )
    return call_id


# --------------------------------------------------------------- mid-call

@router.post("/midcall")
async def outpero_midcall(request: Request) -> dict:
    """Called by Outpero's Custom API action mid-call when HOT intent fires.

    Configure in Outpero Actions → Custom API:
        URL:    https://<your-render-url>/webhooks/outpero/midcall
        Method: POST
        When:   HOT lead detected (lead_temperature = HOT)
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    logger.info("outpero midcall webhook: %s", payload)

    outpero_call_id: str = payload.get("call_id") or ""
    phone = _normalise_phone(payload.get("phone"))
    name: Optional[str] = _clean_str(payload.get("lead_name"), "Lead's name") or None

    if not phone:
        logger.warning("midcall webhook: no phone in payload -- skipping")
        raise HTTPException(400, "phone is required")

    # Map to our internal call_id (creates DB rows if this is the first event).
    try:
        call_id = await _get_or_create_call_id(outpero_call_id, phone)
    except Exception as e:
        logger.error("could not resolve call_id for outpero_call=%s: %s",
                     outpero_call_id, e)
        raise HTTPException(500, "db error")

    summary = _midcall_summary(payload)

    try:
        sid = await notifier.send_whatsapp_midcall(
            call_id=call_id,
            to_phone=phone,
            name=name,
            summary=summary,
        )
    except Exception as e:
        logger.exception("send_whatsapp_midcall failed: %s", e)
        raise HTTPException(500, "whatsapp send failed")

    return {
        "status": "sent" if sid else "already_fired",
        "message_sid": sid,
        "call_id": call_id,
    }


# --------------------------------------------------------------- post-call

@router.post("/postcall")
async def outpero_postcall(request: Request) -> dict:
    """Called by Outpero's Post-Call Webhook after the call ends.

    Configure in Outpero Outcomes → Post-Call Webhook:
        URL:    https://<your-render-url>/webhooks/outpero/postcall
        Method: POST

    Sends the two follow-up WhatsApps (architecture image + resume PDF).
    Idempotent -- safe to retry.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    logger.info("outpero postcall webhook: %s", payload)

    outpero_call_id: str = payload.get("call_id") or ""
    phone = _normalise_phone(payload.get("phone"))

    if not phone:
        logger.warning("postcall webhook: no phone -- skipping follow-up")
        return {"status": "skipped", "reason": "no phone"}

    try:
        call_id = await _get_or_create_call_id(outpero_call_id, phone)
    except Exception as e:
        logger.error("could not resolve call_id for outpero_call=%s: %s",
                     outpero_call_id, e)
        raise HTTPException(500, "db error")

    # Mark call as completed so followup.send_followup doesn't skip it.
    try:
        await db.end_call(call_id, status="completed")
    except Exception as e:
        logger.warning("end_call failed for call_id=%s: %s", call_id, e)

    # Persist a minimal classification row so send_followup has something
    # to build the context message from. Use Outpero's extracted outcomes.
    try:
        await db.upsert_outpero_classification(
            call_id=call_id,
            state=payload.get("lead_temperature") or "WARM",
            products=payload.get("products"),
            budget=payload.get("budget"),
            timeline=payload.get("timeline"),
            features=payload.get("features"),
            barrier=payload.get("barrier"),
        )
    except Exception as e:
        logger.warning("classification upsert failed: %s", e)

    # Fire the two follow-up WhatsApps.
    try:
        await send_followup(call_id=call_id, phone=phone)
    except Exception as e:
        logger.exception("send_followup failed for call_id=%s: %s", call_id, e)
        raise HTTPException(500, "followup failed")

    return {"status": "ok", "call_id": call_id}
