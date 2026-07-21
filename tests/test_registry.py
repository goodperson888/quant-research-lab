from pathlib import Path

import pytest

from quant_lab.registry import list_factors, register_factor


def test_register_and_list_factor(tmp_path: Path) -> None:
    registry = tmp_path / "registry.sqlite3"
    register_factor(
        registry,
        factor_id="trend.ema_slope.20",
        name="EMA slope 20",
        category="trend",
    )
    rows = list(list_factors(registry))
    assert len(rows) == 1
    assert rows[0]["factor_id"] == "trend.ema_slope.20"
    assert rows[0]["status"] == "candidate"


def test_automatic_production_promotion_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="human approval"):
        register_factor(
            tmp_path / "registry.sqlite3",
            factor_id="trend.unsafe",
            name="Unsafe",
            category="trend",
            status="production",
        )
