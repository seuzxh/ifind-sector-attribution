/** 强势归类(scan/market_scan)接口与类型 */
import http from './client'

// 命中股票
export interface HitStock {
  code: string
  name: string
  change_ratio: number
  corr_20d?: number | null   // market_scan（KG 归类）带：该股与板块的 20 日 ρ
}

// 归类分组
export interface ScanGroup {
  group_id: string
  group_name: string
  hit_count: number
  member_total: number
  coverage: number       // market_scan=组内命中占比；custom=板块命中率 [0,1]
  hit_avg_change: number | null
  hits: HitStock[]
  // market_scan（KG 富集归类）扩展字段
  sector_code?: string
  sector_type?: string | null
  lift?: number | null   // 富集倍数（组内命中率 ÷ 板块成员占全市场比例）
  is_watched?: boolean   // 是否「监控板块管理」已勾选
}

// scan 返回
export interface ScanPayload {
  query?: string
  pool_size?: number
  hit_total?: number
  group_hit_count?: number
  order?: 'lift' | 'hits'
  groups?: ScanGroup[]
  error?: string
  raw_preview?: string
}

/** 自选强势归类（命中股票 ∩ 自选分组） */
export function scanCustomGroups(query: string): Promise<ScanPayload> {
  return http.get('/api/custom/scan', { params: { query } })
}

/** 全市场强势归类（全市场命中 → 知识图谱富集归类：lift/命中数排序） */
export function scanMarketGroups(query: string, order: 'lift' | 'hits' = 'lift'): Promise<ScanPayload> {
  return http.get('/api/market/scan', { params: { query, order } })
}
