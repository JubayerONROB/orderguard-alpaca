"""CLI entrypoint for the eval harness: `python -m eval.run_eval --system null|baseline|agent`.

Loads cases from `eval/cases/`, resolves each case's fixture from `eval/fixtures/` (pure
disk I/O, no network), runs the selected system, scores the result, prints a per-case
table plus summary, and writes a timestamped JSON result file under `--out`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from eval.cases import CASES_DIR, CaseLoadError, load_all_cases, load_case
from eval.fixtures import FIXTURES_DIR, FixtureLoadError, load_fixture
from eval.scorer import CaseScore, aggregate, format_report, score_case
from eval.systems import (
    AgentSystem,
    BaselineSystem,
    NullSystem,
    RuleEngineSystem,
    System,
)
from orderguard.llm.cassette import resolve_mode

DEFAULT_OUT_DIR = Path(__file__).parent / "runs"

SUITES = {
    "main": (CASES_DIR, FIXTURES_DIR),
    "holdout": (Path(__file__).parent / "cases_holdout", Path(__file__).parent / "fixtures_holdout"),
}
"""`holdout` is a held-out set written after the main 18 drove two fixes (see
docs/IMPROVEMENT_CHANGELOG.md) -- run it, but never let its results drive further
changes; that would just make it the next training set."""


def _build_systems(llm_mode: str | None) -> dict[str, System]:
    """LLM-backed systems (agent, baseline) are reconstructed with the resolved mode
    each run so `--llm-mode` (or its env var) actually takes effect; null/rules_only
    never touch an LLM and don't care."""
    mode = resolve_mode(llm_mode)
    return {
        "null": NullSystem(),
        "baseline": BaselineSystem(mode=mode),
        "agent": AgentSystem(mode=mode),
        "rules_only": RuleEngineSystem(),
    }


def _case_score_to_dict(score: CaseScore) -> dict:
    d = asdict(score)
    d["expected_decision"] = score.expected_decision.value
    d["actual_decision"] = score.actual_decision.value
    return d


def run(
    system_name: str,
    case_id: str | None,
    out_dir: Path,
    llm_mode: str | None = None,
    suite: str = "main",
) -> int:
    """Runs `system_name` over all (or one) case(s), prints and persists the results.

    Returns:
        Process exit code: 0 if every case scored (regardless of pass/fail), 1 if a
        case or fixture failed to *load*.
    """
    system = _build_systems(llm_mode)[system_name]
    cases_dir, fixtures_dir = SUITES[suite]

    try:
        if case_id is not None:
            cases = [load_case(cases_dir / f"{case_id}.json")]
        else:
            cases = load_all_cases(cases_dir=cases_dir)
    except CaseLoadError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    scores: list[CaseScore] = []
    for case in cases:
        try:
            fixture = load_fixture(case.fixture, fixtures_dir=fixtures_dir)
        except FixtureLoadError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        result = system.run(case, fixture)
        scores.append(score_case(case, result.plan, result.report, result.latency_s, result.llm_calls))

    summary = aggregate(scores)
    report_text = format_report(scores, summary)
    print(report_text)

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{system_name}_{suite}_{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "system": system_name,
                "suite": suite,
                "timestamp": timestamp,
                "cases": [_case_score_to_dict(s) for s in scores],
                "summary": asdict(summary),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OrderGuard eval harness.")
    parser.add_argument(
        "--system",
        choices=("null", "baseline", "agent", "rules_only"),
        required=True,
        help="Which system to evaluate.",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Run a single case by id (e.g. case_003) instead of the full suite.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory to write the timestamped JSON result file into.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("live", "replay", "auto"),
        default=None,
        help="Cassette mode for agent/baseline (live|replay|auto). Overrides ORDERGUARD_LLM_MODE; defaults to auto.",
    )
    parser.add_argument(
        "--suite",
        choices=sorted(SUITES),
        default="main",
        help="Which case suite to run: 'main' (the 18 cases that drove development) or "
        "'holdout' (written after, never used to drive further changes).",
    )
    args = parser.parse_args()
    sys.exit(run(args.system, args.case, args.out, args.llm_mode, args.suite))


if __name__ == "__main__":
    main()
