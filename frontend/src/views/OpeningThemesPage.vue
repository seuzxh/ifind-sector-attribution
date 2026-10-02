<template>
  <div class="opening-page" :aria-busy="loading">
    <div class="controls-bar">
      <label for="opening-mode">模式</label>
      <select id="opening-mode" v-model="mode" aria-label="模式" @change="changeContext">
        <option value="realtime">实时（盘中）</option><option value="historical">历史回放</option>
      </select>
      <label for="opening-date">日期</label>
      <input id="opening-date" v-model="date" type="date" :disabled="mode === 'realtime'" @change="changeContext" />
      <span class="date-hint">{{ mode === 'realtime' ? '中国标准时间 · 每3秒自动跟随' : '使用当日盘前冻结归因' }}</span>
      <button class="refresh-btn" @click="refresh">刷新</button>
    </div>
    <TimeBar :available-times="availableTimes" :current-index="sliderIndex" :current-time-text="currentTimeText"
      :auto-follow="autoFollow" :playing="playing" :speed-ms="speedMs"
      @update:current-index="dragTo" @slider-change="seekTo" @toggle-play="togglePlay"
      @speed-change="setSpeed" @jump-to-latest="jumpToLatest" />
    <div role="status" aria-live="polite" class="status-bar" :class="{ warning: errorCode || degraded }">
      <span v-if="loading">加载中… </span>
      <template v-if="errorCode === 'SNAPSHOT_NOT_FOUND'">
        该日期尚未生成盘前冻结快照。请由维护人员运行：
        <code>python main.py opening-premarket --date {{ date.replace(/-/g, '') }}</code>
      </template>
      <template v-else-if="errorCode === 'QUOTE_DATA_UNAVAILABLE' || errorCode === 'QUOTE_PROVIDER_FAILED'">行情暂不可用，请稍后刷新或选择其他日期、时点。</template>
      <template v-else-if="errorCode">请求失败，请稍后刷新。</template>
      <template v-else-if="payload">
        {{ degraded ? '部分数据降级' : '数据正常' }} · 行情 {{ payload.snapshot_time }}
        <span v-if="payload.cache_status === 'stale'"> · 正在使用最近一次可用行情</span>
        <span v-else-if="payload.data_health < 1"> · 部分个股暂无行情</span>
      </template>
    </div>
    <div v-if="payload" class="summary-bar">
      <span>候选股票 <strong>{{ payload.candidate_count }}</strong></span>
      <span>题材 <strong>{{ payload.theme_count }}</strong></span>
      <span>数据健康 <strong>{{ healthText }}</strong></span>
      <span class="version">冻结版本 <strong>{{ payload.run_id }}</strong></span>
    </div>
    <div class="opening-grid">
      <OpeningThemeTable :themes="payload?.themes || []" :selected-code="selectedCode" @select="selectedCode = $event" />
      <OpeningChangeLists :themes="payload?.themes || []" :acceleration-codes="payload?.acceleration_theme_codes || []"
        :breadth-codes="payload?.breadth_theme_codes || []" :selected-code="selectedCode" @select="selectedCode = $event" />
      <OpeningContributors class="full-width" :theme="selectedTheme" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import TimeBar from '@/components/dashboard/TimeBar.vue'
import OpeningThemeTable from '@/components/opening-themes/OpeningThemeTable.vue'
import OpeningChangeLists from '@/components/opening-themes/OpeningChangeLists.vue'
import OpeningContributors from '@/components/opening-themes/OpeningContributors.vue'
import { getOpeningThemesDashboard, type OpeningDashboardErrorPayload, type OpeningDashboardPayload } from '@/api/openingThemes'
import { usePolling } from '@/composables/usePolling'
import { usePlayTimeline } from '@/composables/usePlayTimeline'

function shanghaiDate() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const part = (type: string) => parts.find(value => value.type === type)!.value
  return `${part('year')}-${part('month')}-${part('day')}`
}
const mode = ref<'realtime' | 'historical'>('realtime')
const date = ref(shanghaiDate())
const payload = ref<OpeningDashboardPayload | null>(null)
const availableTimes = ref<string[]>([])
const selectedCode = ref('')
const selectedMinute = ref<string>()
const autoFollow = ref(true)
const loading = ref(true)
const errorCode = ref('')
let active = false
const selectedTheme = computed(() => payload.value?.themes.find(theme => theme.theme_code === selectedCode.value))
const degraded = computed(() => !!payload.value && (payload.value.data_health < 1 || payload.value.cache_status === 'stale'))
const healthText = computed(() => `${((payload.value?.data_health || 0) * 100).toFixed(2)}%`)
const currentTimeText = computed(() => availableTimes.value[sliderIndex.value] || payload.value?.snapshot_time || '--:--')

async function loadDashboard(seq: number) {
  if (!active) return
  if (mode.value === 'realtime' && date.value !== shanghaiDate()) {
    date.value = shanghaiDate()
    clearTimeline()
    payload.value = null
  }
  if (!date.value) {
    errorCode.value = 'INVALID_REQUEST'
    payload.value = null
    loading.value = false
    return
  }
  loading.value = true
  const params: { trade_date: string; snapshot_time?: string } = { trade_date: date.value.replace(/-/g, '') }
  if (selectedMinute.value) params.snapshot_time = selectedMinute.value
  try {
    const data = await getOpeningThemesDashboard(params)
    if (!active || seq !== currentSeq()) return
    payload.value = data
    errorCode.value = ''
    availableTimes.value = data.available_times
    if (!playing.value) jumpTo(Math.max(0, data.available_times.indexOf(data.snapshot_time)))
    if (!data.themes.some(theme => theme.theme_code === selectedCode.value)) selectedCode.value = data.themes[0]?.theme_code || ''
  } catch (error: unknown) {
    if (!active || seq !== currentSeq()) return
    const response = error as { response?: { data?: OpeningDashboardErrorPayload } }
    errorCode.value = response.response?.data?.error?.code || 'REQUEST_FAILED'
    payload.value = null
    stopPlay()
  } finally {
    if (active && seq === currentSeq()) loading.value = false
  }
}
const { start: startPolling, stop: stopPolling, triggerNow: refresh, currentSeq, nextSeq } = usePolling(loadDashboard, 3000, {
  shouldTick: () => active && mode.value === 'realtime' && autoFollow.value,
})
const { playing, speedMs, currentIndex: sliderIndex, start: startPlay, stop: stopPlay, setSpeed, jumpTo } = usePlayTimeline({
  max: () => availableTimes.value.length - 1,
  onStep: index => { selectedMinute.value = availableTimes.value[index]; void refresh() },
})
function syncPolling() {
  if (active && mode.value === 'realtime' && autoFollow.value) startPolling()
  else stopPolling()
}
function clearTimeline() {
  stopPlay()
  availableTimes.value = []
  selectedMinute.value = undefined
  jumpTo(0)
}
function changeContext() {
  nextSeq()
  clearTimeline()
  payload.value = null
  errorCode.value = ''
  if (mode.value === 'realtime') date.value = shanghaiDate()
  autoFollow.value = mode.value === 'realtime'
  syncPolling()
  void refresh()
}
function dragTo(index: number) {
  stopPlay()
  autoFollow.value = false
  stopPolling()
  nextSeq()
  loading.value = false
  jumpTo(index)
  selectedMinute.value = availableTimes.value[index]
}
function seekTo(index: number) { dragTo(index); void refresh() }
function togglePlay() {
  if (playing.value) { stopPlay(); return }
  if (!availableTimes.value.length) return
  autoFollow.value = false
  stopPolling()
  // The shared player steps immediately; -1 includes the first minute on restart.
  if (sliderIndex.value >= availableTimes.value.length - 1) jumpTo(-1)
  startPlay()
}
function jumpToLatest() {
  stopPlay()
  selectedMinute.value = undefined
  autoFollow.value = mode.value === 'realtime'
  syncPolling()
  void refresh()
}
function activate() {
  // Both hooks fire on first insertion under KeepAlive.
  if (active) return
  active = true
  syncPolling()
  void refresh()
}
function deactivate() {
  active = false
  stopPolling()
  stopPlay()
  nextSeq()
  loading.value = false
}
onMounted(activate)
onActivated(activate)
onDeactivated(deactivate)
onUnmounted(deactivate)
</script>

<style scoped>
.opening-page { min-height: 100%; }
.controls-bar { display: flex; align-items: center; gap: 10px; padding: 8px 16px; background: #fff; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
label { font-size: 13px; color: #6b7280; font-weight: 600; }
select, input, .refresh-btn { font: inherit; font-size: 13px; padding: 5px 10px; border-radius: 6px; border: 1px solid #d1d5db; }
input:disabled { color: #6b7280; background: #f9fafb; } .date-hint { font-size: 11px; color: #6b7280; }
.refresh-btn { color: #1e40af; background: #fff; cursor: pointer; }
.status-bar { padding: 8px 16px; background: #fff; color: #059669; font-size: 13px; border-bottom: 1px solid #e5e7eb; }
.status-bar.warning { color: #b45309; } code { display: inline-block; user-select: all; padding: 5px 8px; border-radius: 4px; background: #fffbeb; overflow-wrap: anywhere; }
.summary-bar { display: flex; gap: 20px; flex-wrap: wrap; background: #fff; padding: 10px 16px; color: #6b7280; font-size: 12px; }
.summary-bar strong { margin-left: 6px; color: #1f2937; } .version { overflow-wrap: anywhere; }
.opening-grid { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 16px; padding: 16px; align-items: start; }
.full-width { grid-column: 1 / -1; }
@media (max-width: 1100px) { .opening-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
