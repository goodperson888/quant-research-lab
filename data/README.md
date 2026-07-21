# Data Layout

- `raw/`: immutable source downloads.
- `interim/`: cleaning and reconciliation outputs.
- `processed/`: versioned datasets used by backtests.
- `external/`: funding, open interest, macro, benchmark, or reference datasets.

Large generated data is ignored by Git. Each dataset should ship with a manifest containing source, market, symbol, timeframe, UTC range, download timestamp, row counts, validation statistics, and checksum.

Large market and derived tables should use Parquet. `catalog/quant.duckdb` is the recommended
local DuckDB query layer and is ignored by Git; its views must be reproducible from versioned
Parquet and manifests. CSV/JSON are for small human interchange or export, not authoritative
large-scale market storage.

The first actual dataset is Binance ETHUSDT USDT-margined perpetual market data for the UTC
window `[2026-04-21, 2026-07-20)`. Immutable official archive ZIPs are under
`raw/binance_vision/`; normalized partitioned Parquet is under `processed/exchange=binance/`;
the committed manifest is `manifests/binance_ethusdt_perpetual_20260421_20260720.json`.
OKX was requested as the primary source but was unavailable from the current network.
