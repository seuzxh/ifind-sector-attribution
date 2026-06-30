<template>
  <div class="rotation-layout">
    <!-- 顶栏 -->
    <div class="topbar">
      <h1>🔮 板块轮动分析</h1>
      <span class="subtitle">第一性原理 + 对抗审查 · Loop Engine</span>
      <button class="analyze-btn" @click="startAnalyze" :disabled="running">
        {{ running ? '⏳ 分析中...' : '🔮 开始分析' }}
      </button>
    </div>

    <!-- 分析输出区 -->
    <div class="output-area" ref="outputEl">
      <div v-if="!output && !running" class="empty-hint">
        点击"开始分析"，智能体将自主采集数据、分析板块轮动、对抗审查后给出明日强势排名。<br><br>
        <b>分析流程</b>：数据采集（日K线+分时+指数）→ 第一性原理分析 → 对抗审查 → 综合结论<br>
        <b>预计耗时</b>：1-2 分钟（全程流式展示）
      </div>
      <div v-if="output" class="output-content" v-html="renderedOutput"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const running = ref(false)
const output = ref('')
const outputEl = ref<HTMLElement | null>(null)

const renderedOutput = computed(() => renderMarkdown(output.value))

async function startAnalyze() {
  if (running.value) return
  running.value = true
  output.value = ''

  try {
    const resp = await fetch('/api/rotation/analyze')
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const evt = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        const dataLine = evt.split('\n').find(l => l.startsWith('data:'))
        if (!dataLine) continue
        try {
          const e = JSON.parse(dataLine.slice(5).trim())
          if (e.type === 'delta') {
            output.value += e.text || ''
            // 自动滚动到底部
            await nextTick()
            if (outputEl.value) outputEl.value.scrollTop = outputEl.value.scrollHeight
          } else if (e.type === 'error') {
            output.value += `\n\n⚠ **错误**: ${e.text}\n`
          }
        } catch { /* 忽略解析错误 */ }
      }
    }
  } catch (e: any) {
    output.value += `\n\n⚠ **请求失败**: ${e.message}\n`
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.rotation-layout {
  display: flex; flex-direction: column; height: 100%;
  background: #f0f2f5;
}
.topbar {
  display: flex; align-items: center; gap: 14px; padding: 12px 20px;
  background: #fff; border-bottom: 1px solid #e5e7eb; flex-shrink: 0;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.topbar h1 { font-size: 16px; color: #111827; margin: 0; white-space: nowrap; }
.subtitle { font-size: 12px; color: #9ca3af; }
.analyze-btn {
  margin-left: auto; padding: 8px 20px; font-size: 14px; font-weight: 600;
  border: none; border-radius: 8px; cursor: pointer; transition: all .15s;
  background: #6366f1; color: #fff;
}
.analyze-btn:hover:not(:disabled) { background: #4f46e5; }
.analyze-btn:disabled { background: #c7d2fe; cursor: not-allowed; }

.output-area {
  flex: 1; overflow-y: auto; padding: 20px;
}
.empty-hint {
  color: #9ca3af; text-align: center; padding: 60px 20px; font-size: 14px; line-height: 2;
}
.output-content {
  max-width: 900px; margin: 0 auto; background: #fff; border-radius: 10px;
  padding: 24px 32px; font-size: 14px; line-height: 1.8; color: #1f2937;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.output-content :deep(h1),
.output-content :deep(h2) {
  border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; margin: 20px 0 12px;
}
.output-content :deep(h2) { font-size: 16px; color: #1e40af; }
.output-content :deep(h3) { font-size: 14px; color: #374151; margin: 14px 0 8px; }
.output-content :deep(table) { border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0; }
.output-content :deep(th), .output-content :deep(td) {
  border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left;
}
.output-content :deep(th) { background: #f9fafb; font-weight: 600; }
.output-content :deep(code) {
  background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px;
}
.output-content :deep(pre) {
  background: #1e293b; color: #e2e8f0; padding: 12px 16px; border-radius: 8px;
  overflow-x: auto; font-size: 13px;
}
.output-content :deep(pre code) { background: none; color: inherit; }
.output-content :deep(strong) { color: #111827; }
.output-content :deep(hr) { border: none; border-top: 2px solid #e5e7eb; margin: 20px 0; }
</style>
