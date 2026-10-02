import { AxiosError, type AxiosAdapter } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import http from '@/api/client'
import {
  getOpeningThemesDashboard,
  type OpeningContribution,
  type OpeningDashboardErrorPayload,
  type OpeningDashboardPayload,
  type OpeningTheme,
} from '@/api/openingThemes'

const contributor: OpeningContribution = {
  stock_code: '600001.SH', stock_name: '甲', attribution_weight: 1,
  confidence: 0.9, reason_codes: ['MULTI_POOL_SUPPORT'], source_pool_ids: ['883910.TI'],
  quote_time: '09:32', pre_close: 100, last_price: 103, avg_price: 102, turnover: 2000,
  return_pct: 3, contribution: 3, positive_contribution: 3, has_quote: true,
}

const missingContributor: OpeningContribution = {
  stock_code: '000001.SZ', stock_name: '乙', attribution_weight: 1,
  confidence: 0.8, reason_codes: [], source_pool_ids: ['883409.TI'],
  quote_time: null, pre_close: null, last_price: null, avg_price: null, turnover: null,
  return_pct: null, contribution: null, positive_contribution: null, has_quote: false,
}

const theme: OpeningTheme = {
  theme_code: '885001.TI', theme_name: '冻结题材', theme_type: 'CONCEPT',
  level: 3, momentum_1m: 2, up_ratio: 1, breadth_delta_1m: 0,
  attributed_stock_count: 1, valid_quote_count: 1, supporting_count: 1,
  support_weight: 1, source_pool_diversity: 1, top1_concentration: 1,
  top3_concentration: 1, data_health: 1, risk_tags: ['单股驱动', '高度集中'],
  contributors: [contributor],
}

const missingTheme: OpeningTheme = {
  theme_code: '884001.TI', theme_name: '无行情行业', theme_type: 'INDUSTRY',
  level: null, momentum_1m: null, up_ratio: null, breadth_delta_1m: null,
  attributed_stock_count: 1, valid_quote_count: 0, supporting_count: 0,
  support_weight: null, source_pool_diversity: 0, top1_concentration: null,
  top3_concentration: null, data_health: 0, risk_tags: ['单股驱动', '数据不足'],
  contributors: [missingContributor],
}

const payload: OpeningDashboardPayload = {
  trade_date: '20260930', run_id: 'frozen-api', mode: 'historical',
  snapshot_time: '09:32', latest_time: '09:32', available_times: ['09:30', '09:32'],
  candidate_count: 2, theme_count: 2, data_health: 0.5, cache_status: 'fresh',
  acceleration_theme_codes: [], breadth_theme_codes: [], themes: [theme, missingTheme],
  generated_at: '2026-10-03T08:00:00+08:00',
}

describe('opening dashboard API through the shared Axios client', () => {
  const originalAdapter = http.defaults.adapter
  let adapter: ReturnType<typeof vi.fn<AxiosAdapter>>

  beforeEach(() => {
    adapter = vi.fn<AxiosAdapter>()
    http.defaults.adapter = adapter
  })

  afterEach(() => {
    http.defaults.adapter = originalAdapter
  })

  it('sends the exact endpoint and date/minute query and returns the unwrapped typed payload', async () => {
    adapter.mockImplementation(async (config) => ({
      config, data: payload, status: 200, statusText: 'OK', headers: {},
    }))

    const result: OpeningDashboardPayload = await getOpeningThemesDashboard({
      trade_date: '20260930', snapshot_time: '09:32',
    })

    expect(adapter).toHaveBeenCalledOnce()
    const config = adapter.mock.calls[0]![0]
    expect(config.method).toBe('get')
    expect(config.url).toBe('/api/opening-strength/dashboard')
    expect(config.params).toEqual({ trade_date: '20260930', snapshot_time: '09:32' })
    expect(result).toEqual(payload)
    expect(result.themes[1]!.level).toBeNull()
    expect(result.themes[1]!.contributors[0]!.quote_time).toBeNull()
  })

  it('omits the optional minute and accepts a frozen dashboard with no themes', async () => {
    const emptyPayload: OpeningDashboardPayload = {
      ...payload, candidate_count: 0, theme_count: 0, data_health: 0, themes: [],
    }
    adapter.mockImplementation(async (config) => ({
      config, data: emptyPayload, status: 200, statusText: 'OK', headers: {},
    }))

    const result = await getOpeningThemesDashboard({ trade_date: '20260930' })

    expect(adapter.mock.calls[0]![0].params).toEqual({ trade_date: '20260930' })
    expect(result).toEqual(emptyPayload)
  })

  it.each([
    { status: 404, code: 'SNAPSHOT_NOT_FOUND', message: '该日期尚未生成盘前冻结快照', retryable: false },
    { status: 422, code: 'INVALID_REQUEST', message: '日期须为 YYYYMMDD，时点须为不早于 09:30 的 HH:MM', retryable: false },
    { status: 503, code: 'QUOTE_DATA_UNAVAILABLE', message: '该时点暂无可用的盘中行情', retryable: true },
    { status: 503, code: 'QUOTE_PROVIDER_FAILED', message: '行情获取暂时失败，请稍后重试', retryable: true },
  ] as const)('preserves nested $code for the page error state', async ({ status, code, message, retryable }) => {
    const errorPayload: OpeningDashboardErrorPayload = { error: { code, message, retryable } }
    adapter.mockImplementation(async (config) => {
      throw new AxiosError(`Request failed with status code ${status}`, undefined, config, undefined, {
        config, data: errorPayload, status, statusText: 'Error', headers: {},
      })
    })

    await expect(getOpeningThemesDashboard({ trade_date: '20260930' })).rejects.toMatchObject({
      message: `Request failed with status code ${status}`,
      response: { status, data: { error: { code, message, retryable } } },
    })
  })

  it('preserves the legacy detail error message for existing dashboard endpoints', async () => {
    adapter.mockImplementation(async (config) => {
      throw new AxiosError('Request failed with status code 422', undefined, config, undefined, {
        config, data: { detail: '旧接口错误说明' }, status: 422, statusText: 'Error', headers: {},
      })
    })

    await expect(http.get('/api/realtime/dashboard')).rejects.toThrow('旧接口错误说明')
  })

  it('preserves transport errors when no server response is available', async () => {
    adapter.mockRejectedValue(new AxiosError('Network Error'))

    await expect(getOpeningThemesDashboard({ trade_date: '20260930' })).rejects.toThrow('Network Error')
  })
})
