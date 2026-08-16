---
title: "快速开始"
nav_order: 2
description: 从零部署：环境、依赖、token 配置、初始化到启动服务
---

# 快速开始

本页带你从零跑起来：装好依赖 → 配置 token → 初始化数据 → 启动服务 → （可选）配置定时任务。全程约 20 分钟（不含 iFinD 账号申请）。

## 1. 前置条件

| 依赖 | 说明 |
|---|---|
| Python 3.11+ | 推荐 conda 独立环境 |
| iFinD 账号 | 提供数据接口的 `ACCESS_TOKEN` / `REFRESH_TOKEN` |
| 中焯行情 API（可选） | 盘中实时监控的分时数据源，不配则实时链路不可用，其余功能不受影响 |
| kline-fetcher（可选） | 分时数据 SDK，本地包需单独安装 |
| LLM API Key（可选） | 板块轮动分析用，不配则该 Tab 不可用 |

## 2. 安装依赖

```bash
git clone https://github.com/seuzxh/ifind-sector-attribution.git
cd ifind-sector-attribution
pip install -r requirements.txt

# 分时数据依赖（盘中实时监控用，需单独装）
pip install -e /path/to/kline-fetcher
# 或：pip install git+https://github.com/seuzxh/kline-fetcher.git
```

## 3. 配置 token

在项目根目录创建 `config_local.py`（已被 `.gitignore` 忽略，**不会提交**）：

```python
# iFinD token（盘后 daily 同步 / 归因用；access_token 7 天过期可自动刷新）
ACCESS_TOKEN = "你的 access token"
REFRESH_TOKEN = "你的 refresh token"

# 中焯行情 API 地址（盘中实时监控用，敏感不入库）
KLINE_API_BASE_URL = "http://your-kline-api-host:port"

# iFinD MCP server 鉴权 JWT（轮动分析 / 强势归类选股用）
IFIND_MCP_TOKEN = "你的 mcp jwt token"

# 轮动分析 LLM（火山方舟 Coding Plan）
# 注意：base_url 必须用 /api/coding/v3（走 Plan 额度），用 /api/v3 会产生额外费用
LLM_API_KEY = "你的 ark api key"
LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
LLM_MODEL = "doubao-seed-2.0-pro"

# 飞书归因推送 webhook（定时推送用）
PUSH_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
```

## 4. 验证接口连通

```bash
python main.py test    # 测试 5 个 iFinD 接口连通性
```

## 5. 首次初始化（拉字典 + 成分股 + 映射）

```bash
python main.py init
```

`init` 依次执行：概念字典 → 个股-概念映射 → 行业成分股 → 概念板块全集补全。最后一步必须执行：接口1 返回的 `885xxx` 概念码与内置 `700xxx/884xxx` 行业码交集为 0，补全后归因 JOIN 才能打通（详见[架构设计（V1 历史）](architecture/ARCHITECTURE.md)）。

## 6. 每日盘后同步

```bash
python main.py daily --date 20260817    # 日期须为交易日
```

同步当日 K 线 → 计算多周期板块强度 → 计算个股归因。

## 7. 导入自选股分组（自选看板用）

```bash
python main.py import-groups    # 读 ths-custom-block-data/同花顺自选分组导出.json，幂等
```

同花顺 custom_block 导出 JSON → `custom_group` 表，自动过滤指数 / ETF / 可转债等非 A 股标的。

## 8. 启动服务

```bash
# 方式A：systemd（生产推荐，开机自启 + 崩溃自动重启）
sudo bash install_service.sh

# 方式B：前台手动启动（调试用）
python main.py server --host 0.0.0.0 --port 8000
```

浏览器打开 `http://localhost:8000` 进入可视化看板（7 个 Tab 的用法见[交互指南](guides/interaction.md)）。

## 9. 定时任务（可选）

两个独立的 crontab 任务（均只在交易日有效，节假日程序内自动跳过）：

```bash
# 盘后数据同步（每日 16:00 示例）
0 16 * * 1-5  cd /path/to/ifind-sector-attribution && python main.py daily >> data/daily.log 2>&1

# 飞书归因推送（交易日 9:33 / 9:45 / 10:00 / 14:30 四个时刻）
33 9  * * 1-5  bash scripts/run_push.sh 933  >> data/push_933.log 2>&1
45 9  * * 1-5  bash scripts/run_push.sh 945  >> data/push_945.log 2>&1
0  10 * * 1-5  bash scripts/run_push.sh 1000 >> data/push_1000.log 2>&1
30 14 * * 1-5  bash scripts/run_push.sh 1430 >> data/push_1430.log 2>&1
```

推送脚本需用项目配套的 conda Python，可直接执行 `bash scripts/install_push_cron.sh` 一键安装（幂等，与既有 crontab 共存）。

## 10. 验收清单

- [ ] `python main.py test` 全部接口通过
- [ ] `python main.py daily --date <交易日>` 无报错，`data/sector_attribution.db` 有当日数据
- [ ] 浏览器打开看板能正常渲染
- [ ] （可选）`python main.py push --slot 1430 --dry-run` 打印出两条卡片 JSON
- [ ] （可选）飞书群收到自选 / 全市场两条推送

## 常见问题

| 现象 | 处理 |
|---|---|
| 接口报 `errorcode:-1302` / HTTP 401 | `ACCESS_TOKEN` 过期；程序会自动用 `REFRESH_TOKEN` 刷新重试，无需手动处理 |
| 实时看板无数据 | 检查 `KLINE_API_BASE_URL` 是否配置、kline-fetcher 是否安装 |
| `daily` 传入非交易日返回空 | 换交易日日期（见 `GET /api/trade_calendar`） |
| 轮动分析报错 | 检查 `LLM_API_KEY` 与 `IFIND_MCP_TOKEN` 是否配置 |

更多配置项说明见仓库根目录 `README.md`；部署细节（公网访问、SSH 隧道、故障排查）见[部署手册](ops/DEPLOYMENT.md)。
