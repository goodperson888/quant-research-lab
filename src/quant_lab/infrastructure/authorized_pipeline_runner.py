from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from quant_lab.application.pipeline import (
    PipelineApplicationService,
    PipelineProfileCatalog,
)
from quant_lab.application.research_authorization import (
    CORE_STAGE_ORDER,
    DIAGNOSTIC_STAGE_ORDER,
    ResearchAuthorizationService,
)
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import Job
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore
from .strategy_evaluators import StrategyPluginRegistry, StrategySpec


class AuthorizedPipelineRunner:
    """Run the reviewed baseline path covered by one exact-subject authorization."""

    def __init__(
        self,
        root: Path,
        repository: ProductRepository,
        strategy_plugins: StrategyPluginRegistry,
    ) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.strategy_plugins = strategy_plugins
        self.artifacts = LocalArtifactStore(self.root)
        self.authorizations = ResearchAuthorizationService(repository)
        self.pipeline = PipelineApplicationService(
            repository, PipelineProfileCatalog(self.root)
        )
        self.research = ResearchApplicationService(repository)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        authorization_id = str(job.payload.get("authorization_id", ""))
        authorization = self.authorizations.assert_scope_covers(
            authorization_id,
            subject_id=str(job.payload.get("subject_id", "")),
            stages=CORE_STAGE_ORDER,
        )
        if job.payload.get("locked_test_used") is not False:
            raise ValueError("authorized pipeline must explicitly exclude locked test")
        if authorization.auto_continue is not True:
            raise ValueError("pipeline_execution requires auto_continue authorization")
        if self.authorizations.stages(authorization.id):
            raise ValueError("authorization already has stage evidence; use explicit retry")

        version = self.repository.get_strategy_version(authorization.subject_id)
        try:
            spec = self.strategy_plugins.find_for_snapshot(version.content_snapshot)
        except ValueError as exc:
            self._record_stage(
                authorization.id,
                authorization.subject_id,
                "correctness",
                "blocked",
                reason=str(exc),
            )
            return {
                "status": "blocked",
                "reason": str(exc),
                "locked_test_used": False,
            }

        config_key = (
            spec.config_resolver(version, authorization.session_id)
            if spec.config_resolver is not None
            else spec.config_artifact_key
        )
        config, correctness = self._evaluate_correctness(
            authorization_id=authorization.id,
            session_id=authorization.session_id,
            version=version,
            spec=spec,
            config_artifact_key=config_key,
        )
        self._record_stage(
            authorization.id,
            authorization.subject_id,
            "correctness",
            correctness.status,
            reason="；".join(correctness.reasons),
        )
        if correctness.status != "passed":
            return {
                "status": "stopped",
                "stopped_at": "correctness",
                "gate_result_id": correctness.id,
                "reason": "；".join(correctness.reasons),
                "locked_test_used": False,
            }

        smoke_result = self._run_backtest_stage(
            job=job,
            spec=spec,
            stage="smoke",
            intent=spec.smoke_intent,
            authorization_id=authorization.id,
            session_id=authorization.session_id,
            subject_id=authorization.subject_id,
            config_artifact_key=str(config_key),
            correctness_gate_result_id=correctness.id,
        )
        smoke_manifest = str(smoke_result["manifest_artifact_key"])
        self._record_stage(
            authorization.id,
            authorization.subject_id,
            "smoke",
            "passed",
            evidence_refs=(smoke_manifest,),
            reason="reviewed Native smoke run completed without locked-test use",
        )

        fast_result = self._run_backtest_stage(
            job=job,
            spec=spec,
            stage="fast_screen",
            intent=spec.fast_screen_intent,
            authorization_id=authorization.id,
            session_id=authorization.session_id,
            subject_id=authorization.subject_id,
            config_artifact_key=str(config_key),
            correctness_gate_result_id=correctness.id,
            smoke_manifest_artifact_key=smoke_manifest,
        )
        fast_manifest = str(fast_result["manifest_artifact_key"])
        fast_gate = self.pipeline.evaluate_gate(
            profile_id=spec.pipeline_profile,
            gate_name="fast_screen",
            subject_type="strategy_version",
            subject_id=authorization.subject_id,
            market_profile=str(spec.market_profile),
            strategy_objective="standalone",
            metrics={},
            checks={
                "saved_manifest": self.artifacts.exists(fast_manifest),
                "smoke_evidence_same_subject": True,
                "locked_test_excluded": fast_result.get("locked_test_used") is False,
            },
        )
        self._record_stage(
            authorization.id,
            authorization.subject_id,
            "fast_screen",
            fast_gate.status,
            evidence_refs=(fast_manifest,),
            reason="；".join(fast_gate.reasons),
        )
        if fast_gate.status != "passed":
            return {
                "status": "stopped",
                "stopped_at": "fast_screen",
                "gate_result_id": fast_gate.id,
                "reason": "；".join(fast_gate.reasons),
                "locked_test_used": False,
            }

        validation = dict(
            fast_result.get("summary", {}).get("metrics", {}).get("validation", {})
        )
        viability_metrics = {
            "validation_net_return": float(validation["total_return"]),
            "validation_profit_factor": float(validation["profit_factor"]),
            "validation_expectancy": float(validation["expectancy"]),
            "validation_trade_count": float(validation["trade_count"]),
            "validation_max_drawdown_abs": abs(float(validation["max_drawdown"])),
        }
        viability = self.pipeline.evaluate_gate(
            profile_id=spec.pipeline_profile,
            gate_name="viability",
            subject_type="strategy_version",
            subject_id=authorization.subject_id,
            market_profile=str(spec.market_profile),
            strategy_objective="standalone",
            metrics=viability_metrics,
            checks={},
        )
        self._record_stage(
            authorization.id,
            authorization.subject_id,
            "viability",
            viability.status,
            evidence_refs=(fast_manifest,),
            reason="；".join(viability.reasons),
        )
        outcome_type = (
            "strategy_candidate" if viability.status == "passed" else "rejected"
        )
        outcome = self.pipeline.create_strategy_outcome(
            strategy_version_id=authorization.subject_id,
            market_profile=str(spec.market_profile),
            pipeline_profile_id=spec.pipeline_profile,
            outcome_type=outcome_type,
            viability_gate_result_id=(
                viability.id if viability.status == "passed" else None
            ),
            evidence_artifact_keys=(fast_manifest,),
            notes=(
                "authorized pipeline viability passed"
                if viability.status == "passed"
                else "authorized pipeline viability failed; strategy remains rejected"
            ),
        )
        diagnostic_job_id = None
        if viability.status == "failed" and all(
            stage in authorization.allowed_stages for stage in DIAGNOSTIC_STAGE_ORDER
        ):
            artifact_keys = tuple(str(item) for item in fast_result["artifact_keys"])
            diagnostic = self.research.create_job(
                job_type="research_diagnostic",
                payload={
                    "authorization_id": authorization.id,
                    "session_id": authorization.session_id,
                    "subject_id": authorization.subject_id,
                    "fast_screen_manifest_artifact_key": fast_manifest,
                    "metrics_artifact_key": _required_suffix(
                        artifact_keys, "/metrics.json"
                    ),
                    "trades_artifact_key": _required_suffix(
                        artifact_keys, "/trades.parquet"
                    ),
                    "signals_artifact_key": _required_suffix(
                        artifact_keys, "/signals.parquet"
                    ),
                    "data_manifest_artifact_key": str(config["data_manifest_key"]),
                    "detector_config_artifact_key": (
                        "configs/regimes/eth_perpetual_1h_v1.yaml"
                    ),
                    "locked_test_used": False,
                },
            )
            diagnostic_job_id = diagnostic.id
        return {
            "status": "completed" if viability.status == "passed" else "viability_failed",
            "outcome_id": outcome.id,
            "outcome_type": outcome.outcome_type,
            "viability_gate_result_id": viability.id,
            "diagnostic_job_id": diagnostic_job_id,
            "manifest_artifact_key": fast_manifest,
            "artifact_keys": list(fast_result["artifact_keys"]),
            "locked_test_used": False,
        }

    def _evaluate_correctness(
        self,
        *,
        authorization_id: str,
        session_id: str,
        version: Any,
        spec: StrategySpec,
        config_artifact_key: str | None,
    ) -> tuple[dict[str, Any], Any]:
        if not config_artifact_key or not spec.smoke_intent or not spec.fast_screen_intent:
            raise ValueError("reviewed StrategySpec has no authorized pipeline metadata")
        if not spec.market_profile:
            raise ValueError("reviewed StrategySpec has no Market Profile")
        config_key = str(config_artifact_key)
        config: dict[str, Any] = {}
        config_exists = self.artifacts.exists(config_key)
        if config_exists:
            loaded = yaml.safe_load(self.artifacts.get(config_key))
            if isinstance(loaded, dict):
                config = loaded
        structured = bool(version.content_snapshot) and (
            version.content_snapshot.get("formalization_status")
            != "source_snapshot_only"
        )
        checks = {
            "baseline_immutable": version.status == "baseline" and version.immutable,
            "structured_rules_confirmed": structured,
            "strategy_spec_registered": True,
            "config_artifact_exists": config_exists,
            "config_schema_supported": config.get("schema_version") == 1,
            "config_strategy_spec_matches": (
                config.get("strategy_spec_id") == spec.strategy_spec_id
            ),
            "config_session_matches": config.get("session_id") == session_id,
            "config_subject_matches": config.get("strategy_version_id") == version.id,
            "market_profile_matches": config.get("market_profile") == spec.market_profile,
            "locked_test_disabled": (
                config.get("time_splits", {})
                .get("locked_test", {})
                .get("use_in_this_authorization")
                is False
            ),
            "funding_not_silently_zero": (
                config.get("funding", {}).get("zero_funding_fallback_allowed")
                is False
            ),
        }
        for field in ("strategy_artifact_key", "data_manifest_key"):
            key = config.get(field)
            checks[f"{field}_exists"] = (
                isinstance(key, str) and self.artifacts.exists(key)
            )
        gate = self.pipeline.evaluate_gate(
            profile_id=spec.pipeline_profile,
            gate_name="correctness",
            subject_type="strategy_version",
            subject_id=version.id,
            market_profile=str(spec.market_profile),
            strategy_objective="standalone",
            metrics={},
            checks=checks,
        )
        return config, gate

    def _run_backtest_stage(
        self,
        *,
        job: Job,
        spec: StrategySpec,
        stage: str,
        intent: str | None,
        authorization_id: str,
        session_id: str,
        subject_id: str,
        config_artifact_key: str,
        correctness_gate_result_id: str,
        smoke_manifest_artifact_key: str | None = None,
    ) -> Mapping[str, Any]:
        if not intent:
            raise ValueError(f"StrategySpec has no {stage} intent")
        payload: dict[str, Any] = {
            "intent": intent,
            "session_id": session_id,
            "strategy_version_id": subject_id,
            "subject_id": subject_id,
            "config_artifact_key": config_artifact_key,
            "agent_run_id": job.payload["agent_run_id"],
            "authorization_id": authorization_id,
            "correctness_gate_result_id": correctness_gate_result_id,
            "locked_test_used": False,
            "routing": {"source": "authorized_pipeline", "stage": stage},
        }
        if smoke_manifest_artifact_key is not None:
            payload["smoke_manifest_artifact_key"] = smoke_manifest_artifact_key
        stage_job = Job(
            id=job.id,
            job_type="backtest",
            status="running",
            payload=payload,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        try:
            return self.strategy_plugins.route_backtest(stage_job)
        except Exception as exc:
            self._record_stage(
                authorization_id,
                subject_id,
                stage,
                "failed",
                reason=str(exc),
            )
            raise

    def _record_stage(
        self,
        authorization_id: str,
        subject_id: str,
        stage: str,
        status: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        reason: str,
    ) -> None:
        self.authorizations.record_stage(
            authorization_id=authorization_id,
            subject_id=subject_id,
            stage=stage,
            status=status,
            evidence_refs=evidence_refs,
            reason=reason,
        )


def _required_suffix(artifact_keys: tuple[str, ...], suffix: str) -> str:
    matches = [key for key in artifact_keys if key.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"fast-screen output requires one artifact ending with {suffix}")
    return matches[0]
