"""Callback re-dial poller.

Runs as a background asyncio task in the FastAPI process. Every N seconds
it reads `callbacks` where status='scheduled' AND scheduled_for_utc <=
now(), and for each row fires a fresh outbound call to the lead's phone
number.

Why not APScheduler:
    - APScheduler with SQLAlchemyJobStore needs an idle SQLAlchemy engine,
      which defeats Neon's scale-to-zero.
    - Its jobstore polls anyway, so the "scheduler" abstraction adds a
      dependency and lifecycle without adding capability at this scale.
    - This poller is 40 lines. APScheduler-integrated would be 200+.

The poll interval defaults to 30s. That means a callback booked for
"kal subah 10:30" might actually fire at 10:30:29 in the worst case.
For a "callback" that is well inside the tolerance a human would allow.

Started from api/main.py on FastAPI startup; cancelled on shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from services import db

logger = logging.getLogger("elevatebox.scheduler")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()

# Configurable via env for tests: default 30 seconds.
POLL_INTERVAL_S = int(os.getenv("CALLBACK_POLL_INTERVAL_S", "30"))


async def _fire_callback(row: dict) -> None:
    """Fire one due callback row. Marks fired on success, failed on error."""
    callback_id = row["id"]
    phone = row["phone"]
    spoken = row["spoken_phrase"]
    logger.info("firing callback id=%s phone=%s spoken=%r", callback_id, phone, spoken)
    try:
        from api.dialer import place_call  # lazy -- not available in Outpero-only deploy
        handle = await place_call(phone)
        await db.mark_callback_fired(callback_id, handle.call_id)
        logger.info(
            "callback fired: id=%s new call_id=%s room=%s",
            callback_id, handle.call_id, handle.room_name,
        )
    except ImportError:
        logger.warning("place_call not available (Outpero mode) -- skipping LiveKit callback for id=%s", callback_id)
    except Exception as e:
        logger.exception("callback fire failed: id=%s err=%s", callback_id, e)
        try:
            await db.mark_callback_failed(callback_id, f"{type(e).__name__}: {e}")
        except Exception:
            logger.exception("could not mark callback %s failed", callback_id)


async def _loop() -> None:
    logger.info("callback poller started (interval=%ss)", POLL_INTERVAL_S)
    while not _stop.is_set():
        try:
            due = await db.due_callbacks()
        except Exception as e:
            # Neon might have just gone to sleep -- log and try again next tick.
            logger.warning("due_callbacks query failed: %s", e)
            due = []
        for row in due:
            # Fire each callback in its own task so a slow one doesn't back
            # the next up. Each fire does its own DB persistence.
            asyncio.create_task(_fire_callback(row))
        # Wait for either the interval OR a stop signal, whichever first.
        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
    logger.info("callback poller stopped")


def start() -> None:
    """Start the background poller. Idempotent."""
    global _task
    if _task is not None and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_loop(), name="callback-poller")


async def stop() -> None:
    """Signal the poller to stop and wait for its current tick to finish."""
    _stop.set()
    if _task is not None:
        try:
            await asyncio.wait_for(_task, timeout=POLL_INTERVAL_S + 5)
        except asyncio.TimeoutError:
            _task.cancel()
