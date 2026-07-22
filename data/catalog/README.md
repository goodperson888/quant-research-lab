# Local Data Catalog

推荐的本地 DuckDB 查询层路径为 `data/catalog/quant.duckdb`。

该数据库、WAL 和临时文件由 `.gitignore` 排除。Parquet 与 manifest 是可重建的
权威数据层；DuckDB 只保存查询视图、聚合和本地分析状态。可复现的 schema、建视图
SQL 或迁移脚本应作为普通源码另行进入版本控制。

第一阶段已创建本地 `quant.duckdb`，由 Git 忽略。可重建视图定义位于
`views_eth_perpetual.sql`，小型行数/UTC 范围摘要位于 `catalog_summary.json`。当前视图
指向数据版本 `binance-vision-ethusdt-perpetual-20250720_20260720-v1`，精确窗口为
`[2025-07-20T00:00:00Z, 2026-07-20T00:00:00Z)`；90 日旧版本仍保留但不与年度视图混合。

DuckDB 属于 `configs/storage-policy.yaml` 定义的 `rebuildable` 区域。删除或损坏数据库时，
应从年度 manifest、分区 Parquet 和本 SQL 重建，不能反向把 DuckDB 当唯一原始数据。
