# Sample trajectories

Three representative trajectory logs, copied out of `eval/runs/trajectories/` (which is
gitignored, since it accumulates across every local run) so a full agent trajectory ships
as part of the repo.

- `case_003.jsonl` — the hard case from the main suite: four rules fire in one basket
  (PDT, a stacked open order, concentration, wash sale), three get repaired, one is a
  warning. See the worked example in the top-level `README.md`.
- `case_017.jsonl` — the case that specifically exercises the two-phase evaluation fix: a
  concentration breach that only exists because of a pending open order, resolved as a
  side effect of that order's cancellation. See `docs/IMPROVEMENT_CHANGELOG.md`'s "Two-phase
  evaluation" entry.
- `case_102_holdout.jsonl` — from the held-out suite, written after the main 18 cases had
  already driven two fixes: extended-hours eligibility, concentration, and buying power
  interacting across two repair stages in a single basket.

## Format

Each file is JSON Lines: one JSON object per line, appended in order, never rewritten. Each
line has the same three keys:

```json
{
  "timestamp": "2026-08-29T07:00:27.216667+00:00",
  "event_type": "compiler_prompt",
  "payload": { "...": "..." }
}
```

`event_type` is one of:

- `compiler_prompt` — the exact system and user prompt sent to the model for one compile
  call. `payload.system` is the versioned prompt from `src/orderguard/compiler/prompts/`;
  `payload.user` is the rendered account/market context plus the trader's instruction (see
  `render_context` in `src/orderguard/compiler/intent_compiler.py`).
- `compiler_response` — the model's parsed, schema-validated basket for that call.
- `compiler_validation_error` / `compiler_retry_prompt` / `compiler_hard_failure` — only
  present if the first response failed schema validation; the compiler retries once with
  the error text appended before giving up (see `IntentCompiler.compile`).

## Reading them

These logs are append-only across every local run of that case, not just one clean
session — a compile that was re-run (e.g. after a prompt version bump, or to verify replay
determinism) adds new entries rather than replacing old ones. To see the CURRENT compiler
behavior for a case, read from the bottom: the last `compiler_prompt` / `compiler_response`
pair is the most recent call. Earlier entries are historical and can reflect an older
prompt version (e.g. `compile_v1.md` before the round-down fix in
`docs/IMPROVEMENT_CHANGELOG.md`'s "A2" entry) -- that's expected, not an error in the log.

The rule engine itself never appears in these files: it's deterministic Python with no LLM
calls, so there's nothing to log a prompt/response for. What the rule engine did with a
given compiled basket is visible in the corresponding `eval/runs/<system>_*.json` result
file's `rules_fired` / decision fields, not here.
