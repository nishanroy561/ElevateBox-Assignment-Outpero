#!/usr/bin/env python3
"""
sarvam_gate.py -- speed + quality gate for the all-Sarvam voice stack.

Follows the official quickstart shape: https://docs.sarvam.ai/api/getting-started/quickstart
Uses the native `sarvamai` SDK (not OpenAI-compatible mode), same as the docs.

Answers two questions before we write the LiveKit worker:

  1. LLM: which chat model is fast enough for the in-call path, and does
     it return reliable JSON for classify_lead? Streaming TTFT measured.
  2. TTS: which Bulbul model and which speaker, and how fast is first
     audio? Writes .wav samples to disk at 8kHz phone-line quality so the
     voice can be picked BY EAR -- the only way that decision should be
     made.

Usage:
    pip install sarvamai python-dotenv
    python scripts/sarvam_gate.py                 # llm + tts
    python scripts/sarvam_gate.py --only llm
    python scripts/sarvam_gate.py --only tts
    python scripts/sarvam_gate.py --models sarvam-105b,sarvam-105b-conversations,gemma4

Needs SARVAM_API_KEY in ../.env

Notes verified against sarvamai SDK 0.1.30 source (2026-08-25):
  - chat.completions() supports stream=True (Iterator[ChatCompletionChunk])
  - SDK type-hints only "sarvam-105b" but the runtime accepts any string;
    "sarvam-105b-conversations" and "gemma4" work via the same call
  - No response_format={"type":"json_object"} field -- JSON is prompt-enforced
  - text_to_speech.convert() returns audios[0] as base64
  - speech_to_text.transcribe(file=..., model="saaras:v4", mode="transcribe")
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
OUT_DIR = ROOT / "scripts" / "voice_samples"

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ENV_PATH)
except ImportError:
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# --------------------------------------------------------------------------
# prompts -- same shape as the real in-call load
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Priya, calling on behalf of ElevateBox, an e-commerce
website development company in Banjara Hills, Hyderabad. You speak to prospective
customers in Telugu, Hindi or English -- whichever they answer in -- and stay in
that language. English tech nouns (payments, delivery, WhatsApp catalogue) inside
Telugu or Hindi sentences are natural and expected.

Rules that never change:
- 1 to 2 sentences per turn. Voice agents die on long turns.
- Never read a script. Never list features as bullets aloud.
- Discover naturally: what they sell, roughly how many SKUs, whether something
  already exists or starts fresh, timeline to go live, features (payments,
  delivery, WhatsApp catalogue, multi-language), and budget last and softly.
- Objections: price -> anchor on revenue not cost. "I'll think about it" -> ask
  what specifically. "My brother handles this" -> capture the decision-maker.

You are warm, quick, human. Respond to the caller's most recent line only.
"""

USER_TURNS = [
    "Hi, tell me what you do?",
    "How soon can you start? What'll it cost?",
    "Interest undi kaani ippudu budget ledu, next month chudam.",
    "My brother handles this, I just answered the phone.",
    "Just browsing, no products yet.",
    "Ma products lo 200 SKUs unnai, payment gateway kavali, budget one lakh.",
    "call me tomorrow morning",
    "kal subah baat karna, abhi busy hoon",
    "next week sometime, not sure which day",
    "How much does a basic site cost, roughly?",
]

# JSON is prompt-enforced here -- the native SDK has no response_format field.
# The prompt hammers on "ONLY JSON, no markdown, no prose" because that is
# the only lever available.
CLASSIFY_SYSTEM = """You classify sales leads for a voice agent selling
e-commerce website development. Read the transcript and score the lead.

- HOT: asks price or timeline, "how soon can you start", names a real budget,
  wants to see work, agrees to a next step.
- WARM: real need plus a named barrier (budget not now, someone else decides,
  busy season, "send me the details").
- COLD: no product, no budget, no timeline, purely curious.

OUTPUT FORMAT -- STRICT:
Respond with a SINGLE JSON object and NOTHING ELSE. No prose before or after.
No markdown code fences. No explanation. Just the JSON. The object must have
exactly these keys:
{
  "state": "HOT" or "WARM" or "COLD",
  "confidence": a number between 0.0 and 1.0,
  "budget": string or null,
  "products": string or null,
  "timeline": string or null,
  "features": array of strings (empty array is fine),
  "barrier": string or null,
  "evidence_quote": a verbatim short substring from the transcript
}
Begin your response with { and end it with }. Nothing else.
"""

CLASSIFY_TRANSCRIPTS = [
    "USER: Handloom sarees, about 60 designs, no website yet. Need it live in a month. Budget around 80k, is that workable?",
    "USER: I sell spices through Instagram DMs, maybe 30 SKUs. Site would be nice but budget is tight, maybe next month.",
    "USER: Nothing yet, just researching. No timeline, just curious what all this costs.",
    "USER: My brother runs the business, I just picked up his phone. Kids clothing, small shop in Ameerpet.",
    "USER: How soon can you start and what does it cost? Send me the details, I want to compare.",
    "USER: 200 SKUs unnai, payment gateway kavali, WhatsApp catalogue kuda. Budget one lakh, two weeks lo live avvali.",
    "USER: We have a Shopify store but it's slow and delivery integration is broken. Budget flexible if you fix it in three weeks.",
    "AGENT: When do you want to be live? USER: Diwali. AGENT: Budget? USER: See what you can do.",
    "USER: Just wanted to know how this works. Might start selling one day. No timeline.",
    "USER: kal subah call karo, tab decide karenge. 10 baje.",
]

# TTS probe lines: the actual fixed strings the agent will say most often.
TTS_LINES = [
    ("greeting_te", "te-IN",
     "Namaskaram, ElevateBox nunchi Priya matladutunna. Ippudu matladataniki time undaa?"),
    ("whatsapp_te", "te-IN",
     "Nenu ippude mee WhatsApp ki details pampistunna, one second."),
    ("greeting_hi", "hi-IN",
     "Namaste, main ElevateBox se Priya bol rahi hoon. Kya abhi baat kar sakte hain?"),
    ("greeting_en", "en-IN",
     "Hi, this is Priya calling from ElevateBox in Hyderabad. Is this a good time?"),
]

# Female speakers worth auditioning for an Indian outbound sales voice.
# Trimmed to 3 by default because credits are tight (100 total on the account).
# Sweep all 6 with --speakers if you want a wider audition later.
# All valid per sarvamai SDK 0.1.30 speaker enum.
CANDIDATE_SPEAKERS = ["anushka", "vidya", "arya"]

# --------------------------------------------------------------------------

@dataclass
class Sample:
    ttft_ms: float
    total_ms: float
    tokens_est: int
    ok: bool
    err: str = ""


@dataclass
class LLMResult:
    model: str
    samples: list = field(default_factory=list)
    json_ok: int = 0
    json_total: int = 0
    json_errors: list = field(default_factory=list)
    sample_reply: str = ""

    def summary(self) -> dict:
        oks = [s for s in self.samples if s.ok]
        if not oks:
            return {"model": self.model, "fatal": "no successful calls",
                    "errors": [s.err for s in self.samples if s.err][:2]}
        ttfts = sorted(s.ttft_ms for s in oks)
        totals = sorted(s.total_ms for s in oks)

        def pct(xs, p):
            return xs[max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))]

        return {
            "model": self.model,
            "ok": "%d/%d" % (len(oks), len(self.samples)),
            "ttft_p50": round(pct(ttfts, 50), 1),
            "ttft_p95": round(pct(ttfts, 95), 1),
            "ttft_min": round(ttfts[0], 1),
            "ttft_max": round(ttfts[-1], 1),
            "total_p50": round(pct(totals, 50), 1),
            "avg_tokens": round(statistics.mean([s.tokens_est for s in oks]), 1),
            "json_valid": "%d/%d" % (self.json_ok, self.json_total),
            "json_errors": self.json_errors[:2],
            "sample_reply": self.sample_reply[:180],
        }


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def _extract_json(text: str) -> dict:
    """Prompt tells the model to return raw JSON, but some models still add
    fences or prose. Tolerate both without hiding a real failure."""
    text = (text or "").strip()
    if text.startswith("```"):
        # ```json ... ``` or ``` ... ```
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    # if there is leading/trailing prose, grab the outermost {...}
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        text = text[i:j + 1]
    return json.loads(text)


def run_llm(client, model: str, iterations: int, json_iterations: int) -> LLMResult:
    res = LLMResult(model=model)

    for i in range(iterations):
        user = USER_TURNS[i % len(USER_TURNS)]
        start = time.perf_counter()
        ttft = None
        parts = []
        try:
            stream = client.chat.completions(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_tokens=150,
                temperature=0.7,
                stream=True,
            )
            for chunk in stream:
                # ChatCompletionChunk shape mirrors OpenAI's
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta else None
                if content:
                    if ttft is None:
                        ttft = (time.perf_counter() - start) * 1000
                    parts.append(content)
            total = (time.perf_counter() - start) * 1000
            if ttft is None:
                ttft = total
            text = "".join(parts)
            if not res.sample_reply:
                res.sample_reply = text.strip()
            res.samples.append(Sample(ttft, total, _est_tokens(text), True))
            print("  [stream %2d/%d] ttft=%4.0fms  total=%4.0fms" % (i + 1, iterations, ttft, total), flush=True)
        except Exception as e:
            res.samples.append(Sample(0, 0, 0, False, "%s: %s" % (type(e).__name__, str(e)[:180])))
            print("  [stream %2d/%d] FAILED: %s" % (i + 1, iterations, str(e)[:110]), flush=True)

    for i in range(json_iterations):
        res.json_total += 1
        try:
            r = client.chat.completions(
                model=model,
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": CLASSIFY_TRANSCRIPTS[i % len(CLASSIFY_TRANSCRIPTS)]},
                ],
                max_tokens=400,
                temperature=0.1,
                stream=False,
            )
            content = r.choices[0].message.content or ""
            data = _extract_json(content)
            assert data["state"] in ("HOT", "WARM", "COLD"), "bad state: %r" % data.get("state")
            assert isinstance(data.get("features"), list), "features must be array"
            assert isinstance(data.get("evidence_quote"), str) and data["evidence_quote"], "evidence_quote required"
            res.json_ok += 1
            print("  [json   %2d/%d] %s  conf=%s" % (i + 1, json_iterations, data["state"], data.get("confidence")), flush=True)
        except Exception as e:
            res.json_errors.append("%s: %s" % (type(e).__name__, str(e)[:180]))
            print("  [json   %2d/%d] FAILED: %s" % (i + 1, json_iterations, str(e)[:120]), flush=True)

    return res


def run_tts(client, models: list, speakers: list) -> list:
    """Time TTS and write .wav samples so the voice can be chosen by ear."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in models:
        for speaker in speakers:
            for label, lang, text in TTS_LINES:
                start = time.perf_counter()
                try:
                    resp = client.text_to_speech.convert(
                        text=text,
                        language_code=lang,
                        speaker=speaker,
                        model=model,
                        speech_sample_rate=8000,   # phone-line reality, not studio
                        enable_preprocessing=True,
                    )
                    ms = (time.perf_counter() - start) * 1000
                    audios = getattr(resp, "audios", None) or []
                    nbytes = 0
                    if audios:
                        raw = base64.b64decode(audios[0])
                        nbytes = len(raw)
                        fn = OUT_DIR / ("%s_%s_%s.wav" % (model.replace(":", ""), speaker, label))
                        fn.write_bytes(raw)
                    rows.append({"model": model, "speaker": speaker, "line": label,
                                 "ms": round(ms, 1), "bytes": nbytes, "ok": True})
                    print("  %-11s %-9s %-12s %6.0fms  %6d bytes" % (model, speaker, label, ms, nbytes), flush=True)
                except Exception as e:
                    rows.append({"model": model, "speaker": speaker, "line": label,
                                 "ms": 0, "ok": False, "err": str(e)[:200]})
                    print("  %-11s %-9s %-12s FAILED: %s" % (model, speaker, label, str(e)[:80]), flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["llm", "tts", "both"], default="both")
    ap.add_argument("--models",
                    default="sarvam-105b,sarvam-105b-conversations",
                    help="comma-separated chat models to compare "
                         "(SDK type-hints only sarvam-105b; others pass as strings and work at runtime)")
    ap.add_argument("--tts-models", default="bulbul:v2",
                    help="v2 costs half of v3; audition v2 first, only add v3 if v2 is not good enough")
    ap.add_argument("--speakers", default=",".join(CANDIDATE_SPEAKERS))
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--json-iterations", type=int, default=10)
    args = ap.parse_args()

    try:
        from sarvamai import SarvamAI
    except ImportError:
        raise SystemExit("sarvamai not installed. Run: pip install sarvamai python-dotenv")

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise SystemExit("SARVAM_API_KEY missing in .env")
    client = SarvamAI(api_subscription_key=key)

    llm_summaries = []
    if args.only in ("llm", "both"):
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            print("\n=== LLM: %s ===" % model)
            llm_summaries.append(run_llm(client, model, args.iterations, args.json_iterations).summary())

    tts_rows = []
    if args.only in ("tts", "both"):
        print("\n=== TTS: latency + voice samples ===")
        print("  writing .wav files to %s" % OUT_DIR)
        tts_rows = run_tts(client,
                           [m.strip() for m in args.tts_models.split(",") if m.strip()],
                           [s.strip() for s in args.speakers.split(",") if s.strip()])

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    for s in llm_summaries:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        if "fatal" in s:
            print("  VERDICT: FAIL -- %s\n" % s["fatal"])
            continue
        reasons, ok = [], True
        if s["ttft_p50"] > 1000:
            ok = False
            reasons.append("TTFT p50 %sms over the 1000ms working budget" % s["ttft_p50"])
        if s["ttft_p95"] > 1800:
            ok = False
            reasons.append("TTFT p95 %sms over the 1800ms working budget" % s["ttft_p95"])
        j_ok, j_tot = s["json_valid"].split("/")
        if int(j_ok) < int(j_tot) - 1:
            ok = False
            reasons.append("structured output only %s (target 9/10 or better)" % s["json_valid"])
        print("  VERDICT: %s" % ("PASS" if ok else "FAIL"))
        for r in reasons or ["within working budget on latency and structured output"]:
            print("    - %s" % r)
        print()

    if tts_rows:
        okr = [r for r in tts_rows if r.get("ok")]
        if okr:
            print("TTS latency by model/speaker (mean ms across the 4 lines):")
            agg = {}
            for r in okr:
                agg.setdefault((r["model"], r["speaker"]), []).append(r["ms"])
            for (m, sp), v in sorted(agg.items(), key=lambda kv: statistics.mean(kv[1])):
                print("  %-11s %-9s  %6.0f ms" % (m, sp, statistics.mean(v)))
        bad = [r for r in tts_rows if not r.get("ok")]
        if bad:
            print("\n  %d TTS calls failed. First error: %s" % (len(bad), bad[0].get("err")))
        print("\n  >>> LISTEN to the .wav files in %s" % OUT_DIR)
        print("  >>> Rendered at 8kHz to match a phone line; do not judge in a browser at 24kHz.")
        print("  >>> Pick the speaker BY EAR, then set SARVAM_TTS_SPEAKER and SARVAM_TTS_MODEL in .env.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
