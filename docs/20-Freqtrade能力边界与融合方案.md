# Freqtrade 能力边界与融合方案

状态日期：2026-07-22

## 1. 当前实际能力

开发环境通过可选 extra 固定 Freqtrade `2026.6`、CCXT `4.5.67`。实机审计确认：

- CLI 提供 `backtesting`、`backtesting-analysis`、`hyperopt`、`download-data`、
  `list-data`、`lookahead-analysis`、`recursive-analysis` 和 `trade`等命令；
- `ParquetDataHandler`、`CandleType`、`IStrategy`、`Backtesting` 和 PairLocks/
  protections 相关模块可导入；
- 本地 `list-data` 可识别 ETH/USDT:USDT 的 5m/15m/1h/4h futures，以及
  1h mark/index/funding 容器；
- Hyperopt CLI 存在，但当前 `freqtrade.optimize.hyperopt` 因未安装 Optuna
  不可导入；项目不因此自动安装或启用盲目调参。
- FreqAI 包路径存在，但没有配置模型、数据或运行入口，本阶段不启用。

## 2. 引擎权威边界

Native Engine 是默认商业核心，未安装 Freqtrade 时核心研究流程仍可运行。Freqtrade
不是商业包必需依赖，而是客户自行安装、以独立进程/受限 CLI/标准文件协议连接的可选
external engine。商业包不捆绑、复制或修改 Freqtrade，`src/quant_lab` 核心业务代码
不得直接 import Freqtrade。

| 用途 | 当前权威引擎 | 说明 |
|---|---|---|
| correctness/smoke | Native research engine | 保守的项目自有语义，易做单元测试和交易级解释 |
| fast screen/cheap sensitivity | Native research engine | 快速 kill test，支持自定义 funding 缺口政策和归因 |
| 当前已保存的 baseline/candidate/stress 证据 | Native research engine | 不得追溯性地改写为“Freqtrade 回测结果” |
| 第二引擎对账 | Freqtrade adapter | 核对策略接口、精度、费用、订单与交易序列 |
| full validation | Freqtrade adapter + Native | 只对 viability 通过的同一版本开放 |
| future dry-run | Freqtrade | 只有经完整门禁和显式批准后才能准备；P0 没有 live trade |

`BacktestEnginePort` 是通用边界，`NativeBacktestEngineAdapter` 包装现有
审阅过的 Job Handler；`FreqtradeBacktestEngineAdapter` 当前只提供能力声明和
可注入的确定性 executor，不直接 import Freqtrade，不暴露任意 Shell 或 `trade`。
未安装 external engine 时应报告 unavailable，而不是使 Native Engine 失效。

## 3. 功能重叠与取舍

- 回测：Freqtrade 覆盖策略接口、订单、费率、stoploss、报表和保护；Native
  保留项目特有的时点、资金费率缺口和 fail-fast 语义。
- 指标：Freqtrade 提供常规报告；项目 manifest 和 SQLite 状态仍是可审计事实源。
- Hyperopt：未经批准的 ExperimentPlan、预算、切分、目标和停止条件时禁止运行。
- Protections：未来可用于 full validation/dry-run，不取代项目独立风险门禁。
- FreqAI：本阶段不启用，不建设重型 ML 基础设施。

## 4. 2026-07-22 smoke 记录

通过用户指定的本机代理，Freqtrade/CCXT 成功加载 Binance 官方 metadata。
引擎 smoke 使用 `EngineSmokeStrategy`：

- 窗口：`2025-07-20T00:00:00Z` 至 `2025-07-22T00:00:00Z`；
- 数据：本地 15m futures Parquet；
- 费率：0.05%/side；
- 策略：测试 fixture，硬性不生成任何入场；
- 结果：引擎加载、数据加载和 backtesting 流程成功，0 笔交易；
- 结论：仅证明引擎连线和本地数据适配可用，不是策略收益或候选证据。

普通 `HTTP_PROXY/HTTPS_PROXY` 不会被 CCXT 的异步 aiohttp 自动采用。本次只在
`/tmp` 临时 overlay 中设置 `aiohttp_proxy`，没有把本机代理或密钥写入仓库。

## 5. 仍未实现

- 尚未将真实研究策略转成 Freqtrade `IStrategy` 并做逐笔对账；
- 尚未将 Freqtrade 结果自动导入 Experiment Run/Trial 注册；
- 尚未建立历史杠杆阶梯和强平模型；
- 尚未启用 Hyperopt、FreqAI 或 dry-run；
- `scripts/freqtrade.sh trade` 仍必须返回退出码 3。

## 6. 商业分发与许可证复核

Freqtrade 当前标注为 GPL-3.0。该许可证对最终制品、安装器、进程边界、协议和客户自行
安装流程的影响必须在正式收费前交由开源许可证律师复核。项目文档只记录工程事实和待
审查风险，不作法律结论。商业交付决策详见
[本地商业交付与模型兼容策略](23-本地商业交付与模型兼容策略.md)。
