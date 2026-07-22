from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import yaml

from quant_lab.application.backtest_engines import BacktestEngineRegistry
from quant_lab.application.pipeline import PipelineApplicationService, PipelineProfileCatalog
from quant_lab.application.services import ResearchApplicationService
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.backtest_engines import (
    FreqtradeBacktestEngineAdapter,
    NativeBacktestEngineAdapter,
)
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository


def add_governance_parsers(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "list-backtest-engines", help="List declared engine capabilities; runs nothing."
    )

    regime = subparsers.add_parser(
        "create-regime-validation-job",
        help="Queue an ex-ante regime diagnostic or formal validation.",
    )
    regime.add_argument(
        "--mode",
        choices=["regime_diagnostic", "regime_validation"],
        default="regime_diagnostic",
    )
    regime.add_argument("--subject-type", choices=["strategy_version", "component_candidate"], required=True)
    regime.add_argument("--subject-id", required=True)
    regime.add_argument("--market-profile", required=True)
    regime.add_argument("--detector-config", required=True)
    regime.add_argument("--data-manifest", required=True)
    regime.add_argument("--trades", required=True)
    regime.add_argument("--agent-run-id")
    regime.add_argument("--viability-gate-result-id")

    triage = subparsers.add_parser(
        "triage-component",
        help="Record cheap ablation evidence without validating or promoting a factor.",
    )
    triage.add_argument("--config", required=True)


def handle_governance_command(
    args: argparse.Namespace, *, root: Path, database_path: Path
) -> int | None:
    if args.command == "list-backtest-engines":
        registry = BacktestEngineRegistry(
            (
                NativeBacktestEngineAdapter(lambda _job: {}),
                FreqtradeBacktestEngineAdapter(),
            )
        )
        print(
            json.dumps(
                [asdict(item) for item in registry.capabilities()],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    repository = SQLiteProductRepository(database_path)
    if args.command == "create-regime-validation-job":
        payload = {
            "intent": args.mode,
            "mode": args.mode,
            "subject_type": args.subject_type,
            "subject_id": args.subject_id,
            "market_profile": args.market_profile,
            "detector_config_artifact_key": args.detector_config,
            "data_manifest_artifact_key": args.data_manifest,
            "trades_artifact_key": args.trades,
            "ex_ante_observable": True,
            "locked_test_used": False,
        }
        if args.agent_run_id:
            payload["agent_run_id"] = args.agent_run_id
        if args.viability_gate_result_id:
            payload["viability_gate_result_id"] = args.viability_gate_result_id
        job = ResearchApplicationService(repository).create_job(
            job_type="regime_validation", payload=payload
        )
        print(json.dumps(asdict(job), ensure_ascii=False, indent=2))
        return 0

    if args.command == "triage-component":
        artifact_key = str(args.config)
        raw = yaml.safe_load(LocalArtifactStore(root).get(artifact_key))
        if not isinstance(raw, dict):
            raise ValueError("component triage config must be a YAML mapping")
        evidence, candidate = PipelineApplicationService(
            repository, PipelineProfileCatalog(root)
        ).triage_component(**raw)
        print(
            json.dumps(
                {
                    "evidence": asdict(evidence),
                    "candidate": asdict(candidate),
                    "automatic_validation": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return None
