"""
Human-readable report for the Block B layer-1 tactic detector (Task 4).

Offline, zero tokens: runs detect_tactics() over every fixture's caller
lines and prints a per-scenario table (expected vs detected tactic codes,
any false-positive/extra fires), the SCN-09 canary result, and the max
per-utterance detection latency.
"""
from __future__ import annotations

import time

from app.contracts import ReasonCode, Speaker
from app.detection import detect_tactics
from tests.detection.scenario_fixtures import Scenario, load_all_scenarios
from tests.detection.test_tactics import KNOWN_UNDEMONSTRATED_EXPECTED_CODES


def _detected_codes(scenario: Scenario) -> set[ReasonCode]:
    codes: set[ReasonCode] = set()
    for line in scenario.caller_lines():
        for event in detect_tactics(line, Speaker.CALLER):
            codes.add(event.tactic_code)
    return codes


def main() -> None:
    scenarios = load_all_scenarios()

    print("\n" + "=" * 116)
    print(f"{'SCENARIO':<10} {'CATEGORY':<12} {'S2':<6} {'EXPECTED CODES':<48} {'RESULT':<10}")
    print("=" * 116)

    max_latency_ms = 0.0
    fp_flags: list[str] = []

    for scenario in scenarios:
        expected = {t.code for t in scenario.expected_tactics}
        detected: set[ReasonCode] = set()
        for line in scenario.caller_lines():
            start = time.perf_counter()
            events = detect_tactics(line, Speaker.CALLER)
            elapsed_ms = (time.perf_counter() - start) * 1000
            max_latency_ms = max(max_latency_ms, elapsed_ms)
            detected.update(e.tactic_code for e in events)

        missing = expected - detected
        extra = detected - expected

        if scenario.stage_2:
            known_gaps = {
                c for c in missing
                if (scenario.scenario_id, c) in KNOWN_UNDEMONSTRATED_EXPECTED_CODES
            }
            real_missing = missing - known_gaps
            if not real_missing and not known_gaps:
                result = "OK"
            elif not real_missing:
                result = f"OK (known gap: {sorted(c.value for c in known_gaps)})"
            else:
                result = f"MISSING {sorted(c.value for c in real_missing)}"
        else:
            result = "OK (0 hits)" if not detected else f"FALSE POSITIVE {sorted(c.value for c in detected)}"
            if detected:
                fp_flags.append(f"{scenario.scenario_id}: fired {sorted(c.value for c in detected)}")

        expected_str = ", ".join(sorted(c.value for c in expected)) or "(none)"
        print(f"{scenario.scenario_id:<10} {scenario.category:<12} {str(scenario.stage_2):<6} "
              f"{expected_str:<48} {result:<10}")
        if extra:
            print(f"{'':<10} {'':<12} {'':<6} EXTRA (allowed, flagged): {sorted(c.value for c in extra)}")

    print("=" * 116)

    scn09 = next(s for s in scenarios if s.scenario_id == "SCN-09")
    scn09_hits = _detected_codes(scn09)
    print(f"\nSCN-09 canary (Legitimate Bank Verification Call): "
          f"{'0 tactic hits -- PASS' if not scn09_hits else f'{len(scn09_hits)} hits -- FAIL {sorted(c.value for c in scn09_hits)}'}")

    print(f"Max per-utterance detection latency: {max_latency_ms:.3f} ms (target <= 50 ms)")

    if fp_flags:
        print("\nFALSE POSITIVES ON LEGITIMATE/AMBIGUOUS SCENARIOS:")
        for flag in fp_flags:
            print(f"  {flag}")
    else:
        print("\nNo false positives on legitimate/ambiguous scenarios (SCN-09, SCN-10).")


if __name__ == "__main__":
    main()
