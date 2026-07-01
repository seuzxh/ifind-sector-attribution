<template>
  <div class="auction-page">
    <!-- 状态栏 -->
    <div class="status-bar" :class="statusCls">{{ statusText }}</div>

    <!-- 统计栏 -->
    <div v-if="payload?.market_stats" class="stats-bar">
      <div class="item"><span class="label">监控股票</span><span class="val">{{ payload.market_stats.stock_count }}</span></div>
      <div class="item"><span class="label">平均高开</span><span class="val">{{ fmt(payload.market_stats.avg_gap) }}%</span></div>
      <div class="item up"><span class="label">高开&gt;2%</span><span class="val">{{ payload.market_stats.strong_gap_count }}</span></div>
      <div class="item down"><span class="label">低开</span><span class="val">{{ payload.market_stats.down_count }}</span></div>
      <div class="item up"><span class="label">涨停级</span><span class="val">{{ payload.market_stats.limit_up_count }}</span></div>
      <div class="item up"><span class="label">爆量</span><span class="val">{{ payload.market_stats.explode_count }}</span></div>
    </div>

    <!-- 顶部主表：竞价强势分组（点击行查看成分股） -->
    <div class="panel">
      <h2>
        <span class="badge badge-red">⚡</span> 竞价强势分组
        <span class="hint">通过竞价个股反推今天可能爆发的分组 · 点击行查看成分股 · 表头可排序</span>
      </h2>
      <div class="table-wrap">
        <table class="rank-table">
          <thead>
            <tr>
              <th class="center">#</th>
              <th class="left">分组名称</th>
              <th v-for="col in grpSortCols" :key="col.key"
                  :class="{ sorted: grpSortKey === col.key, center: false, left: false }"
                  :data-order="grpSortKey === col.key ? grpSortOrder : ''"
                  @click="onGrpSort(col.key)">
                {{ col.label }}<br><span class="sub-label">{{ col.sub }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(g, i) in sortedGroups" :key="g.group_id"
                :class="{ active: selectedGroupId === g.group_id }"
                @click="selectGroup(g.group_id)">
              <td class="center"><span class="rank-num" :class="rankClass(i)">{{ i + 1 }}</span></td>
              <td class="left concept-name">
                {{ g.group_name }}
                <span v-if="g.is_zt" class="zt-tag">涨停池</span>
                <div class="code-sub">{{ g.member_count }} 只成分股</div>
              </td>
              <td :class="gapCls(g.sector_gap)" :data-v="g.sector_gap">{{ fmt(g.sector_gap) }}%</td>
              <td :data-v="g.sector_vol_ratio">{{ g.sector_vol_ratio || '−' }}</td>
              <td :data-v="g.sector_imbalance">{{ (g.sector_imbalance * 100).toFixed(0) }}%</td>
              <td :data-v="g.coherency">{{ (g.coherency * 100).toFixed(0) }}%</td>
              <td><span class="score-bar" :style="{ background: g.score >= 0 ? '#ef4444' : '#10b981' }">{{ fmt(g.score) }}</span></td>
            </tr>
            <tr v-if="sortedGroups.length === 0"><td colspan="7" class="empty">无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 下方：选中分组的成分股详情 -->
    <div class="members-section" ref="membersRef">
      <h2>成分股详情 <span class="sub" v-if="selectedGroup">· {{ selectedGroup.group_name }}</span>
        <span class="hint" v-if="!selectedGroup">点击上方分组查看成分股</span>
      </h2>
      <div v-if="selectedGroup" class="sector-card detail-card">
        <div class="card-head" :style="{ background: headerColor(selectedGroup) }">
          <span class="head-title">
            <span v-if="selectedGroup.is_zt">🔥 </span>{{ selectedGroup.group_name }}
            <span v-if="selectedGroup.is_zt" class="zt-tag">涨停池</span>
          </span>
          <span class="meta">综合分 {{ fmt(selectedGroup.score) }} · 竞价涨幅 {{ fmt(selectedGroup.sector_gap) }}% · 量比中位 {{ selectedGroup.sector_vol_ratio }} · 联动 {{ (selectedGroup.coherency * 100).toFixed(0) }}% · {{ selectedGroup.member_count }} 只</span>
        </div>
        <table class="card-table">
          <thead>
            <tr>
              <th class="left">代码</th>
              <th class="left name-th">名称</th>
              <th :class="{ sorted: memberSortKey === 'gap_pct' }" @click="sortMember('gap_pct')">高开</th>
              <th :class="{ sorted: memberSortKey === 'vol_ratio' }" @click="sortMember('vol_ratio')">量比</th>
              <th :class="{ sorted: memberSortKey === 'order_imbalance' }" @click="sortMember('order_imbalance')">失衡</th>
              <th :class="{ sorted: memberSortKey === 'trend_score' }" @click="sortMember('trend_score')">趋势</th>
              <th :class="{ sorted: memberSortKey === 'score' }" @click="sortMember('score')">综合分</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in sortedMembers" :key="m.code" :class="{ 'is-holding': m.holding }">
              <td class="left">{{ m.code }}</td>
              <td class="left stock-name" :title="m.name">
                {{ m.name }}
                <div v-if="m.groups && m.groups.length" class="group-tags">
                  <span v-for="gn in m.groups" :key="gn" class="grp-tag" :class="{ 'grp-hold': gn === holdingGroupName }">{{ gn }}</span>
                </div>
              </td>
              <td :class="gapCls(m.gap_pct)">
                {{ fmt(m.gap_pct) }}%
                <span v-if="m.holding" class="hold-tag-sm">持</span>
              </td>
              <td>{{ m.vol_ratio ? m.vol_ratio.toFixed(2) : '−' }}</td>
              <td>{{ (m.order_imbalance * 100).toFixed(0) }}%</td>
              <td :class="changeCls(m.trend_score)">{{ fmt(m.trend_score) }}%</td>
              <td><b>{{ fmt(m.score) }}</b></td>
            </tr>
            <tr v-if="sortedMembers.length === 0"><td colspan="7" class="empty">该分组无竞价数据</td></tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-msg">点击上方分组查看成分股</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { getAuctionDashboard, type AuctionPayload, type AuctionGroup } from '@/api/auction'
import { fmt, changeCls, rankClass } from '@/utils/format'

const payload = ref<AuctionPayload | null>(null)
const statusText = ref('加载中...')
const statusCls = ref('')

// ===== 全部分组（top + zt 拼接）=====
const allGroups = computed<AuctionGroup[]>(() => [
  ...(payload.value?.top_groups || []),
  ...(payload.value?.zt_groups || []),
])

// 持仓分组名（来自后端 config.HOLDING_GROUP_NAME，前端据此高亮该分组标签）
const holdingGroupName = computed(() => payload.value?.holding_group_name || '')

// ===== 分组表排序 =====
const grpSortCols = [
  { key: 'sector_gap', label: '竞价涨幅', sub: '成分股均值' },
  { key: 'sector_vol_ratio', label: '量比中位', sub: '爆量度' },
  { key: 'sector_imbalance', label: '挂单失衡', sub: '抢筹度' },
  { key: 'coherency', label: '联动度', sub: '高开占比' },
  { key: 'score', label: '综合分', sub: '4因子加权' },
] as const
const grpSortKey = ref<string>('score')
const grpSortOrder = ref<'↓' | '↑'>('↓')

function grpVal(g: AuctionGroup, key: string): number {
  const v = (g as any)[key] as number
  return v === null || v === undefined || isNaN(v) ? 0 : v
}
const sortedGroups = computed(() => {
  const arr = [...allGroups.value]
  const desc = grpSortOrder.value === '↓'
  arr.sort((a, b) => desc ? grpVal(b, grpSortKey.value) - grpVal(a, grpSortKey.value) : grpVal(a, grpSortKey.value) - grpVal(b, grpSortKey.value))
  return arr
})
function onGrpSort(key: string) {
  if (grpSortKey.value === key) grpSortOrder.value = grpSortOrder.value === '↓' ? '↑' : '↓'
  else { grpSortKey.value = key; grpSortOrder.value = '↓' }
}

// ===== 选中的分组（点击行）=====
const selectedGroupId = ref<string | null>(null)
const membersRef = ref<HTMLElement | null>(null)  // 成分股区 DOM，供点击后滚动
const selectedGroup = computed<AuctionGroup | null>(() =>
  selectedGroupId.value ? allGroups.value.find(g => g.group_id === selectedGroupId.value) || null : null
)
function selectGroup(gid: string) {
  selectedGroupId.value = gid
  // 点击后滚动到成分股详情区（nextTick 等 DOM 更新完）
  nextTick(() => {
    membersRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

// ===== 成分股排序（选中分组内）=====
const memberSortKey = ref<string>('gap_pct')
const memberSortOrder = ref<'↓' | '↑'>('↓')
function memberVal(m: any, key: string): number {
  const v = m[key] as number
  return v === null || v === undefined || isNaN(v) ? 0 : v
}
const sortedMembers = computed(() => {
  const arr = [...(selectedGroup.value?.top_stocks || [])]
  const desc = memberSortOrder.value === '↓'
  arr.sort((a: any, b: any) => desc ? memberVal(b, memberSortKey.value) - memberVal(a, memberSortKey.value) : memberVal(a, memberSortKey.value) - memberVal(b, memberSortKey.value))
  return arr
})
function sortMember(key: string) {
  if (memberSortKey.value === key) memberSortOrder.value = memberSortOrder.value === '↓' ? '↑' : '↓'
  else { memberSortKey.value = key; memberSortOrder.value = '↓' }
}

function headerColor(g: AuctionGroup): string {
  if (g.is_zt) return '#8b5cf6'
  return g.score >= 0 ? '#ef4444' : '#10b981'
}

// ===== 色阶 =====
function gapCls(n: number): string { return n > 0 ? 'up' : n < 0 ? 'down' : '' }

// ===== 数据刷新后，保持选中分组（或默认选第一个）=====
function ensureSelection() {
  if (!allGroups.value.length) { selectedGroupId.value = null; return }
  const stillExists = allGroups.value.some(g => g.group_id === selectedGroupId.value)
  if (!stillExists) selectedGroupId.value = allGroups.value[0].group_id
}

// ===== 轮询（3s，auction 不做会话感知）=====
let pollTimer: ReturnType<typeof setInterval> | null = null
let refreshSeq = 0
const POLL_INTERVAL = 3000

async function refresh() {
  const mySeq = ++refreshSeq
  let data: AuctionPayload
  try {
    data = await getAuctionDashboard({})
  } catch (e: any) {
    statusText.value = '⚠ 请求失败: ' + (e?.message || e); statusCls.value = 'warn'; return
  }
  if (mySeq !== refreshSeq) return
  if (data.error) { statusText.value = '⚠ ' + data.error; statusCls.value = 'warn'; payload.value = null; return }
  payload.value = data
  ensureSelection()
  const ms = data.market_stats
  statusText.value = `⚡ ${data.is_today ? '实时' : '历史回看'} · ${data.trade_date} ${data.snapshot_time} · 监控${ms?.stock_count ?? 0}只 · 高开>2%:${ms?.strong_gap_count ?? 0} · 爆量:${ms?.explode_count ?? 0}` + (data.is_today ? ' · 自动3s' : '')
  statusCls.value = 'live'
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(() => refresh(), POLL_INTERVAL)
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.auction-page { display: flex; flex-direction: column; height: 100%; overflow: auto; }
.status-bar { padding: 6px 16px; font-size: 13px; background: #fff; border-bottom: 1px solid #e5e7eb; }
.status-bar.live { color: #059669; }
.status-bar.warn { color: #d97706; }
.stats-bar { display: flex; gap: 16px; padding: 10px 16px; background: #fff; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.stats-bar .item { display: flex; flex-direction: column; gap: 2px; }
.stats-bar .label { font-size: 11px; color: #6b7280; }
.stats-bar .val { font-size: 16px; font-weight: 600; }
.stats-bar .item.up .val { color: #ef4444; }
.stats-bar .item.down .val { color: #10b981; }
.panel { background: #fff; margin: 16px; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.panel h2 { font-size: 14px; color: #374151; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
.badge-red { background: #fee2e2; color: #dc2626; }
.hint { font-size: 11px; color: #9ca3af; font-weight: normal; margin-left: auto; }
.table-wrap { overflow-x: auto; }
.rank-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.rank-table th, .rank-table td { padding: 8px 10px; text-align: right; border-bottom: 1px solid #f3f4f6; white-space: nowrap; }
.rank-table th { background: #f9fafb; color: #6b7280; font-size: 12px; font-weight: 600; cursor: pointer; }
.rank-table th:hover { background: #f3f4f6; }
.rank-table th.center, .rank-table td.center { text-align: center; cursor: default; }
.rank-table th.left, .rank-table td.left { text-align: left; cursor: default; }
.rank-table th.sorted::after { content: attr(data-order); color: #3b82f6; margin-left: 3px; }
.sub-label { font-weight: normal; color: #9ca3af; }
/* 分组行可点击 */
.rank-table tbody tr { cursor: pointer; }
.rank-table tbody tr:hover { background: #f0f7ff; }
.rank-table tbody tr.active { background: #dbeafe !important; }
.rank-table tbody tr.active td:first-child { border-left: 3px solid #3b82f6; }
.concept-name { font-weight: 600; color: #111827; }
.code-sub { font-size: 11px; color: #9ca3af; font-weight: normal; }
.zt-tag { background: #ede9fe; color: #8b5cf6; padding: 0 5px; border-radius: 3px; font-size: 10px; font-weight: 600; margin-left: 4px; }
.up { color: #ef4444; }
.down { color: #10b981; }
.rank-num { display: inline-block; width: 22px; height: 22px; line-height: 22px; text-align: center; border-radius: 50%; font-size: 11px; font-weight: 600; color: #fff; }
.rank-num.r1 { background: #dc2626; }
.rank-num.r2 { background: #ea580c; }
.rank-num.r3 { background: #d97706; }
.rank-num.rN { background: #f3f4f6; color: #6b7280; }
.score-bar { display: inline-block; min-width: 30px; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; color: #fff; }
.members-section { padding: 0 16px 16px; }
.members-section h2 { font-size: 14px; color: #374151; margin-bottom: 8px; }
.members-section .sub { font-size: 12px; color: #6b7280; font-weight: normal; }
.detail-card { border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; background: #fff; }
.card-head { padding: 8px 12px; font-size: 13px; font-weight: 600; color: #fff; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.head-title { display: flex; align-items: center; gap: 6px; }
.card-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.card-table th, .card-table td { padding: 6px 8px; text-align: right; border-bottom: 1px solid #f3f4f6; white-space: nowrap; }
.card-table th { background: #f9fafb; color: #6b7280; font-weight: 600; cursor: pointer; }
.card-table th:hover { background: #f3f4f6; }
.card-table th.left { text-align: left; cursor: default; }
.card-table th.sorted::after { content: '↓'; color: #3b82f6; margin-left: 3px; }
.card-table td.left { text-align: left; }
.stock-name { color: #374151; max-width: 90px; overflow: hidden; text-overflow: ellipsis; }
.name-th { max-width: 90px; }
.hold-tag-sm { padding: 0 5px; border-radius: 3px; font-size: 10px; background: #f59e0b; color: #fff; margin-left: 3px; }
/* 个股所属自选分组标签 */
.group-tags { margin-top: 2px; display: flex; flex-wrap: wrap; gap: 3px; }
.grp-tag { display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 10px; background: #e0e7ff; color: #4338ca; line-height: 16px; white-space: nowrap; }
.grp-tag.grp-hold { background: #f59e0b; color: #fff; font-weight: 600; }
.card-table tbody tr.is-holding { background: #fef9c3 !important; }
.empty { text-align: center; color: #9ca3af; padding: 16px; }
.empty-msg { text-align: center; color: #9ca3af; padding: 24px; }
</style>
