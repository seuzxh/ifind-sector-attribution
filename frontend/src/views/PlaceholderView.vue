<template>
  <div class="placeholder">
    <el-icon class="icon"><DataAnalysis /></el-icon>
    <h2>{{ meta?.title || '看板' }}</h2>
    <p class="hint">（此看板将在后续阶段迁移到此）</p>
    <p class="status">
      后端联调状态：
      <el-tag v-if="sessionStatus === 'ok'" type="success">已连通 /api/session_status</el-tag>
      <el-tag v-else-if="sessionStatus === 'loading'" type="info">检测中…</el-tag>
      <el-tag v-else-if="sessionStatus === 'error'" type="danger">未连通</el-tag>
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { getSessionStatus } from '@/api/session'

const route = useRoute()
const meta = route.meta as { title?: string } | undefined

// 验证后端 API 联调（阶段 0 交付验证项）
const sessionStatus = ref<'loading' | 'ok' | 'error'>('loading')
watchEffect(async () => {
  try {
    const d = await getSessionStatus()
    sessionStatus.value = d && typeof d === 'object' && 'phase' in d ? 'ok' : 'error'
  } catch {
    sessionStatus.value = 'error'
  }
})
</script>

<style scoped>
.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 12px;
  color: var(--color-text-light);
}
.icon { font-size: 48px; color: var(--color-primary); }
h2 { color: var(--color-text); }
.hint { font-size: 13px; color: var(--color-text-faint); }
.status { font-size: 13px; }
</style>
