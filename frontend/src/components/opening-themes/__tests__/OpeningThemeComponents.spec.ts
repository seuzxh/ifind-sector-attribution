import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import OpeningThemeTable from '../OpeningThemeTable.vue'
import OpeningChangeLists from '../OpeningChangeLists.vue'
import OpeningContributors from '../OpeningContributors.vue'
import { dashboard, theme } from '@/test/openingThemesFixtures'

describe('opening theme panels', () => {
  it('directly previews five pool stocks and expands all without losing missing quotes', async () => {
    const stocks = Array.from({ length: 7 }, (_, index) => ({
      ...theme.contributors[index % 3]!, stock_code: `60000${index}.SH`, stock_name: `个股${index}`,
    }))
    const wrapper = mount(OpeningContributors, { props: {
      theme: { ...theme, total_member_count: 300, contributors: stocks }, compact: true, rank: 1,
    } })
    expect(wrapper.text()).toContain('全量成分 300 只')
    expect(wrapper.text()).toContain('候选个股池 7 只')
    expect(wrapper.findAll('tbody tr')).toHaveLength(5)
    await wrapper.get('button.expand-pool').trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(7)
    expect(wrapper.text()).toContain('暂无行情')
    await wrapper.get('button.expand-pool').trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(5)
  })
  it('renders metrics, missing values and all risks; selects by click and keyboard', async () => {
    const wrapper = mount(OpeningThemeTable, { props: {
      themes: [{ ...theme, risk_tags: ['单股驱动', '低支撑', '高度集中', '数据不足', '行情滞后'] },
        { ...theme, theme_code: 'missing', level: null }], selectedCode: theme.theme_code,
    } })
    for (const label of ['+2.35%', '+0.25', '1 / 2', '+10.00', '80.00%', '66.67%',
      '单股驱动', '低支撑', '高度集中', '数据不足', '行情滞后']) expect(wrapper.text()).toContain(label)
    const rows = wrapper.findAll('tbody tr')
    expect(rows[0]!.attributes('aria-selected')).toBe('true')
    expect(rows[1]!.text()).toContain('—')
    await rows[1]!.trigger('click')
    await rows[0]!.trigger('keydown', { key: 'Enter' })
    await rows[0]!.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('select')).toEqual([['missing'], [theme.theme_code], [theme.theme_code]])
  })
  it('sorts main metrics on request without changing API order by default', async () => {
    const wrapper = mount(OpeningThemeTable, { props: { themes: dashboard().themes, selectedCode: '' } })
    expect(wrapper.findAll('tbody tr')[0]!.text()).toContain('半导体')
    await wrapper.get('button[data-sort="momentum_1m"]').trigger('click')
    expect(wrapper.findAll('tbody tr')[0]!.text()).toContain('机器人')
  })
  it('uses API code order in each change list and shares selection', async () => {
    const payload = dashboard()
    const wrapper = mount(OpeningChangeLists, { props: {
      themes: payload.themes, accelerationCodes: payload.acceleration_theme_codes,
      breadthCodes: payload.breadth_theme_codes, selectedCode: '885002.TI',
    } })
    const acceleration = wrapper.get('[aria-label="加速榜"]').findAll('button')
    expect(acceleration.map(row => row.text())).toEqual([expect.stringContaining('机器人'), expect.stringContaining('半导体')])
    expect(acceleration[0]!.attributes('aria-pressed')).toBe('true')
    await acceleration[0]!.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['885002.TI']])
    expect(wrapper.get('[aria-label="扩散榜"]').text()).toContain('+10.00')
  })
  it('preserves contributor order, negative values and missing quote rows with provenance', () => {
    const wrapper = mount(OpeningContributors, { props: { theme } })
    const rows = wrapper.findAll('tbody tr')
    expect(rows.map(row => row.text())).toEqual([
      expect.stringContaining('甲股份'), expect.stringContaining('乙股份'), expect.stringContaining('丙股份'),
    ])
    for (const label of ['+3.00%', '+1.50', '50.00%', '90.00%', '同花顺热股', '多源股池支持']) expect(rows[0]!.text()).toContain(label)
    expect(rows[1]!.text()).toContain('-1.00')
    expect(rows[2]!.text()).toContain('暂无行情')
    expect(rows[2]!.text()).not.toContain('+3.00%')
  })
})
