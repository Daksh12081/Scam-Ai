"""
Block A tooling verification for Sentinel.

Runs four independent checks against the free-tier Groq + Gemini stack and
prints a pass/fail summary table with observed latency for each. This script
is throwaway de-risking, not production code.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
AUDIO_PATH = Path(__file__).parent / "sample_audio.wav"

results: list[dict] = []


def record(name: str, passed: bool, latency_ms: float, detail: str) -> None:
    results.append({"name": name, "passed": passed, "latency_ms": latency_ms, "detail": detail})


def check_groq_chat() -> None:
    name = "Groq Llama 3.3 70B (chat)"
    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)
    start = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        text = (resp.choices[0].message.content or "").strip()
        if text:
            record(name, True, latency_ms, f"response: {text[:60]!r}")
        else:
            record(name, False, latency_ms, "empty response")
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        record(name, False, latency_ms, f"{type(e).__name__}: {e}")


def check_groq_whisper() -> None:
    name = "Groq Whisper large-v3 (transcription)"
    from groq import Groq

    if not AUDIO_PATH.exists():
        record(name, False, 0.0, f"missing sample audio at {AUDIO_PATH}")
        return

    client = Groq(api_key=GROQ_API_KEY)
    start = time.perf_counter()
    try:
        with open(AUDIO_PATH, "rb") as f:
            resp = client.audio.transcriptions.create(
                file=(AUDIO_PATH.name, f.read()),
                model="whisper-large-v3",
            )
        latency_ms = (time.perf_counter() - start) * 1000
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            record(name, True, latency_ms, f"transcript: {text[:60]!r}")
        else:
            record(name, False, latency_ms, "empty transcript")
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        record(name, False, latency_ms, f"{type(e).__name__}: {e}")


def _pick_current_flash_model(client) -> str | None:
    """List models via the SDK and pick the current free-tier Flash model
    rather than hardcoding a model ID that may have rotated."""
    exclude_kw = (
        "preview", "lite", "image", "tts", "live", "audio",
        "robotics", "computer-use", "antigravity", "deep-research", "omni",
    )
    candidates = []
    for m in client.models.list():
        name = m.name  # e.g. "models/gemini-2.5-flash"
        actions = getattr(m, "supported_actions", None) or []
        if "flash" not in name.lower() or "generateContent" not in actions:
            continue
        if any(kw in name.lower() for kw in exclude_kw):
            continue
        candidates.append(name)

    if not candidates:
        return None
    # "gemini-flash-latest" is Google's own alias for the current default
    # Flash model -- prefer it when present.
    latest_alias = [c for c in candidates if c.endswith("gemini-flash-latest")]
    if latest_alias:
        return latest_alias[0]

    def version_key(name: str):
        match = re.search(r"gemini-(\d+)(?:\.(\d+))?-flash", name)
        if not match:
            return (0, 0)
        major, minor = match.groups()
        return (int(major), int(minor or 0))

    return sorted(candidates, key=version_key, reverse=True)[0]


def check_gemini_flash() -> None:
    name = "Gemini Flash (fallback)"
    from google import genai
    from google.genai import errors as genai_errors

    start = time.perf_counter()
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        model_id = _pick_current_flash_model(client)
        if model_id is None:
            latency_ms = (time.perf_counter() - start) * 1000
            record(name, False, latency_ms, "no Flash model found in models.list()")
            return

        resp = client.models.generate_content(
            model=model_id,
            contents="Reply with the single word: OK",
        )
        latency_ms = (time.perf_counter() - start) * 1000
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            record(name, True, latency_ms, f"model={model_id}, response: {text[:60]!r}")
        else:
            record(name, False, latency_ms, f"model={model_id}, empty response")
    except genai_errors.ClientError as e:
        latency_ms = (time.perf_counter() - start) * 1000
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "billing" in msg.lower():
            record(name, False, latency_ms, f"BILLING/QUOTA ERROR: {msg[:200]}")
        else:
            record(name, False, latency_ms, f"{type(e).__name__}: {msg[:200]}")
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "billing" in msg.lower():
            record(name, False, latency_ms, f"BILLING/QUOTA ERROR: {msg[:200]}")
        else:
            record(name, False, latency_ms, f"{type(e).__name__}: {msg[:200]}")


class _TinyResult(BaseModel):
    fraud_related: bool
    one_word_summary: str


def check_pydantic_ai_groq() -> None:
    name = "Pydantic AI + Groq (typed call)"
    from pydantic_ai import Agent

    start = time.perf_counter()
    try:
        agent = Agent(
            "groq:llama-3.3-70b-versatile",
            output_type=_TinyResult,
            system_prompt=(
                "You classify a single sentence. Return fraud_related=true if the "
                "sentence describes a scam/fraud attempt, else false. Also return a "
                "one_word_summary of the sentence."
            ),
        )
        result = agent.run_sync(
            "The caller claimed to be from the bank's fraud department and asked for the OTP."
        )
        latency_ms = (time.perf_counter() - start) * 1000
        output = result.output
        if isinstance(output, _TinyResult):
            record(
                name, True, latency_ms,
                f"parsed: fraud_related={output.fraud_related}, "
                f"summary={output.one_word_summary!r}",
            )
        else:
            record(name, False, latency_ms, f"output did not validate: {output!r}")
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        record(name, False, latency_ms, f"{type(e).__name__}: {e}")


def main() -> None:
    if not GROQ_API_KEY:
        print("WARNING: GROQ_API_KEY not set in .env")
    if not GEMINI_API_KEY:
        print("WARNING: GEMINI_API_KEY not set in .env")

    checks = [check_groq_chat, check_groq_whisper, check_gemini_flash, check_pydantic_ai_groq]
    for check in checks:
        print(f"Running: {check.__name__} ...")
        check()

    print("\n" + "=" * 88)
    print(f"{'CHECK':<38} {'STATUS':<8} {'LATENCY':<12} DETAIL")
    print("=" * 88)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        latency = f"{r['latency_ms']:.0f} ms"
        print(f"{r['name']:<38} {status:<8} {latency:<12} {r['detail']}")
    print("=" * 88)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n{passed}/{total} checks passed on free tier.")
    if passed < total:
        print("Failing checks:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['name']}: {r['detail']}")


if __name__ == "__main__":
    main()
