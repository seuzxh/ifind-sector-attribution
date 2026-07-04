/** 盘前筛选相关（prescreen 触发 + watchlist 读取） */
import http from './client'

/** watchlist 成分股（rank_stock 仅 GET /api/watchlist 有；POST /api/prescreen 不带） */
export interface WatchlistStock {
  stock_code: string
  stock_name: string
  stock_5d_return: number
  rank_stock?: number
}

/** watchlist 板块 */
export interface WatchlistSector {
  concept_code: string
  concept_name: string
  sector_5d_return: number
  rank_sector: number
  stocks: WatchlistStock[]
}

/** 触发筛选 / 读取 watchlist 的统一返回 */
export interface WatchlistPayload {
  date?: string
  sector_count?: number
  stock_count?: number
  sectors?: WatchlistSector[]
  error?: string
}

/**
 * 触发盘前筛选：5 日涨幅选板块+成分股，存入 watchlist 表。
 * @param date YYYYMMDD，不传则服务端取今天（盘前场景会自动回退到最近交易日）
 */
export function runPrescreen(
  params: { date?: string; top_sector?: number; top_stock?: number } = {},
): Promise<WatchlistPayload> {
  return http.post('/api/prescreen', {
    date: params.date || undefined,
    top_sector: params.top_sector,
    top_stock: params.top_stock,
  })
}

/** 读取 watchlist（盘前筛选结果），默认最近一次 */
export function getWatchlist(date?: string): Promise<WatchlistPayload> {
  return http.get('/api/watchlist', { params: date ? { date } : {} })
}
