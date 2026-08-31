"""Append-only JSONL audit log: every prompt, tool call, rule result, and retry.

One line per event, written in order, never rewritten. This is the record that lets a
judge (or a debugging session) reconstruct exactly what the compiler, rule engine, and
repair agent did for a given instruction, in what order.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrajectoryLogger:
    """Appends structured events to a per-run JSONL audit log."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Appends one timestamped event to the trajectory log.

        Args:
            event_type: e.g. "compiler_prompt", "compiler_response", "compiler_retry",
                "compiler_hard_failure", "rule_result", "repair_attempt".
            payload: event-specific structured data. Must be JSON-serializable.
        """
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"timestamp": self._now(), "event_type": event_type, "payload": payload})
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
