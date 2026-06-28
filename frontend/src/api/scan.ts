/** 强势归类(scan/market_scan)接口与类型 */
import http from './client'

// 命中股票
export interface HitStock {
  code: string
  name: string
  change_ratio: number
}

// 归类分组
export interface ScanGroup {
  group_id: string
  group_name: string
  hit_count: number
  member_total: number
  coverage: number       // 命中率 [0,1]
  hit_avg_change: number
  hits: HitStock[]
}

// scan 返回
export interface ScanPayload {
  query?: string
  pool_size?: number
  hit_total?: number
  group_hit_count?: number
  groups?: ScanGroup[]
  error?: string
  raw_preview?: string
}

/** 自选强势归类（命中股票 ∩ 自选分组） */
export function scanCustomGroups(query: string): Promise<ScanPayload> {
  return http.get('/api/custom/scan', { params: { query } })
}

/** 全市场强势归类（命中 → 884 概念板块） */
export function scanMarketGroups(query: string): Promise<ScanPayload> {
  return http.get('/api/market/scan', { params: { query } })
}
