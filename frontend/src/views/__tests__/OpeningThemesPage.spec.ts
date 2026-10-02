import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OpeningThemesPage from '../OpeningThemesPage.vue'
import { getOpeningThemesDashboard } from '@/api/openingThemes'
import type { OpeningDashboardPayload } from '@/api/openingThemes'
import { dashboard, theme } from '@/test/openingThemesFixtures'
import router from '@/router'
import AppLayout from '@/layouts/AppLayout.vue'

vi.mock('@/api/openingThemes', () => ({ getOpeningThemesDashboard: vi.fn() }))
const getDashboard = vi.mocked(getOpeningThemesDashboard)
const deferred = () => {
  let resolve!: (value: OpeningDashboardPayload) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<OpeningDashboardPayload>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const failure = (code: string) => ({ response: { data: { error: { code, message: '服务提示', retryable: true } } } })

describe('opening dashboard page', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // UTC is still October 2; Shanghai is October 3.
    vi.setSystemTime(new Date('2026-10-02T17:32:00Z'))
    getDashboard.mockReset().mockImplementation(async (params) => dashboard({ snapshot_time: params.snapshot_time || '09:32' }))
  })
  afterEach(() => { vi.useRealTimers() })

  it('fixes realtime date to Shanghai today; enables history and requests the selected date', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003' })
    expect(wrapper.get('input[type=date]').attributes('disabled')).toBeDefined()
    expect((wrapper.get('input[type=date]').element as HTMLInputElement).value).toBe('2026-10-03')
    await wrapper.get('select[aria-label="模式"]').setValue('historical')
    expect(wrapper.get('input[type=date]').attributes('disabled')).toBeUndefined()
    await wrapper.get('input[type=date]').setValue('2026-09-30')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20260930' })
    getDashboard.mockClear()
    await vi.advanceTimersByTimeAsync(9000)
    expect(getDashboard).not.toHaveBeenCalled()
    await wrapper.get('select[aria-label="模式"]').setValue('realtime')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003' })
  })
  it('polls every three seconds and dragging stops follow before releasing the slider', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2999)
    expect(getDashboard).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(getDashboard).toHaveBeenCalledTimes(2)
    const slider = wrapper.get('input[type=range]')
    ;(slider.element as HTMLInputElement).value = '0'
    await slider.trigger('input')
    await vi.advanceTimersByTimeAsync(6000)
    expect(getDashboard).toHaveBeenCalledTimes(2)
    await slider.trigger('change')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003', snapshot_time: '09:30' })
    await wrapper.get('.jump-btn').trigger('click')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003' })
    await vi.advanceTimersByTimeAsync(3000)
    expect(getDashboard).toHaveBeenCalledTimes(5)
  })
  it('plays each minute from the start, changes speed, and stops at the end', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    getDashboard.mockClear()
    await wrapper.get('.play-btn').trigger('click')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003', snapshot_time: '09:30' })
    await vi.advanceTimersByTimeAsync(800)
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003', snapshot_time: '09:31' })
    await wrapper.get('.speed-sel').setValue('400')
    await vi.advanceTimersByTimeAsync(400)
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261003', snapshot_time: '09:32' })
    await vi.advanceTimersByTimeAsync(4000)
    expect(getDashboard).toHaveBeenCalledTimes(3)
    expect(wrapper.get('.play-btn').text()).toContain('播放')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('replays history using the selected date', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    await wrapper.get('select[aria-label="模式"]').setValue('historical')
    await wrapper.get('input[type=date]').setValue('2026-09-30')
    await flushPromises()
    await wrapper.get('.play-btn').trigger('click')
    await flushPromises()
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20260930', snapshot_time: '09:30' })
  })
  it('clearing a history date cancels the loading state of an older pending request', async () => {
    const pending = deferred()
    getDashboard.mockReturnValue(pending.promise)
    const wrapper = mount(OpeningThemesPage)
    await wrapper.get('select[aria-label="模式"]').setValue('historical')
    await wrapper.get('input[type=date]').setValue('')
    expect(wrapper.get('[role=status]').text()).toContain('请求失败')
    expect(wrapper.get('[role=status]').text()).not.toContain('加载中')
    expect(wrapper.attributes('aria-busy')).toBe('false')
    pending.resolve(dashboard())
    await flushPromises()
    expect(wrapper.text()).not.toContain('半导体')
  })
  it.each(['resolve', 'reject'] as const)('discards a stale async %s after changing date', async (settle) => {
    const old = deferred()
    getDashboard.mockReturnValueOnce(old.promise)
    const wrapper = mount(OpeningThemesPage)
    expect(wrapper.get('[role=status]').text()).toContain('加载中')
    await wrapper.get('select[aria-label="模式"]').setValue('historical')
    await wrapper.get('input[type=date]').setValue('2026-09-30')
    await flushPromises()
    if (settle === 'resolve') old.resolve(dashboard({ themes: [{ ...theme, theme_name: '过期结果' }] }))
    else old.reject(failure('SNAPSHOT_NOT_FOUND'))
    await flushPromises()
    expect(wrapper.text()).toContain('半导体')
    expect(wrapper.text()).not.toContain('过期结果')
    expect(wrapper.text()).not.toContain('opening-premarket')
  })
  it('shares selection across lists and contributors, retains it, then falls back when removed', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    await wrapper.get('[aria-label="加速榜"] button').trigger('click')
    expect(wrapper.get('[aria-label="贡献个股"] h2').text()).toContain('机器人')
    expect(wrapper.findAll('[aria-label="题材强弱榜"] tbody tr')[1]!.attributes('aria-selected')).toBe('true')
    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.get('[aria-label="贡献个股"] h2').text()).toContain('机器人')
    getDashboard.mockResolvedValue(dashboard({ themes: [theme] }))
    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.get('[aria-label="贡献个股"] h2').text()).toContain('半导体')
  })
  it.each([
    ['SNAPSHOT_NOT_FOUND', '盘前冻结快照'], ['QUOTE_DATA_UNAVAILABLE', '行情暂不可用'],
    ['QUOTE_PROVIDER_FAILED', '行情暂不可用'], ['INVALID_REQUEST', '请求失败'], ['NETWORK', '请求失败'],
  ])('shows the %s state and only offers a manual command for missing snapshots', async (code, label) => {
    getDashboard.mockRejectedValue(code === 'NETWORK' ? new Error('Network Error') : failure(code!))
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    expect(wrapper.get('[role=status]').text()).toContain(label)
    expect(wrapper.find('code').exists()).toBe(code === 'SNAPSHOT_NOT_FOUND')
    if (code === 'SNAPSHOT_NOT_FOUND') expect(wrapper.get('code').text()).toBe('python main.py opening-premarket --date 20261003')
    expect(wrapper.findAll('button').some(button => button.text().includes('计算'))).toBe(false)
  })
  it.each([{ data_health: 0.5 }, { cache_status: 'stale' as const }])('shows degraded data without hiding rankings: %j', async (partial) => {
    getDashboard.mockResolvedValue(dashboard(partial))
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    expect(wrapper.get('[role=status]').text()).toContain('部分数据降级')
    expect(wrapper.text()).toContain('半导体')
  })
  it('resets the realtime date and minute when Shanghai crosses midnight', async () => {
    const wrapper = mount(OpeningThemesPage)
    await flushPromises()
    vi.setSystemTime(new Date('2026-10-03T16:00:00Z'))
    await vi.advanceTimersByTimeAsync(3000)
    expect(getDashboard).toHaveBeenLastCalledWith({ trade_date: '20261004' })
    expect((wrapper.get('input[type=date]').element as HTMLInputElement).value).toBe('2026-10-04')
  })
  it('stops polling and playback while cached, invalidates pending requests, and activates once', async () => {
    const shown = ref(true)
    const wrapper = mount(defineComponent({ setup: () => () => h(KeepAlive, null, {
      default: () => shown.value ? h(OpeningThemesPage) : null,
    }) }))
    await flushPromises()
    expect(getDashboard).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)
    shown.value = false
    await nextTick()
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(6000)
    expect(getDashboard).toHaveBeenCalledTimes(1)
    shown.value = true
    await nextTick(); await flushPromises()
    expect(getDashboard).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(1)
    const pending = deferred()
    getDashboard.mockReturnValueOnce(pending.promise)
    await wrapper.get('.play-btn').trigger('click')
    expect(vi.getTimerCount()).toBe(1)
    shown.value = false
    await nextTick()
    expect(vi.getTimerCount()).toBe(0)
    pending.resolve(dashboard({ themes: [{ ...theme, theme_name: '隐藏响应' }] }))
    await flushPromises()
    shown.value = true
    await nextTick(); await flushPromises()
    expect(getDashboard).toHaveBeenCalledTimes(4)
    expect(wrapper.text()).not.toContain('隐藏响应')
    expect(wrapper.get('.play-btn').text()).toContain('播放')
    expect(vi.getTimerCount()).toBe(0)
    await wrapper.get('.jump-btn').trigger('click')
    await flushPromises()
    expect(vi.getTimerCount()).toBe(1)
    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('registers direct navigation without changing existing route names', async () => {
    await router.push('/opening-themes')
    expect(router.currentRoute.value.name).toBe('opening-themes')
    expect(router.currentRoute.value.meta.title).toBe('开盘题材')
    expect(window.location.hash).toBe('#/opening-themes')
    expect(router.getRoutes().map(route => route.name)).toEqual(expect.arrayContaining([
      'sector', 'custom', 'auction', 'scan', 'market_scan', 'sector_manage', 'kg',
    ]))
    const layout = mount(AppLayout, { global: { plugins: [router], stubs: { RouterView: true } } })
    expect(layout.findAll('.tab-title').map(tab => tab.text())).toEqual([
      '板块强度监控', '开盘题材', '自选分组监控', '集合竞价', '自选强势归类', '全市场强势归类', '监控板块管理', '知识图谱',
    ])
  })
})
