/** 看板数据类型定义（realtime/custom/auction 共用） */

// 成分股（卡片表格里的一行）
export interface MemberStock {
  code: string
  name: string
  change_ratio: number
  speed: number
  body: number
  acceleration: number
  limit: number
  score: number
}

// 板块/分组排行里的一项
export interface SectorEntry {
  concept_code: string
  concept_name: string
  score: number
  s1_return: number
  body: number | null        // 实体涨幅（history 模式可能为 null）
  s2_breadth: number
  member_count: number
  holding_in_group?: string[]  // 本分组含的持仓股（仅 custom）
  members_top10: MemberStock[]
}

// 市场统计
export interface MarketStats {
  stock_count: number
  market_avg_change: number
  up_count: number
  down_count: number
  flat_count: number
  limit_up_count?: number
}

// 看板返回（realtime / custom / auction 通用骨架）
export interface DashboardPayload {
  trade_date?: string
  snapshot_time?: string
  latest_time?: string
  available_times?: string[]
  is_today?: boolean
  error?: string
  market_stats?: MarketStats
  top_sectors?: SectorEntry[]
  bottom_sectors?: SectorEntry[]
  zt_sectors?: SectorEntry[]      // 涨停分组（仅 custom）
  holding_stocks?: string[]
  // auction 专用
  top_stocks?: any[]
  groups?: Record<string, any>
  [key: string]: any
}
