# Quant Research Lab Agent Rules

These rules apply to every automated agent working in this project.

1. Historical research and dry-run are allowed by default. Live trading is forbidden unless the user explicitly authorizes the exact exchange, account, strategy, capital limit, and time window.
2. Never store API keys, secrets, cookies, passwords, or withdrawal credentials in this repository.
3. Never request or use an exchange API key with withdrawal permission.
4. Raw data under `data/raw/` is immutable. Corrections create a new version; they do not overwrite the original.
5. Every backtest or optimization run must create a manifest under `experiments/runs/` containing code version, data version, parameters, costs, time range, random seed, and result paths.
6. The locked final test period must not be used for parameter tuning. If it is inspected and then used to change the strategy, mark it contaminated and create a newer locked period.
7. Never promote a factor or strategy solely because it has the highest historical profit. Require out-of-sample, cost, sensitivity, and stress-test evidence.
8. Automated research may mark items as `candidate`, `validated`, `degraded`, `retired`, or `rejected`. Promotion to `production` requires explicit human approval.
9. Record failed experiments. Do not delete or hide negative results.
10. Prefer reproducible commands, deterministic seeds, and machine-readable output.
11. This is a personal, lightweight, strategy-first research lab. Factors are accumulated only as reusable by-products of strategy research; do not turn routine automation into blind factor invention.
12. Freeze and preserve the original baseline strategy before any optimization. Never rewrite or overwrite the baseline to improve historical results.
13. Test one explicit improvement hypothesis at a time. Complex changes require ablation evidence that identifies each component's incremental contribution.
14. Never tune indefinitely merely to make a backtest profitable. Record failed hypotheses, stopping criteria, and negative results.
15. Daily automation may monitor data, factors, and existing strategies, but must not invent strategies or promote any strategy to production.
16. Keep the first phase lightweight. Do not introduce heavy machine learning, large-scale factor mining, brute-force search, or institution-scale data infrastructure unless the user explicitly requests it.
17. Store large market and derived columnar datasets in Parquet by default. Do not create large CSV or JSON datasets without a documented interchange-only reason.
18. Raw market data is immutable and must have a manifest with UTC semantics, source, download time, range, row counts, quality statistics, checksum, cleaning lineage, and data version.
19. Do not download, combine, or compare market data until an explicit market profile defines the asset class, instrument type, venue, symbols, timeframes, required datasets, and cost model.
20. A factor or strategy validated in one market profile is not validated in another. Validation and production status must remain separate by asset class, instrument type, venue or broker, timeframe, and cost model.
21. Never describe FX tick volume as global real traded volume. Never treat a futures continuous series as a directly tradable contract. Stock research must handle corporate actions, delistings, and survivorship bias. Perpetual-futures research must handle funding, mark/index prices, liquidation mechanics, and venue-specific leverage rules.
22. Do not introduce PostgreSQL, TimescaleDB, ClickHouse, InfluxDB, Redis, or other data services in the first phase without a demonstrated scale requirement and explicit user approval.
23. Select a machine-readable Pipeline Profile (`smoke`, `fast_screen`, or `full_validation`) before research execution. Do not hide stage order, acceptance thresholds, approvals, or stop rules in strategy-specific code.
24. Evaluate standalone viability separately from correctness, incremental improvement, robustness, locked-test, and dry-run gates. Viability thresholds are profile/market/objective policy, not universal trading truths.
25. A result that merely loses less than baseline is not a tradable strategy candidate. Preserve it as `diagnostic_improvement` or separately evidenced `component_candidate`; never auto-promote a component from a failed strategy to validated.
26. Cheap cost sensitivity is a post-fast-screen kill test. Full stress is forbidden until the same strategy version passes viability; the Worker must re-check this gate.
27. Regime labels must be observable ex ante. Ninety-day regime evidence is only `screening` or `insufficient_history`, never long-term validation.
28. Move full Pine/TradingView reconciliation after fast screen and viability. Pine-origin strategies still require early semantic, repainting, multi-timeframe, and golden-trade checks.
29. Approval records and UI actions must identify an exact `subject_id`; a free-floating “agree/approve” flag is insufficient for backend state changes.
30. Regime work must declare `regime_diagnostic` or `regime_validation`. Diagnostic work may run before viability but can only produce screening/diagnostic evidence; formal validation requires the same subject's passed viability gate.
31. Freqtrade lookahead/recursive diagnostics are correctness evidence only. They must use the safe wrapper, must not use locked-test data or rejected strategies, and cannot be described as strategy performance evidence.
32. Every ResearchSession is bounded by the configured hypothesis, Trial, compute-minute and locked-test-use budget. Exceeding a budget must create blocked evidence; an Agent cannot override it in a prompt.
33. Every Worker execution, whether one-shot or watch-queue, must enforce and report its configured time, memory, concurrency and Parquet batch limits. Resource failure preserves the Job, manifest/Trial evidence already produced and failure reason.
34. Batch parameter research requires a structured Proposal with an exact Baseline/subject, explicit approval, immutable Candidate snapshot, ExperimentPlan and Session budget. Locked-test data is forbidden in the search.
35. Use deterministic grid or seeded-random search in the first phase. Do not enable Hyperopt/Optuna or use one Agent action per Trial.
36. Preserve completed Trial records across failure, cancellation and retry. Retry must reference the prior Job, resume only unfinished combinations and must not reserve the same Session budget twice.
37. Component aggregation must deduplicate by normalized logic, Market Profile and timeframe, not by parameter value. Automated aggregation may create diagnostic or component-candidate evidence, never validated.
38. Research lifecycle actions are Git-independent. Intake, formalization, baseline freeze, Gate evaluation, Job execution and Handoff recording must not automatically commit or push. A frozen Baseline is authoritative because of SQLite state, project-relative Artifact checksum, Approval and append-only audit—not because of Git history.
39. Git is manual backup/release transport only. Use it only for an explicit user backup/publish request, an explicit completed-session export, or a project code release milestone. Customer/local product operation must not require a repository or remote. Preserve existing tracked strategy history; do not rewrite or delete it to enforce this policy.
40. Every pause, stop, refusal, gate failure, budget exhaustion, dependency block, Job completion or failure must create or report a structured ResearchHandoff/NextAction. It must state why execution stopped, completed and unstarted actions, whether user action is required, the exact approval subject when applicable, and the next recommended action.
41. Report `scripts/freqtrade.sh trade` exit code 3 as `Live trade safety guard: PASS (expected rejection, exit code 3)`. Preserve the non-zero process exit code; do not present the expected safety refusal as a defect to fix.
42. A user may approve one exact-subject `ResearchAuthorization` for the bounded path `correctness → smoke → fast_screen → viability` and explicitly listed cheap diagnostics. Internal stage boundaries covered by that authorization must not ask for repeated approval. The authorization is not permission for locked test, parameter search, dry-run, live trade, or an unregistered strategy executor.
43. A failure before viability stops the automatic path. A failed viability Gate may continue only into authorization-listed `loss_attribution`, `regime_diagnostic`, and `component_hypothesis_generation`; these remain non-causal screening evidence and cannot restore a rejected strategy.
44. Loss attribution should reuse saved trades/signals/metrics when possible. If an already inspected validation split informs a new component hypothesis, mark it `screening_contaminated`; do not relabel it independent out-of-sample evidence.
45. Strategy-specific Native execution must enter through a registered `StrategySpec`/`StrategyEvaluator` boundary. Do not add new strategy conditionals to the core Worker, and do not claim an authorization alone executed research when no reviewed executor/Job ran.
46. One ResearchSession may have only one active write-capable AgentRun lease. Parallel strategy research must use separate ResearchSessions; a waiting, paused, completed, failed or cancelled run releases the lease before another AgentRun may write.

## Instruction priority and enforcement

Apply instructions in this order:

1. System, developer, runtime permission, and platform safety requirements.
2. The user's explicit request and exact authorization scope.
3. The closest applicable `AGENTS.md`, including the rules in this file.
4. The required project documents selected by the routing matrix below.
5. An applicable project or installed Skill.
6. General agent defaults and implementation preferences.

Lower-priority guidance cannot weaken a higher-priority safety or approval boundary. A user authorization only unlocks an action when the relevant rule explicitly allows that form of authorization; it does not create arbitrary live-trading, secret-handling, filesystem, or shell authority.

Do not rely on prompts alone for critical controls. Baseline immutability, approval gates, job/tool allowlists, artifact path safety, append-only audit records, locked-test isolation, and live-trading prohibition must also be represented in domain rules, schemas, repositories, wrappers, or automated tests as appropriate.

Research versioning follows `configs/versioning-policy.yaml`. Git is not an authority source and is not part of routine research state transitions. Automated agents must not infer permission to stage, commit or push from a strategy approval, baseline freeze, experiment approval or successful Job.

Before yielding after a research action, persist or report the latest Handoff with: current status, stop reason, completed actions, unstarted actions, required user action, exact `subject_id`/approval subject, safety-to-continue and next recommendation. “Done”, “blocked” or a raw exit code alone is insufficient.

## Mandatory document routing

Before taking an action in one of these intents, read the listed documents in full and satisfy their preconditions. The machine-readable source is `configs/agent_policies/document-routing.yaml`; update it and this table together.

| Intent | Required documents |
|---|---|
| Strategy intake or formalization | `docs/07-个人量化策略研究工作法.md`, `docs/10-市场适配与因子适用性.md`, `docs/15-产品需求规格-v1.md`, `docs/18-Agent与Skill执行架构.md` |
| Market-data download or normalization | `docs/01-数据规范.md`, `docs/09-数据存储架构.md`, `docs/10-市场适配与因子适用性.md`, `docs/14-ETH永续第一阶段数据记录.md`, `docs/18-Agent与Skill执行架构.md`, `docs/21-存储治理与保留策略.md`, `docs/22-ETH永续一年数据扩展记录.md` |
| Baseline backtest | `docs/00-标准研究流程.md`, `docs/02-回测与验收规范.md`, `docs/07-个人量化策略研究工作法.md`, `docs/10-市场适配与因子适用性.md`, `docs/15-产品需求规格-v1.md`, `docs/18-Agent与Skill执行架构.md`, `docs/20-Freqtrade能力边界与融合方案.md`, `docs/27-保守交易执行模型-v1.md` |
| Parameter optimization/search | `docs/02-回测与验收规范.md`, `docs/04-压力测试清单.md`, `docs/07-个人量化策略研究工作法.md`, `docs/15-产品需求规格-v1.md`, `docs/16-产品化目标架构-v1.md`, `docs/18-Agent与Skill执行架构.md`, `docs/25-批量策略研究闭环.md`, `docs/26-流畅研究授权与失败诊断.md` |
| Stress testing | `docs/02-回测与验收规范.md`, `docs/04-压力测试清单.md`, `docs/07-个人量化策略研究工作法.md`, `docs/18-Agent与Skill执行架构.md`, `docs/20-Freqtrade能力边界与融合方案.md`, `docs/27-保守交易执行模型-v1.md` |
| Dry-run preparation or start | `docs/02-回测与验收规范.md`, `docs/05-自动化运行规范.md`, `docs/06-安全与实盘门禁.md`, `docs/07-个人量化策略研究工作法.md`, `docs/15-产品需求规格-v1.md`, `docs/18-Agent与Skill执行架构.md` |
| Pipeline/gate, outcome, component, regime, Pine validation, or batch evidence aggregation | `docs/00-标准研究流程.md`, `docs/02-回测与验收规范.md`, `docs/04-压力测试清单.md`, `docs/07-个人量化策略研究工作法.md`, `docs/15-产品需求规格-v1.md`, `docs/16-产品化目标架构-v1.md`, `docs/18-Agent与Skill执行架构.md`, `docs/25-批量策略研究闭环.md`, `docs/26-流畅研究授权与失败诊断.md` |

If an intent is ambiguous, route to the stricter applicable set. Record the selected intent, documents read, preconditions, approval decision, tools used, and required outputs in the AgentRun or audit trail. Missing preconditions stop execution; they are not warnings to bypass.
