from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Literal, Mapping


Side = Literal["long", "short"]
QuantityUnit = Literal["base_asset", "contracts"]


@dataclass(frozen=True, slots=True)
class MaintenanceMarginTier:
    max_notional: float | None
    maintenance_margin_rate: float
    maintenance_amount: float = 0.0

    def __post_init__(self) -> None:
        if self.max_notional is not None and self.max_notional <= 0:
            raise ValueError("maintenance tier max_notional must be positive")
        if not 0 <= self.maintenance_margin_rate < 1:
            raise ValueError("maintenance margin rate must be in [0, 1)")
        if self.maintenance_amount < 0:
            raise ValueError("maintenance amount must be non-negative")


@dataclass(frozen=True, slots=True)
class InstrumentExecutionRules:
    venue: str
    market_profile: str
    symbol: str
    quantity_unit: QuantityUnit
    contract_size_base: float
    tick_size: float
    quantity_step: float
    minimum_quantity: float
    maximum_quantity: float | None
    minimum_notional: float
    venue_max_leverage_snapshot: float | None
    metadata_manifest_key: str
    leverage_tiers_status: str

    def __post_init__(self) -> None:
        if self.quantity_unit not in {"base_asset", "contracts"}:
            raise ValueError("unsupported quantity unit")
        if min(
            self.contract_size_base,
            self.tick_size,
            self.quantity_step,
            self.minimum_quantity,
        ) <= 0:
            raise ValueError("execution precision values must be positive")
        if self.maximum_quantity is not None and (
            self.maximum_quantity < self.minimum_quantity
        ):
            raise ValueError("maximum quantity is below minimum quantity")
        if self.minimum_notional < 0:
            raise ValueError("minimum notional must be non-negative")


@dataclass(frozen=True, slots=True)
class ConservativeExecutionPolicy:
    model_id: str
    default_leverage: float
    max_research_leverage: float
    require_explicit_leverage_above_one: bool
    margin_mode: Literal["isolated"]
    taker_fee_per_side: float
    maker_fee_per_side: float
    slippage_bps_per_side: float
    stress_slippage_bps_per_side: float
    liquidation_fee_rate: float
    liquidation_buffer_rate: float
    missing_funding_policy: Literal["adverse_nonzero_proxy"]
    same_bar_priority: tuple[str, ...]
    limit_fill_policy: Literal["no_fill_on_touch"]
    partial_fill_policy: Literal["conservative_fraction"]
    partial_fill_fraction: float
    tiers: tuple[MaintenanceMarginTier, ...]
    tier_source: str
    historical_tiers_complete: bool
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.margin_mode != "isolated":
            raise ValueError("v1 supports isolated margin only")
        if not 1 <= self.default_leverage <= self.max_research_leverage:
            raise ValueError("invalid default/max leverage")
        for value in (
            self.taker_fee_per_side,
            self.maker_fee_per_side,
            self.slippage_bps_per_side,
            self.stress_slippage_bps_per_side,
            self.liquidation_fee_rate,
            self.liquidation_buffer_rate,
        ):
            if value < 0:
                raise ValueError("execution cost values must be non-negative")
        if not 0 < self.partial_fill_fraction <= 1:
            raise ValueError("partial fill fraction must be in (0, 1]")
        if self.same_bar_priority[:2] != ("liquidation", "protective_stop"):
            raise ValueError("conservative priority must place liquidation before stop")
        if not self.tiers or self.tiers[-1].max_notional is not None:
            raise ValueError("maintenance tiers require a final open-ended tier")


@dataclass(frozen=True, slots=True)
class PreparedOrder:
    accepted: bool
    rejection_reason: str | None
    side: Side
    entering: bool
    raw_price: float
    execution_price: float
    quantity: float
    base_quantity: float
    notional: float
    initial_margin: float
    fee: float
    slippage_cost: float
    leverage: float


@dataclass(frozen=True, slots=True)
class MarginSnapshot:
    side: Side
    entry_price: float
    mark_price: float
    quantity: float
    base_quantity: float
    notional: float
    initial_margin: float
    maintenance_margin: float
    unrealized_pnl: float
    margin_balance: float
    liquidation_fee_reserve: float
    liquidation_price: float | None
    liquidated: bool
    leverage: float


@dataclass(frozen=True, slots=True)
class IntrabarExitDecision:
    reason: Literal["liquidation", "protective_stop", "take_profit"] | None
    raw_price: float | None
    trigger_price: float | None
    mark_price_used: bool
    mark_price_fallback_used: bool


@dataclass(frozen=True, slots=True)
class LimitFillDecision:
    touched: bool
    filled: bool
    fill_fraction: float
    reason: str


class ConservativeExecutionModel:
    """Deterministic, conservative linear-perpetual execution semantics.

    The model is a research component. It never submits orders and never claims an
    account-specific exchange tier is known when the config marks it as assumed.
    """

    def __init__(
        self,
        *,
        policy: ConservativeExecutionPolicy,
        rules: InstrumentExecutionRules,
    ) -> None:
        self.policy = policy
        self.rules = rules

    def summary(self) -> dict[str, Any]:
        return {
            "model_id": self.policy.model_id,
            "venue": self.rules.venue,
            "market_profile": self.rules.market_profile,
            "symbol": self.rules.symbol,
            "status": "research_only",
            "default_leverage": self.policy.default_leverage,
            "max_research_leverage": self.policy.max_research_leverage,
            "live_trading_enabled": False,
            "margin_mode": self.policy.margin_mode,
            "mark_price_liquidation": True,
            "same_bar_priority": list(self.policy.same_bar_priority),
            "funding_policy": self.policy.missing_funding_policy,
            "leverage_tiers_status": self.rules.leverage_tiers_status,
            "tier_source": self.policy.tier_source,
            "historical_tiers_complete": self.policy.historical_tiers_complete,
            "precision": {
                "tick_size": self.rules.tick_size,
                "quantity_step": self.rules.quantity_step,
                "minimum_quantity": self.rules.minimum_quantity,
                "minimum_notional": self.rules.minimum_notional,
                "quantity_unit": self.rules.quantity_unit,
                "contract_size_base": self.rules.contract_size_base,
            },
            "limitations": list(self.policy.limitations),
        }

    def validate_leverage(self, leverage: float, *, explicitly_requested: bool) -> None:
        if not 1 <= leverage <= self.policy.max_research_leverage:
            raise ValueError(
                f"research leverage must be between 1 and "
                f"{self.policy.max_research_leverage:g}"
            )
        if (
            leverage > 1
            and self.policy.require_explicit_leverage_above_one
            and not explicitly_requested
        ):
            raise ValueError("leverage above 1 requires an explicit research setting")
        if (
            self.rules.venue_max_leverage_snapshot is not None
            and leverage > self.rules.venue_max_leverage_snapshot
        ):
            raise ValueError("leverage exceeds the venue metadata snapshot")

    def prepare_market_order(
        self,
        *,
        side: Side,
        entering: bool,
        raw_price: float,
        requested_notional: float,
        account_equity: float,
        leverage: float | None = None,
        explicitly_requested_leverage: bool = False,
        stress_slippage: bool = False,
    ) -> PreparedOrder:
        selected_leverage = leverage or self.policy.default_leverage
        self.validate_leverage(
            selected_leverage,
            explicitly_requested=explicitly_requested_leverage,
        )
        if side not in {"long", "short"}:
            raise ValueError("unsupported side")
        if min(raw_price, requested_notional, account_equity) <= 0:
            raise ValueError("price, notional and equity must be positive")
        execution_price = self.market_execution_price(
            raw_price=raw_price,
            side=side,
            entering=entering,
            stress_slippage=stress_slippage,
        )
        requested_base = requested_notional / execution_price
        requested_quantity = requested_base / self.rules.contract_size_base
        quantity = self._floor_step(requested_quantity, self.rules.quantity_step)
        base_quantity = quantity * self.rules.contract_size_base
        notional = base_quantity * execution_price
        reason = self._order_rejection_reason(
            quantity=quantity,
            notional=notional,
            account_equity=account_equity,
            leverage=selected_leverage,
        )
        initial_margin = notional / selected_leverage
        fee = notional * self.policy.taker_fee_per_side
        slippage_cost = abs(base_quantity * (execution_price - raw_price))
        return PreparedOrder(
            accepted=reason is None,
            rejection_reason=reason,
            side=side,
            entering=entering,
            raw_price=raw_price,
            execution_price=execution_price,
            quantity=quantity,
            base_quantity=base_quantity,
            notional=notional,
            initial_margin=initial_margin,
            fee=fee,
            slippage_cost=slippage_cost,
            leverage=selected_leverage,
        )

    def market_execution_price(
        self,
        *,
        raw_price: float,
        side: Side,
        entering: bool,
        stress_slippage: bool = False,
    ) -> float:
        bps = (
            self.policy.stress_slippage_bps_per_side
            if stress_slippage
            else self.policy.slippage_bps_per_side
        )
        fraction = bps / 10_000
        buy = (side == "long") == entering
        adjusted = raw_price * (1 + fraction if buy else 1 - fraction)
        return self._adverse_price_tick(adjusted, buy=buy)

    def liquidation_price(
        self,
        *,
        side: Side,
        entry_price: float,
        quantity: float,
        leverage: float,
    ) -> float | None:
        self.validate_leverage(leverage, explicitly_requested=leverage > 1)
        if entry_price <= 0 or quantity <= 0:
            raise ValueError("entry price and quantity must be positive")
        if leverage <= 1:
            return None
        notional = self.notional(quantity=quantity, price=entry_price)
        tier = self.maintenance_tier(notional)
        base_quantity = quantity * self.rules.contract_size_base
        maintenance_amount_per_base = tier.maintenance_amount / base_quantity
        reserve_rate = (
            tier.maintenance_margin_rate
            + self.policy.liquidation_fee_rate
            + self.policy.liquidation_buffer_rate
        )
        if side == "long":
            raw = (
                entry_price * (1 - 1 / leverage) - maintenance_amount_per_base
            ) / (1 - reserve_rate)
            return self._floor_price(raw)
        if side == "short":
            raw = (
                entry_price * (1 + 1 / leverage) + maintenance_amount_per_base
            ) / (1 + reserve_rate)
            return self._ceil_price(raw)
        raise ValueError("unsupported side")

    def margin_snapshot(
        self,
        *,
        side: Side,
        entry_price: float,
        mark_price: float,
        quantity: float,
        leverage: float,
    ) -> MarginSnapshot:
        base_quantity = quantity * self.rules.contract_size_base
        notional = base_quantity * mark_price
        entry_notional = base_quantity * entry_price
        initial_margin = entry_notional / leverage
        tier = self.maintenance_tier(notional)
        maintenance_margin = max(
            0.0,
            notional * tier.maintenance_margin_rate - tier.maintenance_amount,
        )
        direction = 1 if side == "long" else -1
        unrealized_pnl = direction * base_quantity * (mark_price - entry_price)
        margin_balance = initial_margin + unrealized_pnl
        liquidation_fee_reserve = notional * (
            self.policy.liquidation_fee_rate + self.policy.liquidation_buffer_rate
        )
        threshold = maintenance_margin + liquidation_fee_reserve
        return MarginSnapshot(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            quantity=quantity,
            base_quantity=base_quantity,
            notional=notional,
            initial_margin=initial_margin,
            maintenance_margin=maintenance_margin,
            unrealized_pnl=unrealized_pnl,
            margin_balance=margin_balance,
            liquidation_fee_reserve=liquidation_fee_reserve,
            liquidation_price=self.liquidation_price(
                side=side,
                entry_price=entry_price,
                quantity=quantity,
                leverage=leverage,
            ),
            liquidated=margin_balance <= threshold,
            leverage=leverage,
        )

    def select_intrabar_exit(
        self,
        *,
        side: Side,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        stop_price: float | None,
        take_profit_price: float | None,
        liquidation_price: float | None,
        mark_open: float | None = None,
        mark_high: float | None = None,
        mark_low: float | None = None,
    ) -> IntrabarExitDecision:
        mark_complete = None not in (mark_open, mark_high, mark_low)
        check_open = float(mark_open) if mark_complete else bar_open
        check_high = float(mark_high) if mark_complete else bar_high
        check_low = float(mark_low) if mark_complete else bar_low
        fallback = liquidation_price is not None and not mark_complete
        liquidation_touched = (
            liquidation_price is not None
            and (
                check_low <= liquidation_price
                if side == "long"
                else check_high >= liquidation_price
            )
        )
        if liquidation_touched:
            raw = (
                min(check_open, liquidation_price)
                if side == "long"
                else max(check_open, liquidation_price)
            )
            return IntrabarExitDecision(
                reason="liquidation",
                raw_price=raw,
                trigger_price=liquidation_price,
                mark_price_used=mark_complete,
                mark_price_fallback_used=fallback,
            )
        stop_touched = (
            stop_price is not None
            and (bar_low <= stop_price if side == "long" else bar_high >= stop_price)
        )
        if stop_touched:
            raw = min(bar_open, stop_price) if side == "long" else max(bar_open, stop_price)
            return IntrabarExitDecision(
                reason="protective_stop",
                raw_price=raw,
                trigger_price=stop_price,
                mark_price_used=False,
                mark_price_fallback_used=fallback,
            )
        target_touched = (
            take_profit_price is not None
            and (
                bar_high >= take_profit_price
                if side == "long"
                else bar_low <= take_profit_price
            )
        )
        if target_touched:
            return IntrabarExitDecision(
                reason="take_profit",
                raw_price=take_profit_price,
                trigger_price=take_profit_price,
                mark_price_used=False,
                mark_price_fallback_used=fallback,
            )
        return IntrabarExitDecision(
            reason=None,
            raw_price=None,
            trigger_price=None,
            mark_price_used=mark_complete,
            mark_price_fallback_used=fallback,
        )

    def limit_fill_decision(
        self,
        *,
        side: Side,
        entering: bool,
        limit_price: float,
        bar_open: float,
        bar_high: float,
        bar_low: float,
    ) -> LimitFillDecision:
        """Reject touch-only fills and apply a deterministic partial-fill haircut."""

        buy = (side == "long") == entering
        touched = bar_low <= limit_price if buy else bar_high >= limit_price
        if not touched:
            return LimitFillDecision(
                touched=False,
                filled=False,
                fill_fraction=0.0,
                reason="limit_not_touched",
            )
        marketable_at_open = bar_open <= limit_price if buy else bar_open >= limit_price
        crossed = (
            bar_low <= limit_price - self.rules.tick_size
            if buy
            else bar_high >= limit_price + self.rules.tick_size
        )
        if marketable_at_open:
            return LimitFillDecision(
                touched=True,
                filled=True,
                fill_fraction=1.0,
                reason="marketable_at_bar_open",
            )
        if not crossed:
            return LimitFillDecision(
                touched=True,
                filled=False,
                fill_fraction=0.0,
                reason="touch_only_no_fill",
            )
        return LimitFillDecision(
            touched=True,
            filled=True,
            fill_fraction=self.policy.partial_fill_fraction,
            reason="crossed_limit_conservative_partial_fill",
        )

    def funding_pnl(
        self,
        *,
        side: Side,
        quantity: float,
        mark_price: float,
        funding_rate: float | None,
        adverse_proxy_rate: float,
    ) -> tuple[float, bool]:
        if funding_rate is None:
            if adverse_proxy_rate <= 0:
                raise ValueError("missing funding requires a positive adverse proxy")
            rate = adverse_proxy_rate if side == "long" else -adverse_proxy_rate
            imputed = True
        else:
            rate = funding_rate
            imputed = False
        direction = 1 if side == "long" else -1
        return -direction * rate * self.notional(quantity=quantity, price=mark_price), imputed

    def liquidation_cost(self, *, quantity: float, price: float) -> float:
        return self.notional(quantity=quantity, price=price) * self.policy.liquidation_fee_rate

    def notional(self, *, quantity: float, price: float) -> float:
        return quantity * self.rules.contract_size_base * price

    def maintenance_tier(self, notional: float) -> MaintenanceMarginTier:
        if notional < 0:
            raise ValueError("notional must be non-negative")
        for tier in self.policy.tiers:
            if tier.max_notional is None or notional <= tier.max_notional:
                return tier
        raise AssertionError("open-ended maintenance tier is missing")

    def _order_rejection_reason(
        self,
        *,
        quantity: float,
        notional: float,
        account_equity: float,
        leverage: float,
    ) -> str | None:
        if quantity < self.rules.minimum_quantity:
            return "minimum_quantity"
        if (
            self.rules.maximum_quantity is not None
            and quantity > self.rules.maximum_quantity
        ):
            return "maximum_quantity"
        if notional < self.rules.minimum_notional:
            return "minimum_notional"
        required_cash = notional / leverage + notional * self.policy.taker_fee_per_side
        if required_cash > account_equity:
            return "insufficient_margin"
        return None

    def _adverse_price_tick(self, value: float, *, buy: bool) -> float:
        return self._ceil_price(value) if buy else self._floor_price(value)

    def _floor_price(self, value: float) -> float:
        return self._floor_step(value, self.rules.tick_size)

    def _ceil_price(self, value: float) -> float:
        return self._ceil_step(value, self.rules.tick_size)

    @staticmethod
    def _floor_step(value: float, step: float) -> float:
        decimal_value = Decimal(str(value))
        decimal_step = Decimal(str(step))
        units = (decimal_value / decimal_step).to_integral_value(rounding=ROUND_FLOOR)
        return float(units * decimal_step)

    @staticmethod
    def _ceil_step(value: float, step: float) -> float:
        decimal_value = Decimal(str(value))
        decimal_step = Decimal(str(step))
        units = (decimal_value / decimal_step).to_integral_value(rounding=ROUND_CEILING)
        return float(units * decimal_step)


def execution_policy_as_dict(
    policy: ConservativeExecutionPolicy,
) -> Mapping[str, Any]:
    return asdict(policy)
