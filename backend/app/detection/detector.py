"""
Sentinel layer-1 tactic detector (Block B, offline).

Pure keyword/regex matching engine -- no LLM, zero tokens. Consumes the
pattern tables and negation-shape metadata defined in taxonomy.py; see
that module's docstring for the detection-design rationale (request-shape
matching, negation guard + exemptions, the AUTHORITY_IMPERSONATION/BANK
corroboration gate).

detect_tactics() only ever looks at CALLER speech -- USER/UNKNOWN lines
return no events unconditionally. This mirrors how the scenario fixtures
are meant to be exercised ("run detect_tactics over the caller's lines")
and gives a structural guarantee similar to the agent loop's
ToolChoice-has-no-action-field: caller misuse can't accidentally fire
tactic codes off the victim's own speech.
"""
from __future__ import annotations

from app.contracts import AuthorityImpersonationSubtype, ReasonCode, Speaker
from app.detection.taxonomy import (
    ALL_TACTIC_PATTERN_TABLES,
    AUTHORITY_ORG_CLAIM,
    AUTHORITY_ORG_CLAIM_INTL,
    BANK_ALARM_RE,
    CLAUSE_BOUNDARY_RE,
    NEGATION_EXEMPT_CODES,
    NEGATION_RE,
    OVERRIDE_RE,
    SELF_DIRECTED_RE,
    SUBTYPE_KEYWORDS,
    TacticEvent,
)

__all__ = ["TacticEvent", "detect_tactics"]


def _suppressed_by_negation(text: str, start: int) -> bool:
    boundary_end = 0
    for boundary in CLAUSE_BOUNDARY_RE.finditer(text, 0, start):
        boundary_end = boundary.end()
    window = text[boundary_end:start]
    if not NEGATION_RE.search(window):
        return False
    return not OVERRIDE_RE.search(text)


def _detect_authority_subtype(text: str) -> AuthorityImpersonationSubtype | None:
    # Checked in a fixed priority order so a phrase naming multiple
    # institutions resolves deterministically (police/government checked
    # before the generic bank fallback).
    for subtype in (
        AuthorityImpersonationSubtype.POLICE,
        AuthorityImpersonationSubtype.GOVERNMENT,
        AuthorityImpersonationSubtype.TECH_SUPPORT,
        AuthorityImpersonationSubtype.DELIVERY,
        AuthorityImpersonationSubtype.INVESTMENT_FIRM,
        AuthorityImpersonationSubtype.BANK,
    ):
        if SUBTYPE_KEYWORDS[subtype].search(text):
            return subtype
    return None


def _any_other_tactic_matches(text: str) -> bool:
    """Used only for the BANK-subtype corroboration gate."""
    for patterns in ALL_TACTIC_PATTERN_TABLES.values():
        for pattern in patterns:
            if pattern.search(text):
                return True
    return False


def _collect(text: str, code: ReasonCode) -> list[TacticEvent]:
    events: list[TacticEvent] = []
    exempt = code in NEGATION_EXEMPT_CODES
    for pattern in ALL_TACTIC_PATTERN_TABLES[code]:
        for match in pattern.finditer(text):
            if not exempt and _suppressed_by_negation(text, match.start()):
                continue
            events.append(TacticEvent(tactic_code=code, matched_text=match.group(0)))
    return events


def detect_tactics(text: str, speaker: Speaker) -> list[TacticEvent]:
    """Pure keyword/regex detector over a single utterance.

    Only ever fires on CALLER speech -- USER/UNKNOWN lines always return
    an empty list, since every tactic code in CONTRACT.md 2.4 describes
    something the *caller* does to the victim, never the reverse.
    """
    if speaker != Speaker.CALLER:
        return []
    if not text or not text.strip():
        return []

    events: list[TacticEvent] = []

    otp_events = _collect(text, ReasonCode.OTP_REQUEST)
    events.extend(e for e in otp_events if "access code" not in e.matched_text.lower())
    events.extend(_collect(text, ReasonCode.REMOTE_ACCESS_REQUEST))

    payment_events = _collect(text, ReasonCode.PAYMENT_COACHING)
    for event in payment_events:
        start = text.find(event.matched_text)
        window = text[max(0, start - 10):start + len(event.matched_text) + 20]
        if SELF_DIRECTED_RE.search(window):
            continue
        events.append(event)

    events.extend(_collect(text, ReasonCode.IDENTITY_REQUEST))
    events.extend(_collect(text, ReasonCode.FEAR_INDUCTION))
    events.extend(_collect(text, ReasonCode.URGENCY))
    events.extend(_collect(text, ReasonCode.SECRECY_PRESSURE))
    events.extend(_collect(text, ReasonCode.CAMERA_COERCION))
    events.extend(_collect(text, ReasonCode.FINANCIAL_LURE))

    org_claim = AUTHORITY_ORG_CLAIM.search(text) or AUTHORITY_ORG_CLAIM_INTL.search(text)
    if org_claim:
        subtype = _detect_authority_subtype(text)
        if subtype is not None:
            fire = True
            if subtype is AuthorityImpersonationSubtype.BANK:
                fire = bool(BANK_ALARM_RE.search(text)) or _any_other_tactic_matches(text)
            if fire:
                events.append(
                    TacticEvent(
                        tactic_code=ReasonCode.AUTHORITY_IMPERSONATION,
                        matched_text=org_claim.group(0),
                        subtype=subtype,
                    )
                )

    return events
