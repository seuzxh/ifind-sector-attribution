/** 监控板块管理接口与类型 */
import http from './client'

/** 候选板块（管理列表一行） */
export interface SectorCandidate {
  concept_code: string
  concept_name: string
  level: string          // "三级行业" | "概念板块" | "其他"
  change_ratio: number | null   // 当日涨幅 %
  body: number | null           // 实体涨幅 %
  return_3d: number | null      // 3日累计涨幅 %
  return_5d: number | null      // 5日累计涨幅 %
  member_count: number          // 成分股数
  watched: boolean              // 是否已勾选监控
}

/** GET /api/sector_manage/list 返回 */
export interface SectorListPayload {
  date?: string
  total?: number
  watched_count?: number
  sectors: SectorCandidate[]
  error?: string
}

/** GET /api/sector_manage/watched 返回 */
export interface WatchedPayload {
  count: number
  concept_codes: string[]
}

/** POST /api/sector_manage/save 入参 */
export interface SaveWatchedPayload {
  concept_codes: string[]
}

/** 拉取候选板块列表（含指标 + 勾选状态） */
export function getSectorManageList(date?: string): Promise<SectorListPayload> {
  return http.get('/api/sector_manage/list', { params: date ? { date } : {} })
}

/** 读取当前勾选的板块代码列表 */
export function getWatchedSectors(): Promise<WatchedPayload> {
  return http.get('/api/sector_manage/watched')
}

/** 全量覆盖保存勾选清单 */
export function saveWatchedSectors(payload: SaveWatchedPayload): Promise<{ ok: boolean; saved_count: number }> {
  return http.post('/api/sector_manage/save', payload)
}

/** 刷新任务状态（后台线程执行，前端轮询） */
export interface RefreshStatus {
  running: boolean
  done: boolean
  error: string | null
  result: { dict_count?: number; member_concepts?: number; saved_records?: number; member_date?: string } | null
  started_at: string | null
  finished_at: string | null
}

/** 触发刷新（后台线程拉取最新板块字典+成分股，约1-2分钟） */
export function triggerRefresh(): Promise<{ ok: boolean; reason: string; status: RefreshStatus }> {
  return http.post('/api/sector_manage/refresh')
}

/** 查询刷新进度（前端轮询用） */
export function getRefreshStatus(): Promise<RefreshStatus> {
  return http.get('/api/sector_manage/refresh/status')
}
