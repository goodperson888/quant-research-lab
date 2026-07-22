from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_lab.application.pipeline import PipelineApplicationService, PipelineProfileCatalog
from quant_lab.application.regime import (
    ExAnteRegimeDetector,
    RegimeDetectorCatalog,
    summarize_trades_by_regime,
)
from quant_lab.domain.models import Job, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore


class RegimeValidationRunner:
    """Allowlisted one-shot handler for ex-ante regime evidence."""

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.detectors = RegimeDetectorCatalog(self.root)
        self.pipeline = PipelineApplicationService(
            repository, PipelineProfileCatalog(self.root)
        )

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, str]:
        required = {
            "subject_type",
            "subject_id",
            "market_profile",
            "detector_config_artifact_key",
            "data_manifest_artifact_key",
            "trades_artifact_key",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"regime validation payload missing: {missing}")
        values = {key: str(payload[key]) for key in required}
        if values["subject_type"] not in {"strategy_version", "component_candidate"}:
            raise ValueError("unsupported regime validation subject_type")
        for name in (
            "detector_config_artifact_key",
            "data_manifest_artifact_key",
            "trades_artifact_key",
        ):
            validate_artifact_key(values[name])
        config = self.detectors.load(values["detector_config_artifact_key"])
        if config.market_profile != values["market_profile"]:
            raise ValueError("regime detector market profile mismatch")
        return values

    def __call__(self, job: Job) -> Mapping[str, Any]:
        payload = self.validate_payload(job.payload)
        config = self.detectors.load(payload["detector_config_artifact_key"])
        manifest = json.loads(self.artifacts.get(payload["data_manifest_artifact_key"]))
        if manifest["market_profile"] != payload["market_profile"]:
            raise ValueError("data manifest market profile mismatch")
        bars = self._load_manifest_dataset(
            manifest, dataset="futures_ohlcv", timeframe=config.timeframe
        )
        labels = ExAnteRegimeDetector(config).detect(bars)
        trades = pd.read_parquet(io.BytesIO(self.artifacts.get(payload["trades_artifact_key"])))
        metrics, groups = summarize_trades_by_regime(
            trades, labels, minimum_trades=config.minimum_trades_per_regime
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
        if history_days < config.minimum_history_days_for_screening:
            evidence_status = "insufficient_history"
        elif history_days < config.minimum_history_days_for_extended_validation:
            evidence_status = "screening"
        else:
            evidence_status = "extended_validation"

        observed_regimes = tuple(sorted(metrics))
        validation = self.pipeline.create_regime_validation(
            subject_type=payload["subject_type"],
            subject_id=payload["subject_id"],
            market_profile=payload["market_profile"],
            detector_version=f"{config.detector_id}:{config.detector_version}",
            ex_ante_observable=True,
            target_regimes=observed_regimes,
            suitable_regimes=groups["suitable"],
            conditional_regimes=groups["conditional"],
            blocked_regimes=groups["blocked"],
            unknown_regimes=groups["unknown"],
            regime_metrics=metrics,
            transition_policy=dict(config.transition_policy),
            history_days=history_days,
            evidence_status=evidence_status,
        )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = f"experiments/runs/{stamp}-regime-{job.id[-8:]}"
        labels_key = f"{prefix}/regime-labels.parquet"
        report_key = f"reports/regimes/{validation.id}.json"
        manifest_key = f"{prefix}/manifest.json"
        for key in (labels_key, report_key, manifest_key):
            if self.artifacts.exists(key):
                raise FileExistsError(f"regime artifact already exists: {key}")

        label_buffer = io.BytesIO()
        labels.to_parquet(label_buffer, index=False, compression="zstd")
        report = {
            "regime_validation_id": validation.id,
            "subject_type": validation.subject_type,
            "subject_id": validation.subject_id,
            "market_profile": validation.market_profile,
            "detector_version": validation.detector_version,
            "ex_ante_observable": True,
            "history_days": history_days,
            "evidence_status": evidence_status,
            "regime_metrics": metrics,
            "groups": groups,
            "limitations": [
                "Regime labels use only a closed 1h bar and become effective on the next bar.",
                "Screening evidence is not long-term validation.",
            ],
        }
        report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        manifest = {
            "manifest_version": 1,
            "run_id": prefix.rsplit("/", 1)[-1],
            "job_id": job.id,
            "run_type": "regime_validation",
            "subject_id": validation.subject_id,
            "market_profile": validation.market_profile,
            "detector_config_artifact_key": payload["detector_config_artifact_key"],
            "data_manifest_artifact_key": payload["data_manifest_artifact_key"],
            "trades_artifact_key": payload["trades_artifact_key"],
            "detector_version": validation.detector_version,
            "ex_ante_observable": True,
            "history_days": history_days,
            "evidence_status": evidence_status,
            "locked_test_used": False,
            "live_trading": False,
            "outputs": [labels_key, report_key],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        self.artifacts.put(labels_key, label_buffer.getvalue())
        self.artifacts.put(report_key, report_bytes)
        self.artifacts.put(manifest_key, manifest_bytes)
        return {
            "run_id": manifest["run_id"],
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [manifest_key, report_key, labels_key],
            "checksums": {
                report_key: "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
                manifest_key: "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            },
        }

    def _load_manifest_dataset(
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
