# ETHUSDT 永续 90 日数据质量摘要

- Manifest: `data/manifests/binance_ethusdt_perpetual_20260421_20260720.json`
- 精确窗口：2026-04-21T00:00:00Z（含）至 2026-07-20T00:00:00Z（不含）
- 请求主源：OKX；当前网络不可达。
- 实际可用源：Binance 官方历史归档，无密钥。
- 用途：工程验证、原样基准和候选初筛；不证明长期盈利。

| 数据集 | 周期 | 状态 | 行数 | 理论行数 | 缺口 | 重复 | OHLC异常 |
|---|---:|---|---:|---:|---:|---:|---:|
| futures_ohlcv | 5m | processed | 25920 | 25920 | 0 | 0 | 0 |
| futures_ohlcv | 15m | processed | 8640 | 8640 | 0 | 0 | 0 |
| futures_ohlcv | 1h | processed | 2160 | 2160 | 0 | 0 | 0 |
| futures_ohlcv | 4h | processed | 540 | 540 | 0 | 0 | 0 |
| mark_price | 15m | processed | 8544 | 8640 | 96 | 0 | - |
| index_price | 15m | processed | 8544 | 8640 | 96 | 0 | - |
| funding_rate | native | processed | 213 | 270 | 57 | 0 | - |
| open_interest_metrics | native | processed | 25918 | 25920 | 3 | 0 | - |

## 明确缺口

- OKX 当前网络不可达，未取得 OKX 对照数据。
- 杠杆阶梯、强平引擎、最小金额和精度规则尚未进入历史回测模型。
- 任何不可用的 funding/mark/index/OI 数据均在 manifest 中标为 unavailable，不按零处理。
