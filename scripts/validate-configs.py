#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parsed = 0
    for path in sorted((ROOT / "configs").rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            parsed += 1
        elif path.suffix in {".yaml", ".yml"}:
            yaml.safe_load(path.read_text(encoding="utf-8"))
            parsed += 1
    print(f"validated {parsed} JSON/YAML config files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
