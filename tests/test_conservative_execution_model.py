from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.infrastructure.execution_models import ExecutionModelCatalog
from quant_lab.interfaces.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_exposes_research_only_venue_models() -> None:
    catalog = ExecutionModelCatalog(ROOT)
    summaries = {item["venue"]: item for item in catalog.list_summaries()}

    assert set(summaries) == {"binance", "okx"}
    assert summaries["binance"]["default_leverage"] == 1.0
    assert summaries["binance"]["max_research_leverage"] == 50.0
    assert summaries["binance"]["live_trading_enabled"] is False
    assert summaries["binance"]["historical_tiers_complete"] is False
    assert summaries["okx"]["precision"]["quantity_unit"] == "contracts"


def test_market_order_applies_adverse_slippage_precision_and_margin() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    order = model.prepare_market_order(
        side="long",
        entering=True,
        raw_price=3000.0,
        requested_notional=500.0,
        account_equity=1000.0,
    )

    assert order.accepted is True
    assert order.execution_price == pytest.approx(3000.60)
    assert order.quantity == pytest.approx(0.166)
    assert order.notional == pytest.approx(498.0996)
    assert order.initial_margin == pytest.approx(order.notional)
    assert order.fee == pytest.approx(order.notional * 0.0005)
    assert order.slippage_cost > 0


def test_order_rejects_minimum_notional_and_unapproved_leverage() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    tiny = model.prepare_market_order(
        side="long",
        entering=True,
        raw_price=3000.0,
        requested_notional=10.0,
        account_equity=1000.0,
    )
    assert tiny.accepted is False
    assert tiny.rejection_reason == "minimum_notional"

    with pytest.raises(ValueError, match="explicit research setting"):
        model.prepare_market_order(
            side="long",
            entering=True,
            raw_price=3000.0,
            requested_notional=500.0,
            account_equity=1000.0,
            leverage=50.0,
            explicitly_requested_leverage=False,
        )


def test_50x_liquidation_is_mark_price_first_and_near_entry() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    order = model.prepare_market_order(
        side="long",
        entering=True,
        raw_price=3000.0,
        requested_notional=500.0,
        account_equity=1000.0,
        leverage=50.0,
        explicitly_requested_leverage=True,
    )
    liquidation = model.liquidation_price(
        side="long",
        entry_price=order.execution_price,
        quantity=order.quantity,
        leverage=50.0,
    )
    assert liquidation is not None
    assert order.execution_price * 0.99 < liquidation < order.execution_price

    decision = model.select_intrabar_exit(
        side="long",
        bar_open=3000.0,
        bar_high=3060.0,
        bar_low=2940.0,
        stop_price=2970.0,
        take_profit_price=3040.0,
        liquidation_price=liquidation,
        mark_open=3000.0,
        mark_high=3050.0,
        mark_low=liquidation - 1.0,
    )
    assert decision.reason == "liquidation"
    assert decision.mark_price_used is True
    assert decision.mark_price_fallback_used is False


def test_stop_gap_uses_adverse_open_and_one_x_has_no_liquidation() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    assert (
        model.liquidation_price(
            side="long",
            entry_price=3000.0,
            quantity=0.1,
            leverage=1.0,
        )
        is None
    )
    decision = model.select_intrabar_exit(
        side="long",
        bar_open=2950.0,
        bar_high=2990.0,
        bar_low=2940.0,
        stop_price=2970.0,
        take_profit_price=3050.0,
        liquidation_price=None,
    )
    assert decision.reason == "protective_stop"
    assert decision.raw_price == 2950.0


def test_limit_touch_does_not_assume_fill_and_cross_uses_partial_fraction() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    touch = model.limit_fill_decision(
        side="long",
        entering=True,
        limit_price=3000.0,
        bar_open=3001.0,
        bar_high=3005.0,
        bar_low=3000.0,
    )
    crossed = model.limit_fill_decision(
        side="long",
        entering=True,
        limit_price=3000.0,
        bar_open=3001.0,
        bar_high=3005.0,
        bar_low=2999.99,
    )
    assert touch.touched is True
    assert touch.filled is False
    assert touch.reason == "touch_only_no_fill"
    assert crossed.filled is True
    assert crossed.fill_fraction == 0.5


def test_funding_gap_is_adverse_and_never_silently_zero() -> None:
    model = ExecutionModelCatalog(ROOT).get(venue="binance")
    long_pnl, imputed = model.funding_pnl(
        side="long",
        quantity=0.2,
        mark_price=3000.0,
        funding_rate=None,
        adverse_proxy_rate=0.0002,
    )
    short_pnl, short_imputed = model.funding_pnl(
        side="short",
        quantity=0.2,
        mark_price=3000.0,
        funding_rate=None,
        adverse_proxy_rate=0.0002,
    )
    assert long_pnl < 0
    assert short_pnl < 0
    assert imputed is True
    assert short_imputed is True
    with pytest.raises(ValueError, match="positive adverse proxy"):
        model.funding_pnl(
            side="long",
            quantity=0.2,
            mark_price=3000.0,
            funding_rate=None,
            adverse_proxy_rate=0.0,
        )


def test_execution_model_api_is_read_only_and_has_no_trade_surface(
    tmp_path: Path,
) -> None:
    target = (
        tmp_path
        / "configs/execution_models/conservative_crypto_perpetual_v1.yaml"
    )
    target.parent.mkdir(parents=True)
    shutil.copy(
        ROOT / "configs/execution_models/conservative_crypto_perpetual_v1.yaml",
        target,
    )
    app = create_app(
        root=tmp_path,
        database_path=tmp_path / "runtime/app/api.sqlite3",
    )
    client = TestClient(app)
    response = client.get("/api/execution-models")
    assert response.status_code == 200
    assert {item["venue"] for item in response.json()} == {"binance", "okx"}
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/execution-models" in paths
    assert all("/trade" not in path and "/live" not in path for path in paths)
