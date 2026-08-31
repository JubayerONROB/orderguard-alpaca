"""Tests for eval/cases/__init__.py loading."""

from __future__ import annotations

import json

import pytest

from eval.cases import CASES_DIR, CaseLoadError, load_all_cases, load_case
from orderguard.schemas.risk_report import Decision


def test_seed_cases_load() -> None:
    cases = load_all_cases()
    ids = [c.id for c in cases]
    assert ids == [f"case_{i:03d}" for i in range(1, 19)]


def test_case_001_is_clean_allow() -> None:
    case = load_case(CASES_DIR / "case_001.json")
    assert case.expected.decision == Decision.ALLOW
    assert case.expected.rules_fired == ()
    assert len(case.expected.orders) == 1


def test_case_002_is_pdt_block() -> None:
    case = load_case(CASES_DIR / "case_002.json")
    assert case.expected.decision == Decision.BLOCK
    assert case.expected.rules_fired == ("R2_PDT",)
    assert case.expected.orders == ()


def test_case_003_is_allow_with_repairs() -> None:
    case = load_case(CASES_DIR / "case_003.json")
    assert case.expected.decision == Decision.ALLOW_WITH_REPAIRS
    assert set(case.expected.rules_fired) == {"R2_PDT", "R4_OPEN_ORDERS", "R3_CONCENTRATION", "R5_WASH_SALE"}
    assert len(case.expected.orders) == 3
    assert len(case.expected.cancellations) == 1
    assert case.fixture == "retail_rotation"


def test_missing_case_raises_loudly() -> None:
    with pytest.raises(CaseLoadError, match="not found"):
        load_case(CASES_DIR / "case_999.json")


def test_malformed_case_raises_loudly(tmp_path) -> None:
    path = tmp_path / "case_bad.json"
    path.write_text(json.dumps({"id": "case_bad"}), encoding="utf-8")
    with pytest.raises(CaseLoadError, match="invalid case"):
        load_case(path)
