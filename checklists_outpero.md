# Ship Checklist --- ElevateBox Outpero Voice Agent

Phased. **Each phase has an exit gate.** The new architecture uses
Outpero for voice + Indian telephony and only adds custom code where the
assignment actually requires it.

Design doc: [plan.md](plan.md)

------------------------------------------------------------------------

## Target architecture

``` text
Lead
 ↓
OUTPERO
 ├── AI voice employee
 ├── Dedicated +91 caller ID
 ├── Call script
 ├── Outcomes
 ├── WhatsApp
 └── Google Calendar
        │
        └── webhook → FastAPI/Neon only when custom processing is needed
```

------------------------------------------------------------------------

# Phase 0 --- Outpero employee setup

## Employee

-   [ ] Create/configure **Ishita**
-   [ ] Customer Support / Sales role
-   [ ] Instant Lead mode
-   [ ] Primary language = English
-   [ ] Enable/test Telugu
-   [ ] Test Hindi understanding/code-switching
-   [ ] Confirm employee can make a test call

## Indian caller ID

-   [ ] Confirm Outpero assigned a dedicated `+91` number
-   [ ] Confirm the number appears as caller ID on a real test call
-   [ ] Confirm caller sees an Indian number
-   [ ] Save screenshot/evidence

### Exit gate

-   [ ] Ishita calls the phone with a `+91` caller ID
-   [ ] No Twilio Voice number is involved
-   [ ] No LiveKit SIP path is involved

------------------------------------------------------------------------

# Phase 1 --- Call script

## Opening

-   [ ] Uses `Lead Name`
-   [ ] Introduces Ishita/ElevateBox
-   [ ] Explains reason for call
-   [ ] Does not sound like a long scripted monologue

## Discovery

-   [ ] Products/business
-   [ ] SKU count
-   [ ] Existing website
-   [ ] Timeline
-   [ ] Required features
-   [ ] Budget asked naturally

## Conversation quality

-   [ ] Short responses
-   [ ] No unnecessary repetition
-   [ ] Stops when caller interrupts
-   [ ] Does not talk over caller
-   [ ] Handles unknown questions honestly
-   [ ] Does not invent company information

### Exit gate

-   [ ] 60+ seconds of natural conversation
-   [ ] No obvious script-reading
-   [ ] Interruption test passes

------------------------------------------------------------------------

# Phase 2 --- Languages

-   [ ] English test
-   [ ] Telugu test
-   [ ] Hindi understanding test
-   [ ] Telugu-English code-switch test
-   [ ] Hindi-English code-switch test
-   [ ] Caller does not have to repeat themselves unnecessarily
-   [ ] Voice remains natural during switching

Outpero publicly advertises native Telugu and Telugu-English switching;
verify the exact language behavior in the account before claiming
broader Hindi voice support. citeturn0search6turn0search13

### Exit gate

-   [ ] English works
-   [ ] Telugu works
-   [ ] Code-switch works
-   [ ] Hindi behavior is documented accurately

------------------------------------------------------------------------

# Phase 3 --- Lead classification

## HOT

-   [ ] Price question → HOT
-   [ ] Real budget → HOT
-   [ ] "How soon can you start?" → HOT
-   [ ] Wants to see work → HOT

## WARM

-   [ ] Budget delayed → WARM
-   [ ] Another decision-maker → WARM
-   [ ] Wants details first → WARM

## COLD

-   [ ] Just browsing → COLD
-   [ ] No current product/business → COLD

## Evidence

-   [ ] `lead_temperature` captured
-   [ ] Budget captured
-   [ ] Products captured
-   [ ] Timeline captured
-   [ ] Features captured
-   [ ] Barrier captured
-   [ ] Evidence is grounded in caller's words

### Exit gate

-   [ ] Six test personas classify correctly

------------------------------------------------------------------------

# Phase 4 --- Native WhatsApp

Outpero publicly advertises WhatsApp confirmations/follow-ups and
describes WhatsApp being sent during calls.
citeturn0search0turn0search15

## Capability

-   [ ] Find WhatsApp configuration/action in Outpero
-   [ ] Determine whether message can be triggered during an active call
-   [ ] Determine whether it can use caller phone number
-   [ ] Determine whether it can include dynamic context
-   [ ] Determine whether it can send the required assets/links

## Mid-call test

-   [ ] Use HOT persona
-   [ ] Trigger WhatsApp from active call
-   [ ] Record message timestamp
-   [ ] Confirm call is still active
-   [ ] Confirm message arrives on caller's WhatsApp
-   [ ] Confirm message contains relevant context

## Required content

-   [ ] Caller-specific requirement
-   [ ] Budget/timeline when captured
-   [ ] Your mobile number
-   [ ] Resume link
-   [ ] Architecture image/link

### Exit gate

-   [ ] WhatsApp arrives before call ends
-   [ ] Message contains assignment-required information

## Fallback

If native Outpero WhatsApp cannot satisfy the exact assignment:

-   [ ] Enable Outpero Post-Call Webhook
-   [ ] Create FastAPI endpoint
-   [ ] Send via existing Twilio WhatsApp sender
-   [ ] Add idempotency
-   [ ] Document that Twilio is the fallback integration

------------------------------------------------------------------------

# Phase 5 --- Post-call webhook

The Outpero Outcomes UI exposes Post-Call Webhooks.

## Backend

-   [ ] FastAPI endpoint
-   [ ] Validate webhook
-   [ ] Extract call ID
-   [ ] Extract phone
-   [ ] Extract transcript
-   [ ] Extract outcomes
-   [ ] Store required data
-   [ ] Duplicate-event protection

## WhatsApp fallback

-   [ ] Existing Twilio sender works
-   [ ] Template approved
-   [ ] Resume URL works
-   [ ] Architecture URL works
-   [ ] Caller-specific context generated
-   [ ] Message delivered

### Exit gate

-   [ ] Post-call event reaches backend
-   [ ] Follow-up reaches WhatsApp
-   [ ] Duplicate webhook does not duplicate message

------------------------------------------------------------------------

# Phase 6 --- Callback / Calendar

Outpero publicly advertises Google Calendar booking mid-call.
citeturn0search0turn0search10

## Configuration

-   [ ] Connect Google Calendar
-   [ ] Configure available hours
-   [ ] Verify timezone = IST
-   [ ] Confirm Outpero can book during the call

## Tests

-   [ ] "Call me tomorrow morning."
-   [ ] "Kal subah call karna."
-   [ ] "Call me Tuesday at 3 PM."

## Verify

-   [ ] Correct date
-   [ ] Correct time
-   [ ] Calendar entry created
-   [ ] Caller receives spoken confirmation
-   [ ] WhatsApp confirmation if enabled

## Critical distinction

-   [ ] Determine whether this creates a **phone callback** or only a
    **calendar meeting**
-   [ ] Do not claim a calendar booking is a phone callback unless
    Outpero actually initiates the phone call

### Exit gate

-   [ ] Real callback requirement works, OR
-   [ ] Calendar booking is demonstrated and the phone-callback
    limitation is documented

------------------------------------------------------------------------

# Phase 7 --- Six end-to-end personas

## 1. Hot / English

-   [ ] "How soon can you start? What will it cost?"
-   [ ] HOT
-   [ ] WhatsApp during call

## 2. Warm / Hindi

-   [ ] "Interest hai, lekin abhi budget nahi hai. Next month dekhte
    hain."
-   [ ] WARM
-   [ ] Budget barrier captured

## 3. Decision-maker

-   [ ] "My brother handles the website."
-   [ ] WARM
-   [ ] Decision-maker captured

## 4. Cold

-   [ ] "I'm just browsing. I don't have any products yet."
-   [ ] COLD
-   [ ] Short exit

## 5. Code-switch

-   [ ] "Mere business mein around 200 SKUs hain, payment gateway
    chahiye and budget one lakh ke around hai."
-   [ ] 200 SKUs captured
-   [ ] Payment gateway captured
-   [ ] ₹1 lakh captured
-   [ ] Natural code-switching

## 6. Callback

-   [ ] "Call me tomorrow morning."
-   [ ] "Kal subah call karna."
-   [ ] "Call me Tuesday at 3 PM."
-   [ ] Correct scheduled action
-   [ ] Spoken confirmation

### Additional

-   [ ] Interruption
-   [ ] Silence
-   [ ] Background noise
-   [ ] Early hang-up
-   [ ] Unknown question
-   [ ] WhatsApp delivery while call is active

### Exit gate

-   [ ] All six completed
-   [ ] Evidence saved

------------------------------------------------------------------------

# Phase 8 --- Custom backend only if needed

## API

-   [ ] `/health`
-   [ ] `/webhooks/outpero`
-   [ ] `/outpero/midcall` only if Outpero requires an external action

## Database

Only if needed:

-   [ ] call ID
-   [ ] phone
-   [ ] outcome
-   [ ] temperature
-   [ ] callback time
-   [ ] WhatsApp message SID
-   [ ] webhook event ID

## Reliability

-   [ ] Idempotency
-   [ ] Twilio error logging
-   [ ] Retry strategy
-   [ ] Missing-data handling

### Exit gate

-   [ ] Backend adds value that Outpero cannot already provide
-   [ ] No unnecessary second voice pipeline

------------------------------------------------------------------------

# Phase 9 --- Failure handling

-   [ ] Outpero call failure handled
-   [ ] WhatsApp failure handled
-   [ ] Duplicate webhook handled
-   [ ] Missing transcript handled
-   [ ] Missing phone handled
-   [ ] Unknown question handled honestly
-   [ ] Calendar booking failure handled
-   [ ] Ambiguous callback handled
-   [ ] Native WhatsApp limitation documented if encountered
-   [ ] Backend restart tested if backend exists

------------------------------------------------------------------------

# Phase 10 --- Voice comparison evidence

## Outpero

-   [ ] Website test completed
-   [ ] Test call completed
-   [ ] Indian +91 caller ID verified
-   [ ] Voice quality evaluated
-   [ ] Latency observed

## Existing implementation

-   [ ] Twilio + LiveKit + Sarvam comparison recorded
-   [ ] \~1.2--1.5 s first-audio observation recorded
-   [ ] Hindi voice issue noted

## README wording

-   [ ] State that Outpero was tested directly
-   [ ] State that it was not integrated into the old voice pipeline
-   [ ] State that Outpero sounded cleaner/more polished
-   [ ] State that current Hindi sounded less natural
-   [ ] State latency as a practical observation, not controlled
    benchmark

------------------------------------------------------------------------

# Phase 11 --- Evidence/screenshots

-   [ ] Outpero dashboard
-   [ ] Ishita employee
-   [ ] Dedicated +91 number
-   [ ] Call Script
-   [ ] Outcomes
-   [ ] WhatsApp configuration/action
-   [ ] Successful mid-call WhatsApp
-   [ ] Post-call webhook if used
-   [ ] Calendar booking if used
-   [ ] Successful call
-   [ ] WhatsApp follow-up

------------------------------------------------------------------------

# Phase 12 --- README

-   [ ] Why Outpero
-   [ ] Why LiveKit/Twilio voice was removed
-   [ ] Architecture
-   [ ] Indian caller ID
-   [ ] Voice comparison
-   [ ] Lead classification
-   [ ] WhatsApp
-   [ ] Calendar/callback
-   [ ] Webhook integration if used
-   [ ] Known limitations
-   [ ] Exact features actually tested

------------------------------------------------------------------------

# Phase 13 --- Real evaluator call

Do not make the real call until the gates are green.

-   [ ] Ishita ready
-   [ ] +91 caller ID verified
-   [ ] Test allowance/credits checked
-   [ ] WhatsApp tested
-   [ ] Calendar tested
-   [ ] Resume URL checked
-   [ ] Architecture URL checked
-   [ ] Backend `/health` checked if used
-   [ ] One final short test call
-   [ ] Call `+91 8688664337`
-   [ ] Confirm conversation
-   [ ] Confirm lead classification
-   [ ] Confirm WhatsApp
-   [ ] Confirm callback/calendar behavior
-   [ ] Save evidence

------------------------------------------------------------------------

# Phase 14 --- Submission

-   [ ] GitHub updated
-   [ ] `plan.md`
-   [ ] `checklists.md`
-   [ ] README
-   [ ] Architecture image
-   [ ] Resume
-   [ ] Prototype/test instructions
-   [ ] Mobile number
-   [ ] Known limitations
-   [ ] No unsupported claims

------------------------------------------------------------------------

# Standing rules

-   [ ] Do not rebuild LiveKit unless Outpero fails a required scoring
    item.
-   [ ] Do not use Twilio Voice for the new architecture.
-   [ ] Do not use Sarvam TTS/STT for the new architecture.
-   [ ] Do not add a custom scheduler before testing Outpero Calendar.
-   [ ] Do not add custom WhatsApp before testing Outpero native
    WhatsApp.
-   [ ] Do not claim Hindi voice support unless the actual account
    demonstrates it.
-   [ ] Do not claim a calendar meeting is a phone callback.
-   [ ] Do not claim a controlled latency benchmark.
-   [ ] Do not invent undocumented Outpero APIs.
-   [ ] Do not waste paid credits on already-proven behavior.
-   [ ] Do not create multiple accounts just to farm promotional
    credits.
-   [ ] Prefer Outpero-native functionality over custom code.
