from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.pipeline import (
    PipelineApplicationService,
    PipelineProfileCatalog,
)
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import ApprovalRequiredError, GatePolicyError
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def build_services(tmp_path: Path):
    repository = SQLiteProductRepository(tmp_path / "runtime/app/pipeline.sqlite3")
    research = ResearchApplicationService(repository)
    pipeline = PipelineApplicationService(repository, PipelineProfileCatalog(ROOT))
    session = research.create_research_session(title="pipeline test")
    draft = research.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="A deterministic strategy source.",
    )
    baseline = research.freeze_baseline(
        draft_id=draft.id, confirmed_by_user=True
    )
    return repository, research, pipeline, baseline


def pass_fast_screen(pipeline, baseline_id: str):
    correctness = pipeline.evaluate_gate(
        profile_id="fast_screen",
        gate_name="correctness",
        subject_type="strategy_version",
        subject_id=baseline_id,
        market_profile="crypto_perpetual.binance.eth",
        strategy_objective="standalone",
        metrics={},
        checks={"lookahead_free": True, "cost_arithmetic": True},
    )
    fast = pipeline.evaluate_gate(
        profile_id="fast_screen",
        gate_name="fast_screen",
        subject_type="strategy_version",
        subject_id=baseline_id,
        market_profile="crypto_perpetual.binance.eth",
        strategy_objective="standalone",
        metrics={},
        checks={"manifest_written": True, "validation_split_used": True},
    )
    assert correctness.status == fast.status == "passed"
    return fast


def test_pipeline_profiles_and_viability_fail_fast(tmp_path: Path) -> None:
    repository, _research, pipeline, baseline = build_services(tmp_path)
    profiles = {profile.id: profile for profile in pipeline.list_profiles()}
    assert set(profiles) == {"smoke", "fast_screen", "full_validation"}
    assert profiles["smoke"].candidate_eligible is False
    assert (
        profiles["fast_screen"].pine_validation["pine_source"][
            "full_diagnostic_after"
        ]
        == "viability"
    )

    pass_fast_screen(pipeline, baseline.id)
    failed = pipeline.evaluate_gate(
        profile_id="fast_screen",
        gate_name="viability",
        subject_type="strategy_version",
        subject_id=baseline.id,
        market_profile="crypto_perpetual.binance.eth",
        strategy_objective="standalone",
        metrics={
            "validation_net_return": -0.0969,
            "validation_profit_factor": 0.68,
            "validation_expectancy": -0.0012,
            "validation_trade_count": 81,
            "validation_max_drawdown_abs": 0.1264,
        },
        checks={},
    )
    assert failed.status == "failed"
    assert {reason.split("=", 1)[0] for reason in failed.reasons} >= {
        "validation_net_return",
        "validation_profit_factor",
        "validation_expectancy",
    }
    with pytest.raises(GatePolicyError, match="passed viability"):
        pipeline.create_strategy_outcome(
            strategy_version_id=baseline.id,
            market_profile="crypto_perpetual.binance.eth",
            pipeline_profile_id="fast_screen",
            outcome_type="strategy_candidate",
            viability_gate_result_id=failed.id,
            evidence_artifact_keys=(),
            notes="Less loss is not standalone viability.",
        )

    outcome = pipeline.create_strategy_outcome(
        strategy_version_id=baseline.id,
        market_profile="crypto_perpetual.binance.eth",
        pipeline_profile_id="fast_screen",
        outcome_type="diagnostic_improvement",
        viability_gate_result_id=failed.id,
        evidence_artifact_keys=(),
        notes="Preserve diagnostic evidence without tradability claims.",
    )
    assert outcome.outcome_type == "diagnostic_improvement"
    assert repository.list_strategy_outcomes() == [outcome]


def test_full_stress_is_blocked_without_passed_viability(tmp_path: Path) -> None:
    _repository, research, pipeline, baseline = build_services(tmp_path)
    fast = pass_fast_screen(pipeline, baseline.id)
    with pytest.raises(ApprovalRequiredError, match="viability"):
        research.create_job(
            job_type="stress_test",
            payload={
                "stress_level": "full",
                "strategy_version_id": baseline.id,
                "viability_gate_result_id": fast.id,
            },
        )

    cheap = research.create_job(
        job_type="stress_test",
        payload={
            "stress_level": "cheap_cost_sensitivity",
            "strategy_version_id": baseline.id,
            "fast_screen_gate_result_id": fast.id,
        },
    )
    assert cheap.status == "queued"


def test_failed_strategy_component_can_remain_diagnostic_but_not_validated(
    tmp_path: Path,
) -> None:
    _repository, _research, pipeline, baseline = build_services(tmp_path)
    evidence = pipeline.create_component_evidence(
        source_strategy_version_id=baseline.id,
        lineage={"hypothesis": "confirmation entry reduces false entries"},
        component_type="entry",
        target_market_profile="crypto_perpetual.binance.eth",
        incremental_metrics={"validation_net_return_delta": 0.08},
        out_of_sample_status="screening",
        failure_conditions=({"metric": "cost_stress_passed", "value": False},),
    )
    candidate = pipeline.create_component_candidate(
        evidence_id=evidence.id,
        name="confirmation-candle entry",
        status="diagnostic_improvement",
    )
    assert candidate.status == "diagnostic_improvement"
    with pytest.raises(GatePolicyError, match="cannot be automatically marked validated"):
        pipeline.create_component_candidate(
            evidence_id=evidence.id,
            name="unsafe promotion",
            status="validated",
        )


def test_regime_validation_requires_ex_ante_labels_and_limits_90_day_claims(
    tmp_path: Path,
) -> None:
    _repository, _research, pipeline, baseline = build_services(tmp_path)
    common = {
        "subject_type": "strategy_version",
        "subject_id": baseline.id,
        "market_profile": "crypto_perpetual.binance.eth",
        "detector_version": "vol-trend-v1",
        "target_regimes": ["trend"],
        "suitable_regimes": [],
        "conditional_regimes": [],
        "blocked_regimes": [],
        "unknown_regimes": ["trend", "range"],
        "regime_metrics": {},
        "transition_policy": {"on_unknown": "block_promotion"},
        "history_days": 90,
    }
    with pytest.raises(ValueError, match="ex-ante observable"):
        pipeline.create_regime_validation(
            **common,
            ex_ante_observable=False,
            evidence_status="insufficient_history",
        )
    with pytest.raises(GatePolicyError, match="90-day"):
        pipeline.create_regime_validation(
            **common,
            ex_ante_observable=True,
            evidence_status="extended_validation",
        )
    screening = pipeline.create_regime_validation(
        **common,
        ex_ante_observable=True,
        evidence_status="insufficient_history",
    )
    assert screening.evidence_status == "insufficient_history"


def test_pipeline_api_and_explicit_approval_subject(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "configs/pipelines", tmp_path / "configs/pipelines")
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    profiles = client.get("/api/pipeline-profiles")
    assert profiles.status_code == 200
    assert {item["id"] for item in profiles.json()} == {
        "smoke",
        "fast_screen",
        "full_validation",
    }

    session = client.post("/api/research/sessions", json={"title": "subject test"}).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={"source_type": "natural_language", "raw_content": "rule"},
    ).json()
    mismatch = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"confirmed_by_user": True, "subject_id": "another_draft"},
    )
    assert mismatch.status_code == 400
    frozen = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"confirmed_by_user": True, "subject_id": draft["id"]},
    )
    assert frozen.status_code == 201
    assert client.get("/api/gates/results").status_code == 200
    assert client.get("/api/component-candidates").json() == []
    assert client.get("/api/component-evidence").json() == []
    assert client.get("/api/regime-validations").json() == []
