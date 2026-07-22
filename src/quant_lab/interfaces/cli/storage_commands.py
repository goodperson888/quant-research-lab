from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_lab.application.storage import StorageReporter


def add_storage_parsers(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "storage-report",
        help="Read-only storage classification, file-count and threshold report.",
    )


def handle_storage_command(args: argparse.Namespace, *, root: Path) -> int | None:
    if args.command != "storage-report":
        return None
    print(json.dumps(StorageReporter(root).read(), ensure_ascii=False, indent=2))
    return 0
