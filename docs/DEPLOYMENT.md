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

服务器在内网，服务绑 `127.0.0.1`，**通过 SSH 隧道访问**（不直接暴露公网）。

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
| `ExecStart` | python main.py server **--host 127.0.0.1** --port 8000 | 仅绑本地，通过 SSH 隧道访问 |
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

## 3. 访问方式：SSH 隧道

服务绑 `127.0.0.1:8000`，外部无法直接访问。通过 SSH 端口转发把服务映射到你本地机器。

### 基本用法

在你的**本地电脑**（笔记本/工作站）执行：

```bash
ssh -L 8000:127.0.0.1:8000 <用户名>@<服务器入口地址>
```

- `<用户名>`：服务器登录用户（如 root）
- `<服务器入口地址>`：你平时 SSH 连服务器用的地址（IP 或域名，可能是跳板机）

然后本地浏览器打开：**`http://127.0.0.1:8000`**（这是你本地机器的地址，SSH 会自动转发到服务器）

### 后台隧道（不占用终端）

加 `-N -f` 让隧道后台运行：

```bash
ssh -N -f -L 8000:127.0.0.1:8000 <用户名>@<服务器入口地址>
```

- `-N`：不执行远程命令，仅做端口转发
- `-f`：认证后转入后台

关闭后台隧道：

```bash
# Linux/Mac：查找并杀掉隧道进程
ps aux | grep "ssh -N -f -L 8000" | grep -v grep
kill <PID>

# 或用 pkill
pkill -f "ssh -N -f -L 8000"
```

### 通过跳板机访问（两层转发）

如果服务器在跳板机后面：

```bash
# 本地 → 跳板机 → 目标服务器
ssh -L 8000:127.0.0.1:8000 -J <跳板机用户>@<跳板机地址> <目标用户>@<目标服务器内网IP>
```

### SSH config 配置（推荐，简化日常使用）

在本地 `~/.ssh/config` 添加：

```
Host ifind-monitor
    HostName <服务器入口地址>
    User <用户名>
    LocalForward 8000 127.0.0.1:8000
    # 若经过跳板机，加：ProxyJump <跳板机>
```

之后只需 `ssh ifind-monitor`，隧道自动建立，浏览器开 `http://127.0.0.1:8000`。

### Windows 用户

- **PowerShell/cmd**：同样用 `ssh -L 8000:127.0.0.1:8000 ...`（Win10+ 自带 OpenSSH）
- **PuTTY**：Connection → SSH → Tunnels，Source port 填 `8000`，Destination 填 `127.0.0.1:8000`，点 Add 然后 Open
- **MobaXterm**：Session → SSH → Tunneling，新建端口转发

### 多人同时访问

SSH 隧道是每人在自己电脑上各建一条。若需多人共享一条隧道，可在本地把 `-L` 改为绑全部网卡：

```bash
# 允许同内网其它人通过你的电脑访问
ssh -L 0.0.0.0:8000:127.0.0.1:8000 <用户名>@<服务器>
# 同事访问 http://<你的本地IP>:8000
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

### SSH 隧道连不上

```bash
# 1. 确认服务在跑且监听 127.0.0.1
sudo ss -tlnp | grep 8000
# 应显示 127.0.0.1:8000

# 2. 在服务器本地测试
curl http://127.0.0.1:8000/    # 应返回 200

# 3. SSH 隧道错误排查
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

- **非交易时段**：接口4 在非交易时段（周末/夜间）返回空，属正常。实时模式仅 9:30~9:40 有效。
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
