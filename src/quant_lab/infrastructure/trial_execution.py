from __future__ import annotations

import multiprocessing as mp
import os
import queue
import resource
import subprocess
import time
from typing import Sequence

from quant_lab.application.ports import (
    StrategyEvaluator,
    TrialEvaluationRequest,
    TrialEvaluationResult,
)


def _peak_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value / (1024 * 1024) if os.uname().sysname == "Darwin" else value / 1024


def _child_entry(
    evaluator: StrategyEvaluator,
    request: TrialEvaluationRequest,
    output: mp.Queue,
) -> None:
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "DUCKDB_THREADS",
    ):
        os.environ[name] = "1"
    started = time.monotonic()
    try:
        result = evaluator.evaluate(request)
        output.put(
            TrialEvaluationResult(
                trial_id=result.trial_id,
                status=result.status,
                metrics=dict(result.metrics),
                error=result.error,
                elapsed_seconds=max(result.elapsed_seconds, time.monotonic() - started),
                peak_rss_mb=max(result.peak_rss_mb, _peak_rss_mb()),
                result_artifact_key=result.result_artifact_key,
                stop_reason=result.stop_reason,
            )
        )
    except BaseException as exc:  # child boundary must preserve deterministic failure evidence
        output.put(
            TrialEvaluationResult(
                trial_id=request.trial_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=_peak_rss_mb(),
                stop_reason="evaluator_error",
            )
        )


def _process_rss_mb(pid: int) -> float | None:
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
        value = result.stdout.strip()
        return float(value) / 1024 if value else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class InProcessTrialExecutor:
    """Deterministic test/safe fallback executor; concurrency is intentionally one."""

    max_supported_concurrency = 1

    def execute(
        self,
        *,
        evaluator: StrategyEvaluator,
        requests: Sequence[TrialEvaluationRequest],
        max_concurrency: int,
        timeout_seconds: int,
        max_rss_mb: int,
        kill_on_memory_limit: bool,
    ) -> Sequence[TrialEvaluationResult]:
        if max_concurrency != 1:
            raise ValueError("in-process Trial executor only supports concurrency=1")
        results: list[TrialEvaluationResult] = []
        for request in requests:
            started = time.monotonic()
            result = evaluator.evaluate(request)
            elapsed = max(result.elapsed_seconds, time.monotonic() - started)
            rss = max(result.peak_rss_mb, _peak_rss_mb())
            if elapsed > timeout_seconds:
                result = TrialEvaluationResult(
                    trial_id=request.trial_id,
                    status="failed",
                    error="trial time budget exceeded",
                    elapsed_seconds=elapsed,
                    peak_rss_mb=rss,
                    stop_reason="timeout",
                )
            elif kill_on_memory_limit and rss > max_rss_mb:
                result = TrialEvaluationResult(
                    trial_id=request.trial_id,
                    status="failed",
                    error="trial RSS budget exceeded",
                    elapsed_seconds=elapsed,
                    peak_rss_mb=rss,
                    stop_reason="memory_limit",
                )
            else:
                result = TrialEvaluationResult(
                    trial_id=result.trial_id,
                    status=result.status,
                    metrics=result.metrics,
                    error=result.error,
                    elapsed_seconds=elapsed,
                    peak_rss_mb=rss,
                    result_artifact_key=result.result_artifact_key,
                    stop_reason=result.stop_reason,
                )
            results.append(result)
        return results


class ProcessTrialExecutor:
    """Spawn bounded worker processes and kill Trials that exceed time/RSS policy."""

    poll_seconds = 0.05

    def execute(
        self,
        *,
        evaluator: StrategyEvaluator,
        requests: Sequence[TrialEvaluationRequest],
        max_concurrency: int,
        timeout_seconds: int,
        max_rss_mb: int,
        kill_on_memory_limit: bool,
    ) -> Sequence[TrialEvaluationResult]:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        context = mp.get_context("spawn")
        pending = list(requests)
        active: dict[str, tuple[mp.Process, mp.Queue, float]] = {}
        results: list[TrialEvaluationResult] = []
        while pending or active:
            while pending and len(active) < max_concurrency:
                request = pending.pop(0)
                output: mp.Queue = context.Queue(maxsize=1)
                process = context.Process(
                    target=_child_entry,
                    args=(evaluator, request, output),
                    daemon=False,
                )
                process.start()
                active[request.trial_id] = (process, output, time.monotonic())

            for trial_id, (process, output, started) in list(active.items()):
                try:
                    result = output.get_nowait()
                except queue.Empty:
                    result = None
                if result is not None:
                    process.join(timeout=1)
                    results.append(result)
                    del active[trial_id]
                    continue
                elapsed = time.monotonic() - started
                rss = _process_rss_mb(process.pid) if process.pid else None
                stop_reason = None
                if elapsed > timeout_seconds:
                    stop_reason = "timeout"
                elif kill_on_memory_limit and rss is not None and rss > max_rss_mb:
                    stop_reason = "memory_limit"
                elif not process.is_alive():
                    stop_reason = "process_exit"
                if stop_reason is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=2)
                    results.append(
                        TrialEvaluationResult(
                            trial_id=trial_id,
                            status="failed",
                            error=f"trial stopped: {stop_reason}",
                            elapsed_seconds=elapsed,
                            peak_rss_mb=rss or 0.0,
                            stop_reason=stop_reason,
                        )
                    )
                    del active[trial_id]
            if active:
                time.sleep(self.poll_seconds)
        return results
