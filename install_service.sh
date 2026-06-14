#!/bin/bash
# iFinD 监控服务 systemd 安装脚本
# 用法：sudo bash install_service.sh

set -e

SERVICE_NAME="ifind-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_SERVICE="${SCRIPT_DIR}/${SERVICE_NAME}.service"

echo "========================================"
echo "  iFinD 监控服务安装"
echo "========================================"

# 1. 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo "错误：请用 root 或 sudo 执行"
  exit 1
fi

# 2. 检查 service 文件存在
if [ ! -f "$SRC_SERVICE" ]; then
  echo "错误：找不到 ${SRC_SERVICE}"
  exit 1
fi

# 3. 检查 python 可执行文件
PY="/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python"
if [ ! -x "$PY" ]; then
  echo "错误：找不到 python：$PY"
  echo "请确认 conda 环境 vibe-trading 已安装"
  exit 1
fi

# 4. 检查 config_local.py（token 配置）
if [ ! -f "${SCRIPT_DIR}/config_local.py" ]; then
  echo "警告：未找到 config_local.py，服务将无法访问 iFinD API"
  echo "      请先创建 config_local.py 配置 ACCESS_TOKEN"
  read -p "继续安装？(y/N) " confirm
  [ "$confirm" = "y" ] || exit 0
fi

# 5. 创建日志文件
touch /var/log/ifind-monitor.log
chmod 644 /var/log/ifind-monitor.log
echo "✓ 日志文件：/var/log/ifind-monitor.log"

# 6. 安装 service 文件
cp "$SRC_SERVICE" "$SERVICE_FILE"
chmod 644 "$SERVICE_FILE"
echo "✓ 服务文件：$SERVICE_FILE"

# 7. 重载 systemd 配置
systemctl daemon-reload
echo "✓ systemd 配置已重载"

# 8. 启用开机自启
systemctl enable "${SERVICE_NAME}.service"
echo "✓ 已设置开机自启"

# 9. 启动服务
systemctl restart "${SERVICE_NAME}.service"
sleep 3
echo "✓ 服务已启动"

# 10. 检查状态
echo ""
echo "========================================"
echo "  服务状态"
echo "========================================"
systemctl status "${SERVICE_NAME}.service" --no-pager -l | head -15

# 11. 验证端口
echo ""
echo "========================================"
echo "  端口监听检查"
echo "========================================"
if ss -tlnp | grep -q ":8000"; then
  echo "✓ 端口 8000 正在监听"
else
  echo "✗ 端口 8000 未监听，请检查日志：journalctl -u ${SERVICE_NAME} -e"
fi

# 12. 本地访问测试
echo ""
echo "========================================"
echo "  本地访问测试"
echo "========================================"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8000/ 2>/dev/null || echo "失败")
if [ "$HTTP_CODE" = "200" ]; then
  echo "✓ 本地访问正常（HTTP 200）"
else
  echo "✗ 本地访问失败（HTTP $HTTP_CODE）"
fi

# 13. 访问方式提示
echo ""
echo "========================================"
echo "  访问方式"
echo "========================================"
echo "服务器地址：115.191.14.82"
echo ""
echo "方式A：直接访问（需放行 8000 端口）"
echo "  浏览器打开：http://115.191.14.82:8000"
echo ""
echo "方式B：SSH 隧道（不开放端口时用）"
echo "  本地执行：ssh -L 8000:127.0.0.1:8000 <用户名>@115.191.14.82"
echo "  浏览器打开：http://127.0.0.1:8000"
echo ""
echo "若方式A无法访问，检查云服务商安全组是否放行 TCP 8000 入站"
echo ""
echo "========================================"
echo "  常用命令"
echo "========================================"
echo "查看状态：systemctl status ${SERVICE_NAME}"
echo "查看日志：journalctl -u ${SERVICE_NAME} -f"
echo "          tail -f /var/log/ifind-monitor.log"
echo "重启服务：systemctl restart ${SERVICE_NAME}"
echo "停止服务：systemctl stop ${SERVICE_NAME}"
echo "取消自启：systemctl disable ${SERVICE_NAME}"
