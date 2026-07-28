from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_lab.application.failure_diagnostics import (
    ArtifactLossAttribution,
    DeterministicComponentHypothesisGenerator,
)
from quant_lab.application.pipeline import PipelineApplicationService, PipelineProfileCatalog
from quant_lab.application.regime import (
    ExAnteRegimeDetector,
    RegimeDetectorCatalog,
    summarize_trades_by_regime,
)
from quant_lab.application.research_authorization import ResearchAuthorizationService
from quant_lab.application.services import new_id, strategy_is_rejected
from quant_lab.domain.models import Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore


class ResearchDiagnosticsRunner:
    """Reuse saved fast-screen evidence for cheap, non-causal diagnostics."""

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.authorizations = ResearchAuthorizationService(repository)
        self.pipeline = PipelineApplicationService(
            repository, PipelineProfileCatalog(self.root)
        )
        self.regime_catalog = RegimeDetectorCatalog(self.root)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        payload = self._validate_payload(job.payload)
        authorization_id = payload["authorization_id"]
        subject_id = payload["subject_id"]
        session_id = payload["session_id"]
        manifest = json.loads(self.artifacts.get(payload["fast_screen_manifest_artifact_key"]))
        if (
            manifest.get("status") != "succeeded"
            or manifest.get("pipeline_stage") != "fast_screen"
        ):
            raise ValueError("diagnostics require a saved successful fast-screen manifest")
        if _manifest_strategy_subject(manifest) != subject_id:
            raise ValueError("fast-screen evidence belongs to another subject")
        if manifest.get("locked_test", {}).get("used") is not False:
            raise ValueError("diagnostics must not consume locked-test evidence")
        if (
            manifest.get("data_manifest", {}).get("artifact_key")
            != payload["data_manifest_artifact_key"]
        ):
            raise ValueError("diagnostic data manifest does not match fast-screen evidence")

        trades = pd.read_parquet(
            io.BytesIO(self.artifacts.get(payload["trades_artifact_key"]))
        )
        signals = pd.read_parquet(
            io.BytesIO(self.artifacts.get(payload["signals_artifact_key"]))
        )
        metrics = json.loads(self.artifacts.get(payload["metrics_artifact_key"]))
        attribution = ArtifactLossAttribution().analyze(
            trades=trades, signals=signals, metrics=metrics
        )

        run_id = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            f"-diagnostic-{job.id[-8:]}"
        )
        run_prefix = f"experiments/runs/{run_id}"
        report_key = f"reports/diagnostics/{run_id}.json"
        attribution_key = f"{run_prefix}/loss-attribution.json"
        regime_key = f"{run_prefix}/regime-diagnostic.json"
        labels_key = f"{run_prefix}/regime-labels.parquet"
        manifest_key = f"{run_prefix}/manifest.json"
        for key in (
            report_key,
            attribution_key,
            regime_key,
            labels_key,
            manifest_key,
        ):
            if self.artifacts.exists(key):
                raise FileExistsError(f"diagnostic artifact already exists: {key}")

        self._record_stage_once(
            authorization_id=authorization_id,
            subject_id=subject_id,
            stage="loss_attribution",
            status="passed",
            evidence_refs=(payload["trades_artifact_key"], payload["signals_artifact_key"]),
            reason="reused saved trades/signals/metrics; no market reload or backtest",
        )

        regime_report, labels, validation_id = self._regime_diagnostic(
            subject_id=subject_id,
            manifest=manifest,
            trades=trades,
            detector_config_artifact_key=payload["detector_config_artifact_key"],
        )
        self._record_stage_once(
            authorization_id=authorization_id,
            subject_id=subject_id,
            stage="regime_diagnostic",
            status="passed",
            evidence_refs=(payload["trades_artifact_key"], payload["data_manifest_artifact_key"]),
            reason="ex-ante closed-1h labels; diagnostic screening only",
        )

        evidence_refs = (attribution_key, regime_key)
        generated = DeterministicComponentHypothesisGenerator().generate(
            session_id=session_id,
            subject_id=subject_id,
            attribution=attribution,
            evidence_refs=evidence_refs,
        )
        hypotheses = [
            self.repository.create_component_hypothesis(item) for item in generated
        ]
        self._record_stage_once(
            authorization_id=authorization_id,
            subject_id=subject_id,
            stage="component_hypothesis_generation",
            status="passed",
            evidence_refs=evidence_refs,
            reason=(
                f"created {len(hypotheses)} deterministic screening drafts; "
                "no AI provider and no Trial execution"
            ),
        )

        attribution_bytes = _json_bytes(attribution)
        regime_bytes = _json_bytes(regime_report)
        labels_buffer = io.BytesIO()
        labels.to_parquet(labels_buffer, index=False, compression="zstd")
        combined_report = {
            "run_id": run_id,
            "subject_id": subject_id,
            "session_id": session_id,
            "authorization_id": authorization_id,
            "strategy_status_remains": "rejected",
            "loss_attribution": attribution,
            "regime_diagnostic": regime_report,
            "component_hypotheses": [
                {
                    "id": item.id,
                    "title": item.title,
                    "component_type": item.component_type,
                    "source": item.source,
                    "suggested_trials": item.suggested_trials,
                    "status": item.status,
                    "contamination_status": item.contamination_status,
                }
                for item in hypotheses
            ],
            "research_boundaries": {
                "reran_strategy": False,
                "locked_test_used": False,
                "parameter_search_run": False,
                "component_ablation_run": False,
                "walk_forward_run": False,
                "formal_regime_validation": False,
                "strategy_candidate_restored": False,
            },
        }
        report_bytes = _json_bytes(combined_report)
        self.artifacts.put(attribution_key, attribution_bytes)
        self.artifacts.put(regime_key, regime_bytes)
        self.artifacts.put(labels_key, labels_buffer.getvalue())
        self.artifacts.put(report_key, report_bytes)

        run_manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "post_viability_failure_diagnostics",
            "subject_id": subject_id,
            "session_id": session_id,
            "authorization_id": authorization_id,
            "source_fast_screen_manifest_artifact_key": payload[
                "fast_screen_manifest_artifact_key"
            ],
            "source_artifacts": {
                "trades": payload["trades_artifact_key"],
                "signals": payload["signals_artifact_key"],
                "metrics": payload["metrics_artifact_key"],
                "data_manifest": payload["data_manifest_artifact_key"],
            },
            "regime_validation_id": validation_id,
            "component_hypothesis_ids": [item.id for item in hypotheses],
            "outputs": [
                _artifact_record(attribution_key, attribution_bytes),
                _artifact_record(regime_key, regime_bytes),
                _artifact_record(labels_key, labels_buffer.getvalue(), rows=len(labels)),
                _artifact_record(report_key, report_bytes),
            ],
            "locked_test_used": False,
            "strategy_rerun": False,
            "market_data_reloaded_for_loss_attribution": False,
            "market_data_loaded_for_ex_ante_regime_labels": True,
            "formal_validation": False,
            "causal_claim_allowed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_bytes = _json_bytes(run_manifest)
        self.artifacts.put(manifest_key, manifest_bytes)

        created_at = datetime.now(timezone.utc).isoformat()
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="post_viability_failure_diagnostics",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "subject_id": subject_id,
                    "hypothesis_count": len(hypotheses),
                    "regime_evidence_status": regime_report["evidence_status"],
                    "reran_strategy": False,
                    "locked_test_used": False,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [
                manifest_key,
                attribution_key,
                regime_key,
                labels_key,
                report_key,
            ],
            "summary": combined_report["research_boundaries"],
            "component_hypothesis_ids": [item.id for item in hypotheses],
            "regime_validation_id": validation_id,
        }

    def _validate_payload(self, payload: Mapping[str, Any]) -> dict[str, str]:
        required = {
            "authorization_id",
            "session_id",
            "subject_id",
            "fast_screen_manifest_artifact_key",
            "metrics_artifact_key",
            "trades_artifact_key",
            "signals_artifact_key",
            "data_manifest_artifact_key",
            "detector_config_artifact_key",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError("research diagnostic payload missing: " + ", ".join(missing))
        values = {key: str(payload[key]) for key in required}
        if payload.get("locked_test_used") is not False:
            raise ValueError("research diagnostics must explicitly exclude locked test")
        if not strategy_is_rejected(self.repository, values["subject_id"]):
            raise ValueError("post-viability diagnostics require a rejected strategy")
        if (
            self.repository.get_session_id_for_strategy_version(values["subject_id"])
            != values["session_id"]
        ):
            raise ValueError("diagnostic subject belongs to another session")
        for key in (
            "fast_screen_manifest_artifact_key",
            "metrics_artifact_key",
            "trades_artifact_key",
            "signals_artifact_key",
            "data_manifest_artifact_key",
            "detector_config_artifact_key",
        ):
            validate_artifact_key(values[key])
            if not self.artifacts.exists(values[key]):
                raise ValueError(f"required diagnostic artifact does not exist: {values[key]}")
        self.authorizations.assert_scope_covers(
            values["authorization_id"],
            subject_id=values["subject_id"],
            stages=(
                "loss_attribution",
                "regime_diagnostic",
                "component_hypothesis_generation",
            ),
        )
        return values

    def _record_stage_once(
        self,
        *,
        authorization_id: str,
        subject_id: str,
        stage: str,
        status: str,
        evidence_refs: tuple[str, ...],
        reason: str,
    ) -> None:
        existing = [
            item
            for item in self.authorizations.stages(authorization_id)
            if item.stage == stage and item.status in {"passed", "failed", "blocked"}
        ]
        if existing:
            if existing[-1].status != "passed":
                raise ValueError(
                    f"research authorization cannot resume after {stage} "
                    f"{existing[-1].status}"
                )
            return
        self.authorizations.record_stage(
            authorization_id=authorization_id,
            subject_id=subject_id,
            stage=stage,
            status=status,
            evidence_refs=evidence_refs,
            reason=reason,
        )

    def _regime_diagnostic(
        self,
        *,
        subject_id: str,
        manifest: Mapping[str, Any],
        trades: pd.DataFrame,
        detector_config_artifact_key: str,
    ) -> tuple[dict[str, Any], pd.DataFrame, str]:
        config = self.regime_catalog.load(detector_config_artifact_key)
        data_manifest = json.loads(
            self.artifacts.get(str(manifest["data_manifest"]["artifact_key"]))
        )
        bars = self._load_dataset(
            data_manifest,
            dataset="futures_ohlcv",
            timeframe=config.timeframe,
        )
        validation_end = _manifest_validation_end(manifest)
        bars = bars.loc[
            pd.to_datetime(bars["timestamp"], utc=True) < pd.Timestamp(validation_end)
        ].copy()
        labels = ExAnteRegimeDetector(config).detect(bars)
        normalized = trades.copy()
        if "net_return" not in normalized:
            return_column = next(
                (
                    name
                    for name in (
                        "net_return_on_entry_equity",
                        "return_on_initial_equity",
                    )
                    if name in normalized
                ),
                None,
            )
            if return_column is None:
                raise ValueError("trade evidence does not contain a net-return field")
            normalized["net_return"] = normalized[return_column]
        metrics, groups = summarize_trades_by_regime(
            normalized, labels, minimum_trades=config.minimum_trades_per_regime
        )
        history_days = max(
            1,
            int(
                (
                    pd.to_datetime(bars["timestamp"], utc=True).max()
                    - pd.to_datetime(bars["timestamp"], utc=True).min()
                ).total_seconds()
                // 86400
                + 1
            ),
        )
        evidence_status = (
            "screening"
            if history_days >= config.minimum_history_days_for_screening
            else "insufficient_history"
        )
        validation = self.pipeline.create_regime_validation(
            subject_type="strategy_version",
            subject_id=subject_id,
            mode="regime_diagnostic",
            market_profile=config.market_profile,
            detector_version=f"{config.detector_id}:{config.detector_version}",
            ex_ante_observable=True,
            target_regimes=tuple(sorted(metrics)),
            suitable_regimes=groups["suitable"],
            conditional_regimes=groups["conditional"],
            blocked_regimes=groups["blocked"],
            unknown_regimes=groups["unknown"],
            regime_metrics=metrics,
            transition_policy=dict(config.transition_policy),
            history_days=history_days,
            evidence_status=evidence_status,
            viability_gate_result_id=None,
        )
        return (
            {
                "regime_validation_id": validation.id,
                "subject_id": subject_id,
                "mode": "regime_diagnostic",
                "detector_version": validation.detector_version,
                "ex_ante_observable": True,
                "effective_from": "next_complete_1h_bar",
                "history_days": history_days,
                "evidence_status": evidence_status,
                "regime_metrics": metrics,
                "groups": groups,
                "formal_validation": False,
                "strategy_promotion_allowed": False,
                "limitations": [
                    "Labels use closed 1h bars and become effective one bar later.",
                    "This diagnostic reuses an already inspected validation period.",
                    "Regime association is descriptive screening, not causal proof.",
                ],
            },
            labels,
            validation.id,
        )

    def _load_dataset(
        self, manifest: Mapping[str, Any], *, dataset: str, timeframe: str
    ) -> pd.DataFrame:
        matches = [
            item
            for item in manifest["processed_datasets"]
            if item["dataset"] == dataset
            and item.get("timeframe") == timeframe
            and item["status"] == "processed"
        ]
        if len(matches) != 1:
            raise ValueError(f"manifest must contain one {dataset}/{timeframe} dataset")
        frames = [
            pd.read_parquet(io.BytesIO(self.artifacts.get(output["path"])))
            for output in matches[0]["outputs"]
        ]
        return pd.concat(frames, ignore_index=True).sort_values("timestamp")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _artifact_record(
    artifact_key: str, content: bytes, *, rows: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "artifact_key": artifact_key,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if rows is not None:
        result["rows"] = rows
    return result


def _manifest_strategy_subject(manifest: Mapping[str, Any]) -> str | None:
    strategy = manifest.get("strategy", {})
    if not isinstance(strategy, Mapping):
        return None
    for key in ("candidate_version_id", "strategy_version_id"):
        value = strategy.get(key)
        if isinstance(value, str):
            return value
    return None


def _manifest_validation_end(manifest: Mapping[str, Any]) -> str:
    for key in ("time_splits", "time_range_or_splits"):
        splits = manifest.get(key)
        if not isinstance(splits, Mapping):
            continue
        validation = splits.get("validation")
        if not isinstance(validation, Mapping):
            continue
        value = validation.get("end_utc_exclusive")
        if isinstance(value, str):
            return value
    raise ValueError("fast-screen manifest does not declare validation end")
