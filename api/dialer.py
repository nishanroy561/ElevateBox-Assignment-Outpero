"""Outbound-call orchestration.

Given a phone number:
  1. Upsert a `leads` row (by phone) in Neon
  2. Create a `calls` row (status='initiated')
  3. Create a LiveKit room
  4. Dispatch the PriyaAgent worker into it, passing call_id in the room
     metadata so the agent knows which call to persist turns against
  5. Create a SIP participant that dials the number via Twilio
  6. Backfill the Twilio SIP call SID onto the calls row

The agent worker (agent/worker.py) is a separate long-lived process --
this module only PLACES calls; the agent SESSION is owned by the worker.

The metadata is a JSON blob so we can add more context later (persona
overrides, callback context, etc.) without another dispatch change.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass

from livekit import api

from services import db

logger = logging.getLogger("elevatebox.dialer")

# Name must match agent/worker.py's WorkerOptions(agent_name=...) or dispatch
# will fail with "no such agent".
AGENT_NAME = "elevatebox-priya"


@dataclass
class CallHandle:
    """Return value of place_call -- everything the caller needs to track state."""
    room_name: str
    sip_participant_identity: str
    sip_call_id: str          # Twilio-side call SID once ringing
    lead_phone: str
    lead_id: int              # Neon
    call_id: int              # Neon


async def place_call(to_number: str, *, room_prefix: str = "elevatebox") -> CallHandle:
    """Dial `to_number` (E.164) and drop Priya into the resulting room.

    Rejects the evaluator number unless PHASE8_REAL_CALL=1 is set.
    See plan.md upgrade path -- this is the runtime tripwire promised there.
    """
    _refuse_evaluator_unless_authorised(to_number)

    lk_url    = os.environ["LIVEKIT_URL"]
    lk_key    = os.environ["LIVEKIT_API_KEY"]
    lk_secret = os.environ["LIVEKIT_API_SECRET"]
    trunk_id  = os.environ["SIP_OUTBOUND_TRUNK_ID"]

    # Persistence, first. If Neon is down we fail fast rather than tying up
    # LiveKit + Twilio for a call that will have no transcript.
    lead_id = await db.upsert_lead(to_number)
    logger.info("lead upserted: id=%s phone=%s", lead_id, to_number)

    # Rooms are short-lived (one per call); the suffix makes concurrent calls safe.
    room_name = f"{room_prefix}-{uuid.uuid4().hex[:10]}"
    sip_ident = f"caller-{to_number.lstrip('+')}"

    call_id = await db.create_call(lead_id=lead_id, room=room_name)
    logger.info("call created: call_id=%s room=%s", call_id, room_name)

    # Metadata carries call_id and lead_phone into the worker. The agent
    # entrypoint reads ctx.job.metadata as a JSON string.
    metadata = json.dumps({
        "call_id":    call_id,
        "lead_id":    lead_id,
        "phone":      to_number,
    })

    lk = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)

    try:
        # Explicit agent dispatch -- the agent worker only joins rooms it is
        # explicitly told to join. Avoids joining every ambient room.
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        logger.info("agent dispatched: room=%s agent=%s", room_name, AGENT_NAME)

        # Now place the SIP call. The phone starts ringing here.
        sip = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=to_number,
                room_name=room_name,
                participant_identity=sip_ident,
                participant_name=f"Caller {to_number}",
                wait_until_answered=False,
            )
        )
        logger.info("SIP call placed: sid=%s to=%s call_id=%s",
                    sip.sip_call_id, to_number, call_id)

        # Backfill the Twilio SID onto the calls row so we can join to Twilio
        # logs later without the room-name dance.
        await db.update_call_sid(call_id, sip.sip_call_id)

        return CallHandle(
            room_name=room_name,
            sip_participant_identity=sip_ident,
            sip_call_id=sip.sip_call_id,
            lead_phone=to_number,
            lead_id=lead_id,
            call_id=call_id,
        )
    finally:
        await lk.aclose()


# --------------------------------------------------------------- safety guard

def _refuse_evaluator_unless_authorised(number: str) -> None:
    """The Phase 8 tripwire promised in plan.md.

    TARGET_NUMBER is the evaluator's phone; every other number is safe. Dialing
    the evaluator is only permitted when the operator explicitly sets
    PHASE8_REAL_CALL=1 in the environment. This exists so a stray
    place_call(os.getenv('TARGET_NUMBER')) somewhere never fires the real
    call accidentally.
    """
    target = os.getenv("TARGET_NUMBER")
    if not target or number != target:
        return
    if os.getenv("PHASE8_REAL_CALL") != "1":
        raise RuntimeError(
            "Refusing to dial TARGET_NUMBER (%s) without PHASE8_REAL_CALL=1. "
            "This is the runtime tripwire from plan.md. Set the env var only "
            "when you actually mean to place the real evaluator call." % number
        )
    logger.warning("PHASE8_REAL_CALL=1 detected -- dialing evaluator %s", number)
