# Provenance: `.claude/` framework files

The following directories under `.claude/` were copied from an external open-source
toolkit, not authored for OrderGuard:

- `.claude/agents/`
- `.claude/rules/`
- `.claude/commands/`
- `.claude/skills/`

**Source repository:** https://github.com/WorldFlowAI/everything-claude-code
**Commit SHA cloned:** `432485ba6b92c14fb357276a98957f348bcff9ee`
**License:** MIT (per the source repo's README; no standalone `LICENSE` file was
present in the cloned tree at this commit — the README's License section states
"MIT - Use freely, modify as needed, contribute back if you can.")
**Date copied:** 2026-08-29

## What was NOT copied

The toolkit's `hooks/` and `scripts/` directories were intentionally excluded from
this copy. See the project owner's notes / chat log for the list of available hooks
and what each does — they were reviewed and deliberately left unwired pending an
explicit decision on which (if any) to enable.

## What was copied, then pruned

The full `agents/`, `rules/`, `commands/`, `skills/` sets were copied verbatim first
(9 agents, 8 rules, 15 commands, 11 skills), then the project owner pruned items not
relevant to OrderGuard, for context window management (fewer irrelevant files loaded
into every session):

- `skills/clickhouse-io/` — removed (no ClickHouse in this stack)
- `skills/frontend-patterns/` — removed (no frontend framework; UI is a single Streamlit page)
- `skills/project-guidelines-example/` — removed (placeholder/example content, not applicable)
- `agents/e2e-runner.md` — removed (no e2e test suite planned for the hackathon scope)
- `commands/e2e.md` — removed (companion command to the removed e2e-runner agent)
- `commands/setup-pm.md` — removed (package-manager setup for JS/TS projects; this is a Python project)
- `commands/update-codemaps.md` — removed (not used in this project's workflow)

Remaining after pruning: 8 agents, 8 rules, 11 commands, 8 skills.

## Scope note for judges

Everything under `.claude/agents/`, `.claude/rules/`, `.claude/commands/`, and
`.claude/skills/` pre-existed this hackathon and was copied verbatim (then pruned)
from the source above. All other files in this repository — `src/`, `api/`, `ui/`,
`eval/`, `tests/`, schemas, and project docs — were written for OrderGuard during
the hackathon.
