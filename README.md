# iFinD 行业归因与板块强度检测系统

基于 iFinD API 开发的量化行业归因与板块强度检测系统，使用同花顺概念板块作为行业分类。

## 项目结构

```
ifind_sector_attribution/
├── config.py              # 配置（token、URL、概念代码列表）
├── ifind_client.py        # iFinD API 客户端封装
├── database.py            # SQLite 数据库封装
├── sync_pipeline.py       # 数据同步管线
├── core_calculator.py     # 核心计算引擎
├── api_server.py          # FastAPI 服务层
├── main.py                # 入口脚本
├── requirements.txt       # 依赖
├── data/                  # 数据库文件
└── tests/
    └── test_api.py        # 接口测试脚本
```

## 核心功能

- **个股多概念归因**：L1 权重归因法，判断哪个概念对个股涨幅影响最大
- **板块强度检测**：三维评分（涨幅强度、上涨广度、相对强度），快速定位最强板块
- **组合归因分析**：根据持仓组合，定位当前最强势板块

## 5 个 iFinD 接口

| # | 接口 | 用途 | 缓存策略 |
|---|---|---|---|
| 1 | `basic_data_service` (ths_the_ths_concept_index_stock) | 个股所属同花顺概念 | 一次性永久缓存 |
| 2 | `data_pool` (p03473) | 概念板块成分股 | 一次性永久缓存 |
| 3 | `cmd_history_quotation` | 历史行情日K | 按 (code, date) 缓存 |
| 4 | `high_frequency` | 1min K线 | 保留最近2天 |
| 5 | `basic_data_service` (ths_index_short_name_index) | 概念基本信息字典 | 一次性永久缓存 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行接口测试

```bash
python main.py test
```

### 3. 首次部署初始化

```bash
# 使用默认测试股票
python main.py init

# 或指定股票列表文件
python main.py init --stocks stocks.txt
```

### 4. 每日同步

```bash
python main.py daily --date 20260612
```

### 5. 启动 API 服务

```bash
python main.py server --host 0.0.0.0 --port 8000
```

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `GET /api/sector/rankings` | 获取板块强度排名 |
| `POST /api/attribution/stock` | 个股多概念归因 |
| `POST /api/attribution/portfolio` | 组合归因 + 强势板块定位 |
| `GET /api/realtime/sector` | 最新板块强度排名 |
| `GET /api/concept/list` | 全部概念板块列表 |
| `GET /api/concept/members` | 概念板块成分股 |

## 数据模型

SQLite 数据库包含 7 张表：
- `ths_concept_dict` - 概念板块字典
- `stock_concept_map` - 个股-概念映射
- `concept_members` - 概念成分股
- `daily_kline` - 日K线
- `min1_kline` - 1min K线
- `concept_strength` - 板块强度评分
- `stock_attribution` - 个股归因结果
