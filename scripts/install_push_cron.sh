#!/bin/bash
# 安装股池归因定时推送的 crontab（幂等：已存在则跳过）
#
# 在周一~周五 4 个时刻各调用一次 run_push.sh：
#   09:33 / 09:45 / 10:00 / 14:30
# 交易日（节假日）校验在程序内完成，cron 的 1-5 仅做粗筛。
#
# 日志输出到 data/push_<slot>.log。本脚本不删除其他已有 crontab 条目（如 ifind-concept-trend）。
# 卸载：crontab -l | grep -v 'run_push.sh' | crontab -

set -euo pipefail

PROJ_DIR="/root/projects/2.monitor_940/ifind-sector-attribution"
SCRIPT="$PROJ_DIR/scripts/run_push.sh"
LOG_DIR="$PROJ_DIR/data"
MARKER="run_push.sh"   # 用脚本名作为幂等标记

# 4 个时间槽：(cron 分钟, cron 小时, slot)
SLOTS=(
    "33 9 933"
    "45 9 945"
    "0 10 1000"
    "30 14 1430"
)

mkdir -p "$LOG_DIR"

# 读取当前 crontab（无则空）
EXISTING="$(crontab -l 2>/dev/null || true)"

# 已安装过则跳过
if echo "$EXISTING" | grep -q "$MARKER"; then
    echo "[install-cron] 已存在含 $MARKER 的 crontab 条目，跳过安装。"
    echo "[install-cron] 当前相关条目："
    echo "$EXISTING" | grep "$MARKER" || true
    echo "[install-cron] 如需重装，先卸载：crontab -l | grep -v '$MARKER' | crontab -"
    exit 0
fi

NEW_LINES=""
for entry in "${SLOTS[@]}"; do
    read -r minute hour slot <<< "$entry"
    line="$minute $hour * * 1-5 $SCRIPT $slot >> $LOG_DIR/push_${slot}.log 2>&1"
    if [ -z "$NEW_LINES" ]; then
        NEW_LINES="$line"
    else
        NEW_LINES="$NEW_LINES"$'\n'"$line"
    fi
done

# 追加到现有 crontab（保留其他条目）
{ echo "$EXISTING"; echo "$NEW_LINES"; } | crontab -

echo "[install-cron] 已安装 4 条推送 crontab："
crontab -l | grep "$MARKER"
echo ""
echo "[install-cron] 日志：$LOG_DIR/push_<slot>.log"
echo "[install-cron] 手动测试：bash $SCRIPT 1430"
echo "[install-cron] 卸载：crontab -l | grep -v '$MARKER' | crontab -"
