from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("QUANT_LAB_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    return project_root() / "factor_library" / "registry.sqlite3"
