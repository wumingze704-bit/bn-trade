"""bookTicker 录制（只在东京服务器执行；06 §4.3、07 §3.3）。

规格：docs/06-回测引擎.md §4.3；docs/07-实盘与风控.md §3.3

按 `/public` 路由订阅交易范围全部币的 `bookTicker`，按 UTC 日落盘
`artifacts/live/book/{alias}/{YYYYMMDD}.parquet`（字段 `E/bid/ask/bid_qty/ask_qty`）。
连接与解析复用 `trade.live.exchange`（不另写 WS 客户端）；只读公开行情、不下单，
访问币安只在服务器执行（AGENTS.md §6 红线 9）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trade.live.exchange.endpoints import endpoints_for, stream_route
from trade.live.exchange.ws import WsConnection, parse_book_ticker

_FLUSH_ROWS = 50_000


def _flush(
    out_root: Path,
    key: tuple[str, str],
    rows: list[tuple[int, float, float, float, float]],
    writers: dict[tuple[str, str], pq.ParquetWriter],
) -> None:
    """把缓冲写入对应的按日 parquet（06 §4.3 字段 E/bid/ask/bid_qty/ask_qty）。"""
    alias, day = key
    path = out_root / alias / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict(
        {
            "E": pa.array([row[0] for row in rows], type=pa.int64()),
            "bid": pa.array([row[1] for row in rows], type=pa.float64()),
            "ask": pa.array([row[2] for row in rows], type=pa.float64()),
            "bid_qty": pa.array([row[3] for row in rows], type=pa.float64()),
            "ask_qty": pa.array([row[4] for row in rows], type=pa.float64()),
        }
    )
    writer = writers.get(key)
    if writer is None:
        writer = pq.ParquetWriter(path, table.schema)
        writers[key] = writer
    writer.write_table(table)


async def record_book_ticker(*, out_root: Path, symbols: list[str], days: float) -> int:
    """连续录制 bookTicker（06 §4.3）。

    Args:
        out_root: `artifacts/live/book/`。
        symbols: 币安 symbol 列表（交易范围 U(R) 内的全部币）。
        days: 录制天数。

    Returns:
        写入的（币, 日）文件数。
    """
    endpoints = endpoints_for("mainnet")
    streams = [f"{symbol.lower()}@bookTicker" for symbol in symbols]
    url = endpoints.ws_stream_url(stream_route(streams[0]), streams)
    alias_of = {symbol: symbol.removesuffix("USDT") for symbol in symbols}
    writers: dict[tuple[str, str], pq.ParquetWriter] = {}
    buffers: dict[tuple[str, str], list[tuple[int, float, float, float, float]]] = {}
    loop = asyncio.get_event_loop()
    deadline = loop.time() + days * 86_400.0
    connection = WsConnection(url)
    await connection.connect()
    try:
        while loop.time() < deadline:
            async for payload in connection:
                data = payload.get("data", payload)
                event = parse_book_ticker(data)
                alias = alias_of.get(event.symbol, event.symbol.removesuffix("USDT"))
                day = datetime.fromtimestamp(event.event_time_ms // 1000, tz=UTC).strftime("%Y%m%d")
                key = (alias, day)
                buffers.setdefault(key, []).append(
                    (
                        event.event_time_ms,
                        event.best_bid,
                        event.best_ask,
                        float(data.get("B", 0.0)),
                        float(data.get("A", 0.0)),
                    )
                )
                if len(buffers[key]) >= _FLUSH_ROWS:
                    _flush(out_root, key, buffers[key], writers)
                    buffers[key] = []
    finally:
        for key, rows in buffers.items():
            if rows:
                _flush(out_root, key, rows, writers)
        for writer in writers.values():
            writer.close()
        await connection.close()
    return len(writers)


def main(argv: list[str] | None = None) -> int:
    """入口：按交易范围录制 bookTicker。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="bookTicker 录制（06 §4.3）")
    parser.add_argument("--symbols-file", type=Path, required=True, help="币安 symbol 列表 JSON")
    parser.add_argument("--book-root", type=Path, default=Path("artifacts/live/book"))
    parser.add_argument("--days", type=float, default=7.0)
    args = parser.parse_args(argv)

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    symbols = json.loads(resolve(args.symbols_file).read_text(encoding="utf-8"))
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"symbols 列表为空：{args.symbols_file}")
    written = asyncio.run(
        record_book_ticker(out_root=resolve(args.book_root), symbols=list(symbols), days=args.days)
    )
    print(f"录制完成：{written} 个（币, 日）文件 → {resolve(args.book_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
