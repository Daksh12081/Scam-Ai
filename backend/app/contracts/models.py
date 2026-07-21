"""
Sentinel shared data contract (Pydantic models / enums).

Mirrors the team's CONTRACT.md (Agent states, kill-chain stages, CAMARA
APIs, reason codes, evidence->stage mapping, action-selection rule). This
is the canonical, single source of truth for the shared models -- other
backend modules import from `app.contracts`, not from this file directly.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    MONITOR = "MONITOR"
    WARN_USER = "WARN_USER"
    STEP_UP_VERIFICATION = "STEP_UP_VERIFICATION"
    HOLD_TRANSACTION = "HOLD_TRANSACTION"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"


class KillChainStage(str, Enum):
    STAGE_1_IDENTITY_TAKEOVER = "STAGE_1_IDENTITY_TAKEOVER"
    STAGE_2_SOCIAL_ENGINEERING = "STAGE_2_SOCIAL_ENGINEERING"
    STAGE_3_FRAUDULENT_TRANSACTION = "STAGE_3_FRAUDULENT_TRANSACTION"


class CamaraApi(str, Enum):
    SIM_SWAP = "SIM_SWAP"
    NUMBER_VERIFICATION = "NUMBER_VERIFICATION"
    DEVICE_STATUS = "DEVICE_STATUS"
    NONE = "NONE"


class Speaker(str, Enum):
    CALLER = "CALLER"
    USER = "USER"
    UNKNOWN = "UNKNOWN"


class AuthorityImpersonationSubtype(str, Enum):
    BANK = "BANK"
    POLICE = "POLICE"
    GOVERNMENT = "GOVERNMENT"
    TECH_SUPPORT = "TECH_SUPPORT"
    DELIVERY = "DELIVERY"
    INVESTMENT_FIRM = "INVESTMENT_FIRM"


class ReasonCode(str, Enum):
    OTP_REQUEST = "OTP_REQUEST"
    AUTHORITY_IMPERSONATION = "AUTHORITY_IMPERSONATION"
    URGENCY = "URGENCY"
    REMOTE_ACCESS_REQUEST = "REMOTE_ACCESS_REQUEST"
    PAYMENT_COACHING = "PAYMENT_COACHING"
    SECRECY_PRESSURE = "SECRECY_PRESSURE"
    FEAR_INDUCTION = "FEAR_INDUCTION"
    IDENTITY_REQUEST = "IDENTITY_REQUEST"
    FINANCIAL_LURE = "FINANCIAL_LURE"
    CAMERA_COERCION = "CAMERA_COERCION"
    RECENT_SIM_SWAP = "RECENT_SIM_SWAP"
    NUMBER_VERIFICATION_FAILED = "NUMBER_VERIFICATION_FAILED"
    DEVICE_UNRECOGNIZED = "DEVICE_UNRECOGNIZED"
    NEW_BENEFICIARY = "NEW_BENEFICIARY"


class IncidentState(BaseModel):
    incident_id: str
    active_stages: list[KillChainStage] = Field(default_factory=list)
    evidence: list[ReasonCode] = Field(default_factory=list)
    current_state: AgentState = AgentState.MONITOR


class AgentDecision(BaseModel):
    """Schema the LLM must output every reasoning step."""

    action: AgentState
    chosen_tool: CamaraApi
    reason: str
    active_stages: list[KillChainStage] = Field(default_factory=list)
    evidence_considered: list[ReasonCode] = Field(default_factory=list)


# --- Evidence -> stage mapping (CONTRACT.md section 4) ---------------------

_STAGE_1_CODES: frozenset[ReasonCode] = frozenset({ReasonCode.RECENT_SIM_SWAP})

_STAGE_2_CODES: frozenset[ReasonCode] = frozenset({
    ReasonCode.AUTHORITY_IMPERSONATION,
    ReasonCode.URGENCY,
    ReasonCode.OTP_REQUEST,
    ReasonCode.REMOTE_ACCESS_REQUEST,
    ReasonCode.PAYMENT_COACHING,
    ReasonCode.SECRECY_PRESSURE,
    ReasonCode.FEAR_INDUCTION,
    ReasonCode.IDENTITY_REQUEST,
    ReasonCode.FINANCIAL_LURE,
    ReasonCode.CAMERA_COERCION,
})

_STAGE_3_CODES: frozenset[ReasonCode] = frozenset({
    ReasonCode.NEW_BENEFICIARY,
    ReasonCode.DEVICE_UNRECOGNIZED,
    ReasonCode.NUMBER_VERIFICATION_FAILED,
})


def map_evidence_to_stages(evidence: list[ReasonCode]) -> set[KillChainStage]:
    """Pure function: which kill-chain stages does this evidence light?"""
    stages: set[KillChainStage] = set()
    for code in evidence:
        if code in _STAGE_1_CODES:
            stages.add(KillChainStage.STAGE_1_IDENTITY_TAKEOVER)
        elif code in _STAGE_2_CODES:
            stages.add(KillChainStage.STAGE_2_SOCIAL_ENGINEERING)
        elif code in _STAGE_3_CODES:
            stages.add(KillChainStage.STAGE_3_FRAUDULENT_TRANSACTION)
    return stages


# --- Action-selection rule (CONTRACT.md section 5) --------------------------

def select_action(active_stages: set[KillChainStage]) -> AgentState:
    """Pure function: which action follows from the currently lit stages?

    Composition-based on STAGE_2 (manipulation) and STAGE_3 (transaction/
    identity risk) -- STAGE_1 alone never changes the outcome, it's only
    ever corroborating evidence:

    - STAGE_2 and STAGE_3 both lit -> HOLD_TRANSACTION (ESCALATE_TO_ANALYST
      accompanies it at the orchestration layer, not returned here since
      AgentState is a single value).
    - STAGE_3 lit without STAGE_2 -> STEP_UP_VERIFICATION (transaction/
      identity risk with no manipulation signal -- SCN-10).
    - STAGE_2 lit without STAGE_3 -> WARN_USER (manipulation in progress
      before a transaction; intervene before hard signals converge, per
      CONTRACT.md D-3).
    - otherwise (no stages, or a lone STAGE_1 signal) -> MONITOR.
    """
    s2 = KillChainStage.STAGE_2_SOCIAL_ENGINEERING in active_stages
    s3 = KillChainStage.STAGE_3_FRAUDULENT_TRANSACTION in active_stages
    if s2 and s3:
        return AgentState.HOLD_TRANSACTION
    if s3:
        return AgentState.STEP_UP_VERIFICATION
    if s2:
        return AgentState.WARN_USER
    return AgentState.MONITOR


def apply_monotonic_stages(
    previous_stages: set[KillChainStage], new_stages: set[KillChainStage]
) -> set[KillChainStage]:
    """Merge newly computed stages into the previous set, enforcing that a
    lit stage never un-lights within a window (CONTRACT.md section 5).

    Raises ValueError if `new_stages` would drop a stage that was
    previously lit -- that is an illegal regression.
    """
    dropped = previous_stages - new_stages
    if dropped:
        dropped_names = sorted(stage.value for stage in dropped)
        raise ValueError(f"Illegal stage regression: {dropped_names} would un-light")
    return previous_stages | new_stages
