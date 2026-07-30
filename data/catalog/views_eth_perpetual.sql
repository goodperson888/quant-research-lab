CREATE OR REPLACE VIEW binance_ethusdt_futures_5m AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=futures_ohlcv/timeframe=5m/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_futures_15m AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=futures_ohlcv/timeframe=15m/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_futures_1h AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=futures_ohlcv/timeframe=1h/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_futures_4h AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=futures_ohlcv/timeframe=4h/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_mark_15m AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=mark_price/timeframe=15m/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_index_15m AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=index_price/timeframe=15m/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_funding AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=funding_rate/timeframe=native/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);

CREATE OR REPLACE VIEW binance_ethusdt_open_interest_5m AS
SELECT * FROM read_parquet(
  'data/processed/exchange=binance/market=usdt_perpetual/symbol=ETHUSDT/dataset=open_interest_metrics/timeframe=native/version=binance-vision-ethusdt-perpetual-20240720_20260720-v1/**/*.parquet',
  hive_partitioning = true
);
