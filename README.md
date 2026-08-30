# ElevateBox Voice Agent — Priya

An outbound voice agent that dials a lead, holds a Telugu/Hindi/English
conversation, classifies intent, fires a WhatsApp mid-call on high buying
intent, books callbacks from spoken time, and follows up after hangup with
resume + architecture + mobile.

Built for the ElevateBox SDE Intern Assignment (August 2026). Contact:
**+91 98302 37930**.

---

## Note to the evaluator *(under 200 words)*

Hi. Priya just called you — that call is the deliverable.

**Stack.** Twilio SIP (US caller ID — Indian CLI blocked by DoT) →
LiveKit → Python agent with Sarvam Saaras STT, sarvam-105b-conversations
LLM, Bulbul v3 TTS. Neon Postgres for state. Twilio Content API for
WhatsApp templates.

**What works.** Two-way conversation in Telugu / Hindi / English with
code-switch, verified live. Classifier runs per user turn; HOT detected
from indirect answers, fires WhatsApp in ~2 seconds. Callback booking
via an LLM tool: "kal subah" → the poller re-dials at 10:30. Follow-up
WhatsApp delivers resume + mobile.

**What doesn't.** Voice and WhatsApp use different numbers — contextual
link only (Priya announces the WhatsApp source aloud). Meta throttled
the architecture-image follow-up to my own low-engagement test number
(error 63049); should deliver to a fresh recipient. Twilio sometimes
times out on a WhatsApp send; no retry queue yet. Running from my laptop
— Render free tier OOMs on Silero VAD.

**What next.** Indian DLT provider (Exotel) so both channels share one
Indian number. Post-call retry queue. Extended classifier: decision-maker
name, callback urgency, competitor mentions.

Call me back on **+91 98302 37930**.

— Nishan Roy

---

## Architecture

One page, hand-drawn:
**[nishanroy.me/architecture.png](https://nishanroy.me/architecture.png)**

Diagram source is committed at [docs/architecture.png](docs/architecture.png).
For the design-time flow the diagram was drawn against, see
[plan.md §3](plan.md).

## Repository shape

```
agent/       LiveKit voice worker — persona, session wiring, tools
api/         FastAPI control plane — POST /call, callback poller, dialer
services/    Standalone services — classifier, notifier, timeparse,
             followup, db, scheduler, logging setup
db/          schema.sql — six tables, applied to Neon
docs/        resume.pdf, architecture.png (served publicly from
             nishanroy.me for WhatsApp media headers)
scripts/     One-off tools: sarvam_gate.py (LLM benchmark), llm_gate.py
             (historical comparison against Gemini/Groq),
             voice_samples/ (8 kHz audition renders)
tests/       fixtures/ for STT tests; manual/ for hand-run CLI payloads
```

## Design docs

Two files in the repo are worth reading if any of the above raises a
question:

- **[plan.md](plan.md)** — every decision that shaped the stack, with
  the reasoning behind each. Payment failures, plugin API mismatches,
  network route choices, latency budget vs. measured reality.
- **[checklists.md](checklists.md)** — phased shipping checklist. Every
  item has been marked checked (with the deviation reason) or unchecked
  (with the reason it was skipped or deferred). Running score at the top.

## Tech decisions — the short version

- **Sarvam for everything voice.** One Indian vendor, INR + UPI billing —
  international cards from India get declined on Anthropic and OpenAI.
- **`sarvam-105b-conversations` via OpenAI-compat endpoint.** The native
  LiveKit Sarvam plugin ships a stale allowlist that rejects this model
  name; we use the OpenAI plugin against `https://api.sarvam.ai/v1`.
  Verified on the phase 3.5 gate at 378 ms p50, 10/10 JSON validity.
- **Neon over Supabase.** Supabase is DNS-blocked on Jio here. Neon
  auto-wakes from scale-to-zero on next query (~500 ms), so a demo
  received a week after submission still works.
- **Vercel for assets, not GitHub Pages.** Custom-domain redirect chain
  broke Pages; Vercel already served the personal site cleanly.
- **Localhost for the real call, not Render.** See the 200-word note.

## Reproducing the call locally

You do not need to reproduce this. The deliverable is the call I placed
to you. Kept here for reference:

```powershell
# One-time setup
python -m pip install "livekit-agents[sarvam,silero]~=1.7" livekit-api ^
                     fastapi "uvicorn[standard]" psycopg[binary] twilio ^
                     python-dotenv sarvamai openai
```

Two terminals, both `cd`'d into this folder:

```powershell
python -m agent.worker dev
```

```powershell
python -m uvicorn api.main:app --port 8000
```

Trigger a call to a number you own:

```powershell
curl -X POST http://localhost:8000/call/+91XXXXXXXXXX
```

The dialer refuses to call `TARGET_NUMBER` (the evaluator's phone)
unless `PHASE8_REAL_CALL=1` is explicitly set in the environment. A
runtime tripwire, not a production behaviour.
