# Freqtrade Runtime

`user_data/` 由 Freqtrade 2026.6 的 `create-userdir` 初始化。

- Freqtrade 生成的 sample strategy 只用于一次引擎启动检查，不作为研究资产提交；
  第一份策略必须从用户提供的规则建立并冻结原样基准。
- 第一阶段未安装 Hyperopt/FreqAI extras；生成的 Hyperopt 和 notebook 示例已移除。
- `config.json`、日志、数据库、行情和回测结果均为本地运行状态，由 `.gitignore`
  排除。
- 调用 Freqtrade 时优先使用 `scripts/freqtrade.sh`；该包装器默认拒绝 `trade`。
- `scripts/export_freqtrade_data.py` 从项目规范 Parquet 生成 Binance futures/mark/index/
  funding 本地 Parquet，不重复下载行情。
- Freqtrade `list-data` 已验证 5m/15m/1h/4h futures 数据；backtesting 因当前网络无法
  获取 Binance `exchangeInfo` 而停止，未绕过市场元数据校验。
