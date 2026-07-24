---
name: quant-strategy-research
description: Orchestrate safe, auditable, fail-fast quantitative strategy research in Quant Research Lab. Use when Codex or another agent intakes or formalizes a natural-language/Pine strategy, selects a pipeline profile, evaluates correctness/viability/robustness gates, downloads market data, runs backtests or approved bounded batch parameter research, manages Proposal/Candidate/Trial evidence, performs cost or full stress tests, records component/regime evidence, prepares dry-run work, or creates strategy/experiment/report artifacts in this repository.
---

# Quant Strategy Research

## Execute the routed workflow

1. Read the repository `AGENTS.md` completely.
2. Read `configs/agent_policies/document-routing.yaml`.
3. Classify the request as one or more configured intents. When uncertain, choose the stricter route.
4. Read every `required_docs` file for each selected intent completely before acting.
5. Check every `required_preconditions`. Stop and report missing conditions; do not downgrade them to warnings.
6. Use only the route's `allowed_tools` through project CLI/API/ResearchToolGateway-compatible interfaces.
7. Pause at each `approval_gate`. Never infer approval from past research or general permission.
8. Produce every `required_outputs` artifact and append audit events for key actions and failures.
9. Report the intent, documents read, preconditions, approvals, tools, artifact keys, tests, and unresolved gaps.

## Follow the configured fail-fast pipeline

1. Select `smoke`, `fast_screen`, or `full_validation` from `configs/pipelines/`; do not invent a hidden pipeline in strategy-specific code.
2. Execute in order: correctness → fast screen → viability → cheap sensitivity → regime/Pine → full validation/locked/full stress → dry-run.
3. Stop on a failed configured gate. After failure, allow only inexpensive attribution and component-evidence capture unless the user approves a new hypothesis.
4. Treat viability as a standalone-strategy gate, separate from correctness, incremental improvement, robustness, locked-test and dry-run gates.
5. Never call a strategy candidate merely because it loses less than baseline. Record it as `diagnostic_improvement`, reject the standalone strategy when appropriate, and preserve a reusable component only with separate evidence.
6. Require a passed viability result before full stress. Cheap cost sensitivity requires fast-screen evidence and is a kill test, not full validation.
7. Declare regime work as `regime_diagnostic` or `regime_validation`. Allow diagnostics before viability only as screening evidence; require the same subject's passed viability gate for formal validation.

Read [references/pipeline-gates.md](references/pipeline-gates.md) whenever evaluating a gate, creating a StrategyOutcome or ComponentCandidate, running regime validation, reconciling Pine, or creating a stress Job.

## Preserve research integrity

- Save the original source before formalization.
- Treat AI and Agent output as draft/proposal until the user confirms it.
- Freeze baseline v0 once. Create a new Proposal/StrategyVersion for every later change.
- Test one explicit hypothesis at a time; require ablation for compound changes.
- Keep failed experiments, contaminated-test markers, costs, data versions, and stopping reasons.
- Never optimize solely for maximum historical profit or inspect locked test repeatedly.
- Never treat validation in one Market Profile as validation in another.

## Keep research state independent from Git

- Load `configs/versioning-policy.yaml` before deciding whether an Artifact should be exported.
- Treat SQLite product state, project-relative Artifact checksums, Approval and append-only audit as authoritative.
- Never commit or push because intake, formalization, baseline freeze, Gate evaluation or a Job succeeded.
- Use Git only when the user explicitly requests backup/publish, explicitly exports a completed session, or authorizes a project code release milestone.
- Keep existing tracked strategy history; do not rewrite or delete it. Local customer operation must work without `.git` or a remote.

## Select storage and backtest engines explicitly

- Read `docs/21-存储治理与保留策略.md` for storage inventory or retention work. Preserve permanent Trial metrics, manifests and failure reasons; never auto-delete evidence.
- Read `docs/22-ETH永续一年数据扩展记录.md` before extending or consuming the current annual ETH perpetual dataset. Keep the v2 manifest authoritative and leave funding/mark/index/OI gaps explicit.
- Read `docs/20-Freqtrade能力边界与融合方案.md` before baseline or stress execution. Use the native engine for current smoke/fast-screen evidence and Freqtrade only as a reviewed reconciliation/full-validation adapter.
- Do not relabel existing native runs as Freqtrade results. Do not enable Hyperopt, FreqAI or dry-run merely because their modules or CLI commands exist.

## Bound parameter search

Do not tune parameters conversationally or one-by-one without a budget. Require an approved ExperimentPlan containing:

- frozen baseline and one falsifiable hypothesis;
- ParameterSpace, Objective, risk Constraints;
- train, validation, and locked-test splits;
- complete cost model;
- max Trials or time budget;
- stopping conditions and explicit user approval.

Only then create a `parameter_search` Job. The deterministic Worker, not the Agent, runs Trials. Analyze stable regions and validation results before using the locked test.

Read [references/batch-research.md](references/batch-research.md) whenever creating, approving,
executing, retrying, summarizing, or aggregating evidence from a batch parameter search.

Also load the ResearchSession budget before approving a new hypothesis or creating a search Job. Stop and preserve `research_budget.blocked` evidence when max hypotheses, total Trials, compute minutes or locked-test uses are exhausted. A plan-level budget never replaces the session-level budget.

## Keep regime and Pine claims bounded

- Use only ex-ante observable regime labels. Never label history from future returns.
- Use `regime_diagnostic` before viability and keep it at diagnostic/screening/insufficient-history. Use `regime_validation` only with the same subject's passed viability GateEvaluation.
- Treat 90-day regime results as `screening` or `insufficient_history`, never long-term validation.
- For natural-language/Python sources, move full Pine/TradingView reconciliation after fast screen and viability.
- For Pine sources, perform early semantic, repainting, multi-timeframe and a few golden-trade checks; move the complete Pine diagnostic after viability.
- Require explicit approval records with an exact `subject_id`; a free-floating “approved” flag is insufficient.

## Run correctness diagnostics safely

- Use Freqtrade `lookahead-analysis` or `recursive-analysis` only through `scripts/freqtrade.sh` and the allowlisted correctness Job.
- Treat Freqtrade as a customer-installed optional external engine. If unavailable, record unsupported/blocked evidence and continue to support the Native Engine.
- Never run a rejected strategy, locked-test range, `trade`, arbitrary command or arbitrary path.
- Record stdout/stderr in a project-relative Artifact and label it correctness-only, not strategy performance.

## Respect Worker resources

Load `configs/workers/local.yaml`. Default one-shot concurrency to one; an automatic memory-based
recommendation may raise it only within the configured hard cap. Report elapsed time and peak RSS,
batch Trial metrics using the configured Parquet row target, and fail with preserved evidence when
time or memory limits are exceeded.

## Use safe artifact references

Store only project-relative `artifact_key` values. Reject absolute paths, `file://` URIs, `..` traversal, credentials, live-trading configuration, and arbitrary Shell payloads. See [references/research-artifacts.md](references/research-artifacts.md) when creating Artifact, ToolCall, Trial, Run, or Report records.

## Respect the product boundary

- Current mode: `external_local_agent + local_runtime + guided`.
- Embedded Provider and Hosted Sandbox are planned/unsupported.
- Do not request an API key, claim an LLM is connected, or fabricate AI output.
- Do not expose or call live trade, credential, arbitrary path, arbitrary Shell, or automatic production-promotion capabilities.
- Use `scripts/freqtrade.sh`; it must reject `trade`.
- Leave long work as a white-listed Job; do not execute it inside an HTTP request.

## Always finish with a Stop/Handoff

Create or retrieve the latest `ResearchHandoff` whenever work pauses, completes its approved scope,
waits for input/approval, fails a Gate, exhausts budget, hits an optional dependency, is refused for
safety, or finishes/fails a Job. Include the exact `subject_id` and `approval_subject_id` when needed.

Use this final response shape; never return only “done”, “blocked” or a raw exit code:

- Current status
- Why execution stopped
- Completed actions
- Not started actions
- User action required (yes/no and exact action)
- Next recommended action (exact subject/button/command)

Normalize the wrapper result as `Live trade safety guard: PASS (expected rejection, exit code 3)`
while preserving process exit code 3. Do not describe the expected refusal as a product failure.
