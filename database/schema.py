# -*- coding: utf-8 -*-
"""monitor 私有表的建表 DDL（板块三表由 ifind-sector-hub 组件管理，不在此处）"""

DDL = """
-- 日K线行情（个股 + 概念指数）
CREATE TABLE IF NOT EXISTS daily_kline (
    code          TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    pre_close     REAL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    change_ratio  REAL,
    volume        REAL,
    amount        REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dk_date ON daily_kline(trade_date);

-- 1min K线行情（盘中用，仅保留最近2个交易日）
CREATE TABLE IF NOT EXISTS min1_kline (
    code          TEXT NOT NULL,
    trade_time    TEXT NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    change_ratio  REAL,
    volume        REAL,
    amount        REAL,
    PRIMARY KEY (code, trade_time)
);
CREATE INDEX IF NOT EXISTS idx_m1_code_time ON min1_kline(code, trade_time);

-- 概念板块强度评分（每日结果）
CREATE TABLE IF NOT EXISTS concept_strength (
    calc_date      TEXT NOT NULL,
    concept_code   TEXT NOT NULL,
    s1_return      REAL,
    s2_breadth     REAL,
    s4_relative    REAL,
    score_1d       REAL,
    score_5d       REAL,
    score_20d      REAL,
    score_final    REAL,
    rank_1d        INTEGER,
    coherency      REAL,
    PRIMARY KEY (calc_date, concept_code)
);
CREATE INDEX IF NOT EXISTS idx_cs_date ON concept_strength(calc_date);

-- 个股归因结果（每日）
CREATE TABLE IF NOT EXISTS stock_attribution (
    stock_code     TEXT NOT NULL,
    calc_date      TEXT NOT NULL,
    total_return   REAL,
    top_concept    TEXT,
    top_contrib_pct REAL,
    attribution_json TEXT,
    PRIMARY KEY (stock_code, calc_date)
);

-- 自选股分组（同花顺 custom_block 导入，静态手动分组）
CREATE TABLE IF NOT EXISTS custom_group (
    group_id    TEXT NOT NULL,
    group_name  TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    PRIMARY KEY (group_id, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_cg_group ON custom_group(group_id);

-- 监控板块持久化选择（读取时还需应用成员数资格规则）
CREATE TABLE IF NOT EXISTS watched_concepts (
    concept_code  TEXT PRIMARY KEY,
    added_at      TEXT NOT NULL
);

-- ========== 知识图谱（KG）：节点 / 双时态边 / 快照 / 变更日志 ==========
-- 节点统一建模：股票 + 板块。node_id 形如 'STOCK:600519.SH' / 'SECTOR:884091.TI'
CREATE TABLE IF NOT EXISTS kg_node (
    node_id     TEXT PRIMARY KEY,
    node_type   TEXT NOT NULL,           -- 'stock' | 'sector'
    code        TEXT NOT NULL,           -- 原始代码（600519.SH）
    name        TEXT,                    -- 股票名/板块名
    sector_type TEXT,                    -- 板块专用: 'industry'(884) / 'concept'(885/886)；股票为 NULL
    props_json  TEXT,                    -- 扩展属性（member_count、monitorable 等）
    first_seen  TEXT NOT NULL,           -- 首次进入图谱日期
    last_seen   TEXT NOT NULL,           -- 最近一次确认存在的日期
    is_active   INTEGER DEFAULT 1        -- 长期未被确认置 0，不物理删
);
CREATE INDEX IF NOT EXISTS idx_kg_node_type ON kg_node(node_type, is_active);
CREATE INDEX IF NOT EXISTS idx_kg_node_code ON kg_node(code);

-- 双时态归属边：valid_to IS NULL 表示当前生效。
-- 同一对(股,板块)可有多个 source 的生效边并存（多源交叉验证）。
CREATE TABLE IF NOT EXISTS kg_edge (
    edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id      TEXT NOT NULL,           -- 股票节点 'STOCK:xxx'
    dst_id      TEXT NOT NULL,           -- 板块节点 'SECTOR:xxx'
    edge_type   TEXT NOT NULL DEFAULT 'BELONGS_TO',
    source      TEXT NOT NULL,           -- 'ifind_p03473' / 'ifind_concept' / 未来其他源
    valid_from  TEXT NOT NULL,           -- 关系生效日（快照日期）
    valid_to    TEXT,                    -- NULL=生效中；非空=已失效日
    props_json  TEXT,                    -- 边动态属性（corr_20d 等）
    confidence  REAL DEFAULT 1.0         -- 双源 1.0 / 仅主源 0.8 / 仅验证源 0.6
);
CREATE INDEX IF NOT EXISTS idx_kg_edge_src ON kg_edge(src_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_kg_edge_dst ON kg_edge(dst_id, valid_to);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_edge_open
    ON kg_edge(src_id, dst_id, source) WHERE valid_to IS NULL;

-- 图谱版本快照（每周 kg_update 产出；kg_init 产出首份）
CREATE TABLE IF NOT EXISTS kg_snapshot (
    snapshot_id  TEXT PRIMARY KEY,       -- '20260714'
    built_at     TEXT NOT NULL,
    node_count   INTEGER,
    edge_count   INTEGER,
    added_edges  INTEGER,
    removed_edges INTEGER,
    sources      TEXT,                   -- 参与源列表 json
    stats_json   TEXT                    -- 度分布/置信分布/族群等统计
);

-- 变更日志（P2 周维护 diff 产出；P1 仅建表）
CREATE TABLE IF NOT EXISTS kg_change (
    change_date TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    edge_id     INTEGER,
    change_type TEXT NOT NULL,           -- 'edge_added' / 'edge_removed' / ...
    detail_json TEXT,
    PRIMARY KEY (change_date, node_id, edge_id)
);

-- 板块族群（P3：Louvain 社区发现结果，供图谱投影图着色/族群看板）
CREATE TABLE IF NOT EXISTS kg_community (
    calc_date    TEXT NOT NULL,
    community_id INTEGER NOT NULL,       -- 族群编号（按规模重排，1=最大）
    node_id      TEXT NOT NULL,          -- 成员节点（板块为主，含股票）
    node_type    TEXT NOT NULL,
    PRIMARY KEY (calc_date, node_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_comm_date ON kg_community(calc_date, community_id);
"""
