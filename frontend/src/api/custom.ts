/** 自选分组相关（check_reload / dashboard / scan） */
import http from './client'

export interface ReloadResult {
  reloaded: boolean
  reason?: string
  group_count?: number
  stock_count?: number
  row_count?: number
}

/** 检查自选分组 JSON 是否变更，变了就全量重导（切到 custom/scan tab 时调） */
export function checkCustomReload(): Promise<ReloadResult> {
  return http.post('/api/custom/check_reload')
}
