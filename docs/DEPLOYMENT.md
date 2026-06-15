# 部署运维手册

本文件记录 iFinD 板块强度监控系统的部署、访问方式、日常运维与故障排查。

## 目录

- [1. 环境要求](#1-环境要求)
- [2. systemd 服务部署](#2-systemd-服务部署)
- [3. 访问方式：SSH 隧道](#3-访问方式ssh-隧道)
- [4. 日常运维](#4-日常运维)
- [5. 故障排查](#5-故障排查)
- [6. 升级与回滚](#6-升级与回滚)

---

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| Python 环境 | conda 环境 `vibe-trading`（含 fastapi/uvicorn/pandas/numpy/plotly） |
| Python 路径 | `/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python` |
| 项目目录 | `/root/projects/2.monitor_940/ifind-sector-attribution` |
| token 配置 | `config_local.py`（含 `ACCESS_TOKEN` / `REFRESH_TOKEN`，已 gitignore） |
| 数据库 | `data/sector_attribution.db`（SQLite，首次 init 自动创建） |
| 操作系统 | Linux（systemd） |
| 权限 | root（因 conda 环境在 /root 下） |
| **服务器地址** | **115.191.14.82** |

服务绑 `0.0.0.0:8000`，**支持两种访问方式**：公网直连（需安全组放行）和 SSH 隧道（无需开放端口）。

---

## 2. systemd 服务部署

### 一键安装

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution
sudo bash install_service.sh
```

脚本自动完成：环境检查 → 安装 service 文件 → 启用开机自启 → 启动 → 验证端口和访问。

### service 配置说明（ifind-monitor.service）

| 配置项 | 值 | 说明 |
|---|---|---|
| `Type` | simple | 前台进程型服务 |
| `User` | root | 运行用户（conda 在 /root 下） |
| `WorkingDirectory` | 项目目录 | 让 `config_local.py` / `data/` 相对路径生效 |
| `Environment=PYTHONPATH` | 项目目录 | 确保 import 正确 |
| `ExecStart` | python main.py server **--host 0.0.0.0** --port 8000 | 绑全部网卡，公网 115.191.14.82:8000 可达 |
| `Restart` | always | 崩溃自动重启 |
| `RestartSec` | 5 | 崩溃后 5 秒重启 |
| `KillSignal` | SIGINT | 优雅停止（uvicorn 收到 SIGINT 完成清理） |
| `TimeoutStopSec` | 30 | 停止超时 30 秒 |
| `WantedBy` | multi-user.target | 开机自启 |

### 手动安装（不用脚本）

```bash
# 复制 service 文件
sudo cp ifind-monitor.service /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/ifind-monitor.service

# 创建日志文件
sudo touch /var/log/ifind-monitor.log
sudo chmod 644 /var/log/ifind-monitor.log

# 重载 + 启用 + 启动
sudo systemctl daemon-reload
sudo systemctl enable ifind-monitor
sudo systemctl start ifind-monitor
```

---

## 3. 访问方式

服务器地址 **115.191.14.82**。服务绑 `0.0.0.0:8000`，支持两种访问方式。

### 方式A：公网直连（推荐，日常使用）

浏览器直接打开：

```
http://115.191.14.82:8000
```

**前提**：云服务商安全组放行 TCP 8000 入站（见下方）。

### ⚠ 安全组放行（公网直连的前提）

服务绑 `0.0.0.0` + 本机防火墙（ufw）已关闭，但**云服务商的安全组**是独立的一层。若公网无法访问，**99% 是安全组未放行**。

在云控制台的安全组规则里添加：

| 方向 | 协议 | 端口 | 来源 |
|---|---|---|---|
| 入站 | TCP | 8000 | 0.0.0.0/0（或限制特定IP段，更安全） |

修改后立即生效，无需重启服务。

### 方式B：SSH 隧道（无需开放端口，更安全）

不开放公网端口时，通过 SSH 端口转发访问。在你的**本地电脑**执行：

```bash
ssh -L 8000:127.0.0.1:8000 <用户名>@115.191.14.82
```

然后本地浏览器打开：**`http://127.0.0.1:8000`**

后台运行隧道（不占用终端）：

```bash
ssh -N -f -L 8000:127.0.0.1:8000 <用户名>@115.191.14.82
```

关闭后台隧道：`pkill -f "ssh -N -f -L 8000"`

### 两种方式对比

| | 公网直连 | SSH 隧道 |
|---|---|---|
| 便利性 | 浏览器直接开 | 每次先建隧道 |
| 安全性 | 端口对公网开放 | 仅 SSH 用户可达 |
| 适用 | 日常监控、多人共享 | 敏感场景、单人调试 |

### 更换端口

若 8000 被占用或想换端口：

1. 编辑 `/etc/systemd/system/ifind-monitor.service`，把 `--port 8000` 改为目标端口
2. `sudo systemctl daemon-reload && sudo systemctl restart ifind-monitor`
3. 公网访问需同步更新安全组规则

### HTTPS（可选，生产建议）

当前是 HTTP。若需 HTTPS，加 nginx 反向代理 + Let's Encrypt：

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

nginx 配置示例（反代到 8000）：

```nginx
server {
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 4. 日常运维

### 常用命令（在服务器上执行）

```bash
# 状态
systemctl status ifind-monitor

# 启动 / 停止 / 重启
sudo systemctl start ifind-monitor
sudo systemctl stop ifind-monitor
sudo systemctl restart ifind-monitor

# 开机自启管理
sudo systemctl enable ifind-monitor     # 启用
sudo systemctl disable ifind-monitor    # 取消

# 日志（实时）
journalctl -u ifind-monitor -f              # systemd 日志
tail -f /var/log/ifind-monitor.log          # 服务 stdout/stderr

# 日志（历史）
journalctl -u ifind-monitor --since "1 hour ago"
journalctl -u ifind-monitor --since today | tail -50
```

### 日常数据维护

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution
PY=/root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python

# 每日盘后（约 16:00）同步数据
PYTHONPATH=. "$PY" main.py daily --date $(date +%Y%m%d)

# 盘前（约 9:15）筛选 watchlist
PYTHONPATH=. "$PY" main.py prescreen --date $(date +%Y%m%d)

# 定期清理海外数据（如有）
PYTHONPATH=. "$PY" main.py purge --vacuum
```

建议用 crontab 自动化（`crontab -e`）：

```cron
# 盘前筛选（周一至周五 9:15）
15 9 * * 1-5 cd /root/projects/2.monitor_940/ifind-sector-attribution && PYTHONPATH=. /root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python main.py prescreen --date $(date +\%Y\%m\%d) >> /var/log/ifind-prescreen.log 2>&1

# 盘后同步（周一至周五 16:30）
30 16 * * 1-5 cd /root/projects/2.monitor_940/ifind-sector-attribution && PYTHONPATH=. /root/Projects/5.test-autoresearch/qlib/miniconda3/envs/vibe-trading/bin/python main.py daily --date $(date +\%Y\%m\%d) >> /var/log/ifind-daily.log 2>&1
```

### 数据库备份

```bash
# 手动备份
cp data/sector_attribution.db data/sector_attribution.db.bak.$(date +%Y%m%d)

# 定时备份（crontab，每天 17:00）
0 17 * * * cp /root/projects/2.monitor_940/ifind-sector-attribution/data/sector_attribution.db /backup/sector_attribution.$(date +\%Y\%m\%d).db
```

---

## 5. 故障排查

### 服务无法启动

```bash
# 1. 查看详细错误
journalctl -u ifind-monitor -e | tail -30

# 2. 常见原因
#    - conda 环境路径变了 → 检查 ExecStart 的 python 路径
#    - config_local.py 缺失 → 服务无法访问 iFinD（但能启动）
#    - 端口被占用 → ss -tlnp | grep 8000
#    - data/ 目录权限 → chmod 或 chown
```

### 公网访问不通（方式A）

```bash
# 1. 确认服务在跑且监听 0.0.0.0
sudo ss -tlnp | grep 8000
# 应显示 0.0.0.0:8000（若显示 127.0.0.1 则公网不通）

# 2. 在服务器本地测试
curl http://127.0.0.1:8000/    # 应返回 200

# 3. 检查本机防火墙
sudo ufw status
sudo iptables -L INPUT -n | grep 8000

# 4. 检查云安全组（最常见原因）
#    → 云控制台确认 TCP 8000 入站已放行
#    本地能通但公网不通，几乎都是安全组问题
```

### SSH 隧道连不上（方式B）

```bash
# 1. 确认服务在跑
sudo ss -tlnp | grep 8000

# 2. SSH 隧道错误排查
#    "bind: Address already in use" → 本地 8000 被占用，换端口
#    ssh -L 8888:127.0.0.1:8000 ...  # 本地用 8888 访问
#    "channel_setup_fwd_listener: cannot listen to port" → 同上
```

### 隧道连上但页面打不开

```bash
# 1. 确认本地转发生效（本地电脑执行）
curl http://127.0.0.1:8000/api/dates
# 应返回 JSON 日期列表

# 2. 浏览器缓存 → 强制刷新 Ctrl+Shift+R
# 3. plotly.js CDN 被墙 → 换 CDN 或本地 vendored（见下）
```

### iFinD API 调用失败（401/403）

```bash
# token 过期。检查 config_local.py 的 ACCESS_TOKEN
cat config_local.py | grep ACCESS_TOKEN

# 更新 token 后重启服务
sudo systemctl restart ifind-monitor
```

### 实时模式无数据

- **非交易时段**：分时数据 API 在非交易时段（周末/夜间/< 9:15）返回空，属正常。前端会通过 `/api/session_status` 判断时段，盘前(`pre_open`)和收盘后(`closed`)自动停止轮询并显示友好提示，9:15 集合竞价开始自动恢复。
- **集合竞价阶段（9:15~9:25）**：此阶段 `trading` 为空但 `pre_market` 有 ref_price，系统用末点 ref_price 算涨跌幅（speed/body/acceleration 置 0），监控从 9:15 就开始。进度条自动从 09:15 起。
- **watchlist 为空**：若开 watchlist 聚焦但当日未跑 prescreen，会提示"请先盘前筛选"。

### 性能问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 实时模式响应慢（2分钟+） | 全市场模式拉 5500 只 | 开启 watchlist 聚焦（290 只，30秒） |
| daily 同步慢 | 全市场 K 线同步 | 正常，约 2 分钟 |
| init 慢 | 1121 概念成分股并发拉取 | 正常，约 1-2 分钟 |

---

## 6. 升级与回滚

### 升级代码

```bash
cd /root/projects/2.monitor_940/ifind-sector-attribution
git pull

# 若改了 service 文件
sudo cp ifind-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload

# 重启生效
sudo systemctl restart ifind-monitor
```

### 回滚

```bash
# 代码回滚
git log --oneline -10
git checkout <commit-hash>

# 数据库回滚（从备份恢复）
sudo systemctl stop ifind-monitor
cp data/sector_attribution.db.bak.YYYYMMDD data/sector_attribution.db
sudo systemctl start ifind-monitor
```

### 完整卸载

```bash
sudo systemctl stop ifind-monitor
sudo systemctl disable ifind-monitor
sudo rm /etc/systemd/system/ifind-monitor.service
sudo systemctl daemon-reload
# 数据和代码保留，如需删除手动 rm -rf 项目目录
```
