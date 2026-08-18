#!/bin/bash
# 知识图谱周维护（crontab 周日 20:00 调用）：拉两源 → diff → 开/关边 + 变更日志 + 快照
# 参照 run_push.sh 模式：固定 PROJ_DIR + 绝对路径 conda python + PYTHONPATH
PROJ_DIR=/root/projects/2.monitor_940/ifind-sector-attribution
PY=/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python

cd "$PROJ_DIR" || exit 1
export PYTHONPATH="$PROJ_DIR"
exec "$PY" main.py kg_update
