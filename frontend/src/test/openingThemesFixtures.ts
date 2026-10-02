import type { OpeningContribution, OpeningDashboardPayload, OpeningTheme } from '@/api/openingThemes'

export const contribution: OpeningContribution = {
  stock_code: '600001.SH', stock_name: '甲股份', attribution_weight: 0.5,
  confidence: 0.9, reason_codes: ['MULTI_POOL_SUPPORT'], source_pool_ids: ['883910.TI'],
  quote_time: '09:32', pre_close: 100, last_price: 103, avg_price: 102, turnover: 2000,
  return_pct: 3, contribution: 1.5, positive_contribution: 1.5, has_quote: true,
}
export const theme: OpeningTheme = {
  theme_code: '885001.TI', theme_name: '半导体', theme_type: 'CONCEPT',
  level: 2.35, momentum_1m: 0.25, up_ratio: 0.5, breadth_delta_1m: 0.1,
  attributed_stock_count: 3, valid_quote_count: 2, supporting_count: 1,
  support_weight: 0.5, source_pool_diversity: 1, top1_concentration: 0.8,
  top3_concentration: 1, data_health: 2 / 3, risk_tags: ['低支撑', '高度集中'],
  contributors: [contribution,
    { ...contribution, stock_code: '600002.SH', stock_name: '乙股份', return_pct: -2, contribution: -1, positive_contribution: 0 },
    { ...contribution, stock_code: '600003.SH', stock_name: '丙股份', quote_time: null,
      pre_close: null, last_price: null, avg_price: null, turnover: null, return_pct: null,
      contribution: null, positive_contribution: null, has_quote: false },
  ],
}
export function dashboard(overrides: Partial<OpeningDashboardPayload> = {}): OpeningDashboardPayload {
  return {
    trade_date: '20261003', run_id: 'frozen-123', mode: 'realtime', snapshot_time: '09:32',
    latest_time: '09:32', available_times: ['09:30', '09:31', '09:32'], candidate_count: 3,
    theme_count: 2, data_health: 1, cache_status: 'fresh', generated_at: '2026-10-03T09:32:00+08:00',
    themes: [theme, { ...theme, theme_code: '885002.TI', theme_name: '机器人', level: 1, momentum_1m: 0.5 }],
    acceleration_theme_codes: ['885002.TI', '885001.TI'], breadth_theme_codes: ['885001.TI'],
    ...overrides,
  }
}
