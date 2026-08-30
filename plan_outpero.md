# ElevateBox SDE Intern Assignment --- Outpero Build Plan

**Goal:** Build the assignment around **Outpero as the voice-agent and
telephony layer**, using its dedicated Indian `+91` caller ID, native
calling/conversation stack, outcomes, webhooks, WhatsApp follow-ups and
calendar capabilities where supported. Keep custom code only for
assignment-specific integrations or gaps that Outpero does not cover.

**Core decision:** Do not rebuild the existing Twilio + LiveKit + Sarvam
voice pipeline. Outpero already provides a dedicated Indian calling
line, AI voice employee, multilingual/code-switching support, call
outcomes, webhooks, WhatsApp confirmations/follow-ups and mid-call
Google Calendar booking. Its current public documentation also describes
WhatsApp messages being sent during the call.
citeturn0search0turn0search3turn0search15

**Evidence rule:** A public product claim is not the same thing as a
feature verified in this account. For the final submission, mark a
feature as implemented only after it has been tested in the actual
Outpero account.

------------------------------------------------------------------------

## 1. Scoring-driven priority order

  -----------------------------------------------------------------------------------------------------
  Order         Requirement                         Points Outpero approach              Ship gate
  ------------- ------------------------ ----------------- ----------------------------- --------------
  1             Outbound call + two-way                 25 Outpero AI employee +         Real Indian
                conversation                               dedicated +91 line            caller-ID call
                                                                                         holds
                                                                                         naturally

  2             Mid-call WhatsApp on                    15 Outpero native                WhatsApp
                intent                                     WhatsApp/action capability    arrives before
                                                                                         call ends

  3             Hot/Warm/Cold                           15 Outpero Outcomes + backend    Six cases
                classification                             fallback if needed            classify
                                                                                         correctly

  4             Telugu/Hindi/English +                  10 Outpero                       Language
                code-switch                                multilingual/code-switching   follows caller
                                                           support                       

  5             Discovery quality                       10 Outpero Call Script +         Products,
                                                           Training                      budget,
                                                                                         timeline,
                                                                                         features
                                                                                         captured

  6             Callback scheduling                     10 Outpero Google Calendar       Spoken time
                                                           booking/action                becomes real
                                                                                         booking

  7             Follow-up WhatsApp +                    10 Outpero native WhatsApp       Context +
                required items                             first; custom Twilio only if  resume +
                                                           required                      number +
                                                                                         architecture
                                                                                         delivered

  8             Engineering judgement                    5 Webhooks, outcomes,           Failure paths
                                                           integration, README           documented
  -----------------------------------------------------------------------------------------------------

**Priority rule:** Use Outpero's built-in capabilities first. Do not
write custom code for something Outpero already demonstrably handles.

------------------------------------------------------------------------

## 2. Stack

  --------------------------------------------------------------------------
  Layer                   Choice                  Why
  ----------------------- ----------------------- --------------------------
  AI voice employee       **Outpero**             Complete AI
                                                  employee/calling platform

  Telephony               **Outpero dedicated +91 Indian caller ID; no
                          line**                  Twilio SIP or LiveKit
                                                  required for outbound
                                                  voice

  Voice/STT/TTS           **Outpero**             Current practical test
                                                  showed cleaner voice and
                                                  lower perceived latency

  Call script             **Outpero Call Script** Visual conversation flow

  Pre-call data           **Outpero variables**   Lead Name, Phone number,
                                                  Enquiry Topic

  Lead outcomes           **Outpero Outcomes**    Structured post-call
                                                  extraction

  WhatsApp                **Outpero native        Current product
                          WhatsApp** where        documentation advertises
                          available               confirmations/follow-ups
                                                  and mid-call WhatsApp

  Custom WhatsApp         **Existing Twilio       Use only if Outpero cannot
  fallback                WhatsApp sender**       satisfy the
                                                  assignment-specific
                                                  message

  Calendar                **Outpero Google        Public product
                          Calendar integration**  documentation says
                                                  calendar booking can
                                                  happen mid-call

  Post-call integration   **Outpero Post-Call     Custom backend can receive
                          Webhook**               transcript/outcome/call
                                                  data

  Backend                 **FastAPI**, only if    Assignment-specific
                          needed                  processing/integration

  Database                **Neon Postgres**, only Existing project database
                          if persistence is       
                          required                

  Hosting                 Outpero for voice;      Removes the long-running
                          small/local backend if  voice-worker requirement
                          needed                  
  --------------------------------------------------------------------------

Outpero's current pricing page lists a dedicated DLT-registered `+91`
calling line, WhatsApp confirmations/follow-ups, Google Calendar
booking, CRM/webhook integrations and multilingual support.
citeturn0search0

------------------------------------------------------------------------

## 3. Architecture

### Primary architecture

``` text
                         ┌───────────────────────────────┐
                         │           OUTPERO              │
                         │                               │
Lead + phone + topic ──► │ AI Employee: Ishita           │
                         │ Dedicated +91 caller ID       │
                         │ Call Script                   │
                         │ Voice / STT / conversation    │
                         │ Outcomes                      │
                         │ WhatsApp actions              │
                         │ Google Calendar booking       │
                         └──────────────┬────────────────┘
                                        │
                         ┌──────────────┼──────────────┐
                         │              │              │
                         ▼              ▼              ▼
                    WhatsApp       Calendar       Post-call
                    follow-up      booking        webhook
                         │              │              │
                         │              │              ▼
                         │              │       ┌──────────────┐
                         │              │       │ FastAPI      │
                         │              │       │ / database   │
                         │              │       └──────────────┘
                         │              │
                         ▼              ▼
                      Caller        Scheduled
                                    callback/meeting
```

### Custom backend is optional, not the voice layer

``` text
Outpero
  ├── Voice / telephony
  ├── AI conversation
  ├── +91 caller ID
  ├── Outcomes
  ├── WhatsApp
  └── Calendar
          │
          └── webhook → FastAPI → Neon
```

Do not put LiveKit, Twilio Voice or Sarvam into the new architecture
unless a concrete Outpero limitation forces it.

------------------------------------------------------------------------

## 4. Outpero employee configuration

### Employee

-   **Name:** Ishita
-   **Role:** Customer Support / Sales Representative
-   **Mode:** Instant Lead
-   **Primary language:** English
-   Enable/test Hindi and Telugu according to the capabilities exposed
    by the account.

Outpero publicly advertises native Telugu and real-time Telugu-English
switching, with Indian-language support designed for business calls.
citeturn0search6turn0search13

### Opening

Use:

> "Hello, am I speaking with {{Lead Name}}?"

Then:

> "Hi, this is Ishita from ElevateBox. You had enquired about {{Enquiry
> Topic}}, so I wanted to understand what you're looking for and help
> with the next step."

Keep the opening short.

### Conversation rules

-   Speak naturally.
-   Keep responses concise.
-   Stop when the caller interrupts.
-   Ask one question at a time.
-   Do not repeat information unnecessarily.
-   Mirror the caller's language where supported.
-   Allow Indian English/code-switching.
-   Never invent company information.
-   If information is unavailable, say so.

### Discovery order

1.  What do they sell or plan to sell?
2.  Rough number of products/SKUs.
3.  Existing website or starting fresh?
4.  Desired launch timeline.
5.  Required features such as payments, delivery, WhatsApp catalogue or
    multiple languages.
6.  Budget, asked naturally and late.

------------------------------------------------------------------------

## 5. Variables

Pre-call:

``` text
lead_name
phone
enquiry_topic
```

Captured:

``` text
specific_question
next_step
lead_temperature
products
budget
timeline
features
barrier
decision_maker
callback_time
```

Do not create unnecessary fields merely because the UI permits them.

------------------------------------------------------------------------

## 6. Lead classification

Target:

``` json
{
  "state": "HOT|WARM|COLD",
  "confidence": 0.0,
  "budget": "string|null",
  "products": "string|null",
  "timeline": "string|null",
  "features": ["..."],
  "barrier": "string|null",
  "evidence_quote": "string|null"
}
```

### HOT

-   Asks price.
-   Names a budget.
-   Asks how soon development can start.
-   Wants to see work.
-   Agrees to a concrete next step.

### WARM

-   Genuine requirement but budget is delayed.
-   Another person makes the decision.
-   Wants details first.
-   Has a timeline but is not ready.

### COLD

-   Just browsing.
-   No current business/product.
-   No timeline or meaningful requirement.

**Implementation:** first use Outpero Outcomes/extraction. Only add
backend classification if Outpero's extraction is insufficient for the
six evaluation personas.

------------------------------------------------------------------------

## 7. Mid-call WhatsApp

This is now a **feature to test directly**, not merely a theoretical
integration.

Outpero's current public material says its AI agent can send a WhatsApp
message during the call, and its product pages describe WhatsApp
confirmations/follow-ups. citeturn0search15turn0search0

### Required assignment behavior

``` text
Caller expresses high intent
        ↓
Outpero detects intent / executes configured action
        ↓
WhatsApp message is sent
        ↓
Caller receives it before the call ends
```

Suggested spoken line:

> "I'll send those details to your WhatsApp as well."

### Test

-   Configure the Outpero action/follow-up.
-   Use the caller's actual WhatsApp number.
-   Trigger it from a high-intent scenario.
-   Confirm message timestamp.
-   Confirm the call is still active when the message arrives.
-   Confirm the message contains the required context.

If the Outpero account cannot expose the exact trigger required by the
assignment, use the post-call webhook/custom Twilio path as a fallback
and document the difference.

------------------------------------------------------------------------

## 8. Post-call WhatsApp

Outpero's current product pages advertise WhatsApp
confirmations/follow-ups, while the employee Outcomes UI exposes
Post-Call Webhooks. citeturn0search0turn0search4

### Preferred

Use Outpero's native WhatsApp follow-up if it can deliver all
assignment-required content:

-   caller-specific requirement
-   budget/timeline when captured
-   your mobile number
-   resume
-   architecture image

### Fallback

If Outpero's native WhatsApp message cannot carry the exact required
assets/context:

``` text
Outpero
   ↓
Post-Call Webhook
   ↓
FastAPI
   ↓
Existing Twilio WhatsApp sender
```

Backend:

``` text
POST /webhooks/outpero
```

Process:

1.  Validate event.
2.  Extract call ID and phone.
3.  Extract transcript.
4.  Extract Outcomes.
5.  Build follow-up context.
6.  Send Twilio template.
7.  Store message SID/event.
8.  Reject duplicate processing.

Do not invent caller quotes. Use transcript-grounded information only.

------------------------------------------------------------------------

## 9. WhatsApp content

Recommended follow-up structure:

``` text
Hi, thanks for speaking with Ishita.

Based on our conversation about {{specific_requirement}},
here are the details for the next step.

You can reach me at {{MY_MOBILE}}.

Resume: {{RESUME_URL}}
Architecture: {{ARCH_IMAGE_URL}}
```

If Outpero's native WhatsApp system supports these exact
variables/assets, use it. Otherwise use the existing Twilio sender.

------------------------------------------------------------------------

## 10. Callback scheduling

Outpero currently advertises **Google Calendar booking mid-call**.
citeturn0search0turn0search10

### Preferred flow

``` text
Caller: "Call me tomorrow morning."
        ↓
Outpero interprets time
        ↓
Calendar availability checked
        ↓
Slot booked
        ↓
Caller receives confirmation
        ↓
WhatsApp confirmation if configured
```

### Test

-   "Call me tomorrow morning."
-   "Kal subah call karna."
-   "Call me Tuesday at 3 PM."

Verify:

-   correct date
-   correct IST time
-   real calendar entry
-   spoken confirmation
-   WhatsApp confirmation if enabled

### Important distinction

If the assignment specifically requires **a phone callback** rather than
a calendar meeting, verify whether Outpero's calendar booking actually
creates the required callback. Do not call a meeting booking a phone
callback just to tick a checkbox.

------------------------------------------------------------------------

## 11. Six test personas

1.  **Hot / English:** "How soon can you start? What will it cost?" →
    HOT + mid-call WhatsApp.
2.  **Warm / Hindi:** "Interest hai, lekin abhi budget nahi hai. Next
    month dekhte hain." → WARM + budget barrier.
3.  **Decision-maker:** "My brother handles the website." → WARM +
    decision-maker captured.
4.  **Cold:** "I'm just browsing. I don't have any products yet." →
    COLD.
5.  **Code-switch:** "Mere business mein around 200 SKUs hain, payment
    gateway chahiye and budget one lakh ke around hai." →
    products/features/budget captured.
6.  **Callback:** "Call me tomorrow morning", "Kal subah call karna",
    "Call me Tuesday at 3 PM." → correct scheduled action.

Also test:

-   interruption
-   silence
-   background noise
-   early hang-up
-   unknown question
-   duplicate webhook
-   WhatsApp delivery while the call is active

------------------------------------------------------------------------

## 12. Repo layout

``` text
ElevateBox ASSIGNMENT/
├── outpero/
│   ├── employee_config.md
│   ├── variables.json
│   └── test_personas.md
├── api/
│   ├── main.py
│   ├── outpero_webhook.py
│   └── whatsapp.py
├── services/
│   ├── classifier.py
│   └── followup.py
├── db/
│   └── schema.sql
├── docs/
│   ├── resume.pdf
│   └── architecture.png
├── tests/
│   └── scenarios.py
├── .env
├── .env.example
├── plan.md
├── checklists.md
└── README.md
```

Do not create LiveKit/Sarvam worker files in the new Outpero
implementation unless they are still needed elsewhere in the repository.

------------------------------------------------------------------------

## 13. Latency and voice comparison

Do not present this as a controlled benchmark.

  -----------------------------------------------------------------------
  System                  Method                  Observation
  ----------------------- ----------------------- -----------------------
  Outpero                 Browser + Outpero test  Cleaner voice; almost
                          calls                   instantaneous perceived
                                                  response

  Current system          Local Twilio +          \~1.2--1.5 s to first
                          LiveKit + Sarvam        audio

  Current Hindi           Sarvam Bulbul V3        Less natural in the
                                                  comparison

  Outpero                 Website/test call       More polished overall
                                                  voice experience
  -----------------------------------------------------------------------

Suggested README wording:

> "I tested Outpero directly through its website and test calls rather
> than integrating it into my existing voice pipeline. In my practical
> testing, Outpero produced cleaner voice quality and noticeably lower
> perceived latency. My existing Twilio + LiveKit + Sarvam pipeline was
> functional, but its Hindi voice sounded less natural and its current
> local end-to-end response was around 1.2--1.5 seconds to first audio."

Do not call this a controlled benchmark.

------------------------------------------------------------------------

## 14. Failure handling

  -----------------------------------------------------------------------
  Failure                             Response
  ----------------------------------- -----------------------------------
  Outpero call fails                  Record and retry only through
                                      supported mechanisms

  WhatsApp fails                      Retry/record according to the
                                      available Outpero or Twilio path

  Duplicate webhook                   Idempotency prevents duplicate
                                      processing

  Missing transcript                  Send minimal deterministic
                                      follow-up

  Missing phone                       Do not attempt WhatsApp

  Classification unavailable          Use Outcomes/backend fallback

  Ambiguous callback time             Confirm instead of silently
                                      guessing

  Calendar booking fails              Tell caller clearly; do not claim a
                                      booking

  Native WhatsApp cannot send         Fall back to Twilio webhook path
  required assets                     

  Outpero unavailable                 Document vendor dependency

  Custom backend stops                Restart and verify `/health` before
                                      demo
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 15. Budget rules

Current Outpero public pricing lists a dedicated `+91` line and paid
calling beyond the free/test path, with call charges based on the
selected voice tier. citeturn0search0

For the assignment:

-   Keep test calls short.
-   Test script logic before long calls.
-   Use free/test tools where possible.
-   Save evidence immediately after successful tests.
-   Do not repeat already-proven calls unnecessarily.
-   Do not create multiple accounts just to farm promotional credits.
-   Do not spend money rebuilding infrastructure that Outpero already
    provides.

------------------------------------------------------------------------

## 16. Timeline

### Phase 0 --- Outpero setup

-   Configure Ishita.
-   Configure Indian +91 calling.
-   Configure languages.
-   Add variables.
-   Configure script.
-   Configure Outcomes.
-   Verify test call.

### Phase 1 --- Conversation

-   English.
-   Hindi.
-   Telugu.
-   Code-switch.
-   Interruption.
-   Script tuning.

### Phase 2 --- Classification

-   Configure Hot/Warm/Cold.
-   Run six personas.
-   Verify extracted fields.

### Phase 3 --- Native WhatsApp

-   Find/configure Outpero WhatsApp action/follow-up.
-   Test high-intent mid-call delivery.
-   Test post-call follow-up.
-   Confirm required content.
-   Use Twilio fallback only if necessary.

### Phase 4 --- Callback

-   Configure Google Calendar.
-   Test spoken time interpretation.
-   Verify actual booking.
-   Determine whether it satisfies the assignment's phone-callback
    requirement.

### Phase 5 --- Custom integration

Only if needed:

-   Post-call webhook.
-   FastAPI.
-   Twilio WhatsApp.
-   Neon persistence.
-   Idempotency.

### Phase 6 --- Hardening

-   Failure cases.
-   Evidence.
-   README.
-   Architecture diagram.

### Phase 7 --- Real call

1.  Verify Ishita.
2.  Verify +91 caller ID.
3.  Verify test allowance.
4.  Verify WhatsApp.
5.  Verify calendar.
6.  Run final short test.
7.  Call `+91 8688664337`.
8.  Confirm conversation.
9.  Confirm WhatsApp.
10. Confirm callback/calendar behavior.
11. Save evidence.

------------------------------------------------------------------------

## 17. Submission package

Send:

1.  Outpero prototype/test-call access where appropriate.
2.  Architecture image.
3.  Resume PDF.
4.  Mobile number.
5.  GitHub repository.
6.  Short honest implementation note.

Suggested note:

> "I tested Outpero directly through its website and test calls rather
> than integrating it into my existing Twilio + LiveKit + Sarvam voice
> pipeline. Outpero gave me a dedicated Indian +91 caller ID and, in my
> practical testing, produced cleaner voice quality and noticeably lower
> perceived latency. I therefore used Outpero as the voice and telephony
> layer and kept custom code focused on the assignment-specific
> integrations and verification. I have explicitly documented any
> features that were not available or not independently verified in my
> account."

------------------------------------------------------------------------

## 18. Resolved decisions

-   ~~Rebuild LiveKit voice agent~~ → **No.**
-   ~~Use Twilio for outbound voice~~ → **No. Outpero provides the
    Indian caller ID/telephony layer.**
-   ~~Use Sarvam for TTS~~ → **No. Outpero owns the voice layer.**
-   ~~Replace Outpero WhatsApp with Twilio immediately~~ → **No. Test
    Outpero native WhatsApp first.**
-   ~~Build custom callback scheduler first~~ → **No. Test Outpero
    Calendar first.**
-   ~~Claim every advertised feature automatically~~ → **No. Verify in
    the actual account.**
-   ~~Depend on multiple promotional accounts~~ → **No.**
