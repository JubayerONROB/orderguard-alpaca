# Demo Runbook

One page. Read this, not the codebase, while you're on stage.

## 10 minutes before

Run these from the project root, in order:

```
python -m pytest -q
python -m eval.run_eval --system rules_only
python -m eval.run_eval --system agent --llm-mode replay
```

**Healthy output:**
- pytest: `53 passed` (no failures, no errors)
- `rules_only`: `primary_score = 100.0%`, `catch_rate = 100.0%`, `false_block_rate = 0.0%`
- `agent` (replay): same three numbers, `total_llm_calls = 18`, each case's latency well
  under a second (it's replaying cassettes, not calling out)

If any of these differ, do not go on stage with live mode — fixture mode still works
regardless (it doesn't depend on the live account), but something about the environment
has drifted and needs investigating before trusting the live segment.

**Pre-warm the live path** (so the audience never watches a cold first call):

```
streamlit run ui/app.py
```

In the sidebar, flip to **Live Alpaca paper account**, type any instruction, click
**Review this** once, privately, before you open the app to the room. This is the call
that pays the ~10-20s cold-start cost — do it now, not in front of anyone. A second live
call right after is faster.

## Reset and re-seed between rehearsals

```
python scripts/reset_paper.py
python scripts/seed_demo.py
```

`reset_paper.py` cancels every open order and closes every position on the paper account
— it refuses to run against anything but a paper account, checked from `.env`.
`seed_demo.py` places two AAPL orders: a $10,000 market buy (starter position, only fills
once the market is open) and a 5-share GTC limit buy priced 20% below the last trade
(stays open regardless of market hours). Run reset then seed as one pair before every
rehearsal so the account state is identical each time.

## The demo shape

**Lead with fixture mode** — the `retail_rotation` instruction (the default in the
instruction box) produces all four findings (PDT, stacked order, concentration, wash
sale) instantly, exactly, with zero network calls. This is the reliable, rehearsable
core of the demo. Walk through the account panel, click Review, narrate the findings
table and the repaired basket.

**Then a short live segment** to prove the Alpaca integration is real, not staged: flip
the sidebar toggle to Live, show the account panel is now your actual paper account, run
an instruction that touches the seeded AAPL order (e.g. "buy $2,000 of AAPL"), and let
the audience watch a real API round-trip happen.

**One line for why the live account can't reproduce all four findings:** PDT needs
equity under $25,000 and wash sale needs a real realized loss inside 30 days — both are
account *history* you can't manufacture on demand, whereas concentration and open-order
conflicts are both just current holdings, which seeding controls directly.

## Fallback script

If the live call hangs or errors: **do not narrate the error.** Say — *"Let's not wait on
the network live, here's the same engine against a frozen scenario"* — click the sidebar
back to **Fixture (replay, no network)**, and re-click **Review this**. It's instant
because it's not calling anyone.

**Hard rule: if nothing has rendered within 30 seconds of clicking Review in live mode,
switch to fixture mode immediately and keep talking.** Don't wait it out on stage.

## If a judge asks you to reproduce the numbers

Three commands, no API key needed:

```
python -m eval.run_eval --system rules_only
python -m eval.run_eval --system agent    --llm-mode replay
python -m eval.run_eval --system baseline --llm-mode replay
```

All three read from committed cassettes in `eval/cassettes/` and produce the exact
numbers in `README.md`.
