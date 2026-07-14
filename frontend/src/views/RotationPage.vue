<template>
  <div class="rotation-layout">
    <!-- 顶栏 -->
    <div class="topbar">
      <h1>🔮 板块轮动分析</h1>
      <span class="subtitle">第一性原理 + 情绪周期 + 对抗审查</span>
      <button class="analyze-btn" @click="startAnalyze" :disabled="running">
        {{ running ? '⏳ 分析中...' : '🔮 开始分析' }}
      </button>
    </div>

    <!-- 分析输出区 -->
    <div class="output-area" ref="outputEl">
      <div v-if="!sections.length && !running" class="empty-hint">
        点击"开始分析"，智能体将自主采集数据、分析板块轮动、对抗审查后给出明日强势排名。<br><br>
        <b>分析流程</b>：数据采集（日K线+指数）→ 第一性原理分析 → 对抗审查 → 综合结论<br>
        <b>预计耗时</b>：1-2 分钟（全程流式展示思考过程）
      </div>

      <!-- 按阶段分卡片展示 -->
      <div v-for="(sec, i) in sections" :key="i" class="stage-card" :class="sec.cls">
        <div class="stage-header">
          <span class="stage-icon">{{ sec.icon }}</span>
          <span class="stage-title">{{ sec.title }}</span>
        </div>
        <div class="stage-body" v-html="sec.html"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const running = ref(false)
const outputEl = ref<HTMLElement | null>(null)
const sections = ref<{title: string; icon: string; cls: string; html: string}[]>([])

// 把 SSE 累积的文本按 "---" 分隔成阶段卡片
function pushSection(text: string) {
  // 从文本提取阶段标题和图标
  const titleMatch = text.match(/\*\*(.+?)\*\*/)
  let title = '分析输出'
  let icon = '📝'
  let cls = 'stage-default'

  if (text.includes('阶段1') || text.includes('数据采集')) {
    title = titleMatch ? titleMatch[1] : '阶段1：数据采集'
    icon = '📊'; cls = 'stage-collect'
  } else if (text.includes('阶段2') || text.includes('第一性原理')) {
    title = titleMatch ? titleMatch[1] : '阶段2：第一性原理分析'
    icon = '📈'; cls = 'stage-analyze'
  } else if (text.includes('阶段3') || text.includes('对抗审查')) {
    title = titleMatch ? titleMatch[1] : '阶段3：对抗审查'
    icon = '⚔️'; cls = 'stage-review'
  } else if (text.includes('阶段4') || text.includes('综合结论')) {
    title = titleMatch ? titleMatch[1] : '阶段4：综合结论'
    icon = '✅'; cls = 'stage-final'
  } else if (text.includes('分析完成')) {
    title = '分析完成'; icon = '🏁'; cls = 'stage-done'
  } else if (text.includes('启动')) {
    title = '启动'; icon = '📋'; cls = 'stage-init'
  }

  sections.value.push({
    title, icon, cls,
    html: renderMarkdown(text.trim()),
  })
}

async function startAnalyze() {
  if (running.value) return
  running.value = true
  sections.value = []

  try {
    const resp = await fetch('/api/rotation/analyze')
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let currentSection = ''

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
            const text = e.text || ''
            // "---" 作为阶段分隔符
            // 只把"独立行的 ---"（阶段分隔）当分隔符，不把表格 |---| 误判
            const isStageSep = /^---\s*$/m.test(text.trim()) || text.startsWith('---\n')
            if (isStageSep) {
              // 把分隔前的内容推送为卡片
              const parts = text.split(/^---$/m)
              if (parts[0].trim()) {
                currentSection += parts[0]
                pushSection(currentSection)
                currentSection = ''
              }
              // 分隔后的新阶段文本（追加，不覆盖）
              const afterSep = parts.slice(1).join('---')
              if (afterSep.trim()) {
                currentSection += afterSep
              }
            } else {
              currentSection += text
            }
            // 自动滚动
            await nextTick()
            if (outputEl.value) outputEl.value.scrollTop = outputEl.value.scrollHeight
          } else if (e.type === 'error') {
            currentSection += `\n\n⚠ **错误**: ${e.text}\n`
          }
        } catch { /* 忽略解析错误 */ }
      }
    }
    // 推送最后一个 section
    if (currentSection.trim()) {
      pushSection(currentSection)
    }
  } catch (e: any) {
    pushSection(`\n\n⚠ **请求失败**: ${e.message}\n`)
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

/* 阶段卡片 */
.stage-card {
  max-width: 900px; margin: 0 auto 16px; background: #fff; border-radius: 10px;
  overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  border-left: 4px solid #e5e7eb;
}
.stage-card.stage-init { border-left-color: #6366f1; }
.stage-card.stage-collect { border-left-color: #3b82f6; }
.stage-card.stage-analyze { border-left-color: #10b981; }
.stage-card.stage-review { border-left-color: #f59e0b; }
.stage-card.stage-final { border-left-color: #8b5cf6; }
.stage-card.stage-done { border-left-color: #6b7280; }
.stage-card.stage-default { border-left-color: #e5e7eb; }

.stage-header {
  display: flex; align-items: center; gap: 8px; padding: 10px 16px;
  font-size: 14px; font-weight: 600; color: #111827;
  background: #f9fafb; border-bottom: 1px solid #f3f4f6;
}
.stage-icon { font-size: 18px; }
.stage-title { font-size: 13px; }

.stage-body {
  padding: 16px 20px; font-size: 14px; line-height: 1.8; color: #1f2937;
}
.stage-body :deep(h1),
.stage-body :deep(h2) {
  border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; margin: 16px 0 8px;
}
.stage-body :deep(h2) { font-size: 15px; color: #1e40af; }
.stage-body :deep(h3) { font-size: 14px; color: #374151; margin: 12px 0 6px; }
.stage-body :deep(table) { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0; }
.stage-body :deep(th), .stage-body :deep(td) {
  border: 1px solid #e5e7eb; padding: 5px 8px; text-align: left;
}
.stage-body :deep(th) { background: #f9fafb; font-weight: 600; }
.stage-body :deep(code) {
  background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13px;
}
.stage-body :deep(pre) {
  background: #1e293b; color: #e2e8f0; padding: 12px 16px; border-radius: 8px;
  overflow-x: auto; font-size: 13px;
}
.stage-body :deep(pre code) { background: none; color: inherit; }
.stage-body :deep(strong) { color: #111827; }
.stage-body :deep(hr) { border: none; border-top: 1px solid #e5e7eb; margin: 16px 0; }
</style>
