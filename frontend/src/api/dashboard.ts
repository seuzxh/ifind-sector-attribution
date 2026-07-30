/** 看板数据接口（realtime / custom / auction） */
import http from './client'
import type { DashboardPayload, MemberStock } from './types'

export interface DashboardParams {
  trade_date?: string
  snapshot_time?: string
  top_n?: number
}

export interface DashboardMemberParams {
  concept_code: string
  trade_date?: string
  snapshot_time?: string
  custom_mode?: boolean
  sort_key: 'change_ratio' | 'speed' | 'acceleration' | 'body' | 'score'
  descending: boolean
}

export interface DashboardMemberPayload {
  concept_code: string
  sort_key: DashboardMemberParams['sort_key']
  descending: boolean
  member_count: number
  members: MemberStock[]
  snapshot_time?: string
  error?: string
}

/** 板块强度看板 */
export function getRealtimeDashboard(params: DashboardParams = {}): Promise<DashboardPayload> {
  return http.get('/api/realtime/dashboard', { params })
}

/** 自选分组看板 */
export function getCustomDashboard(params: DashboardParams = {}): Promise<DashboardPayload> {
  return http.get('/api/custom/dashboard', { params })
}

/** 单个板块的全部有效成员排序，接口仅返回排序后的前 10 支。 */
export function getDashboardMembers(params: DashboardMemberParams): Promise<DashboardMemberPayload> {
  return http.get('/api/dashboard/members', { params })
}

/** 集合竞价看板 */
export function getAuctionDashboard(params: { trade_date?: string } = {}): Promise<DashboardPayload> {
  return http.get('/api/auction/dashboard', { params })
}

/** 历史看板（收盘数据，mode=history） */
export function getHistoryDashboard(
  date: string,
  topN = 10,
  forceCalc = false,
  scope: 'sector' | 'custom' = 'sector',
): Promise<DashboardPayload> {
  return http.get('/api/history/dashboard', { params: { date, top_n: topN, force_calc: forceCalc, scope } })
}
