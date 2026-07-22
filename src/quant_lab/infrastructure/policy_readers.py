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


@dataclass(frozen=True, slots=True)
class WorkerResourcePolicy:
    policy_id: str
    max_rss_mb: int
    max_concurrent_trials: int
    max_job_minutes: int
    parquet_batch_rows: int
    kill_on_memory_limit: bool


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
        policy = ResearchBudgetPolicy(
            policy_id=str(raw["policy_id"]),
            max_hypotheses=int(limits["max_hypotheses"]),
            max_trials_total=int(limits["max_trials_total"]),
            max_compute_minutes=int(limits["max_compute_minutes"]),
            max_locked_test_uses=int(limits["max_locked_test_uses"]),
            require_user_approval_for_new_hypothesis=bool(
                limits["require_user_approval_for_new_hypothesis"]
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
        policy = WorkerResourcePolicy(
            policy_id=str(raw["policy_id"]),
            max_rss_mb=int(limits["max_rss_mb"]),
            max_concurrent_trials=int(limits["max_concurrent_trials"]),
            max_job_minutes=int(limits["max_job_minutes"]),
            parquet_batch_rows=int(limits["parquet_batch_rows"]),
            kill_on_memory_limit=bool(limits["kill_on_memory_limit"]),
        )
        if min(
            policy.max_rss_mb,
            policy.max_concurrent_trials,
            policy.max_job_minutes,
            policy.parquet_batch_rows,
        ) <= 0:
            raise ValueError("worker resource limits must be positive")
        if policy.max_concurrent_trials != 1:
            raise ValueError("the one-shot worker currently requires max_concurrent_trials=1")
        return policy
