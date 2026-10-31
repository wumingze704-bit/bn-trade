#!/usr/bin/env bash
# 本地 <-> 服务器同步：推代码、拉分钟数据与交易范围快照、推模型产物。
# 服务器地址由 ~/.ssh/config 中的 Host tokyo 提供，脚本不写 IP 与密钥。
# 规格：docs/02-系统架构与部署.md §5.8
set -euo pipefail

REMOTE="${TOKYO_REMOTE:-tokyo}"
REMOTE_DIR="trade"

usage() {
  cat <<'EOF'
用法: sync.sh <push-code|pull-data|push-models> [model_id]

  push-code          推送代码到服务器裸仓库（git push tokyo main）
  pull-data          拉取服务器分钟数据与交易范围快照到本地 data/
  push-models <id>   推送指定 model_id 的模型产物到服务器
EOF
}

case "${1:-}" in
  push-code)
    git push "$REMOTE" main
    ;;
  pull-data)
    rsync -avz --partial "$REMOTE:$REMOTE_DIR/data/bars/" ./data/bars/
    rsync -avz --partial "$REMOTE:$REMOTE_DIR/data/universe/" ./data/universe/
    ;;
  push-models)
    if [[ $# -lt 2 ]]; then
      usage
      exit 1
    fi
    rsync -avz "./artifacts/models/$2/" "$REMOTE:$REMOTE_DIR/artifacts/models/$2/"
    ;;
  *)
    usage
    exit 1
    ;;
esac
