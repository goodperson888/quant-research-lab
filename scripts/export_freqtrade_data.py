#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from freqtrade.data.history.datahandlers.parquetdatahandler import ParquetDataHandler
from freqtrade.enums import CandleType


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = (
    ROOT
    / "data"
    / "processed"
    / "exchange=binance"
    / "market=usdt_perpetual"
    / "symbol=ETHUSDT"
)
DATADIR = ROOT / "runtime" / "freqtrade" / "user_data" / "data" / "binance"
PAIR = "ETH/USDT:USDT"


def load(dataset: str, timeframe: str, *, data_version: str) -> pd.DataFrame:
    version_root = (
        PROCESSED
        / f"dataset={dataset}"
        / f"timeframe={timeframe}"
        / f"version={data_version}"
    )
    paths = sorted(
        version_root.glob("year=*/month=*/*.parquet")
    )
    if not paths:
        raise FileNotFoundError(f"No processed files for {dataset} {timeframe}")
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def to_freqtrade_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.rename(columns={"timestamp": "date"}).copy()
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
    result["date"] = pd.to_datetime(result["date"], utc=True)
    return result[["date", "open", "high", "low", "close", "volume"]]


def resample_ohlcv(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    source = to_freqtrade_ohlcv(frame).set_index("date")
    result = source.resample(rule, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return result.dropna(subset=["open", "high", "low", "close"]).reset_index()


def funding_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    rate_column = next(
        column
        for column in ("last_funding_rate", "fundingrate", "funding_rate")
        if column in frame.columns
    )
    rate = pd.to_numeric(frame[rate_column], errors="raise").astype(float)
    result = pd.DataFrame(
        {
            "date": pd.to_datetime(frame["timestamp"], utc=True),
            "open": rate,
            "high": rate,
            "low": rate,
            "close": rate,
            "volume": 0.0,
        }
    )
    return result.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-version",
        default="binance-vision-ethusdt-perpetual-20250720_20260720-v1",
    )
    args = parser.parse_args()
    handler = ParquetDataHandler(DATADIR)
    for timeframe in ("5m", "15m", "1h", "4h"):
        frame = to_freqtrade_ohlcv(
            load("futures_ohlcv", timeframe, data_version=args.data_version)
        )
        handler.ohlcv_store(PAIR, timeframe, frame, CandleType.FUTURES)
        print(f"stored futures {timeframe}: {len(frame)}")

    mark = resample_ohlcv(
        load("mark_price", "15m", data_version=args.data_version), "1h"
    )
    handler.ohlcv_store(PAIR, "1h", mark, CandleType.MARK)
    print(f"stored mark 1h: {len(mark)}")

    index = resample_ohlcv(
        load("index_price", "15m", data_version=args.data_version), "1h"
    )
    handler.ohlcv_store(PAIR, "1h", index, CandleType.INDEX)
    print(f"stored index 1h: {len(index)}")

    funding = funding_ohlcv(
        load("funding_rate", "native", data_version=args.data_version)
    )
    handler.ohlcv_store(PAIR, "1h", funding, CandleType.FUNDING_RATE)
    print(f"stored funding events in 1h container: {len(funding)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
