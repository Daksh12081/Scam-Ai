import pytest

from app.contracts import (
    AgentState,
    KillChainStage,
    ReasonCode,
    apply_monotonic_stages,
    map_evidence_to_stages,
    select_action,
)

S1 = KillChainStage.STAGE_1_IDENTITY_TAKEOVER
S2 = KillChainStage.STAGE_2_SOCIAL_ENGINEERING
S3 = KillChainStage.STAGE_3_FRAUDULENT_TRANSACTION


class TestMapEvidenceToStages:
    def test_no_evidence_lights_nothing(self):
        assert map_evidence_to_stages([]) == set()

    def test_recent_sim_swap_lights_stage_1(self):
        assert map_evidence_to_stages([ReasonCode.RECENT_SIM_SWAP]) == {S1}

    @pytest.mark.parametrize(
        "code",
        [
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
        ],
    )
    def test_tactic_codes_light_stage_2(self, code):
        assert map_evidence_to_stages([code]) == {S2}

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.NEW_BENEFICIARY,
            ReasonCode.DEVICE_UNRECOGNIZED,
            ReasonCode.NUMBER_VERIFICATION_FAILED,
        ],
    )
    def test_transaction_codes_light_stage_3(self, code):
        assert map_evidence_to_stages([code]) == {S3}

    def test_evidence_across_all_three_stages(self):
        evidence = [
            ReasonCode.RECENT_SIM_SWAP,
            ReasonCode.OTP_REQUEST,
            ReasonCode.NEW_BENEFICIARY,
        ]
        assert map_evidence_to_stages(evidence) == {S1, S2, S3}


class TestSelectAction:
    def test_zero_stages_monitor(self):
        assert select_action(set()) == AgentState.MONITOR

    def test_single_stage_1_monitor(self):
        assert select_action({S1}) == AgentState.MONITOR

    def test_stage_2_and_3_hold_transaction(self):
        assert select_action({S2, S3}) == AgentState.HOLD_TRANSACTION

    def test_all_three_stages_hold_transaction(self):
        assert select_action({S1, S2, S3}) == AgentState.HOLD_TRANSACTION

    def test_stage_3_without_stage_2_step_up_verification(self):
        # SCN-10: transaction/identity risk with STAGE_1 corroboration but
        # no manipulation signal -- must NOT escalate to HOLD.
        assert select_action({S1, S3}) == AgentState.STEP_UP_VERIFICATION

    def test_lone_stage_3_step_up_verification(self):
        assert select_action({S3}) == AgentState.STEP_UP_VERIFICATION

    def test_stage_2_without_stage_3_warns_user(self):
        assert select_action({S2}) == AgentState.WARN_USER
        assert select_action({S1, S2}) == AgentState.WARN_USER


class TestMonotonicStages:
    def test_stages_accumulate(self):
        merged = apply_monotonic_stages({S2}, {S1, S2})
        assert merged == {S1, S2}

    def test_illegal_regression_is_rejected(self):
        with pytest.raises(ValueError, match="Illegal stage regression"):
            apply_monotonic_stages({S1, S2}, {S2})
