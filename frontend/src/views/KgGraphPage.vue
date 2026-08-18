<template>
  <div class="kg-page">
    <!-- 控制栏：视图切换 + 代码查询 + 重叠阈值 -->
    <div class="controls-bar">
      <div class="view-tabs">
        <button :class="{ active: view === 'projection' }" @click="switchView('projection')">🗺️ 板块族群</button>
        <button :class="{ active: view === 'star' }" @click="switchView('star')">⭐ 个股关联</button>
        <button :class="{ active: view === 'sector' }" @click="switchView('sector')">🧩 板块成分</button>
        <button :class="{ active: view === 'locate' }" @click="switchView('locate')">🎯 组合定位</button>
      </div>
      <template v-if="view === 'star' || view === 'sector'">
        <input v-model="queryCode" class="code-input" :placeholder="view === 'star' ? '股票代码，如 600519' : '板块代码，如 884091'"
               @keyup.enter="loadGraph" />
        <button class="btn-primary" @click="loadGraph" :disabled="loading">查询</button>
      </template>
      <template v-else-if="view === 'projection'">
        <label class="mini-label">重叠阈值</label>
        <input type="range" v-model.number="minJaccard" min="0.1" max="0.6" step="0.05" class="slider"
               @change="loadGraph" />
        <span class="mini-val">{{ minJaccard.toFixed(2) }}</span>
      </template>
      <template v-else>
        <select v-model="selGroup" class="group-select">
          <option value="">— 自选分组 —</option>
          <option v-for="g in groups" :key="g.id" :value="g.name">{{ g.name }}（{{ g.count }}）</option>
        </select>
        <span class="mini-label">或代码</span>
        <input v-model="codesInput" class="code-input codes" placeholder="逗号分隔，如 600519,000858（可不带后缀）"
               @keyup.enter="runLocate" />
        <label class="mini-label">最少命中</label>
        <select v-model.number="minHits" class="group-select slim">
          <option :value="1">1</option>
          <option :value="2">2</option>
          <option :value="3">3</option>
          <option :value="5">5</option>
        </select>
        <button class="btn-primary" @click="runLocate" :disabled="loading">定位</button>
        <label class="sort-toggle">
          <input type="radio" value="lift" v-model="order" @change="runLocate" /> 富集倍数
          <input type="radio" value="hits" v-model="order" @change="runLocate" /> 命中数
        </label>
      </template>
      <span class="stat-hint">{{ statusText }}</span>
    </div>

    <div class="main-area" v-if="view !== 'locate'">
      <!-- 图画布 -->
      <div ref="cyContainer" class="cy-canvas"></div>

      <!-- 信息侧栏 -->
      <div class="info-panel" v-if="selected">
        <div class="info-head">
          <b>{{ selected.label }}</b>
          <span class="info-kind">{{ kindLabel(selected.kind) }}</span>
        </div>
        <div class="info-rows">
          <div v-for="(v, k) in selected.info" :key="k" class="info-row">
            <span class="k">{{ k }}</span><span class="v">{{ v }}</span>
          </div>
        </div>
        <div class="info-tip">双击{{ selected.kind === 'sector' ? '板块' : '个股' }}可跳转展开</div>
      </div>
    </div>

    <!-- 组合定位结果表 -->
    <div class="locate-area" v-else>
      <el-table v-if="locateResult" :data="locateResult.sectors" height="100%" size="small"
                :header-cell-style="{ background: '#f1f5f9' }">
        <el-table-column label="#" type="index" width="44" />
        <el-table-column label="板块" min-width="150">
          <template #default="{ row }">
            <span class="sec-name" @click="jumpSector(row.sector_code)">{{ row.sector_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="64">
          <template #default="{ row }">
            <span class="type-tag" :class="row.sector_type">{{ row.sector_type === 'industry' ? '行业' : '概念' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="命中" prop="hits" width="70" sortable />
        <el-table-column label="组内占比" width="90">
          <template #default="{ row }">{{ (row.group_ratio * 100).toFixed(0) }}%</template>
        </el-table-column>
        <el-table-column label="板块成员" prop="members" width="86" sortable />
        <el-table-column label="富集倍数" width="92" sortable :sort-method="(a: any, b: any) => (a.lift || 0) - (b.lift || 0)">
          <template #default="{ row }"><b class="lift">{{ row.lift != null ? row.lift + '×' : '—' }}</b></template>
        </el-table-column>
        <el-table-column label="平均ρ" width="76">
          <template #default="{ row }">{{ row.avg_corr != null ? row.avg_corr.toFixed(2) : '—' }}</template>
        </el-table-column>
      </el-table>
      <div v-else class="locate-empty">
        选择<b>自选分组</b>或粘贴一批股票代码（如 MCP 选股结果），点「定位」<br />
        <span class="sub">富集倍数 = 组内命中率 ÷ 板块成员占全市场比例——消除"融资融券"类大基数板块的命中噪音，找真正异常聚集的小圈子</span>
      </div>
    </div>

    <!-- 族群图例（投影图） -->
    <div class="legend" v-if="view === 'projection' && legend.length">
      <span v-for="lg in legend" :key="lg.id" class="lg-item">
        <i :style="{ background: lg.color }"></i>族群{{ lg.id }}（{{ lg.size }}）
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import cytoscape, { type Core, type EventObject } from 'cytoscape'
import { getKgProjection, getKgStar, getKgSectorGraph, locateKgSectors, getKgLocateGroups, type LocateResult } from '@/api/kg'

// ===== 状态 =====
const view = ref<'projection' | 'star' | 'sector' | 'locate'>('projection')
const queryCode = ref('600519')
const minJaccard = ref(0.3)
const loading = ref(false)
const statusText = ref('')
const selected = ref<{ label: string; kind: string; info: Record<string, string> } | null>(null)
const legend = ref<{ id: number; size: number; color: string }[]>([])

// 组合定位
const groups = ref<{ id: string; name: string; count: number }[]>([])
const selGroup = ref('')
const codesInput = ref('')
const minHits = ref(2)
const order = ref<'lift' | 'hits'>('lift')
const locateResult = ref<LocateResult | null>(null)

const cyContainer = ref<HTMLElement>()
let cy: Core | null = null

// 族群色板（10 色，超过循环取模）
const PALETTE = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
                 '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#64748b']
const commColor = (id?: number) => (id == null ? '#94a3b8' : PALETTE[(id - 1) % PALETTE.length])

// ===== cytoscape 实例管理 =====
function destroyCy() {
  if (cy) { cy.destroy(); cy = null }
}

function renderCy(elements: { nodes: any[]; edges: any[] }, layoutName: string, isProjection: boolean) {
  destroyCy()
  if (!cyContainer.value) return
  const layoutOptions: any =
    layoutName === 'cose'
      ? { name: 'cose', nodeRepulsion: 6000, idealEdgeLength: 70, animate: false }
      : { name: 'concentric', concentric: (n: any) => (n.data('kind') === 'stock' ? 2 : 1), levelWidth: () => 1, animate: false }
  cy = cytoscape({
    container: cyContainer.value,
    elements,
    style: [
      { selector: 'node', style: {
        label: 'data(label)', 'font-size': 9, color: '#475569',
        'background-color': isProjection ? 'data(color)' : '#d0e3ff',
        'border-width': 2, 'border-color': isProjection ? 'data(color)' : '#3b82f6',
        width: 'mapData(size, 0, 100, 14, 44)', height: 'mapData(size, 0, 100, 14, 44)',
        'text-valign': 'bottom', 'text-margin-y': 4, 'min-zoomed-font-size': 8,
      }},
      { selector: 'node[kind="stock"]', style: { 'background-color': '#dbeafe', 'border-color': '#60a5fa' } },
      { selector: 'node[kind="stock_linked"]', style: { 'background-color': '#fde68a', 'border-color': '#f59e0b', 'border-style': 'dashed' } },
      { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#dc2626' } },
      { selector: 'edge', style: {
        width: 'mapData(weight, 0, 1, 1, 6)', 'line-color': '#cbd5e1', 'line-opacity': 0.7,
      }},
    ],
    layout: layoutOptions,
    wheelSensitivity: 0.25,
  })

  // 点击 → 信息面板
  cy.on('tap', 'node', (evt: EventObject) => {
    const d = evt.target.data()
    const info: Record<string, string> = {}
    for (const k of ['code', 'member_count', 'avg_corr', 'community', 'corr', 'confidence', 'score', 'shared', 'sector_type']) {
      if (d[k] !== undefined && d[k] !== null) info[infoKey(k)] = String(d[k])
    }
    selected.value = { label: d.label, kind: d.kind, info }
  })
  cy.on('tap', (evt: EventObject) => { if (evt.target === cy) selected.value = null })

  // 双击 → 视图跳转（板块↔成分图 / 个股↔星型图）
  cy.on('dblclick', 'node', (evt: EventObject) => {
    const d = evt.target.data()
    const code: string = d.code || String(d.id).split(':')[1]
    if (d.kind === 'sector') { queryCode.value = code; switchView('sector') }
    else if (d.kind === 'stock') { queryCode.value = code; switchView('star') }
  })
}

function infoKey(k: string): string {
  return ({ code: '代码', member_count: '成分股数', avg_corr: '平均|ρ|', community: '族群',
            corr: 'ρ(20日)', confidence: '可信度', score: '联动分', shared: '共享板块',
            sector_type: '类型' } as Record<string, string>)[k] || k
}
function kindLabel(kind: string): string {
  return ({ sector: '板块', stock: '个股', stock_linked: '联动股' } as Record<string, string>)[kind] || kind
}

// ===== 数据加载 =====
async function loadGraph() {
  loading.value = true
  selected.value = null
  try {
    if (view.value === 'projection') {
      statusText.value = '加载板块投影图…'
      const d = await getKgProjection(minJaccard.value)
      const els = {
        nodes: d.elements.nodes.map(n => ({
          data: {
            ...n.data,
            color: commColor(n.data.community as number | undefined),
            size: Math.sqrt((n.data.member_count as number) || 0) * 4,
          },
        })),
        edges: d.elements.edges,
      }
      // 图例：族群统计
      const commCount: Record<number, number> = {}
      for (const n of d.elements.nodes) {
        const c = n.data.community as number | undefined
        if (c !== undefined) commCount[c] = (commCount[c] || 0) + 1
      }
      legend.value = Object.entries(commCount)
        .map(([id, size]) => ({ id: Number(id), size, color: commColor(Number(id)) }))
        .sort((a, b) => b.size - a.size).slice(0, 10)
      renderCy(els, 'cose', true)
      statusText.value = `${d.node_count} 板块 · ${d.edge_count} 条重叠边`
    } else if (view.value === 'star') {
      statusText.value = '加载个股关联图…'
      const d = await getKgStar(queryCode.value.trim())
      if ((d as any).error) { statusText.value = (d as any).error; return }
      renderCy(d.elements, 'concentric', false)
      statusText.value = `${queryCode.value.trim()} · ${d.elements.nodes.length} 节点（板块按|ρ|排，外圈为联动股）`
    } else {
      statusText.value = '加载板块成分图…'
      const d = await getKgSectorGraph(queryCode.value.trim().toUpperCase())
      if ((d as any).error) { statusText.value = (d as any).error; return }
      renderCy(d.elements, 'concentric', false)
      statusText.value = `${queryCode.value.trim()} · 展示 ${d.shown}/${d.total_members} 只成分股（按|ρ|）`
    }
  } catch (e: any) {
    statusText.value = '⚠ ' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

function switchView(v: 'projection' | 'star' | 'sector' | 'locate') {
  if (view.value === v) return
  view.value = v
  selected.value = null
  legend.value = []
  if (v === 'locate') {
    destroyCy()
    if (!groups.value.length) loadGroups()
    statusText.value = locateResult.value
      ? `${locateResult.value.source} · ${locateResult.value.total} 只 → ${locateResult.value.sectors.length} 个板块`
      : ''
  } else {
    loadGraph()
  }
}

// ===== 组合定位 =====
async function loadGroups() {
  try {
    const d = await getKgLocateGroups()
    groups.value = d.groups
  } catch { /* 下拉加载失败不阻塞页面 */ }
}

async function runLocate() {
  const codes = codesInput.value.trim()
  const group = selGroup.value.trim()
  if (!codes && !group) { statusText.value = '⚠ 请选择自选分组或输入股票代码'; return }
  loading.value = true
  try {
    const d = await locateKgSectors(codes ? { codes, min_hits: minHits.value, order: order.value }
                                          : { group, min_hits: minHits.value, order: order.value })
    locateResult.value = d
    const unres = d.unresolved.length ? ` · ${d.unresolved.length} 只未入图谱` : ''
    statusText.value = `${d.source} · 命中 ${d.matched}/${d.total}${unres} · 按${order.value === 'lift' ? '富集倍数' : '命中数'}排序`
  } catch (e: any) {
    statusText.value = '⚠ ' + (e?.response?.data?.error || e?.message || e)
  } finally {
    loading.value = false
  }
}

function jumpSector(code: string) {
  queryCode.value = code
  switchView('sector')
}


onMounted(async () => {
  await nextTick()
  loadGraph()
})
onBeforeUnmount(destroyCy)
</script>

<style scoped>
.kg-page { display: flex; flex-direction: column; height: 100%; background: #fff; overflow: hidden; }
.controls-bar {
  display: flex; align-items: center; gap: 12px; padding: 8px 16px;
  border-bottom: 1px solid #e5e7eb; flex-shrink: 0; flex-wrap: wrap;
}
.view-tabs { display: flex; gap: 6px; }
.view-tabs button {
  padding: 6px 14px; font-size: 13px; border: 1px solid #d1d5db; background: #fff;
  border-radius: 8px; cursor: pointer; color: #6b7280;
}
.view-tabs button.active { background: #1e40af; color: #fff; border-color: #1e40af; font-weight: 600; }
.code-input {
  font-size: 13px; padding: 6px 12px; border-radius: 8px; border: 1px solid #e5e7eb;
  background: #f9fafb; width: 220px; font-family: monospace;
}
.code-input:focus { outline: none; border-color: #1e40af; background: #fff; }
.btn-primary {
  padding: 6px 18px; font-size: 13px; border-radius: 8px; cursor: pointer;
  border: 1px solid #059669; background: #059669; color: #fff; font-weight: 600;
}
.btn-primary:disabled { background: #d1d5db; border-color: #d1d5db; cursor: not-allowed; }
.mini-label { font-size: 12px; color: #6b7280; }
.slider { width: 120px; }
.mini-val { font-size: 12px; color: #1e40af; font-weight: 600; width: 32px; }
.stat-hint { margin-left: auto; font-size: 12px; color: #6b7280; }
.main-area { flex: 1; display: flex; min-height: 0; }
.cy-canvas { flex: 1; min-width: 0; }
.info-panel {
  width: 240px; border-left: 1px solid #e5e7eb; padding: 14px; overflow: auto; flex-shrink: 0;
  background: #f8fafc;
}
.info-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 15px; }
.info-kind {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: #dbeafe; color: #1e40af;
}
.info-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; border-bottom: 1px dashed #e5e7eb; }
.info-row .k { color: #6b7280; }
.info-row .v { color: #1f2937; font-weight: 500; }
.info-tip { margin-top: 10px; font-size: 11px; color: #9ca3af; }
.legend {
  display: flex; gap: 14px; padding: 6px 16px; border-top: 1px solid #e5e7eb;
  font-size: 12px; color: #6b7280; flex-shrink: 0; flex-wrap: wrap;
}
.lg-item { display: flex; align-items: center; gap: 4px; }
.lg-item i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
/* ===== 组合定位 ===== */
.group-select {
  font-size: 13px; padding: 6px 8px; border-radius: 8px; border: 1px solid #e5e7eb;
  background: #f9fafb; max-width: 180px;
}
.group-select.slim { width: 64px; }
.code-input.codes { width: 300px; }
.sort-toggle { display: flex; align-items: center; gap: 2px; font-size: 12px; color: #6b7280; }
.sort-toggle input { margin: 0 2px 0 8px; accent-color: #1e40af; }
.locate-area { flex: 1; min-height: 0; padding: 12px 16px; overflow: auto; }
.locate-empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; color: #6b7280; font-size: 14px; background: #f8fafc; border-radius: 10px;
}
.locate-empty .sub { font-size: 12px; color: #9ca3af; max-width: 640px; text-align: center; line-height: 1.7; }
.sec-name { color: #1e40af; cursor: pointer; font-weight: 500; }
.sec-name:hover { text-decoration: underline; }
.type-tag {
  font-size: 11px; padding: 1px 8px; border-radius: 10px;
}
.type-tag.industry { background: #fef3c7; color: #92400e; }
.type-tag.concept { background: #dbeafe; color: #1e40af; }
.lift { color: #dc2626; font-size: 13px; }
</style>
