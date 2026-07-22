# ETHUSDT 永续数据质量摘要

- Manifest: `data/manifests/binance_ethusdt_perpetual_20250720_20260720_v2.json`
- 精确窗口：2025-07-20T00:00:00+00:00（含）至 2026-07-20T00:00:00+00:00（不含）
- 代理使用：True（仅公开官方数据）。
- 实际行情源：Binance 官方历史归档，无密钥。
- 用途：工程验证、原样基准、候选初筛和市场状态覆盖；不证明长期盈利。

| 数据集 | 周期 | 状态 | 行数 | 理论行数 | 缺口 | 重复 | OHLC异常 |
|---|---:|---|---:|---:|---:|---:|---:|
| futures_ohlcv | 5m | processed | 105120 | 105120 | 0 | 0 | 0 |
| futures_ohlcv | 15m | processed | 35040 | 35040 | 0 | 0 | 0 |
| futures_ohlcv | 1h | processed | 8760 | 8760 | 0 | 0 | 0 |
| futures_ohlcv | 4h | processed | 2190 | 2190 | 0 | 0 | 0 |
| mark_price | 15m | processed | 34944 | 35040 | 96 | 0 | - |
| index_price | 15m | processed | 34944 | 35040 | 96 | 0 | - |
| funding_rate | native | processed | 1038 | 1095 | 57 | 0 | - |
| open_interest_metrics | native | processed | 105115 | 105120 | 56 | 0 | - |

## 明确缺口

- OKX 公共 metadata 可通过用户指定的本机代理访问；本轮范围仅扩展 Binance 一年历史，未下载 OKX 一年数据。
- 杠杆阶梯、强平引擎、最小金额和精度规则尚未进入历史回测模型。
- 任何不可用的 funding/mark/index/OI 数据均在 manifest 中标为 unavailable，不按零处理。
