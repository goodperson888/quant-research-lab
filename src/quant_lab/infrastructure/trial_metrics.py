from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from quant_lab.domain.models import validate_artifact_key


class ParquetTrialMetricsSink:
    """Write one Parquet object per batch flush, never one file per Trial."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def append_batch(
        self, *, experiment_plan_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> tuple[str, ...]:
        if not rows:
            return ()
        import pyarrow as pa
        import pyarrow.parquet as pq

        now = datetime.now(timezone.utc)
        artifact_key = validate_artifact_key(
            "experiments/runs/"
            f"{experiment_plan_id}/trial_metrics/"
            f"{now:%Y%m%dT%H%M%S}_{uuid4().hex[:10]}.parquet"
        )
        target = self.root / artifact_key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Trial metrics artifact already exists: {artifact_key}")
        normalized = []
        for row in rows:
            item = dict(row)
            item["parameters_json"] = json.dumps(
                item.pop("parameters", {}), sort_keys=True, ensure_ascii=False
            )
            item["metrics_json"] = json.dumps(
                item.pop("metrics", {}), sort_keys=True, ensure_ascii=False
            )
            normalized.append(item)
        temporary = target.with_suffix(target.suffix + ".tmp")
        pq.write_table(pa.Table.from_pylist(normalized), temporary, compression="zstd")
        temporary.replace(target)
        return (artifact_key,)
