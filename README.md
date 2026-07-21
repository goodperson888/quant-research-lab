# Quant Research Lab

一个集中管理数据、因子、策略、回测、实验记录和自动化任务的本地量化研究工程。

## 个人轻量策略研究模式

本项目服务个人研究者，采用“策略优先、因子随策略研究自然沉淀”的模式：先把
自然语言、Pine Script、公开资料或书籍视频中的策略原样收录并冻结基准，再用一次
一个假设、可消融、可复现的实验逐步验证。因子库、失败库和策略库是长期研究资产，
不是每日盲目挖掘的产量目标。

第一阶段只建设统一数据与成本、可重复回测、策略版本和实验注册、样本外与压力
测试、简单组件库、报告和模拟盘门禁；不启用重型机器学习、大规模因子工厂、暴力
组合搜索或自动生产晋升。完整原则见
[docs/07-个人量化策略研究工作法.md](docs/07-个人量化策略研究工作法.md)。

## 数据存储方案

个人阶段采用混合架构：大体量行情和衍生数据使用 Parquet；DuckDB 作为本地查询、
跨 Parquet 聚合和报告视图层；SQLite 保存因子、策略、实验和状态等注册元数据；
JSON/YAML 保存配置与 manifest。Parquet 是可重建分析数据的权威层，DuckDB 不替代
原始文件。当前不部署 PostgreSQL、ClickHouse 或其他服务。详见
[docs/09-数据存储架构.md](docs/09-数据存储架构.md)。

## 市场适配原则

因子拆分为“通用思想、市场适配实现、市场独有数据”三层。同名或同公式因子不能跨
市场继承 `validated`/`production` 结论，必须按资产类别、工具类型、交易场所、周期
和成本独立验证。第一阶段已选择 OKX ETH-USDT 永续为请求主研究源、Binance 为跨所
对照源；由于当前网络无法访问两家的实时公共 API，本次实际数据来自 Binance 官方
历史归档。后续下载仍必须明确选择 market profile。详见
[docs/10-市场适配与因子适用性.md](docs/10-市场适配与因子适用性.md)。

## 安全边界

- 默认只允许历史回测和模拟盘。
- 未经明确人工批准，不得发送真实订单。
- API 密钥不得写入代码、配置、日志或版本库。
- 实盘密钥必须关闭提现权限，并设置 IP 白名单。
- “验证通过”只代表历史和模拟测试合格，不代表未来盈利。

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
- ETH-USDT USDT 本位永续的 90 个完整 UTC 日初筛数据；
- 可重建 DuckDB 视图和 Freqtrade 本地 Parquet 数据。

第一阶段依赖固定为 Python 3.12、研究数据栈、QuantStats、pytest 和基础
Freqtrade。Hyperopt/FreqAI/vectorbt 暂不安装。

当前数据窗口为 `2026-04-21T00:00:00Z`（含）至
`2026-07-20T00:00:00Z`（不含）：

- Binance ETHUSDT 永续交易 OHLCV：5m/15m/1h/4h 全覆盖；
- mark/index 15m 各缺 2026-06-29 的 96 根；
- funding 只覆盖至 2026-06-30 16:00 UTC；
- OI metrics 缺 3 个 5m 时点；
- OKX 公共 API 当前网络不可达，尚无 OKX 对照数据。

这 90 日只用于工程验证、原样基准和候选初筛，不能证明长期盈利，也不足以覆盖完整
牛熊周期。详见 [docs/11-ETH永续第一阶段数据记录.md](docs/11-ETH永续第一阶段数据记录.md)。

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
  --profile configs/market_profiles/crypto_perpetual.binance.eth.yaml
.venv/bin/python scripts/export_freqtrade_data.py
.venv/bin/python scripts/build_data_catalog.py
```

## 推荐落地顺序

1. 用户提供第一份自然语言或 Pine Script 策略。
2. 原样收录并冻结 15m 基准版本。
3. 定义训练、验证和锁定测试切分；90 日数据只用于初筛。
4. 先完成基准回测，再一次验证一个改进假设。
5. 出现候选后扩展更长历史、OKX 数据和更多市场状态。
6. 通过样本外、成本和压力测试后进入模拟盘。
7. 流程稳定后再配置每日自动任务。

详细规范见 [docs/00-标准研究流程.md](docs/00-标准研究流程.md)。
本机安装和版本记录见 [docs/08-本地安装与依赖记录.md](docs/08-本地安装与依赖记录.md)。
