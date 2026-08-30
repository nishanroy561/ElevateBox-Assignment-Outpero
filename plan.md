# ElevateBox SDE Intern Assignment — Build Plan

**Goal:** an autonomous AI voice agent that dials **+91 8688664337**, sells e-commerce website development in Telugu/Hindi/English, qualifies the lead, fires a WhatsApp **mid-call** on high intent, books callbacks from spoken time, and sends a context-rich WhatsApp with resume + number + architecture image after the call.

**Target:** 60+ scores a callback. The plan is sequenced so even a partial build lands in the 60s.

**Hard constraints:**
- **International cards are declined** (RBI / international transactions). Anthropic and OpenAI are both unpayable. The stack must be free-tier or INR-payable.
- Twilio works (paid, domestic card OK). LiveKit free tier. **Sarvam: 100 free credits ≈ ₹100, UPI top-up available.**
- **Supabase is DNS-blocked on Jio**, the dev network. Not a preference — unusable without a VPN on every session. Postgres is **Neon**; see §2.3.

*Last revised 2026-08-25.*

---

## 1. Scoring-driven priority order

Build in this order. Do not move on until the previous block works on a real call to your own phone.

| Order | Block | Points | Ship gate |
|---|---|---|---|
| 1 | Outbound call connects + two-way conversation | 25 | Agent calls your phone, holds 60s of natural talk |
| 2 | Mid-call WhatsApp on intent | 15 | Message lands while you are still talking |
| 3 | Intent classification Hot/Warm/Cold | 15 | Correct on 6 scripted indirect-answer cases |
| 4 | Language handling (Te/Hi/En + code-switch) | 10 | Agent stays in the language it was answered in |
| 5 | Discovery quality | 10 | Budget, products, timeline, features asked conversationally |
| 6 | Callback scheduling from speech | 10 | "kal subah" becomes a real dated slot, confirmed aloud |
| 7 | Follow-up WhatsApp + the 4 required items | 10 | Quotes something specific; carries resume, number, diagram |
| 8 | Engineering judgement | 5 | Retries, fallbacks, logs, a README you can defend |

Blocks 1–3 alone = 55. Adding block 4 or 5 clears the bar. **Do not gold-plate before block 3 is green.**

---

## 2. Stack

**All-Sarvam voice pipeline.** One vendor, one key, one bill, INR/UPI.

| Layer | Choice | Why |
|---|---|---|
| Telephony (PSTN) | **Twilio Elastic SIP Trunking** | Already paid for; LiveKit SIP takes a BYO trunk cleanly |
| Session orchestration | **LiveKit Cloud (free) + LiveKit Agents 1.7 (Python)** | Free tier covers a demo; gives barge-in, VAD and turn detection — three of the hardest parts of this assignment |
| STT | **Sarvam Saaras** (`saaras:v4`) | 22 Indian languages, real Telugu, per-utterance language detection |
| LLM (in-call + post-call) | **Sarvam** (`sarvam-105b` / `sarvam-105b-conversations`) | `-conversations` is purpose-built for real-time dialogue. Cheap enough to be a rounding error (§10). |
| TTS | **Sarvam Bulbul** (`bulbul:v2`, female speaker) | Purpose-built Indic voice. A female voice lands better on Indian outbound. |
| Plugin | **`livekit-plugins-sarvam` 1.7.0** | First-party, version-matched to `livekit-agents` 1.7.0. No custom adapter needed. |
| WhatsApp | **Twilio WhatsApp Business sender**, AiSensy/Interakt fallback | See §5 — the #1 schedule risk |
| Scheduler | APScheduler with a Postgres jobstore | The callback must actually re-dial, not just get logged |
| Store | **Neon Postgres** (leads, calls, turns, actions) | Free tier, no card, **auto-wakes from scale-to-zero**. Supabase is DNS-blocked on Jio (§2.3). |
| Host | Render or Railway | The LiveKit agent is a long-lived worker, not a serverless function |
| Static assets | **GitHub Pages** (`resume.pdf`, `architecture.png`) | Neon is pure Postgres with no file storage. Pages is free, permanent, and serves correct MIME types — which `raw.githubusercontent.com` does not (§2.3). |

### 2.1 Why this stack — the one-paragraph defence

The original plan specified Anthropic (Haiku in-call, Opus for the follow-up). **The card was declined there and again on OpenAI** — the standard Indian international-payment problem, not a vendor quirk. A benchmark of free-tier alternatives (`scripts/llm_gate.py`) measured Gemini 3.5-flash at 1643 ms p50 and unusable at 5 RPM on the free tier, and Groq `gpt-oss-120b` at **766 ms p50 / 9-of-10 valid JSON**. Groq was viable, but adding it meant a second vendor and a second free-tier ceiling to babysit for no Indic advantage — the Telugu quality in this system comes from STT and TTS, not the LLM. **Sarvam does all three, bills in INR, and tops up over UPI.** That is the whole argument: the only stack that is fully funded, fully Indic, and single-vendor.

**Worth saying in the README:** a cascaded pipeline was also considered against OpenAI's speech-to-speech Realtime API (better voice, ~400 ms turns). Beyond being unpayable, cascaded has a real advantage here — **the code decides exactly when the mid-call WhatsApp fires**, rather than hoping a speech-to-speech model emits a tool call at the right moment. That is 15 points made deterministic.

### 2.2 Upgrade path, if payment is ever resolved

Keep `agent/providers/` a real boundary so this stays cheap. Card fixes worth **10 minutes, not a day**: enable international transactions in the bank app (off by default, most common cause); use a credit card not debit/RuPay; try a neobank virtual card (Niyo, Fi, Jupiter). If a card starts working before **day 5**, swapping the in-call path to OpenAI Realtime + Maple voice is contained. After day 5, don't.

### 2.3 Database — Neon, not Supabase

**Two independent reasons, either one sufficient:**

1. **Supabase is DNS-blocked on Jio.** The dev network cannot reach it without a VPN on every session. A wall, not an inconvenience.
2. **Idle behaviour favours Neon for this assignment.** The deadline is "rolling, no cut off" and §13 promises a prototype that calls **on demand** — so the system must work whenever the evaluator gets round to it, possibly a week after submission.

| | Idle behaviour | Recovery |
|---|---|---|
| **Neon** | Compute scales to zero after 5 min | **Auto-wakes on next query** (~500 ms cold start) |
| Supabase | Project **pauses after 7 days inactivity** | **Manual reactivation via dashboard** |

If the evaluator triggers the prototype on day 9 and a Supabase project paused on day 8, the demo is dead until someone notices. Neon just wakes up.

**Free tiers, for the record:** Neon gives 0.5 GB storage + **100 compute-hours/month**, no card. Supabase gives 500 MB DB + 1 GB file storage, no card.

**The one Neon trap — compute-hours vs. a long-lived worker.** 100 CU-hours is roughly **4 days of *continuous* compute**. The LiveKit worker and APScheduler run 24/7 on Render; if they hold an idle connection pool open, Neon never scales to zero and the quota is gone mid-build. Fix it in config, not by hoping:

```python
create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)
```

`NullPool` closes connections after each use, so Neon sleeps between calls and burns compute only on actual queries. All DB writes are already in `asyncio.create_task` (§8), so the ~500 ms cold start never touches the audio path. **Also check the APScheduler jobstore polling interval** — callbacks are hours away, not seconds, so poll infrequently or it defeats scale-to-zero on its own.

**Connectivity verified 2026-08-25** from the dev network: `api.sarvam.ai`, `console.neon.tech`, `api.twilio.com`, `cloud.livekit.io`, `api.groq.com` and GitHub Pages all resolve. **No other endpoint in the stack is blocked.**

---

## 3. Architecture

```
                    ┌─────────────────────────────────────┐
   Trigger (CLI /   │  FastAPI control plane              │
   POST /call /     │  POST /call · /webhooks/twilio      │
   scheduler fire)  │  APScheduler (callbacks)            │
                    └───────────────┬─────────────────────┘
                                    │ LiveKit API: create room
                                    │ + SIP outbound participant
                                    ▼
   PSTN  ◄──── Twilio Elastic SIP Trunk ◄──── LiveKit SIP Gateway
 (+91 868…)                                        │
                                                   ▼  audio in / out
                              ┌────────────────────────────────────┐
                              │  LiveKit Agent worker (Python)     │
                              │  VAD → STT (Saaras v4)             │
                              │    → LLM (sarvam-105b)             │
                              │    → TTS (Bulbul v2) → caller      │
                              │  turn detection · barge-in         │
                              └───────┬────────────────────────────┘
                                      │ tool calls (non-blocking, asyncio.create_task)
             ┌────────────────────────┼──────────────────────┬─────────────────┐
             ▼                        ▼                      ▼                 ▼
      classify_lead()         send_whatsapp_now()     book_callback()      log_turn()
       Hot/Warm/Cold          Twilio WA template      APScheduler +        Neon PG
             │                 (fires MID-CALL)       re-dial
             ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ Post-call: sarvam-105b writes the follow-up from the real     │
   │ transcript → WhatsApp #2: context + resume PDF + mobile       │
   │ number + architecture image                                   │
   └──────────────────────────────────────────────────────────────┘
```

Draw this by hand on paper as well — the assignment explicitly says hand-drawn is fine and that they want to see how you think.

---

## 4. Telephony — SETTLED

### 4.0 Caller ID (tested 2026-08-23)

**An Indian mobile cannot be used as caller ID. A US number was purchased.**

| Check | Result |
|---|---|
| `+919875389511` in Verified Caller IDs | Yes — correctly verified |
| Outbound with +91 CLI | **Rejected, error 21212** — DNO list, at Twilio's API edge |
| India dialing permissions | **Already enabled** |
| **`+14784008435` → `+919830237930`** | **`ringing` → `busy` after 37s — handset rang.** Caller ID confirmed working. |

**Use `+14784008435` everywhere as `CALLER_ID` and as the SIP trunk's number.**

*Why:* since Oct 2024 the DoT's spoofed-call prevention system makes Indian telcos drop inbound *international* calls presenting an Indian CLI. A Twilio→+91 call is international-origin, so an Indian CLI is exactly the pattern that gets killed. Twilio enforces this at their edge, so no SIP or LiveKit config works around it.

**Expect an "International Call" label** on the handset (separate DoT mandate) and a lower pickup rate. Open with "Namaskaram, ElevateBox nunchi…" immediately, before they decide it's spam.

### 4.1 Remaining steps

1. Twilio Console → **Elastic SIP Trunking** → create a trunk:
   - Termination SIP URI, e.g. `elevatebox-xxxx.pstn.twilio.com`
   - A Credential List (username/password) — LiveKit authenticates with these
   - Skip the Origination URI — outbound only
   - No phone number needs attaching for outbound-only
2. LiveKit: `lk sip outbound create` pointing at the Twilio termination URI with those credentials and `numbers = ["+14784008435"]`.
3. Test: `lk sip participant create` → room + `sip_call_to: +918688664337`.

A `403`/`401` on the trunk almost always means the `From` failed caller-ID validation — check E.164 formatting first.

**Known risk:** the Indian DND/NDNC registry may block some destination numbers. Test against 2–3 Indian mobiles before the real call.

---

## 5. WhatsApp — the #1 schedule risk

**The trap:** the Twilio WhatsApp *Sandbox* requires the recipient to send `join <code>` first. The evaluator will never do that. Sandbox = zero points on two scoring rows.

**Required path:**
1. Twilio → Messaging → WhatsApp senders → **Embedded Signup**. Sender must reach **ONLINE**.
2. Submit **3 templates** (approval, not code, is the blocker):
   - `hot_lead_midcall` (UTILITY) — fires during the call
   - `followup_context` (UTILITY, **document media header**) — carries the resume PDF
   - `callback_confirmed` (UTILITY) — booked-slot confirmation
3. Templates allow variables, not free text — fixed human framing plus `{{1}}…{{5}}` slots the LLM fills with real quotes.
4. Host `resume.pdf` and `architecture.png` on **GitHub Pages**, pass as `MediaUrl`. **Use Pages, not `raw.githubusercontent.com`** — raw serves PDFs as `application/octet-stream`, and Twilio's WhatsApp document header needs a real `application/pdf` or it will not render. Push to a `docs/` folder, enable Pages, done in five minutes.

### 5.0 Current status (2026-08-24)

| Asset | State | Decision |
|---|---|---|
| Meta WABA `Nishan's Market` | **Approved** | Keep |
| `+91 98753 89511` | **Connected** | Primary test number |
| `+1 478-400-8435` | **Pending** | Leave in place; do not delete for being Pending |
| Old `Agents Market` WABA | **Restricted** | Abandoned |
| Twilio sender list | No sender yet; compliance pending | Monitor |

**Do not buy another number, create another WABA, or delete the US number unless Twilio/Meta explicitly names it as the blocker.**

### 5.1 Hard cutover rule — dates, not vibes

| Checkpoint | If not met | Same-day action |
|---|---|---|
| End of **day 2** — sender off Pending | Still Pending, no Twilio contact | Open a Twilio ticket **and** start AiSensy/Interakt onboarding **in parallel** |
| End of **day 3** — sender ONLINE, test template delivered | Not delivered | AiSensy becomes primary, Twilio drops to fallback |
| End of **day 5** — a template delivering to an unopted number | Nothing delivering anywhere | Fall through to Twilio SMS with links, disclosed plainly in the note |

`notifier.send(template, to, vars, media)` is the only interface the agent touches, so each swap is a one-file change. **Do not let a Pending status consume a day — the parallel path costs an hour to start and buys back three days.**

**Template approval is the long pole, not sender status.** Submit all three the moment drafting is allowed.

---

## 6. Conversation design

### In-call system prompt

- **Persona:** "Priya from ElevateBox, Banjara Hills, Hyderabad." Warm, quick, human. **1–2 sentences per turn, maximum.**
- **Language:** open in Telugu-flavoured English ("Namaskaram, ElevateBox nunchi Priya matladutunna — is this a good time?"). Mirror whichever language they reply in and **stay in it**, while allowing English tech nouns inside Telugu/Hindi sentences — that is how people actually speak here.
  - **Implementation:** Saaras returns a detected language code per utterance. Feed it to the prompt *and* switch the Bulbul `language_code`. Do not rely on the LLM alone to notice the switch.
- Never read a script. Never list features as bullets aloud.
- **Discovery order** (woven in, never a form):
  1. What do you sell, or plan to sell?
  2. Roughly how many products/SKUs?
  3. Anything running today, or starting fresh?
  4. Timeline — when do you want to be live?
  5. Features — payments, delivery, WhatsApp catalogue, multi-language?
  6. Budget, asked last and softly: "most people at your stage land between ₹40k and ₹1.5L — where are you thinking?"
- **Objections:** price → anchor on revenue, not cost. "I'll think about it" → ask what specifically. "My brother handles this" → capture the decision-maker and offer to send details.
- **Barge-in:** always interruptible; on interrupt, stop TTS and yield.

### Classification (`classify_lead`)

Read signals, not keywords. Structured output over the running transcript:

```json
{
  "state": "HOT|WARM|COLD",
  "confidence": 0.0,
  "budget": "string|null",
  "products": "string|null",
  "timeline": "string|null",
  "features": ["..."],
  "barrier": "string|null",
  "evidence_quote": "the verbatim line that decided it"
}
```

- **HOT** — asks price or timeline, "how soon can you start", names a real budget, wants to see work, agrees to a next step.
- **WARM** — real need plus a named barrier: budget not now, someone else decides, busy season, "send me the details".
- **COLD** — no product, no budget, no timeline, purely curious.

**Note:** Sarvam's native SDK has no `response_format` field — **JSON is prompt-enforced**, so the classifier prompt must demand raw JSON explicitly and the parser must tolerate stray fences. See `scripts/sarvam_gate.py` for the working prompt and a tolerant extractor.

### Mid-call firing — 15 points, so belt-and-braces

Re-run `classify_lead` after **every user turn** in `asyncio.create_task` — never inline in the response path. The moment `state == HOT` and it was not before, fire the WhatsApp in the background while the agent says something natural: *"Nenu ippude mee WhatsApp ki details pampistunna — one second."* That sentence is what makes the points visible to the evaluator.

- **Idempotency guard** — a `whatsapp_sent` flag on the call record, checked before sending.
- **Fire-and-forget** — the send never blocks inside the turn; failures retry in the background per §9.
- **Prompt-level backup trigger** — also give the LLM a `send_whatsapp_now` tool with explicit rules ("the moment they ask price, name a budget, or ask how soon we can start"). If the classifier lags, the model fires first; the guard prevents collision.
- **Test both paths independently** — one persona run with the classifier disabled to prove the tool path, one with the tool removed to prove the classifier path.

### Callback scheduling (`book_callback`)

- Pass the spoken phrase plus the current IST datetime; require ISO-8601 back.
- Vague-phrase defaults: "tomorrow morning" → next day 10:30 IST; "evening" → 18:00; "next week" → next Monday 11:00; weekend → shift to Monday.
- **Always confirm aloud:** "Set — Tuesday the 25th, 10:30 in the morning. I'll call you then." The confirmation is the proof it booked.
- Persist to Postgres and register an APScheduler job that re-triggers `POST /call` with prior context loaded.

### Post-call follow-up

`sarvam-105b` over the full verbatim transcript, no latency cap — it runs after hangup, so use a long prompt and a generous token budget. Must carry: two or more verbatim specifics (their product, SKU count, timeline), the barrier if WARM, the booked slot if any, your mobile number, the resume PDF and the architecture image. It has to read like a person wrote it after a real call — no log dump, no "Dear Sir/Madam".

**The follow-up is graded on quoting things they actually said, and a smaller model's failure mode is *inventing* those quotes.** So:

- Feed the **raw transcript**, not a pre-summary. Summarising first destroys exactly the specificity being graded.
- Require a `quotes: [...]` field of 2+ exact substrings, then **assert in code that each appears verbatim in the transcript**. Regenerate once on failure, then fall back to a deterministic template built from the `classify_lead` fields.
- Two-pass: pass 1 extracts facts, pass 2 writes prose from those facts only.
- **Hand-read the output for all 6 personas** before the real call.

---

## 7. Repo layout

```
D:\Internships projects\Elevatebox ASSIGNMENT\
├── agent/
│   ├── __init__.py
│   ├── worker.py            # LiveKit AgentSession: VAD/STT/LLM/TTS wiring
│   ├── prompts.py           # persona, discovery, objections, language rules
│   ├── tools.py             # classify_lead, send_whatsapp_now, book_callback, log_turn
│   └── providers/
│       ├── __init__.py
│       └── sarvam.py        # keep this a real boundary (§2.2)
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI: POST /call, /health, webhooks
│   ├── dialer.py            # LiveKit room + SIP participant creation
│   └── scheduler.py         # APScheduler + callback re-dial
├── services/
│   ├── __init__.py
│   ├── notifier.py          # provider-agnostic WhatsApp interface
│   ├── classifier.py        # lead scoring + tolerant JSON extraction
│   ├── timeparse.py         # spoken time → IST datetime
│   ├── ttscache.py          # pre-rendered fixed strings (§10)
│   └── followup.py          # post-call writer + quote verification
├── db/
│   └── schema.sql           # leads, calls, turns, classifications, actions, callbacks
├── docs/                    # served by GitHub Pages
│   ├── resume.pdf
│   └── architecture.png
├── scripts/
│   ├── llm_gate.py          # historical: Gemini vs Groq benchmark
│   ├── sarvam_gate.py       # Sarvam LLM latency + TTS voice audition
│   └── voice_samples/       # 8kHz .wav output — pick the speaker by ear
├── tests/
│   ├── __init__.py
│   ├── scenarios.py         # 6 scripted lead personas
│   └── fixtures/            # pre-recorded .wav for STT unit tests
├── .env
├── .env.example
├── test_call.sh             # standalone Twilio caller-ID sanity check
├── plan.md                  # this file
├── checklists.md            # phased ship checklist
└── README.md                # decisions, what works, what doesn't
```

Repo root is the assignment folder itself — no `elevatebox-voice/` wrapper. Original assignment PDFs (`ElevateBox_SDE_Intern_Assignment.pdf`, `_with_Diagrams.md`) stay at root, gitignored.

**Env vars:** `LIVEKIT_URL/API_KEY/API_SECRET`, `SIP_OUTBOUND_TRUNK_ID`, `TWILIO_ACCOUNT_SID/AUTH_TOKEN/WHATSAPP_FROM/CALLER_ID`, `SARVAM_API_KEY`, `SARVAM_CHAT_MODEL`, `SARVAM_STT_MODEL`, `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER`, `DATABASE_URL` (Neon), `MY_MOBILE`, `RESUME_URL`, `ARCH_IMAGE_URL`, `TARGET_NUMBER`.

---

## 8. Latency budget

**Measured 2026-08-25 with `scripts/sarvam_gate.py`. The original sub-1.2 s target is achievable — `sarvam-105b-conversations` is Haiku-tier fast.**

| Stage | Budget | Source |
|---|---|---|
| End-of-turn detection | 300–500 ms | LiveKit semantic turn detection, not raw silence |
| STT final | ~150–300 ms | Saaras streaming; partials already in flight |
| LLM first token | **378 ms p50 / 458 ms p95** | **Measured** — `sarvam-105b-conversations`. `sarvam-105b` (general) hit 1625 ms / 4264 ms and was rejected. |
| TTS first chunk (Bulbul v2) | 561–1349 ms per speaker | Measured. `anushka` fastest at ~805 ms mean; `arya` slowest at ~2 s with a 6 s outlier. |
| **Total to first audio** | **~1.2–1.6 s realistic** | With anushka and streaming end-to-end |

The `-conversations` variant is the pick: 378 ms p50 clears the original Haiku target, and its 10/10 structured-output rate means `classify_lead` is reliable without a fallback dance.

**Rules that still matter:**
- Stream every stage. Never buffer a full response before speaking.
- Never `await` a tool before speaking — `classify_lead`, WhatsApp and `log_turn` all go in `asyncio.create_task`.
- Keep the system prompt byte-stable so prefix caching applies (Sarvam bills cached input at ₹10.98/MTok vs ₹29.28).
- Cap in-call `max_tokens` around 150. Long turns kill voice agents twice — latency and unnaturalness.
- **Serve cached TTS for fixed strings** — saves credits *and* first-audio latency on the most-repeated lines.
- **Stall phrase past ~2 s** ("one second, let me check that") rather than dead air.

---

## 9. Failure handling (the 5 engineering-judgement points)

| Failure | Response |
|---|---|
| Sarvam STT failing mid-call | End gracefully, persist partial transcript, fire the follow-up WhatsApp from what was captured. |
| Sarvam STT failing at call start | Abort, retry per the not-answered logic below. |
| Sarvam TTS failing | Cached audio covers the fixed lines; beyond that end gracefully with an immediate WhatsApp rather than dead air. |
| LLM slow (>2 s) | Speak a stall phrase. On hard failure, drop to a scripted minimal flow that still captures the callback and fires the WhatsApp. |
| Sarvam credits exhausted mid-build | **UPI top-up** — the reason this stack was chosen (§2.1). |
| A tool call fails | Conversation continues; the action queues for post-call retry. Never let a failing tool break the audio stream. |
| WhatsApp send fails | 3 retries with backoff → SMS → queue for post-call retry. Must never block the call. |
| Not answered / busy | Two retries at 20-minute intervals, then a "tried to reach you" WhatsApp. |
| Call dies at second 2 | Every call writes a transcript row regardless, and still produces a partial follow-up. |
| **Neon cold start** (~500 ms after 5 min idle) | Harmless — all DB writes are in `asyncio.create_task`, off the audio path. Use `pool_pre_ping=True` so a stale connection retries rather than raising. |
| **Neon compute-hours exhausted** | Compute suspends until the next cycle — a hard stop. Prevent it with `NullPool` (§2.3); monitor the usage graph on the Neon dashboard around day 4. |

---

## 10. Budget — Sarvam credits are the binding constraint

**100 credits ≈ ₹100.** UPI top-up works; international cards do not.

| Item | Rate | Fits in ₹100 |
|---|---|---|
| STT | ₹30/hour | 3.3 hours of audio |
| TTS Bulbul v2 | ₹15 / 10K chars | ~66,000 chars |
| TTS Bulbul v3 | ₹30 / 10K chars | ~33,000 chars |
| LLM `sarvam-105b` | ₹29.28 in / ₹73.20 out per MTok | Effectively unlimited here |

**Per 5-minute test call:** ~₹2.50 STT + ~2,700 TTS chars (₹4 on v2, ₹8 on v3) + under ₹0.50 LLM ≈ **₹6–10 on v2**. So **10–15 full calls** on the free credits.

You need ~30–40 calls (dev testing + 6 personas + the real one). **Plan a ₹200–500 UPI top-up around day 3 and treat it as the plan, not a fallback.**

**Burn rules:**
1. **Bulbul v2 by default.** Test v3 only on the 4 fixed lines to see if double the cost is audibly worth it for the real call.
2. **Cache fixed strings.** Greeting, mid-call WhatsApp line, callback confirmation, sign-off. Render once, serve from disk forever. These play on every persona and dominate TTS burn — **biggest single lever.** (`enable_cached_responses` also exists server-side.)
3. **Unit-test STT against `tests/fixtures/*.wav`**, not live calls. A live call to debug one STT edge case is ₹6–10 you didn't need to spend.
4. **Cap early dev calls at 60 seconds.** You're testing turn-taking, not endurance.
5. **Log per-call spend** on every call. Burn should be visible, not discovered at zero.
6. **Hard floor: ≥ ₹30 credit entering day 7.** Six personas plus the real call. Running dry mid-demo is unrecoverable — top up rather than gamble.
7. **`sarvam_gate.py` costs ~₹3 per run.** Run once, act on the results.

### Other services

| Service | Allowance | Status |
|---|---|---|
| **LiveKit Build (free)** | 1,000 third-party SIP min, 1,000 agent-session min, 5,000 WebRTC min, 5 concurrent, 1 deployment | **Not a constraint** — ~12% expected burn, overage $0.004/min. Don't optimise this. |
| **Twilio** | Paid, card works | US number ~₹100/mo; ~100 test minutes ₹800–1,200; ~50 WhatsApp conversations ₹150 |
| Hosting (Render) | Free tier viable | ₹0–600 |
| **Neon Postgres** | 0.5 GB storage, 100 compute-hours/month, no card | Free — but see the `NullPool` requirement (§2.3) |
| **GitHub Pages** | Static hosting for the 2 assets | Free, permanent |

**Total expected spend: ~₹1,100–2,000**, mostly Twilio voice minutes.

*Note: LiveKit sells phone numbers directly (1 free, 50 inbound min). **Ignore it** — no outbound route to India. The Twilio BYO trunk stays the plan.*

---

## 11. Test plan — 6 personas before the real call

Run each end-to-end against your own phone:

1. **Hot / English** — "How soon can you start? What'll it cost?" → HOT + WhatsApp mid-call.
2. **Warm / Telugu** — "Interest undi kaani ippudu budget ledu, next month chudam." → WARM, barrier captured, callback offered.
3. **Warm / decision-maker** — "My brother handles this." → WARM, capture, offer to send details.
4. **Cold** — "Just browsing, no products yet." → COLD, brochure, short exit.
5. **Code-switch** — "Ma products lo 200 SKUs unnai, payment gateway kavali, budget one lakh." → all four fields parsed, stays in Telugu.
6. **Callback phrasing** — "call me tomorrow morning" / "kal subah" / "next week sometime" → correct IST slot, confirmed aloud.

Also test: interrupting mid-sentence, five seconds of silence, background noise, and a hang-up at second 3 (no crash; still writes a partial follow-up).

---

## 12. Timeline

### Done (as of 2026-08-25)

- ✅ Twilio India geographic permissions enabled and verified.
- ✅ Caller ID settled (§4.0). US number `+1 478 400 8435` purchased, confirmed ringing an Indian handset. `test_call.sh` working.
- ✅ Meta WABA `Nishan's Market` approved; `+91 98753 89511` connected; US number Pending.
- ✅ LLM benchmark run (`scripts/llm_gate.py`); stack decided; `livekit-plugins-sarvam` 1.7.0 confirmed on PyPI.
- ✅ `scripts/sarvam_gate.py` written — not yet run.
- ❌ **No application code exists yet.** No `agent/`, `api/`, or `services/`. No Twilio SIP trunk. No LiveKit project.

### Remaining

| Day | Deliverable |
|---|---|
| **1** | **Run `sarvam_gate.py`** → pick the speaker by ear, write real latency into §8. **Twilio Elastic SIP trunk; LiveKit project + outbound trunk; first ring to your own phone.** Submit the 3 WhatsApp templates if drafting is allowed. |
| **2** | LiveKit worker: `AgentSession` wiring Saaras → sarvam-105b → Bulbul. **A real two-way call in English, barge-in working.** **WhatsApp checkpoint (§5.1).** |
| **3** | Telugu, Hindi and code-switch verified on real calls; TTS caching in place before heavy testing; discovery prompt tuned. **WhatsApp checkpoint. UPI top-up if credits are low.** |
| **4** | `classify_lead` + `send_whatsapp_now` — **mid-call WhatsApp proven on your own number.** This is 30 points; if a day runs long, it runs long here. |
| **5** | `book_callback` + time parsing + APScheduler re-dial; transcript persistence. **WhatsApp checkpoint.** |
| **6** | Post-call follow-up with quote verification, resume, number, architecture image; retries and fallbacks; hand-read all 6 persona outputs. |
| **7** | All 6 personas end-to-end; fixes; deploy; **make the real call to 8688664337**; send prototype, resume, diagram and the note. |

**Slip rule:** if day 4 isn't green by end of day 5, cut the callback re-dial to a logged-and-confirmed-aloud stub (still scores most of those 10 points) and protect blocks 1–3. **55 points shipped beats 75 points unfinished.**

---

## 13. What to send to 8688664337

1. **The live prototype** — a URL (or a number to text) that triggers a real call on demand, so nothing has to be installed to see it work.
2. **A one-page architecture image** — the §3 diagram cleaned up; hand-drawn is explicitly fine.
3. **Your resume PDF.**
4. **Your mobile number**, plainly visible.
5. **An under-200-word honest note** — what works, what doesn't, what you'd build next. Name the real gaps ("Telugu STT degrades in heavy noise", "callback re-dial only tested in IST", "international-card payments blocked, so the stack is all-Sarvam"). They said they'd rather see honest partial work.

---

## 14. Open decisions

- **Which Bulbul speaker.** Run `sarvam_gate.py`, listen to `scripts/voice_samples/` at 8 kHz, pick by ear, set `SARVAM_TTS_SPEAKER`, then stop fiddling — re-picking costs credits to re-cache the fixed strings.
- **Bulbul v2 vs v3.** v3 is double. Decide once, by ear, on the fixed lines only.
- **`sarvam-105b` vs `sarvam-105b-conversations`.** The gate tests both. `-conversations` is documented as built for real-time dialogue and should win on latency.
- **Deploy region.** Prefer a good route to Sarvam (India). Measure before committing — and pick the **Neon region** to match, so the worker is not crossing an ocean for every `log_turn`.

### Resolved — do not revisit

- ~~Caller ID~~ → `+14784008435`. Indian CLI is unusable.
- ~~Twilio→India outbound~~ → enabled and verified.
- ~~Which WABA~~ → `Nishan's Market`. Don't create a third.
- ~~Is the card failure vendor-specific?~~ → **Card-side.** Anthropic *and* OpenAI both declined. A free-tier key authorising proves nothing about payment.
- ~~Sarvam LiveKit plugin~~ → `livekit-plugins-sarvam` 1.7.0, first-party, version-matched. No custom adapter.
- ~~Sarvam free credits~~ → 100 (≈₹100) ≈ 10–15 calls. Binding. Top up over UPI.
- ~~Which Postgres~~ → **Neon.** Supabase is DNS-blocked on Jio, and Neon auto-wakes where Supabase needs manual unpause (§2.3).
- ~~Where to host the resume and diagram~~ → **GitHub Pages**, not raw GitHub (MIME types).
