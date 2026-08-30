"""LiveKit agent worker for the ElevateBox voice agent.

Runs as a long-lived daemon that receives job dispatches from LiveKit Cloud
whenever a room needs an agent. Structure follows Sarvam's LiveKit doc with
three deliberate overrides -- see AGENT NOTES below.

Launch (dev, hot-reload):
    python -m agent.worker dev

Launch (prod):
    python -m agent.worker start

AGENT NOTES:

  1. LLM model: `sarvam-105b-conversations` (not `sarvam-105b`).
     Measured: 378 ms p50 vs 1625 ms p50; 10/10 vs 2/10 JSON validity.
     Used via livekit.plugins.openai.LLM with base_url=Sarvam's /v1 endpoint,
     because livekit.plugins.sarvam.LLM has a hard-coded allowlist that
     rejects `-conversations`.
  2. TTS voice: `vidya` on `bulbul:v2` (not `shubh` on `bulbul:v3`).
     v2 costs half; vidya was picked by ear at Phase 0.
  3. STT model: `saaras:v4` (not `saaras:v3`). Newer; docs are stale.

Phase 2C additions:
  - reads `call_id` from ctx.job.metadata (JSON blob set by api/dialer.py)
  - persists every user + agent turn to Neon via services.db
  - marks the call as ended on session close
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli, function_tool, RunContext
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.plugins import openai, sarvam

from agent import prompts
from services import db, notifier, timeparse
from services.classifier import classify_running_call
from services.followup import send_followup
from services.logging_setup import setup_logging

# Load .env from the project root, regardless of where this file is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
setup_logging()

logger = logging.getLogger("elevatebox.agent")


# ----------------------------------------------------- mid-call helpers

def _midcall_summary_from(c) -> str:
    """Build the {{2}} variable for hot_lead_midcall from a Classification.

    Template body reads: "...Quick summary of what we just discussed: {{2}}.
    Full follow-up will land right after our call. — Nishan, ElevateBox"

    So {{2}} needs to sound like "your saree business, ~60 products, live
    within a month, budget around 80k". If the classifier extracted structured
    fields, use them. Otherwise fall back to the evidence quote it produced.
    """
    parts = []
    if c.products:
        parts.append(str(c.products))
    if c.budget:
        parts.append(f"budget {c.budget}")
    if c.timeline:
        parts.append(f"timeline {c.timeline}")
    if c.features:
        parts.append(", ".join(c.features[:3]))
    if parts:
        return "; ".join(parts)
    # No structured fields -- use the verbatim evidence quote. Keep under
    # ~200 chars because WhatsApp template variables have a soft limit.
    q = (c.evidence_quote or "").strip().strip('"').strip("'")
    return q[:200] if q else "your interest in an e-commerce website"


# --------------------------------------------------------------------- Agent

class PriyaAgent(Agent):
    """Priya from ElevateBox -- warm, quick, Indic-multilingual voice agent."""

    def __init__(self) -> None:
        super().__init__(
            instructions=prompts.SYSTEM_PROMPT,
            stt=sarvam.STT(
                api_key=os.environ["SARVAM_API_KEY"],
                model=os.getenv("SARVAM_STT_MODEL", "saaras:v4"),
                language="unknown",       # auto-detect per utterance
                mode="transcribe",
                flush_signal=True,
            ),
            llm=openai.LLM(
                api_key=os.environ["SARVAM_API_KEY"],
                base_url="https://api.sarvam.ai/v1",
                model=os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b-conversations"),
                temperature=0.7,
            ),
            tts=sarvam.TTS(
                api_key=os.environ["SARVAM_API_KEY"],
                model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v2"),
                speaker=os.getenv("SARVAM_TTS_SPEAKER", "vidya"),
                target_language_code="en-IN",
                enable_cached_responses=True,
            ),
        )

    # Populated by entrypoint() before session.start() -- lets tools access
    # DB identifiers without threading them through RunContext plumbing.
    call_id: int | None = None
    lead_id: int | None = None
    caller_phone: str | None = None

    async def on_enter(self) -> None:
        """Speak the greeting the moment the agent joins.

        Without this, AgentSession sits silent waiting for the user to speak
        first -- deadlock on a phone call.
        """
        await self.session.say(prompts.GREETING_TE, allow_interruptions=True)

    # ------------------------------------------------------ tools

    @function_tool
    async def book_callback(
        self,
        ctx: RunContext,
        spoken_phrase: str,
    ) -> str:
        """Book a callback for later when the caller names a time to call
        them back.

        Call this the moment the caller names a time -- "kal subah",
        "call me tomorrow evening", "next week Monday", "repu udayam".
        Pass the caller's exact words in `spoken_phrase`. The tool
        resolves it into a real dated slot, persists it, and returns a
        one-line confirmation you should say aloud verbatim.

        Do NOT call this for vague statements ("later", "sometime"),
        past times, or if the caller declines a callback.

        Args:
            spoken_phrase: the caller's verbatim phrase naming when to
                           call back, e.g. "kal subah 10 baje" or
                           "tomorrow morning".

        Returns:
            The confirmation sentence to speak aloud, e.g.
            "Set - kal subah 10:30 baje, main call karungi."
        """
        logger.info("book_callback invoked: call_id=%s phrase=%r",
                    self.call_id, spoken_phrase)

        # Resolve the phrase via Sarvam LLM -> ISO datetime.
        parsed = await timeparse.parse_spoken_time(spoken_phrase)
        if parsed is None:
            logger.warning("timeparse returned None for %r -- using default slot",
                           spoken_phrase)
            parsed = timeparse.default_slot()

        # Persist if we have the DB identifiers.
        if self.lead_id is not None and self.call_id is not None:
            try:
                cb_id = await db.create_callback(
                    lead_id=self.lead_id,
                    call_id=self.call_id,
                    spoken_phrase=spoken_phrase,
                    scheduled_for_utc=parsed.scheduled_for_utc,
                )
                if cb_id is None:
                    # Priya already booked one for this call. LLM sometimes
                    # re-invokes the tool after confirming aloud -- the
                    # ux_callbacks_one_per_call partial unique index refuses
                    # the second insert. Return a plain acknowledgement so
                    # she doesn't announce a second slot.
                    logger.info("book_callback: callback already exists for call_id=%s -- returning existing acknowledgement",
                                self.call_id)
                    return "That's already booked -- I will call you then."
                logger.info(
                    "callback persisted: id=%s scheduled_for=%s (%s IST)",
                    cb_id, parsed.scheduled_for_utc.isoformat(),
                    parsed.scheduled_for_ist.strftime("%A %H:%M"),
                )
            except Exception as e:
                # Don't fail the tool call -- Priya should still confirm aloud.
                logger.error("callback persist failed: %s", e)

        return parsed.speak_line


# --------------------------------------------------------------- entrypoint

async def entrypoint(ctx: JobContext) -> None:
    """Called by LiveKit whenever a job is dispatched to a room."""
    logger.info("job dispatched: room=%s participants=%s metadata=%s",
                ctx.room.name, len(ctx.room.remote_participants),
                ctx.job.metadata or "(none)")

    # Parse call_id + lead_id + phone from metadata (set by api/dialer.py).
    # Absent metadata = someone launched the worker independently for testing
    # -- still run, just skip persistence + the mid-call WhatsApp.
    call_id: int | None = None
    lead_id: int | None = None
    caller_phone: str | None = None
    try:
        if ctx.job.metadata:
            meta = json.loads(ctx.job.metadata)
            call_id = int(meta.get("call_id")) if meta.get("call_id") is not None else None
            lead_id = int(meta.get("lead_id")) if meta.get("lead_id") is not None else None
            caller_phone = meta.get("phone")
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("could not parse job metadata: %s -- persistence disabled", e)

    if call_id is None:
        logger.warning("no call_id in metadata -- persistence disabled (dev-only session)")

    session = AgentSession(
        turn_detection="stt",
        min_endpointing_delay=0.07,
    )

    # ---------- persistence + classification + mid-call action fan-out.
    #
    # Every conversation_item_added event we care about does three things,
    # all fired off in create_task so the audio loop never blocks:
    #
    #   1. INSERT one row into `turns`.
    #   2. If it's a user turn: kick classify_running_call() on the running
    #      transcript. When it returns HOT (and this call hasn't fired yet),
    #      fire send_whatsapp_midcall(). Idempotency is guaranteed at the DB
    #      level by the ux_actions_midcall_once partial unique index.
    #   3. LiveKit fires this event for MORE than chat messages (session-
    #      level AgentHandoff etc). Guard by explicit role check.
    if call_id is not None:
        seq_counter = {"n": 0}
        # Owned by this session -- flips to True the first time HOT fires,
        # never flips back. Also serialises overlapping classifier tasks.
        hot_fired: dict = {"done": False}
        # In-memory transcript so the classifier does not race the DB
        # writer (which is fire-and-forget from insert_turn). List of
        # (role, text) tuples in the order LiveKit emitted them.
        transcript_lines: list = []

        async def _classify_and_maybe_fire(
            call_id_local: int,
            phone_local: str,
            transcript_snapshot: list,
        ) -> None:
            """Run one classification pass against the snapshot given;
            if HOT for the first time, fire the mid-call WhatsApp."""
            try:
                c = await classify_running_call(call_id_local, transcript_snapshot)
            except Exception as e:
                logger.warning("classifier task raised: %s", e)
                return
            if c is None:
                return
            if c.state == "HOT" and not hot_fired["done"]:
                # Mark before firing so a second classifier task racing
                # in doesn't double-schedule -- the DB unique index is
                # the ultimate guard, but this keeps logs clean.
                hot_fired["done"] = True
                summary = _midcall_summary_from(c)
                logger.info("HOT detected on call_id=%s -- firing WhatsApp", call_id_local)
                try:
                    await notifier.send_whatsapp_midcall(
                        call_id=call_id_local,
                        to_phone=phone_local,
                        name=None,
                        summary=summary,
                    )
                except Exception as e:
                    # Do not crash the audio loop over a notifier failure.
                    logger.exception("send_whatsapp_midcall failed: %s", e)

        @session.on("conversation_item_added")
        def _on_item_added(ev: ConversationItemAddedEvent) -> None:
            item = ev.item
            role_str = getattr(item, "role", None)
            if role_str not in ("user", "assistant"):
                return
            text = (getattr(item, "text_content", "") or "").strip()
            if not text:
                return
            role = "user" if role_str == "user" else "agent"
            language = (
                getattr(item, "language", None)
                or getattr(item, "detected_language", None)
                or None
            )
            seq = seq_counter["n"]
            seq_counter["n"] += 1

            # Update the in-memory transcript BEFORE dispatching either
            # task. Both the persist and the classify read from this,
            # so ordering is deterministic.
            transcript_lines.append((role, text))

            # 1. persist the turn
            asyncio.create_task(db.insert_turn(
                call_id=call_id, seq=seq, role=role, text=text, language=language
            ), name=f"insert_turn:{call_id}:{seq}")

            # 2. classify only on user turns (agent turns don't add signal)
            #    and only if we have a phone to fire to. Snapshot the list
            #    to avoid the classifier seeing later appends mid-run.
            if role == "user" and caller_phone and not hot_fired["done"]:
                snapshot = list(transcript_lines)
                logger.debug("dispatching classifier: call_id=%s seq=%s turns=%d",
                             call_id, seq, len(snapshot))
                asyncio.create_task(
                    _classify_and_maybe_fire(call_id, caller_phone, snapshot),
                    name=f"classify:{call_id}:{seq}",
                )

    # ---------- start the session
    agent = PriyaAgent()
    # Give the agent access to the DB identifiers so its tools can persist
    # (see PriyaAgent.book_callback).
    agent.call_id = call_id
    agent.lead_id = lead_id
    agent.caller_phone = caller_phone
    await session.start(agent=agent, room=ctx.room)
    logger.info("session started: room=%s call_id=%s lead_id=%s",
                ctx.room.name, call_id, lead_id)

    # ---------- register the end-of-call hook. The worker process persists
    # until the session naturally closes (participant disconnect, etc.).
    # Two things happen on close:
    #   1. Mark the call ended in Neon (status, ended_at, duration_s).
    #   2. Fire the follow-up WhatsApps (phase 6). send_followup skips
    #      internally if this call is a callback re-dial or had no
    #      classification, so it is always safe to invoke here.
    async def _finalise() -> None:
        if call_id is None:
            return
        try:
            await db.end_call(call_id, status="completed")
            logger.info("call finalised in db: call_id=%s", call_id)
        except Exception as e:
            logger.error("end_call failed for call_id=%s: %s", call_id, e)

        if caller_phone:
            try:
                await send_followup(call_id=call_id, phone=caller_phone)
            except Exception as e:
                logger.exception("send_followup failed for call_id=%s: %s", call_id, e)

    ctx.add_shutdown_callback(_finalise)


# ----------------------------------------------------------------- launcher

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="elevatebox-priya",
        )
    )
