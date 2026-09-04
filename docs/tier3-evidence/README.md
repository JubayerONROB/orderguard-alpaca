# Tier 3 evidence: two real runs

Two real (paper) `paper-demo.yml` runs, captured in full, as the two stories the demo
tells: **AI proposes and executes when it's safe to**, and **the deterministic layer
refuses when it isn't** -- both against the real account, both live LLM calls, neither
scripted or replayed.

Each directory holds:
- `console-output.txt` -- the complete, unedited workflow log for the "Run the full
  research -> compile -> rules -> approve -> submit flow" step.
- `paper_demo_report.txt` -- the same content, as the artifact the workflow itself
  uploads (`actions/upload-artifact`).
- `trajectories/paper_demo.jsonl` -- the structured trajectory log: every LLM prompt and
  response (strategy discovery, intent compiler), plus the scripted-approval event
  carrying the full final `OrderPlan`.

No screenshots of the GitHub Actions UI are included here -- this environment has no
browser/screenshot tooling. The text evidence above is the complete, verifiable record
(more auditable than a screenshot, since every number in it is greppable and diffable);
follow the run links below if you want to see or capture the graphical UI yourself.

## Story 1: successful run (real order, filled)

[Run 33855220353](https://github.com/JubayerONROB/orderguard-alpaca/actions/runs/33855220353) -- `docs/tier3-evidence/successful-run-33855220353/`

- Research pipeline proposed **"MSFT Bullish Momentum Pullback"** (momentum entry, fixed
  hold-days exit), backtested OOS return 16.3%, Sharpe 1.74, adversary verdict PASS
  (60/100), lifecycle state **WATCH**.
- Market was closed at run time, so the generated instruction asked for an
  extended-hours limit order at a real quote (`$512.70`, 0.5% above the last trade).
- R1-R7: **Decision: ALLOW**, no findings.
- Submitted: `buy MSFT 9 sh` -> order `ae7ab3ce-6700-44d6-a5f1-e05e1792f355`.
- Confirmed against the live account afterward: **filled** at an average price of
  $508.2684/share (better than the $512.70 limit) -- a real 9-share MSFT position.

## Story 2: blocked run (safety demonstration)

[Run 33852216976](https://github.com/JubayerONROB/orderguard-alpaca/actions/runs/33852216976) -- `docs/tier3-evidence/blocked-run-33852216976/`

- Research pipeline proposed **"MSFT Bullish Momentum Capture"** (momentum entry, ATR
  stop), backtested OOS return 24.5%, Sharpe 2.32, adversary verdict PASS (71/100),
  lifecycle state **WATCH**.
- Compiled to a plain instruction with no extended-hours request: `Buy approximately
  $5001 of MSFT`.
- R1-R7: **Decision: BLOCK** -- `R6_SESSION: market is closed (next open 2026-09-04
  09:30:00-04:00) and the MSFT order is not extended-hours eligible`.
- Nothing submitted to Alpaca. No order exists from this run.

Same pipeline, same account, same day -- the only difference was whether the compiled
order was eligible to execute in the current session. That's the deterministic layer
doing its job: a strategy the AI is confident about does not override a session rule it
fails.
