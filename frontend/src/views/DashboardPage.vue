<template>
  <div class="dashboard-page">
    <!-- 顶部控制栏：模式切换 + 日期选择 -->
    <div class="controls-bar">
      <label>模式</label>
      <select v-model="mode" class="mode-sel" @change="onModeChange">
        <option value="realtime">实时（盘中）</option>
        <option value="history">历史（收盘）</option>
      </select>
      <label>日期</label>
      <input type="date" v-model="dateSelValue" class="date-sel" min="2026-01-01" max="2026-12-31" @change="onDateChange" />
      <span class="date-hint">{{ dateHint }}</span>
    </div>

    <!-- 时间轴（仅实时模式） -->
    <TimeBar
      v-if="mode === 'realtime'"
      :available-times="availableTimes"
      :current-index="sliderIndex"
      :current-time-text="currentTimeText"
      :auto-follow="autoFollow"
      :playing="playing"
      :speed-ms="speedMs"
      @update:current-index="sliderIndex = $event"
      @slider-change="onSliderChange"
      @toggle-play="togglePlay"
      @speed-change="onSpeedChange"
      @jump-to-latest="jumpToLatest"
    />

    <!-- 状态栏 -->
    <div class="status-bar" :class="statusCls">
      {{ statusText }}
      <button v-if="canCalc" class="calc-btn" @click="forceCalc" :disabled="calcLoading">
        {{ calcLoading ? '计算中（约2分钟）…' : '📥 拉取并计算' }}
      </button>
    </div>

    <!-- 统计栏 -->
    <div v-if="payload?.market_stats" class="stats-bar">
      <div class="item"><span class="label">监控股票</span><span class="val">{{ payload.market_stats.stock_count }}</span></div>
      <div class="item"><span class="label">平均涨幅</span><span class="val">{{ fmt(payload.market_stats.market_avg_change) }}%</span></div>
      <div class="item up"><span class="label">上涨</span><span class="val">{{ payload.market_stats.up_count }}</span></div>
      <div class="item down"><span class="label">下跌</span><span class="val">{{ payload.market_stats.down_count }}</span></div>
      <div class="item"><span class="label">平盘</span><span class="val">{{ payload.market_stats.flat_count }}</span></div>
      <div class="item up"><span class="label">涨停</span><span class="val">{{ payload.market_stats.limit_up_count ?? 0 }}</span></div>
    </div>

    <!-- 排行面板：强势 / 弱势 / 涨停 -->
    <div class="panels">
      <div class="panel">
        <h2><span class="badge badge-red">强势</span> Top 板块 <span class="hint">点击行查看下方成分股</span></h2>
        <RankTable :sectors="payload?.top_sectors || []" tab="top" :is-custom="isCustom"
                   :active-code="activeCode" @row-click="onRowClick('top', $event)" />
      </div>
      <div class="panel">
        <h2><span class="badge badge-green">弱势</span> Bottom 板块 <span class="hint">点击行查看下方成分股</span></h2>
        <RankTable :sectors="payload?.bottom_sectors || []" tab="bottom" :is-custom="isCustom"
                   :active-code="activeCode" @row-click="onRowClick('bottom', $event)" />
      </div>
      <div v-if="isCustom && (payload?.zt_sectors?.length || 0) > 0" class="panel">
        <h2><span class="badge badge-orange">🔥 涨停板分组</span> ZT <span class="hint">按涨幅排序</span></h2>
        <RankTable :sectors="payload?.zt_sectors || []" tab="zt" :is-custom="isCustom"
                   :active-code="activeCode" @row-click="onRowClick('zt', $event)" />
      </div>
    </div>

    <!-- 成分股区 -->
    <div class="members-section">
      <h2>板块成分股</h2>
      <div class="members-tabs">
        <button :class="{ active: membersTab === 'top' }" @click="membersTab = 'top'">📈 强势板块成分股</button>
        <button :class="{ active: membersTab === 'bottom' }" @click="membersTab = 'bottom'">📉 弱势板块成分股</button>
        <button v-if="isCustom && (payload?.zt_sectors?.length || 0) > 0"
                :class="{ active: membersTab === 'zt' }" @click="membersTab = 'zt'">🔥 涨停分组成分股</button>
      </div>
      <MemberCardGrid ref="memberGrid" :sectors="currentMembers" :tab="membersTab" :is-custom="isCustom" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import TimeBar from '@/components/dashboard/TimeBar.vue'
import RankTable from '@/components/dashboard/RankTable.vue'
import MemberCardGrid from '@/components/dashboard/MemberCardGrid.vue'
import { getCustomDashboard, getRealtimeDashboard, getHistoryDashboard } from '@/api/dashboard'
import type { DashboardPayload } from '@/api/types'
import { getSessionStatus } from '@/api/session'
import { getAvailableDates } from '@/api/calendar'
import { fmt } from '@/utils/format'

const route = useRoute()
// board: custom 或 sector（同一组件复用）
const isCustom = computed(() => route.name === 'custom')

// ===== 模式 + 日期 =====
const mode = ref<'realtime' | 'history'>('realtime')
// 日期选择器值（YYYY-MM-DD），默认今天
const todayHyphen = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` })()
const dateSelValue = ref(todayHyphen)
const dateHint = ref('加载中…')   // 已入库日期范围提示
const ingestedDates = ref<string[]>([])  // 已入库历史日期（YYYYMMDD）

// dateSelValue(YYYY-MM-DD) → YYYYMMDD
function getDateVal(): string {
  return dateSelValue.value.replace(/-/g, '')
}

// ===== 状态 =====
const payload = ref<DashboardPayload | null>(null)
const canCalc = ref(false)   // 历史日期无数据时，是否可拉取计算（显示按钮）
const availableTimes = ref<string[]>([])
const sliderIndex = ref(0)
const autoFollow = ref(true)
const membersTab = ref<'top' | 'bottom' | 'zt'>('top')
const activeCode = ref('')
const statusText = ref('加载中...')
const statusCls = ref('')

// 成分股网格 ref（用于高亮滚动）
const memberGrid = ref<InstanceType<typeof MemberCardGrid>>()

// ===== 轮询（竞态守卫） =====
let refreshSeq = 0
let pollTimer: ReturnType<typeof setInterval> | null = null
const POLL_INTERVAL = 3000

// ===== 时间轴播放 =====
const playing = ref(false)
const speedMs = ref(800)
let playTimer: ReturnType<typeof setInterval> | null = null

// ===== 会话感知 =====
let sessionTimer: ReturnType<typeof setInterval> | null = null
let resumeTimer: ReturnType<typeof setInterval> | null = null

// 当前时刻文本
const currentTimeText = computed(() => {
  if (playing.value || !autoFollow.value) {
    return availableTimes.value[sliderIndex.value] || payload.value?.snapshot_time || '--:--'
  }
  return payload.value?.snapshot_time || payload.value?.latest_time || '--:--'
})

// 当前成分股来源（按 tab）
const currentMembers = computed(() => {
  const p = payload.value
  if (!p) return []
  if (membersTab.value === 'top') return p.top_sectors || []
  if (membersTab.value === 'zt') return p.zt_sectors || []
  return p.bottom_sectors || []
})

// ===== 模式/日期切换 =====
function onModeChange() {
  stopPlay(); stopPolling()
  if (mode.value === 'realtime') { autoFollow.value = true }
  refresh()
  if (mode.value === 'realtime') checkSession()
}
function onDateChange() {
  stopPlay()
  if (mode.value === 'realtime') { autoFollow.value = true }
  refresh()
}

// 加载已入库历史日期（提示用）
async function loadDates() {
  try {
    const d = await getAvailableDates()
    ingestedDates.value = d.dates || []
    if (ingestedDates.value.length) {
      dateHint.value = `已入库 ${ingestedDates.value[ingestedDates.value.length - 1]}~${ingestedDates.value[0]}`
    } else {
      dateHint.value = '暂无入库数据'
    }
  } catch { dateHint.value = '' }
}

// ===== 核心：刷新数据 =====
async function refresh() {
  const mySeq = ++refreshSeq

  let data: DashboardPayload
  try {
    if (mode.value === 'history') {
      // 历史模式：走 /api/history/dashboard，按当日涨幅
      const date = getDateVal()
      if (!date) { statusText.value = '请选择日期'; statusCls.value = 'warn'; return }
      statusText.value = '加载历史数据…'; statusCls.value = ''
      data = await getHistoryDashboard(date, 10)
    } else {
      // 实时模式：走分时接口
      const params: Record<string, string | boolean | number> = {}
      const today = new Date()
      const todayStr = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`
      params.trade_date = todayStr
      if (!autoFollow.value && availableTimes.value[sliderIndex.value]) {
        params.snapshot_time = availableTimes.value[sliderIndex.value]
      }
      data = isCustom.value
        ? await getCustomDashboard(params)
        : await getRealtimeDashboard({ ...params, watchlist_mode: true })
    }
  } catch (e: any) {
    statusText.value = '⚠ 请求失败: ' + (e?.message || e)
    statusCls.value = 'warn'
    return
  }

  // 竞态守卫：丢弃过期响应
  if (mySeq !== refreshSeq) return

  if (data.error) {
    statusText.value = '⚠ ' + data.error
    statusCls.value = 'warn'
    payload.value = null
    canCalc.value = !!(data as any).can_calc   // 历史无数据时可拉取计算
    return
  }

  canCalc.value = false
  payload.value = data

  // 更新时间轴（仅实时模式有 available_times；历史模式无）
  if (mode.value === 'realtime') {
    const times = data.available_times || []
    if (times.length && times.length !== availableTimes.value.length) {
      availableTimes.value = times
      sliderIndex.value = times.length - 1
    } else if (!availableTimes.value.length && times.length) {
      availableTimes.value = times
      sliderIndex.value = times.length - 1
    }
    if (autoFollow.value) sliderIndex.value = availableTimes.value.length - 1
  }

  // 状态行
  if (mode.value === 'history') {
    statusText.value = `历史 · ${data.date || data.trade_date || getDateVal()}`
  } else {
    const ts = data.snapshot_time || data.latest_time || '--:--'
    statusText.value = `${data.is_today ? '实时' : '历史回看'} · ${ts} · 自动${POLL_INTERVAL / 1000}s`
  }
  statusCls.value = 'live'
}

// ===== 历史无数据时：拉取并计算（force_calc）=====
const calcLoading = ref(false)
async function forceCalc() {
  if (calcLoading.value) return
  calcLoading.value = true
  const mySeq = ++refreshSeq
  try {
    const data = await getHistoryDashboard(getDateVal(), 10, true)
    if (mySeq !== refreshSeq) return
    if (data.error) { statusText.value = '⚠ ' + data.error; statusCls.value = 'warn'; payload.value = null }
    else { canCalc.value = false; payload.value = data; statusText.value = `历史 · ${data.date || getDateVal()}`; statusCls.value = 'live' }
  } catch (e: any) { statusText.value = '⚠ 计算失败: ' + (e?.message || e); statusCls.value = 'warn' }
  finally { calcLoading.value = false }
}

// ===== 行点击 → 切成员 tab + 高亮卡片 =====
function onRowClick(tab: 'top' | 'bottom' | 'zt', code: string) {
  if (membersTab.value !== tab) membersTab.value = tab
  activeCode.value = code
  nextTick(() => memberGrid.value?.highlight(code))
}

// ===== 时间轴交互 =====
function onSliderChange(idx: number) {
  stopPlay()
  autoFollow.value = false
  sliderIndex.value = idx
  refresh()
}
function togglePlay() {
  if (playing.value) { stopPlay(); return }
  // 播放：从头或当前位置开始
  if (sliderIndex.value >= availableTimes.value.length - 1) sliderIndex.value = 0
  autoFollow.value = false
  playing.value = true
  stepPlay()
  playTimer = setInterval(stepPlay, speedMs.value)
}
function stepPlay() {
  if (sliderIndex.value >= availableTimes.value.length - 1) { stopPlay(); return }
  sliderIndex.value += 1
  refresh()
}
function stopPlay() {
  playing.value = false
  if (playTimer) { clearInterval(playTimer); playTimer = null }
}
function onSpeedChange(ms: number) {
  speedMs.value = ms
  if (playing.value) { clearInterval(playTimer!); playTimer = setInterval(stepPlay, ms) }
}
function jumpToLatest() {
  stopPlay()
  autoFollow.value = true
  sliderIndex.value = availableTimes.value.length - 1
  refresh()
}

// ===== 轮询 + 会话感知 =====
function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => { if (autoFollow.value) refresh() }, POLL_INTERVAL)
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function checkSession() {
  try {
    const s = await getSessionStatus()
    if (!s.is_trading_day) {
      stopPolling()
      statusText.value = `今日非交易日（下一交易日 ${s.next_trade_day || ''}）`
      statusCls.value = 'warn'
      scheduleResume()
      return
    }
    if (s.phase === 'pre_open') {
      stopPolling()
      statusText.value = `⏰ 盘前 · ${s.next_open_time || '09:15'} 后自动开始监控`
      statusCls.value = 'warn'
      scheduleResume()
      return
    }
    // 有数据时段 → 启动轮询
    stopPollingResume()
    startPolling()
    refresh()
  } catch {
    // 接口异常 → 保守轮询
    startPolling()
    refresh()
  }
}

function scheduleResume() {
  if (resumeTimer) return
  resumeTimer = setInterval(async () => {
    try {
      const s = await getSessionStatus()
      if (s.is_trading_day && ['auction', 'pre_morning', 'morning', 'lunch', 'afternoon'].includes(s.phase)) {
        clearInterval(resumeTimer!); resumeTimer = null
        startPolling()
        refresh()
      }
    } catch { /* 忽略，继续探测 */ }
  }, 10000)
}
function stopPollingResume() {
  if (resumeTimer) { clearInterval(resumeTimer); resumeTimer = null }
}

// ===== 生命周期 =====
onMounted(async () => {
  await loadDates()
  await refresh()
  if (mode.value === 'realtime') {
    checkSession()
    sessionTimer = setInterval(checkSession, 60000)
  }
})
onUnmounted(() => {
  stopPolling()
  stopPlay()
  stopPollingResume()
  if (sessionTimer) clearInterval(sessionTimer)
})
</script>

<style scoped>
.dashboard-page { display: flex; flex-direction: column; height: 100%; overflow: auto; }
.controls-bar {
  display: flex; align-items: center; gap: 10px; padding: 8px 16px;
  background: #fff; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap;
}
.controls-bar label { font-size: 13px; color: #6b7280; font-weight: 600; }
.mode-sel, .date-sel {
  font-size: 13px; padding: 5px 10px; border-radius: 6px; border: 1px solid #d1d5db; font-family: inherit;
}
.mode-sel:focus, .date-sel:focus { outline: none; border-color: #1e40af; }
.date-hint { font-size: 11px; color: #9ca3af; }
.status-bar { padding: 6px 16px; font-size: 13px; background: #fff; border-bottom: 1px solid #e5e7eb; }
.status-bar.live { color: #059669; }
.calc-btn {
  margin-left: 12px; padding: 3px 10px; font-size: 12px; border-radius: 6px;
  border: 1px solid #1e40af; background: #1e40af; color: #fff; cursor: pointer;
}
.calc-btn:hover:not(:disabled) { background: #1e3a8a; }
.calc-btn:disabled { background: #9ca3af; border-color: #9ca3af; cursor: not-allowed; }
.status-bar.warn { color: #d97706; }
.stats-bar {
  display: flex; gap: 16px; padding: 10px 16px; background: #fff;
  border-bottom: 1px solid #e5e7eb; flex-wrap: wrap;
}
.stats-bar .item { display: flex; flex-direction: column; gap: 2px; }
.stats-bar .label { font-size: 11px; color: #6b7280; }
.stats-bar .val { font-size: 16px; font-weight: 600; }
.stats-bar .item.up .val { color: #ef4444; }
.stats-bar .item.down .val { color: #10b981; }
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; padding: 16px; }
.panel { background: #fff; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.panel h2 { font-size: 14px; color: #374151; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }
.badge-red { background: #fee2e2; color: #dc2626; }
.badge-green { background: #d1fae5; color: #059669; }
.badge-orange { background: #ffedd5; color: #ea580c; }
.hint { font-size: 11px; color: #9ca3af; font-weight: normal; margin-left: auto; }
.members-section { padding: 0 16px 16px; }
.members-section h2 { font-size: 14px; color: #374151; margin-bottom: 8px; }
.members-tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.members-tabs button {
  padding: 5px 14px; border: 1px solid #d1d5db; background: #fff; border-radius: 16px;
  font-size: 12px; cursor: pointer; color: #374151;
}
.members-tabs button.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }
</style>
