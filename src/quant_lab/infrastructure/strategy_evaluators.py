from __future__ import annotations

from quant_lab.application.ports import TrialEvaluationRequest, TrialEvaluationResult


class DeterministicFixtureStrategyEvaluator:
    """Non-research fixture used only for wiring tests and explicitly labelled demos."""

    evaluator_id = "deterministic_fixture"

    def evaluate(self, request: TrialEvaluationRequest) -> TrialEvaluationResult:
        numeric = [
            float(value)
            for value in request.parameters.values()
            if isinstance(value, (int, float))
        ]
        score = sum(numeric)
        return TrialEvaluationResult(
            trial_id=request.trial_id,
            status="succeeded",
            metrics={
                "train_net_return": score / 1000,
                "validation_net_return": score / 2000,
                "validation_profit_factor": 1.0 + score / 100,
                "validation_expectancy": score / 10000,
                "validation_trade_count": 30.0,
                "validation_max_drawdown_abs": max(0.01, 0.2 - score / 1000),
                "incremental_net_return": score / 5000,
                "fixture_evidence": 1.0,
            },
            elapsed_seconds=0.001,
            peak_rss_mb=1.0,
            stop_reason=None,
        )


class StrategyEvaluatorRegistry:
    def __init__(self, evaluators=()) -> None:
        self._evaluators = {item.evaluator_id: item for item in evaluators}

    def get(self, evaluator_id: str):
        try:
            return self._evaluators[evaluator_id]
        except KeyError as exc:
            raise ValueError(
                f"strategy evaluator is unsupported: {evaluator_id}; "
                "no generic strategy execution is being fabricated"
            ) from exc
