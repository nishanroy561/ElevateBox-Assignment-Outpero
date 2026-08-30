"""FastAPI control plane for the ElevateBox voice agent.

Endpoints:
    GET  /health                       -- liveness probe
    POST /webhooks/outpero/midcall     -- Outpero Custom API mid-call trigger
    POST /webhooks/outpero/postcall    -- Outpero post-call webhook
    POST /webhooks/twilio              -- Twilio status callbacks (legacy)
    POST /call                         -- place a Twilio/LiveKit call (legacy dev)
    POST /call/{phone}                 -- place a Twilio/LiveKit call (legacy dev)

Run locally:
    uvicorn api.main:app --reload --port 8000

Prod deploy target: Render (web service, free tier).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

# .env must load before importing anything that reads env at import-time.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.logging_setup import setup_logging  # noqa: E402
setup_logging()

from api.outpero_webhook import router as outpero_router  # noqa: E402

# Legacy LiveKit dialer + scheduler -- only imported if LiveKit env vars exist.
# The Outpero deployment does not need these.
try:
    from api.dialer import place_call, CallHandle  # noqa: E402
    from services import scheduler  # noqa: E402
    _livekit_available = True
except Exception as _e:  # noqa: BLE001
    logger = logging.getLogger("elevatebox.api")
    logger.warning("LiveKit dialer not available (Outpero mode): %s", _e)
    _livekit_available = False

logger = logging.getLogger("elevatebox.api")

app = FastAPI(title="ElevateBox voice agent")
app.include_router(outpero_router)


@app.on_event("startup")
async def _startup() -> None:
    if _livekit_available:
        from services import scheduler as _sched
        _sched.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _livekit_available:
        from services import scheduler as _sched
        await _sched.stop()


# ------------------------------------------------------------------- /health

@app.get("/health")
async def health() -> dict:
    """Liveness check. Also serves as the Render keep-warm target (plan Phase 7)."""
    return {"status": "ok", "service": "elevatebox-voice"}


# --------------------------------------------------------------------- /call

@app.post("/call")
async def call_default() -> dict:
    """Dial MY_MOBILE. The dev-testing default -- safe, no evaluator risk."""
    to = os.getenv("MY_MOBILE")
    if not to:
        raise HTTPException(500, "MY_MOBILE not set in .env")
    return await _place_and_serialise(to)


@app.post("/call/{phone}")
async def call_specific(phone: str) -> dict:
    """Dial a specific E.164 number. The dialer's tripwire refuses TARGET_NUMBER
    without PHASE8_REAL_CALL=1, so this is safe to expose during dev."""
    if not phone.startswith("+"):
        raise HTTPException(400, "phone must be E.164 (start with +)")
    return await _place_and_serialise(phone)


async def _place_and_serialise(to: str) -> dict:
    try:
        handle: CallHandle = await place_call(to)
    except RuntimeError as e:      # tripwire violation
        logger.warning("call refused: %s", e)
        raise HTTPException(403, str(e))
    except Exception as e:
        logger.exception("call failed")
        raise HTTPException(500, f"place_call raised: {type(e).__name__}: {e}")

    return {
        "status": "dialing",
        "to": handle.lead_phone,
        "room": handle.room_name,
        "sip_call_id": handle.sip_call_id,
        "agent": "elevatebox-priya",
    }


# ---------------------------------------------------------- twilio webhooks

@app.post("/webhooks/twilio")
async def twilio_webhook(request: Request) -> dict:
    """Twilio posts call status transitions here (initiated/ringing/answered/
    completed). Wired up in Phase 2C for hangup detection and duration."""
    form = await request.form()
    logger.info("twilio webhook: %s", dict(form))
    return {"received": True}
