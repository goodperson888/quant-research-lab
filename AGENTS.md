# Quant Research Lab Agent Rules

These rules apply to every automated agent working in this project.

1. Historical research and dry-run are allowed by default. Live trading is forbidden unless the user explicitly authorizes the exact exchange, account, strategy, capital limit, and time window.
2. Never store API keys, secrets, cookies, passwords, or withdrawal credentials in this repository.
3. Never request or use an exchange API key with withdrawal permission.
4. Raw data under `data/raw/` is immutable. Corrections create a new version; they do not overwrite the original.
5. Every backtest or optimization run must create a manifest under `experiments/runs/` containing code version, data version, parameters, costs, time range, random seed, and result paths.
6. The locked final test period must not be used for parameter tuning. If it is inspected and then used to change the strategy, mark it contaminated and create a newer locked period.
7. Never promote a factor or strategy solely because it has the highest historical profit. Require out-of-sample, cost, sensitivity, and stress-test evidence.
8. Automated research may mark items as `candidate`, `validated`, `degraded`, `retired`, or `rejected`. Promotion to `production` requires explicit human approval.
9. Record failed experiments. Do not delete or hide negative results.
10. Prefer reproducible commands, deterministic seeds, and machine-readable output.
11. This is a personal, lightweight, strategy-first research lab. Factors are accumulated only as reusable by-products of strategy research; do not turn routine automation into blind factor invention.
12. Freeze and preserve the original baseline strategy before any optimization. Never rewrite or overwrite the baseline to improve historical results.
13. Test one explicit improvement hypothesis at a time. Complex changes require ablation evidence that identifies each component's incremental contribution.
14. Never tune indefinitely merely to make a backtest profitable. Record failed hypotheses, stopping criteria, and negative results.
15. Daily automation may monitor data, factors, and existing strategies, but must not invent strategies or promote any strategy to production.
16. Keep the first phase lightweight. Do not introduce heavy machine learning, large-scale factor mining, brute-force search, or institution-scale data infrastructure unless the user explicitly requests it.
17. Store large market and derived columnar datasets in Parquet by default. Do not create large CSV or JSON datasets without a documented interchange-only reason.
18. Raw market data is immutable and must have a manifest with UTC semantics, source, download time, range, row counts, quality statistics, checksum, cleaning lineage, and data version.
19. Do not download, combine, or compare market data until an explicit market profile defines the asset class, instrument type, venue, symbols, timeframes, required datasets, and cost model.
20. A factor or strategy validated in one market profile is not validated in another. Validation and production status must remain separate by asset class, instrument type, venue or broker, timeframe, and cost model.
21. Never describe FX tick volume as global real traded volume. Never treat a futures continuous series as a directly tradable contract. Stock research must handle corporate actions, delistings, and survivorship bias. Perpetual-futures research must handle funding, mark/index prices, liquidation mechanics, and venue-specific leverage rules.
22. Do not introduce PostgreSQL, TimescaleDB, ClickHouse, InfluxDB, Redis, or other data services in the first phase without a demonstrated scale requirement and explicit user approval.
