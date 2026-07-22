from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class StorageAreaReport:
    area_id: str
    artifact_key: str
    classification: str
    immutable: bool
    exists: bool
    file_count: int
    total_bytes: int
    small_file_count: int
    warnings: tuple[str, ...]


class StoragePolicyCatalog:
    """Load the repository-owned storage policy without accepting arbitrary paths."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.path = self.project_root / "configs" / "storage-policy.yaml"

    def load(self) -> Mapping[str, Any]:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid storage policy")
        if raw.get("automatic_deletion") is not False:
            raise ValueError("storage policy must not enable automatic deletion")
        classifications = set(raw.get("classification", {}))
        if classifications != {"authoritative", "rebuildable", "archiveable"}:
            raise ValueError("storage policy must define the three storage classes")
        for area in raw.get("areas", []):
            self.resolve_artifact_key(str(area.get("path", "")))
            if area.get("classification") not in classifications:
                raise ValueError("storage area uses an unknown classification")
        return raw

    def resolve_artifact_key(self, artifact_key: str) -> Path:
        path = PurePosixPath(artifact_key)
        if (
            not artifact_key
            or path.is_absolute()
            or "\\" in artifact_key
            or "://" in artifact_key
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise ValueError("storage paths must be project-relative artifact keys")
        resolved = (self.project_root / Path(*path.parts)).resolve()
        resolved.relative_to(self.project_root)
        return resolved


class StorageReporter:
    """Read-only disk inventory governed by configs/storage-policy.yaml."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self.catalog = StoragePolicyCatalog(self.root)

    def read(self) -> dict[str, Any]:
        policy = self.catalog.load()
        thresholds = policy["thresholds"]
        small_file_bytes = int(thresholds["small_file_bytes"])
        count_warning = int(thresholds["directory_file_count_warning"])
        ratio_warning = float(thresholds["small_file_ratio_warning"])
        size_warning = int(float(thresholds["directory_size_warning_gib"]) * 1024**3)

        areas: list[StorageAreaReport] = []
        for configured in policy["areas"]:
            artifact_key = str(configured["path"])
            target = self.catalog.resolve_artifact_key(artifact_key)
            files = [] if not target.exists() else [p for p in target.rglob("*") if p.is_file()]
            sizes = [path.stat().st_size for path in files]
            total_bytes = sum(sizes)
            small_count = sum(size < small_file_bytes for size in sizes)
            warnings: list[str] = []
            if len(files) > count_warning:
                warnings.append("file_count_threshold_exceeded")
            if total_bytes > size_warning:
                warnings.append("directory_size_threshold_exceeded")
            if files and small_count / len(files) > ratio_warning and len(files) > 100:
                warnings.append("small_file_ratio_threshold_exceeded")
            areas.append(
                StorageAreaReport(
                    area_id=str(configured["id"]),
                    artifact_key=artifact_key,
                    classification=str(configured["classification"]),
                    immutable=bool(configured["immutable"]),
                    exists=target.exists(),
                    file_count=len(files),
                    total_bytes=total_bytes,
                    small_file_count=small_count,
                    warnings=tuple(warnings),
                )
            )

        stat = shutil.disk_usage(self.root)
        return {
            "policy_id": policy["policy_id"],
            "automatic_deletion": False,
            "disk": {
                "total_bytes": stat.total,
                "used_bytes": stat.used,
                "free_bytes": stat.free,
            },
            "thresholds": dict(thresholds),
            "areas": [asdict(area) for area in areas],
            "summary": {
                "file_count": sum(area.file_count for area in areas),
                "total_bytes": sum(area.total_bytes for area in areas),
                "warning_count": sum(len(area.warnings) for area in areas),
                "authoritative_areas": [
                    area.artifact_key
                    for area in areas
                    if area.classification == "authoritative"
                ],
                "rebuildable_areas": [
                    area.artifact_key
                    for area in areas
                    if area.classification == "rebuildable"
                ],
                "archiveable_rule": policy["experiment_retention"][
                    "full_trades_and_equity"
                ],
            },
        }
