# Reproduction

## Requirements

- Python 3.10 or later.
- Git.
- No API keys needed for the reproducible path (replay mode, below).

## Setup, from a clean machine

```
git clone https://github.com/JubayerONROB/orderguard.git
cd orderguard
python -m venv .venv
```

Activate the venv, then install:

```
# Windows
.venv\Scripts\pip install -e ".[dev]"

# macOS / Linux
.venv/bin/pip install -e ".[dev]"
```

Run the test suite to confirm the install:

```
python -m pytest -q
```

Expect 53 tests, all passing, in well under a second.

## The reproducible path: replay mode

`eval/cassettes/` holds one JSON file per LLM call ever made against the eval case set,
keyed on a hash of `(model, temperature, system prompt, user prompt)`, and is committed to
this repo. **Replay mode needs no Agnes API key and makes no network call.** Because the
compiler and baseline both run at `temperature=0` and every call is cached, a replayed run
is fully deterministic — same scores, every time.

```
python -m eval.run_eval --system rules_only
python -m eval.run_eval --system agent    --llm-mode replay
python -m eval.run_eval --system baseline --llm-mode replay

python -m eval.run_eval --system rules_only --suite holdout
python -m eval.run_eval --system agent      --suite holdout --llm-mode replay
python -m eval.run_eval --system baseline   --suite holdout --llm-mode replay
```

`--llm-mode replay` only ever reads a cassette; a miss is a loud `CassetteMissError`, never
a silent fall-through to a live call. `rules_only` never touches an LLM at all (`--llm-mode`
is accepted but ignored for it).

**Approximate runtime (replay):** each of the six commands above completes in under a
second (18 cases for the main suite, 4 for holdout) — dominated by process startup, not the
eval itself. **Cost: $0.** No network call is made.

## Live mode

```
python -m eval.run_eval --system agent    --llm-mode live
python -m eval.run_eval --system baseline --llm-mode live
```

Requires `AGNES_API_KEY` (and the other `AGNES_*` values) set in a `.env` file at the
project root — see `.env.example` for the key names; `config.py` fails loudly if any
required key is missing. Every live call is written to its cassette, so a second run of the
same case/system/prompt combination replays instead of calling out again.

**Approximate runtime (live):** roughly 10-70 seconds per case depending on the provider's
load (observed mean 20-46s across the runs in `docs/IMPROVEMENT_CHANGELOG.md`), so a full
18-case live run for one system takes on the order of 5-15 minutes. **Cost:** one Agnes
completion per case per system (18 for the main suite, 4 for holdout) at whatever the
provider charges for `agnes-2.5-flash`; not needed for reproduction, only for regenerating
cassettes.

## The UI

```
streamlit run ui/app.py
```

Opens on `http://localhost:8501`. Defaults to fixture mode (the sidebar toggle), which
drives the whole page from `eval/fixtures/` with cassette replay — no API key, no network.
This is the mode judges should use. Live mode (same sidebar toggle) uses the real Alpaca
paper account and requires `ALPACA_*` keys in `.env`.

## Everything together

```
python -m pytest -q
python -m ruff check .
python -m eval.run_eval --system rules_only
python -m eval.run_eval --system agent    --llm-mode replay
python -m eval.run_eval --system baseline --llm-mode replay
```
