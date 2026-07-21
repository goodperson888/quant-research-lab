# ETH 永续第一阶段数据记录

## 研究范围

- 资产：ETH；
- 工具：USDT 本位永续合约；
- 请求主研究源：OKX；
- 跨交易所稳健性与数据后备：Binance；
- 第一候选执行周期：15m；
- 市场状态/趋势过滤：1h、4h；
- 执行细化或敏感性：5m；
- 精确窗口：`2026-04-21T00:00:00Z`（含）至
  `2026-07-20T00:00:00Z`（不含），90 个完整 UTC 日。

不是所有策略都必须使用全部周期。多周期信号必须只使用决策时点已经完整收盘的数据，
禁止把未来完成的低周期聚合或高周期收盘值回填到当前时点。

## 实际数据源

Freqtrade/CCXT 访问 OKX `www.okx.com` 和 Binance `fapi.binance.com` 的公开市场接口时
均出现超时或 Host Down。未使用密钥，也没有使用代理、伪造市场元数据或其他绕过。

Binance 官方历史归档 `data.binance.vision` 可访问，因此本次使用它完成工程验证数据。
OKX 仍保留为目标主研究源，网络条件允许后再补充相同窗口并做跨所对照。

统一符号为 `ETH/USDT:USDT`；OKX 原生符号为 `ETH-USDT-SWAP`，Binance 原生符号为
`ETHUSDT`。Binance 原生符号已由官方归档实际文件验证；实时市场 metadata 校验因网络
超时未完成。

## 数据质量

| 数据集 | 周期 | 实际行数 | 理论行数 | 缺口 | 重复 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| futures OHLCV | 5m | 25,920 | 25,920 | 0 | 0 | OHLC/成交量检查通过 |
| futures OHLCV | 15m | 8,640 | 8,640 | 0 | 0 | 第一候选执行周期 |
| futures OHLCV | 1h | 2,160 | 2,160 | 0 | 0 | 市场状态候选 |
| futures OHLCV | 4h | 540 | 540 | 0 | 0 | 市场状态候选 |
| mark price | 15m | 8,544 | 8,640 | 96 | 0 | 缺 2026-06-29 全天，官方日包 404 |
| index price | 15m | 8,544 | 8,640 | 96 | 0 | 缺 2026-06-29 全天，官方日包 404 |
| funding rate | 8h 事件 | 213 | 270 | 57 | 0 | 只到 2026-06-30 16:00 UTC；7 月日包不存在 |
| open interest metrics | 5m | 25,918 | 25,920 | 3 | 0 | 首个时点从 00:05 开始，另有少量缺口 |

4 个交易 OHLCV 周期均为时间单调、零重复、零缺口、零负成交量、零 OHLC 关系异常。
详细 URL、ZIP SHA-256、Parquet SHA-256、文件大小和缺口样本见：

- `data/manifests/binance_ethusdt_perpetual_20260421_20260720.json`；
- `reports/data_quality/ethusdt_perpetual_stage1.md`；
- `data/catalog/catalog_summary.json`。

## 存储

- 原始官方 ZIP：`data/raw/binance_vision/`，约 4.0 MiB，只追加不覆盖；
- 规范 Parquet：`data/processed/exchange=binance/`，约 4.8 MiB；
- Freqtrade 本地 Parquet：`runtime/freqtrade/user_data/data/binance/`，约 1.5 MiB；
- DuckDB 查询层：`data/catalog/quant.duckdb`，可由
  `data/catalog/views_eth_perpetual.sql` 重建，不是唯一数据源。

所有大数据和本地数据库均由 Git 忽略；只提交 manifest、SQL、配置和小型质量摘要。

## 成本模型

当前仅为保守默认假设，后续按实际账户等级和交易规则修正：

- maker：0.02%/side；
- taker：0.05%/side；
- 基准回测使用 taker 0.05%/side；
- 基准滑点：2 bps/side；
- 滑点压力：5 bps/side；
- 费用与滑点同时翻倍压力。

因此基准单边交易摩擦为 7 bps、往返约 14 bps，均不含资金费率；翻倍压力往返约
28 bps，也不含资金费率。资金费率必须按实际数据计入，缺失区间不得按零处理。

尚未完整建模：强平引擎、杠杆阶梯、最小金额、数量/价格精度、标记价格异常和交易所
故障。研究默认 `leverage=1`，不使用杠杆放大利润，实盘关闭。

## Freqtrade 验证

Freqtrade 已离线识别 7 组本地数据：4 组 futures OHLCV、1h mark、1h index 和 1h
funding 容器。尝试运行 15m 示例策略 backtest smoke 时，配置验证和本地数据准备通过，
但引擎仍需要 Binance `exchangeInfo` 获取市场精度和限制；该公开接口当前网络超时，
因此 backtest 未启动。

没有把示例策略结果当作收益，没有自动优化，也没有通过伪造 metadata 或零资金费率
绕过限制。

## 使用边界与下一步

90 日 5m/15m 数据足以验证工程流程并进行候选初筛，但不足以覆盖完整牛熊、极端波动、
交易所故障和长期资金费率状态。出现候选后必须扩展更长历史、补 OKX、增加不同市场
状态并重新运行样本外、Walk-forward、成本、敏感性和压力测试。

下一步需要用户提供第一份自然语言策略、Pine Script 或公开策略规则。先冻结原样基准，
再一次只验证一个改进假设。
