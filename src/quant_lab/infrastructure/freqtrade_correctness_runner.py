from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Any

from quant_lab.application.services import new_id, utc_now
from quant_lab.domain.models import AuditEvent, Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore


CommandRunner = Callable[[list[str], int], tuple[int, str, str]]


class DiagnosticExecutionError(RuntimeError):
    def __init__(self, message: str, *, artifact_keys: list[str]) -> None:
        super().__init__(message)
        self.artifact_keys = artifact_keys


class FreqtradeCorrectnessDiagnosticRunner:
    """Run two fixed, read-only Freqtrade diagnostics through the safety wrapper."""

    def __init__(
        self,
        root: Path,
        repository: ProductRepository,
        *,
        timeout_seconds: int = 1800,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner or self._run_command
        self.artifacts = LocalArtifactStore(self.root)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        payload = job.payload
        analysis_type = str(payload["analysis_type"])
        if analysis_type not in {"lookahead-analysis", "recursive-analysis"}:
            raise ValueError("unsupported Freqtrade correctness diagnostic")
        if payload.get("locked_test_used") is not False:
            raise ValueError("correctness diagnostic cannot use locked-test data")
        strategy_key = validate_artifact_key(str(payload["strategy_artifact_key"]))
        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        strategy_path = self._resolve_existing(strategy_key)
        config_path = self._resolve_existing(config_key)
        wrapper = self.root / "scripts" / "freqtrade.sh"
        command = [
            str(wrapper),
            analysis_type,
            "--no-color",
            "--config",
            str(config_path),
            "--strategy",
            str(payload["strategy_name"]),
            "--strategy-path",
            str(strategy_path.parent),
            "--data-format-ohlcv",
            "parquet",
        ]
        if analysis_type == "lookahead-analysis":
            command.extend(["--export", "none"])
        timerange = payload.get("timerange")
        if isinstance(timerange, str):
            command.extend(["--timerange", timerange])

        returncode, stdout, stderr = self.command_runner(command, self.timeout_seconds)
        redacted_stdout = stdout.replace(str(self.root), "<PROJECT_ROOT>")
        redacted_stderr = stderr.replace(str(self.root), "<PROJECT_ROOT>")
        diagnostic_status = "passed" if returncode == 0 else "unsupported_or_failed"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-correctness-{job.id[-8:]}"
        report_key = f"reports/correctness/{job.id}.json"
        manifest_key = f"experiments/runs/{run_id}/manifest.json"
        report = {
            "job_id": job.id,
            "strategy_version_id": payload["strategy_version_id"],
            "analysis_type": analysis_type,
            "diagnostic_status": diagnostic_status,
            "returncode": returncode,
            "stdout": redacted_stdout,
            "stderr": redacted_stderr,
            "evidence_scope": "correctness_only_no_strategy_performance_claim",
            "locked_test_used": False,
            "live_trading": False,
        }
        report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "freqtrade_correctness_diagnostic",
            "engine": "customer_installed_freqtrade_external",
            "analysis_type": analysis_type,
            "strategy_version_id": payload["strategy_version_id"],
            "strategy_artifact_key": strategy_key,
            "config_artifact_key": config_key,
            "timerange": timerange,
            "locked_test_used": False,
            "live_trading": False,
            "result": diagnostic_status,
            "outputs": [report_key],
            "created_at": utc_now(),
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        self.artifacts.put(report_key, report_bytes)
        self.artifacts.put(manifest_key, manifest_bytes)
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="freqtrade_correctness_diagnostic",
                artifact_key=report_key,
                summary={
                    "analysis_type": analysis_type,
                    "diagnostic_status": diagnostic_status,
                    "returncode": returncode,
                },
                created_at=utc_now(),
            )
        )
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type="correctness_diagnostic.evidence_recorded",
                aggregate_type="job",
                aggregate_id=job.id,
                actor_type="system",
                payload={
                    "analysis_type": analysis_type,
                    "diagnostic_status": diagnostic_status,
                    "manifest_artifact_key": manifest_key,
                    "report_artifact_key": report_key,
                    "strategy_performance_claim": False,
                },
                created_at=utc_now(),
            )
        )
        if returncode != 0:
            raise DiagnosticExecutionError(
                "Freqtrade correctness diagnostic unavailable or failed; "
                f"evidence={report_key}",
                artifact_keys=[manifest_key, report_key],
            )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [manifest_key, report_key],
            "checksums": {
                report_key: "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
                manifest_key: "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            },
        }

    def _resolve_existing(self, artifact_key: str) -> Path:
        path = (self.root / artifact_key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("diagnostic artifact resolves outside project root") from exc
        if not path.is_file():
            raise FileNotFoundError(f"diagnostic artifact not found: {artifact_key}")
        return path

    @staticmethod
    def _run_command(command: list[str], timeout_seconds: int) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
            return 124, stdout or "", "diagnostic exceeded worker timeout"
        return completed.returncode, completed.stdout, completed.stderr
