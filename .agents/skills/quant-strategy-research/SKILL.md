---
name: quant-strategy-research
description: Orchestrate safe, auditable, reproducible quantitative strategy research in Quant Research Lab. Use when Codex or another agent intakes or formalizes a natural-language/Pine strategy, downloads market data, runs a baseline backtest, proposes parameter optimization, performs stress tests, prepares dry-run work, or creates strategy/experiment/report artifacts in this repository.
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

## Preserve research integrity

- Save the original source before formalization.
- Treat AI and Agent output as draft/proposal until the user confirms it.
- Freeze baseline v0 once. Create a new Proposal/StrategyVersion for every later change.
- Test one explicit hypothesis at a time; require ablation for compound changes.
- Keep failed experiments, contaminated-test markers, costs, data versions, and stopping reasons.
- Never optimize solely for maximum historical profit or inspect locked test repeatedly.
- Never treat validation in one Market Profile as validation in another.

## Bound parameter search

Do not tune parameters conversationally or one-by-one without a budget. Require an approved ExperimentPlan containing:

- frozen baseline and one falsifiable hypothesis;
- ParameterSpace, Objective, risk Constraints;
- train, validation, and locked-test splits;
- complete cost model;
- max Trials or time budget;
- stopping conditions and explicit user approval.

Only then create a `parameter_search` Job. The deterministic Worker, not the Agent, runs Trials. Analyze stable regions and validation results before using the locked test.

## Use safe artifact references

Store only project-relative `artifact_key` values. Reject absolute paths, `file://` URIs, `..` traversal, credentials, live-trading configuration, and arbitrary Shell payloads. See [references/research-artifacts.md](references/research-artifacts.md) when creating Artifact, ToolCall, Trial, Run, or Report records.

## Respect the product boundary

- Current mode: `external_local_agent + local_runtime + guided`.
- Embedded Provider and Hosted Sandbox are planned/unsupported.
- Do not request an API key, claim an LLM is connected, or fabricate AI output.
- Do not expose or call live trade, credential, arbitrary path, arbitrary Shell, or automatic production-promotion capabilities.
- Use `scripts/freqtrade.sh`; it must reject `trade`.
- Leave long work as a white-listed Job; do not execute it inside an HTTP request.
