"""Neon Postgres access layer.

Design decisions worth naming (see plan.md section 2.3):

- **No persistent pool.** Every helper opens its own connection and closes
  it when done. This is the "NullPool spirit" from the plan -- Neon's
  free tier gives 100 compute-hours/month, and an idle pool would eat
  them in ~4 days by preventing scale-to-zero.

- **Sync psycopg + asyncio.to_thread.** psycopg's async mode does not
  work with Windows's ProactorEventLoop, which LiveKit's agent runner
  requires (for subprocess audio). Rather than fight the event loop, we
  keep the psycopg calls synchronous and offload each one to a worker
  thread. Latency impact is negligible because every db write from the
  audio path is already wrapped in `asyncio.create_task`.

- **All writes are fire-and-forget from the audio path.** The worker
  wraps every db call in `asyncio.create_task` so a slow DB doesn't
  slow down speech.

- **Neon cold-start.** First query after ~5 minutes idle takes ~500 ms
  to wake. That's harmless because db writes are off the audio path.

Public API (all coroutines):
    upsert_lead(phone, name=None)                  -> lead_id
    create_call(lead_id, room)                     -> call_id
    upsert_call_by_external_id(lead_id, ext_id, status) -> call_id
    update_call_sid(call_id, sip_sid)
    insert_turn(call_id, seq, role, text, language=None)
    end_call(call_id, status, duration_s=None)
    count_turns(call_id)                           -> int   (test helper)
    upsert_outpero_classification(call_id, state, **outcomes)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import psycopg

logger = logging.getLogger("elevatebox.db")


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in env")
    return dsn


# ------------------------------------------- synchronous inner functions

def _upsert_lead(phone: str, name: Optional[str] = None) -> int:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO leads (phone, name)
                VALUES (%s, %s)
                ON CONFLICT (phone) DO UPDATE
                    SET name = COALESCE(EXCLUDED.name, leads.name)
                RETURNING id
                """,
                (phone, name),
            )
            return int(cur.fetchone()[0])


def _upsert_call_by_external_id(lead_id: int, external_id: str,
                                 status: str) -> int:
    """Look up a call by its Outpero call UUID (stored in twilio_call_sid).
    Creates the row on first sight; returns the integer call_id."""
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            if not external_id:
                # During test button clicks in the dashboard, create a fresh call row
                # so every test button click sends a new WhatsApp message.
                cur.execute(
                    """
                    INSERT INTO calls (lead_id, status)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (lead_id, status),
                )
                return int(cur.fetchone()[0])

            cur.execute(
                "SELECT id FROM calls WHERE twilio_call_sid = %s",
                (external_id,),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE calls SET status = %s WHERE id = %s",
                    (status, row[0]),
                )
                return int(row[0])

            cur.execute(
                """
                INSERT INTO calls (lead_id, status, twilio_call_sid)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (lead_id, status, external_id),
            )
            return int(cur.fetchone()[0])


def _upsert_outpero_classification(
    call_id: int,
    state: str,
    products: Optional[str],
    budget: Optional[str],
    timeline: Optional[str],
    features: Optional[str],
    barrier: Optional[str],
) -> None:
    """Persist the classification extracted by Outpero Outcomes so that
    services/followup.py can build a personalised follow-up message.
    Uses turn_seq=0 as a sentinel (Outpero has no per-turn seq)."""
    safe_state = state if state in ("HOT", "WARM", "COLD") else "WARM"
    features_list = [f.strip() for f in features.split(",")] if features else []
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO classifications
                    (call_id, turn_seq, state, confidence,
                     budget, products, timeline, features, barrier,
                     evidence_quote)
                VALUES (%s, 0, %s, 0.85, %s, %s, %s, %s, %s,
                        'extracted by Outpero Outcomes')
                ON CONFLICT DO NOTHING
                """,
                (call_id, safe_state, budget, products, timeline,
                 features_list, barrier),
            )


def _create_call(lead_id: int, room: str) -> int:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO calls (lead_id, status, livekit_room)
                VALUES (%s, 'initiated', %s)
                RETURNING id
                """,
                (lead_id, room),
            )
            return int(cur.fetchone()[0])


def _update_call_sid(call_id: int, sip_call_sid: str) -> None:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE calls SET twilio_call_sid = %s WHERE id = %s",
                (sip_call_sid, call_id),
            )


def _end_call(call_id: int, status: str, duration_s: Optional[int]) -> None:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE calls
                SET status = %s,
                    ended_at = now(),
                    duration_s = COALESCE(%s, EXTRACT(EPOCH FROM (now() - started_at))::int)
                WHERE id = %s
                """,
                (status, duration_s, call_id),
            )


def _insert_turn(call_id: int, seq: int, role: str, text: str,
                 language: Optional[str]) -> None:
    if role not in ("agent", "user"):
        raise ValueError(f"role must be 'agent' or 'user', got: {role!r}")
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO turns (call_id, seq, role, text, language)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (call_id, seq) DO NOTHING
                """,
                (call_id, seq, role, text, language),
            )


def _count_turns(call_id: int) -> int:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM turns WHERE call_id = %s", (call_id,))
            return int(cur.fetchone()[0])


# ------------------------------------------------ public async wrappers

async def upsert_lead(phone: str, name: Optional[str] = None) -> int:
    return await asyncio.to_thread(_upsert_lead, phone, name)


async def upsert_call_by_external_id(
    lead_id: int, external_id: str, status: str = "answered"
) -> int:
    return await asyncio.to_thread(
        _upsert_call_by_external_id, lead_id, external_id, status
    )


async def upsert_outpero_classification(
    call_id: int,
    state: str,
    products: Optional[str] = None,
    budget: Optional[str] = None,
    timeline: Optional[str] = None,
    features: Optional[str] = None,
    barrier: Optional[str] = None,
) -> None:
    await asyncio.to_thread(
        _upsert_outpero_classification,
        call_id, state, products, budget, timeline, features, barrier,
    )


async def create_call(lead_id: int, room: str) -> int:
    return await asyncio.to_thread(_create_call, lead_id, room)


async def update_call_sid(call_id: int, sip_call_sid: str) -> None:
    await asyncio.to_thread(_update_call_sid, call_id, sip_call_sid)


async def end_call(call_id: int, status: str = "completed",
                   duration_s: Optional[int] = None) -> None:
    await asyncio.to_thread(_end_call, call_id, status, duration_s)


async def insert_turn(call_id: int, seq: int, role: str, text: str,
                      language: Optional[str] = None) -> None:
    await asyncio.to_thread(_insert_turn, call_id, seq, role, text, language)


async def count_turns(call_id: int) -> int:
    return await asyncio.to_thread(_count_turns, call_id)


# ---------------------------------------------------- callbacks

def _create_callback_sync(lead_id: int, call_id: int, spoken_phrase: str,
                          scheduled_for_utc) -> Optional[int]:
    """Insert a callback row. Returns the new id, or None if a callback for
    this call already exists (LLM re-invoked book_callback -- see
    ux_callbacks_one_per_call). Idempotent."""
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO callbacks
                        (lead_id, call_id, spoken_phrase, scheduled_for_utc, status)
                    VALUES (%s, %s, %s, %s, 'scheduled')
                    RETURNING id
                    """,
                    (lead_id, call_id, spoken_phrase, scheduled_for_utc),
                )
                return int(cur.fetchone()[0])
            except psycopg.errors.UniqueViolation:
                return None


def _due_callbacks_sync():
    """Rows scheduled to fire now-ish. Returns list of dicts, joined with
    the lead phone so the scheduler can dial directly."""
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cb.id, cb.lead_id, cb.call_id, cb.scheduled_for_utc,
                       cb.spoken_phrase, l.phone
                FROM callbacks cb
                JOIN leads l ON l.id = cb.lead_id
                WHERE cb.status = 'scheduled'
                  AND cb.scheduled_for_utc <= now()
                ORDER BY cb.scheduled_for_utc
                LIMIT 25
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _mark_callback_fired_sync(callback_id: int, new_call_id: int) -> None:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE callbacks SET status = 'fired' WHERE id = %s",
                (callback_id,),
            )
            # Link the newly placed call back to its origin.
            cur.execute(
                "UPDATE calls SET is_callback_of = ("
                "  SELECT call_id FROM callbacks WHERE id = %s"
                ") WHERE id = %s",
                (callback_id, new_call_id),
            )


def _mark_callback_failed_sync(callback_id: int, err: str) -> None:
    with psycopg.connect(_dsn(), connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE callbacks SET status = 'failed' WHERE id = %s",
                (callback_id,),
            )


async def create_callback(lead_id: int, call_id: int, spoken_phrase: str,
                          scheduled_for_utc) -> Optional[int]:
    """Returns the new callback id, or None if one already exists for this
    call (idempotency guard from ux_callbacks_one_per_call)."""
    return await asyncio.to_thread(
        _create_callback_sync, lead_id, call_id, spoken_phrase, scheduled_for_utc
    )


async def due_callbacks():
    return await asyncio.to_thread(_due_callbacks_sync)


async def mark_callback_fired(callback_id: int, new_call_id: int) -> None:
    await asyncio.to_thread(_mark_callback_fired_sync, callback_id, new_call_id)


async def mark_callback_failed(callback_id: int, err: str) -> None:
    await asyncio.to_thread(_mark_callback_failed_sync, callback_id, err)
