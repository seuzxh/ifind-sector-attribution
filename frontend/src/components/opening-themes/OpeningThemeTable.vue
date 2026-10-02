<template>
  <section class="theme-panel">
    <h2>题材强弱榜 <span>选择题材查看贡献个股</span></h2>
    <div class="table-scroll">
      <table aria-label="题材强弱榜">
        <thead><tr><th>题材</th>
          <th v-for="column in columns" :key="column.key" :aria-sort="sortKey === column.key ? (descending ? 'descending' : 'ascending') : 'none'">
            <button :data-sort="column.key" :title="column.hint" @click="sort(column.key)">{{ column.label }}{{ sortKey === column.key ? (descending ? ' ↓' : ' ↑') : '' }}</button>
          </th><th title="有效行情股票数 ÷ 归因股票数">数据健康</th><th>风险提示</th>
        </tr></thead>
        <tbody><tr v-for="item in sortedThemes" :key="item.theme_code" tabindex="0"
          :aria-selected="selectedCode === item.theme_code" :class="{ selected: selectedCode === item.theme_code, subdued: item.data_health < 0.6 || item.attributed_stock_count === 1 || item.supporting_count < 2 }"
          @click="$emit('select', item.theme_code)" @keydown.enter.prevent="$emit('select', item.theme_code)" @keydown.space.prevent="$emit('select', item.theme_code)">
          <th scope="row">{{ item.theme_name }}<small>{{ item.theme_type === 'INDUSTRY' ? '行业' : '概念' }} · {{ item.theme_code }}</small></th>
          <td :class="changeCls(item.level)">{{ signed(item.level, '%') }}</td>
          <td :class="changeCls(item.momentum_1m)">{{ signed(item.momentum_1m, ' pp') }}</td>
          <td :title="`归因 ${item.attributed_stock_count} 只；上涨权重 ${ratio(item.support_weight)}；覆盖 ${item.source_pool_diversity} 个来源池`">{{ item.supporting_count }} / {{ item.valid_quote_count }}</td>
          <td :class="changeCls(item.breadth_delta_1m)">{{ signed(item.breadth_delta_1m === null ? null : item.breadth_delta_1m * 100, ' pp') }}</td>
          <td :title="`前三股集中度 ${ratio(item.top3_concentration)}`">{{ ratio(item.top1_concentration) }}</td>
          <td>{{ ratio(item.data_health) }}</td>
          <td><span v-for="tag in item.risk_tags" :key="tag" class="risk-tag">{{ tag }}</span><span v-if="!item.risk_tags.length">—</span></td>
        </tr></tbody>
      </table>
    </div>
    <p v-if="!themes.length" class="empty">暂无题材</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { OpeningTheme } from '@/api/openingThemes'
import { fmt, changeCls } from '@/utils/format'
const props = defineProps<{ themes: OpeningTheme[]; selectedCode: string }>()
defineEmits<{ select: [code: string] }>()
type SortKey = 'level' | 'momentum_1m' | 'supporting_count' | 'breadth_delta_1m' | 'top1_concentration'
const columns: { key: SortKey; label: string; hint: string }[] = [
  { key: 'level', label: '强度', hint: '按盘前归因权重加权的股票涨幅' },
  { key: 'momentum_1m', label: '1分钟加速', hint: '强度较上一分钟的变化，单位：百分点（pp）' },
  { key: 'supporting_count', label: '上涨 / 有效', hint: '上涨股票数 / 有效行情股票数' },
  { key: 'breadth_delta_1m', label: '扩散变化', hint: '上涨占比较上一分钟的变化，单位：百分点（pp）' },
  { key: 'top1_concentration', label: '首股集中度', hint: '最大正贡献占全部正贡献的比例' },
]
const sortKey = ref<SortKey | null>(null)
const descending = ref(true)
function sort(key: SortKey) { descending.value = sortKey.value === key ? !descending.value : true; sortKey.value = key }
const sortedThemes = computed(() => {
  const key = sortKey.value
  if (!key) return props.themes
  return [...props.themes].sort((a, b) => {
    const left = a[key], right = b[key]
    if (left === null) return right === null ? 0 : 1
    if (right === null) return -1
    return (descending.value ? right - left : left - right) || a.theme_code.localeCompare(b.theme_code)
  })
})
function signed(value: number | null, unit: string) { return value === null ? '—' : fmt(value) + unit }
function ratio(value: number | null) { return value === null ? '—' : (value * 100).toFixed(2) + '%' }
</script>

<style scoped>
.theme-panel { min-width: 0; background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px #0000000f; }
h2 { font-size: 14px; margin-bottom: 12px; } h2 span { font-size: 11px; font-weight: normal; color: #6b7280; margin-left: 8px; }
.table-scroll { overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 12px; white-space: nowrap; }
th, td { text-align: right; padding: 10px 8px; border-bottom: 1px solid #e5e7eb; } th:first-child { text-align: left; }
thead { background: #f9fafb; } th button { font: inherit; color: inherit; border: 0; background: none; cursor: pointer; }
small { display: block; color: #6b7280; font-size: 10px; font-weight: normal; margin-top: 4px; }
tbody tr { cursor: pointer; } tbody tr:hover, tbody tr.selected { background: #eff6ff; } tbody tr:focus-visible { outline: 2px solid #1e40af; outline-offset: -2px; }
.subdued th { color: #6b7280; } .risk-tag { display: inline-block; background: #fff7ed; color: #9a3412; border-radius: 4px; padding: 2px 5px; margin: 2px; }
.empty { padding: 24px; text-align: center; color: #6b7280; }
</style>
