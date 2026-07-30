from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_two_year_download_template_is_disabled_append_only_plan() -> None:
    config = yaml.safe_load(
        (
            ROOT
            / "configs/data_downloads/binance_ethusdt_perpetual_2y.example.yaml"
        ).read_text(encoding="utf-8")
    )

    assert config["enabled"] is False
    assert config["plan_only_default"] is True
    assert config["approval"]["public_network_download_authorized_by_user"] is False
    assert config["range"] == {
        "start_utc_inclusive": "2024-07-20T00:00:00Z",
        "end_utc_exclusive": "2026-07-20T00:00:00Z",
    }
    assert config["storage"]["raw_write_mode"] == "append_only"
    assert config["storage"]["overwrite_existing_one_year_data"] is False


def test_rolling_validation_template_keeps_search_out_of_locked_test() -> None:
    config = yaml.safe_load(
        (
            ROOT / "configs/validation/eth_perpetual_rolling_2y.example.yaml"
        ).read_text(encoding="utf-8")
    )

    assert config["enabled"] is False
    assert config["method"] == "rolling_walk_forward"
    assert config["timestamp_semantics"] == "utc_closed_bar_only"
    assert config["search"]["train_and_validation_only"] is True
    assert (
        config["search"]["validation_reuse_after_hypothesis_generation"]
        == "screening_contaminated"
    )
    assert config["locked_test"]["search_allowed"] is False
    assert config["locked_test"]["exact_subject_approval_required"] is True
