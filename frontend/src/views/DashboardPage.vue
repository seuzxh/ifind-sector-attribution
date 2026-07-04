<template>
  <div class="dashboard-page">
    <!-- 顶部控制栏：模式切换 + 日期选择 -->
    <div class="controls-bar">
      <label>模式</label>
      <select v-model="mode" class="mode-sel" @change="onModeChange">
        <option value="realtime">实时（盘中）</option>
        <option value="history">历史（收盘）</option>
        <option v-if="!isCustom" value="watchlist">盘前筛选</option>
      </select>
      <label>日期</label>
      <input type="date" v-model="dateSelValue" class="date-sel" min="2026-01-01" max="2026-12-31" @change="onDateChange" />
      <span class="date-hint">{{ dateHint }}</span>
      <button v-if="!isCustom && mode === 'watchlist'" class="ps-btn" @click="runPrescreen" :disabled="prescreening">
        {{ prescreening ? '筛选中…' : '🔍 盘前筛选' }}
      </button>
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

    <!-- 排行面板：强势 / 弱势 / 涨停（watchlist 模式下隐藏） -->
    <div v-if="mode !== 'watchlist'" class="panels">
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

    <!-- 成分股区（watchlist 模式下隐藏） -->
    <div v-if="mode !== 'watchlist'" class="members-section">
      <h2>板块成分股</h2>
      <div class="members-tabs">
        <button :class="{ active: membersTab === 'top' }" @click="membersTab = 'top'">📈 强势板块成分股</button>
        <button :class="{ active: membersTab === 'bottom' }" @click="membersTab = 'bottom'">📉 弱势板块成分股</button>
        <button v-if="isCustom && (payload?.zt_sectors?.length || 0) > 0"
                :class="{ active: membersTab === 'zt' }" @click="membersTab = 'zt'">🔥 涨停分组成分股</button>
      </div>
      <MemberCardGrid ref="memberGrid" :sectors="currentMembers" :tab="membersTab" :is-custom="isCustom" />
    </div>

    <!-- 盘前 watchlist 视图（watchlist 模式专用） -->
    <div v-if="mode === 'watchlist'" class="members-section">
      <h2>盘前 watchlist（5日涨幅筛选）<span class="hint">{{ watchlistHint }}</span></h2>
      <div v-if="watchlistLoading" class="empty-msg">加载中...</div>
      <div v-else-if="!watchlistSectors.length" class="empty-msg">点击右上角"盘前筛选"生成</div>
      <div v-else class="members-grid watchlist-grid">
        <div v-for="(s, i) in watchlistSectors" :key="s.concept_code" class="sector-card">
          <div class="card-head" style="background: #f59e0b;">
            <span><span class="num">{{ i + 1 }}</span>{{ s.concept_name }}</span>
            <span class="meta">5d {{ fmtPct(s.sector_5d_return) }}</span>
          </div>
          <table class="wl-table">
            <thead>
              <tr><th class="left">代码</th><th class="left">名称</th><th class="r">5d涨幅</th><th class="r">排名</th></tr>
            </thead>
            <tbody>
              <tr v-for="m in s.stocks" :key="m.stock_code">
                <td class="left">{{ m.stock_code }}</td>
                <td class="left stock-name" :title="m.stock_name">{{ m.stock_name }}</td>
                <td class="r" :class="changeCls(m.stock_5d_return)">{{ fmt(m.stock_5d_return) }}%</td>
                <td class="r">{{ m.rank_stock }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 盘前筛选结果弹窗 -->
    <div v-if="psModalShow" class="modal-mask" @click.self="psModalShow = false">
      <div class="modal">
        <div class="modal-head">
          <h2>🔍 盘前筛选结果</h2>
          <button class="modal-close" @click="psModalShow = false">✕</button>
        </div>
        <div class="modal-body">
          <div v-if="psModalLoading" class="empty-msg">筛选中（约5秒）...</div>
          <div v-else-if="psModalError" class="empty-msg">⚠ {{ psModalError }}</div>
          <template v-else>
            <div class="ps-summary">
              📅 {{ psModalData?.date }} · 共选出 <b>{{ psModalData?.sector_count }}</b> 个强势板块、
              <b>{{ psModalData?.stock_count }}</b> 只成分股（5日累计涨幅）
            </div>
            <div class="ps-list">
              <div v-for="s in psModalData?.sectors || []" :key="s.concept_code" class="ps-sector">
                <div class="ps-head">
                  <span class="ps-rank">{{ s.rank_sector }}</span>
                  <span class="ps-name">{{ s.concept_name }}</span>
                  <span class="ps-ret" :class="{ neg: s.sector_5d_return < 0 }">5d {{ fmtPct(s.sector_5d_return) }}</span>
                </div>
                <table class="ps-table">
                  <thead><tr><th>代码</th><th>名称</th><th class="r">5日涨幅</th></tr></thead>
                  <tbody>
                    <tr v-for="m in s.stocks.slice(0, 5)" :key="m.stock_code">
                      <td>{{ m.stock_code }}</td>
                      <td>{{ m.stock_name }}</td>
                      <td class="r" :class="changeCls(m.stock_5d_return)">{{ fmtPct(m.stock_5d_return) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </template>
        </div>
      </div>
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
import { runPrescreen as runPrescreenApi, getWatchlist, type WatchlistSector } from '@/api/prescreen'
import { fmt, fmtPct, changeCls } from '@/utils/format'

const route = useRoute()
// board: custom 或 sector（同一组件复用）
const isCustom = computed(() => route.name === 'custom')

// ===== 模式 + 日期 =====
const mode = ref<'realtime' | 'history' | 'watchlist'>('realtime')
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

// ===== 盘前筛选 / watchlist =====
const watchlistSectors = ref<WatchlistSector[]>([])
const watchlistHint = ref('')
const watchlistLoading = ref(false)
const prescreening = ref(false)
const psModalShow = ref(false)
const psModalLoading = ref(false)
const psModalError = ref('')
const psModalData = ref<{ date?: string; sector_count?: number; stock_count?: number; sectors?: WatchlistSector[] } | null>(null)

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
  if (mode.value === 'watchlist') {
    // 盘前筛选模式：不调实时/历史看板，直接读 watchlist
    loadWatchlist()
    return
  }
  refresh()
  if (mode.value === 'realtime') checkSession()
}
function onDateChange() {
  stopPlay()
  if (mode.value === 'realtime') { autoFollow.value = true }
  if (mode.value === 'watchlist') { loadWatchlist(); return }
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

// ===== 盘前筛选：触发 + 加载 watchlist =====
async function runPrescreen() {
  if (prescreening.value) return
  prescreening.value = true
  psModalShow.value = true
  psModalLoading.value = true
  psModalError.value = ''
  psModalData.value = null
  try {
    const date = getDateVal()
    const data = await runPrescreenApi({ date: date || undefined })
    if (data.error) {
      psModalError.value = data.error
    } else {
      psModalData.value = data
      // 筛选成功后刷新 watchlist 视图（若当前在该模式）
      if (mode.value === 'watchlist') await loadWatchlist()
    }
  } catch (e: any) {
    psModalError.value = '筛选失败: ' + (e?.message || e)
  } finally {
    psModalLoading.value = false
    prescreening.value = false
  }
}

async function loadWatchlist() {
  watchlistLoading.value = true
  try {
    const data = await getWatchlist()
    if (data.error) {
      watchlistSectors.value = []
      watchlistHint.value = ''
      statusText.value = data.error
      statusCls.value = 'warn'
      return
    }
    watchlistSectors.value = data.sectors || []
    watchlistHint.value = `（${data.date} · ${data.sector_count} 板块）`
    statusText.value = `watchlist · ${data.date} · ${data.sector_count} 板块`
    statusCls.value = 'live'
  } catch (e: any) {
    watchlistSectors.value = []
    watchlistHint.value = ''
    statusText.value = '⚠ ' + (e?.message || e)
    statusCls.value = 'warn'
  } finally {
    watchlistLoading.value = false
  }
}
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

/* 盘前筛选按钮 */
.ps-btn {
  padding: 5px 14px; font-size: 13px; border-radius: 6px; cursor: pointer;
  border: 1px solid #059669; background: #059669; color: #fff; margin-left: auto;
}
.ps-btn:hover:not(:disabled) { background: #047857; }
.ps-btn:disabled { background: #9ca3af; border-color: #9ca3af; cursor: not-allowed; }

/* watchlist 网格（复用 sector-card 视觉） */
.watchlist-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px; }
.watchlist-grid .sector-card { background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.watchlist-grid .card-head {
  padding: 8px 12px; color: #fff; font-weight: 600; font-size: 13px;
  display: flex; justify-content: space-between; align-items: center;
}
.watchlist-grid .card-head .num {
  display: inline-block; width: 20px; height: 20px; line-height: 20px; text-align: center;
  background: rgba(255,255,255,0.3); border-radius: 50%; margin-right: 6px; font-size: 11px;
}
.watchlist-grid .card-head .meta { font-size: 11px; font-weight: normal; opacity: 0.9; }
.wl-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.wl-table th, .wl-table td { padding: 5px 10px; border-bottom: 1px solid #f3f4f6; }
.wl-table th { color: #9ca3af; font-weight: 500; text-align: left; }
.wl-table th.r, .wl-table td.r { text-align: right; }
.wl-table td.left { text-align: left; }
.wl-table .stock-name { max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 盘前筛选弹窗 */
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000;
  display: flex; align-items: flex-start; justify-content: center; padding: 40px 20px; overflow: auto;
}
.modal {
  background: #fff; border-radius: 10px; width: 100%; max-width: 820px;
  max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 8px 32px rgba(0,0,0,0.2);
}
.modal-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px; border-bottom: 1px solid #e5e7eb; }
.modal-head h2 { font-size: 15px; color: #1f2937; }
.modal-close { background: none; border: none; font-size: 18px; cursor: pointer; color: #9ca3af; padding: 0 4px; }
.modal-close:hover { color: #1f2937; }
.modal-body { padding: 16px 20px; overflow: auto; }
.empty-msg { text-align: center; color: #9ca3af; padding: 40px 0; font-size: 13px; }
.ps-summary { font-size: 13px; color: #374151; margin-bottom: 14px; }
.ps-list { display: flex; flex-direction: column; gap: 10px; }
.ps-sector { background: #f9fafb; border-radius: 6px; padding: 10px 12px; }
.ps-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ps-rank { display: inline-block; min-width: 20px; height: 20px; line-height: 20px; text-align: center; background: #f59e0b; color: #fff; border-radius: 50%; font-size: 11px; font-weight: 600; }
.ps-name { font-weight: 600; color: #111827; font-size: 13px; }
.ps-ret { margin-left: auto; font-size: 12px; color: #ef4444; font-weight: 600; }
.ps-ret.neg { color: #10b981; }
.ps-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.ps-table th, .ps-table td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #f3f4f6; }
.ps-table th { color: #9ca3af; font-weight: 500; }
.ps-table th.r, .ps-table td.r { text-align: right; }
</style>
