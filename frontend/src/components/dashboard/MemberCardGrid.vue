<template>
  <div class="members-grid" v-if="sectors.length">
    <div v-for="(s, i) in sectors" :key="s.concept_code"
         class="sector-card"
         :class="{ 'has-holding': isCustom && (s.holding_in_group?.length || 0) > 0 }"
         :data-code="s.concept_code"
         :ref="el => registerCardRef(s.concept_code, el as HTMLElement | null)">
      <!-- 卡片头：颜色按 tab 区分 -->
      <div class="card-head" :style="{ background: headerColor(s) }">
        <span class="head-title">
          <span class="num">{{ i + 1 }}</span>{{ s.concept_name }}
        </span>
        <span class="meta">评分 {{ fmt(s.score) }} · S1 {{ fmtPct(s.s1_return) }} · S2 {{ (s.s2_breadth * 100).toFixed(0) }}%</span>
      </div>
      <!-- 成分股表格 -->
      <table class="card-table">
        <thead>
          <tr>
            <th class="left">代码</th>
            <th class="left name-th">名称</th>
            <th :class="{ sorted: cardSortKey(s.concept_code) === 'change_ratio' }"
                :data-order="cardSortKey(s.concept_code) === 'change_ratio' ? cardSortOrder(s.concept_code) : ''"
                @click="sortCard(s.concept_code, 'change_ratio')">涨幅</th>
            <th :class="{ sorted: cardSortKey(s.concept_code) === 'speed' }"
                :data-order="cardSortKey(s.concept_code) === 'speed' ? cardSortOrder(s.concept_code) : ''"
                @click="sortCard(s.concept_code, 'speed')">涨速</th>
            <th title="涨速加速 = 当前涨速 - 上一周期涨速"
                :class="{ sorted: cardSortKey(s.concept_code) === 'acceleration' }"
                :data-order="cardSortKey(s.concept_code) === 'acceleration' ? cardSortOrder(s.concept_code) : ''"
                @click="sortCard(s.concept_code, 'acceleration')">加速</th>
            <th title="开盘至今涨幅"
                :class="{ sorted: cardSortKey(s.concept_code) === 'body' }"
                :data-order="cardSortKey(s.concept_code) === 'body' ? cardSortOrder(s.concept_code) : ''"
                @click="sortCard(s.concept_code, 'body')">开盘至今</th>
            <th :class="{ sorted: cardSortKey(s.concept_code) === 'score' }"
                :data-order="cardSortKey(s.concept_code) === 'score' ? cardSortOrder(s.concept_code) : ''"
                @click="sortCard(s.concept_code, 'score')">综合分</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in sortedMembers(s)" :key="m.code"
              :class="{ 'is-holding': isHolding(s, m.code) }">
            <td class="left">{{ m.code }}</td>
            <td class="left stock-name" :title="m.name">{{ m.name }}</td>
            <td :class="changeCls(m.change_ratio)">
              {{ fmt(m.change_ratio) }}%
              <span v-if="m.limit" class="limit-tag">涨停</span>
              <span v-if="isHolding(s, m.code)" class="holding-tag">持仓</span>
            </td>
            <td>{{ m.speed ? fmt(m.speed) + '%' : '-' }}</td>
            <td :class="accelText(m.acceleration).cls">{{ accelText(m.acceleration).text }}</td>
            <td>{{ m.body !== null && m.body !== undefined ? fmt(m.body) + '%' : '-' }}</td>
            <td><b>{{ fmt(m.score) }}</b></td>
          </tr>
          <tr v-if="!s.members_top10 || s.members_top10.length === 0">
            <td colspan="7" class="empty">无数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div v-else class="empty-msg">无数据</div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import type { SectorEntry, MemberStock } from '@/api/types'
import { fmt, fmtPct, changeCls, accelText } from '@/utils/format'

const props = defineProps<{
  sectors: SectorEntry[]
  tab: 'top' | 'bottom' | 'zt'
  isCustom?: boolean
  highlightCode?: string
}>()

// 卡片排序状态：{ [concept_code]: { key, order } }
const cardSortState = reactive<Record<string, { key: string; order: '↓' | '↑' }>>({})

function cardSortKey(code: string): string {
  return cardSortState[code]?.key || 'change_ratio'
}
function cardSortOrder(code: string): '↓' | '↑' {
  return cardSortState[code]?.order || '↓'
}

function headerColor(s: SectorEntry): string {
  // top: 强=红 弱=橙；bottom/zt: 负=绿 中性=灰
  if (props.tab === 'top') return s.score >= 0 ? '#ef4444' : '#f59e0b'
  return s.score < 0 ? '#10b981' : '#6b7280'
}

function isHolding(s: SectorEntry, code: string): boolean {
  return !!(props.isCustom && s.holding_in_group && s.holding_in_group.includes(code))
}

function memberVal(m: MemberStock, key: string): number {
  let v: number | null | undefined
  if (key === 'change_ratio') v = m.change_ratio
  else if (key === 'speed') v = m.speed
  else if (key === 'acceleration') v = m.acceleration
  else if (key === 'body') v = m.body
  else v = m.score
  if (v === null || v === undefined || isNaN(v)) return 0
  return v
}

function sortedMembers(s: SectorEntry): MemberStock[] {
  const members = s.members_top10 || []
  if (!members.length) return members
  const st = cardSortState[s.concept_code] || { key: 'change_ratio', order: '↓' }
  const desc = st.order === '↓'
  return [...members].sort((a, b) => {
    const av = memberVal(a, st.key)
    const bv = memberVal(b, st.key)
    return desc ? bv - av : av - bv
  })
}

function sortCard(code: string, key: string) {
  const cur = cardSortState[code]
  if (cur && cur.key === key) {
    cur.order = cur.order === '↓' ? '↑' : '↓'
  } else {
    cardSortState[code] = { key, order: '↓' }
  }
}

// 高亮卡片（点击排行表行时滚动到对应卡片）
const cardRefs: Record<string, HTMLElement> = {}
function registerCardRef(code: string, el: HTMLElement | null) {
  if (el) cardRefs[code] = el
  else delete cardRefs[code]
}
function highlight(code: string) {
  Object.values(cardRefs).forEach(el => { el.style.outline = 'none' })
  const el = cardRefs[code]
  if (el) {
    el.style.outline = '2px solid #3b82f6'
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}
defineExpose({ highlight })
</script>

<style scoped>
.members-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px;
}
.sector-card {
  border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden;
  transition: box-shadow 0.15s; background: #fff;
}
.sector-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
.sector-card.has-holding { border: 2px solid #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,0.15); }
.card-head {
  padding: 8px 12px; font-size: 13px; font-weight: 600; color: #fff;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.head-title { display: flex; align-items: center; gap: 6px; }
.num {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 50%; background: rgba(255,255,255,0.25); font-size: 11px;
}
.meta { font-size: 11px; opacity: 0.9; font-weight: normal; }
.card-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.card-table th, .card-table td {
  padding: 6px 8px; text-align: right; border-bottom: 1px solid #f3f4f6; white-space: nowrap;
}
.card-table th {
  background: #f9fafb; color: #6b7280; font-weight: 600; cursor: pointer;
}
.card-table th:hover { background: #f3f4f6; }
.card-table th.left { text-align: left; cursor: default; }
.card-table th.sorted::after { content: attr(data-order); color: #3b82f6; margin-left: 3px; }
.card-table td.left { text-align: left; }
.up { color: #ef4444; }
.down { color: #10b981; }
.stock-name { color: #374151; max-width: 70px; overflow: hidden; text-overflow: ellipsis; }
.name-th { max-width: 70px; }
.limit-tag { padding: 0 5px; border-radius: 3px; font-size: 10px; background: #dc2626; color: #fff; margin-left: 3px; }
.holding-tag { padding: 0 5px; border-radius: 3px; font-size: 10px; background: #f59e0b; color: #fff; margin-left: 3px; font-weight: 600; }
.card-table tbody tr.is-holding { background: #fef9c3 !important; }
.card-table tbody tr.is-holding td:first-child { border-left: 3px solid #f59e0b; }
.empty { text-align: center; color: #9ca3af; padding: 16px; }
.empty-msg { text-align: center; color: #9ca3af; padding: 24px; }
</style>
