#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
import yaml


BASE_URL = "https://data.binance.vision/data/futures/um"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


@dataclass(frozen=True)
class DownloadItem:
    dataset: str
    archive_kind: str
    period: str
    timeframe: str | None
    url: str
    raw_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def month_starts(start: datetime, end: datetime) -> Iterable[date]:
    current = date(start.year, start.month, 1)
    while current < end.date():
        yield current
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def archive_periods(start: datetime, end: datetime) -> list[tuple[str, str]]:
    periods: list[tuple[str, str]] = []
    for month in month_starts(start, end):
        if next_month(month) <= end.date():
            periods.append(("monthly", month.strftime("%Y-%m")))
        else:
            current = max(month, start.date())
            while current < end.date():
                periods.append(("daily", current.isoformat()))
                current += timedelta(days=1)
    return periods


def build_item(
    root: Path,
    symbol: str,
    dataset: str,
    archive_kind: str,
    period: str,
    timeframe: str | None,
) -> DownloadItem:
    if dataset in {"klines", "markPriceKlines", "indexPriceKlines"}:
        assert timeframe
        filename = f"{symbol}-{timeframe}-{period}.zip"
        relative = Path(archive_kind) / dataset / symbol / timeframe / filename
    else:
        filename = f"{symbol}-{dataset}-{period}.zip"
        relative = Path(archive_kind) / dataset / symbol / filename
    return DownloadItem(
        dataset=dataset,
        archive_kind=archive_kind,
        period=period,
        timeframe=timeframe,
        url=f"{BASE_URL}/{relative.as_posix()}",
        raw_path=root / "data" / "raw" / "binance_vision" / "futures" / "um" / relative,
    )


def build_plan(
    root: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    timeframes: tuple[str, ...] = tuple(TIMEFRAME_MINUTES),
) -> list[DownloadItem]:
    plan: list[DownloadItem] = []
    for archive_kind, period in archive_periods(start, end):
        for timeframe in timeframes:
            plan.append(build_item(root, symbol, "klines", archive_kind, period, timeframe))
        for dataset in ("markPriceKlines", "indexPriceKlines"):
            plan.append(build_item(root, symbol, dataset, archive_kind, period, "15m"))
        plan.append(build_item(root, symbol, "fundingRate", archive_kind, period, None))
    current = start.date()
    while current < end.date():
        plan.append(build_item(root, symbol, "metrics", "daily", current.isoformat(), None))
        current += timedelta(days=1)
    return plan


def request_with_retries(url: str, *, timeout: int, attempts: int) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                timeout=(15, timeout),
                headers={"User-Agent": "quant-research-lab/0.1 public-market-research"},
            )
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < attempts:
                time.sleep(min(attempt * 2, 10))
    assert last_error is not None
    raise last_error


def download_item(item: DownloadItem, *, timeout: int, attempts: int) -> dict[str, Any]:
    item.raw_path.parent.mkdir(parents=True, exist_ok=True)
    if item.raw_path.is_file():
        return {
            "dataset": item.dataset,
            "timeframe": item.timeframe,
            "archive_kind": item.archive_kind,
            "period": item.period,
            "url": item.url,
            "raw_path": str(item.raw_path),
            "status": "cached_immutable",
            "bytes": item.raw_path.stat().st_size,
            "sha256": sha256_file(item.raw_path),
            "official_checksum": None,
        }

    try:
        response = request_with_retries(item.url, timeout=timeout, attempts=attempts)
    except requests.RequestException as error:
        return {
            "dataset": item.dataset,
            "timeframe": item.timeframe,
            "archive_kind": item.archive_kind,
            "period": item.period,
            "url": item.url,
            "raw_path": str(item.raw_path),
            "status": "network_error",
            "error": str(error),
        }

    if response.status_code == 404:
        return {
            "dataset": item.dataset,
            "timeframe": item.timeframe,
            "archive_kind": item.archive_kind,
            "period": item.period,
            "url": item.url,
            "raw_path": str(item.raw_path),
            "status": "not_available",
            "http_status": 404,
        }
    if response.status_code != 200:
        return {
            "dataset": item.dataset,
            "timeframe": item.timeframe,
            "archive_kind": item.archive_kind,
            "period": item.period,
            "url": item.url,
            "raw_path": str(item.raw_path),
            "status": "http_error",
            "http_status": response.status_code,
        }

    content = response.content
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if archive.testzip() is not None:
                raise ValueError("zip CRC validation failed")
    except (zipfile.BadZipFile, ValueError) as error:
        return {
            "dataset": item.dataset,
            "timeframe": item.timeframe,
            "archive_kind": item.archive_kind,
            "period": item.period,
            "url": item.url,
            "raw_path": str(item.raw_path),
            "status": "invalid_archive",
            "error": str(error),
        }

    digest = hashlib.sha256(content).hexdigest()
    official_checksum = None
    try:
        checksum_response = request_with_retries(
            item.url + ".CHECKSUM", timeout=timeout, attempts=max(2, attempts // 2)
        )
        if checksum_response.status_code == 200:
            official_checksum = checksum_response.text.strip().split()[0]
            if official_checksum != digest:
                return {
                    "dataset": item.dataset,
                    "timeframe": item.timeframe,
                    "archive_kind": item.archive_kind,
                    "period": item.period,
                    "url": item.url,
                    "status": "checksum_mismatch",
                    "sha256": digest,
                    "official_checksum": official_checksum,
                }
    except requests.RequestException:
        pass

    temporary = item.raw_path.with_suffix(item.raw_path.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(item.raw_path)
    return {
        "dataset": item.dataset,
        "timeframe": item.timeframe,
        "archive_kind": item.archive_kind,
        "period": item.period,
        "url": item.url,
        "raw_path": str(item.raw_path),
        "status": "downloaded",
        "bytes": len(content),
        "sha256": digest,
        "official_checksum": official_checksum,
    }


def read_archive(path: Path, dataset: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}, found {names}")
        with archive.open(names[0]) as handle:
            frame = pd.read_csv(handle)
    if dataset in {"klines", "markPriceKlines", "indexPriceKlines"}:
        if "open_time" not in frame.columns:
            frame = pd.read_csv(BytesIO(zipfile.ZipFile(path).read(names[0])), header=None)
            frame.columns = KLINE_COLUMNS[: len(frame.columns)]
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    return frame


def timestamp_column(frame: pd.DataFrame, dataset: str) -> str:
    candidates = {
        "klines": ("open_time",),
        "markPriceKlines": ("open_time",),
        "indexPriceKlines": ("open_time",),
        "fundingRate": ("funding_time", "fundingtime", "calc_time", "time"),
        "metrics": ("create_time", "timestamp", "time"),
    }[dataset]
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"no timestamp column for {dataset}: {list(frame.columns)}")


def normalize_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.95:
        unit = "ms" if numeric.dropna().abs().median() > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def output_dataset_name(dataset: str) -> str:
    return {
        "klines": "futures_ohlcv",
        "markPriceKlines": "mark_price",
        "indexPriceKlines": "index_price",
        "fundingRate": "funding_rate",
        "metrics": "open_interest_metrics",
    }[dataset]


def write_partitioned_parquet(
    frame: pd.DataFrame,
    *,
    root: Path,
    symbol: str,
    dataset: str,
    timeframe: str | None,
    data_version: str,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    grouped = frame.groupby([frame["timestamp"].dt.year, frame["timestamp"].dt.month])
    for (year, month), partition in grouped:
        directory = (
            root
            / "data"
            / "processed"
            / "exchange=binance"
            / "market=usdt_perpetual"
            / f"symbol={symbol}"
            / f"dataset={output_dataset_name(dataset)}"
            / f"timeframe={timeframe or 'native'}"
            / f"version={data_version}"
            / f"year={year:04d}"
            / f"month={month:02d}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        first = partition["timestamp"].min().strftime("%Y%m%dT%H%M%SZ")
        last = partition["timestamp"].max().strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"part-{first}-{last}.parquet"
        temporary = path.with_suffix(".parquet.part")
        partition.to_parquet(temporary, index=False, compression="zstd")
        if path.exists():
            if sha256_file(temporary) != sha256_file(path):
                raise FileExistsError(
                    f"processed partition already exists with different content: {path}"
                )
            temporary.unlink()
        else:
            temporary.replace(path)
        outputs.append(
            {
                "path": str(path),
                "rows": int(len(partition)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return outputs


def quality_summary(
    frame: pd.DataFrame,
    *,
    dataset: str,
    timeframe: str | None,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    timestamps = frame["timestamp"]
    summary: dict[str, Any] = {
        "rows": int(len(frame)),
        "first_timestamp": timestamps.min().isoformat() if len(frame) else None,
        "last_timestamp": timestamps.max().isoformat() if len(frame) else None,
        "duplicate_timestamps": int(timestamps.duplicated().sum()),
        "null_timestamps": int(timestamps.isna().sum()),
        "monotonic_increasing": bool(timestamps.is_monotonic_increasing),
    }
    interval_minutes = TIMEFRAME_MINUTES.get(timeframe or "")
    if dataset == "fundingRate":
        interval_minutes = 8 * 60
    elif dataset == "metrics":
        interval_minutes = 5
    if interval_minutes:
        minutes = interval_minutes
        expected = int((end - start).total_seconds() // (minutes * 60))
        expected_index = pd.date_range(start, end, freq=f"{minutes}min", inclusive="left")
        observed = pd.DatetimeIndex(timestamps.dropna().unique())
        missing = expected_index.difference(observed)
        summary.update(
            {
                "theoretical_rows": expected,
                "missing_intervals": int(len(missing)),
                "coverage_ratio": round(len(observed) / expected, 8) if expected else None,
                "unexpected_interval_jumps": int(
                    (timestamps.diff().dropna() != pd.Timedelta(minutes=minutes)).sum()
                ),
                "sample_missing_timestamps": [value.isoformat() for value in missing[:20]],
            }
        )
        if dataset == "metrics":
            summary["off_grid_timestamps"] = int(
                (timestamps.dropna().dt.floor(f"{minutes}min") != timestamps.dropna()).sum()
            )
    if dataset == "klines" and len(frame):
        numeric = frame[["open", "high", "low", "close"]].apply(
            pd.to_numeric, errors="coerce"
        )
        invalid = (
            (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
            | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
            | (numeric <= 0).any(axis=1)
        )
        summary["invalid_ohlc_rows"] = int(invalid.sum())
        volume = pd.to_numeric(frame.get("volume"), errors="coerce")
        summary["negative_volume_rows"] = int((volume < 0).sum())
    return summary


def process_group(
    records: list[dict[str, Any]],
    *,
    root: Path,
    symbol: str,
    dataset: str,
    timeframe: str | None,
    start: datetime,
    end: datetime,
    data_version: str,
) -> dict[str, Any]:
    usable = [
        record
        for record in records
        if record["dataset"] == dataset
        and record.get("timeframe") == timeframe
        and record["status"] in {"downloaded", "cached_immutable"}
    ]
    if not usable:
        return {
            "dataset": output_dataset_name(dataset),
            "timeframe": timeframe or "native",
            "status": "unavailable",
            "quality": None,
            "outputs": [],
        }

    frames = [read_archive(Path(record["raw_path"]), dataset) for record in usable]
    frame = pd.concat(frames, ignore_index=True)
    source_timestamp = timestamp_column(frame, dataset)
    frame.insert(0, "timestamp", normalize_timestamp(frame[source_timestamp]))
    if dataset == "fundingRate":
        frame["timestamp"] = frame["timestamp"].dt.round("s")
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    frame = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] < end_ts)]
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    frame = frame.reset_index(drop=True)
    quality = quality_summary(
        frame,
        dataset=dataset,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    outputs = write_partitioned_parquet(
        frame,
        root=root,
        symbol=symbol,
        dataset=dataset,
        timeframe=timeframe,
        data_version=data_version,
    )
    return {
        "dataset": output_dataset_name(dataset),
        "timeframe": timeframe or "native",
        "status": "processed",
        "quality": quality,
        "outputs": outputs,
    }


def write_manifest(
    *,
    root: Path,
    profile: dict[str, Any],
    start: datetime,
    end: datetime,
    records: list[dict[str, Any]],
    processed: list[dict[str, Any]],
    dataset_id: str,
    data_version: str,
    manifest_name: str,
    timeframes: tuple[str, ...],
    proxy_used: bool,
) -> Path:
    okx_gap = (
        "Official OKX public metadata is reachable through the user-provided local "
        "proxy; this scoped annual extension did not download OKX history."
        if proxy_used
        else "OKX public API was not reachable without a proxy; no OKX history was downloaded."
    )
    portable_records = []
    for record in records:
        portable = dict(record)
        portable["raw_path"] = str(Path(record["raw_path"]).relative_to(root))
        portable_records.append(portable)
    portable_processed = []
    for dataset in processed:
        portable = dict(dataset)
        portable["outputs"] = [
            {
                **output,
                "path": str(Path(output["path"]).relative_to(root)),
            }
            for output in dataset["outputs"]
        ]
        portable_processed.append(portable)
    manifest = {
        "manifest_version": 1,
        "dataset_id": dataset_id,
        "data_version": data_version,
        "created_at": utc_now(),
        "market_profile": profile["profile_id"],
        "source": {
            "requested_primary": "okx",
            "effective_source": "binance_official_archive",
            "base_url": BASE_URL,
            "credentials_used": False,
            "proxy_used": proxy_used,
            "okx_gap": okx_gap,
        },
        "range": {
            "start_utc_inclusive": start.isoformat(),
            "end_utc_exclusive": end.isoformat(),
            "complete_utc_days": (end - start).days,
        },
        "symbol": {"unified": "ETH/USDT:USDT", "native": "ETHUSDT"},
        "timeframes": list(timeframes),
        "cost_model": profile["cost_model"],
        "processing": {
            "script": "scripts/download_binance_vision.py",
            "script_sha256": sha256_file(Path(__file__)),
            "raw_immutable": True,
            "atomic_writes": True,
            "parquet_compression": "zstd",
            "steps": [
                "Validate ZIP CRC and official checksum when available.",
                "Parse source timestamps with explicit UTC semantics.",
                "Filter to the exact inclusive-start/exclusive-end window.",
                "Sort by timestamp and remove duplicate timestamps, keeping the last source row.",
                "Run interval, monotonicity, duplicate, OHLC and volume checks.",
                "Write month-partitioned Parquet with atomic rename and SHA-256.",
            ],
        },
        "raw_archives": portable_records,
        "processed_datasets": portable_processed,
        "data_gaps": [
            okx_gap,
            "Leverage tiers and liquidation engine are not included in Binance Vision archives.",
            "Funding, mark, index and open-interest availability is reported per processed dataset and never assumed to be zero.",
        ],
        "research_limitations": [
            "Historical coverage is research evidence only and cannot establish future profitability.",
            "A one-year window improves regime coverage but still does not prove a complete or repeatable market cycle.",
            "No future lower-timeframe aggregate may be used at the current decision timestamp.",
        ],
    }
    path = root / "data" / "manifests" / manifest_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"manifest is immutable and already exists: {path}")
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def write_report(
    root: Path,
    processed: list[dict[str, Any]],
    manifest_path: Path,
    *,
    start: datetime,
    end: datetime,
    report_name: str,
    proxy_used: bool,
) -> Path:
    lines = [
        "# ETHUSDT 永续数据质量摘要",
        "",
        f"- Manifest: `{manifest_path.relative_to(root)}`",
        f"- 精确窗口：{start.isoformat()}（含）至 {end.isoformat()}（不含）",
        f"- 代理使用：{proxy_used}（仅公开官方数据）。",
        "- 实际行情源：Binance 官方历史归档，无密钥。",
        "- 用途：工程验证、原样基准、候选初筛和市场状态覆盖；不证明长期盈利。",
        "",
        "| 数据集 | 周期 | 状态 | 行数 | 理论行数 | 缺口 | 重复 | OHLC异常 |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in processed:
        quality = item.get("quality") or {}
        lines.append(
            "| {dataset} | {timeframe} | {status} | {rows} | {expected} | {missing} | {duplicates} | {invalid} |".format(
                dataset=item["dataset"],
                timeframe=item["timeframe"],
                status=item["status"],
                rows=quality.get("rows", "-"),
                expected=quality.get("theoretical_rows", "-"),
                missing=quality.get("missing_intervals", "-"),
                duplicates=quality.get("duplicate_timestamps", "-"),
                invalid=quality.get("invalid_ohlc_rows", "-"),
            )
        )
    lines.extend(
        [
            "",
            "## 明确缺口",
            "",
            (
                "- OKX 公共 metadata 可通过用户指定的本机代理访问；"
                "本轮范围仅扩展 Binance 一年历史，未下载 OKX 一年数据。"
                if proxy_used
                else "- OKX 直连不可达，未取得 OKX 对照数据。"
            ),
            "- 杠杆阶梯、强平引擎、最小金额和精度规则尚未进入历史回测模型。",
            "- 任何不可用的 funding/mark/index/OI 数据均在 manifest 中标为 unavailable，不按零处理。",
        ]
    )
    path = root / "reports" / "data_quality" / report_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"quality report already exists: {path}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--dataset-id")
    parser.add_argument("--data-version")
    parser.add_argument("--manifest-name")
    parser.add_argument("--report-name")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        choices=sorted(TIMEFRAME_MINUTES),
        default=list(TIMEFRAME_MINUTES),
    )
    parser.add_argument("--proxy-used", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    profile_path = root / args.profile
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if profile["profile_id"] != "crypto_perpetual.binance.eth":
        raise SystemExit("This downloader only supports the explicit Binance ETH profile.")
    if not profile.get("enabled"):
        raise SystemExit("Selected market profile is not enabled.")

    start = parse_utc(args.start_utc or profile["history"]["start_utc_inclusive"])
    end = parse_utc(args.end_utc or profile["history"]["end_utc_exclusive"])
    if end <= start:
        raise SystemExit("end must be after start")
    range_slug = start.strftime("%Y%m%d") + "_" + end.strftime("%Y%m%d")
    dataset_id = args.dataset_id or f"binance_ethusdt_perpetual_{range_slug}"
    data_version = args.data_version or f"binance-vision-ethusdt-perpetual-{range_slug}-v1"
    manifest_name = args.manifest_name or f"{dataset_id}.json"
    report_name = args.report_name or f"{dataset_id}.md"
    timeframes = tuple(args.timeframes)
    symbol = profile["symbols"][0]["native"]
    plan = build_plan(root, symbol, start, end, timeframes=timeframes)
    if args.plan_only:
        cached = [item for item in plan if item.raw_path.is_file()]
        print(
            json.dumps(
                {
                    "market_profile": profile["profile_id"],
                    "symbol": symbol,
                    "range": {
                        "start_utc_inclusive": start.isoformat(),
                        "end_utc_exclusive": end.isoformat(),
                        "complete_utc_days": (end - start).days,
                    },
                    "timeframes": list(timeframes),
                    "planned_archive_requests": len(plan),
                    "cached_immutable_archives": len(cached),
                    "missing_archive_requests": len(plan) - len(cached),
                    "cached_bytes": sum(item.raw_path.stat().st_size for item in cached),
                    "disk_free_bytes": shutil.disk_usage(root).free,
                    "dataset_id": dataset_id,
                    "data_version": data_version,
                    "manifest_artifact_key": f"data/manifests/{manifest_name}",
                    "automatic_deletion": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download_item,
                item,
                timeout=args.timeout,
                attempts=args.attempts,
            ): item
            for item in plan
        }
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(
                f"[{index}/{len(plan)}] {record['dataset']} {record.get('timeframe') or 'native'} "
                f"{record['period']} {record['status']}",
                flush=True,
            )
    records.sort(
        key=lambda value: (
            value["dataset"],
            value.get("timeframe") or "",
            value["period"],
        )
    )

    processed: list[dict[str, Any]] = []
    for timeframe in timeframes:
        processed.append(
            process_group(
                records,
                root=root,
                symbol=symbol,
                dataset="klines",
                timeframe=timeframe,
                start=start,
                end=end,
                data_version=data_version,
            )
        )
    for dataset in ("markPriceKlines", "indexPriceKlines"):
        processed.append(
            process_group(
                records,
                root=root,
                symbol=symbol,
                dataset=dataset,
                timeframe="15m",
                start=start,
                end=end,
                data_version=data_version,
            )
        )
    for dataset in ("fundingRate", "metrics"):
        processed.append(
            process_group(
                records,
                root=root,
                symbol=symbol,
                dataset=dataset,
                timeframe=None,
                start=start,
                end=end,
                data_version=data_version,
            )
        )

    manifest_path = write_manifest(
        root=root,
        profile=profile,
        start=start,
        end=end,
        records=records,
        processed=processed,
        dataset_id=dataset_id,
        data_version=data_version,
        manifest_name=manifest_name,
        timeframes=timeframes,
        proxy_used=args.proxy_used,
    )
    report_path = write_report(
        root,
        processed,
        manifest_path,
        start=start,
        end=end,
        report_name=report_name,
        proxy_used=args.proxy_used,
    )
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
