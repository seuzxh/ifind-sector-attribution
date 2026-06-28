<template>
  <div class="rank-table-wrap">
    <table class="rank-table">
      <thead>
        <tr>
          <th class="center">#</th>
          <th class="left">板块名称 / 代码</th>
          <th v-for="col in sortCols" :key="col.key"
              :class="{ sorted: sortKey === col.key }"
              :data-order="sortKey === col.key ? sortOrder : ''"
              @click="onSort(col.key)">
            {{ col.label }}<br>
            <span class="sub-label">{{ col.sub }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(s, i) in sortedSectors" :key="s.concept_code"
            :class="{ 'has-holding': isCustom && (s.holding_in_group?.length || 0) > 0, active: activeCode === s.concept_code }"
            @click="onRowClick(s.concept_code)">
          <td class="center">
            <span class="rank-num" :class="rankClass(i)">{{ i + 1 }}</span>
          </td>
          <td class="left concept-name">
            {{ s.concept_name }}
            <span v-if="isCustom && (s.holding_in_group?.length || 0) > 0" class="holding-badge">
              持仓{{ s.holding_in_group!.length }}
            </span>
            <div class="code-sub">{{ s.concept_code }}</div>
          </td>
          <td :class="changeCls(s.s1_return)" :data-v="s.s1_return">{{ fmt(s.s1_return) }}%</td>
          <td :class="changeCls(s.body ?? null)" :data-v="s.body ?? ''">
            {{ s.body === null || s.body === undefined ? '-' : fmt(s.body) + '%' }}
          </td>
          <td :data-v="s.s2_breadth">{{ Math.round(s.s2_breadth * (s.member_count || 0)) }}/{{ s.member_count || 0 }}</td>
          <td :data-v="s.member_count || 0">{{ s.member_count || '-' }}</td>
          <td><span class="score-bar" :style="{ background: scoreColor(s.score, tab) }">{{ fmt(s.score) }}</span></td>
        </tr>
        <tr v-if="sortedSectors.length === 0"><td colspan="7" class="empty">无数据</td></tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { SectorEntry } from '@/api/types'
import { fmt, changeCls, scoreColor, rankClass } from '@/utils/format'

const props = defineProps<{
  sectors: SectorEntry[]
  tab: 'top' | 'bottom' | 'zt'
  isCustom?: boolean
  activeCode?: string
}>()

const emit = defineEmits<{ (e: 'rowClick', code: string): void }>()

// 排序列定义（顺序对应表头）
const sortCols = [
  { key: 's1', label: '涨幅', sub: '成分股平均涨幅' },
  { key: 'body', label: '实体涨幅', sub: '开盘至今均值' },
  { key: 's2', label: '上涨家数', sub: '上涨只数/总只数' },
  { key: 'cnt', label: '成分', sub: '股数' },
  { key: 'score', label: '评分', sub: '综合分' },
] as const

// 排序状态（默认 score 降序，与后端一致）
const sortKey = ref<string>('score')
const sortOrder = ref<'↓' | '↑'>('↓')

function sortVal(s: SectorEntry, key: string): number {
  let v: number | null | undefined
  if (key === 's1') v = s.s1_return
  else if (key === 'body') v = s.body
  else if (key === 's2') v = s.s2_breadth
  else if (key === 'cnt') v = s.member_count
  else v = s.score
  if (v === null || v === undefined || isNaN(v)) return 0
  return v
}

const sortedSectors = computed(() => {
  const arr = [...props.sectors]
  const key = sortKey.value
  const desc = sortOrder.value === '↓'
  arr.sort((a, b) => {
    const av = sortVal(a, key)
    const bv = sortVal(b, key)
    return desc ? bv - av : av - bv
  })
  return arr
})

function onSort(key: string) {
  if (sortKey.value === key) {
    sortOrder.value = sortOrder.value === '↓' ? '↑' : '↓'
  } else {
    sortKey.value = key
    sortOrder.value = '↓'
  }
}

function onRowClick(code: string) {
  emit('rowClick', code)
}

defineExpose({ setSort: (key: string, order: '↓' | '↑') => { sortKey.value = key; sortOrder.value = order } })
</script>

<style scoped>
.rank-table-wrap { overflow-x: auto; }
.rank-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.rank-table th, .rank-table td {
  padding: 8px 10px; text-align: right; border-bottom: 1px solid #f3f4f6;
  white-space: nowrap; cursor: pointer;
}
.rank-table th { background: #f9fafb; color: #6b7280; font-size: 12px; font-weight: 600; position: sticky; top: 0; z-index: 1; }
.rank-table th:hover { background: #f3f4f6; }
.rank-table th.center, .rank-table td.center { text-align: center; }
.rank-table th.left, .rank-table td.left { text-align: left; }
.rank-table th.sorted::after { content: attr(data-order); color: #3b82f6; margin-left: 3px; }
.rank-table th.left { cursor: default; }
.rank-table th.center { cursor: default; }
.sub-label { font-weight: normal; color: #9ca3af; }
.rank-table tbody tr:hover { background: #f9fafb; }
.rank-table tbody tr.active { background: #eff6ff; }
.rank-table tbody tr.active td { color: #1e40af; }

.up { color: #ef4444; }
.down { color: #10b981; }

.concept-name { color: #111827; font-weight: 600; }
.code-sub { font-size: 11px; color: #9ca3af; font-weight: normal; }

.rank-num {
  display: inline-block; width: 22px; height: 22px; line-height: 22px;
  text-align: center; border-radius: 50%; font-size: 11px; font-weight: 600; color: #fff;
}
.rank-num.r1 { background: #dc2626; }
.rank-num.r2 { background: #ea580c; }
.rank-num.r3 { background: #d97706; }
.rank-num.rN { background: #f3f4f6; color: #6b7280; }

.score-bar {
  display: inline-block; min-width: 30px; padding: 2px 8px; border-radius: 10px;
  font-size: 12px; font-weight: 600; color: #fff;
}

/* 持仓高亮（custom 看板） */
tr.has-holding { background: linear-gradient(90deg, #fef9c3 0%, #fffbeb 100%) !important; border-left: 3px solid #f59e0b; }
tr.has-holding td { color: #92400e; }
tr.has-holding:hover { background: #fef3c7 !important; }
.holding-badge { padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 700; background: #f59e0b; color: #fff; margin-left: 5px; }

.empty { text-align: center; color: #9ca3af; padding: 24px; cursor: default; }
</style>
