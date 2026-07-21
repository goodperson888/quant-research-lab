"""Compatibility adapter for the existing standalone factor registry.

The phase-0 product database intentionally does not absorb or rewrite the existing
factor registry. This adapter gives layered code an infrastructure-level import while
the public ``quant_lab.registry`` API remains stable.
"""

from quant_lab.registry import (
    VALID_STATUSES,
    initialize,
    list_factors,
    register_experiment,
    register_factor,
)

__all__ = [
    "VALID_STATUSES",
    "initialize",
    "list_factors",
    "register_experiment",
    "register_factor",
]
