-- ElevateBox voice agent -- Neon Postgres schema
-- Six tables. Idempotent (safe to re-run). See plan.md section 6.
--
-- Design notes worth naming:
--
-- 1. classifications is a HISTORY table, not "latest state on the call row".
--    Being able to point at "state flipped to HOT at turn 7, at 22:14:03,
--    and the WhatsApp fired at 22:14:04" is the demo of the 15-point row.
--
-- 2. actions.sent_at < calls.ended_at is the single most valuable comparison
--    in this whole schema. It is the machine-checkable proof that the
--    mid-call WhatsApp fired mid-call (not after). Every column here exists
--    to support that assertion.
--
-- 3. APScheduler creates and owns its own `apscheduler_jobs` table on first
--    use of the postgres jobstore. We only reference its job IDs from
--    callbacks.apscheduler_job_id; we do not manage that table ourselves.

BEGIN;

-- -------------------------------------------------------------------- leads
CREATE TABLE IF NOT EXISTS leads (
    id                  BIGSERIAL PRIMARY KEY,
    phone               TEXT NOT NULL UNIQUE,          -- E.164, e.g. +919830237930
    name                TEXT,
    preferred_language  TEXT CHECK (preferred_language IN ('te-IN', 'hi-IN', 'en-IN')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE  leads IS 'The person on the other end. Unique per phone number.';
COMMENT ON COLUMN leads.preferred_language IS 'Locked once STT detects the reply language on first pickup; used to bias the follow-up.';

-- -------------------------------------------------------------------- calls
CREATE TABLE IF NOT EXISTS calls (
    id                  BIGSERIAL PRIMARY KEY,
    lead_id             BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    status              TEXT NOT NULL CHECK (status IN (
                            'initiated', 'ringing', 'answered', 'completed',
                            'no-answer', 'busy', 'failed', 'canceled')),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    duration_s          INTEGER,                       -- populated at hangup; also computable from ended-started
    livekit_room        TEXT,
    twilio_call_sid     TEXT,
    outcome             TEXT CHECK (outcome IN ('HOT', 'WARM', 'COLD')),  -- final classification, backfilled from classifications
    attempt_no          INTEGER NOT NULL DEFAULT 1,    -- 1 for first dial; retries increment
    is_callback_of      BIGINT REFERENCES calls(id) ON DELETE SET NULL,  -- links a re-dialled callback to its original
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE  calls IS 'One row per dial attempt (including retries and callbacks).';
COMMENT ON COLUMN calls.is_callback_of IS 'Self-reference: if this call was triggered by a scheduled callback, points at the original call.';

CREATE INDEX IF NOT EXISTS ix_calls_lead_started  ON calls (lead_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_calls_status        ON calls (status) WHERE status IN ('initiated', 'ringing', 'answered');
-- Unique index on twilio_call_sid so Outpero webhook can upsert by external call UUID.
CREATE UNIQUE INDEX IF NOT EXISTS ux_calls_external_id ON calls (twilio_call_sid) WHERE twilio_call_sid IS NOT NULL;

-- -------------------------------------------------------------------- turns
CREATE TABLE IF NOT EXISTS turns (
    id                  BIGSERIAL PRIMARY KEY,
    call_id             BIGINT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    seq                 INTEGER NOT NULL,              -- 0, 1, 2, ... per call
    role                TEXT NOT NULL CHECK (role IN ('agent', 'user')),
    text                TEXT NOT NULL,
    language            TEXT CHECK (language IN ('te-IN', 'hi-IN', 'en-IN')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (call_id, seq)                              -- one row per turn number per call
);
COMMENT ON TABLE turns IS 'The verbatim transcript. The follow-up writer reads this raw; do not summarise on the way in.';

CREATE INDEX IF NOT EXISTS ix_turns_call_created  ON turns (call_id, created_at);

-- ---------------------------------------------------------- classifications
CREATE TABLE IF NOT EXISTS classifications (
    id                  BIGSERIAL PRIMARY KEY,
    call_id             BIGINT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    turn_seq            INTEGER NOT NULL,              -- the user turn that this classify_lead run was triggered by
    state               TEXT NOT NULL CHECK (state IN ('HOT', 'WARM', 'COLD')),
    confidence          NUMERIC(3,2) NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    budget              TEXT,
    products            TEXT,
    timeline            TEXT,
    features            TEXT[] NOT NULL DEFAULT '{}',  -- e.g. ['payments','whatsapp_catalog']
    barrier             TEXT,                          -- present for WARM
    evidence_quote      TEXT NOT NULL,                 -- the verbatim line that decided this state
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE classifications IS 'History table -- one row per classify_lead run, not one per call. Being able to point at "state flipped to HOT at turn 7" is the 15-point story.';

CREATE INDEX IF NOT EXISTS ix_class_call_created  ON classifications (call_id, created_at DESC);

-- ------------------------------------------------------------------ actions
CREATE TABLE IF NOT EXISTS actions (
    id                  BIGSERIAL PRIMARY KEY,
    call_id             BIGINT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    type                TEXT NOT NULL CHECK (type IN (
                            'whatsapp_midcall',
                            'whatsapp_followup_context',
                            'whatsapp_followup_resume',
                            'callback_confirmation',
                            'sms_fallback')),
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                            'pending', 'sent', 'failed', 'retrying', 'gave_up')),
    payload             JSONB NOT NULL,                -- template SID, variable values, media URL, etc.
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ                    -- the single most valuable timestamp in the schema
);
COMMENT ON TABLE  actions IS 'Outbound side effects with retry state. actions.sent_at < calls.ended_at proves mid-call firing.';
COMMENT ON COLUMN actions.sent_at IS 'For whatsapp_midcall this must be less than the parent calls.ended_at -- that comparison is the 15-point proof.';

-- Idempotency guard for once-per-call action types (the WhatsApp midcall
-- must fire at most once even if classifications flips HOT -> WARM -> HOT).
-- Partial unique index so retries of other types can share (call_id, type).
CREATE UNIQUE INDEX IF NOT EXISTS ux_actions_midcall_once
    ON actions (call_id) WHERE type = 'whatsapp_midcall';

CREATE INDEX IF NOT EXISTS ix_actions_retry
    ON actions (status, created_at) WHERE status IN ('pending', 'retrying');

CREATE INDEX IF NOT EXISTS ix_actions_call_type   ON actions (call_id, type);

-- ---------------------------------------------------------------- callbacks
CREATE TABLE IF NOT EXISTS callbacks (
    id                  BIGSERIAL PRIMARY KEY,
    lead_id             BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    call_id             BIGINT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,  -- the call where the booking happened
    spoken_phrase       TEXT NOT NULL,                 -- what the caller literally said, e.g. "kal subah"
    scheduled_for_utc   TIMESTAMPTZ NOT NULL,          -- resolved IST time, stored in UTC
    status              TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                            'scheduled', 'fired', 'cancelled', 'completed', 'failed')),
    apscheduler_job_id  TEXT,                          -- links to apscheduler_jobs table (managed by APScheduler)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE  callbacks IS 'Scheduled re-dials. The spoken phrase is stored alongside the resolved datetime so parsing bugs stay debuggable.';
COMMENT ON COLUMN callbacks.spoken_phrase IS 'Keep this raw. If timeparse gets kal subah wrong we can see exactly what it heard vs what it booked.';

CREATE INDEX IF NOT EXISTS ix_callbacks_pending
    ON callbacks (status, scheduled_for_utc) WHERE status = 'scheduled';

-- One callback per call at most. If Priya invokes book_callback twice in
-- one conversation (LLM sometimes double-fires tool calls after saying
-- a confirmation aloud), the second INSERT hits this and the app layer
-- catches UniqueViolation to return the existing speak_line unchanged.
CREATE UNIQUE INDEX IF NOT EXISTS ux_callbacks_one_per_call
    ON callbacks (call_id) WHERE status IN ('scheduled', 'fired');

COMMIT;

-- --------------------------------------------------------------------------
-- Sanity queries -- run these after applying the schema to verify.
--
-- \d+ leads calls turns classifications actions callbacks
--
-- SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
--
-- The 15-point proof query (returns rows when mid-call WhatsApp fires before
-- the call ends):
--
--   SELECT c.id AS call_id, c.ended_at, a.sent_at,
--          (a.sent_at < c.ended_at) AS fired_mid_call
--   FROM   calls c
--   JOIN   actions a ON a.call_id = c.id
--   WHERE  a.type = 'whatsapp_midcall'
--     AND  a.status = 'sent';
--
-- --------------------------------------------------------------------------
