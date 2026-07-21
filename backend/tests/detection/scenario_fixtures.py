"""
Loader for the Scenario Library v2 fixtures in <repo-root>/scenarios/.

Shared between test_tactics.py (validation) and
experimental/tactics_report.py (human-readable report) so both read the
exact same fixture data.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.contracts import AuthorityImpersonationSubtype, ReasonCode, Speaker

# backend/tests/detection/scenario_fixtures.py -> repo root is 3 parents up.
SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"


@dataclass(frozen=True)
class ExpectedTactic:
    code: ReasonCode
    subtype: AuthorityImpersonationSubtype | None = None


@dataclass(frozen=True)
class Utterance:
    speaker_role: Speaker
    text: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    category: str
    stage_2: bool
    expected_tactics: list[ExpectedTactic]
    conversation: list[Utterance]

    def caller_lines(self) -> list[str]:
        return [u.text for u in self.conversation if u.speaker_role == Speaker.CALLER]


def _load_one(path: Path) -> Scenario:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    expected_tactics = [
        ExpectedTactic(
            code=ReasonCode(item["code"]),
            subtype=(
                AuthorityImpersonationSubtype(item["subtype"].upper())
                if item.get("subtype")
                else None
            ),
        )
        for item in (data.get("expected_tactic_codes") or [])
    ]

    conversation = [
        Utterance(speaker_role=Speaker(turn["speaker_role"]), text=turn["text"])
        for turn in (data.get("conversation") or [])
    ]

    return Scenario(
        scenario_id=data["scenario_id"],
        title=data["title"],
        category=data["category"],
        stage_2=bool(data["expected_stages"]["stage_2"]),
        expected_tactics=expected_tactics,
        conversation=conversation,
    )


def load_all_scenarios() -> list[Scenario]:
    paths = sorted(SCENARIOS_DIR.glob("scn-*.yaml"))
    return [_load_one(p) for p in paths]
