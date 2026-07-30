#!/bin/bash
# 股池归因定时推送 - crontab 启动脚本
# 用法: 由 crontab 调用，传入时间槽参数
#   run_push.sh 933   # 09:33 槽
#   run_push.sh 945   # 09:45 槽
#   run_push.sh 1000  # 10:00 槽
#   run_push.sh 1430  # 14:30 槽
#
# 交易日校验在程序内完成（cron 只用 1-5 工作日粗筛），节假日会自动跳过。

set -euo pipefail

PROJ_DIR="/root/projects/2.monitor_940/ifind-sector-attribution"
PYTHON="/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python"

SLOT="${1:-}"
if [ -z "$SLOT" ]; then
    echo "[run_push] 缺少时间槽参数（933/945/1000/1430）" >&2
    exit 1
fi

cd "$PROJ_DIR" || { echo "[run_push] 项目目录不存在: $PROJ_DIR" >&2; exit 1; }

# 加载 .env 环境变量（token 等敏感配置）
if [ -f "$PROJ_DIR/.env" ]; then
    set -a
    source "$PROJ_DIR/.env" 2>/dev/null || true
    set +a
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJ_DIR"

exec "$PYTHON" "$PROJ_DIR/main.py" push --slot "$SLOT"
