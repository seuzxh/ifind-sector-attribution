<template>
  <div class="chat-layout">
    <!-- 顶栏 -->
    <div class="topbar">
      <h1>🤖 AI 问答</h1>
      <span class="mode-tag" :class="modeCls">{{ modeText }}</span>
      <div class="model-group">
        <span class="mlabel">模型</span>
        <el-select v-model="currentModel" size="small" filterable placeholder="加载中…" @change="onModelChange" style="width: 200px;">
          <el-option v-for="m in models" :key="m.id" :value="m.id" :label="shortModelName(m.id) + (m.id === defaultModel ? '（默认）' : '')" />
        </el-select>
        <el-button size="small" @click="onResetModel">↺ 默认</el-button>
      </div>
    </div>

    <div class="main">
      <!-- 对话区 -->
      <div class="chat-col">
        <div class="messages" ref="messagesEl">
          <div v-for="(msg, i) in messages" :key="i" class="msg" :class="msg.role">
            <div class="bubble" :class="{ error: msg.error }" v-html="msg.html"></div>
          </div>
        </div>
        <div class="composer">
          <div class="tool-hint" v-if="selectedTool">
            🔧 手动查询：<b>{{ selectedTool.name }}</b> · 直接输入内容即可（再点该工具取消，回到自由对话）
          </div>
          <div class="input-row">
            <textarea v-model="input" ref="inputEl" rows="1" class="input"
                      :placeholder="inputPlaceholder"
                      @input="autoGrow" @keydown.enter.exact.prevent="send"></textarea>
            <button class="send-btn" @click="send" :disabled="sending">{{ sending ? '…' : '发送' }}</button>
          </div>
        </div>
      </div>

      <!-- 工具侧栏 -->
      <div class="tools-col">
        <div class="tools-head">可用工具 <span class="count">{{ tools.length }} 个</span></div>
        <div class="tools-list">
          <div v-if="!tools.length" class="tools-empty">加载中…</div>
          <div v-for="(t, i) in tools" :key="t.name + t.server" class="tool-item"
               :class="{ selected: selectedToolIndex === i }" @click="selectTool(i)">
            <div class="tname">{{ t.name }} <span class="tsrv">[{{ t.server }}]</span></div>
            <div class="tdesc">{{ (t.description || '').slice(0, 100) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getMcpTools, callMcpTool, listModels, switchModel, resetModel, streamChat, shortModelName,
  type McpTool,
} from '@/api/chat'
import { renderMarkdown } from '@/utils/markdown'

interface Msg { role: 'user' | 'assistant'; html: string; error?: boolean }

const messages = ref<Msg[]>([
  { role: 'assistant', html: '你好！我是 iFinD AI 助手。直接用自然语言提问，例如：<br>「同花顺最新估值水平」「宁德时代最近5日涨跌幅」「新能源车板块今日表现」' },
])
const input = ref('')
const sending = ref(false)
const messagesEl = ref<HTMLElement>()
const inputEl = ref<HTMLTextAreaElement>()

// 工具
const tools = ref<McpTool[]>([])
const selectedToolIndex = ref<number | null>(null)
const selectedTool = computed(() => selectedToolIndex.value !== null ? tools.value[selectedToolIndex.value] : null)
const inputPlaceholder = computed(() =>
  selectedTool.value
    ? `已选工具「${selectedTool.value.name}」，输入查询内容（如：同花顺最新估值）后点发送`
    : '输入你的问题…（Enter 发送，Shift+Enter 换行）',
)

// 模型
const models = ref<{ id: string; name: string }[]>([])
const currentModel = ref('')
const defaultModel = ref('')
// 模式标签
const mode = ref<'loading' | 'llm' | 'fallback'>('loading')
const modeText = computed(() => mode.value === 'llm' ? 'AI · ' + shortModelName(currentModel.value) : mode.value === 'fallback' ? '手动模式' : '加载中…')
const modeCls = computed(() => mode.value === 'llm' ? 'llm' : mode.value === 'fallback' ? 'fallback' : '')

// ===== 加载工具 + 模型 =====
async function loadTools() {
  try {
    const d = await getMcpTools()
    if (d.error) { ElMessage.warning(d.error); return }
    tools.value = d.tools || []
  } catch (e: any) { ElMessage.error('工具加载失败: ' + e.message) }
}
async function loadModels() {
  try {
    const d = await listModels()
    if (d.error) { ElMessage.warning(d.error); return }
    models.value = d.models || []
    currentModel.value = d.current
    defaultModel.value = d.default
    // 兜底：当前模型不在列表（如配置用简短别名），追加一项
    if (currentModel.value && !models.value.some(m => m.id === currentModel.value)) {
      models.value.unshift({ id: currentModel.value, name: currentModel.value })
    }
  } catch (e: any) { ElMessage.error('模型加载失败: ' + e.message) }
}

function selectTool(i: number) {
  selectedToolIndex.value = selectedToolIndex.value === i ? null : i
}

async function onModelChange(id: string) {
  try {
    const d = await switchModel(id)
    if (d.ok) { currentModel.value = d.current; ElMessage.success('已切换到 ' + shortModelName(d.current)) }
  } catch (e: any) { ElMessage.error('切换失败: ' + e.message) }
}
async function onResetModel() {
  try {
    const d = await resetModel()
    if (d.ok) { currentModel.value = d.current; ElMessage.success('已重置为 ' + shortModelName(d.current)) }
  } catch (e: any) { ElMessage.error('重置失败: ' + e.message) }
}

// ===== 发送 =====
async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  messages.value.push({ role: 'user', html: escapeText(text) })
  input.value = ''
  if (inputEl.value) inputEl.value.style.height = 'auto'
  await scrollBottom()

  // 手动工具查询
  if (selectedTool.value) {
    const idx = messages.value.push({ role: 'assistant', html: '<span class="typing"></span>' }) - 1
    sending.value = true
    try {
      const d = await callMcpTool(selectedTool.value.server, selectedTool.value.name, text)
      messages.value[idx].html = d.ok ? renderMarkdown(d.result || '(空结果)') : `⚠ ${d.error || '查询失败'}`
      messages.value[idx].error = !d.ok
    } catch (e: any) {
      messages.value[idx].html = `⚠ 请求失败: ${e.message}`; messages.value[idx].error = true
    } finally { sending.value = false }
    await scrollBottom()
    return
  }

  // 自由对话（SSE）
  const idx = messages.value.push({ role: 'assistant', html: '<span class="typing"></span>' }) - 1
  let acc = ''
  sending.value = true
  try {
    await streamChat(text, (e) => {
      if (e.type === 'mode') {
        mode.value = e.mode === 'fallback' ? 'fallback' : 'llm'
        if (e.model) currentModel.value = e.model
      } else if (e.type === 'delta') {
        acc += e.text || ''
        messages.value[idx].html = renderMarkdown(acc) + '<span class="typing"></span>'
        scrollBottom()
      } else if (e.type === 'error') {
        messages.value[idx].html = `⚠ ${e.text}`; messages.value[idx].error = true
      }
    })
    messages.value[idx].html = renderMarkdown(acc) || '(无内容)'
  } catch (e: any) {
    messages.value[idx].html = `⚠ 请求失败: ${e.message}`; messages.value[idx].error = true
  } finally { sending.value = false }
  await scrollBottom()
}

function escapeText(s: string): string {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}
async function scrollBottom() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}
function autoGrow() {
  if (!inputEl.value) return
  inputEl.value.style.height = 'auto'
  inputEl.value.style.height = Math.min(inputEl.value.scrollHeight, 120) + 'px'
}

onMounted(() => { loadTools(); loadModels() })
</script>

<style scoped>
.chat-layout { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.topbar {
  display: flex; align-items: center; gap: 12px; padding: 12px 20px;
  background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06); border-bottom: 1px solid #e5e7eb;
}
.topbar h1 { font-size: 18px; color: #1e40af; font-weight: 700; }
.mode-tag { font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 600; background: #e0e7ff; color: #1e40af; }
.mode-tag.fallback { background: #fef3c7; color: #b45309; }
.mode-tag.llm { background: #dcfce7; color: #166534; }
.model-group { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.mlabel { font-size: 12px; color: #6b7280; font-weight: 600; }
.main { flex: 1; display: flex; overflow: hidden; }
.chat-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.messages { flex: 1; overflow-y: auto; padding: 20px; }
.msg { max-width: 80%; margin-bottom: 16px; }
.msg.user { margin-left: auto; }
.bubble { padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.7; word-break: break-word; }
.msg.user .bubble { background: #1e40af; color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: #fff; color: #1f2937; border-bottom-left-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.msg.assistant .bubble.error { background: #fef2f2; color: #b91c1c; }
.bubble :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
.bubble :deep(th), .bubble :deep(td) { border: 1px solid #e5e7eb; padding: 5px 8px; text-align: left; }
.bubble :deep(th) { background: #f9fafb; font-weight: 600; }
.bubble :deep(code) { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.typing { display: inline-block; width: 6px; height: 14px; background: #1e40af; margin-left: 2px; animation: blink 1s infinite; vertical-align: middle; }
@keyframes blink { 0%,100%{opacity:1;} 50%{opacity:0;} }
.composer { padding: 14px 20px; background: #fff; border-top: 1px solid #e5e7eb; }
.tool-hint { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.tool-hint b { color: #1e40af; }
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.input { flex: 1; resize: none; border: 1px solid #d1d5db; border-radius: 10px; padding: 10px 14px; font-size: 14px; font-family: inherit; line-height: 1.5; max-height: 120px; outline: none; }
.input:focus { border-color: #1e40af; }
.send-btn { background: #1e40af; color: #fff; border: none; border-radius: 10px; padding: 10px 20px; font-size: 14px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.send-btn:hover:not(:disabled) { background: #1e3a8a; }
.send-btn:disabled { background: #9ca3af; cursor: not-allowed; }
.tools-col { width: 300px; flex-shrink: 0; background: #fff; border-left: 1px solid #e5e7eb; display: flex; flex-direction: column; overflow: hidden; }
.tools-head { padding: 14px 16px; border-bottom: 1px solid #e5e7eb; font-size: 13px; font-weight: 700; color: #374151; display: flex; align-items: center; justify-content: space-between; }
.count { font-size: 11px; color: #6b7280; font-weight: 500; }
.tools-list { flex: 1; overflow-y: auto; padding: 8px; }
.tools-empty { padding: 20px; font-size: 12px; color: #9ca3af; text-align: center; }
.tool-item { padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 4px; border: 1px solid transparent; }
.tool-item:hover { background: #f9fafb; }
.tool-item.selected { background: #eff6ff; border-color: #93c5fd; }
.tname { font-size: 13px; font-weight: 600; color: #1e40af; margin-bottom: 2px; }
.tsrv { font-size: 10px; color: #9ca3af; }
.tdesc { font-size: 11px; color: #6b7280; line-height: 1.4; margin-top: 3px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
</style>
