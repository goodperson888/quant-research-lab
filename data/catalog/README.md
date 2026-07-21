# Local Data Catalog

推荐的本地 DuckDB 查询层路径为 `data/catalog/quant.duckdb`。

该数据库、WAL 和临时文件由 `.gitignore` 排除。Parquet 与 manifest 是可重建的
权威数据层；DuckDB 只保存查询视图、聚合和本地分析状态。可复现的 schema、建视图
SQL 或迁移脚本应作为普通源码另行进入版本控制。

第一阶段已创建本地 `quant.duckdb`，由 Git 忽略。可重建视图定义位于
`views_eth_perpetual.sql`，小型行数/UTC 范围摘要位于 `catalog_summary.json`。
