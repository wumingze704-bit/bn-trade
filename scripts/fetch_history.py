"""历史研究数据下载器：1h/1d K 线与资金费月度 zip → 月度 parquet。

规格：docs/13-研究框架.md §1（研究数据目录与字段）。

- 只在东京服务器运行（AGENTS.md §6 红线 9）；本机只跑 fixture 测试。
- 币种与各月文件清单来自 S3 ListObjectsV2，不逐月试探 404。
- 每个 zip 读入内存、校验 CHECKSUM、内存解压解析，磁盘上不保存 zip。
- 先写临时文件再改名；已存在且行数一致的文件跳过（断点续传）。
- 月度 1h/1d 只保留研究需要的列，价格与成交量转 float32，时间戳 int64 毫秒。
- 资金费统一换算为每小时费率；旧文件缺结算间隔时按相邻结算时刻差推断。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow.parquet as pq

from trade.data.aggregate import KLINE_CSV_COLUMNS, normalize_timestamp_ms

VISION_BASE = "https://data.binance.vision"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_DEFAULT_RETRIES = 3
_DEFAULT_TIMEOUT = 60.0
_S3_PAGE = 1000
_MS_PER_HOUR = 3_600_000
_SYMBOL_PREFIX = "data/futures/um/monthly/klines/"
_KEY_RE = re.compile(
    r"^data/futures/um/monthly/(?P<kind>klines|fundingRate)/(?P<symbol>[A-Z0-9]+)/"
    r"(?P<sub>1h|1d)?/?"
    r"(?P<name>(?P=symbol)-(?P<kind2>1h|1d|fundingRate)-(?P<month>\d{4}-\d{2})\.zip)$"
)
KLINE_KEEP = ("open_time", "open", "high", "low", "close", "volume", "quote_volume", "count")
_DTYPE = {"1h": "klines_1h", "1d": "klines_1d", "funding": "funding"}


class RateLimitError(Exception):
    """币安返回 HTTP 429/418，需要降低并发并等待。"""


class AdaptiveLimiter:
    """可动态收紧的异步并发限制（遇 429/418 时收紧）。"""

    def __init__(self, limit: int) -> None:
        self._limit = max(2, limit)
        self._active = 0
        self._condition = asyncio.Condition()

    async def __aenter__(self) -> AdaptiveLimiter:
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def reduce(self) -> int:
        """并发上限减半（不低于 2），返回新上限。"""
        self._limit = max(2, self._limit // 2)
        return self._limit


def month_periods(start_month: str, end_month: str) -> list[str]:
    """返回 [start_month, end_month] 闭区间的 YYYY-MM 列表。"""
    start = datetime.strptime(start_month, "%Y-%m")
    end = datetime.strptime(end_month, "%Y-%m")
    if (start.year, start.month) > (end.year, end.month):
        raise ValueError(f"起始月 {start_month} 晚于结束月 {end_month}")
    periods: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return periods


def research_end_month(ledger_path: Path) -> str:
    """读取 ledger.research_end 的月份；下载不得越过该月（红线 2）。"""
    text = ledger_path.read_text(encoding="utf-8")
    match = re.search(r'^research_end:\s*"(\d{4}-\d{2})-\d{2}"', text, flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"ledger 缺少 research_end：{ledger_path}")
    return match.group(1)


def _looks_like_data(first_line: str) -> bool:
    """首行第一列能否解析为数字（是 → 无表头）。"""
    token = first_line.split(",", 1)[0].strip()
    try:
        float(token)
    except ValueError:
        return False
    return True


def parse_klines_payload(payload: bytes, hint: str) -> pd.DataFrame:
    """解析 1h/1d 月度 zip（兼容有表头 / 无表头、毫秒 / 微秒）。

    Args:
        payload: zip 文件字节。
        hint: 出错时写入异常上下文的数据标识。

    Returns:
        `KLINE_KEEP` 列 DataFrame，`open_time` 为 int64 毫秒，数值列 float32。

    Raises:
        ValueError: zip 内没有 CSV，或列数 / 必需列不符。
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"zip 内没有 CSV：{hint}")
        raw = archive.read(names[0])
    first_line = raw.split(b"\n", 1)[0].decode("utf-8", errors="strict").strip()
    header = 0 if not _looks_like_data(first_line) else None
    frame = pd.read_csv(io.BytesIO(raw), header=header)
    if header is None:
        if len(frame.columns) != len(KLINE_CSV_COLUMNS):
            raise ValueError(
                f"CSV 列数不符：{hint}，期望 {len(KLINE_CSV_COLUMNS)}，实际 {len(frame.columns)}"
            )
        frame.columns = list(KLINE_CSV_COLUMNS)
    frame = frame.rename(columns=lambda column: str(column).strip())
    frame["open_time"] = normalize_timestamp_ms(frame["open_time"]).astype("int64")
    if "ignore" in frame.columns:
        frame = frame.drop(columns="ignore")
    missing = [column for column in KLINE_KEEP if column not in frame.columns]
    if missing:
        raise ValueError(f"K 线缺少列：{hint}，缺失 {missing}")
    frame = frame[list(KLINE_KEEP)].copy()
    for column in KLINE_KEEP:
        if column != "open_time":
            frame[column] = frame[column].astype("float32")
    frame["open_time"] = frame["open_time"].astype("int64")
    return frame.sort_values("open_time").drop_duplicates("open_time", keep="last")


def parse_funding_payload(payload: bytes, hint: str) -> pd.DataFrame:
    """解析资金费月度 zip，返回每小时费率（docs/13 §1）。

    兼容旧文件缺 `funding_interval_hours`：按相邻结算时刻差推断间隔，首行沿用
    下一行的推断值；单点文件按 8 小时（币安默认结算间隔）。

    Args:
        payload: zip 文件字节。
        hint: 出错时写入异常上下文的数据标识。

    Returns:
        含 `calc_time`（int64 毫秒）、`interval_hours`、`funding_rate`、`hourly_rate`。

    Raises:
        ValueError: zip 内没有 CSV，或缺少 `calc_time` / 费率列。
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"zip 内没有 CSV：{hint}")
        raw = archive.read(names[0])
    first_line = raw.split(b"\n", 1)[0].decode("utf-8", errors="strict").strip()
    header = 0 if not _looks_like_data(first_line) else None
    frame = pd.read_csv(io.BytesIO(raw), header=header)
    if header is None:
        if len(frame.columns) == 3:
            frame.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"]
        elif len(frame.columns) == 2:
            frame.columns = ["calc_time", "last_funding_rate"]
        else:
            raise ValueError(f"资金费列数不符：{hint}，实际 {len(frame.columns)}")
    frame = frame.rename(columns=lambda column: str(column).strip().lower())
    if "calc_time" not in frame.columns or "last_funding_rate" not in frame.columns:
        raise ValueError(f"资金费缺少 calc_time/last_funding_rate：{hint}")
    calc_time = normalize_timestamp_ms(frame["calc_time"]).astype("int64").to_numpy()
    rate = pd.to_numeric(frame["last_funding_rate"], errors="raise").to_numpy(dtype="float64")
    order = np.argsort(calc_time, kind="stable")
    calc_time, rate = calc_time[order], rate[order]
    unique = np.concatenate([[True], calc_time[1:] != calc_time[:-1]])
    calc_time, rate = calc_time[unique], rate[unique]
    if "funding_interval_hours" in frame.columns:
        raw_interval = pd.to_numeric(frame["funding_interval_hours"], errors="coerce").to_numpy(
            dtype="float64"
        )
        interval = raw_interval[order][unique]
    else:
        interval = np.full(calc_time.shape, np.nan)
    interval = _fill_interval(calc_time, interval)
    return pd.DataFrame(
        {
            "calc_time": calc_time.astype("int64"),
            "interval_hours": interval.astype("float32"),
            "funding_rate": rate.astype("float32"),
            "hourly_rate": (rate / interval).astype("float32"),
        }
    )


def _fill_interval(calc_time: np.ndarray, interval: np.ndarray) -> np.ndarray:
    """用相邻结算时刻差补齐缺失的结算间隔（小时）。"""
    diffs = np.full(calc_time.shape, np.nan)
    if calc_time.size >= 2:
        diffs[1:] = np.diff(calc_time) / _MS_PER_HOUR
    filled = interval.astype("float64").copy()
    missing = ~np.isfinite(filled)
    filled[missing] = diffs[missing]
    if filled.size and not np.isfinite(filled[0]):
        filled[0] = filled[1] if filled.size > 1 else 8.0
    if not np.all(np.isfinite(filled)):
        raise ValueError(f"无法推断资金费结算间隔：calc_time={calc_time[:3].tolist()}")
    return np.clip(filled, 1.0, 24.0)


def build_file_index(keys: Iterable[str]) -> dict[str, dict[str, list[str]]]:
    """把 S3 key 列表整理成 `{类型: {symbol: [月份...]}}`。"""
    index: dict[str, dict[str, list[str]]] = {"1h": {}, "1d": {}, "funding": {}}
    for key in keys:
        match = _KEY_RE.match(key)
        if match is None:
            continue
        kind = match.group("kind2")
        data_type = "funding" if kind == "fundingRate" else str(kind)
        index[data_type].setdefault(match.group("symbol"), []).append(match.group("month"))
    for mapping in index.values():
        for symbol in mapping:
            mapping[symbol] = sorted(set(mapping[symbol]))
    return index


def download_url(data_type: str, symbol: str, month: str) -> str:
    """月度数据 zip 的 data.binance.vision 地址。"""
    if data_type in {"1h", "1d"}:
        return (
            f"{VISION_BASE}/data/futures/um/monthly/klines/{symbol}/{data_type}/"
            f"{symbol}-{data_type}-{month}.zip"
        )
    return (
        f"{VISION_BASE}/data/futures/um/monthly/fundingRate/{symbol}/"
        f"{symbol}-fundingRate-{month}.zip"
    )


def output_path(root: Path, data_type: str, symbol: str, month: str) -> Path:
    """月度 parquet 输出路径（docs/13 §1）。"""
    return root / _DTYPE[data_type] / symbol / f"{month}.parquet"


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    """先写临时文件再改名为目标 parquet。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def parquet_rows(path: Path) -> int:
    """读取 parquet 行数；不存在或不可读时返回 -1。"""
    if not path.is_file():
        return -1
    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:
        return -1


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_checksum(text: str) -> str:
    for token in text.split():
        if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
            return token.lower()
    raise ValueError("CHECKSUM 文件缺少 SHA256 摘要")


async def _get(client: httpx.AsyncClient, url: str, *, retries: int) -> bytes | None:
    """GET 一个文件；404 返回 None，429/418 抛出 RateLimitError，其它错误重试。"""
    backoff = 1.0
    for attempt in range(retries + 1):
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            if attempt >= retries:
                raise
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        if response.status_code == 404:
            return None
        if response.status_code in (429, 418):
            raise RateLimitError(f"{response.status_code} {url}")
        if response.status_code >= 400:
            if attempt >= retries:
                response.raise_for_status()
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        return response.content
    raise RuntimeError(f"下载失败：{url}")


async def _list_pages(client: httpx.AsyncClient, params: dict[str, Any]) -> list[ET.Element]:
    """S3 ListObjectsV2 分页，返回每页 XML 根节点。"""
    roots: list[ET.Element] = []
    token: str | None = None
    while True:
        page_params = dict(params)
        if token:
            page_params["continuation-token"] = token
        response = await client.get(S3_LIST, params=page_params)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        roots.append(root)
        token = root.findtext(f"{_S3_NS}NextContinuationToken")
        if not token:
            return roots


async def _list_keys(client: httpx.AsyncClient, prefix: str) -> list[str]:
    """S3 ListObjectsV2 分页读取全部 key。"""
    keys: list[str] = []
    for root in await _list_pages(
        client, {"prefix": prefix, "list-type": "2", "max-keys": _S3_PAGE}
    ):
        for content in root.iter(f"{_S3_NS}Contents"):
            key = content.findtext(f"{_S3_NS}Key")
            if key:
                keys.append(key)
    return keys


async def _list_symbols(client: httpx.AsyncClient) -> list[str]:
    """S3 目录分隔列表读取全部 USDT 结尾的合约目录（含已下架）。"""
    symbols: list[str] = []
    for root in await _list_pages(
        client,
        {"prefix": _SYMBOL_PREFIX, "delimiter": "/", "list-type": "2", "max-keys": _S3_PAGE},
    ):
        for common in root.iter(f"{_S3_NS}CommonPrefixes"):
            value = common.findtext(f"{_S3_NS}Prefix") or ""
            symbol = value.removeprefix(_SYMBOL_PREFIX).rstrip("/")
            if symbol.endswith("USDT"):
                symbols.append(symbol)
    stable = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT"}
    return sorted(set(symbols) - stable)


async def _fetch_task(
    client: httpx.AsyncClient,
    limiter: AdaptiveLimiter,
    root: Path,
    data_type: str,
    symbol: str,
    month: str,
) -> dict[str, Any]:
    """下载并转换单个 (类型, 币, 月)；返回 manifest 条目。"""
    path = output_path(root, data_type, symbol, month)
    record: dict[str, Any] = {"path": str(path.relative_to(root)), "status": "ok"}
    url = download_url(data_type, symbol, month)
    delay = 0.0
    for attempt in range(_DEFAULT_RETRIES + 1):
        try:
            async with limiter:
                payload = await _get(client, url, retries=_DEFAULT_RETRIES)
                if payload is None:
                    record["status"] = "missing"
                    return record
                checksum = await _get(client, url + ".CHECKSUM", retries=_DEFAULT_RETRIES)
            if checksum is None:
                raise ValueError(f"缺少 CHECKSUM：{url}")
            expected = _parse_checksum(checksum.decode("utf-8", errors="strict"))
            if _sha256(payload) != expected:
                raise ValueError(f"SHA256 不匹配：{url}")
            frame = (
                parse_funding_payload(payload, url)
                if data_type == "funding"
                else parse_klines_payload(payload, url)
            )
            write_parquet_atomic(frame, path)
            time_column = "calc_time" if data_type == "funding" else "open_time"
            record.update(
                {
                    "rows": int(len(frame)),
                    "zip_sha256": expected,
                    "first_ms": int(frame[time_column].iloc[0]),
                    "last_ms": int(frame[time_column].iloc[-1]),
                }
            )
            return record
        except RateLimitError as error:
            limit = limiter.reduce()
            delay = max(delay * 2.0, 60.0)
            print(f"限速（{error}），等待 {delay:.0f}s，并发降为 {limit}", file=sys.stderr)
            await asyncio.sleep(delay)
        except Exception as error:  # noqa: BLE001 - 逐文件记录失败并继续
            if attempt >= _DEFAULT_RETRIES:
                record.update({"status": "failed", "error": str(error)})
                return record
            await asyncio.sleep(float(2**attempt))
    record.update({"status": "failed", "error": "重试耗尽"})
    return record


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _existing_record(
    out_root: Path, data_type: str, symbol: str, month: str
) -> dict[str, Any] | None:
    """从 manifest 读取已存在文件的行数（用于断点续传判断）。"""
    manifest_path = out_root / "history" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    record = manifest.get("files", {}).get(f"{data_type}/{symbol}/{month}")
    return record if isinstance(record, dict) else None


async def run(args: argparse.Namespace) -> int:
    """执行下载主流程。"""
    root = Path(__file__).resolve().parents[1]
    ledger = _resolve(root, args.ledger)
    end_month = research_end_month(ledger)
    if args.end > end_month:
        raise SystemExit(f"--end {args.end} 超过 research_end（{end_month}），拒绝下载")
    month_set = set(month_periods(args.start, args.end))
    out_root = _resolve(root, args.out)
    history_dir = out_root / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    limits = httpx.Limits(
        max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, limits=limits, follow_redirects=True
    ) as client:
        print("读取 S3 币种与文件清单…")
        symbols = await _list_symbols(client)
        types = [item.strip() for item in args.types.split(",") if item.strip()]
        keys: list[str] = []
        for data_type in types:
            prefix = (
                "data/futures/um/monthly/fundingRate/"
                if data_type == "funding"
                else "data/futures/um/monthly/klines/"
            )
            keys.extend(await _list_keys(client, prefix))
        index = build_file_index(keys)
        (history_dir / "symbols.json").write_text(
            json.dumps({"symbols": symbols, "index": index}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        allowed = set(symbols[: args.symbol_limit]) if args.symbol_limit else set(symbols)
        if args.symbols:
            allowed &= {item.strip() for item in args.symbols.split(",") if item.strip()}
        tasks: list[tuple[str, str, str]] = []
        for data_type in types:
            for symbol, available in index.get(data_type, {}).items():
                if symbol not in allowed:
                    continue
                for month in available:
                    if month not in month_set:
                        continue
                    record = _existing_record(out_root, data_type, symbol, month)
                    if record is not None and parquet_rows(
                        output_path(out_root, data_type, symbol, month)
                    ) == record.get("rows"):
                        continue
                    tasks.append((data_type, symbol, month))
        print(f"待下载 {len(tasks)} 个文件（{args.start}~{args.end}，类型 {types}）")

        manifest_path = history_dir / "manifest.json"
        manifest: dict[str, Any] = {"research_end": end_month, "files": {}}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {"research_end": end_month, "files": {}}
            manifest.setdefault("files", {})
        limiter = AdaptiveLimiter(args.concurrency)
        started = time.monotonic()
        done = 0
        failed = 0
        for chunk_start in range(0, len(tasks), 200):
            chunk = tasks[chunk_start : chunk_start + 200]
            results = await asyncio.gather(
                *[
                    _fetch_task(client, limiter, out_root, data_type, symbol, month)
                    for data_type, symbol, month in chunk
                ]
            )
            for (data_type, symbol, month), record in zip(chunk, results, strict=True):
                manifest["files"][f"{data_type}/{symbol}/{month}"] = record
                done += 1
                if record["status"] == "failed":
                    failed += 1
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed > 0 else 0.0
            print(f"进度 {done}/{len(tasks)}，失败 {failed}，{rate:.1f} 文件/秒")
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            manifest["done"] = done
            manifest["total"] = len(tasks)
            manifest_path.write_text(
                json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest_path.write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        statuses = [entry.get("status") for entry in manifest["files"].values()]
        missing = statuses.count("missing")
        ok = statuses.count("ok")
        total = max(1, len(statuses))
        print(f"完成：ok={ok} missing={missing} failed={failed} 完成率={ok / total:.1%}")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """fetch_history.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="下载 1h/1d K 线与资金费（docs/13 §1）")
    parser.add_argument("--ledger", default="docs/research/ledger.yaml")
    parser.add_argument("--start", default="2020-10", help="起始月 YYYY-MM")
    parser.add_argument("--end", default=None, help="结束月 YYYY-MM，默认取 research_end 月份")
    parser.add_argument("--types", default="1h,1d,funding")
    parser.add_argument("--out", default="data")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--symbol-limit", type=int, default=0, help="只下载前 N 个币")
    parser.add_argument("--symbols", default=None, help="只下载指定币（逗号分隔）")
    args = parser.parse_args(argv)
    if args.end is None:
        args.end = research_end_month(_resolve(root, args.ledger))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
