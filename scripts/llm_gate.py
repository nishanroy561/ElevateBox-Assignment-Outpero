#!/usr/bin/env python3
"""
llm_gate.py -- §3.5 gate for the ElevateBox voice agent.

Measures whether Gemini Flash (and optionally Groq) meets the latency and
structured-output bar the voice loop needs. Runs before any agent code so we
don't build around a provider we haven't verified.

Two things it tells you:
  1. Does your API key actually authorise? (matters because Anthropic's card
     kept failing -- if Gemini's key fails too, the problem is card-side and
     the whole plan needs re-thinking today, not on day 6.)
  2. Is the model fast and structured enough for the in-call path?

Reads GEMINI_API_KEY (and optionally GROQ_API_KEY) from ../.env.

Usage:
    pip install google-genai python-dotenv
    # optional: pip install groq
    python scripts/llm_gate.py
    python scripts/llm_gate.py --provider gemini
    python scripts/llm_gate.py --provider groq
    python scripts/llm_gate.py --iterations 30 --json-iterations 15

Thresholds (from plan_updated.md §3.5):
    TTFT p50 <= 500 ms       -> pass
    TTFT p95 <= 900 ms       -> pass
    classify_lead JSON valid 10/10 -> pass
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# --- .env loading ------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ENV_PATH)
except ImportError:
    # Cheap fallback so the script runs even without python-dotenv installed.
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# --- prompts (kept representative of the real in-call load) ------------------

SYSTEM_PROMPT = """You are Priya, calling on behalf of ElevateBox, an e-commerce
website development company based in Banjara Hills, Hyderabad. You speak to
prospective customers in Telugu, Hindi or English -- whichever they answer in --
and stay in that language for the rest of the call. English tech nouns
(payments, delivery, WhatsApp catalogue) inside Telugu or Hindi sentences are
natural and expected.

Rules that never change:
- 1 to 2 sentences per turn. Voice agents die on long turns.
- Never read a script. Never list features as bullets aloud.
- Discover, in a natural weave: what they sell, roughly how many SKUs, whether
  something already exists or starts fresh, timeline to go live, features
  (payments, delivery, WhatsApp catalogue, multi-language), and budget last
  and softly ("most people at your stage land between 40k and 1.5L -- where
  are you thinking?").
- Objections: price -> anchor on revenue not cost. "I'll think about it" ->
  ask what specifically. "My brother handles this" -> capture the
  decision-maker and offer to send details.
- If the caller shows high buying intent (asks price or timeline, names a
  budget, wants to see work, agrees to a next step) you will send them a
  WhatsApp with details in the next moment. Announce it in one short line:
  "Nenu ippude mee WhatsApp ki details pampistunna -- one second."

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

CLASSIFY_SYSTEM = """You are the classifier for an outbound sales voice agent
selling e-commerce website development. Read the running transcript and score
the lead.

Rules:
- HOT: asks price or timeline, "how soon can you start", names a real budget,
  wants to see work, agrees to a next step.
- WARM: real need plus a named barrier (budget not now, someone else decides,
  busy season, "send me the details").
- COLD: no product, no budget, no timeline, purely curious.

Return ONLY a JSON object matching this schema, no prose, no code fences:
{
  "state": "HOT" | "WARM" | "COLD",
  "confidence": <float 0..1>,
  "budget": <string or null>,
  "products": <string or null>,
  "timeline": <string or null>,
  "features": [<string>, ...],
  "barrier": <string or null>,
  "evidence_quote": <string, verbatim from the transcript>
}
"""

CLASSIFY_TRANSCRIPTS = [
    "USER: Hi. AGENT: Namaskaram, Priya from ElevateBox. Is this a good time? USER: Yeah go ahead. AGENT: What do you sell? USER: Handloom sarees, about 60 designs, no website yet. Need it live in a month. Budget around 80k, is that workable?",
    "USER: I sell spices online through Instagram DMs, maybe 30 SKUs. Site would be nice but budget is tight right now, maybe next month. AGENT: What kind of budget are you thinking when the time comes? USER: One lakh maybe.",
    "AGENT: What do you sell? USER: Nothing yet, just researching. AGENT: Any idea when you'd want to launch? USER: No timeline, just curious what all this costs.",
    "USER: My brother runs the business, I just picked up his phone. He's out till evening. AGENT: What does the business do? USER: Kids clothing, small shop in Ameerpet, some Instagram sales.",
    "USER: How soon can you start and what does it cost? AGENT: We can start next week; a full site with payments and delivery lands around 1.2 lakh. USER: Send me the details, I want to compare.",
    "USER: 200 SKUs unnai, payment gateway kavali, WhatsApp catalogue kuda. Budget one lakh, timeline two weeks lo live avvali. AGENT: Understood.",
    "USER: We already have a Shopify store but it's slow and delivery integration is broken. Looking to rebuild. Budget flexible if you can fix it in three weeks.",
    "AGENT: Anything running today or starting fresh? USER: Fresh. AGENT: When do you want to be live? USER: Diwali. AGENT: Budget? USER: See what you can do.",
    "USER: Just wanted to know how this works. AGENT: Sure -- do you sell anything currently? USER: No, might start one day. AGENT: Any timeline? USER: Nah.",
    "USER: kal subah call karo, tab decide karenge. AGENT: Set. What time works? USER: 10 baje.",
]

# --- provider interface ------------------------------------------------------

@dataclass
class Sample:
    ttft_ms: float
    total_ms: float
    output_tokens_est: int
    ok: bool
    err: str = ""


@dataclass
class GateResult:
    provider: str
    model: str
    stream_samples: list[Sample] = field(default_factory=list)
    json_ok: int = 0
    json_total: int = 0
    json_errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        oks = [s for s in self.stream_samples if s.ok]
        if not oks:
            return {"provider": self.provider, "model": self.model, "fatal": "no successful streaming calls"}
        ttfts = sorted(s.ttft_ms for s in oks)
        totals = sorted(s.total_ms for s in oks)
        tokens = [s.output_tokens_est for s in oks]
        def pct(xs, p): return xs[max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))]
        return {
            "provider": self.provider,
            "model": self.model,
            "streaming_calls": len(self.stream_samples),
            "streaming_ok": len(oks),
            "ttft_ms_p50": round(pct(ttfts, 50), 1),
            "ttft_ms_p95": round(pct(ttfts, 95), 1),
            "ttft_ms_min": round(ttfts[0], 1),
            "ttft_ms_max": round(ttfts[-1], 1),
            "total_ms_p50": round(pct(totals, 50), 1),
            "total_ms_p95": round(pct(totals, 95), 1),
            "avg_output_tokens_est": round(statistics.mean(tokens), 1),
            "json_valid": f"{self.json_ok}/{self.json_total}",
            "json_errors": self.json_errors[:3],
        }


def _est_tokens(s: str) -> int:
    # crude 4 chars/token approximation -- fine for a smell test
    return max(1, len(s) // 4)


# ---- Gemini ---------------------------------------------------------------

def run_gemini(iterations: int, json_iterations: int, model_name: str) -> GateResult:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY missing in .env")

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise SystemExit(
            "google-genai not installed. Run: pip install google-genai python-dotenv"
        )

    client = genai.Client(api_key=api_key)
    result = GateResult(provider="gemini", model=model_name)

    # --- streaming latency samples ---
    for i in range(iterations):
        user = USER_TURNS[i % len(USER_TURNS)]
        contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=user)]),
        ]
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=150,
            temperature=0.7,
        )
        start = time.perf_counter()
        ttft = None
        text_parts: list[str] = []
        try:
            for chunk in client.models.generate_content_stream(
                model=model_name, contents=contents, config=config,
            ):
                if ttft is None and getattr(chunk, "text", None):
                    ttft = (time.perf_counter() - start) * 1000
                if getattr(chunk, "text", None):
                    text_parts.append(chunk.text)
            total = (time.perf_counter() - start) * 1000
            if ttft is None:
                ttft = total  # no text streamed
            result.stream_samples.append(Sample(
                ttft_ms=ttft, total_ms=total,
                output_tokens_est=_est_tokens("".join(text_parts)),
                ok=True,
            ))
        except Exception as e:
            result.stream_samples.append(Sample(
                ttft_ms=0, total_ms=0, output_tokens_est=0, ok=False, err=str(e),
            ))
        print(f"  [stream {i+1}/{iterations}] ttft={ttft:.0f}ms" if ttft else f"  [stream {i+1}/{iterations}] FAILED", flush=True)

    # --- structured-output samples ---
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "state": types.Schema(type=types.Type.STRING, enum=["HOT", "WARM", "COLD"]),
            "confidence": types.Schema(type=types.Type.NUMBER),
            "budget": types.Schema(type=types.Type.STRING, nullable=True),
            "products": types.Schema(type=types.Type.STRING, nullable=True),
            "timeline": types.Schema(type=types.Type.STRING, nullable=True),
            "features": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
            "barrier": types.Schema(type=types.Type.STRING, nullable=True),
            "evidence_quote": types.Schema(type=types.Type.STRING),
        },
        required=["state", "confidence", "features", "evidence_quote"],
    )
    for i in range(json_iterations):
        transcript = CLASSIFY_TRANSCRIPTS[i % len(CLASSIFY_TRANSCRIPTS)]
        result.json_total += 1
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=transcript)])],
                config=types.GenerateContentConfig(
                    system_instruction=CLASSIFY_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=400,
                    temperature=0.2,
                ),
            )
            text = resp.text or ""
            data = json.loads(text)
            assert data["state"] in ("HOT", "WARM", "COLD")
            assert isinstance(data.get("features"), list)
            assert isinstance(data.get("evidence_quote"), str) and data["evidence_quote"]
            result.json_ok += 1
            print(f"  [json  {i+1}/{json_iterations}] {data['state']} ({data.get('confidence')})", flush=True)
        except Exception as e:
            result.json_errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            print(f"  [json  {i+1}/{json_iterations}] FAILED: {e}", flush=True)

    return result


# ---- Groq -----------------------------------------------------------------

def run_groq(iterations: int, json_iterations: int, model_name: str) -> GateResult:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY missing in .env")
    try:
        from groq import Groq
    except ImportError:
        raise SystemExit("groq not installed. Run: pip install groq")

    client = Groq(api_key=api_key)
    result = GateResult(provider="groq", model=model_name)

    for i in range(iterations):
        user = USER_TURNS[i % len(USER_TURNS)]
        start = time.perf_counter()
        ttft = None
        text_parts: list[str] = []
        try:
            stream = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_tokens=150,
                temperature=0.7,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    if ttft is None:
                        ttft = (time.perf_counter() - start) * 1000
                    text_parts.append(delta)
            total = (time.perf_counter() - start) * 1000
            if ttft is None:
                ttft = total
            result.stream_samples.append(Sample(
                ttft_ms=ttft, total_ms=total,
                output_tokens_est=_est_tokens("".join(text_parts)),
                ok=True,
            ))
        except Exception as e:
            result.stream_samples.append(Sample(
                ttft_ms=0, total_ms=0, output_tokens_est=0, ok=False, err=str(e),
            ))
        print(f"  [stream {i+1}/{iterations}] ttft={ttft:.0f}ms" if ttft else f"  [stream {i+1}/{iterations}] FAILED", flush=True)

    for i in range(json_iterations):
        transcript = CLASSIFY_TRANSCRIPTS[i % len(CLASSIFY_TRANSCRIPTS)]
        result.json_total += 1
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": transcript},
                ],
                response_format={"type": "json_object"},
                max_tokens=400,
                temperature=0.2,
            )
            text = resp.choices[0].message.content or ""
            data = json.loads(text)
            assert data["state"] in ("HOT", "WARM", "COLD")
            assert isinstance(data.get("features"), list)
            assert isinstance(data.get("evidence_quote"), str) and data["evidence_quote"]
            result.json_ok += 1
            print(f"  [json  {i+1}/{json_iterations}] {data['state']}", flush=True)
        except Exception as e:
            result.json_errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            print(f"  [json  {i+1}/{json_iterations}] FAILED: {e}", flush=True)

    return result


# --- verdict -----------------------------------------------------------------

def verdict(summary: dict) -> tuple[str, list[str]]:
    if "fatal" in summary:
        return "FAIL", [summary["fatal"]]
    reasons = []
    ok = True
    if summary["ttft_ms_p50"] > 500:
        ok = False
        reasons.append(f"TTFT p50 {summary['ttft_ms_p50']}ms exceeds 500ms budget")
    if summary["ttft_ms_p95"] > 900:
        ok = False
        reasons.append(f"TTFT p95 {summary['ttft_ms_p95']}ms exceeds 900ms budget")
    ok_j, tot_j = summary["json_valid"].split("/")
    if int(ok_j) < int(tot_j):
        ok = False
        reasons.append(f"structured output: only {summary['json_valid']} valid")
    if ok:
        reasons.append("meets in-call latency budget and structured-output requirement")
        return "PASS", reasons
    return "FAIL", reasons


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", choices=["gemini", "groq", "both"], default="gemini")
    ap.add_argument("--gemini-model", default="gemini-3.5-flash")
    ap.add_argument("--groq-model", default="openai/gpt-oss-120b")
    ap.add_argument("--iterations", type=int, default=20, help="streaming samples")
    ap.add_argument("--json-iterations", type=int, default=10, help="classify_lead calls")
    args = ap.parse_args()

    providers: list[tuple[str, Callable[[], GateResult]]] = []
    if args.provider in ("gemini", "both"):
        providers.append(("gemini", lambda: run_gemini(args.iterations, args.json_iterations, args.gemini_model)))
    if args.provider in ("groq", "both"):
        providers.append(("groq", lambda: run_groq(args.iterations, args.json_iterations, args.groq_model)))

    results: list[dict] = []
    for name, fn in providers:
        print(f"\n=== {name} ===")
        try:
            res = fn()
        except SystemExit as e:
            print(f"  ABORT: {e}")
            results.append({"provider": name, "fatal": str(e)})
            continue
        summary = res.summary()
        results.append(summary)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for s in results:
        print(json.dumps(s, indent=2))
        v, reasons = verdict(s) if "fatal" not in s else ("FAIL", [s["fatal"]])
        print(f"  VERDICT: {v}")
        for r in reasons:
            print(f"    - {r}")
        print()

    # exit code = number of failures, so CI-ish callers can key off it
    fails = sum(1 for s in results if verdict(s)[0] == "FAIL")
    return fails


if __name__ == "__main__":
    sys.exit(main())
