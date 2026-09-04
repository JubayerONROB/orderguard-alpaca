"""The research pipeline: market intelligence, strategy discovery, backtesting,
adversarial robustness testing, lifecycle classification, and portfolio allocation.

Everything here is upstream of the existing compile -> rule engine -> human approval ->
AlpacaClient pipeline (src/orderguard/compiler/, src/orderguard/rules/,
src/orderguard/broker/), which is untouched. This package's job ends the moment it hands
a plain-English instruction to that pipeline, or blocks before doing so
(see portfolio_guard.py) -- it never bypasses it.
"""
