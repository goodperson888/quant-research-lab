from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
from importlib import metadata

import yaml

from quant_lab.application.experiment_plan_config import (
    PARAMETER_OPTIMIZATION_DOCS,
    PARAMETER_OPTIMIZATION_PRECONDITIONS,
    parse_experiment_plan_draft,
)
from quant_lab.application.services import ResearchApplicationService, utc_now
from quant_lab.application.storage import StorageReporter
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.baseline_backtest_runner import BaselineBacktestRunner
from quant_lab.infrastructure.candidate_cost_stress_runner import (
    CandidateCostStressRunner,
)
from quant_lab.infrastructure.entry_confirmation_experiment_runner import (
    CANDIDATE_STRATEGY_KEY,
)
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.loss_attribution_report import (
    TrainingLossAttributionReportGenerator,
)
from quant_lab.infrastructure.offline_licensing import OfflineLicenseService
from quant_lab.infrastructure.trade_reconciliation_report import (
    TradeReconciliationReportGenerator,
)
from quant_lab.paths import app_database_path, project_root, registry_path
from quant_lab.registry import initialize, list_factors, register_factor
from quant_lab.runs import create_run

from .governance_commands import add_governance_parsers, handle_governance_command
from .licensing_commands import add_licensing_parsers, handle_licensing_command
from .storage_commands import add_storage_parsers, handle_storage_command


def _installed_versions() -> dict[str, str | None]:
    packages = (
        "pytest",
        "numpy",
        "pandas",
        "pyarrow",
        "duckdb",
        "PyYAML",
        "ccxt",
        "cryptography",
        "quantstats",
        "freqtrade",
        "fastapi",
        "pydantic",
        "uvicorn",
    )
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def command_doctor() -> int:
    root = project_root()
    storage = StorageReporter(root).read()
    commercial_license = OfflineLicenseService.from_environment(root).status(
        update_clock_state=False
    )
    uv_path = shutil.which("uv")
    managed_uv = root / ".tools" / "bin" / "uv"
    if not uv_path and managed_uv.is_file():
        uv_path = str(managed_uv)
    venv_python = root / ".venv" / "bin" / "python"
    report = {
        "project_root": str(root),
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "python_supported_for_project": (3, 12) <= sys.version_info < (3, 13),
        "venv_python": str(venv_python) if venv_python.is_file() else None,
        "git": shutil.which("git"),
        "docker": shutil.which("docker"),
        "uv": uv_path,
        "registry_initialized": registry_path().is_file(),
        "product_database_initialized": app_database_path().is_file(),
        "research_configured": (root / "configs" / "lab.json").is_file(),
        "freqtrade_configured": any(
            path.name != "freqtrade.example.json"
            for path in (root / "configs").glob("freqtrade*.json")
        ),
        "installed_versions": _installed_versions(),
        "disk_free_gib": round(shutil.disk_usage(root).free / (1024**3), 2),
        "storage_policy": {
            "policy_id": storage["policy_id"],
            "tracked_file_count": storage["summary"]["file_count"],
            "tracked_total_bytes": storage["summary"]["total_bytes"],
            "warning_count": storage["summary"]["warning_count"],
            "automatic_deletion": storage["automatic_deletion"],
        },
        "live_trading_enabled": False,
        "ai_provider_configured": False,
        "commercial_license": commercial_license.as_dict(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["python_supported_for_project"]:
        print("提示：基础管理工具可运行；完整依赖固定使用项目内 Python 3.12。")
    return 0


def command_init() -> int:
    root = project_root()
    required = [
        "data/raw",
        "data/interim",
        "data/processed",
        "data/external",
        "experiments/runs",
        "reports/daily",
        "reports/weekly",
        "reports/backtests",
        "reports/risk",
        "runtime/app",
    ]
    for relative in required:
        (root / relative).mkdir(parents=True, exist_ok=True)
    initialize(registry_path())
    print("initialized: %s" % root)
    return 0


def command_register_factor(args: argparse.Namespace) -> int:
    register_factor(
        registry_path(),
        factor_id=args.factor_id,
        name=args.name,
        category=args.category,
        status=args.status,
        version=args.version,
        formula_path=args.formula_path,
        description=args.description,
    )
    print("registered factor: %s" % args.factor_id)
    return 0


def command_list_factors() -> int:
    rows = list(list_factors(registry_path()))
    if not rows:
        print("no factors registered")
        return 0
    for row in rows:
        print(
            "{factor_id}\t{status}\t{category}\tv{version}\t{name}".format(**dict(row))
        )
    return 0


def command_new_run(args: argparse.Namespace) -> int:
    result = create_run(
        project_root(),
        registry_path(),
        args.run_type,
        random_seed=args.random_seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_create_baseline_job(args: argparse.Namespace) -> int:
    root = project_root()
    store = LocalArtifactStore(root)
    config = yaml.safe_load(store.get(args.config))
    if not isinstance(config, dict):
        raise ValueError("baseline config must be a YAML mapping")
    repository = SQLiteProductRepository(app_database_path())
    service = ResearchApplicationService(repository)
    agent_run = service.create_agent_run(
        session_id=config["session_id"],
        agent_name="codex",
        mode="guided",
        plan_summary=(
            "Run the frozen Binance ETH/USDT perpetual baseline with declared "
            "costs, chronological splits and non-zero funding-gap treatment."
        ),
    )
    payload = {
        "intent": "baseline_backtest",
        "session_id": config["session_id"],
        "strategy_version_id": config["strategy_version_id"],
        "config_artifact_key": args.config,
        "agent_run_id": agent_run.id,
        "routing": {
            "required_docs": [
                "docs/00-标准研究流程.md",
                "docs/02-回测与验收规范.md",
                "docs/07-个人量化策略研究工作法.md",
                "docs/10-市场适配与因子适用性.md",
                "docs/15-产品需求规格-v1.md",
                "docs/18-Agent与Skill执行架构.md",
                "docs/20-Freqtrade能力边界与融合方案.md",
            ],
            "preconditions": {
                "baseline_frozen": True,
                "market_profile_matches_validation_scope": True,
                "data_manifest_selected": True,
                "cost_model_complete": True,
                "funding_not_silently_zero_for_perpetuals": True,
                "time_splits_recorded": True,
            },
            "approval": {
                "binance_engineering_baseline_confirmed_by_user": True,
                "baseline_change_requested": False,
            },
        },
    }
    BaselineBacktestRunner(root, repository).validate_payload(payload)
    job = service.create_job(job_type="backtest", payload=payload)
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="create_job",
        sanitized_input={
            "intent": "baseline_backtest",
            "strategy_version_id": config["strategy_version_id"],
            "market_profile": config["market_profile"],
            "config_artifact_key": args.config,
        },
        sanitized_output={"job_id": job.id, "status": job.status},
        status="completed",
    )
    print(
        json.dumps(
            {"job_id": job.id, "agent_run_id": agent_run.id, "status": job.status},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_audit_baseline_run(args: argparse.Namespace) -> int:
    repository = SQLiteProductRepository(app_database_path())
    result = TradeReconciliationReportGenerator(
        project_root(), repository
    ).generate(args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_analyze_baseline_losses(args: argparse.Namespace) -> int:
    repository = SQLiteProductRepository(app_database_path())
    result = TrainingLossAttributionReportGenerator(
        project_root(), repository
    ).generate(args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def register_experiment_plan_draft(
    *, root, database_path, config_artifact_key: str
) -> dict[str, object]:
    store = LocalArtifactStore(root)
    config_bytes = store.get(config_artifact_key)
    raw = yaml.safe_load(config_bytes)
    if not isinstance(raw, dict):
        raise ValueError("experiment plan config must be a YAML mapping")
    config = parse_experiment_plan_draft(raw)
    if not store.exists(config.evidence_artifact_key):
        raise ValueError(
            "evidence_artifact_key does not exist: " + config.evidence_artifact_key
        )

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    service.get_research_session(config.session_id)
    baseline = repository.get_strategy_version(config.baseline_version_id)
    draft = repository.get_draft(baseline.strategy_id)
    if baseline.status != "baseline" or not baseline.immutable:
        raise ValueError("baseline_version_id must reference an immutable baseline")
    if draft.session_id != config.session_id:
        raise ValueError("baseline_version_id does not belong to session_id")

    market_profile_key = (
        "configs/market_profiles/" + config.market_profile + ".yaml"
    )
    if not store.exists(market_profile_key):
        raise ValueError("market profile does not exist: " + market_profile_key)

    agent_run = service.create_agent_run(
        session_id=config.session_id,
        agent_name="codex",
        mode="guided",
        plan_summary=(
            "Register one bounded entry-confirmation ExperimentPlan as a draft; "
            "wait for explicit user approval before creating any Trial or Job."
        ),
    )
    plan = service.create_experiment_plan(
        baseline_version_id=config.baseline_version_id,
        hypothesis=config.hypothesis,
        parameter_space=config.parameter_space,
        objectives=config.objectives,
        constraints=config.constraints,
        data_splits=config.data_splits,
        cost_model=config.cost_model,
        max_trials=config.max_trials,
        time_budget_seconds=config.time_budget_seconds,
        stopping_conditions=config.stopping_conditions,
    )
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="experiment",
        artifact_key=config_artifact_key,
        checksum="sha256:" + hashlib.sha256(config_bytes).hexdigest(),
    )
    evidence_bytes = store.get(config.evidence_artifact_key)
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="report",
        artifact_key=config.evidence_artifact_key,
        checksum="sha256:" + hashlib.sha256(evidence_bytes).hexdigest(),
    )
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="create_experiment_plan",
        sanitized_input={
            "intent": "parameter_optimization",
            "config_artifact_key": config_artifact_key,
            "baseline_version_id": config.baseline_version_id,
            "market_profile": config.market_profile,
            "single_rule_change": config.single_rule_change,
            "routing": {
                "required_docs": list(PARAMETER_OPTIMIZATION_DOCS),
                "preconditions": {
                    item: True for item in PARAMETER_OPTIMIZATION_PRECONDITIONS
                },
                "approval": {
                    "confirmed_by_user": False,
                    "parameter_search_job_allowed": False,
                },
            },
        },
        sanitized_output={
            "experiment_plan_id": plan.id,
            "status": plan.status,
            "job_created": False,
            "trial_created": False,
        },
        status="completed",
    )
    service.update_agent_run_status(
        agent_run_id=agent_run.id, status="waiting_approval"
    )
    return {
        "experiment_plan_id": plan.id,
        "agent_run_id": agent_run.id,
        "status": plan.status,
        "approval_required": True,
        "job_created": False,
        "trial_created": False,
        "future_locked_data_available": raw["guardrails"].get(
            "new_locked_data_available"
        ),
    }


def command_create_experiment_plan_from_config(args: argparse.Namespace) -> int:
    result = register_experiment_plan_draft(
        root=project_root(),
        database_path=app_database_path(),
        config_artifact_key=args.config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def approve_experiment_plan_and_create_job(
    *,
    root,
    database_path,
    plan_id: str,
    agent_run_id: str,
    config_artifact_key: str,
    confirmed_by_user: bool,
) -> dict[str, object]:
    if not confirmed_by_user:
        raise ValueError("explicit --confirmed-by-user is required")
    store = LocalArtifactStore(root)
    raw = yaml.safe_load(store.get(config_artifact_key))
    if not isinstance(raw, dict):
        raise ValueError("experiment plan config must be a YAML mapping")
    config = parse_experiment_plan_draft(raw)

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    plan = repository.get_experiment_plan(plan_id)
    agent_run = repository.get_agent_run(agent_run_id)
    if plan.baseline_version_id != config.baseline_version_id:
        raise ValueError("approved plan does not match config baseline")
    if plan.hypothesis != config.hypothesis:
        raise ValueError("approved plan does not match config hypothesis")
    if agent_run.session_id != config.session_id:
        raise ValueError("agent run does not belong to experiment session")

    evidence = json.loads(store.get(config.evidence_artifact_key))
    baseline_run_id = evidence.get("baseline_run_id")
    if not isinstance(baseline_run_id, str) or not baseline_run_id:
        raise ValueError("evidence artifact does not identify the baseline run")
    baseline_manifest_key = f"experiments/runs/{baseline_run_id}/manifest.json"
    if not store.exists(baseline_manifest_key):
        raise ValueError("baseline run manifest does not exist")
    if not store.exists(CANDIDATE_STRATEGY_KEY):
        raise ValueError("candidate strategy implementation does not exist")

    if plan.status == "draft":
        approved = service.approve_experiment_plan(
            plan_id=plan_id,
            confirmed_by_user=True,
        )
        approval_reused = False
    elif plan.status == "approved" and plan.approved_by == "user":
        approved = plan
        approval_reused = True
    else:
        raise ValueError("experiment plan is not eligible for approval or retry")

    existing_jobs = [
        job
        for job in service.list_jobs()
        if job.job_type == "parameter_search"
        and job.payload.get("experiment_plan_id") == plan_id
    ]
    blocking_jobs = [
        job for job in existing_jobs if job.status in {"queued", "running", "succeeded"}
    ]
    if blocking_jobs:
        raise ValueError(
            "parameter-search Job already exists in a non-failed terminal state"
        )
    existing_trials = list(repository.list_trials(plan_id))
    if len(existing_trials) > 1:
        raise ValueError("approved one-Trial budget has been exceeded")
    if existing_trials and (
        existing_trials[0].status != "failed"
        or dict(existing_trials[0].parameters)
        != {"entry_mode": "confirmation_candle_breakout"}
    ):
        raise ValueError("existing Trial is not an eligible infrastructure retry")
    service.record_tool_call(
        agent_run_id=agent_run_id,
        tool_name="approve_experiment_plan",
        sanitized_input={
            "experiment_plan_id": plan_id,
            "explicit_user_confirmation": True,
        },
        sanitized_output={"status": approved.status, "approved_by": "user"},
        status="completed",
    )
    payload = {
        "intent": "parameter_optimization",
        "experiment_plan_id": plan_id,
        "config_artifact_key": config_artifact_key,
        "baseline_manifest_artifact_key": baseline_manifest_key,
        "candidate_strategy_artifact_key": CANDIDATE_STRATEGY_KEY,
        "agent_run_id": agent_run_id,
        "routing": {
            "required_docs": list(PARAMETER_OPTIMIZATION_DOCS),
            "preconditions": {
                item: True for item in PARAMETER_OPTIMIZATION_PRECONDITIONS
            },
            "allowed_tool": "run_parameter_search",
        },
        "approval": {
            "experiment_plan_id": plan_id,
            "confirmed_by_user": True,
            "approved_by": "user",
        },
        "execution_scope": {
            "max_trials": 1,
            "train_and_validation_only": True,
            "previous_locked_test_used": False,
            "future_locked_test_used": False,
            "live_trading_enabled": False,
        },
    }
    job = service.create_job(job_type="parameter_search", payload=payload)
    service.record_tool_call(
        agent_run_id=agent_run_id,
        tool_name="run_parameter_search",
        sanitized_input={
            "job_id": job.id,
            "experiment_plan_id": plan_id,
            "max_trials": 1,
            "train_and_validation_only": True,
        },
        sanitized_output={"job_id": job.id, "status": job.status},
        status="approved",
    )
    service.update_agent_run_status(agent_run_id=agent_run_id, status="queued")
    return {
        "experiment_plan_id": approved.id,
        "experiment_plan_status": approved.status,
        "approval_reused": approval_reused,
        "agent_run_id": agent_run_id,
        "job_id": job.id,
        "job_status": job.status,
        "max_trials": 1,
        "locked_test_enabled": False,
        "live_trading_enabled": False,
    }


def command_approve_experiment_plan(args: argparse.Namespace) -> int:
    result = approve_experiment_plan_and_create_job(
        root=project_root(),
        database_path=app_database_path(),
        plan_id=args.plan_id,
        agent_run_id=args.agent_run_id,
        config_artifact_key=args.config,
        confirmed_by_user=args.confirmed_by_user,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def create_experiment_proposal(
    *,
    root,
    database_path,
    plan_id: str,
    run_id: str,
    agent_run_id: str,
) -> dict[str, object]:
    store = LocalArtifactStore(root)
    manifest_key = f"experiments/runs/{run_id}/manifest.json"
    manifest_bytes = store.get(manifest_key)
    manifest = json.loads(manifest_bytes)
    if manifest.get("experiment_plan", {}).get("id") != plan_id:
        raise ValueError("run manifest does not match experiment plan")
    if manifest.get("decision") != "proposal_pending_user_acceptance":
        raise ValueError("run result is not eligible for a strategy proposal")
    if manifest.get("locked_test", {}).get("inspected_in_this_run") is not False:
        raise ValueError("proposal must not inspect locked-test data")
    report_keys = [
        item["artifact_key"]
        for item in manifest.get("outputs", [])
        if item.get("artifact_key", "").startswith("reports/backtests/")
        and item["artifact_key"].endswith(".md")
    ]
    if len(report_keys) != 1:
        raise ValueError("run manifest must identify exactly one backtest report")

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    plan = repository.get_experiment_plan(plan_id)
    if plan.status != "approved" or plan.approved_by != "user":
        raise ValueError("proposal requires an approved experiment plan")
    agent_run = repository.get_agent_run(agent_run_id)

    proposal_content = {
        "experiment_plan_id": plan_id,
        "baseline_version_id": plan.baseline_version_id,
        "run_id": run_id,
        "single_change": {"entry_mode": "confirmation_candle_breakout"},
        "candidate_implementation_artifact_key": CANDIDATE_STRATEGY_KEY,
        "manifest_artifact_key": manifest_key,
        "report_artifact_key": report_keys[0],
        "derived_metrics": manifest["comparison"]["derived_metrics"],
        "constraints": manifest["comparison"]["constraints"],
        "parameter_stability_analysis": manifest["comparison"][
            "parameter_stability_analysis"
        ],
        "out_of_sample_comparison": manifest["comparison"][
            "out_of_sample_comparison"
        ],
        "status": "awaiting_user_acceptance",
        "baseline_immutable": True,
        "strategy_version_created": False,
        "future_locked_test_required": True,
        "production_promotion_requested": False,
    }
    proposal = service.propose_strategy_version(
        baseline_version_id=plan.baseline_version_id,
        content=proposal_content,
    )
    proposal_key = f"reports/proposals/{proposal.id}.json"
    proposal_payload = {
        "proposal_id": proposal.id,
        "proposal_type": proposal.proposal_type,
        **proposal_content,
    }
    proposal_bytes = (
        json.dumps(proposal_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    store.put(proposal_key, proposal_bytes)
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="diff",
        artifact_key=proposal_key,
        checksum="sha256:" + hashlib.sha256(proposal_bytes).hexdigest(),
    )
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="propose_strategy_version",
        sanitized_input={
            "experiment_plan_id": plan_id,
            "run_id": run_id,
            "baseline_version_id": plan.baseline_version_id,
        },
        sanitized_output={
            "proposal_id": proposal.id,
            "status": "awaiting_user_acceptance",
            "strategy_version_created": False,
        },
        status="completed",
    )
    service.update_agent_run_status(
        agent_run_id=agent_run.id, status="waiting_approval"
    )
    return {
        "proposal_id": proposal.id,
        "proposal_artifact_key": proposal_key,
        "status": "awaiting_user_acceptance",
        "strategy_version_created": False,
        "baseline_immutable": True,
    }


def command_create_experiment_proposal(args: argparse.Namespace) -> int:
    result = create_experiment_proposal(
        root=project_root(),
        database_path=app_database_path(),
        plan_id=args.plan_id,
        run_id=args.run_id,
        agent_run_id=args.agent_run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def accept_experiment_proposal(
    *,
    root,
    database_path,
    proposal_id: str,
    agent_run_id: str,
    confirmed_by_user: bool,
) -> dict[str, object]:
    if not confirmed_by_user:
        raise ValueError("explicit --confirmed-by-user is required")
    store = LocalArtifactStore(root)
    proposal_key = f"reports/proposals/{proposal_id}.json"
    proposal_artifact = json.loads(store.get(proposal_key))
    if proposal_artifact.get("proposal_id") != proposal_id:
        raise ValueError("proposal artifact identity mismatch")
    if proposal_artifact.get("status") != "awaiting_user_acceptance":
        raise ValueError("proposal artifact is not awaiting acceptance")
    candidate_key = proposal_artifact.get("candidate_implementation_artifact_key")
    if candidate_key != CANDIDATE_STRATEGY_KEY or not store.exists(candidate_key):
        raise ValueError("candidate implementation artifact is missing")
    manifest_key = proposal_artifact.get("manifest_artifact_key")
    if not isinstance(manifest_key, str) or not store.exists(manifest_key):
        raise ValueError("proposal experiment manifest is missing")

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    agent_run = repository.get_agent_run(agent_run_id)
    version = service.accept_proposal(
        proposal_id=proposal_id,
        confirmed_by_user=True,
    )
    acceptance_key = f"reports/proposals/{proposal_id}-acceptance.json"
    acceptance_payload = {
        "proposal_id": proposal_id,
        "decision": "accepted_by_user",
        "strategy_version_id": version.id,
        "version": version.version,
        "status": version.status,
        "baseline_version_id": proposal_artifact["baseline_version_id"],
        "baseline_immutable": True,
        "candidate_implementation_artifact_key": candidate_key,
        "experiment_manifest_artifact_key": manifest_key,
        "future_locked_test_required": True,
        "validated": False,
        "dry_run_enabled": False,
        "production_enabled": False,
        "live_trading_enabled": False,
    }
    acceptance_bytes = (
        json.dumps(
            acceptance_payload, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n"
    ).encode("utf-8")
    store.put(acceptance_key, acceptance_bytes)
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="strategy",
        artifact_key=CANDIDATE_STRATEGY_KEY,
        checksum="sha256:"
        + hashlib.sha256(store.get(CANDIDATE_STRATEGY_KEY)).hexdigest(),
    )
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="manifest",
        artifact_key=acceptance_key,
        checksum="sha256:" + hashlib.sha256(acceptance_bytes).hexdigest(),
    )
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="accept_proposal",
        sanitized_input={
            "proposal_id": proposal_id,
            "explicit_user_confirmation": True,
        },
        sanitized_output={
            "strategy_version_id": version.id,
            "version": version.version,
            "status": version.status,
            "baseline_overwritten": False,
            "validated": False,
            "production": False,
        },
        status="completed",
    )
    service.update_agent_run_status(agent_run_id=agent_run.id, status="completed")
    return acceptance_payload


def command_accept_experiment_proposal(args: argparse.Namespace) -> int:
    result = accept_experiment_proposal(
        root=project_root(),
        database_path=app_database_path(),
        proposal_id=args.proposal_id,
        agent_run_id=args.agent_run_id,
        confirmed_by_user=args.confirmed_by_user,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def create_candidate_stress_job(
    *, root, database_path, config_artifact_key: str
) -> dict[str, object]:
    store = LocalArtifactStore(root)
    config_bytes = store.get(config_artifact_key)
    config = yaml.safe_load(config_bytes)
    if not isinstance(config, dict):
        raise ValueError("stress config must be a YAML mapping")

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    candidate = repository.get_strategy_version(config["candidate_version_id"])
    draft = repository.get_draft(candidate.strategy_id)
    agent_run = service.create_agent_run(
        session_id=draft.session_id,
        agent_name="codex",
        mode="guided",
        plan_summary=(
            "Run the two declared train/validation cost stress scenarios for "
            "candidate v1 without changing strategy status or using locked data."
        ),
    )
    payload = {
        "intent": "stress_test",
        "candidate_version_id": config["candidate_version_id"],
        "config_artifact_key": config_artifact_key,
        "agent_run_id": agent_run.id,
        "routing": {
            "required_docs": [
                "docs/02-回测与验收规范.md",
                "docs/04-压力测试清单.md",
                "docs/07-个人量化策略研究工作法.md",
                "docs/18-Agent与Skill执行架构.md",
                "docs/20-Freqtrade能力边界与融合方案.md",
            ],
            "preconditions": {
                "candidate_or_baseline_version_selected": True,
                "pipeline_profile_selected": True,
                "stress_level_declared_as_cheap_or_full": True,
                "stress_scenarios_declared": True,
                "cost_and_execution_assumptions_declared": True,
                "immutable_run_manifest_destination": True,
                "fast_screen_passed_when_stress_level_is_cheap": True,
                "viability_passed_when_stress_level_is_full": False,
            },
            "approval": {
                "strategy_status_change_requested": False,
                "approval_required": False,
            },
        },
        "execution_scope": {
            "train_and_validation_only": True,
            "previous_locked_test_used": False,
            "future_locked_test_used": False,
            "candidate_status_change_allowed": False,
            "live_trading_enabled": False,
        },
    }
    CandidateCostStressRunner(root, repository).validate_payload(payload)
    job = service.create_job(job_type="stress_test", payload=payload)
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="experiment",
        artifact_key=config_artifact_key,
        checksum="sha256:" + hashlib.sha256(config_bytes).hexdigest(),
    )
    acceptance_key = config["candidate_acceptance_artifact_key"]
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="manifest",
        artifact_key=acceptance_key,
        checksum="sha256:" + hashlib.sha256(store.get(acceptance_key)).hexdigest(),
    )
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="create_job",
        sanitized_input={
            "intent": "stress_test",
            "candidate_version_id": candidate.id,
            "scenario_ids": [item["id"] for item in config["scenarios"]],
            "locked_test_used": False,
        },
        sanitized_output={"job_id": job.id, "status": job.status},
        status="completed",
    )
    return {
        "job_id": job.id,
        "job_status": job.status,
        "agent_run_id": agent_run.id,
        "candidate_version_id": candidate.id,
        "candidate_status": candidate.status,
        "scenario_ids": [item["id"] for item in config["scenarios"]],
        "locked_test_enabled": False,
        "candidate_status_change_allowed": False,
        "live_trading_enabled": False,
    }


def command_create_candidate_stress_job(args: argparse.Namespace) -> int:
    result = create_candidate_stress_job(
        root=project_root(),
        database_path=app_database_path(),
        config_artifact_key=args.config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def reject_candidate_after_stress(
    *,
    root,
    database_path,
    version_id: str,
    stress_manifest_artifact_key: str,
    confirmed_by_user: bool,
) -> dict[str, object]:
    if not confirmed_by_user:
        raise ValueError("explicit --confirmed-by-user is required")

    store = LocalArtifactStore(root)
    manifest_bytes = store.get(stress_manifest_artifact_key)
    manifest = json.loads(manifest_bytes)
    if manifest.get("intent") != "stress_test":
        raise ValueError("rejection evidence must be a stress-test manifest")
    sensitivity = manifest.get("sensitivity_results")
    if not isinstance(sensitivity, dict):
        raise ValueError("stress manifest is missing sensitivity results")
    if sensitivity.get("candidate_version_id") != version_id:
        raise ValueError("stress manifest candidate identity mismatch")
    if sensitivity.get("cost_stress_passed") is not False:
        raise ValueError("candidate rejection requires a failed cost stress test")
    if sensitivity.get("locked_test_used") is not False:
        raise ValueError("candidate rejection evidence must not use locked-test data")
    failure_conditions = sensitivity.get("failure_conditions")
    if not isinstance(failure_conditions, list) or not failure_conditions:
        raise ValueError("stress manifest has no recorded failure conditions")
    if not all(isinstance(item, dict) for item in failure_conditions):
        raise ValueError("stress failure conditions must be mappings")

    report_keys = [
        item.get("artifact_key")
        for item in manifest.get("outputs", [])
        if isinstance(item, dict)
        and isinstance(item.get("artifact_key"), str)
        and item["artifact_key"].startswith("reports/risk/")
    ]
    if len(report_keys) != 1 or not store.exists(report_keys[0]):
        raise ValueError("stress report artifact is missing or ambiguous")

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    candidate = repository.get_strategy_version(version_id)
    if candidate.status != "candidate":
        raise ValueError("strategy version is not an active candidate")
    baseline_id = candidate.content_snapshot.get("baseline_version_id")
    if not isinstance(baseline_id, str):
        raise ValueError("candidate does not reference its baseline")
    baseline_before = repository.get_strategy_version(baseline_id)
    draft = repository.get_draft(candidate.strategy_id)
    rejection_key = f"reports/risk/{version_id}-rejection.json"
    if store.exists(rejection_key):
        raise ValueError("candidate rejection artifact already exists")

    agent_run = service.create_agent_run(
        session_id=draft.session_id,
        agent_name="codex",
        mode="guided",
        plan_summary=(
            "Apply the user's explicit candidate rejection after failed cost stress; "
            "preserve the immutable baseline and all experiment evidence."
        ),
    )
    rejected = service.reject_candidate(
        version_id=version_id,
        confirmed_by_user=True,
        stress_manifest_artifact_key=stress_manifest_artifact_key,
        failure_conditions=failure_conditions,
    )
    baseline_after = repository.get_strategy_version(baseline_id)
    if baseline_after != baseline_before or baseline_after.status != "baseline":
        raise RuntimeError("baseline changed during candidate rejection")

    rejection_payload = {
        "schema_version": 1,
        "decision": "rejected_by_user",
        "created_at": utc_now(),
        "candidate_version_id": rejected.id,
        "previous_status": "candidate",
        "new_status": rejected.status,
        "candidate_immutable": rejected.immutable,
        "baseline_version_id": baseline_id,
        "baseline_status": baseline_after.status,
        "baseline_immutable": baseline_after.immutable,
        "baseline_overwritten": False,
        "stress_run_id": manifest.get("run_id"),
        "stress_manifest_artifact_key": stress_manifest_artifact_key,
        "stress_report_artifact_key": report_keys[0],
        "cost_stress_passed": False,
        "failure_conditions": failure_conditions,
        "triggered_scenario": sensitivity.get("scenarios", {}).get(
            "doubled_fee_and_slippage", {}
        ).get("sensitivity", {}),
        "previous_locked_test_used": False,
        "future_locked_test_used": False,
        "dry_run_enabled": False,
        "production_enabled": False,
        "live_trading_enabled": False,
        "next_experiment_created": False,
    }
    rejection_bytes = (
        json.dumps(rejection_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    store.put(rejection_key, rejection_bytes)
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="report",
        artifact_key=rejection_key,
        checksum="sha256:" + hashlib.sha256(rejection_bytes).hexdigest(),
    )
    service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="manifest",
        artifact_key=stress_manifest_artifact_key,
        checksum="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
    )
    service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="reject_candidate",
        sanitized_input={
            "candidate_version_id": version_id,
            "stress_manifest_artifact_key": stress_manifest_artifact_key,
            "explicit_user_confirmation": True,
        },
        sanitized_output={
            "previous_status": "candidate",
            "status": rejected.status,
            "baseline_version_id": baseline_id,
            "baseline_overwritten": False,
            "locked_test_used": False,
            "dry_run": False,
            "production": False,
        },
        status="completed",
    )
    service.update_agent_run_status(agent_run_id=agent_run.id, status="completed")
    return {
        **rejection_payload,
        "agent_run_id": agent_run.id,
        "rejection_artifact_key": rejection_key,
    }


def command_reject_candidate(args: argparse.Namespace) -> int:
    result = reject_candidate_after_stress(
        root=project_root(),
        database_path=app_database_path(),
        version_id=args.version_id,
        stress_manifest_artifact_key=args.stress_manifest,
        confirmed_by_user=args.confirmed_by_user,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("init")
    subparsers.add_parser("list-factors")

    factor = subparsers.add_parser("register-factor")
    factor.add_argument("--factor-id", required=True)
    factor.add_argument("--name", required=True)
    factor.add_argument("--category", required=True)
    factor.add_argument("--status", default="candidate")
    factor.add_argument("--version", type=int, default=1)
    factor.add_argument("--formula-path")
    factor.add_argument("--description")

    run = subparsers.add_parser("new-run")
    run.add_argument(
        "--run-type", choices=["daily", "weekly", "backtest", "stress"], required=True
    )
    run.add_argument("--random-seed", type=int, default=20260720)

    baseline = subparsers.add_parser("create-baseline-job")
    baseline.add_argument(
        "--config",
        default="configs/research/price_structure_hh_hl_binance_baseline_v0.yaml",
    )

    audit = subparsers.add_parser("audit-baseline-run")
    audit.add_argument("--run-id", required=True)

    attribution = subparsers.add_parser("analyze-baseline-losses")
    attribution.add_argument("--run-id", required=True)

    experiment_plan = subparsers.add_parser(
        "create-experiment-plan-from-config"
    )
    experiment_plan.add_argument(
        "--config",
        default="configs/research/price_structure_entry_confirmation_plan.yaml",
    )

    approve_plan = subparsers.add_parser("approve-experiment-plan")
    approve_plan.add_argument("--plan-id", required=True)
    approve_plan.add_argument("--agent-run-id", required=True)
    approve_plan.add_argument(
        "--config",
        default="configs/research/price_structure_entry_confirmation_plan.yaml",
    )
    approve_plan.add_argument("--confirmed-by-user", action="store_true")

    proposal = subparsers.add_parser("create-experiment-proposal")
    proposal.add_argument("--plan-id", required=True)
    proposal.add_argument("--run-id", required=True)
    proposal.add_argument("--agent-run-id", required=True)

    accept_proposal = subparsers.add_parser("accept-experiment-proposal")
    accept_proposal.add_argument("--proposal-id", required=True)
    accept_proposal.add_argument("--agent-run-id", required=True)
    accept_proposal.add_argument("--confirmed-by-user", action="store_true")

    stress_job = subparsers.add_parser("create-candidate-stress-job")
    stress_job.add_argument(
        "--config",
        default="configs/research/price_structure_entry_confirmation_cost_stress.yaml",
    )

    reject_candidate = subparsers.add_parser("reject-candidate")
    reject_candidate.add_argument("--version-id", required=True)
    reject_candidate.add_argument("--stress-manifest", required=True)
    reject_candidate.add_argument("--confirmed-by-user", action="store_true")
    add_licensing_parsers(subparsers)
    add_storage_parsers(subparsers)
    add_governance_parsers(subparsers)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    modular_result = handle_storage_command(args, root=project_root())
    if modular_result is not None:
        return modular_result
    modular_result = handle_licensing_command(args, root=project_root())
    if modular_result is not None:
        return modular_result
    modular_result = handle_governance_command(
        args, root=project_root(), database_path=app_database_path()
    )
    if modular_result is not None:
        return modular_result
    if args.command == "doctor":
        return command_doctor()
    if args.command == "init":
        return command_init()
    if args.command == "register-factor":
        return command_register_factor(args)
    if args.command == "list-factors":
        return command_list_factors()
    if args.command == "new-run":
        return command_new_run(args)
    if args.command == "create-baseline-job":
        return command_create_baseline_job(args)
    if args.command == "audit-baseline-run":
        return command_audit_baseline_run(args)
    if args.command == "analyze-baseline-losses":
        return command_analyze_baseline_losses(args)
    if args.command == "create-experiment-plan-from-config":
        return command_create_experiment_plan_from_config(args)
    if args.command == "approve-experiment-plan":
        return command_approve_experiment_plan(args)
    if args.command == "create-experiment-proposal":
        return command_create_experiment_proposal(args)
    if args.command == "accept-experiment-proposal":
        return command_accept_experiment_proposal(args)
    if args.command == "create-candidate-stress-job":
        return command_create_candidate_stress_job(args)
    if args.command == "reject-candidate":
        return command_reject_candidate(args)
    raise AssertionError("unhandled command")


if __name__ == "__main__":
    raise SystemExit(main())
