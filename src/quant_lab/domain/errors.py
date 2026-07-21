"""Domain errors translated by interfaces at the system boundary."""


class QuantLabError(Exception):
    """Base class for expected product errors."""


class NotFoundError(QuantLabError):
    """Requested aggregate does not exist."""


class ConflictError(QuantLabError):
    """Requested transition conflicts with immutable state."""


class ApprovalRequiredError(QuantLabError):
    """A human confirmation gate has not been satisfied."""


class InvalidJobError(QuantLabError):
    """Job type or payload violates the local worker allowlist."""


class ProviderNotConfiguredError(QuantLabError):
    """No LLM provider has been configured for this local project."""


class ExperimentPlanValidationError(QuantLabError):
    """An experiment plan is missing an explicit research safety boundary."""
