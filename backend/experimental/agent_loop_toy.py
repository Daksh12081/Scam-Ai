"""
Sentinel agent-loop toy (Block A de-risk, hardened).

ReAct-style loop: the agent reasons over the current IncidentState, may
request a CAMARA tool call, observes the result, and reasons again -- one
decision at a time, not batch-call-then-reason. This script (unlike
app.contracts) is throwaway/experimental.

The LLM never decides the final action. It only chooses whether to call a
tool (ToolChoice). The action is always computed deterministically by
select_action(active_stages) -- the already-tested rule function is the
single source of truth, so the model can never over- or under-escalate.
"""
from __future__ import annotations

import statistics
import time

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

from app.contracts import (
    AgentDecision,
    AgentState,
    CamaraApi,
    IncidentState,
    ReasonCode,
    apply_monotonic_stages,
    map_evidence_to_stages,
    select_action,
)

load_dotenv()

MAX_STEPS = 5
LATENCY_BUDGET_MS = 1500


class ToolChoice(BaseModel):
    """The LLM's entire output. No action field -- the model cannot decide
    the final action, only whether more evidence is worth gathering."""

    chosen_tool: CamaraApi
    reason: str


SYSTEM_PROMPT = """\
Sentinel fraud-detection agent core. You reason over a live phone-call
fraud incident using a three-stage kill-chain model:

- STAGE_1_IDENTITY_TAKEOVER: lit by RECENT_SIM_SWAP.
- STAGE_2_SOCIAL_ENGINEERING: lit by tactic evidence (AUTHORITY_IMPERSONATION,
  URGENCY, OTP_REQUEST, REMOTE_ACCESS_REQUEST, PAYMENT_COACHING, SECRECY_PRESSURE,
  FEAR_INDUCTION, IDENTITY_REQUEST, FINANCIAL_LURE, CAMERA_COERCION).
- STAGE_3_FRAUDULENT_TRANSACTION: lit by NEW_BENEFICIARY, DEVICE_UNRECOGNIZED,
  NUMBER_VERIFICATION_FAILED.

You do NOT decide the final action -- the system computes it deterministically
from active_stages after you finish. Your only job is to decide whether
gathering more evidence is worth it before the system finalizes.

One tool available: SIM_SWAP (CAMARA API) -- checks if the caller's number
recently had a SIM swap. The result may add RECENT_SIM_SWAP evidence,
lighting STAGE_1, or may come back negative (no swap) and add nothing. If
there is no evidence at all, there is nothing to check -- return
chosen_tool=NONE immediately.

You will always be told explicitly, in a separate line below the
IncidentState JSON, which tools have already been called this incident.
That line is the ONLY source of truth for whether SIM_SWAP was already
called -- never infer it from active_stages, current_state, or evidence
being empty/unchanged, since a negative result looks identical to "not
checked yet" in the evidence list. If SIM_SWAP is NOT in that
already-called list and STAGE_1 is not yet lit and there is other evidence
(STAGE_2 or STAGE_3), call it. If SIM_SWAP IS in that list, never call it
again -- trust whatever result it already gave (positive or negative) and
finalize with chosen_tool=NONE.

You get the current IncidentState as JSON (incident_id, evidence,
active_stages, current_state), plus the already-called-tools line. Return
a ToolChoice: chosen_tool (SIM_SWAP to gather more evidence, else NONE)
and reason (short, machine-readable).

--- Example 1: evidence exists, SIM_SWAP not yet called ---
evidence=[AUTHORITY_IMPERSONATION, OTP_REQUEST], active_stages=[STAGE_2_SOCIAL_ENGINEERING]
Tools already called this incident: []
Thought: SIM_SWAP is not in the already-called list, and there's real STAGE_2 evidence -- checking it could confirm or rule out STAGE_1.
Action: ToolChoice(chosen_tool=SIM_SWAP, reason="STAGE_2 lit via AUTHORITY_IMPERSONATION+OTP_REQUEST; checking SIM swap for STAGE_1")

--- Example 2: SIM_SWAP already called and came back positive ---
evidence=[AUTHORITY_IMPERSONATION, OTP_REQUEST, RECENT_SIM_SWAP], active_stages=[STAGE_1_IDENTITY_TAKEOVER, STAGE_2_SOCIAL_ENGINEERING]
Tools already called this incident: ["SIM_SWAP"]
Thought: SIM_SWAP is already in the called list and came back positive (RECENT_SIM_SWAP is present). Never call it again -- finalize.
Action: ToolChoice(chosen_tool=NONE, reason="SIM swap already checked, came back positive; no further evidence to gather")

--- Example 3: no evidence at all ---
evidence=[], active_stages=[]
Tools already called this incident: []
Thought: no evidence at all -- nothing suggests a check would find anything. Don't call the tool.
Action: ToolChoice(chosen_tool=NONE, reason="no evidence yet, nothing to check")

--- Example 4: SIM_SWAP already called and came back negative ---
evidence=[AUTHORITY_IMPERSONATION, OTP_REQUEST], active_stages=[STAGE_2_SOCIAL_ENGINEERING]
Tools already called this incident: ["SIM_SWAP"]
Thought: SIM_SWAP is already in the called list -- it came back negative (no RECENT_SIM_SWAP was added to evidence). Trust that result, never call it again -- finalize.
Action: ToolChoice(chosen_tool=NONE, reason="SIM swap already checked, came back negative; finalizing on existing evidence")
"""


def fake_sim_swap(incident_id: str, swapped: bool) -> dict:
    """Fake CAMARA SIM_SWAP tool. No real API call. Configurable per scenario
    so the harness can exercise both a positive and a negative result."""
    return {"swapped": swapped, "last_swap_hours": 3 if swapped else None}


def build_agent() -> Agent:
    return Agent(
        "groq:llama-3.3-70b-versatile",
        output_type=ToolChoice,
        system_prompt=SYSTEM_PROMPT,
        model_settings={"temperature": 0.0},
        retries=2,
    )


def _stage_list(stages: set) -> list:
    return sorted(stages, key=lambda s: s.value)


def build_user_prompt(state: IncidentState, tools_called: set[CamaraApi]) -> str:
    called_list = sorted(t.value for t in tools_called)
    return (
        "Current incident state (JSON):\n"
        f"{state.model_dump_json(indent=2)}\n\n"
        f"Tools already called this incident: {called_list}\n\n"
        "Decide the next ToolChoice now."
    )


class _RateLimiter:
    """Spaces out LLM calls to stay under the Groq free-tier TPM budget.

    Free-tier llama-3.3-70b-versatile caps at 12000 TPM, and even a terse
    structured-output call costs several hundred tokens for the schema
    alone. Without pacing, a back-to-back burst of calls gets throttled
    server-side and later calls balloon to 7-9s. The wait happens outside
    the timed block, so it never pollutes the per-decision latency
    measurements below.
    """

    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            remaining = self.min_interval_s - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


def run_incident_loop(
    agent,
    incident_id: str,
    seed_evidence: list[ReasonCode],
    sim_swap_swapped: bool = True,
    max_steps: int = MAX_STEPS,
    rate_limiter: "_RateLimiter | None" = None,
) -> dict:
    """Run one incident through the ReAct loop.

    `agent` only needs a `run_sync(prompt) -> object with .output` interface
    (a real pydantic_ai.Agent, or a test double) -- see tests for the
    determinism test, which uses a scripted stub instead of a live model.
    """
    evidence = list(seed_evidence)
    active_stages = map_evidence_to_stages(evidence)
    state = IncidentState(
        incident_id=incident_id,
        evidence=evidence,
        active_stages=_stage_list(active_stages),
        current_state=select_action(active_stages),
    )

    tools_called: set[CamaraApi] = set()
    step_records: list[dict] = []
    schema_valid = True
    terminal_reason = None
    degraded = False
    last_reason = "no reasoning captured"

    for step in range(1, max_steps + 1):
        if rate_limiter is not None:
            rate_limiter.wait()
        start = time.perf_counter()
        try:
            tool_choice: ToolChoice = agent.run_sync(build_user_prompt(state, tools_called)).output
        except Exception as e:  # noqa: BLE001 - guard against any model/validation failure
            latency_ms = (time.perf_counter() - start) * 1000
            step_records.append({
                "step": step, "tool_choice": None, "latency_ms": latency_ms,
                "note": f"EXCEPTION: {type(e).__name__}: {e}",
            })
            schema_valid = False
            terminal_reason = "exception"
            degraded = True
            break

        latency_ms = (time.perf_counter() - start) * 1000
        step_records.append({"step": step, "tool_choice": tool_choice, "latency_ms": latency_ms, "note": ""})
        last_reason = tool_choice.reason

        if tool_choice.chosen_tool == CamaraApi.SIM_SWAP:
            if CamaraApi.SIM_SWAP in tools_called:
                # Known ReAct failure mode: repeated action with no new
                # information. Degrade instead of spinning.
                terminal_reason = "repeated_tool_call"
                degraded = True
                break

            tools_called.add(CamaraApi.SIM_SWAP)
            tool_response = fake_sim_swap(incident_id, swapped=sim_swap_swapped)
            if tool_response["swapped"]:
                evidence = evidence + [ReasonCode.RECENT_SIM_SWAP]
            active_stages = apply_monotonic_stages(active_stages, map_evidence_to_stages(evidence))
            state = IncidentState(
                incident_id=incident_id,
                evidence=evidence,
                active_stages=_stage_list(active_stages),
                current_state=select_action(active_stages),
            )
            continue

        terminal_reason = "agent_decided"
        break
    else:
        terminal_reason = "step_budget_exceeded"
        degraded = True

    # Single source of truth for the action, always -- the model's output
    # (ToolChoice) has no action field, so this can't be bypassed.
    final_action = select_action(active_stages)

    final_decision = AgentDecision(
        action=final_action,
        chosen_tool=CamaraApi.NONE,
        reason=last_reason,
        active_stages=_stage_list(active_stages),
        evidence_considered=evidence,
    )

    return {
        "incident_id": incident_id,
        "steps": step_records,
        "final_decision": final_decision,
        "final_action": final_action,
        "schema_valid": schema_valid,
        "terminal_reason": terminal_reason,
        "degraded": degraded,
        "active_stages_final": active_stages,
        "tools_called": tools_called,
    }


# --- Discrimination harness (Change 2) --------------------------------------

SCENARIOS = [
    {
        "key": "legit",
        "label": "Legit call (true negative)",
        "seed_evidence": [],
        "sim_swap_swapped": False,
        "expect_tool_call": False,
        "expected_action": AgentState.MONITOR,
    },
    {
        "key": "manipulation_only",
        "label": "Manipulation only (WARN)",
        "seed_evidence": [ReasonCode.AUTHORITY_IMPERSONATION, ReasonCode.OTP_REQUEST],
        "sim_swap_swapped": False,
        "expect_tool_call": True,
        "expected_action": AgentState.WARN_USER,
    },
    {
        "key": "technical_risk_no_manipulation",
        "label": "Technical risk, no manip. (SCN-10)",
        "seed_evidence": [ReasonCode.NEW_BENEFICIARY, ReasonCode.DEVICE_UNRECOGNIZED],
        "sim_swap_swapped": True,
        "expect_tool_call": True,
        "expected_action": AgentState.STEP_UP_VERIFICATION,
    },
    {
        "key": "full_fraud",
        "label": "Full fraud (HOLD)",
        "seed_evidence": [
            ReasonCode.AUTHORITY_IMPERSONATION,
            ReasonCode.OTP_REQUEST,
            ReasonCode.NEW_BENEFICIARY,
            ReasonCode.DEVICE_UNRECOGNIZED,
        ],
        "sim_swap_swapped": True,
        "expect_tool_call": True,
        "expected_action": AgentState.HOLD_TRANSACTION,
    },
]


def run_discrimination_harness(
    runs_per_scenario: int = 5, max_steps: int = MAX_STEPS, call_interval_s: float = 6.5
) -> dict[str, list[dict]]:
    agent = build_agent()
    rate_limiter = _RateLimiter(call_interval_s)
    all_results: dict[str, list[dict]] = {}
    for scenario in SCENARIOS:
        scenario_results = []
        for i in range(1, runs_per_scenario + 1):
            result = run_incident_loop(
                agent,
                incident_id=f"{scenario['key']}-{i}",
                seed_evidence=scenario["seed_evidence"],
                sim_swap_swapped=scenario["sim_swap_swapped"],
                max_steps=max_steps,
                rate_limiter=rate_limiter,
            )
            scenario_results.append(result)
        all_results[scenario["key"]] = scenario_results
    return all_results


def _tool_behaviour_correct(scenario: dict, result: dict) -> bool:
    called_sim_swap = CamaraApi.SIM_SWAP in result["tools_called"]
    if scenario["expect_tool_call"]:
        return called_sim_swap and len(result["tools_called"]) == 1
    return not called_sim_swap


def print_discrimination_report(all_results: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 108)
    print(f"{'SCENARIO':<26} {'SCHEMA':<9} {'ACTION_OK':<11} {'TOOL_OK':<9} {'STUCK':<7} {'LAT min/avg/max (ms)':<24}")
    print("=" * 108)

    critical_flags: list[str] = []

    for scenario in SCENARIOS:
        key = scenario["key"]
        results = all_results[key]
        n = len(results)

        schema_ok = sum(1 for r in results if r["schema_valid"])
        action_ok = sum(1 for r in results if r["final_action"] == scenario["expected_action"])
        tool_ok = sum(1 for r in results if _tool_behaviour_correct(scenario, r))
        stuck = sum(1 for r in results if r["terminal_reason"] in {"step_budget_exceeded", "repeated_tool_call", "exception"})

        latencies = [s["latency_ms"] for r in results for s in r["steps"]]
        if latencies:
            lat_str = f"{min(latencies):.0f}/{statistics.mean(latencies):.0f}/{max(latencies):.0f}"
        else:
            lat_str = "n/a"

        print(
            f"{scenario['label']:<26} {f'{schema_ok}/{n}':<9} {f'{action_ok}/{n}':<11} "
            f"{f'{tool_ok}/{n}':<9} {f'{stuck}/{n}':<7} {lat_str:<24}"
        )

        # --- critical discrimination checks -------------------------------
        if key == "legit":
            bad_runs = [
                r for r in results
                if len(r["tools_called"]) > 0 or r["final_action"] != AgentState.MONITOR
            ]
            if bad_runs:
                critical_flags.append(
                    f"*** FALSE POSITIVE: Legit scenario made a tool call or escalated in "
                    f"{len(bad_runs)}/{n} runs (expected 0 tool calls, always MONITOR). "
                    f"Details: {[(r['incident_id'], len(r['tools_called']), r['final_action'].value) for r in bad_runs]}"
                )

        if key == "manipulation_only":
            invented_swap = [r for r in results if ReasonCode.RECENT_SIM_SWAP in r["final_decision"].evidence_considered]
            wrong_action = [r for r in results if r["final_action"] != AgentState.WARN_USER]
            if invented_swap:
                critical_flags.append(
                    f"*** Manipulation-only scenario invented a SIM swap that never happened in "
                    f"{len(invented_swap)}/{n} runs (tool returned swapped=False)."
                )
            if wrong_action:
                critical_flags.append(
                    f"*** Manipulation-only scenario did not land on WARN_USER in {len(wrong_action)}/{n} runs: "
                    f"{[(r['incident_id'], r['final_action'].value) for r in wrong_action]}"
                )

        if key == "technical_risk_no_manipulation":
            # SCN-10: proof that STAGE_2 is required for a HOLD. Technical/
            # identity risk (S3) plus a positive SIM_SWAP (S1) must NOT be
            # enough to hold the transaction on its own.
            held = [r for r in results if r["final_action"] == AgentState.HOLD_TRANSACTION]
            wrong_action = [r for r in results if r["final_action"] != AgentState.STEP_UP_VERIFICATION]
            if held:
                critical_flags.append(
                    f"*** SCN-10 REGRESSION: technical-risk-no-manipulation scenario escalated to "
                    f"HOLD_TRANSACTION in {len(held)}/{n} runs -- STAGE_2 must be required for a hold. "
                    f"Details: {[(r['incident_id'], r['final_action'].value) for r in held]}"
                )
            elif wrong_action:
                critical_flags.append(
                    f"*** SCN-10 scenario did not land on STEP_UP_VERIFICATION in {len(wrong_action)}/{n} runs: "
                    f"{[(r['incident_id'], r['final_action'].value) for r in wrong_action]}"
                )

    print("=" * 108)

    if critical_flags:
        print("\nCRITICAL DISCRIMINATION FAILURES:")
        for flag in critical_flags:
            print(f"  {flag}")
    else:
        print("\nNo critical discrimination failures: Legit made 0 tool calls / 0 escalations; "
              "SCN-10 (technical risk, no manipulation) correctly landed on STEP_UP_VERIFICATION, "
              "not HOLD -- confirming STAGE_2 is required for a hold.")


def main() -> None:
    all_results = run_discrimination_harness(runs_per_scenario=5, max_steps=MAX_STEPS)
    print_discrimination_report(all_results)


if __name__ == "__main__":
    main()
