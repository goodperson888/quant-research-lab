"""Backward-compatible entry point for ``python -m quant_lab.cli``."""

from quant_lab.interfaces.cli.main import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
