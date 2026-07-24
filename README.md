# Quant Research Lab

一个集中管理数据、因子、策略、回测、实验记录和自动化任务的本地量化研究工程。

## 第一次阅读

- [产品说明与使用指南](docs/11-产品说明与使用指南.md)：先了解项目为谁服务、如何使用和不做什么；
- [系统架构](docs/12-系统架构.md)：查看模块、数据流、策略流和安全边界；
- [当前状态与路线图](docs/13-当前状态与路线图.md)：查看真实完成度、阻塞和下一步；
- [个人量化策略研究工作法](docs/07-个人量化策略研究工作法.md)：理解策略优先和防过拟合原则；
- [数据存储架构](docs/09-数据存储架构.md)：理解 Parquet、DuckDB 和 SQLite 分工；
- [市场适配与因子适用性](docs/10-市场适配与因子适用性.md)：理解按市场独立验证；
- [完整文档地图](docs/README.md)：按流程、数据、治理和安装查找文档。
- [产品需求规格 v1](docs/15-产品需求规格-v1.md)：查看已冻结的 Agent-first 产品需求和验收；
- [产品化目标架构 v1](docs/16-产品化目标架构-v1.md)：查看 Web、External Agent、API、Worker 和审计控制平面；
- [架构重构实施计划](docs/17-架构重构实施计划.md)：查看阶段0纵切和后续功能渐进路线。
- [Agent 与 Skill 执行架构](docs/18-Agent与Skill执行架构.md)：查看 Agent、Skill、文档路由、工具白名单和状态机如何共同约束执行。
- [本地启动与操作手册](docs/19-本地启动与操作手册.md)：两个终端启动 API/Web、one-shot Worker、排错与备份。
- [Freqtrade 能力边界与融合方案](docs/20-Freqtrade能力边界与融合方案.md)：区分 Native 与 Freqtrade 引擎职责；
- [存储治理与保留策略](docs/21-存储治理与保留策略.md)：查看权威、可重建、可归档数据和 Trial 保留规则；
- [ETH 永续一年数据扩展记录](docs/22-ETH永续一年数据扩展记录.md)：查看当前年度数据、缺口、metadata 和代理结果。
- [本地商业交付与模型兼容策略](docs/23-本地商业交付与模型兼容策略.md)：查看本地交付、许可证到期、模型门禁、密钥和 Freqtrade 商业边界。
- [阶段2研究门禁与资源预算](docs/24-阶段2研究门禁与资源预算.md)：查看 Regime 双模式、正确性诊断、会话与 Worker 硬预算。
- [批量策略研究闭环](docs/25-批量策略研究闭环.md)：查看 Proposal、批量 Trial、稳定区间、组件证据和恢复语义。

## 产品化阶段 0

产品首页正在演进为“AI 策略研究工作台”。当前采用 Agent-first hybrid architecture：Codex 等 External Local Agent 继续作为可见研究执行者，Web 负责控制、审批、审计和结果展示；二者共享 FastAPI/CLI 领域接口与 append-only 审计记录。当前固定为 `external_local_agent + local_runtime`，Embedded Provider、Hosted Sandbox、Local Connector 和真实 LLM 调用均未实现。

阶段0开发入口：

```bash
./scripts/dev-api.sh
./scripts/dev-web.sh
./scripts/dev-worker.sh
```

API 默认仅监听 `127.0.0.1:8000`，Web 默认监听 `127.0.0.1:3000`。根页面进入 `/studio`。
`dev-worker.sh` 是 one-shot：无 `--job-id` 只打印 status 并退出，不是正在运行的常驻队列。
指定 `--job-id job_xxx` 时才执行一个白名单 Job。

商业首版计划以客户本地安装包或受管容器交付 Web Studio，不交付 Git 仓库。Native
Engine 是默认核心；Freqtrade 是客户自行安装、通过独立进程/标准文件协议连接的可选
external engine，不随商业包捆绑。模型必须满足
`capability_gated_modern_models_only` 契约，当前 Provider 仍未配置且不提供 Key 录入。

项目级 Agent 编排 Skill 位于 `.agents/skills/quant-strategy-research/`。所有研究动作先按 `configs/agent_policies/document-routing.yaml` 选择 intent、读取必需文档、检查前置条件和审批门禁；安全不只依赖提示词，后端状态机、Repository、ArtifactStore、Job 白名单和测试共同执行约束。

### 研究状态与 Git 解耦

研究事实以 SQLite 产品状态、项目相对 Artifact、checksum、Approval 和 append-only audit
为权威。Intake、形式化、冻结 Baseline、Gate 和 Job 不会自动 commit 或 push；冻结 Baseline
不等于 Git commit。本地/客户产品不需要 `.git` 或 remote 也能完成研究流程。

Git 只作为人工备份和代码发布通道：仅在用户明确要求备份/发布、显式导出完整研究会话，
或项目代码 release milestone 时使用。现有已跟踪策略历史继续保留，不重写、不删除。
机器可读规则见 [`configs/versioning-policy.yaml`](configs/versioning-policy.yaml)。Studio 与
Settings 显示 `Local authoritative / Git backup manual`。

所有暂停和停止都会生成 `ResearchHandoff`，明确“为什么停在这里、已完成、未执行、需要
用户做什么、精确 subject ID 和下一步”。`scripts/freqtrade.sh trade` 的退出码 3 在报告中
归一化为：`Live trade safety guard: PASS (expected rejection, exit code 3)`。

## 个人轻量策略研究模式

本项目服务个人研究者，采用“策略优先、因子随策略研究自然沉淀”的模式：先把
自然语言、Pine Script、公开资料或书籍视频中的策略原样收录并冻结基准，再用一次
一个假设、可消融、可复现的实验逐步验证。因子库、失败库和策略库是长期研究资产，
不是每日盲目挖掘的产量目标。

第一阶段只建设统一数据与成本、可重复回测、策略版本和实验注册、样本外与压力
测试、简单组件库、报告和模拟盘门禁；不启用重型机器学习、大规模因子工厂、暴力
组合搜索或自动生产晋升。完整原则见
[docs/07-个人量化策略研究工作法.md](docs/07-个人量化策略研究工作法.md)。

Pipeline Profile 配置于 `configs/pipelines/`，顺序为 correctness → fast screen → viability →
cheap sensitivity → regime/Pine → full validation/locked/full stress → dry-run。只比 baseline
少亏的结果只作为 diagnostic improvement，不是完整可交易 strategy candidate。

Regime 在 viability 前只能使用 `regime_diagnostic`，正式 `regime_validation` 必须引用同一
subject 的 passed viability。每个 ResearchSession 和 one-shot Worker 都受机器可读预算
限制；Freqtrade lookahead/recursive 只作为可选外部引擎的 correctness 证据。

冻结 Baseline 后，系统支持最多 3 个结构化改进方向、精确 subject 审批、不可变 Candidate、
确定性 grid/seeded-random Batch Trials、稳定参数区间和 ComponentEvidence 逻辑签名去重。
当前真实策略 evaluator 尚未通用化；内置 deterministic fixture 只验证连线，不能作为收益证据。

## 数据存储方案

个人阶段采用混合架构：大体量行情和衍生数据使用 Parquet；DuckDB 作为本地查询、
跨 Parquet 聚合和报告视图层；SQLite 保存因子、策略、实验和状态等注册元数据；
JSON/YAML 保存配置与 manifest。Parquet 是可重建分析数据的权威层，DuckDB 不替代
原始文件。当前不部署 PostgreSQL、ClickHouse 或其他服务。详见
[docs/09-数据存储架构.md](docs/09-数据存储架构.md) 和
[docs/21-存储治理与保留策略.md](docs/21-存储治理与保留策略.md)。

## 市场适配原则

因子拆分为“通用思想、市场适配实现、市场独有数据”三层。同名或同公式因子不能跨
市场继承 `validated`/`production` 结论，必须按资产类别、工具类型、交易场所、周期
和成本独立验证。第一阶段已选择 OKX ETH-USDT 永续为请求主研究源、Binance 为跨所
对照源；当前一年历史实际来自 Binance 官方归档。Binance/OKX 实时 metadata 直连超时，
但已通过用户指定的本机代理取得并缓存官方公开快照；没有下载 OKX 一年历史。后续下载
仍必须明确选择 market profile。详见
[docs/10-市场适配与因子适用性.md](docs/10-市场适配与因子适用性.md)。

## 安全边界

- 默认只允许历史回测和模拟盘。
- 未经明确人工批准，不得发送真实订单。
- API 密钥不得写入代码、配置、日志或版本库。
- 实盘密钥必须关闭提现权限，并设置 IP 白名单。
- “验证通过”只代表历史和模拟测试合格，不代表未来盈利。
- Live trade wrapper 返回退出码 3 是预期安全门禁通过，不是待修复故障。

## 目录

```text
configs/            研究、市场和风险配置
data/raw/           原始数据，只追加不修改
data/interim/       清洗中的中间数据
data/processed/     可供回测使用的数据
data/external/      外部参考数据
docs/               标准流程、验收标准和注意事项
experiments/        每次实验的不可变记录
factor_library/     候选、验证、生产、衰减和淘汰因子
reports/            日报、周报、回测和风险报告
runtime/            Freqtrade 等运行时文件
src/quant_lab/      本工程自动化代码
strategies/         Pine、Freqtrade 和研究策略
tests/              自动测试
vendor/             外部开源项目或源码归档
automation/         定时任务模板
```

## 当前状态

第一阶段工程骨架已经建立。它包含：

- 标准研究流程；
- 数据、回测和因子治理规范；
- SQLite 因子与实验注册表；
- 每日、每周任务入口；
- Freqtrade/研究环境的隔离安装位置；
- 禁止自动实盘的工程级约束。
- ETH-USDT USDT 本位永续的 365 个完整 UTC 日研究数据，并保留原 90 日版本；
- 可重建 DuckDB 视图和 Freqtrade 本地 Parquet 数据。
- 只读 storage policy/report、ex-ante regime detector 和 Native/Freqtrade 引擎边界。

第一阶段核心依赖固定为 Python 3.12、研究数据栈、QuantStats 和 pytest；开发环境另有
固定版 Freqtrade 可选 extra，用于数据兼容和外部引擎诊断，不是商业核心必需依赖。
Hyperopt/FreqAI/vectorbt 暂不安装。

当前权威年度数据窗口为 `2025-07-20T00:00:00Z`（含）至
`2026-07-20T00:00:00Z`（不含）：

- Binance ETHUSDT 永续交易 OHLCV：5m/15m/1h/4h 全覆盖；
- mark/index 15m 各缺 2026-06-29 的 96 根；
- funding 只覆盖至 2026-06-30 16:00 UTC；
- OI metrics 在精确 5m 网格缺 56 个时点，其中 51 个官方时间戳偏离网格；
- OKX/Binance 官方 metadata 可通过本机代理访问，但尚无 OKX 一年历史对照。

一年数据改善了 regime screening 和 Walk-forward 设计条件，但仍不能证明长期盈利或
覆盖可重复的完整市场周期。详见
[docs/22-ETH永续一年数据扩展记录.md](docs/22-ETH永续一年数据扩展记录.md)；原 90 日证据
保留在 [docs/14-ETH永续第一阶段数据记录.md](docs/14-ETH永续第一阶段数据记录.md)。

## 快速检查

系统 Python 仅用于紧急运行基础管理工具；完整环境固定在项目目录：

```bash
cd /Users/laochen/Documents/lianghua/quant-research-lab
PYTHONPATH=src python3 -m quant_lab.cli doctor
PYTHONPATH=src python3 -m quant_lab.cli init
PYTHONPATH=src python3 -m quant_lab.cli list-factors
```

安装隔离研究环境后：

```bash
./scripts/bootstrap.sh
./scripts/run_daily.sh
./scripts/run_weekly.sh
```

开发验证分级：

```bash
make check-fast
make check-standard
make check-release
```

环境位置：

- uv：`.tools/bin/uv`；
- uv 管理的 Python：`.tools/python/`；
- 虚拟环境：`.venv/`；
- 缓存：`.cache/`；
- 精确依赖锁：`uv.lock`。

手动调用 uv 时使用 `./scripts/uv.sh`，确保缓存和 Python 安装目录仍位于项目内。

Freqtrade 必须优先通过安全包装器调用：

```bash
./scripts/freqtrade.sh --version
./scripts/freqtrade.sh backtesting --help
```

包装器默认拒绝 `trade` 命令，避免误启动交易循环。

本阶段数据可复现命令：

```bash
.venv/bin/python scripts/download_binance_vision.py \
  --profile configs/market_profiles/crypto_perpetual.binance.eth.yaml \
  --start-utc 2025-07-20T00:00:00Z \
  --end-utc 2026-07-20T00:00:00Z \
  --data-version binance-vision-ethusdt-perpetual-20250720_20260720-v1 \
  --manifest-name REPLACE_WITH_NEW_IMMUTABLE_MANIFEST.json \
  --report-name REPLACE_WITH_NEW_IMMUTABLE_REPORT.md \
  --plan-only
.venv/bin/python scripts/export_freqtrade_data.py \
  --data-version binance-vision-ethusdt-perpetual-20250720_20260720-v1
.venv/bin/python scripts/build_data_catalog.py
PYTHONPATH=src .venv/bin/python -m quant_lab.cli storage-report
```

现有年度 manifest 不可覆盖；复现或修订必须使用新文件名。`--plan-only` 不发起网络下载。

## 推荐落地顺序

1. 用户提供下一份自然语言/Pine 策略，或对已有 baseline 提出一个新假设。
2. 新策略原样收录并冻结基准；新假设必须引用现有不可变 baseline。
3. 定义训练、验证和锁定测试切分；一年数据仍只提供有限历史证据。
4. 先完成基准回测，再一次验证一个改进假设。
5. 出现可行候选后再扩展更长历史、OKX 数据和更多市场状态。
6. 通过样本外、成本和压力测试后进入模拟盘。
7. 流程稳定后再配置每日自动任务。

详细规范见 [docs/00-标准研究流程.md](docs/00-标准研究流程.md)。
本机安装和版本记录见 [docs/08-本地安装与依赖记录.md](docs/08-本地安装与依赖记录.md)。
