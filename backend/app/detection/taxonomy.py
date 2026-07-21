"""
Sentinel layer-1 tactic taxonomy: the catalog of what each tactic code
looks like -- regex pattern tables, subtype keywords, and the shape
metadata detector.py's matching engine consumes.

Design rule (CRITICAL): patterns match the request/imperative *shape* of a
tactic, not bare keyword presence. "Read me the code" fires OTP_REQUEST;
"I just received a code" does not, because it isn't a request at all.

Patterns are derived from the trigger phrases and false-positive notes
captured in scenarios/scn-*.yaml (the frozen Scenario Library v2
fixtures). Every code here lights STAGE_2_SOCIAL_ENGINEERING (CONTRACT.md
S2.4/S4); this module never touches Stage 1 or Stage 3 evidence.

Negation handling. Most tactic phrasing is a bare positive imperative, so
detector.py's shared negation guard (built from _NEGATION_RE) suppresses
matches immediately preceded by "not/never/don't/..." within the same
clause -- this is what keeps SCN-09's "Please do not share your OTP" and
"Do not share your password or security code" (SCN-01) from firing
OTP_REQUEST. That guard has two kinds of exception, both declared here:

  * CAMERA_COERCION and SECRECY_PRESSURE are canonically phrased as
    prohibitions ("do not turn off the camera", "do not contact anyone")
    -- the negation IS the coercion. URGENCY and FEAR_INDUCTION are
    commonly embedded in a negated *conditional* clause that is still
    fully manipulative ("if we don't fix it immediately...", "if payment
    is not completed..., your package will be returned") -- the negation
    attaches to the compliance verb, not to the urgency/threat clause
    itself. All four are listed in _NEGATION_EXEMPT_CODES so the shared
    guard is never applied to them.
  * A handful of scam lines explicitly neutralise a warning before
    reinforcing it ("That warning does not apply to...", "...but it is
    safe because I am from the bank") -- _OVERRIDE_RE cancels the
    negation guard for these, per SCN-01 section 7's own worked example.

AUTHORITY_IMPERSONATION/BANK subtype gets one more gate, applied in
detector.py: SCN-09 (legit bank call) proves that "calling from your
bank" alone must NOT fire the code -- it needs to co-occur with an alarm
cue (_BANK_ALARM_RE) or another tactic match in the same utterance. Other
subtypes (POLICE, GOVERNMENT, TECH_SUPPORT, DELIVERY, INVESTMENT_FIRM)
have no such corpus counterexample, so they fire directly off the
organisation-claim + subtype-keyword match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.contracts import AuthorityImpersonationSubtype, ReasonCode

__all__ = [
    "TacticEvent",
    "NEGATION_RE",
    "OVERRIDE_RE",
    "CLAUSE_BOUNDARY_RE",
    "NEGATION_EXEMPT_CODES",
    "OTP_REQUEST_PATTERNS",
    "AUTHORITY_ORG_CLAIM",
    "AUTHORITY_ORG_CLAIM_INTL",
    "SUBTYPE_KEYWORDS",
    "BANK_ALARM_RE",
    "REMOTE_ACCESS_PATTERNS",
    "PAYMENT_COACHING_PATTERNS",
    "SELF_DIRECTED_RE",
    "IDENTITY_REQUEST_PATTERNS",
    "FEAR_INDUCTION_PATTERNS",
    "URGENCY_PATTERNS",
    "SECRECY_PRESSURE_PATTERNS",
    "CAMERA_COERCION_PATTERNS",
    "FINANCIAL_LURE_PATTERNS",
    "ALL_TACTIC_PATTERN_TABLES",
]


@dataclass(frozen=True)
class TacticEvent:
    tactic_code: ReasonCode
    matched_text: str
    subtype: AuthorityImpersonationSubtype | None = None


# --- Shared negation handling ------------------------------------------------

NEGATION_RE = re.compile(
    r"\b(not|never|don't|do not|won't|will not|shouldn't|should not|"
    r"must not|mustn't|no need to)\b",
    re.IGNORECASE,
)
# A negation immediately followed by one of these within the same clause
# means the speaker is neutralising/overriding a warning rather than
# reinforcing it -- SCN-01 section 7's worked example.
OVERRIDE_RE = re.compile(
    r"\b(but (it'?s|it is|that'?s|that is) (safe|fine|okay|ok)|"
    r"does not apply|doesn't apply|is safe (to share|because)|"
    r"safe because|that (message|warning) is (automatic|for))\b",
    re.IGNORECASE,
)
# Sentence/clause boundary -- the negation lookback never crosses one, so a
# negation in an earlier sentence ("Don't tell anyone yet.") can't suppress
# an unrelated match in a later one ("Just send the money first.").
CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;]")

NEGATION_EXEMPT_CODES = frozenset({
    ReasonCode.SECRECY_PRESSURE,
    ReasonCode.CAMERA_COERCION,
    ReasonCode.URGENCY,
    ReasonCode.FEAR_INDUCTION,
})


# --- Per-code pattern tables --------------------------------------------------
# Each entry is a compiled regex whose *whole match* is the matched_text.
# English patterns are imperative/request-shaped; Hindi patterns use the
# Romanized (Latin-script) Hindi the source library itself uses; Arabic
# patterns mix transliterated and Arabic-script fragments, matching
# whichever form the corpus actually used for that phrase. Patterns
# annotated "demo coverage" have no corpus example and are best-effort
# translations for breadth, not validated against a fixture.

OTP_REQUEST_PATTERNS = [
    re.compile(
        r"\b(read|share|tell|send|give|disclose|provide|ask for)\b"
        r"(?:\s+\w+){0,4}?\s+"
        r"(the\s+)?(six[- ]digit\s+)?(otp|code|verification code|security code|pin)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bread it to me\b", re.IGNORECASE),
    # Hindi (Romanized) -- demo coverage.
    re.compile(r"\bcode\s+batao\b|\botp\s+share\s+karo\b|\bcode\s+padho\b", re.IGNORECASE),
    # Arabic (script) -- demo coverage.
    re.compile(r"شارك.{0,10}(الرمز|رمز التحقق)|اقرأ.{0,10}(الرمز|رمز التحقق)"),
]

AUTHORITY_ORG_CLAIM = re.compile(
    r"\b(i am|i'm|this is)\b(?:\s+\w+){0,4}?\s+(calling|speaking)\b(?:\s+\w+){0,3}?\s+from\b"
    r"|\bcalling from\b"
    r"|\bthis is\b(?:\s+\w+){0,5}?\s+from\b"
    r"|\b(department|division|team)\s+from\b",
    re.IGNORECASE,
)
# Best-effort Hindi/Arabic org-claim shapes -- demo coverage.
AUTHORITY_ORG_CLAIM_INTL = re.compile(
    r"\bmain\b.{0,15}\bse\s+bol\s+raha\s+hoon\b|\bmain\b.{0,10}\bse\s+hoon\b"
    r"|أتصل\s+من|أنا\s+من",
    re.IGNORECASE,
)

SUBTYPE_KEYWORDS: dict[AuthorityImpersonationSubtype, re.Pattern[str]] = {
    AuthorityImpersonationSubtype.POLICE: re.compile(
        r"\bpolice\b|\bcyber crime\b|\bofficer\b|\bشرطة\b", re.IGNORECASE
    ),
    AuthorityImpersonationSubtype.GOVERNMENT: re.compile(
        r"\bgovernment\b|\bimmigration\b|\bministry\b", re.IGNORECASE
    ),
    AuthorityImpersonationSubtype.TECH_SUPPORT: re.compile(
        r"\bmicrosoft\b|\bapple\b|\betisalat\b|\bdu\b|\btech(nical)? support\b"
        r"|\bcomputer security\b|\banydesk\b|\bteamviewer\b",
        re.IGNORECASE,
    ),
    AuthorityImpersonationSubtype.DELIVERY: re.compile(
        r"\bdhl\b|\baramex\b|\bemirates post\b|\bcourier\b|\bcustoms\b|\bparcel\b",
        re.IGNORECASE,
    ),
    AuthorityImpersonationSubtype.INVESTMENT_FIRM: re.compile(
        r"\binvestment\b|\btrading\b|\bcapital\b|\bcrypto(currency)?\b",
        re.IGNORECASE,
    ),
    AuthorityImpersonationSubtype.BANK: re.compile(
        r"\bbank\b|\bfraud[- ](prevention|monitoring)\b|\bsecurity department\b",
        re.IGNORECASE,
    ),
}

# Same-utterance alarm lexicon that must corroborate a BANK-subtype claim
# (SCN-09 proves "calling from your bank" alone is not enough). Excludes
# "fraud" itself since it's part of both the scam AND legitimate
# department names ("Fraud Prevention Department" / "fraud-monitoring
# team").
BANK_ALARM_RE = re.compile(
    r"\b(unusual|suspicious|unauthorised|unauthorized|detected|compromised|"
    r"hacked|fraudulent|flagged|blocked|frozen|under investigation)\b",
    re.IGNORECASE,
)

REMOTE_ACCESS_PATTERNS = [
    re.compile(r"\bdownload\s+(anydesk|teamviewer)\b", re.IGNORECASE),
    re.compile(r"\bshare\s+(your\s+)?screen\b", re.IGNORECASE),
    re.compile(r"\bshare\s+the\s+access\s+code\b", re.IGNORECASE),
    re.compile(r"\binstall\s+(the\s+)?remote[- ](access|desktop)\b", re.IGNORECASE),
    # Demo coverage.
    re.compile(r"\banydesk\s+download\s+karo\b|\bscreen\s+share\s+karo\b", re.IGNORECASE),
    re.compile(r"حمّل\s*(teamviewer|anydesk)|شارك\s+الشاشة"),
]

PAYMENT_COACHING_PATTERNS = [
    re.compile(r"\bopen\s+your\s+banking\s+app(lication)?\b", re.IGNORECASE),
    re.compile(
        r"\bapprove\s+(the\s+|this\s+|a\s+|an\s+)?(beneficiary|payment|transfer|request|notification)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(approve|confirm)\s+it\b", re.IGNORECASE),
    re.compile(r"\b(add|confirm)\s+(this\s+|the\s+)?beneficiary\b", re.IGNORECASE),
    re.compile(r"\bignore\s+the\s+(new-beneficiary\s+)?warning\b", re.IGNORECASE),
    re.compile(r"\b(transfer|send)\s+(the\s+)?(amount|funds|money)\s*(now|immediately)?\b", re.IGNORECASE),
    # "Transfer AED 4,500 now" -- a currency amount, not the literal noun
    # "amount"/"funds"/"money", still needs to fire.
    re.compile(r"\btransfer\b(?:\s+\w+){0,4}?\s+(now|immediately)\b", re.IGNORECASE),
    re.compile(r"\bcomplete\s+the\s+(payment|transfer)\b", re.IGNORECASE),
    re.compile(r"\bI will guide you through\b", re.IGNORECASE),
    # Demo coverage.
    re.compile(r"\bbanking\s+app\s+kholo\b|\bbeneficiary\s+approve\s+karo\b", re.IGNORECASE),
    re.compile(r"افتح\s+تطبيق\s+البنك|وافق\s+على\s+المستفيد"),
]
# Guard against SCN-01's legitimate counterexample: "Open the banking
# application yourself and review the transaction." -- self-directed
# review, not caller-guided payment.
SELF_DIRECTED_RE = re.compile(
    r"\b(yourself|independently|on your own|without (any )?help)\b", re.IGNORECASE
)

IDENTITY_REQUEST_PATTERNS = [
    re.compile(
        r"\b(confirm|show|provide|display|read|hold)\b(?:\s+\w+){0,4}?\s+"
        r"(your\s+)?(emirates id|passport|id number|identification document|identity)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bverify your identity\b", re.IGNORECASE),
    re.compile(r"\bneed to see\b.{0,20}\b(id|identification|passport)\b", re.IGNORECASE),
    # Demo coverage.
    re.compile(r"\bid\s+number\s+batao\b|\bpassport\s+dikhao\b", re.IGNORECASE),
    re.compile(r"أرني\s+هويتك|اعرض\s+جواز\s+سفرك"),
]

FEAR_INDUCTION_PATTERNS = [
    re.compile(
        r"\b(you may be arrested|arrest notice|legal action (will|may) be initiated|"
        r"account will be frozen|id will be suspended|criminal complaint|"
        r"you are a suspect|banking information could be stolen|"
        r"computer is infected|malware and suspicious activity|"
        r"under your (name|emirates id))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(package|parcel)\b.{0,20}\b(returned?|lost|seized|held)\b", re.IGNORECASE),
    re.compile(r"\bhospital (needs|requires) payment\b", re.IGNORECASE),
    # Fear of losing the money/ability to stop the transaction, rather than
    # a direct threat (SCN-01's framing: "if you delay, the transaction
    # may be completed").
    re.compile(
        r"\b(transaction|transfer|payment)\b.{0,15}\b(may|will)\b.{0,15}"
        r"\b(be completed|be approved|be processed)\b",
        re.IGNORECASE,
    ),
    # Corpus Hindi (SCN-02): "khilaaf legal action... account freeze".
    re.compile(r"\bkhilaaf\s+legal\s+action\b|\baccount\s+freeze\b", re.IGNORECASE),
    # Corpus Arabic (SCN-02): tajmid hisabik / legal proceedings against you.
    re.compile(r"تجميد\s*حسابك|إجراءات\s*قانونية\s*ضدك"),
]

URGENCY_PATTERNS = [
    re.compile(
        r"\b(immediately|right now|hurry|urgent(ly)?|without delay|"
        r"within (the )?next \d+\s*(minutes?|hours?)|"
        r"only \w+ (slots?|minutes?) remain|"
        r"offer closes today|expires shortly|limited time|last chance|"
        r"few minutes|market is moving)\b",
        re.IGNORECASE,
    ),
    # Corpus Hindi/Arabic (SCN-01/03/06).
    re.compile(r"\bkam\s+time\s+hai\b|\bwapas\s+chala\s+jayega\b|\boffer\s+sirf\s+aaj\b", re.IGNORECASE),
    re.compile(r"محدود\s*وقت|قيد\s*التنفيذ|يوم\s*واحد\s*فقط"),
]

SECRECY_PRESSURE_PATTERNS = [
    re.compile(
        r"\b(do not|don'?t)\s+(disconnect|contact|tell|call|discuss|say)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bstay on the line\b", re.IGNORECASE),
    re.compile(r"\bkeep (this|it) confidential\b", re.IGNORECASE),
    re.compile(r"\bthis (investigation|matter|case) is confidential\b", re.IGNORECASE),
    re.compile(r"\btreated as non-cooperation\b", re.IGNORECASE),
    # Corpus Hindi/Arabic (SCN-01): "call cut mat kijiye" / "la tughliq al-mukalama".
    re.compile(r"\bcall\s*cut\s*mat\s*kijiye\b", re.IGNORECASE),
    re.compile(r"\bla\s+tughliq\s+al-mukalama\b", re.IGNORECASE),
]

CAMERA_COERCION_PATTERNS = [
    re.compile(r"\bswitch on your camera\b", re.IGNORECASE),
    re.compile(r"\b(do not|don'?t)\s+(turn off|switch off|disconnect)\s+the\s+camera\b", re.IGNORECASE),
    re.compile(r"\bkeep your (face|camera)\b", re.IGNORECASE),
    re.compile(
        r"\bshow\s+(your\s+)?(emirates id|passport|identification|id)\b(?:\s+\w+){0,4}?\s+camera\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmove (it|the document) closer\b", re.IGNORECASE),
    re.compile(r"\bshow the (front and back|back side)\b", re.IGNORECASE),
    # Corpus Hindi/Arabic (SCN-05): "camera band mat kijiye" / "la tughliq al-camera".
    re.compile(r"\bcamera\s*band\s*mat\s*kijiye\b", re.IGNORECASE),
    re.compile(r"\bla\s+tughliq\s+al-camera\b", re.IGNORECASE),
]

FINANCIAL_LURE_PATTERNS = [
    re.compile(
        r"\b(guaranteed returns?|risk-free|no risk|earn \d+% (in|within)|"
        r"profit is guaranteed|hundreds of (investors|clients) have already earned|"
        r"exclusive investment opportunity|regulated opportunity)\b",
        re.IGNORECASE,
    ),
    # Trust-based lure (SCN-07's "financial lure through trust" framing).
    re.compile(r"\byou'?re the only (person|one)( i trust)?\b|\bi trust you\b", re.IGNORECASE),
    # Corpus Hindi/Arabic scarcity framing (SCN-06).
    re.compile(r"\bpremium\s+slot\s+close\b|\boffer\s+sirf\s+aaj\b", re.IGNORECASE),
    re.compile(r"فرصة.{0,15}يوم\s*واحد|أماكن\s*للمستثمرين"),
]

# All non-authority pattern tables, keyed by tactic code -- used by
# detector.py both to run detection and for the BANK-subtype
# corroboration check ("did some other tactic also match this utterance?").
ALL_TACTIC_PATTERN_TABLES: dict[ReasonCode, list[re.Pattern[str]]] = {
    ReasonCode.OTP_REQUEST: OTP_REQUEST_PATTERNS,
    ReasonCode.REMOTE_ACCESS_REQUEST: REMOTE_ACCESS_PATTERNS,
    ReasonCode.PAYMENT_COACHING: PAYMENT_COACHING_PATTERNS,
    ReasonCode.IDENTITY_REQUEST: IDENTITY_REQUEST_PATTERNS,
    ReasonCode.FEAR_INDUCTION: FEAR_INDUCTION_PATTERNS,
    ReasonCode.URGENCY: URGENCY_PATTERNS,
    ReasonCode.SECRECY_PRESSURE: SECRECY_PRESSURE_PATTERNS,
    ReasonCode.CAMERA_COERCION: CAMERA_COERCION_PATTERNS,
    ReasonCode.FINANCIAL_LURE: FINANCIAL_LURE_PATTERNS,
}
