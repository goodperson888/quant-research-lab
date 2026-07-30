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
        "mode": "regime_diagnostic",
    }
    with pytest.raises(GatePolicyError, match="ex-ante observable"):
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


def test_component_candidate_soft_archive_preserves_evidence_and_audit(
    tmp_path: Path,
) -> None:
    shutil.copytree(ROOT / "configs/pipelines", tmp_path / "configs/pipelines")
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    session = client.post("/api/research/sessions", json={"title": "archive test"}).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={"source_type": "natural_language", "raw_content": "rule"},
    ).json()
    baseline = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"confirmed_by_user": True, "subject_id": draft["id"]},
    ).json()
    created = client.post(
        "/api/component-candidates",
        json={
            "source_strategy_version_id": baseline["id"],
            "lineage": {"source": "diagnostic"},
            "component_type": "filter",
            "target_market_profile": "crypto_perpetual.binance.eth",
            "incremental_metrics": {"validation_net_return_delta": 0.01},
            "out_of_sample_status": "screening",
            "failure_conditions": [],
            "name": "minimum reward filter",
            "status": "diagnostic_improvement",
        },
    ).json()

    mismatch = client.post(
        f"/api/component-candidates/{created['id']}/archive",
        json={
            "subject_id": "component_other",
            "confirmed_by_user": True,
            "reason": "user archived",
        },
    )
    assert mismatch.status_code == 400
    archived = client.post(
        f"/api/component-candidates/{created['id']}/archive",
        json={
            "subject_id": created["id"],
            "confirmed_by_user": True,
            "reason": "user archived",
        },
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert client.get("/api/component-candidates").json() == []
    assert len(client.get("/api/component-evidence").json()) == 1
    assert len(
        client.get("/api/component-candidates?include_archived=true").json()
    ) == 1

    restored = client.post(
        f"/api/component-candidates/{created['id']}/restore",
        json={
            "subject_id": created["id"],
            "confirmed_by_user": True,
            "reason": "user restored",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    events = client.get("/api/audit/events?limit=100").json()
    assert {item["event_type"] for item in events} >= {
        "component_candidate.archived",
        "component_candidate.restored",
    }


def test_engine_reconciliation_entry_requires_viability_and_never_fakes_job(
    tmp_path: Path,
) -> None:
    shutil.copytree(ROOT / "configs/pipelines", tmp_path / "configs/pipelines")
    app = create_app(
        root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3"
    )
    client = TestClient(app)
    session = client.post("/api/research/sessions", json={"title": "engine test"}).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={"source_type": "natural_language", "raw_content": "rule"},
    ).json()
    baseline = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"confirmed_by_user": True, "subject_id": draft["id"]},
    ).json()
    market = "crypto_perpetual.binance.eth"

    blocked = client.post(
        "/api/engine-reconciliation/jobs",
        json={
            "subject_id": baseline["id"],
            "market_profile": market,
            "native_run_bundle_id": "report_fixture",
            "confirmed_by_user": True,
            "locked_test_used": False,
        },
    )
    assert blocked.status_code == 400
    assert "尚未通过可行性" in blocked.json()["detail"]

    pipeline = app.state.pipeline_service
    pass_fast_screen(pipeline, baseline["id"])
    viability = pipeline.evaluate_gate(
        profile_id="fast_screen",
        gate_name="viability",
        subject_type="strategy_version",
        subject_id=baseline["id"],
        market_profile=market,
        strategy_objective="standalone",
        metrics={
            "validation_net_return": 0.02,
            "validation_profit_factor": 1.3,
            "validation_expectancy": 0.001,
            "validation_trade_count": 80,
            "validation_max_drawdown_abs": 0.05,
        },
        checks={},
    )
    assert viability.status == "passed"
    client.post(
        "/api/strategy-outcomes",
        json={
            "strategy_version_id": baseline["id"],
            "market_profile": "crypto_perpetual.other_venue.eth",
            "pipeline_profile_id": "fast_screen",
            "outcome_type": "rejected",
            "notes": "A rejection in another market profile must remain isolated.",
        },
    )
    status_response = client.get(
        "/api/engine-reconciliation/status",
        params={"subject_id": baseline["id"], "market_profile": market},
    )
    assert status_response.json()["eligible"] is True
    assert status_response.json()["implementation_status"] == "not_connected"

    unavailable = client.post(
        "/api/engine-reconciliation/jobs",
        json={
            "subject_id": baseline["id"],
            "market_profile": market,
            "native_run_bundle_id": "report_fixture",
            "confirmed_by_user": True,
            "locked_test_used": False,
        },
    )
    assert unavailable.status_code == 409
    assert "no Job was created" in unavailable.json()["detail"]
    assert client.get("/api/jobs").json() == []

    same_market_rejection = client.post(
        "/api/strategy-outcomes",
        json={
            "strategy_version_id": baseline["id"],
            "market_profile": market,
            "pipeline_profile_id": "fast_screen",
            "outcome_type": "rejected",
            "notes": "Same-market rejection blocks reconciliation.",
        },
    )
    assert same_market_rejection.status_code == 201
    blocked_status = client.get(
        "/api/engine-reconciliation/status",
        params={"subject_id": baseline["id"], "market_profile": market},
    ).json()
    assert blocked_status["eligible"] is False
    assert "已被拒绝" in blocked_status["reason"]
