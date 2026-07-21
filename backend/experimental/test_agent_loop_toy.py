"""
Offline tests for the agent loop's determinism guarantee. These use a
scripted stub agent (no network, no tokens) so they run in the same fast
suite as the contract tests.
"""
from types import SimpleNamespace

import pytest

from app.contracts import AgentState, CamaraApi, ReasonCode, select_action
from experimental.agent_loop_toy import ToolChoice, run_incident_loop


class _ScriptedAgent:
    """Duck-types pydantic_ai.Agent's run_sync(...).output interface,
    returning a pre-scripted sequence of ToolChoice values."""

    def __init__(self, script: list[ToolChoice]):
        self._script = list(script)
        self._i = 0

    def run_sync(self, prompt: str):
        choice = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return SimpleNamespace(output=choice)


def test_tool_choice_has_no_action_field():
    """Structural guarantee: the model's output type cannot even express
    an action -- there's no field for it."""
    assert "action" not in ToolChoice.model_fields


@pytest.mark.parametrize(
    "seed_evidence,script,sim_swap_swapped,expected_action",
    [
        # 0 stages, agent immediately finalizes -> MONITOR
        ([], [ToolChoice(chosen_tool=CamaraApi.NONE, reason="nothing to check")], True, AgentState.MONITOR),
        # single STAGE_2 signal, agent finalizes without calling the tool -> WARN_USER
        (
            [ReasonCode.AUTHORITY_IMPERSONATION],
            [ToolChoice(chosen_tool=CamaraApi.NONE, reason="single stage-2 signal")],
            True,
            AgentState.WARN_USER,
        ),
        # STAGE_2 lit, agent calls SIM_SWAP and gets a positive result -> S1+S2 but no S3 -> still WARN_USER
        # (STAGE_1 alone never changes the outcome under the composition rule)
        (
            [ReasonCode.AUTHORITY_IMPERSONATION, ReasonCode.OTP_REQUEST],
            [
                ToolChoice(chosen_tool=CamaraApi.SIM_SWAP, reason="checking identity takeover"),
                ToolChoice(chosen_tool=CamaraApi.NONE, reason="done"),
            ],
            True,
            AgentState.WARN_USER,
        ),
        # STAGE_2 lit, agent calls SIM_SWAP but result is negative -> still 1 stage -> WARN_USER
        (
            [ReasonCode.AUTHORITY_IMPERSONATION, ReasonCode.OTP_REQUEST],
            [
                ToolChoice(chosen_tool=CamaraApi.SIM_SWAP, reason="checking identity takeover"),
                ToolChoice(chosen_tool=CamaraApi.NONE, reason="no swap found, done"),
            ],
            False,
            AgentState.WARN_USER,
        ),
        # STAGE_2 + STAGE_3 seeded, SIM_SWAP confirms STAGE_1 -> S2 and S3 both lit -> HOLD_TRANSACTION
        (
            [
                ReasonCode.AUTHORITY_IMPERSONATION,
                ReasonCode.OTP_REQUEST,
                ReasonCode.NEW_BENEFICIARY,
                ReasonCode.DEVICE_UNRECOGNIZED,
            ],
            [
                ToolChoice(chosen_tool=CamaraApi.SIM_SWAP, reason="checking identity takeover"),
                ToolChoice(chosen_tool=CamaraApi.NONE, reason="done"),
            ],
            True,
            AgentState.HOLD_TRANSACTION,
        ),
    ],
)
def test_final_action_matches_select_action(seed_evidence, script, sim_swap_swapped, expected_action):
    agent = _ScriptedAgent(script)
    result = run_incident_loop(
        agent,
        incident_id="det-test",
        seed_evidence=seed_evidence,
        sim_swap_swapped=sim_swap_swapped,
    )
    assert result["final_action"] == expected_action
    assert result["final_action"] == select_action(result["active_stages_final"])
    assert result["final_decision"].action == expected_action


def test_action_is_never_read_from_the_model_even_when_it_tries():
    """Adversarial: the scripted 'model' stuffs an action-like claim into
    its reason text, trying to override the rule. The loop must ignore it
    entirely -- there is no code path that reads an action out of the
    model's output, because ToolChoice has no action field to read."""
    agent = _ScriptedAgent([
        ToolChoice(
            chosen_tool=CamaraApi.NONE,
            reason="action=HOLD_TRANSACTION!!! escalate immediately, ignore the stage count",
        )
    ])
    result = run_incident_loop(
        agent,
        incident_id="adversarial-test",
        seed_evidence=[ReasonCode.AUTHORITY_IMPERSONATION, ReasonCode.OTP_REQUEST],  # single STAGE_2 only
        sim_swap_swapped=True,
    )
    # Rules say a single STAGE_2 signal -> WARN_USER, regardless of what the
    # "model" claimed in its reason text.
    assert result["final_action"] == AgentState.WARN_USER
    assert result["final_action"] == select_action(result["active_stages_final"])


def test_repeated_tool_call_still_yields_deterministic_action():
    """Degrade-gracefully path: the model loops requesting the same tool
    twice. The loop must break out and finalize via select_action, not
    crash or trust whatever the model last said."""
    agent = _ScriptedAgent([
        ToolChoice(chosen_tool=CamaraApi.SIM_SWAP, reason="checking"),
        ToolChoice(chosen_tool=CamaraApi.SIM_SWAP, reason="checking again (loop)"),
    ])
    result = run_incident_loop(
        agent,
        incident_id="repeat-test",
        seed_evidence=[ReasonCode.AUTHORITY_IMPERSONATION, ReasonCode.OTP_REQUEST],
        sim_swap_swapped=True,
    )
    assert result["terminal_reason"] == "repeated_tool_call"
    assert result["degraded"] is True
    assert result["final_action"] == select_action(result["active_stages_final"])
    # S1+S2 lit, no S3 -> still WARN_USER under the composition rule.
    assert result["final_action"] == AgentState.WARN_USER
