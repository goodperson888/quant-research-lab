from __future__ import annotations

from typing import Any, Mapping, Protocol


ALLOWED_RESEARCH_TOOLS = frozenset(
    {
        "intake_strategy",
        "formalize_strategy",
        "list_ambiguities",
        "download_market_data",
        "validate_market_data",
        "build_data_manifest",
        "build_catalog_views",
        "create_job",
        "freeze_baseline",
        "create_experiment_plan",
        "approve_experiment_plan",
        "run_backtest",
        "run_parameter_search",
        "get_job",
        "get_agent_run",
        "compare_runs",
        "propose_strategy_version",
        "accept_proposal",
        "reject_candidate",
        "reject_proposal",
        "generate_report",
        "prepare_dry_run",
        "list_pipeline_profiles",
        "evaluate_gate",
        "create_strategy_outcome",
        "create_component_candidate",
        "record_regime_validation",
    }
)


class ResearchToolGateway(Protocol):
    """Domain allowlist shared by external agents, API and future MCP adapters.

    No arbitrary shell, arbitrary path, credential or live-trading operation belongs
    on this interface.
    """

    def intake_strategy(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def formalize_strategy(self, draft_id: str) -> Mapping[str, Any]: ...

    def list_ambiguities(self, draft_id: str) -> list[Mapping[str, Any]]: ...

    def download_market_data(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def validate_market_data(self, dataset_id: str) -> Mapping[str, Any]: ...

    def build_data_manifest(self, dataset_id: str) -> Mapping[str, Any]: ...

    def build_catalog_views(self, dataset_id: str) -> Mapping[str, Any]: ...

    def create_job(self, job_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def freeze_baseline(self, draft_id: str, approval_id: str) -> Mapping[str, Any]: ...

    def create_experiment_plan(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def approve_experiment_plan(self, plan_id: str) -> Mapping[str, Any]: ...

    def run_backtest(self, plan_id: str) -> Mapping[str, Any]: ...

    def run_parameter_search(self, plan_id: str) -> Mapping[str, Any]: ...

    def get_job(self, job_id: str) -> Mapping[str, Any]: ...

    def get_agent_run(self, agent_run_id: str) -> Mapping[str, Any]: ...

    def compare_runs(self, run_ids: list[str]) -> Mapping[str, Any]: ...

    def propose_strategy_version(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def accept_proposal(self, proposal_id: str) -> Mapping[str, Any]: ...

    def reject_candidate(self, version_id: str) -> Mapping[str, Any]: ...

    def reject_proposal(self, proposal_id: str) -> Mapping[str, Any]: ...

    def generate_report(self, job_id: str) -> Mapping[str, Any]: ...

    def prepare_dry_run(self, strategy_version_id: str) -> Mapping[str, Any]: ...

    def list_pipeline_profiles(self) -> list[Mapping[str, Any]]: ...

    def evaluate_gate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def create_strategy_outcome(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def create_component_candidate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def record_regime_validation(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
