<template>
  <div class="sector-manage-page">
    <!-- 控制栏：层级筛选 + 搜索 + 保存 -->
    <div class="controls-bar">
      <label>层级</label>
      <select v-model="levelFilter" class="level-sel">
        <option value="">全部</option>
        <option value="884">三级行业</option>
        <option value="885">概念板块(885)</option>
        <option value="886">概念板块(886)</option>
      </select>
      <label>状态</label>
      <select v-model="watchFilter" class="level-sel">
        <option value="">全部</option>
        <option value="watched">已勾选</option>
        <option value="unwatched">未勾选</option>
      </select>
      <label>搜索</label>
      <input v-model="keyword" class="search-input" placeholder="代码 / 名称" />
      <span class="count-hint">
        已选 <b>{{ selectedCount }}</b> / 筛选 {{ filtered.length }} / 共 {{ allSectors.length }}
      </span>
      <button class="btn-select" @click="selectAllFiltered(true)">全选筛选</button>
      <button class="btn-select" @click="selectAllFiltered(false)">取消筛选</button>
      <button class="btn-refresh" @click="onRefresh" :disabled="refreshing">
        {{ refreshing ? '🔄 刷新中…' : '🔄 刷新板块' }}
      </button>
      <button class="btn-save" @click="onSave" :disabled="saving || dirty === 0">
        {{ saving ? '保存中…' : `💾 保存勾选${dirty ? ' *' : ''}` }}
      </button>
    </div>

    <!-- 状态栏 -->
    <div class="status-bar" :class="statusCls">{{ statusText }}</div>

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
          <col class="w-num" />
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
            <th class="col-num" @click="sortBy('return_3d')">3日<span class="arrow">{{ sortArrow('return_3d') }}</span></th>
            <th class="col-num" @click="sortBy('return_5d')">5日<span class="arrow">{{ sortArrow('return_5d') }}</span></th>
            <th class="col-num" @click="sortBy('member_count')">成分股<span class="arrow">{{ sortArrow('member_count') }}</span></th>
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
            <td class="col-num" :class="changeCls(s.return_3d)">{{ fmt(s.return_3d) }}%</td>
            <td class="col-num" :class="changeCls(s.return_5d)">{{ fmt(s.return_5d) }}%</td>
            <td class="col-num">{{ s.member_count }}</td>
            <td class="col-level">{{ s.level }}</td>
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
})

onUnmounted(stopPollingRefresh)
</script>

<style scoped>
.sector-manage-page { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.controls-bar {
  display: flex; align-items: center; gap: 10px; padding: 8px 16px; flex-wrap: wrap;
  background: #fff; border-bottom: 1px solid #e5e7eb;
}
.controls-bar label { font-size: 13px; color: #6b7280; font-weight: 600; }
.level-sel, .search-input {
  font-size: 13px; padding: 5px 10px; border-radius: 6px; border: 1px solid #d1d5db; font-family: inherit;
}
.search-input { min-width: 160px; }
.level-sel:focus, .search-input:focus { outline: none; border-color: #1e40af; }
.count-hint { font-size: 12px; color: #6b7280; margin-left: auto; }
.count-hint b { color: #1e40af; }
.btn-select {
  padding: 5px 12px; font-size: 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid #d1d5db; background: #fff; color: #374151;
}
.btn-select:hover { background: #f3f4f6; }
.btn-refresh {
  padding: 5px 12px; font-size: 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid #2563eb; background: #fff; color: #2563eb;
}
.btn-refresh:hover:not(:disabled) { background: #eff6ff; }
.btn-refresh:disabled { color: #9ca3af; border-color: #d1d5db; cursor: not-allowed; }
.btn-save {
  padding: 6px 16px; font-size: 13px; border-radius: 6px; cursor: pointer;
  border: 1px solid #059669; background: #059669; color: #fff; font-weight: 600;
}
.btn-save:hover:not(:disabled) { background: #047857; }
.btn-save:disabled { background: #9ca3af; border-color: #9ca3af; cursor: not-allowed; }
.status-bar { padding: 6px 16px; font-size: 13px; background: #fff; border-bottom: 1px solid #e5e7eb; }
.status-bar.live { color: #059669; }
.status-bar.warn { color: #d97706; }
.table-wrap { flex: 1; overflow: auto; padding: 0; }
.empty-msg { text-align: center; color: #9ca3af; padding: 40px 0; font-size: 13px; }
.sector-table { width: 100%; font-size: 12px; border-collapse: collapse; table-layout: fixed; }
/* colgroup 列宽（fixed 布局下表头与数据严格对齐） */
.w-check { width: 40px; }
.w-code { width: 110px; }
.w-name { width: auto; }      /* 名称列弹性，吸收剩余宽度 */
.w-num { width: 90px; }
.w-level { width: 90px; }
.sector-table th, .sector-table td { padding: 6px 10px; border-bottom: 1px solid #f3f4f6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sector-table th {
  background: #f9fafb; color: #6b7280; font-weight: 600; text-align: left;
  position: sticky; top: 0; z-index: 1; cursor: pointer; user-select: none;
}
.sector-table th:hover { color: #1e40af; }
.sector-table th .arrow { font-size: 10px; color: #1e40af; }
/* 数值列：表头与数据都右对齐 */
.sector-table th.col-num, .sector-table td.col-num { text-align: right; font-variant-numeric: tabular-nums; }
.sector-table td.col-code { color: #6b7280; font-family: monospace; }
.sector-table td.col-level { color: #9ca3af; font-size: 11px; }
.sector-table tr.selected { background: #eff6ff; }
.sector-table tr:hover { background: #f9fafb; }
.col-check { width: 40px; text-align: center; }
</style>
