from __future__ import annotations

import json
import hashlib
import platform
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from .registry import register_experiment


def _git_revision(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def _source_tree_digest(root: Path) -> str:
    paths = []
    for relative in ("src", "strategies", "scripts"):
        directory = root / relative
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    for relative in ("pyproject.toml", "uv.lock", ".python-version"):
        path = root / relative
        if path.is_file():
            paths.append(path)

    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "tree-sha256:%s" % digest.hexdigest()


def create_run(
    root: Path,
    registry: Path,
    run_type: str,
    *,
    random_seed: int = 20260720,
) -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    run_id = "%s-%s-%s" % (
        now.strftime("%Y%m%dT%H%M%SZ"),
        run_type,
        secrets.token_hex(3),
    )
    run_dir = root / "experiments" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id,
        "run_type": run_type,
        "status": "created",
        "created_at": now.isoformat(),
        "project_root": str(root),
        "git_revision": _git_revision(root),
        "code_version": _source_tree_digest(root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "data_manifest": None,
        "strategy": None,
        "parameters": {},
        "cost_model": {},
        "time_splits": {},
        "random_seed": random_seed,
        "outputs": [],
        "notes": [
            "No market data or strategy was executed by the scaffold task.",
            "Populate the manifest before running a real backtest.",
        ],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    register_experiment(
        registry,
        run_id=run_id,
        run_type=run_type,
        manifest_path=str(manifest_path.relative_to(root)),
    )
    return {"run_id": run_id, "run_dir": str(run_dir)}
