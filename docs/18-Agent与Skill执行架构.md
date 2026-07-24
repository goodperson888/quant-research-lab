# Agent 与 Skill 执行架构

## 1. 目的

本文件定义 Quant Research Lab 中“谁决定、谁编排、谁执行、谁记录”。目标不是用更长提示词约束 AI，而是让本地 External Agent 和未来线上 Embedded Agent 共用同一组领域工具、状态机、审批与审计协议。

当前优先模式是：

```text
external_local_agent + local_runtime + guided
```

即 Codex 等本地 Agent 读取项目规则并调用安全 CLI/API；Web 显示同一任务、事件、审批和成果。Embedded Provider、BYOK、Local Model、Hosted Sandbox、Local Connector 均是 planned/unsupported。

## 2. 七层职责

| 层 | 解决的问题 | 能否单独保证安全 |
|---|---|---|
| System/Developer Prompt | 平台权限、工具、安全和通用行为 | 不能替代项目业务状态机 |
| `AGENTS.md` | 项目不可违反规则、指令优先级和文档路由 | 不能只靠文字阻止数据库覆盖或 Shell |
| Skill | 为一类任务提供精简、可重复的工作流 | 不能扩大权限或绕过审批 |
| Agent | 理解用户目标、选择 intent、提出计划和调用工具 | 不能直接写生产状态或执行任意命令 |
| ResearchToolGateway | 暴露领域白名单工具 | 是 Agent/API/MCP 的统一能力边界 |
| 后端状态机/Repository | 校验状态迁移、不可变性、路径、审批和审计 | 是关键业务约束的权威执行点 |
| Worker/Experiment Engine | 确定性执行回测、Trial、报告 | 只能处理白名单 Job，不能接受任意 Shell |

### 2.1 指令优先级

1. System、Developer、runtime permission 和平台安全；
2. 用户的明确请求与精确授权范围；
3. 最近的 `AGENTS.md`；
4. `configs/agent_policies/document-routing.yaml` 选出的必读项目文档；
5. 适用 Skill；
6. Agent 默认偏好。

低层不能削弱高层。Skill 是编排说明，不是权限来源。网页文字和聊天确认也不能替代后端 `Approval` 或状态机。

## 3. 强制文档路由

Agent 在行动前先分类 intent：strategy intake、data download、baseline backtest、parameter optimization、stress test 或 dry-run。随后读取 YAML 中 `required_docs` 的完整内容，并逐项验证：

- `required_preconditions`：缺失即停止，不是警告；
- `allowed_tools`：只能调用列出的领域工具；
- `required_outputs`：完成后必须留下机器可读产物；
- `approval_gate`：触发动作前必须存在用户批准。

如果一个请求跨多个 intent，例如“下载数据并回测”，必须合并两组要求，采用更严格的门禁。
当前 data download 路由还要求阅读存储治理和年度数据记录；baseline/stress 路由要求阅读
Freqtrade 能力边界，避免把 Native 证据误称为 Freqtrade 结果。

每个 AgentRun/AuditEvent 应记录：选择的 intent、读过的文档、前置条件结果、审批、工具、脱敏参数和输出 Artifact。

## 4. Skill 的位置与职责

项目级 Skill 位于：

```text
.agents/skills/quant-strategy-research/
```

该 Skill 只负责：

1. 识别研究 intent；
2. 加载路由 YAML 和必读文档；
3. 检查 Market Profile、baseline、成本、切分、预算等前置条件；
4. 通过 ResearchToolGateway/兼容 CLI/API 调用白名单工具；
5. 保存 manifest、Trial、报告、Artifact 和审计；
6. 在审批门禁处暂停。

Skill 不包含交易秘诀，不承诺盈利，不调用真实账户，不运行任意 Shell，不自行改变 `production`。

## 5. Agent Provider 与 Execution Target

Provider 和执行位置独立：

```mermaid
flowchart LR
    subgraph Providers["Agent Providers"]
      ELA["external_local_agent<br/>supported"]
      ECP["embedded_cloud_provider<br/>planned"]
      BYOK["byok_provider<br/>planned"]
      LMP["local_model_provider<br/>planned"]
    end
    subgraph Protocol["Shared Research Protocol"]
      S["ResearchSession / StrategyVersion"]
      P["ExperimentPlan / Approval"]
      R["AgentRun / ToolCall / AuditEvent"]
      J["Job / Trial / Report / Artifact"]
      T["ResearchToolGateway"]
    end
    subgraph Targets["Execution Targets"]
      LOCAL["local_runtime<br/>supported"]
      HOSTED["hosted_sandbox<br/>planned"]
    end
    ELA --> T
    ECP --> T
    BYOK --> T
    LMP --> T
    T --> S
    T --> P
    T --> R
    T --> J
    J --> LOCAL
    J --> HOSTED
```

未来增加线上 Embedded Agent 时，不建立第二套策略或实验对象，只增加 AgentProvider adapter。未来增加 Hosted Sandbox 时，只增加 ExecutionTarget adapter；Artifact、Approval 和审计协议保持不变。

## 6. ResearchToolGateway

目标白名单：

- intake/formalization：`intake_strategy`、`formalize_strategy`、`list_ambiguities`；
- data：`download_market_data`、`validate_market_data`、`build_data_manifest`、`build_catalog_views`；
- baseline/version：`freeze_baseline`、`propose_strategy_version`、`accept_proposal`、`reject_proposal`、`reject_candidate`（仅允许有明确人工批准和失败证据的 `candidate → rejected`）；
- experiment：`create_experiment_plan`、`approve_experiment_plan`、`run_backtest`、`run_parameter_search`、`compare_runs`；
- observation：`get_job`、`get_agent_run`、`generate_report`。
- dry-run preparation：`prepare_dry_run`（只生成/校验模拟配置，不启动 live trade）。
- pipeline：`list_pipeline_profiles`、`evaluate_gate`、`create_strategy_outcome`、
  `create_component_candidate`、`record_regime_validation`。

禁止加入：

- `run_shell(command)`；
- 任意绝对路径读写；
- 密钥获取/保存；
- `live_trade`；
- 自动 production 晋升。

CLI、HTTP 和未来 MCP 都只是这个 Gateway 的 transport adapter，不能各自创造额外能力。

## 7. ArtifactStore 与路径安全

业务对象只保存 project-relative `artifact_key`，例如：

```text
experiments/runs/run_123/manifest.json
reports/backtests/run_123.html
strategies/research/strategy_abc/v1.py
```

禁止保存 `/Users/...`、`file://...`、`../outside` 或其他逃逸项目根的路径。ArtifactStore adapter 负责把 key 安全解析到项目目录，验证归属后再读写。数据库不保存工作站绝对路径，便于本地和未来 Hosted Sandbox 共用协议。

## 8. 状态机执行

### 8.1 baseline

只有显式用户确认才能 `draft → baseline_frozen`。Repository 用唯一约束和事务阻止第二次冻结。Agent 或 Provider 无权覆盖 baseline，只能创建 Proposal/新版本。

### 8.2 ExperimentPlan

只有具备以下字段才能 approved：

- baseline version；
- 单一可证伪假设；
- ParameterSpace；
- Objective 与风险 Constraint；
- train/validation/locked-test；
- 成本模型；
- max Trials 或时间预算；
- 停止条件；
- 用户批准。

`run_parameter_search` Job 必须引用已批准计划。当前 Worker 保留既定入场确认
single-Trial 兼容 Handler，并增加通用 Batch Trial 编排。Batch 仍必须通过
StrategyEvaluator 端口；没有真实 evaluator 时明确 unsupported，唯一内置 deterministic
fixture 只能作为连线证据，不能伪造策略收益。

### 8.3 Trial

每个 Trial 保存 plan、参数、数据版本、状态、指标和日志 Artifact。搜索只能使用 train/validation；locked test 仅对少量候选开放，查看后修改必须标记污染。

### 8.4 AgentRun 与 ToolCall

AgentRun 保存 Provider、Execution Target、run mode、状态和计划摘要。ToolCall 保存领域工具名、脱敏输入/输出和状态。密钥、Cookie、账户标识和提现凭据不得进入 ToolCall 或事件 payload。

### 8.5 Pipeline 与 Fail-fast

Agent 先选择 `smoke`、`fast_screen` 或 `full_validation`，然后按
correctness、fast screen、viability、cheap sensitivity、regime/Pine、full validation/
locked/full stress、dry-run 前进。GateEvaluation 是独立事实；任何前置失败必须
`blocked` 或 `failed`，不可由对话跳过。

“少亏”只能创建 diagnostic improvement。组件证据与完整 StrategyOutcome 分开持久化；
失败策略不自动产生 validated factor。Regime 标签必须 ex ante，90 日只是 screening。
完整 Pine 对账后移，Pine 来源早期仍检查 repainting/MTF 和少量 golden trades。

## 9. 审计与可观察性

所有关键动作形成 append-only AuditEvent：

- 原始策略保存；
- AI/Agent Proposal；
- baseline 冻结；
- ExperimentPlan 创建/批准/拒绝；
- Job/Trial 状态；
- ToolCall；
- Artifact 创建；
- 报告；
- 暂停、取消、失败和审批。

Web 时间线只读这些结构化事件，不从聊天文本反推事实。SQLite Trigger 当前拒绝事件 UPDATE/DELETE；未来即使更换数据库也保留 append-only 语义。

## 10. Worker

Worker 是确定性执行层，不是自主 Agent。它接收已校验的 Job ID，加载不可变计划和版本，执行注册 Handler，写 Trial/Run/Report/Artifact 和日志。

当前 Worker 是 one-shot：无 `--job-id` 只打印能力/status 并退出，不得在 Web 显示为
正在常驻消费。Full stress Handler 必须自己复查 passed viability，不信任单独的 Agent 说明。

当前 Handler 限于 baseline、一个有边界的入场确认 Trial、通用 Batch router、成本压力测试和
ex-ante regime validation。新增或现有 Handler 都必须：

- 不使用任意 Shell payload；
- 创建实验 manifest；
- 写入数据版本、成本、切分和随机种子；
- 不把 funding/mark/index 缺口静默补零；
- 不启动 Freqtrade `trade`；
- 失败时保留错误和部分 Artifact。

## 11. External Agent 与 Embedded Agent 一致性

External Agent 可以通过 CLI/API/未来 MCP 工作；Embedded Agent 由 Worker 调用 AgentProvider。两者的差异只在 Provider adapter，不在业务权限：

- 都只能生成 draft/proposal；
- 都必须走文档路由；
- 都必须记录 AgentRun、ToolCall 和 AuditEvent；
- 都必须在 approval gate 暂停；
- 都不能覆盖 baseline、运行未批准参数搜索或实盘；
- 都通过 ArtifactStore 使用 project-relative key。

因此 Web 不是本地 Agent 的替代品，而是所有 Agent 模式共享的控制、审计和审批界面。

## 12. Phase 0/本轮边界

本轮实现文档路由、项目 Skill、端口、领域验证、SQLite 持久化和有预算 Batch 编排。
不实现完整 MCP Server、LLM 调用、Hyperopt/Optuna、Local Connector、Hosted Sandbox、
多用户或远程事件总线。

## 13. 模型能力与密钥契约

External Agent 与未来 Embedded/BYOK Provider 共用业务协议，但 Embedded/BYOK 模型还
必须满足 `configs/agents/agent-manifest.json`。门禁要求原生 tool calling、JSON Schema、
多轮 tool results、至少 32K context、指令层级和中英文；禁止 chat-only、提示词模拟工具、
free-text JSON 修补和静默弱模型降级。

Provider adapter 只转换厂商协议，不解释或放宽 ResearchToolGateway 权限。Key 禁止写入
浏览器持久存储、Git、项目配置、SQLite 和日志；本地未来只允许 OS Keychain、环境变量
和进程内存。当前 API 只有 manifest/status 只读端点，不提供 Key 接口或真实模型调用。
商业与分发边界见 [本地商业交付与模型兼容策略](23-本地商业交付与模型兼容策略.md)。

## 14. ResearchBudget、Regime mode 与 Worker budget

Agent 在批准新 hypothesis、创建 search/locked-test Job 前必须读取会话预算。超限只能记录
blocked，不得通过新对话或换 Provider 绕过。Regime diagnostic 可在 viability 前运行但
不得晋升；formal validation 必须引用同 subject 的 passed viability。

Freqtrade correctness 仅映射到 `run_correctness_diagnostic` 白名单工具，Worker 最终执行
固定命令并记录 Artifact。Worker 的时间/RSS/并发/batch 政策同样由代码执行，不依赖 Agent
承诺。详见 [阶段2研究门禁与资源预算](24-阶段2研究门禁与资源预算.md)。

## 15. Batch Proposal 与 Trial 执行

External/Embedded Agent 只能创建最多 3 个结构化 Proposal draft。用户批准精确 Proposal
subject 后，Repository 创建不可变 Candidate；之后 ExperimentPlan 才能进入批量执行。
Agent 不逐个执行 Trial，不在聊天中盲调。Worker 使用进程 executor 和确定性 evaluator，
按计划/会话/资源三层预算运行，并把指标批量写入 Parquet。

失败、取消和资源超限保留已完成 Trial。Retry 必须引用原 Job，仅恢复未完成组合，不再次
预留会话预算，也不覆盖已完成 Trial。组件聚合按规范化逻辑签名去重，不按参数值造因子，
最高自动状态为 component candidate。完整约束见
[批量策略研究闭环](25-批量策略研究闭环.md)。
