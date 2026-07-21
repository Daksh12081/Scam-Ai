# CONTRACT.md — Sentinel Shared Data Contract

**Status:** v1.0.0-candidate — pending Daksh's freeze verification (TS types + Pydantic models import cleanly on all three sides)
**Schema version:** `1.0.0`
**Owners:** Adithya (agent-decision, tactic-event, CAMARA-call schemas, tactic taxonomy); Daksh (transport/state/DB schemas, freeze chair).

> Single seam every component builds against: STT, layer-1 detector, agent core, CAMARA client, state machine, both WebSocket endpoints, both frontends.

## Changelog since first draft
1. `event_id` (globally unique) added to every event envelope.
2. `correlation_id` added (spans the whole decision→CAMARA→bank-action chain); `call_id` retained on the CAMARA schema.
3. `decision_version` added to AGENT_DECISION (AI policy/rules version).
4. New §7 **WebSocket Event Types** (per socket).
5. Tactic-code set expanded 6 → **10** (added FEAR_INDUCTION, IDENTITY_REQUEST, FINANCIAL_LURE, CAMERA_COERCION) + optional `subtype` on AUTHORITY_IMPERSONATION.
6. **Action rule rewritten** from stage-count to stage-composition (see §5 and Decision Log D10). HOLD now requires Stage 2 ∧ Stage 3; Stage 1 corroborates.
7. `GUIDED_RECOVERY` confirmed **not** an AgentState; new `IncidentLifecycleStatus` enum added.
8. Incident reports carry no raw transcript text; money is integer minor units (confirmed).

---

## 0. Change-control rule
Any change after freeze requires Daksh's approval AND one PR updating this file + the TS types + the Pydantic models together. Freeze is verified, not declared: it is real only once the TS types and Pydantic models are generated from these schemas and each of the three of us imports them cleanly.

## 1. Shared conventions
| Convention | Rule |
|---|---|
| Casing | All codes/states/stages are `UPPER_SNAKE_CASE`, defined once in §2. |
| IDs | `incident_id`, `event_id`, `decision_id`, `call_id`, `correlation_id`, `transaction_id` are UUIDv4. `incident_id` groups an incident; `event_id` is unique per event. |
| Timestamps | ISO-8601 UTC with ms. |
| Envelope | Every message has `schema_version`, `type`, `event_id`, `incident_id`, `timestamp`. |
| Enums closed | A value not listed in §2 is invalid; adding one is change-control. |
| Money | Integer minor units (fils/cents) + ISO-4217 currency. |

## 2. Frozen vocabulary

### 2.1 AgentState
`MONITOR` · `WARN_USER` · `STEP_UP_VERIFICATION` · `HOLD_TRANSACTION` · `ESCALATE_TO_ANALYST`
*(GUIDED_RECOVERY is NOT here — see 2.1b.)*

### 2.1b IncidentLifecycleStatus (on the incident object)
`OPEN` · `HELD` · `IN_RECOVERY` · `CLOSED`

### 2.2 KillChainStage
| Code | Meaning | Lit by |
|---|---|---|
| `STAGE_1_IDENTITY_TAKEOVER` | SIM/identity taken over | `RECENT_SIM_SWAP` |
| `STAGE_2_SOCIAL_ENGINEERING` | Active manipulation on a live call | any tactic code (2.4) |
| `STAGE_3_FRAUDULENT_TRANSACTION` | Transaction from an untrusted context | `NEW_BENEFICIARY`, `DEVICE_UNRECOGNIZED`, `NUMBER_VERIFICATION_FAILED` |

### 2.3 Stage-evidence codes (from CAMARA / transaction, not the detector)
`RECENT_SIM_SWAP` · `NUMBER_VERIFICATION_FAILED` · `DEVICE_UNRECOGNIZED` · `NEW_BENEFICIARY`

### 2.4 Tactic codes (10, frozen — emitted by the detector, all light STAGE_2)
`AUTHORITY_IMPERSONATION` · `URGENCY` · `OTP_REQUEST` · `REMOTE_ACCESS_REQUEST` · `PAYMENT_COACHING` · `SECRECY_PRESSURE` · `FEAR_INDUCTION` · `IDENTITY_REQUEST` · `FINANCIAL_LURE` · `CAMERA_COERCION`

**AuthorityImpersonationSubtype (optional field on a tactic event, `AUTHORITY_IMPERSONATION` only):**
`BANK` · `POLICE` · `GOVERNMENT` · `TECH_SUPPORT` · `DELIVERY` · `INVESTMENT_FIRM`

*Notes:* `FEAR_INDUCTION` covers fear / legal threat / intimidation. `FINANCIAL_LURE` covers "guaranteed returns" / social proof / scarcity. `URGENCY` covers scarcity/time-pressure. `COMPLIANCE_COACHING` ("do exactly as told") is intentionally NOT its own code — represent it via `PAYMENT_COACHING`/`REMOTE_ACCESS_REQUEST` in context.

### 2.5 CamaraApi
`SIM_SWAP` · `NUMBER_VERIFICATION` · `DEVICE_STATUS`

### 2.6 Source / liveness
`LIVE` · `CACHED` · `PRERECORDED`

### 2.7 Speaker
`CALLER` · `USER` · `UNKNOWN`

### 2.8 CallStatus
`SUCCESS` · `TIMEOUT` · `ERROR` · `CIRCUIT_OPEN`

---

## 3. Schemas
Every schema carries the envelope fields (§1). Only distinctive fields shown.

**3.1 TRANSCRIPT_SEGMENT** (STT → victim client): `speaker`, `text`, `start_ms`, `end_ms`, `is_final`, `language` (`en`/`ar`/`hi`/`mixed`), `source`.

**3.2 TACTIC_EVENT** (detector → agent): `tactic_code` (2.4), `subtype` (optional, AUTHORITY_IMPERSONATION only), `kill_chain_stage` = `STAGE_2_SOCIAL_ENGINEERING`, `matched_text`, `transcript_start_ms`/`_end_ms`, `detector_layer` (`L1_REGEX`|`L2_LLM`), `confidence?`, `language`.

**3.3 TRANSACTION_AUTH** (bank → Sentinel): `transaction_id`, `amount` (int minor units), `currency`, `beneficiary {id, name, is_new_beneficiary}`, `device {id, is_recognized}`, `channel`, `initiated_at`.

**3.4 CAMARA_CALL** (agent/CAMARA client): `call_id`, `correlation_id`, `api`, `reason` (machine-readable why-called), `requested_at`, `responded_at`, `latency_ms`, `status` (2.8), `source` (2.6), `request{}`, `response{}`, `derived_evidence []` (stage-evidence codes).

**3.5 AGENT_DECISION** (agent core, Pydantic-validated): `decision_id`, `correlation_id`, `decision_version`, `action` (AgentState), `chosen_tool` (CamaraApi|`NONE`), `reason`, `active_stages []`, `evidence_considered []`, `reasoning_latency_ms` (≤1500). *The action is derived deterministically from stages (§5), not free-chosen by the model.*

**3.6 INCIDENT_REPORT** (→ console/bank): `generated_at`, `final_action`, `lifecycle_status`, `active_stages []`, `timeline []` (`{timestamp, event_type, summary, ref_id}` — summaries use reason codes, **no raw transcript text**), `camara_calls []` (`{api, reason, latency_ms, source, derived_evidence}`), `narrative` (constrained-generated), `recommendation`, `real_vs_simulated []`.

**3.7 GUIDED_RECOVERY** (→ victim client, deterministic): `generated_at`, `emirate`, `police_channel {name, contact, url}`, `cybercrime_portal {name, url}`, `bank_helpline {name, number}`, `immediate_checklist []`, `incident_report_id`.

---

## 4. Evidence → stage mapping
| Evidence | Lights |
|---|---|
| `RECENT_SIM_SWAP` | STAGE_1 |
| any tactic code (2.4) | STAGE_2 |
| `NEW_BENEFICIARY` / `DEVICE_UNRECOGNIZED` / `NUMBER_VERIFICATION_FAILED` | STAGE_3 |

## 5. Action-selection rule (composition-based — enforced in agent core; the LLM never picks the action)
Let S1/S2/S3 = the three stages active.

- **HOLD_TRANSACTION** (+ `ESCALATE_TO_ANALYST`) ⇐ **S2 ∧ S3** (active manipulation co-occurring with an active transaction). S1 corroborates but is **not required**.
- **STEP_UP_VERIFICATION** ⇐ **S3 ∧ ¬S2** (transaction-side / identity risk *without* manipulation — e.g. SCN-10: real SIM change + new beneficiary, no scam call). Also the interim escalation step.
- **WARN_USER** ⇐ **S2 ∧ ¬S3** (manipulation detected before a transaction — early intervention, D-3).
- **MONITOR** ⇐ otherwise (no stages, or a lone S1 — a SIM swap alone is often legitimate).

Stages are monotonic within a window (a lit stage cannot un-light). `ESCALATE_TO_ANALYST` always accompanies `HOLD_TRANSACTION`.

## 6. Open decisions — all resolved
D-1 tactic codes frozen (now the 10 in 2.4) · D-2 no transcript text to bank (confirmed) · D-3 WARN fires pre-convergence on strong S2 (confirmed) · D-4 money integer minor units (confirmed) · D-5 Daksh's additions incorporated (event_id, correlation_id, decision_version, WS events §7).

## 7. WebSocket Event Types
- **Victim socket → victim client:** `TRANSCRIPT_SEGMENT`, `WARN_USER` banner payloads, `GUIDED_RECOVERY`, incident-lifecycle status updates.
- **Console socket → bank console:** `TACTIC_EVENT`, `AGENT_DECISION`, `CAMARA_CALL` activity, `TRANSACTION_AUTH`, `INCIDENT_REPORT`, incident-lifecycle status updates. **No raw transcript text on the console socket** (privacy §7 of the Master Doc).

## 8. Per-scenario metadata (for the Scenario Library — agreed with Kriti)
Each scenario carries: `scenario_id` (SCN-01…10), `expected_tactic_codes []`, `transaction_present` (bool), `expected_stages {stage_1, stage_2, stage_3}`, `expected_final_action` (AgentState), `resulting_lifecycle_status` (IncidentLifecycleStatus), `category` (Scam/Legitimate/Ambiguous), `demo_or_test`.

## 9. Decision Log
**D10 (21 Jul):** HOLD rule changed from "3 stages required" to "Stage 2 ∧ Stage 3 required; Stage 1 corroborates." Reason: most scams (courier, investment, remote-access) never involve a SIM swap, so requiring Stage 1 for a HOLD would leave them un-held; verified against Scenario Library v1.0 (SCN-03/04/06 need HOLD without S1; SCN-10 needs STEP_UP because S2 absent). Trade-off consciously accepted: softens the Master Doc §2.1–2.2 "all three stages" framing into "an active transaction co-occurring with active social engineering." Confirmed by all three (Kriti, Daksh, Adithya).
**D11 (21 Jul):** Tactic taxonomy expanded 6 → 10 (+ AUTHORITY_IMPERSONATION subtype). Reason: Scenario Library uses fear/threat, identity-document, investment-lure, and camera-coercion tactics with no existing code.
