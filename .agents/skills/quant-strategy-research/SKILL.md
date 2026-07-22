---
name: quant-strategy-research
description: Orchestrate safe, auditable, fail-fast quantitative strategy research in Quant Research Lab. Use when Codex or another agent intakes or formalizes a natural-language/Pine strategy, selects a pipeline profile, evaluates correctness/viability/robustness gates, downloads market data, runs backtests or bounded parameter research, performs cost or full stress tests, records component/regime evidence, prepares dry-run work, or creates strategy/experiment/report artifacts in this repository.
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

Read [references/pipeline-gates.md](references/pipeline-gates.md) whenever evaluating a gate, creating a StrategyOutcome or ComponentCandidate, running regime validation, reconciling Pine, or creating a stress Job.

## Preserve research integrity

- Save the original source before formalization.
- Treat AI and Agent output as draft/proposal until the user confirms it.
- Freeze baseline v0 once. Create a new Proposal/StrategyVersion for every later change.
- Test one explicit hypothesis at a time; require ablation for compound changes.
- Keep failed experiments, contaminated-test markers, costs, data versions, and stopping reasons.
- Never optimize solely for maximum historical profit or inspect locked test repeatedly.
- Never treat validation in one Market Profile as validation in another.

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

## Keep regime and Pine claims bounded

- Use only ex-ante observable regime labels. Never label history from future returns.
- Treat 90-day regime results as `screening` or `insufficient_history`, never long-term validation.
- For natural-language/Python sources, move full Pine/TradingView reconciliation after fast screen and viability.
- For Pine sources, perform early semantic, repainting, multi-timeframe and a few golden-trade checks; move the complete Pine diagnostic after viability.
- Require explicit approval records with an exact `subject_id`; a free-floating “approved” flag is insufficient.

## Use safe artifact references

Store only project-relative `artifact_key` values. Reject absolute paths, `file://` URIs, `..` traversal, credentials, live-trading configuration, and arbitrary Shell payloads. See [references/research-artifacts.md](references/research-artifacts.md) when creating Artifact, ToolCall, Trial, Run, or Report records.

## Respect the product boundary

- Current mode: `external_local_agent + local_runtime + guided`.
- Embedded Provider and Hosted Sandbox are planned/unsupported.
- Do not request an API key, claim an LLM is connected, or fabricate AI output.
- Do not expose or call live trade, credential, arbitrary path, arbitrary Shell, or automatic production-promotion capabilities.
- Use `scripts/freqtrade.sh`; it must reject `trade`.
- Leave long work as a white-listed Job; do not execute it inside an HTTP request.
