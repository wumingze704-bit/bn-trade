"""bookTicker 录制（只在东京服务器执行；06 §4.3、07 §3.3）。

规格：docs/06-回测引擎.md §4.3；docs/07-实盘与风控.md §3.3

按 `/public` 路由订阅交易范围全部币的 `bookTicker`，按 UTC 日落盘
`artifacts/live/book/{alias}/{YYYYMMDD}-{HHMMSS}.parquet`（字段 `E/bid/ask/bid_qty/ask_qty`，
`transact_time` 为事件时间毫秒）。每次落盘写一个独立分段文件，录制进行中也能被
`scripts/spread_profile.py` 读取。连接与解析复用 `trade.live.exchange`（不另写 WS 客户端）；
只读公开行情、不下单，访问币安只在服务器执行（AGENTS.md §6 红线 9）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from trade.live.exchange.endpoints import endpoints_for, stream_route  # type: ignore[import-untyped]
from trade.live.exchange.ws import WsConnection, parse_book_ticker  # type: ignore[import-untyped]

_FLUSH_ROWS = 50_000
# 空闲时定时落盘（秒），避免进程被信号终止或断线时丢失缓冲
_FLUSH_IDLE_S = 30.0


def _flush(
    out_root: Path,
    key: tuple[str, str],
    rows: list[tuple[int, float, float, float, float]],
) -> None:
    """把缓冲写成一个独立的分段 parquet（06 §4.3 字段 E/bid/ask/bid_qty/ask_qty）。"""
    alias, day = key
    directory = out_root / alias
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%H%M%S")
    path = directory / f"{day}-{stamp}.parquet"
    table = pa.Table.from_pydict(
        {
            "E": pa.array([row[0] for row in rows], type=pa.int64()),
            "bid": pa.array([row[1] for row in rows], type=pa.float64()),
            "ask": pa.array([row[2] for row in rows], type=pa.float64()),
            "bid_qty": pa.array([row[3] for row in rows], type=pa.float64()),
            "ask_qty": pa.array([row[4] for row in rows], type=pa.float64()),
        }
    )
    pq.write_table(table, path)
    print(f"flush {alias} {len(rows)} rows -> {path.name}", flush=True)


async def record_book_ticker(
    *,
    out_root: Path,
    symbols: list[str],
    days: float,
    aliases: dict[str, str] | None = None,
) -> int:
    """连续录制 bookTicker（06 §4.3）。

    Args:
        out_root: `artifacts/live/book/`。
        symbols: 币安 symbol 列表（交易范围 U(R) 内的全部币）。
        days: 录制天数。
        aliases: symbol → alias 映射（`data/universe/aliases.json`）；None 时用去掉
            `USDT` 的结果作为目录名，与回测的 alias 键不一致时需显式传入。

    Returns:
        写入的分段文件数。
    """
    endpoints = endpoints_for("mainnet")
    streams = [f"{symbol.lower()}@bookTicker" for symbol in symbols]
    url = endpoints.ws_stream_url(stream_route(streams[0]), streams)
    alias_of = aliases or {symbol: symbol.removesuffix("USDT") for symbol in symbols}
    buffers: dict[tuple[str, str], list[tuple[int, float, float, float, float]]] = {}
    loop = asyncio.get_event_loop()
    deadline = loop.time() + days * 86_400.0
    stop = False
    written = 0

    def _request_stop() -> None:
        nonlocal stop
        stop = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - Windows 无 add_signal_handler
            signal.signal(sig, lambda *_: _request_stop())

    def flush_all() -> None:
        """把所有缓冲写成独立分段 parquet。"""
        nonlocal written
        for key, rows in list(buffers.items()):
            if rows:
                _flush(out_root, key, rows)
                written += 1
                buffers[key] = []

    connection = WsConnection(url)
    await connection.connect()
    iterator = connection.__aiter__()
    last_flush = loop.time()
    try:
        while loop.time() < deadline and not stop:
            try:
                payload = await asyncio.wait_for(iterator.__anext__(), timeout=_FLUSH_IDLE_S)
            except TimeoutError:
                flush_all()
                last_flush = loop.time()
                continue
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
            if len(buffers[key]) >= _FLUSH_ROWS or loop.time() - last_flush >= _FLUSH_IDLE_S:
                flush_all()
                last_flush = loop.time()
    finally:
        flush_all()
        await connection.close()
    return written


def main(argv: list[str] | None = None) -> int:
    """入口：按交易范围录制 bookTicker。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="bookTicker 录制（06 §4.3）")
    parser.add_argument("--symbols-file", type=Path, required=True, help="币安 symbol 列表 JSON")
    parser.add_argument("--book-root", type=Path, default=Path("artifacts/live/book"))
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument(
        "--aliases-file",
        type=Path,
        default=None,
        help="data/universe/aliases.json；给出时目录名用回测 alias（C001…）",
    )
    args = parser.parse_args(argv)

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    symbols = json.loads(resolve(args.symbols_file).read_text(encoding="utf-8"))
    if not isinstance(symbols, list) or not symbols:
        raise ValueError(f"symbols 列表为空：{args.symbols_file}")
    aliases: dict[str, str] | None = None
    if args.aliases_file is not None:
        raw: dict[str, str] = json.loads(resolve(args.aliases_file).read_text(encoding="utf-8"))
        aliases = raw
    written = asyncio.run(
        record_book_ticker(
            out_root=resolve(args.book_root),
            symbols=list(symbols),
            days=args.days,
            aliases=aliases,
        )
    )
    print(f"录制完成：{written} 个分段文件 → {resolve(args.book_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
