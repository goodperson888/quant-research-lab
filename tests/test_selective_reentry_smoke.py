from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quant_lab.application.guided_research import GuidedResearchService
from quant_lab.application.pipeline import (
    PipelineApplicationService,
    PipelineProfileCatalog,
)
from quant_lab.application.selective_reentry_smoke import (
    SelectiveReentrySmokeBacktester,
    resolve_funding_rate,
)
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import ApprovalRequiredError, InvalidJobError
from quant_lab.domain.models import (
    Constraint,
    Job,
    Objective,
    ParameterSpace,
)
from quant_lab.infrastructure.selective_reentry_smoke_runner import (
    NativeBacktestRouter,
)
from quant_lab.infrastructure.sqlite_product_repository import (
    SQLiteProductRepository,
)


ROOT = Path(__file__).resolve().parents[1]


def _ohlcv(start: str, end: str, freq: str) -> pd.DataFrame:
    timestamps = pd.date_range(start, end, freq=freq, inclusive="left")
    base = pd.Series(range(len(timestamps)), dtype=float) * 0.1 + 2000.0
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.2,
            "volume": 10.0,
        }
    )


def _candidate_and_gate(tmp_path: Path):
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service = ResearchApplicationService(repository)
    session = service.create_research_session(title="candidate smoke gate")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="closed-candle test strategy",
    )
    baseline = service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)
    guided = GuidedResearchService(repository)
    direction = guided.create_direction(
        baseline_version_id=baseline.id,
        subject_id=baseline.id,
        hypothesis="one correctness clarification preserves baseline intent",
        rule_diff={"clarification": "closed candles only"},
        evidence_refs=(),
        parameter_space=(
            ParameterSpace(name="clarification", kind="categorical", values=("v1",)),
        ),
        data_splits={
            "train": "unused",
            "validation": "smoke",
            "locked_test": "reserved_uninspected",
        },
        cost_model={"fee_per_side": 0.0005, "slippage_bps_per_side": 2.0},
        objectives=(Objective(metric="correctness_checks", direction="maximize"),),
        constraints=(Constraint(metric="locked_test_used", operator="eq", value=0),),
        estimated_trials=1,
        estimated_minutes=1,
        failure_conditions=("correctness fails",),
        stopping_conditions=("stop after smoke",),
        rollback_plan="retain baseline",
    )
    submitted = guided.submit_for_approval(direction.id)
    approved, candidate = guided.approve(
        proposal_id=submitted.id,
        subject_id=submitted.id,
        confirmed_by_user=True,
    )
    gate = PipelineApplicationService(
        repository, PipelineProfileCatalog(ROOT)
    ).evaluate_gate(
        profile_id="smoke",
        gate_name="correctness",
        subject_type="strategy_version",
        subject_id=candidate.id,
        market_profile="crypto_perpetual.binance.eth",
        strategy_objective="standalone",
        metrics={"locked_test_used": 0.0},
        checks={"closed_candles_only": True},
    )
    return service, session, approved, candidate, gate


def test_missing_funding_uses_directionally_adverse_nonzero_proxy() -> None:
    rates = pd.Series(
        [0.0001],
        index=pd.DatetimeIndex([pd.Timestamp("2025-07-27T00:00:00Z")]),
    )
    missing = pd.Timestamp("2025-07-27T08:00:00Z")
    assert resolve_funding_rate(
        timestamp=missing,
        funding_rates=rates,
        position_side="long",
        adverse_proxy_rate=0.0003,
    ) == (0.0003, True)
    assert resolve_funding_rate(
        timestamp=missing,
        funding_rates=rates,
        position_side="short",
        adverse_proxy_rate=0.0003,
    ) == (-0.0003, True)


def test_native_smoke_backtester_is_deterministic_and_does_not_claim_fast_screen() -> None:
    warmup = "2025-07-20T00:00:00Z"
    start = "2025-07-21T00:00:00Z"
    end = "2025-07-22T00:00:00Z"
    funding_times = pd.date_range(warmup, end, freq="8h", inclusive="left")
    funding = pd.DataFrame(
        {"timestamp": funding_times, "last_funding_rate": 0.0001}
    )
    mark = _ohlcv(warmup, end, "15min")
    result = SelectiveReentrySmokeBacktester(
        candidate_version_id="candidate_fixture",
        baseline_version_id="baseline_fixture",
    ).run(
        ohlcv_5m=_ohlcv(warmup, end, "5min"),
        ohlcv_15m=_ohlcv(warmup, end, "15min"),
        ohlcv_1h=_ohlcv(warmup, end, "1h"),
        funding=funding,
        mark_15m=mark,
        warmup_start_utc_inclusive=warmup,
        start_utc_inclusive=start,
        end_utc_exclusive=end,
    )
    assert result.data_quality["complete"] is True
    assert result.metrics["trade_count"] == 0
    assert result.execution["engine"] == (
        "quant_lab_native_selective_reentry_smoke_v1"
    )
    assert result.funding["zero_funding_fallback_used"] is False


def test_candidate_smoke_job_requires_exact_approval_gate_and_scope(
    tmp_path: Path,
) -> None:
    service, session, _proposal, candidate, gate = _candidate_and_gate(tmp_path)
    base_payload = {
        "intent": "candidate_smoke",
        "session_id": session.id,
        "strategy_version_id": candidate.id,
        "subject_id": candidate.id,
        "config_artifact_key": "configs/research/candidate-smoke.yaml",
        "agent_run_id": "agent_run_fixture",
        "correctness_gate_result_id": gate.id,
        "confirmed_by_user": True,
        "pipeline_profile": "smoke",
        "locked_test_used": False,
        "run_fast_screen": False,
    }
    queued = service.create_job(job_type="backtest", payload=base_payload)
    assert queued.status == "queued"

    with pytest.raises(ApprovalRequiredError, match="exact subject"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "subject_id": "version_wrong"},
        )
    with pytest.raises(InvalidJobError, match="locked-test"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "locked_test_used": True},
        )
    with pytest.raises(InvalidJobError, match="fast_screen"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "run_fast_screen": True},
        )


def test_native_router_preserves_baseline_compatibility() -> None:
    router = NativeBacktestRouter(
        baseline_runner=lambda _job: {"runner": "baseline"},
        candidate_smoke_runner=lambda _job: {"runner": "candidate_smoke"},  # type: ignore[arg-type]
        candidate_fast_screen_runner=lambda _job: {"runner": "candidate_fast_screen"},
    )
    common = {
        "id": "job_fixture",
        "job_type": "backtest",
        "status": "queued",
        "created_at": "2025-07-27T00:00:00+00:00",
        "updated_at": "2025-07-27T00:00:00+00:00",
    }
    baseline = Job(payload={"intent": "baseline_backtest"}, **common)  # type: ignore[arg-type]
    smoke = Job(payload={"intent": "candidate_smoke"}, **common)  # type: ignore[arg-type]
    fast_screen = Job(
        payload={"intent": "candidate_fast_screen"}, **common
    )  # type: ignore[arg-type]
    assert router(baseline) == {"runner": "baseline"}
    assert router(smoke) == {"runner": "candidate_smoke"}
    assert router(fast_screen) == {"runner": "candidate_fast_screen"}


def test_candidate_fast_screen_requires_exact_scope_and_excludes_locked_test(
    tmp_path: Path,
) -> None:
    service, session, _proposal, candidate, gate = _candidate_and_gate(tmp_path)
    base_payload = {
        "intent": "candidate_fast_screen",
        "session_id": session.id,
        "strategy_version_id": candidate.id,
        "subject_id": candidate.id,
        "config_artifact_key": "configs/research/candidate-fast-screen.yaml",
        "agent_run_id": "agent_run_fixture",
        "correctness_gate_result_id": gate.id,
        "smoke_manifest_artifact_key": "experiments/runs/smoke/manifest.json",
        "confirmed_by_user": True,
        "pipeline_profile": "fast_screen",
        "locked_test_used": False,
        "run_viability": False,
    }
    queued = service.create_job(job_type="backtest", payload=base_payload)
    assert queued.status == "queued"
    with pytest.raises(ApprovalRequiredError, match="exact subject"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "subject_id": "version_wrong"},
        )
    with pytest.raises(InvalidJobError, match="locked-test"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "locked_test_used": True},
        )
    with pytest.raises(InvalidJobError, match="viability"):
        service.create_job(
            job_type="backtest",
            payload={**base_payload, "run_viability": True},
        )
