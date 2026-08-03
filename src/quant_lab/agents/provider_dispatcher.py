from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from quant_lab.agents.runner import complete_formalization_run, fail_agent_run
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import AgentRun
from quant_lab.infrastructure.llm import InMemoryOpenAICompatibleProvider


class WebProviderDispatcher:
    """Small in-process dispatcher because BYOK secrets live only in API memory."""

    def __init__(
        self,
        service: ResearchApplicationService,
        provider: InMemoryOpenAICompatibleProvider,
    ) -> None:
        self.service = service
        self.provider = provider
        self._pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="quant-lab-provider",
        )

    def submit(self, run: AgentRun) -> None:
        self._pool.submit(self._execute, run)

    def _execute(self, run: AgentRun) -> None:
        try:
            self.service.update_agent_run_status(
                agent_run_id=run.id,
                status="running",
            )
            if not run.subject_id:
                raise RuntimeError("AgentRun 缺少 StrategyDraft subject_id")
            draft = self.service.repository.get_draft(run.subject_id)
            result = self.provider.propose_formalization(draft.raw_content)
            complete_formalization_run(
                service=self.service,
                run=run,
                result=result,
                actor_type="embedded_provider",
            )
        except BaseException as error:
            fail_agent_run(
                service=self.service,
                run=run,
                error=error,
                actor_type="embedded_provider",
            )

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=False)
