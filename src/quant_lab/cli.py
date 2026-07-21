from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from importlib import metadata

from .paths import project_root, registry_path
from .registry import initialize, list_factors, register_factor
from .runs import create_run


def _installed_versions() -> dict[str, str | None]:
    packages = (
        "pytest",
        "numpy",
        "pandas",
        "pyarrow",
        "duckdb",
        "PyYAML",
        "ccxt",
        "quantstats",
        "freqtrade",
    )
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def command_doctor() -> int:
    root = project_root()
    uv_path = shutil.which("uv")
    managed_uv = root / ".tools" / "bin" / "uv"
    if not uv_path and managed_uv.is_file():
        uv_path = str(managed_uv)
    venv_python = root / ".venv" / "bin" / "python"
    report = {
        "project_root": str(root),
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "python_supported_for_project": (3, 12) <= sys.version_info < (3, 13),
        "venv_python": str(venv_python) if venv_python.is_file() else None,
        "git": shutil.which("git"),
        "docker": shutil.which("docker"),
        "uv": uv_path,
        "registry_initialized": registry_path().is_file(),
        "research_configured": (root / "configs" / "lab.json").is_file(),
        "freqtrade_configured": any(
            path.name != "freqtrade.example.json"
            for path in (root / "configs").glob("freqtrade*.json")
        ),
        "installed_versions": _installed_versions(),
        "disk_free_gib": round(shutil.disk_usage(root).free / (1024**3), 2),
        "live_trading_enabled": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["python_supported_for_project"]:
        print("提示：基础管理工具可运行；完整依赖固定使用项目内 Python 3.12。")
    return 0


def command_init() -> int:
    root = project_root()
    required = [
        "data/raw",
        "data/interim",
        "data/processed",
        "data/external",
        "experiments/runs",
        "reports/daily",
        "reports/weekly",
        "reports/backtests",
        "reports/risk",
    ]
    for relative in required:
        (root / relative).mkdir(parents=True, exist_ok=True)
    initialize(registry_path())
    print("initialized: %s" % root)
    return 0


def command_register_factor(args: argparse.Namespace) -> int:
    register_factor(
        registry_path(),
        factor_id=args.factor_id,
        name=args.name,
        category=args.category,
        status=args.status,
        version=args.version,
        formula_path=args.formula_path,
        description=args.description,
    )
    print("registered factor: %s" % args.factor_id)
    return 0


def command_list_factors() -> int:
    rows = list(list_factors(registry_path()))
    if not rows:
        print("no factors registered")
        return 0
    for row in rows:
        print(
            "{factor_id}\t{status}\t{category}\tv{version}\t{name}".format(**dict(row))
        )
    return 0


def command_new_run(args: argparse.Namespace) -> int:
    result = create_run(
        project_root(),
        registry_path(),
        args.run_type,
        random_seed=args.random_seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("init")
    subparsers.add_parser("list-factors")

    factor = subparsers.add_parser("register-factor")
    factor.add_argument("--factor-id", required=True)
    factor.add_argument("--name", required=True)
    factor.add_argument("--category", required=True)
    factor.add_argument("--status", default="candidate")
    factor.add_argument("--version", type=int, default=1)
    factor.add_argument("--formula-path")
    factor.add_argument("--description")

    run = subparsers.add_parser("new-run")
    run.add_argument("--run-type", choices=["daily", "weekly", "backtest", "stress"], required=True)
    run.add_argument("--random-seed", type=int, default=20260720)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "doctor":
        return command_doctor()
    if args.command == "init":
        return command_init()
    if args.command == "register-factor":
        return command_register_factor(args)
    if args.command == "list-factors":
        return command_list_factors()
    if args.command == "new-run":
        return command_new_run(args)
    raise AssertionError("unhandled command")


if __name__ == "__main__":
    raise SystemExit(main())
