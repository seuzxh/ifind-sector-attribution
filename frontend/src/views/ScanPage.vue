<template>
  <div class="scan-page">
    <!-- 选股条件表单 -->
    <div class="scan-filter">
      <div class="filter-row">
        <select v-model="selectedKey" class="preset-sel" @change="onPresetChange">
          <option value="">— 选择条件 —</option>
          <optgroup label="预置">
            <option v-for="(p, i) in presets" :key="i" :value="String(i)">
              {{ presetLabels[String(i)] || p.slice(0, 24) }}
            </option>
          </optgroup>
          <optgroup v-if="customQueries.length" label="自定义">
            <option v-for="(c, i) in customQueries" :key="'c'+i" :value="'custom:'+i">
              {{ c.label || c.query.slice(0, 20) }}
            </option>
          </optgroup>
        </select>
        <input v-model="queryInput" type="text" class="query-input"
               placeholder="输入自然语言选股条件，如「涨幅大于7%并且小于12.1%；未涨停；非ST」" />
        <button class="btn primary" @click="applyScan" :disabled="loading">🔍 选股归类</button>
        <button class="btn" @click="saveCustom">💾 存为条件</button>
        <button class="btn" @click="renameItem">✏️ 重命名</button>
        <button class="btn" @click="deleteCustom">🗑 删除</button>
      </div>
      <div class="scan-hint">{{ boardHint }}</div>
    </div>

    <!-- 状态 -->
    <div class="status-bar" :class="statusCls" v-if="statusText">{{ statusText }}</div>

    <!-- 结果：手风琴 -->
    <div class="scan-result">
      <div v-if="loading" class="empty-msg">选股归类中…（调用 MCP 选股，约需数秒）</div>
      <div v-else-if="error" class="empty-msg warn">⚠ {{ error }}</div>
      <div v-else-if="!groups.length" class="empty-msg">当前条件下无命中股票，可放宽筛选条件</div>
      <div v-else class="scan-summary">
        共命中 <b>{{ payload?.hit_total ?? 0 }}</b> 只股票，归类到 <b>{{ payload?.group_hit_count ?? 0 }}</b> 个{{ groupName }}
      </div>
      <div v-for="(g, i) in groups" :key="g.group_id" class="scan-group" :class="{ open: expanded.has(g.group_id) }">
        <div class="scan-group-head" @click="toggle(g.group_id)">
          <span class="arrow">▶</span>
          <span class="gname">#{{ i + 1 }} {{ g.group_name }}</span>
          <span class="gstat">
            命中 <b>{{ g.hit_count }}</b>/{{ g.member_total }}
            <span class="cov-bar"><i :style="{ width: Math.min(100, g.coverage * 100 * 2) + '%' }"></i></span>
            {{ (g.coverage * 100).toFixed(1) }}%
          </span>
          <span class="gstat">均涨 <b class="up-text">{{ fmt(g.hit_avg_change) }}%</b></span>
        </div>
        <div class="scan-group-body">
          <div class="hit-chips">
            <span v-for="h in g.hits" :key="h.code" class="hit-chip" :title="`${h.name} ${h.code}`">
              <b class="up-text">{{ fmt(h.change_ratio) }}%</b> {{ h.name || h.code }}
            </span>
            <span v-if="!g.hits || g.hits.length === 0" class="faint">无</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { scanCustomGroups, scanMarketGroups, type ScanPayload, type ScanGroup } from '@/api/scan'
import { fmt } from '@/utils/format'

const route = useRoute()
const isMarket = computed(() => route.name === 'market_scan')

// ===== 预置条件 =====
const presets = [
  '实体涨幅大于3%或最大涨幅大于3%；成交额大于6亿',
  '实体涨幅大于3%或最大涨幅大于3%；成交金额大于10亿',
  '成交金额大于20亿；且实体涨幅大于4%或最大涨幅大于4%',
  '涨幅大于7%并且小于12.1%；未涨停；非ST',
]
// localStorage：自定义查询 [{label, query}]、预置重命名 {idx: label}
const CUSTOM_KEY = 'market_scan_custom_queries'
const PRESET_LABELS_KEY = 'market_scan_preset_labels'
const customQueries = ref<{ label: string; query: string }[]>(loadCustom())
const presetLabels = ref<Record<string, string>>(loadPresetLabels())

function loadCustom(): { label: string; query: string }[] {
  try { return JSON.parse(localStorage.getItem(CUSTOM_KEY) || '[]') } catch { return [] }
}
function persistCustom() {
  localStorage.setItem(CUSTOM_KEY, JSON.stringify(customQueries.value))
}
function loadPresetLabels(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(PRESET_LABELS_KEY) || '{}') } catch { return {} }
}
function savePresetLabels() {
  localStorage.setItem(PRESET_LABELS_KEY, JSON.stringify(presetLabels.value))
}

// ===== 表单状态 =====
const selectedKey = ref('3')       // 默认预置第 3 条
const queryInput = ref(presets[3])
const loading = ref(false)
const payload = ref<ScanPayload | null>(null)
const error = ref('')
const statusText = ref('')
const statusCls = ref('')
const expanded = ref<Set<string>>(new Set())  // 展开的分组（内存级）

const groups = computed<ScanGroup[]>(() => payload.value?.groups || [])
const groupName = computed(() => isMarket.value ? '板块' : '分组')
const boardHint = computed(() =>
  isMarket.value
    ? '（全市场强势归类：自然语言选股 → 按 884 概念板块归类，点"选股归类"触发）'
    : '（自选强势归类：自然语言选股 → 取自选交集 → 按自选分组归类，点"选股归类"触发）'
)

// ===== 预置/自定义切换 =====
function onPresetChange() {
  const v = selectedKey.value
  if (v.startsWith('custom:')) {
    queryInput.value = customQueries.value[Number(v.slice(7))]?.query || ''
  } else if (v) {
    queryInput.value = presets[Number(v)]
  }
}

// ===== 执行选股归类 =====
async function applyScan() {
  const q = queryInput.value.trim()
  if (!q) { ElMessage.warning('请输入选股条件'); return }
  loading.value = true; error.value = ''; payload.value = null
  statusText.value = '选股归类中…'; statusCls.value = ''
  try {
    const data = isMarket.value ? await scanMarketGroups(q) : await scanCustomGroups(q)
    if (data.error) { error.value = data.error; statusText.value = ''; }
    else {
      payload.value = data
      statusText.value = `${isMarket.value ? '全市场' : '自选'} · 命中 ${data.hit_total ?? 0} 只 → ${data.group_hit_count ?? 0} 个${groupName.value}`
      statusCls.value = 'live'
    }
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

// ===== 存为自定义条件 =====
function saveCustom() {
  const q = queryInput.value.trim()
  if (!q) { ElMessage.warning('请先输入条件'); return }
  if (customQueries.value.some(c => c.query === q)) { ElMessage.info('该条件已存在'); return }
  const label = q.slice(0, 16) + (q.length > 16 ? '...' : '')
  customQueries.value.push({ label, query: q })
  persistCustom()
  selectedKey.value = 'custom:' + (customQueries.value.length - 1)
  ElMessage.success('已保存')
}

// ===== 重命名 =====
async function renameItem() {
  const v = selectedKey.value
  if (!v) { ElMessage.warning('请先选择一个条件'); return }
  try {
    const { value } = await ElMessageBox.prompt('输入新名称', '重命名', { inputValue: '' })
    if (!value) return
    if (v.startsWith('custom:')) {
      customQueries.value[Number(v.slice(7))].label = value
      persistCustom()
    } else {
      presetLabels.value[v] = value
      savePresetLabels()
    }
    ElMessage.success('已重命名')
  } catch { /* 取消 */ }
}

// ===== 删除自定义 =====
function deleteCustom() {
  const v = selectedKey.value
  if (!v.startsWith('custom:')) { ElMessage.warning('预置条件不可删除'); return }
  customQueries.value.splice(Number(v.slice(7)), 1)
  persistCustom()
  selectedKey.value = ''
  ElMessage.success('已删除')
}

// ===== 手风琴展开 =====
function toggle(gid: string) {
  if (expanded.value.has(gid)) expanded.value.delete(gid)
  else expanded.value.add(gid)
  // 触发响应式（Set 需重新赋值）
  expanded.value = new Set(expanded.value)
}

onMounted(() => {
  // 不自动触发（选股是主动操作）
})
</script>

<style scoped>
.scan-page { display: flex; flex-direction: column; height: 100%; overflow: auto; padding: 16px; gap: 12px; }
.scan-filter { background: #fff; border-radius: 8px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.filter-row { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }
.preset-sel { font-size: 13px; padding: 6px 10px; border-radius: 6px; border: 1px solid #d1d5db; max-width: 200px; }
.query-input { flex: 1; min-width: 280px; font-size: 13px; padding: 7px 12px; border-radius: 6px; border: 1px solid #d1d5db; font-family: inherit; }
.query-input:focus { outline: none; border-color: #1e40af; }
.btn { padding: 6px 12px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; font-size: 12px; cursor: pointer; color: #374151; white-space: nowrap; }
.btn:hover { background: #f9fafb; }
.btn.primary { background: #1e40af; color: #fff; border-color: #1e40af; }
.btn.primary:hover { background: #1e3a8a; }
.btn.primary:disabled { background: #9ca3af; border-color: #9ca3af; cursor: not-allowed; }
.scan-hint { font-size: 11px; color: #9ca3af; margin-top: 8px; }
.status-bar { padding: 6px 16px; font-size: 13px; background: #fff; border-radius: 6px; }
.status-bar.live { color: #059669; }
.scan-summary { font-size: 13px; color: #374151; padding: 4px 0; }
.scan-summary b { color: #1e40af; }
.scan-group { border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; background: #fff; }
.scan-group-head { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #f9fafb; cursor: pointer; flex-wrap: wrap; }
.scan-group-head:hover { background: #f3f4f6; }
.arrow { display: inline-block; transition: transform 0.15s; color: #6b7280; font-size: 10px; }
.scan-group.open .arrow { transform: rotate(90deg); }
.gname { font-weight: 600; color: #1f2937; min-width: 120px; }
.gstat { font-size: 12px; color: #6b7280; display: flex; align-items: center; gap: 6px; }
.gstat b { color: #1f2937; }
.cov-bar { display: inline-block; width: 60px; height: 6px; background: #fee2e2; border-radius: 3px; overflow: hidden; vertical-align: middle; }
.cov-bar i { display: block; height: 100%; background: #ef4444; }
.up-text { color: #ef4444; }
.scan-group-body { display: none; padding: 12px 14px; border-top: 1px solid #f3f4f6; }
.scan-group.open .scan-group-body { display: block; }
.hit-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.hit-chip { display: inline-block; padding: 3px 8px; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; font-size: 12px; color: #374151; cursor: default; transition: background 0.1s; }
.hit-chip:hover { background: #fef9c3; }
.hit-chip b { font-weight: 600; }
.faint { color: #9ca3af; font-size: 12px; }
.empty-msg { text-align: center; color: #9ca3af; padding: 32px; }
.empty-msg.warn { color: #d97706; }
</style>
