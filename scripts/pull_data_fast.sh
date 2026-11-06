#!/usr/bin/env bash
# 并行增量同步服务器 bars 到本地（排除封存集 M23~M24），在 WSL 下运行。
# 用法（WSL，项目根目录）：bash scripts/pull_data_fast.sh [并发数]
# 规格：docs/02-系统架构与部署.md §5.8；封存集排除见 AGENTS.md §6 红线 2、03 §6。
# 相比 sync.sh pull-data：按币并行（xargs -P），rsync 自动增量（本地已有修复前版本，
# 只传首日大单阈值差异），跨境链路单连接约 30 KB/s、8 并发实测约 143 KB/s。
set -euo pipefail

REMOTE="${TOKYO_REMOTE:-tokyo}"
REMOTE_DIR="trade"
CONC="${1:-8}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BARS_DIR="$ROOT_DIR/data/bars"

# 封存集月份（03 §6、红线 2）：其 bars 只在服务器保留，不拉到本地。
EXCLUDES="--exclude=202608.parquet --exclude=202609.parquet"

sync_coin() {
    local coin="$1"
    mkdir -p "$BARS_DIR/$coin"
    rsync -avz --partial $EXCLUDES \
        "$REMOTE:$REMOTE_DIR/data/bars/$coin/" "$BARS_DIR/$coin/"
}
export -f sync_coin
export REMOTE REMOTE_DIR EXCLUDES BARS_DIR

coins="$(ssh "$REMOTE" "ls $REMOTE_DIR/data/bars")"

printf '%s\n' "$coins" | xargs -P "$CONC" -n 1 bash -c 'sync_coin "$1"' _
