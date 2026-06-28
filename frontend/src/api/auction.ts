/** 集合竞价类型 + 接口 */
import http from './client'

// 竞价个股（4 因子评分）
export interface AuctionStock {
  code: string
  name: string
  gap_pct: number       // 竞价涨跌幅（高开）
  vol_ratio: number     // 量比 = 竞价量/昨日量
  order_imbalance: number  // 挂单失衡 [-1,1]
  trend_score: number   // 9:20→9:25 趋势
  score: number         // 综合分
  holding: boolean
}

// 竞价分组
export interface AuctionGroup {
  group_id: string
  group_name: string
  member_count: number
  sector_gap: number
  sector_vol_ratio: number
  sector_imbalance: number
  coherency: number     // 联动率 [0,1]
  score: number
  is_zt: boolean
  top_stocks: AuctionStock[]
}

// 竞价市场统计
export interface AuctionMarketStats {
  stock_count: number
  avg_gap: number
  up_count: number
  down_count: number
  strong_gap_count: number  // 高开>2%
  limit_up_count: number    // 涨停级(>=9.8)
  explode_count: number     // 爆量(vol_ratio>=2)
}

// 竞价看板返回
export interface AuctionPayload {
  trade_date?: string
  is_today?: boolean
  snapshot_time?: string
  yest_date?: string | null
  market_stats?: AuctionMarketStats
  top_stocks?: AuctionStock[]
  top_groups?: AuctionGroup[]
  zt_groups?: AuctionGroup[]
  error?: string
}

export function getAuctionDashboard(params: { trade_date?: string } = {}): Promise<AuctionPayload> {
  return http.get('/api/auction/dashboard', { params })
}
