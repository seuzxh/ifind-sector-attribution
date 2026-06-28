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

    <!-- 竞价强势个股排行 -->
    <div class="panel">
      <h2><span class="badge badge-red">⚡</span> 竞价强势个股 <span class="hint">4 因子综合分（高开/爆量/失衡/趋势）· 表头可排序</span></h2>
      <div class="table-wrap">
        <table class="rank-table">
          <thead>
            <tr>
              <th class="center">#</th>
              <th class="left">代码 / 名称</th>
              <th v-for="col in sortCols" :key="col.key"
                  :class="{ sorted: sortKey === col.key }"
                  :data-order="sortKey === col.key ? sortOrder : ''"
                  @click="onSort(col.key)">
                {{ col.label }}<br><span class="sub-label">{{ col.sub }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in sortedStocks" :key="s.code"
                :class="{ 'is-holding': s.holding }">
              <td class="center"><span class="rank-num" :class="rankClass(i)">{{ i + 1 }}</span></td>
              <td class="left">
                {{ s.code }}
                <div class="code-sub">{{ s.name }}</div>
              </td>
              <td :class="gapCls(s.gap_pct)" :data-v="s.gap_pct">
                {{ fmt(s.gap_pct) }}%
                <span v-if="s.holding" class="hold-tag">持仓</span>
              </td>
              <td :data-v="s.vol_ratio">{{ s.vol_ratio ? s.vol_ratio.toFixed(2) : '−' }}</td>
              <td :class="imbCls(s.order_imbalance)" :data-v="s.order_imbalance">{{ (s.order_imbalance * 100).toFixed(0) }}%</td>
              <td :data-v="s.trend_score" :class="changeCls(s.trend_score)">{{ fmt(s.trend_score) }}%</td>
              <td><span class="score-bar" :style="{ background: s.score >= 0 ? '#ef4444' : '#10b981' }">{{ fmt(s.score) }}</span></td>
            </tr>
            <tr v-if="sortedStocks.length === 0"><td colspan="7" class="empty">无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 分组详情 -->
    <div class="members-section">
      <h2>分组详情</h2>
      <div v-if="allGroups.length" class="members-grid">
        <div v-for="(g, i) in allGroups" :key="g.group_id" class="sector-card">
          <div class="card-head" :style="{ background: headerColor(g) }">
            <span class="head-title">
              <span class="num">{{ i + 1 }}</span>{{ g.group_name }}
              <span v-if="g.is_zt" class="zt-tag">涨停池</span>
            </span>
            <span class="meta">分{{ fmt(g.score) }} · 高开{{ fmt(g.sector_gap) }}% · 量比{{ g.sector_vol_ratio }} · 联动{{ (g.coherency * 100).toFixed(0) }}% · {{ g.member_count }}只</span>
          </div>
          <table class="card-table">
            <thead>
              <tr>
                <th class="left">代码</th>
                <th class="left name-th">名称</th>
                <th :class="{ sorted: grpSortKey(g.group_id) === 'gap_pct' }" @click="sortGrp(g.group_id, 'gap_pct')">高开</th>
                <th :class="{ sorted: grpSortKey(g.group_id) === 'vol_ratio' }" @click="sortGrp(g.group_id, 'vol_ratio')">量比</th>
                <th :class="{ sorted: grpSortKey(g.group_id) === 'order_imbalance' }" @click="sortGrp(g.group_id, 'order_imbalance')">失衡</th>
                <th :class="{ sorted: grpSortKey(g.group_id) === 'trend_score' }" @click="sortGrp(g.group_id, 'trend_score')">趋势</th>
                <th :class="{ sorted: grpSortKey(g.group_id) === 'score' }" @click="sortGrp(g.group_id, 'score')">综合分</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in sortedGroupStocks(g)" :key="m.code" :class="{ 'is-holding': m.holding }">
                <td class="left">{{ m.code }}</td>
                <td class="left stock-name" :title="m.name">{{ m.name }}</td>
                <td :class="gapCls(m.gap_pct)">
                  {{ fmt(m.gap_pct) }}%
                  <span v-if="m.holding" class="hold-tag-sm">持</span>
                </td>
                <td>{{ m.vol_ratio ? m.vol_ratio.toFixed(2) : '−' }}</td>
                <td>{{ (m.order_imbalance * 100).toFixed(0) }}%</td>
                <td :class="changeCls(m.trend_score)">{{ fmt(m.trend_score) }}%</td>
                <td><b>{{ fmt(m.score) }}</b></td>
              </tr>
              <tr v-if="!g.top_stocks || g.top_stocks.length === 0"><td colspan="7" class="empty">无</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-else class="empty-msg">无数据</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onUnmounted } from 'vue'
import { getAuctionDashboard, type AuctionPayload, type AuctionStock, type AuctionGroup } from '@/api/auction'
import { fmt, changeCls, rankClass } from '@/utils/format'

const payload = ref<AuctionPayload | null>(null)
const statusText = ref('加载中...')
const statusCls = ref('')

// ===== 排行表排序 =====
const sortCols = [
  { key: 'gap_pct', label: '高开', sub: '竞价涨跌幅' },
  { key: 'vol_ratio', label: '量比', sub: '竞价/昨日量' },
  { key: 'order_imbalance', label: '失衡', sub: '买-卖/总' },
  { key: 'trend_score', label: '趋势', sub: '9:20→9:25' },
  { key: 'score', label: '综合分', sub: '4因子加权' },
] as const
const sortKey = ref<string>('score')
const sortOrder = ref<'↓' | '↑'>('↓')

function stockVal(s: AuctionStock, key: string): number {
  const v = (s as any)[key] as number
  return v === null || v === undefined || isNaN(v) ? 0 : v
}
const sortedStocks = computed(() => {
  const arr = [...(payload.value?.top_stocks || [])]
  const desc = sortOrder.value === '↓'
  arr.sort((a, b) => desc ? stockVal(b, sortKey.value) - stockVal(a, sortKey.value) : stockVal(a, sortKey.value) - stockVal(b, sortKey.value))
  return arr
})
function onSort(key: string) {
  if (sortKey.value === key) sortOrder.value = sortOrder.value === '↓' ? '↑' : '↓'
  else { sortKey.value = key; sortOrder.value = '↓' }
}

// ===== 分组卡片排序 =====
const grpSortState = reactive<Record<string, { key: string; order: '↓' | '↑' }>>({})
function grpSortKey(id: string): string { return grpSortState[id]?.key || 'gap_pct' }
function sortGrp(id: string, key: string) {
  const cur = grpSortState[id]
  if (cur && cur.key === key) cur.order = cur.order === '↓' ? '↑' : '↓'
  else grpSortState[id] = { key, order: '↓' }
}
function sortedGroupStocks(g: AuctionGroup): AuctionStock[] {
  const arr = g.top_stocks || []
  if (!arr.length) return arr
  const st = grpSortState[g.group_id] || { key: 'gap_pct', order: '↓' }
  const desc = st.order === '↓'
  return [...arr].sort((a, b) => desc ? stockVal(b, st.key) - stockVal(a, st.key) : stockVal(a, st.key) - stockVal(b, st.key))
}

// ===== 全部分组（top + zt 拼接）=====
const allGroups = computed(() => [...(payload.value?.top_groups || []), ...(payload.value?.zt_groups || [])])

function headerColor(g: AuctionGroup): string {
  if (g.is_zt) return '#8b5cf6'  // ZT 紫色
  return g.score >= 0 ? '#ef4444' : '#10b981'
}

// ===== 色阶 =====
function gapCls(n: number): string { return n > 0 ? 'up' : n < 0 ? 'down' : '' }
function imbCls(n: number): string { return n > 0.3 ? 'up' : n < -0.3 ? 'down' : '' }

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
.rank-table tbody tr.is-holding { background: #fef9c3 !important; }
.rank-table tbody tr.is-holding td:first-child { border-left: 3px solid #f59e0b; }
.up { color: #ef4444; }
.down { color: #10b981; }
.code-sub { font-size: 11px; color: #9ca3af; }
.rank-num { display: inline-block; width: 22px; height: 22px; line-height: 22px; text-align: center; border-radius: 50%; font-size: 11px; font-weight: 600; color: #fff; }
.rank-num.r1 { background: #dc2626; }
.rank-num.r2 { background: #ea580c; }
.rank-num.r3 { background: #d97706; }
.rank-num.rN { background: #f3f4f6; color: #6b7280; }
.score-bar { display: inline-block; min-width: 30px; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; color: #fff; }
.hold-tag { padding: 0 5px; border-radius: 3px; font-size: 10px; background: #f59e0b; color: #fff; margin-left: 3px; font-weight: 600; }
.members-section { padding: 0 16px 16px; }
.members-section h2 { font-size: 14px; color: #374151; margin-bottom: 8px; }
.members-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px; }
.sector-card { border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; background: #fff; }
.card-head { padding: 8px 12px; font-size: 13px; font-weight: 600; color: #fff; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.head-title { display: flex; align-items: center; gap: 6px; }
.num { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border-radius: 50%; background: rgba(255,255,255,0.25); font-size: 11px; }
.zt-tag { background: rgba(255,255,255,0.9); color: #8b5cf6; padding: 0 4px; border-radius: 3px; font-size: 10px; }
.meta { font-size: 11px; opacity: 0.9; font-weight: normal; }
.card-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.card-table th, .card-table td { padding: 6px 8px; text-align: right; border-bottom: 1px solid #f3f4f6; white-space: nowrap; }
.card-table th { background: #f9fafb; color: #6b7280; font-weight: 600; cursor: pointer; }
.card-table th:hover { background: #f3f4f6; }
.card-table th.left { text-align: left; cursor: default; }
.card-table th.sorted::after { content: attr(data-order); color: #3b82f6; margin-left: 3px; }
.card-table td.left { text-align: left; }
.stock-name { color: #374151; max-width: 70px; overflow: hidden; text-overflow: ellipsis; }
.name-th { max-width: 70px; }
.hold-tag-sm { padding: 0 5px; border-radius: 3px; font-size: 10px; background: #f59e0b; color: #fff; margin-left: 3px; }
.card-table tbody tr.is-holding { background: #fef9c3 !important; }
.empty { text-align: center; color: #9ca3af; padding: 16px; }
.empty-msg { text-align: center; color: #9ca3af; padding: 24px; }
</style>
