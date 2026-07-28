from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from quant_lab.application.execution import (
    ConservativeExecutionModel,
    ConservativeExecutionPolicy,
    InstrumentExecutionRules,
    MaintenanceMarginTier,
)


class ExecutionModelCatalog:
    """Load reviewed execution policies from project configuration."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.config_path = (
            self.root
            / "configs/execution_models/conservative_crypto_perpetual_v1.yaml"
        )

    def list_summaries(self) -> tuple[dict[str, Any], ...]:
        config = self._read()
        return tuple(
            self._build(config, venue).summary()
            for venue in sorted(config["venues"])
        )

    def get(self, *, venue: str) -> ConservativeExecutionModel:
        return self._build(self._read(), venue)

    def _read(self) -> Mapping[str, Any]:
        if not self.config_path.is_file():
            raise ValueError("execution model config is unavailable")
        loaded = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("execution model config must be a YAML mapping")
        if loaded.get("schema_version") != 1:
            raise ValueError("unsupported execution model schema version")
        if loaded.get("status") != "active_research_only":
            raise ValueError("execution model must remain research-only")
        if loaded.get("scope", {}).get("live_trading_enabled") is not False:
            raise ValueError("execution model must explicitly disable live trading")
        return loaded

    def _build(
        self, config: Mapping[str, Any], venue: str
    ) -> ConservativeExecutionModel:
        venues = config.get("venues")
        if not isinstance(venues, dict) or venue not in venues:
            raise ValueError(f"execution model venue is unsupported: {venue}")
        defaults = config["defaults"]
        margin = config["maintenance_margin"]
        venue_config = venues[venue]
        policy = ConservativeExecutionPolicy(
            model_id=str(config["model_id"]),
            default_leverage=float(defaults["leverage"]),
            max_research_leverage=float(defaults["max_research_leverage"]),
            require_explicit_leverage_above_one=bool(
                defaults["require_explicit_leverage_above_one"]
            ),
            margin_mode=str(defaults["margin_mode"]),  # type: ignore[arg-type]
            taker_fee_per_side=float(defaults["taker_fee_per_side"]),
            maker_fee_per_side=float(defaults["maker_fee_per_side"]),
            slippage_bps_per_side=float(defaults["slippage_bps_per_side"]),
            stress_slippage_bps_per_side=float(
                defaults["stress_slippage_bps_per_side"]
            ),
            liquidation_fee_rate=float(defaults["liquidation_fee_rate"]),
            liquidation_buffer_rate=float(defaults["liquidation_buffer_rate"]),
            missing_funding_policy=str(
                defaults["missing_funding_policy"]
            ),  # type: ignore[arg-type]
            same_bar_priority=tuple(defaults["same_bar_priority"]),
            limit_fill_policy=str(defaults["limit_fill_policy"]),  # type: ignore[arg-type]
            partial_fill_policy=str(
                defaults["partial_fill_policy"]
            ),  # type: ignore[arg-type]
            partial_fill_fraction=float(defaults["partial_fill_fraction"]),
            tiers=tuple(
                MaintenanceMarginTier(
                    max_notional=(
                        float(item["max_notional"])
                        if item["max_notional"] is not None
                        else None
                    ),
                    maintenance_margin_rate=float(
                        item["maintenance_margin_rate"]
                    ),
                    maintenance_amount=float(item["maintenance_amount"]),
                )
                for item in margin["tiers"]
            ),
            tier_source=str(margin["source"]),
            historical_tiers_complete=bool(margin["historical_tiers_complete"]),
            limitations=tuple(config["limitations"]),
        )
        rules = InstrumentExecutionRules(
            venue=venue,
            market_profile=str(venue_config["market_profile"]),
            symbol=str(venue_config["symbol"]),
            quantity_unit=str(venue_config["quantity_unit"]),  # type: ignore[arg-type]
            contract_size_base=float(venue_config["contract_size_base"]),
            tick_size=float(venue_config["tick_size"]),
            quantity_step=float(venue_config["quantity_step"]),
            minimum_quantity=float(venue_config["minimum_quantity"]),
            maximum_quantity=(
                float(venue_config["maximum_quantity"])
                if venue_config["maximum_quantity"] is not None
                else None
            ),
            minimum_notional=float(venue_config["minimum_notional"]),
            venue_max_leverage_snapshot=(
                float(venue_config["venue_max_leverage_snapshot"])
                if venue_config["venue_max_leverage_snapshot"] is not None
                else None
            ),
            metadata_manifest_key=str(venue_config["metadata_manifest_key"]),
            leverage_tiers_status=str(venue_config["leverage_tiers_status"]),
        )
        return ConservativeExecutionModel(policy=policy, rules=rules)
