from __future__ import annotations

from pathlib import Path

from quant_lab.domain.models import validate_artifact_key


class LocalArtifactStore:
    """Project-rooted ArtifactStore adapter with no absolute-path API."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def _resolve(self, artifact_key: str) -> Path:
        validated = validate_artifact_key(artifact_key)
        destination = (self.project_root / validated).resolve()
        try:
            destination.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("artifact_key resolves outside the project root") from exc
        return destination

    def put(self, artifact_key: str, content: bytes) -> None:
        destination = self._resolve(artifact_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)

    def get(self, artifact_key: str) -> bytes:
        return self._resolve(artifact_key).read_bytes()

    def exists(self, artifact_key: str) -> bool:
        return self._resolve(artifact_key).is_file()
