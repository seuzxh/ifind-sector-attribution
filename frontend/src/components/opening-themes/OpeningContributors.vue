<template>
  <section class="contributors" aria-label="贡献个股">
    <h2>贡献个股 <span v-if="theme">{{ theme.theme_name }} · {{ theme.contributors.length }} 只</span></h2>
    <div v-if="theme?.contributors.length" class="table-scroll"><table>
      <thead><tr><th>股票</th><th>涨幅</th><th>归因权重</th><th title="归因权重 × 股票涨幅">贡献值</th><th>来源池</th><th>归因置信度</th><th>归因原因</th><th>行情时点</th></tr></thead>
      <tbody><tr v-for="stock in theme.contributors" :key="stock.stock_code">
        <th scope="row">{{ stock.stock_name }}<small>{{ stock.stock_code }}</small></th>
        <td :class="changeCls(stock.return_pct)">{{ stock.has_quote ? signed(stock.return_pct, '%') : '暂无行情' }}</td>
        <td>{{ ratio(stock.attribution_weight) }}</td><td :class="changeCls(stock.contribution)">{{ stock.has_quote ? signed(stock.contribution) : '—' }}</td>
        <td><span v-for="pool in stock.source_pool_ids" :key="pool" class="tag" :title="pool">{{ pools[pool] || pool }}</span></td>
        <td>{{ ratio(stock.confidence) }}</td><td><span v-for="reason in stock.reason_codes" :key="reason" class="tag" :title="reason">{{ reasons[reason] || reason }}</span><span v-if="!stock.reason_codes.length">—</span></td>
        <td>{{ stock.quote_time || '—' }}</td>
      </tr></tbody>
    </table></div>
    <p v-else>选择题材查看贡献个股</p>
  </section>
</template>
<script setup lang="ts">
import type { OpeningTheme } from '@/api/openingThemes'
import { fmt, changeCls } from '@/utils/format'
defineProps<{ theme?: OpeningTheme }>()
const pools: Record<string, string> = {
  '883926.TI': '高贝塔值', high_beta: '高贝塔值', '883409.TI': '近期强势', recent_strong: '近期强势', '883910.TI': '同花顺热股', hot_stock: '同花顺热股',
}
const reasons: Record<string, string> = {
  PEER_CONFIRMATION: '同题材个股印证', LOW_SUPPORT: '同题材支撑偏少', STRONG_RECENT_THEME: '题材近期强势',
  MULTI_POOL_SUPPORT: '多源股池支持', HIGH_SYNCHRONY: '走势同步性高', MISSING_HISTORY: '历史数据不足', LOW_CONFIDENCE: '归因置信度偏低',
}
function ratio(value: number) { return `${(value * 100).toFixed(2)}%` }
function signed(value: number | null, unit = '') { return value === null ? '—' : fmt(value) + unit }
</script>
<style scoped>
.contributors { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px #0000000f; min-width: 0; }
h2 { font-size: 14px; margin-bottom: 12px; } h2 span { color: #1e40af; margin-left: 12px; font-size: 12px; }
.table-scroll { overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 12px; white-space: nowrap; }
th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e5e7eb; } thead { background: #f9fafb; }
small { display: block; color: #6b7280; font-size: 10px; font-weight: normal; margin-top: 4px; }
.tag { display: inline-block; margin: 2px; padding: 2px 5px; border-radius: 4px; background: #f3f4f6; color: #4b5563; }
p { padding: 24px; color: #6b7280; text-align: center; }
</style>
