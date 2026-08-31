"""Plain-English names for rule codes, one place, reused by the approval UI.

`RuleResult`/`FiredRule.rule_id` uses codes like "R3_CONCENTRATION" -- correct for an
audit log, wrong for a trader-facing screen. This is the only place that translation
happens.
"""

from __future__ import annotations

RULE_LABELS: dict[str, str] = {
    "R1_BUYING_POWER": "Buying power",
    "R2_PDT": "Pattern day trading",
    "R3_CONCENTRATION": "Position size limit",
    "R4_OPEN_ORDERS": "Conflicting order",
    "R5_WASH_SALE": "Wash sale",
    "R6_SESSION": "Market hours",
    "R7_ASSET_ELIGIBILITY": "Asset eligibility",
}

ALL_RULE_CODES: tuple[str, ...] = tuple(RULE_LABELS)


def rule_label(rule_id: str) -> str:
    """Plain-English name for a rule code, falling back to the code itself if unknown."""
    return RULE_LABELS.get(rule_id, rule_id)
