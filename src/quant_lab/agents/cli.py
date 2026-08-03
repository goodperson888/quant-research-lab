from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time

from quant_lab.agents.local_codex import LocalCodexFormalizer
from quant_lab.agents.runner import complete_formalization_run, fail_agent_run
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import AgentProviderKind
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.paths import app_database_path, project_root


STOP = False


def handle_signal(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def write_heartbeat(root: Path, *, state: str, run_id: str | None = None) -> None:
    heartbeat = root / "runtime" / "agent-connector" / "heartbeat.json"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    temporary = heartbeat.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"updated_at_epoch": time.time(), "state": state, "agent_run_id": run_id},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(heartbeat)


def execute_one(
    *,
    repository: SQLiteProductRepository,
    service: ResearchApplicationService,
    formalizer: LocalCodexFormalizer,
) -> bool:
    run = repository.claim_next_agent_run(
        agent_provider=AgentProviderKind.EXTERNAL_LOCAL_AGENT
    )
    if run is None:
        return False
    write_heartbeat(formalizer.root, state="running", run_id=run.id)
    try:
        if not run.subject_id:
            raise RuntimeError("AgentRun 缺少 StrategyDraft subject_id")
        draft = repository.get_draft(run.subject_id)
        result = formalizer.propose_formalization(
            raw_content=draft.raw_content,
            agent_run_id=run.id,
        )
        complete_formalization_run(
            service=service,
            run=run,
            result=result,
            actor_type="external_agent",
        )
    except BaseException as error:
        fail_agent_run(
            service=service,
            run=run,
            error=error,
            actor_type="external_agent",
        )
    finally:
        write_heartbeat(formalizer.root, state="idle")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Lab Local Codex Connector")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    root = project_root()
    repository = SQLiteProductRepository(app_database_path())
    service = ResearchApplicationService(repository)
    formalizer = LocalCodexFormalizer(root)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    write_heartbeat(root, state="idle")
    while not STOP:
        worked = execute_one(
            repository=repository,
            service=service,
            formalizer=formalizer,
        )
        if not args.watch:
            break
        if not worked:
            time.sleep(max(0.2, args.poll_seconds))
            write_heartbeat(root, state="idle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
