# ETH 永续一年与两年数据扩展记录

状态日期：2026-07-30

## 1. 范围与来源

- Market Profile：`crypto_perpetual.binance.eth`；
- 符号：`ETH/USDT:USDT`（Binance `ETHUSDT`）；
- 精确窗口：`2025-07-20T00:00:00Z` inclusive 至
  `2026-07-20T00:00:00Z` exclusive；
- 共 365 个完整 UTC 日；
- 行情源：Binance 官方 `data.binance.vision` USDT-M futures 归档；
- 密钥：不需要、未使用；
- 下载：582 个计划项，338 新下载，225 复用不可变 raw，19 个官方不可用。

下载前盘空间约 165.5 GiB，因此保留 5m，无需降级为只下载
15m/1h/4h。raw 只追加，年度 processed 使用独立 `version=` 分区，不与 90 日
产物混合。

## 2. 数据质量

| 数据集 | 周期 | 实际行数 | 理论行数 | 缺口 | 重复 | 说明 |
|---|---:|---:|---:|---:|---:|---|
| futures OHLCV | 5m | 105,120 | 105,120 | 0 | 0 | OHLC 异常 0 |
| futures OHLCV | 15m | 35,040 | 35,040 | 0 | 0 | OHLC 异常 0 |
| futures OHLCV | 1h | 8,760 | 8,760 | 0 | 0 | regime detector 默认输入 |
| futures OHLCV | 4h | 2,190 | 2,190 | 0 | 0 | 高周期状态参考 |
| mark price | 15m | 34,944 | 35,040 | 96 | 0 | 缺 2026-06-29 全天 |
| index price | 15m | 34,944 | 35,040 | 96 | 0 | 缺 2026-06-29 全天 |
| funding rate | 8h event | 1,038 | 1,095 | 57 | 0 | 只至 2026-06-30 16:00 UTC |
| OI metrics | 5m source | 105,115 | 105,120 | 精确网格缺 56 | 0 | 51 个官方时间戳偏离整 5 分钟 |

OI 没有被擅自取整。manifest v2 同时记录 `missing_intervals=56` 和
`off_grid_timestamps=51`，便于未来在 interim 层建立明确的对齐版本。

## 3. 权威产物

- 当前 manifest：
  `data/manifests/binance_ethusdt_perpetual_20250720_20260720_v2.json`；
- v1 manifest：保留为首次生成证据，v2 修正 OKX 网络描述并增加 OI
  off-grid 统计；
- 数据版本：`binance-vision-ethusdt-perpetual-20250720_20260720-v1`；
- 质量报告：
  `reports/data_quality/binance_ethusdt_perpetual_20250720_20260720_v2.md`；
- DuckDB 视图：`data/catalog/views_eth_perpetual.sql`；
- 小型目录摘要：`data/catalog/catalog_summary.json`。

raw 约 13.2 MiB，全部 processed 约 24.3 MiB，Freqtrade 可重建本地数据约
6.3 MiB。大文件、DuckDB 和 Freqtrade runtime 不进入 Git。

## 4. metadata 与代理结果

- `data.binance.vision`：直连 200，通过代理也为 200；
- Binance `fapi/v1/exchangeInfo`：直连超时，通过本机代理 200；
- OKX `public/instruments`：直连超时，通过本机代理 200；
- 官方 metadata manifest：
  `data/manifests/market_metadata_eth_perpetual_20260722.json`。

当次 Binance metadata 确认 ETHUSDT 为 `PERPETUAL/TRADING`，价格 tick `0.01`，
数量 step/minQty `0.001`，最小名义金额 `20 USDT`。OKX 确认
`ETH-USDT-SWAP` 为 live linear swap。这些是抓取时点的公开快照，不代表
历史杠杆阶梯或用户费率。

## 5. 研究边界

一年数据已经比 90 日更适合做 regime screening、Walk-forward 设计和稳定性检查，
但仍不得声称“长期验证”或未来盈利。funding、mark/index 缺口必须由明示政策
处理，不得填 0。本轮没有下载 OKX 一年行情，不得把 Binance 结论传播为
OKX validated。

## 6. 两年扩展

2026-07-30 在用户明确授权公开网络下载后，将 Binance ETHUSDT USDT 本位永续窗口扩展为：

```text
[2024-07-20T00:00:00Z, 2026-07-20T00:00:00Z)
```

共 730 个完整 UTC 日。下载计划包含 1,031 个官方归档项：复用 563 个不可变 raw，
新下载 449 个，19 个官方归档不可用。使用本机代理只访问公开 Binance Vision 文件，
未使用密钥，代理地址未写入配置。

| 数据集 | 周期 | 实际行数 | 理论行数 | 已知缺口 |
|---|---:|---:|---:|---|
| futures OHLCV | 5m | 210,240 | 210,240 | 0 |
| futures OHLCV | 15m | 70,080 | 70,080 | 0 |
| futures OHLCV | 1h | 17,520 | 17,520 | 0 |
| futures OHLCV | 4h | 4,380 | 4,380 | 0 |
| mark price | 15m | 69,984 | 70,080 | 缺 2026-06-29 全天 96 根 |
| index price | 15m | 69,984 | 70,080 | 缺 2026-06-29 全天 96 根 |
| funding rate | 8h event | 2,133 | 2,190 | 缺 57，只到 2026-06-30 16:00 UTC |
| OI metrics | 5m source | 210,233 | 210,240 | 精确网格缺 58；51 个官方时间戳 off-grid |

权威两年证据：

- 下载配置：`configs/data_downloads/binance_ethusdt_perpetual_2y.yaml`；
- data version：`binance-vision-ethusdt-perpetual-20240720_20260720-v1`；
- manifest：`data/manifests/binance_ethusdt_perpetual_20240720_20260720_v2.json`；
- 质量报告：`reports/data_quality/binance_ethusdt_perpetual_20240720_20260720_v2.md`；
- processed Parquet：199 个文件，约 40.8 MB；
- raw Binance Vision：当前合计约 27.2 MB。

两年数据适合更有意义的滚动验证与行情筛选，但仍不等于长期验证，不证明未来盈利。
OKX 同期历史尚未下载，Binance 结论仍不得传播为 OKX validated。原一年和 90 日数据、
manifest 与报告均保留，未被覆盖。
