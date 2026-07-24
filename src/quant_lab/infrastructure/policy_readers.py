from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ResearchBudgetPolicy:
    policy_id: str
    max_hypotheses: int
    max_trials_total: int
    max_compute_minutes: int
    max_locked_test_uses: int
    require_user_approval_for_new_hypothesis: bool
    max_directions: int = 3
    max_trials_per_direction: int = 20
    recommended_trials_total: int = 60
    recommended_compute_minutes: int = 45
    recommended_locked_test_uses: int = 1


@dataclass(frozen=True, slots=True)
class WorkerResourcePolicy:
    policy_id: str
    max_rss_mb: int
    max_concurrent_trials: int
    max_job_minutes: int
    parquet_batch_rows: int
    kill_on_memory_limit: bool
    default_concurrent_trials: int = 1
    auto_concurrency: bool = True
    auto_concurrency_by_memory_gb: tuple[tuple[int, int], ...] = (
        (8, 1),
        (16, 2),
        (32, 4),
    )

    def recommended_concurrency(self, memory_gb: float) -> int:
        recommended = self.default_concurrent_trials
        for threshold, value in sorted(self.auto_concurrency_by_memory_gb):
            if memory_gb >= threshold:
                recommended = value
        return max(1, min(recommended, self.max_concurrent_trials))


@dataclass(frozen=True, slots=True)
class VersioningPolicy:
    policy_id: str
    authoritative_strategy_state: tuple[str, ...]
    git_role: str
    git_required_for_local_product: bool
    remote_required: bool
    auto_commit_on_intake: bool
    auto_commit_on_formalization: bool
    auto_commit_on_freeze: bool
    auto_push: bool
    git_allowed_events: tuple[str, ...]
    local_authoritative_roots: tuple[str, ...]
    existing_tracked_strategy_history_policy: str
    future_local_artifacts_may_remain_uncommitted: bool
    versioned_export_requires_explicit_user_action: bool
    versioned_export_root: str
    research_actions_must_not_invoke_git: bool


class _FixedYamlPolicyReader:
    relative_path: str

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / self.relative_path

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise ValueError(f"required policy file not found: {self.relative_path}")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError(f"invalid policy file: {self.relative_path}")
        if not isinstance(raw.get("limits"), dict):
            raise ValueError(f"policy limits missing: {self.relative_path}")
        return raw


class ResearchBudgetPolicyReader(_FixedYamlPolicyReader):
    relative_path = "configs/research_budgets/default.yaml"

    def read(self) -> ResearchBudgetPolicy:
        raw = self._read()
        limits = raw["limits"]
        guided = raw.get("guided_defaults") or {}
        policy = ResearchBudgetPolicy(
            policy_id=str(raw["policy_id"]),
            max_hypotheses=int(limits["max_hypotheses"]),
            max_trials_total=int(limits["max_trials_total"]),
            max_compute_minutes=int(limits["max_compute_minutes"]),
            max_locked_test_uses=int(limits["max_locked_test_uses"]),
            require_user_approval_for_new_hypothesis=bool(
                limits["require_user_approval_for_new_hypothesis"]
            ),
            max_directions=int(guided.get("max_directions", 3)),
            max_trials_per_direction=int(guided.get("max_trials_per_direction", 20)),
            recommended_trials_total=int(guided.get("recommended_trials_total", 60)),
            recommended_compute_minutes=int(
                guided.get("recommended_compute_minutes", 45)
            ),
            recommended_locked_test_uses=int(
                guided.get("recommended_locked_test_uses", 1)
            ),
        )
        if min(
            policy.max_hypotheses,
            policy.max_trials_total,
            policy.max_compute_minutes,
            policy.max_locked_test_uses,
        ) <= 0:
            raise ValueError("research budget limits must be positive")
        return policy


class WorkerResourcePolicyReader(_FixedYamlPolicyReader):
    relative_path = "configs/workers/local.yaml"

    def read(self) -> WorkerResourcePolicy:
        raw = self._read()
        limits = raw["limits"]
        defaults = raw.get("defaults") or {}
        auto_raw = raw.get("auto_concurrency_by_memory_gb") or {}
        policy = WorkerResourcePolicy(
            policy_id=str(raw["policy_id"]),
            max_rss_mb=int(limits["max_rss_mb"]),
            max_concurrent_trials=int(limits["max_concurrent_trials"]),
            max_job_minutes=int(limits["max_job_minutes"]),
            parquet_batch_rows=int(limits["parquet_batch_rows"]),
            kill_on_memory_limit=bool(limits["kill_on_memory_limit"]),
            default_concurrent_trials=int(defaults.get("max_concurrent_trials", 1)),
            auto_concurrency=bool(defaults.get("auto_concurrency", True)),
            auto_concurrency_by_memory_gb=tuple(
                sorted((int(key), int(value)) for key, value in auto_raw.items())
            )
            or ((8, 1), (16, 2), (32, 4)),
        )
        if min(
            policy.max_rss_mb,
            policy.max_concurrent_trials,
            policy.max_job_minutes,
            policy.parquet_batch_rows,
        ) <= 0:
            raise ValueError("worker resource limits must be positive")
        if not 1 <= policy.default_concurrent_trials <= policy.max_concurrent_trials:
            raise ValueError("worker default concurrency must be within the hard limit")
        if any(value > policy.max_concurrent_trials for _, value in policy.auto_concurrency_by_memory_gb):
            raise ValueError("auto concurrency recommendation exceeds the hard limit")
        return policy


class VersioningPolicyReader:
    relative_path = "configs/versioning-policy.yaml"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / self.relative_path

    def read(self) -> VersioningPolicy:
        if not self.path.is_file():
            raise ValueError(f"required policy file not found: {self.relative_path}")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError(f"invalid policy file: {self.relative_path}")
        git = raw.get("git")
        lifecycle = raw.get("artifact_lifecycle")
        customer = raw.get("customer_product")
        authority = raw.get("authoritative_strategy_state")
        if not all(isinstance(item, dict) for item in (git, lifecycle, customer)):
            raise ValueError("versioning policy sections are incomplete")
        if not isinstance(authority, list) or not authority:
            raise ValueError("authoritative_strategy_state is required")
        policy = VersioningPolicy(
            policy_id=str(raw["policy_id"]),
            authoritative_strategy_state=tuple(str(item) for item in authority),
            git_role=str(git["role"]),
            git_required_for_local_product=bool(git["required_for_local_product"]),
            remote_required=bool(git["remote_required"]),
            auto_commit_on_intake=bool(git["auto_commit_on_intake"]),
            auto_commit_on_formalization=bool(git["auto_commit_on_formalization"]),
            auto_commit_on_freeze=bool(git["auto_commit_on_freeze"]),
            auto_push=bool(git["auto_push"]),
            git_allowed_events=tuple(str(item) for item in git["allowed_events"]),
            local_authoritative_roots=tuple(
                str(item) for item in lifecycle["local_authoritative_roots"]
            ),
            existing_tracked_strategy_history_policy=str(
                lifecycle["existing_tracked_strategy_history_policy"]
            ),
            future_local_artifacts_may_remain_uncommitted=bool(
                lifecycle["future_local_artifacts_may_remain_uncommitted"]
            ),
            versioned_export_requires_explicit_user_action=bool(
                lifecycle["versioned_export_requires_explicit_user_action"]
            ),
            versioned_export_root=str(lifecycle["versioned_export_root"]),
            research_actions_must_not_invoke_git=bool(
                customer["research_actions_must_not_invoke_git"]
            ),
        )
        if any(
            (
                policy.auto_commit_on_intake,
                policy.auto_commit_on_formalization,
                policy.auto_commit_on_freeze,
                policy.auto_push,
                policy.git_required_for_local_product,
                policy.remote_required,
            )
        ):
            raise ValueError("local versioning policy must keep research actions Git-independent")
        if not policy.research_actions_must_not_invoke_git:
            raise ValueError("research actions must not invoke Git")
        return policy
