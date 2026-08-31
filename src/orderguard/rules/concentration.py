"""R3: resulting position weight vs the user's cap, against equity.

REPAIR: the trader stated the cap, so rounding a buy down to fit it enforces their own
words -- see CLAUDE.md's repair principle. Repair always resizes to a whole-share `qty`
order (rounding DOWN), regardless of whether the original order was qty- or
notional-based, so the result is consistent across cases.

The worst case a buy must be checked against includes not just the current held
position, but any of that symbol's OWN open orders that could still fill (unless this
basket cancels them) -- a naive check using only "current position + new basket
orders" misses a pending order that would push the position over cap the moment it
fills too. This is the single most common naive-implementation failure (case_017).
"""

from __future__ import annotations

from decimal import Decimal

from orderguard.rules._util import (
    floor_shares,
    get_position,
    is_buy,
    order_notional_value,
    order_reference_price,
)
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import (
    OrderPlan,
)
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


def _cap_value(state: AccountState, constraints: UserConstraints) -> Decimal:
    return (constraints.max_position_pct / Decimal(100)) * state.equity


def _existing_position_value(state: AccountState, symbol: str) -> Decimal:
    position = get_position(state, symbol)
    if position is None:
        return Decimal(0)
    return position.qty * position.current_price


def _pending_open_buy_value(state: AccountState, market: MarketSnapshot, symbol: str, cancelled: set[str]) -> Decimal:
    """Worst-case value if every uncancelled open BUY order on `symbol` also fills."""
    total = Decimal(0)
    for oo in state.open_orders:
        if oo.symbol != symbol or oo.order_id in cancelled or oo.side != "buy":
            continue
        price = oo.limit_price if oo.limit_price is not None else None
        if oo.qty is not None:
            total += oo.qty * (price if price is not None else _quote_price(market, symbol))
        elif oo.notional is not None:
            total += oo.notional
    return total


def _quote_price(market: MarketSnapshot, symbol: str) -> Decimal:
    for quote in market.quotes:
        if quote.symbol == symbol:
            return quote.last_price
    raise ValueError(f"no quote for {symbol}")


def _worst_case_value(
    state: AccountState, market: MarketSnapshot, symbol: str, basket_buy_value: Decimal, cancelled: set[str]
) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (existing, pending_open, total_worst_case) for `symbol`, excluding
    `basket_buy_value` (added separately by the caller so it can be swapped out during
    repair sizing)."""
    existing = _existing_position_value(state, symbol)
    pending = _pending_open_buy_value(state, market, symbol, cancelled)
    return existing, pending, existing + pending + basket_buy_value


def _buy_symbols_in_order(plan: OrderPlan) -> list[str]:
    seen: list[str] = []
    for o in plan.orders:
        if is_buy(o) and o.symbol not in seen:
            seen.append(o.symbol)
    return seen


class ConcentrationRule:
    """Fails if any resulting position would exceed the configured max weight of equity."""

    id = "R3"
    name = "concentration"
    severity = Severity.BLOCKING
    always_repairable = False
    """Almost always repairable (resize down), but NOT unconditionally: if a symbol has
    more than one buy order in the basket, repair() skips it rather than guess how to
    split headroom (see repair()'s docstring note) -- a genuine no-fix case, unlike R4."""

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        cap = _cap_value(state, constraints)
        cancelled = set(plan.cancellations)

        for symbol in _buy_symbols_in_order(plan):
            basket_buy_value = sum(
                (order_notional_value(o, market) for o in plan.orders if is_buy(o) and o.symbol == symbol),
                start=Decimal(0),
            )
            existing, pending, total = _worst_case_value(state, market, symbol, basket_buy_value, cancelled)
            if total > cap:
                order_index = next(i for i, o in enumerate(plan.orders) if is_buy(o) and o.symbol == symbol)
                pct = (total / state.equity) * Decimal(100)
                cap_pct = constraints.max_position_pct
                pending_note = f" + {pending} from a pending open order not cancelled by this basket" if pending else ""
                return RuleResult(
                    rule_id=self.id,
                    rule_name=self.name,
                    passed=False,
                    severity=self.severity,
                    order_index=order_index,
                    explanation=(
                        f"{symbol} would reach {pct:.1f}% of equity ({total} of {state.equity}: "
                        f"{existing} held{pending_note} + {basket_buy_value} from this basket), "
                        f"exceeding your {cap_pct}% cap ({cap})"
                    ),
                )

        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=True,
            severity=self.severity,
            explanation=f"every resulting position stays at or under the {constraints.max_position_pct}% cap ({cap})",
        )

    def repair(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
        result: RuleResult,
    ) -> OrderPlan:
        cap = _cap_value(state, constraints)
        cancelled = set(plan.cancellations)
        new_orders = list(plan.orders)

        for symbol in _buy_symbols_in_order(plan):
            buy_indices = [i for i, o in enumerate(new_orders) if is_buy(o) and o.symbol == symbol]
            if len(buy_indices) != 1:
                # Multiple buy orders for the same symbol in one basket isn't a shape
                # any current case produces; skip rather than guess how to split headroom.
                continue
            i = buy_indices[0]
            order = new_orders[i]
            existing, pending, _ = _worst_case_value(state, market, symbol, Decimal(0), cancelled)
            headroom = cap - existing - pending
            current_value = order_notional_value(order, market)
            if current_value <= headroom:
                continue

            price = order_reference_price(order, market)
            capped_qty = floor_shares(headroom, price)
            if capped_qty <= 0:
                new_orders = [o for j, o in enumerate(new_orders) if j != i]
                continue
            new_orders[i] = order.model_copy(update={"qty": capped_qty, "notional": None})

        if tuple(new_orders) == plan.orders:
            return plan
        return plan.model_copy(update={"orders": tuple(new_orders)})
