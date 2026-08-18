/** 知识图谱接口与类型（P3：查询 + cytoscape 图供数） */
import http from './client'

/** cytoscape 元素格式 */
export interface CytoNode { data: Record<string, unknown> }
export interface CytoEdge { data: Record<string, unknown> }
export interface CytoElements { nodes: CytoNode[]; edges: CytoEdge[] }

/** 个股归属板块 */
export interface StockSectorEdge {
  edge_id: number
  confidence: number
  corr_20d: number | null
  valid_from: string
  sector_code: string
  sector_name: string
  sector_type: string | null
}

/** 板块成分股 */
export interface SectorStockEdge {
  edge_id: number
  confidence: number
  corr_20d: number | null
  stock_code: string
  stock_name: string | null
}

/** 联动股 */
export interface LinkedStock {
  code: string
  name: string
  shared: number
  score: number
  via: { sector: string; corr: number | null }[]
}

/** 个股 → 归属板块（按|ρ|降序） */
export function getKgStockSectors(code: string): Promise<{ code: string; count: number; sectors: StockSectorEdge[] }> {
  return http.get(`/api/kg/stock/${code}/sectors`)
}

/** 板块 → 成分股 */
export function getKgSectorStocks(code: string, order: 'corr' | 'code' = 'corr'): Promise<{ code: string; count: number; stocks: SectorStockEdge[] }> {
  return http.get(`/api/kg/sector/${code}/stocks`, { params: { order } })
}

/** 联动股 TopN */
export function getKgLinked(code: string, topN = 10): Promise<{ code: string; count: number; linked: LinkedStock[] }> {
  return http.get(`/api/kg/linked/${code}`, { params: { top_n: topN } })
}

/** 族群 */
export function getKgCommunities(): Promise<{
  count: number
  communities: { community_id: number; size: number; members: { node_id: string; name: string; type: string }[] }[]
}> {
  return http.get('/api/kg/communities')
}

/** 板块投影图（族群着色 + Jaccard 边） */
export function getKgProjection(minJaccard = 0.3): Promise<{ node_count: number; edge_count: number; elements: CytoElements }> {
  return http.get('/api/kg/graph/projection', { params: { min_jaccard: minJaccard } })
}

/** 个股星型图 */
export function getKgStar(code: string, limit = 20): Promise<{ code: string; elements: CytoElements }> {
  return http.get('/api/kg/graph/star', { params: { code, limit } })
}

/** 板块展开图 */
export function getKgSectorGraph(code: string, limit = 30): Promise<{ code: string; total_members: number; shown: number; elements: CytoElements }> {
  return http.get(`/api/kg/graph/sector/${code}`, { params: { limit } })
}

/** 组合定位：板块行（hits 命中数 / lift 富集倍数） */
export interface LocatedSector {
  sector_code: string
  sector_name: string
  sector_type: string | null
  hits: number
  group_ratio: number
  members: number
  lift: number | null
  avg_corr: number | null
}

export interface LocateResult {
  source: string
  total: number
  matched: number
  unresolved: string[]
  sectors: LocatedSector[]
}

/** 一批股票 → 共同指向的板块（codes 逗号分隔，或 group=自选分组名） */
export function locateKgSectors(params: {
  codes?: string
  group?: string
  min_hits?: number
  top_n?: number
  order?: 'lift' | 'hits'
}): Promise<LocateResult> {
  return http.get('/api/kg/locate', { params })
}

/** 自选分组列表（组合定位下拉用） */
export function getKgLocateGroups(): Promise<{ groups: { id: string; name: string; count: number }[] }> {
  return http.get('/api/kg/locate/groups')
}
