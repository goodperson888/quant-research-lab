# Product Runtime

`runtime/app/quant_lab.sqlite3` is the local product-state database for research
sessions, immutable strategy baselines, jobs and reports. SQLite WAL and log files
also stay in this directory.

All generated files here are excluded from Git. The database is not the market-data
authority: Parquet remains the research-data layer, DuckDB remains rebuildable, and
the existing factor registry remains available through its compatibility adapter.

No credentials or live-trading settings belong in this directory.
