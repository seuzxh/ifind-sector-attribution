/** 看板数据接口（realtime / custom / auction） */
import http from './client'
import type { DashboardPayload } from './types'

export interface DashboardParams {
  trade_date?: string
  snapshot_time?: string
  top_n?: number
  watchlist_mode?: boolean
}

/** 板块强度看板 */
export function getRealtimeDashboard(params: DashboardParams = {}): Promise<DashboardPayload> {
  return http.get('/api/realtime/dashboard', { params })
}

/** 自选分组看板 */
export function getCustomDashboard(params: DashboardParams = {}): Promise<DashboardPayload> {
  return http.get('/api/custom/dashboard', { params })
}

/** 集合竞价看板 */
export function getAuctionDashboard(params: { trade_date?: string } = {}): Promise<DashboardPayload> {
  return http.get('/api/auction/dashboard', { params })
}

/** 历史看板（收盘数据，mode=history） */
export function getHistoryDashboard(date: string, topN = 10, forceCalc = false): Promise<DashboardPayload> {
  return http.get('/api/history/dashboard', { params: { date, top_n: topN, force_calc: forceCalc } })
}
