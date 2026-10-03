import http from './client'

export interface OpeningContribution {
  stock_code: string
  stock_name: string
  attribution_weight: number
  confidence: number
  reason_codes: string[]
  source_pool_ids: string[]
  quote_time: string | null
  pre_close: number | null
  last_price: number | null
  avg_price: number | null
  turnover: number | null
  return_pct: number | null
  contribution: number | null
  positive_contribution: number | null
  has_quote: boolean
}

export interface OpeningTheme {
  theme_code: string
  theme_name: string
  theme_type: 'INDUSTRY' | 'CONCEPT'
  level: number | null
  momentum_1m: number | null
  up_ratio: number | null
  breadth_delta_1m: number | null
  attributed_stock_count: number
  valid_quote_count: number
  supporting_count: number
  support_weight: number | null
  source_pool_diversity: number
  top1_concentration: number | null
  top3_concentration: number | null
  data_health: number
  risk_tags: string[]
  contributors: OpeningContribution[]
}

export interface OpeningDashboardPayload {
  trade_date: string
  run_id: string
  mode: 'realtime' | 'historical'
  snapshot_time: string
  latest_time: string
  available_times: string[]
  candidate_count: number
  theme_count: number
  data_health: number
  themes: OpeningTheme[]
  acceleration_theme_codes: string[]
  breadth_theme_codes: string[]
  generated_at: string
  cache_status: 'fresh' | 'hit' | 'stale'
}

export interface OpeningDashboardErrorPayload {
  error: {
    code: 'SNAPSHOT_NOT_FOUND' | 'INVALID_REQUEST' | 'QUOTE_DATA_UNAVAILABLE' | 'QUOTE_PROVIDER_FAILED'
    message: string
    retryable: boolean
  }
}

/** Shared client unwraps JSON; server failures retain response.data as OpeningDashboardErrorPayload. */
export function getOpeningThemesDashboard(params: {
  trade_date: string
  snapshot_time?: string
  fallback_to_previous?: boolean
}): Promise<OpeningDashboardPayload> {
  return http.get<OpeningDashboardPayload, OpeningDashboardPayload>('/api/opening-strength/dashboard', { params })
}
