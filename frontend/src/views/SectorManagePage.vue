<template>
  <div class="sector-manage-page">
    <!-- 控制栏：左侧筛选 + 右侧操作 -->
    <div class="controls-bar">
      <div class="filter-group">
        <select v-model="levelFilter" class="sel" title="按层级筛选">
          <option value="">全部层级</option>
          <option value="884">三级行业</option>
          <option value="885">概念板块(885)</option>
          <option value="886">概念板块(886)</option>
        </select>
        <select v-model="watchFilter" class="sel" title="按勾选状态筛选">
          <option value="">全部状态</option>
          <option value="watched">已勾选</option>
          <option value="unwatched">未勾选</option>
        </select>
        <input v-model="keyword" class="search-input" placeholder="🔍 搜索代码 / 名称" />
      </div>

      <div class="action-group">
        <span class="stat-pill" v-if="!refreshing && !loading">
          已选 <b>{{ selectedCount }}</b><span class="sep">·</span>筛选 {{ filtered.length }}<span class="sep">·</span>共 {{ allSectors.length }}
        </span>
        <button class="btn-ghost" @click="selectAllFiltered(true)">全选</button>
        <button class="btn-ghost" @click="selectAllFiltered(false)">取消</button>
        <button class="btn-outline" @click="onRefresh" :disabled="refreshing">
          {{ refreshing ? '⏳ 刷新中…' : '🔄 刷新板块' }}
        </button>
        <button class="btn-primary" @click="onSave" :disabled="saving || dirty === 0">
          {{ saving ? '保存中…' : `💾 保存${dirty ? ' ·' : ''}` }}
        </button>
      </div>
    </div>

    <!-- 状态栏（仅刷新/错误时显示，避免常态占行） -->
    <div v-if="statusText" class="status-bar" :class="statusCls">{{ statusText }}</div>

    <!-- 板块表格 -->
    <div class="table-wrap">
      <div v-if="loading" class="empty-msg">加载中...</div>
      <div v-else-if="!allSectors.length" class="empty-msg">暂无候选板块数据</div>
      <table v-else class="sector-table">
        <colgroup>
          <col class="w-check" />
          <col class="w-code" />
          <col class="w-name" />
          <col class="w-num" />
          <col class="w-num" />
          <col class="w-num-sm" />
          <col class="w-num-sm" />
          <col class="w-num-sm" />
          <col class="w-num" />
          <col class="w-num" />
          <col class="w-level" />
        </colgroup>
        <thead>
          <tr>
            <th class="col-check">
              <input type="checkbox" :checked="allFilteredSelected" :indeterminate.prop="someFilteredSelected"
                     @change="onToggleAll(($event.target as HTMLInputElement).checked)" />
            </th>
            <th class="col-code" @click="sortBy('concept_code')">代码<span class="arrow">{{ sortArrow('concept_code') }}</span></th>
            <th class="col-name" @click="sortBy('concept_name')">名称<span class="arrow">{{ sortArrow('concept_name') }}</span></th>
            <th class="col-num" @click="sortBy('change_ratio')">涨幅<span class="arrow">{{ sortArrow('change_ratio') }}</span></th>
            <th class="col-num" @click="sortBy('body')">实体<span class="arrow">{{ sortArrow('body') }}</span></th>
            <th class="col-num" @click="sortBy('rise_count')">涨<span class="arrow">{{ sortArrow('rise_count') }}</span></th>
            <th class="col-num" @click="sortBy('fall_count')">跌<span class="arrow">{{ sortArrow('fall_count') }}</span></th>
            <th class="col-num" @click="sortBy('limit_up_count')">涨停<span class="arrow">{{ sortArrow('limit_up_count') }}</span></th>
            <th class="col-num" @click="sortBy('return_3d')">3日<span class="arrow">{{ sortArrow('return_3d') }}</span></th>
            <th class="col-num" @click="sortBy('return_5d')">5日<span class="arrow">{{ sortArrow('return_5d') }}</span></th>
            <th class="col-level">层级</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in filtered" :key="s.concept_code" :class="{ selected: s.watched }">
            <td class="col-check">
              <input type="checkbox" v-model="s.watched" @change="markDirty" />
            </td>
            <td class="col-code">{{ s.concept_code }}</td>
            <td class="col-name" :title="s.concept_name">{{ s.concept_name }}</td>
            <td class="col-num" :class="changeCls(s.change_ratio)">{{ fmt(s.change_ratio) }}%</td>
            <td class="col-num" :class="changeCls(s.body)">{{ fmt(s.body) }}%</td>
            <td class="col-num up">{{ s.rise_count ?? '-' }}</td>
            <td class="col-num down">{{ s.fall_count ?? '-' }}</td>
            <td class="col-num up">{{ s.limit_up_count ?? '-' }}</td>
            <td class="col-num" :class="changeCls(s.return_3d)">{{ fmt(s.return_3d) }}%</td>
            <td class="col-num" :class="changeCls(s.return_5d)">{{ fmt(s.return_5d) }}%</td>
            <td class="col-level"><span class="level-tag" :class="s.level === '三级行业' ? 'tag-industry' : 'tag-concept'">{{ s.level }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSectorManageList, saveWatchedSectors, triggerRefresh, getRefreshStatus, type SectorCandidate } from '@/api/sectorManage'
import { fmt, changeCls } from '@/utils/format'

// ===== 数据 =====
const allSectors = ref<SectorCandidate[]>([])
const loading = ref(false)
const saving = ref(false)
const refreshing = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | null = null
const dirty = ref(0)            // 未保存改动计数（>0 表示有改动）
const statusText = ref('')
const statusCls = ref('')

// ===== 筛选 =====
const levelFilter = ref('')     // ''=全部, '884'/'885'/'886'
const watchFilter = ref('')     // ''=全部, 'watched'=已勾选, 'unwatched'=未勾选
const keyword = ref('')

const filtered = computed(() => {
  let list = allSectors.value
  if (levelFilter.value) {
    list = list.filter(s => s.concept_code.startsWith(levelFilter.value))
  }
  if (watchFilter.value === 'watched') {
    list = list.filter(s => s.watched)
  } else if (watchFilter.value === 'unwatched') {
    list = list.filter(s => !s.watched)
  }
  const kw = keyword.value.trim()
  if (kw) {
    list = list.filter(s => s.concept_code.includes(kw) || s.concept_name.includes(kw))
  }
  // 排序
  const { key, asc } = sortState.value
  return [...list].sort((a, b) => {
    const va = a[key as keyof SectorCandidate]
    const vb = b[key as keyof SectorCandidate]
    if (typeof va === 'number' && typeof vb === 'number') {
      return asc ? va - vb : vb - va
    }
    const sa = String(va ?? '')
    const sb = String(vb ?? '')
    return asc ? sa.localeCompare(sb) : sb.localeCompare(sa)
  })
})

// ===== 排序 =====
const sortState = ref<{ key: string; asc: boolean }>({ key: 'change_ratio', asc: false })
function sortBy(key: string) {
  if (sortState.value.key === key) {
    sortState.value.asc = !sortState.value.asc
  } else {
    sortState.value = { key, asc: false }
  }
}
function sortArrow(key: string): string {
  if (sortState.value.key !== key) return ''
  return sortState.value.asc ? ' ↑' : ' ↓'
}

// ===== 选择 =====
const selectedCount = computed(() => allSectors.value.filter(s => s.watched).length)
const allFilteredSelected = computed(() =>
  filtered.value.length > 0 && filtered.value.every(s => s.watched))
const someFilteredSelected = computed(() =>
  !allFilteredSelected.value && filtered.value.some(s => s.watched))

function onToggleAll(checked: boolean) {
  for (const s of filtered.value) {
    if (s.watched !== checked) { s.watched = checked; dirty.value++ }
  }
}
function selectAllFiltered(checked: boolean) {
  onToggleAll(checked)
}
function markDirty() { dirty.value++ }

// ===== 加载 =====
async function loadList() {
  loading.value = true
  statusText.value = '加载中…'; statusCls.value = ''
  try {
    const data = await getSectorManageList()
    if (data.error) {
      statusText.value = '⚠ ' + data.error; statusCls.value = 'warn'
      allSectors.value = []
    } else {
      allSectors.value = data.sectors || []
      statusText.value = `📅 ${data.date || ''} · 共 ${data.total} 个板块，已监控 ${data.watched_count} 个`
      statusCls.value = 'live'
      dirty.value = 0
    }
  } catch (e: any) {
    statusText.value = '⚠ 加载失败: ' + (e?.message || e); statusCls.value = 'warn'
  } finally {
    loading.value = false
  }
}

// ===== 行情刷新（仅更新行情数据，保留本地 watched 勾选状态）=====
let quoteTimer: ReturnType<typeof setInterval> | null = null

async function refreshQuotes() {
  try {
    const data = await getSectorManageList()
    if (data.error || !data.sectors) return
    // 以 concept_code 为键，只更新行情字段，保留本地 watched
    const map = new Map(data.sectors.map(s => [s.concept_code, s]))
    for (const row of allSectors.value) {
      const fresh = map.get(row.concept_code)
      if (!fresh) continue
      row.change_ratio = fresh.change_ratio
      row.body = fresh.body
      row.rise_count = fresh.rise_count
      row.fall_count = fresh.fall_count
      row.limit_up_count = fresh.limit_up_count
      row.return_3d = fresh.return_3d
      row.return_5d = fresh.return_5d
      // watched 不覆盖（保留本地未保存的勾选改动）
    }
    const now = new Date()
    const hh = String(now.getHours()).padStart(2, '0')
    const mm = String(now.getMinutes()).padStart(2, '0')
    statusText.value = `🔄 ${hh}:${mm} 行情已刷新 · 已监控 ${selectedCount.value} 个`; statusCls.value = 'live'
  } catch { /* 轮询失败静默，下次重试 */ }
}

function startQuotePolling() {
  if (quoteTimer) return
  quoteTimer = setInterval(refreshQuotes, 60000)  // 1 分钟
}
function stopQuotePolling() {
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
}

// ===== 保存 =====
async function onSave() {
  if (saving.value || dirty.value === 0) return
  saving.value = true
  try {
    const codes = allSectors.value.filter(s => s.watched).map(s => s.concept_code)
    const res = await saveWatchedSectors({ concept_codes: codes })
    if (res.ok) {
      ElMessage.success(`已保存 ${res.saved_count} 个监控板块`)
      dirty.value = 0
      statusText.value = `✅ 已保存 ${res.saved_count} 个监控板块`; statusCls.value = 'live'
    } else {
      ElMessage.error('保存失败')
    }
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.message || e))
  } finally {
    saving.value = false
  }
}

// ===== 刷新板块（后台线程拉取 iFinD 字典+成分股，约1-2分钟）=====
async function onRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  statusText.value = '🔄 正在从 iFinD 拉取最新板块字典+成分股（约1-2分钟）…'; statusCls.value = ''
  try {
    const res = await triggerRefresh()
    if (!res.ok) {
      // 已在刷新中：直接进入轮询
      ElMessage.info(res.reason)
    }
    startPollingRefresh()
  } catch (e: any) {
    refreshing.value = false
    statusText.value = '⚠ 刷新启动失败: ' + (e?.message || e); statusCls.value = 'warn'
  }
}

function startPollingRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = setInterval(async () => {
    try {
      const st = await getRefreshStatus()
      if (st.error) {
        stopPollingRefresh()
        refreshing.value = false
        statusText.value = '⚠ 刷新失败: ' + st.error; statusCls.value = 'warn'
        ElMessage.error('刷新失败: ' + st.error)
      } else if (st.done && st.result) {
        stopPollingRefresh()
        refreshing.value = false
        const r = st.result
        statusText.value = `✅ 刷新完成：字典 ${r.dict_count} 个，成分股 ${r.member_concepts} 板块 / ${r.saved_records} 条`
        statusCls.value = 'live'
        ElMessage.success(`板块信息已刷新（${r.member_concepts} 板块）`)
        await loadList()   // 重新加载列表展示新数据
      }
    } catch { /* 轮询失败忽略，下次重试 */ }
  }, 3000)
}

function stopPollingRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
}

onMounted(async () => {
  // 进页面先检查是否有正在进行的刷新（如服务重启后），有则恢复轮询
  try {
    const st = await getRefreshStatus()
    if (st.running) { refreshing.value = true; startPollingRefresh() }
  } catch { /* 忽略 */ }
  await loadList()
  startQuotePolling()   // 停留页面时每分钟刷新实时行情
})

onUnmounted(() => {
  stopPollingRefresh()
  stopQuotePolling()
})
</script>

<style scoped>
.sector-manage-page { display: flex; flex-direction: column; height: 100%; overflow: hidden; background: #fff; }

/* ===== 控制栏：左筛选区 + 右操作区 ===== */
.controls-bar {
  display: flex; align-items: center; gap: 20px; padding: 10px 16px;
  background: #fff; border-bottom: 1px solid #e5e7eb; flex-shrink: 0;
}
.filter-group { display: flex; align-items: center; gap: 8px; }
.action-group { display: flex; align-items: center; gap: 8px; margin-left: auto; }

.sel, .search-input {
  font-size: 13px; padding: 6px 12px; border-radius: 8px; border: 1px solid #e5e7eb;
  background: #f9fafb; color: #374151; font-family: inherit; cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.sel { padding-right: 28px; }
.sel:hover, .search-input:hover { border-color: #cbd5e1; }
.sel:focus, .search-input:focus { outline: none; border-color: var(--color-primary, #1e40af); background: #fff; }
.search-input { min-width: 200px; cursor: text; }

/* 统计胶囊 */
.stat-pill {
  font-size: 12px; color: #6b7280; background: #f3f4f6; padding: 5px 12px; border-radius: 16px;
  white-space: nowrap;
}
.stat-pill b { color: #1e40af; font-weight: 700; }
.stat-pill .sep { color: #d1d5db; margin: 0 4px; }

/* 按钮体系 */
.btn-ghost {
  padding: 6px 14px; font-size: 12px; border-radius: 8px; cursor: pointer;
  border: 1px solid #e5e7eb; background: #fff; color: #6b7280; transition: all 0.15s;
}
.btn-ghost:hover { background: #f9fafb; color: #374151; border-color: #cbd5e1; }
.btn-outline {
  padding: 6px 14px; font-size: 12px; border-radius: 8px; cursor: pointer;
  border: 1px solid #bfdbfe; background: #eff6ff; color: #1e40af; font-weight: 500; transition: all 0.15s;
}
.btn-outline:hover:not(:disabled) { background: #dbeafe; }
.btn-outline:disabled { color: #9ca3af; border-color: #e5e7eb; background: #f9fafb; cursor: not-allowed; }
.btn-primary {
  padding: 6px 18px; font-size: 13px; border-radius: 8px; cursor: pointer;
  border: 1px solid #059669; background: #059669; color: #fff; font-weight: 600; transition: all 0.15s;
}
.btn-primary:hover:not(:disabled) { background: #047857; box-shadow: 0 1px 4px rgba(5,150,105,0.3); }
.btn-primary:disabled { background: #d1d5db; border-color: #d1d5db; cursor: not-allowed; box-shadow: none; }

/* ===== 状态栏 ===== */
.status-bar { padding: 7px 16px; font-size: 12px; background: #fffbeb; border-bottom: 1px solid #fde68a; flex-shrink: 0; }
.status-bar.live { background: #ecfdf5; border-color: #a7f3d0; color: #059669; }
.status-bar.warn { background: #fffbeb; color: #d97706; }

/* ===== 表格 ===== */
.table-wrap { flex: 1; overflow: auto; }
.empty-msg { text-align: center; color: #9ca3af; padding: 60px 0; font-size: 14px; }
.sector-table { width: 100%; font-size: 13px; border-collapse: collapse; table-layout: fixed; }
.w-check { width: 44px; }
.w-code { width: 116px; }
.w-name { width: auto; }
.w-num { width: 92px; }
.w-num-sm { width: 64px; }
.w-level { width: 96px; }

.sector-table th, .sector-table td {
  padding: 9px 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sector-table th {
  background: #f8fafc; color: #475569; font-weight: 600; font-size: 12px; text-align: left;
  position: sticky; top: 0; z-index: 1; cursor: pointer; user-select: none;
  border-bottom: 2px solid #e2e8f0; letter-spacing: 0.02em;
}
.sector-table th:hover { color: #1e40af; background: #f1f5f9; }
.sector-table th .arrow { font-size: 10px; color: #1e40af; margin-left: 2px; }

/* 数值列：表头与数据统一右对齐 */
.sector-table th.col-num, .sector-table td.col-num { text-align: right; font-variant-numeric: tabular-nums; }
.sector-table td.col-code { color: #94a3b8; font-family: 'SF Mono', 'Consolas', monospace; font-size: 12px; }

/* 斑马纹（未勾选行）+ 勾选行高亮 */
.sector-table tbody tr:nth-child(even):not(.selected) { background: #fafbfc; }
.sector-table tbody tr.selected {
  background: #eff6ff;
  box-shadow: inset 3px 0 0 #3b82f6;  /* 左侧蓝色指示条 */
}
.sector-table tbody tr:hover:not(.selected) { background: #f1f5f9; }
.sector-table tbody tr.selected:hover { background: #e0edff; }
.col-check { width: 44px; text-align: center; }

/* 层级标签（pill） */
.col-level { text-align: center; }
.level-tag {
  display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 11px; font-weight: 500;
  white-space: nowrap;
}
.tag-industry { background: #ede9fe; color: #7c3aed; }   /* 三级行业 = 紫 */
.tag-concept { background: #f0fdf4; color: #16a34a; }     /* 概念板块 = 绿 */
</style>
