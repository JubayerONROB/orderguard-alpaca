"""Tests for eval/fixtures/__init__.py loading."""

from __future__ import annotations

import json

import pytest

from eval.fixtures import FIXTURES_DIR, FixtureLoadError, load_fixture


@pytest.mark.parametrize("name", ["simple_msft_buy", "pdt_aapl_daytrade", "retail_rotation"])
def test_seed_fixtures_load(name: str) -> None:
    fixture = load_fixture(name)
    assert fixture.name == name
    assert fixture.account.account_id
    assert fixture.market.as_of


def test_missing_fixture_raises_loudly() -> None:
    with pytest.raises(FixtureLoadError, match="not found"):
        load_fixture("does_not_exist")


def test_malformed_account_json_raises_loudly(tmp_path) -> None:
    fixture_dir = tmp_path / "broken"
    fixture_dir.mkdir()
    (fixture_dir / "account.json").write_text(json.dumps({"account_id": "x"}), encoding="utf-8")
    (fixture_dir / "market.json").write_text(
        json.dumps(
            {
                "as_of": "2026-08-27T14:30:00Z",
                "clock": {
                    "timestamp": "2026-08-27T14:30:00Z",
                    "is_open": True,
                    "next_open": "2026-08-28T13:30:00Z",
                    "next_close": "2026-08-27T20:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FixtureLoadError, match="invalid AccountState"):
        load_fixture("broken", fixtures_dir=tmp_path)


def test_fixtures_dir_points_at_eval_fixtures() -> None:
    assert FIXTURES_DIR.name == "fixtures"
    assert (FIXTURES_DIR / "retail_rotation" / "account.json").is_file()
