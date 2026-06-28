<template>
  <div class="layout">
    <header class="tabbar">
      <div
        v-for="tab in tabs"
        :key="tab.name"
        class="tab"
        :class="{ active: activeTab === tab.name }"
        @click="onTabClick(tab.name)"
      >
        <span class="tab-icon">{{ tab.icon }}</span>
        <span class="tab-title">{{ tab.title }}</span>
      </div>
    </header>
    <main class="content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { checkCustomReload } from '@/api/custom'

const route = useRoute()
const router = useRouter()

// Tab 列表（与路由 name 一一对应）
const tabs = [
  { name: 'sector', title: '板块强度监控', icon: '📊' },
  { name: 'custom', title: '自选分组监控', icon: '⭐' },
  { name: 'auction', title: '集合竞价', icon: '⚡' },
  { name: 'scan', title: '自选强势归类', icon: '🎯' },
  { name: 'market_scan', title: '全市场强势归类', icon: '🌐' },
  { name: 'chat', title: 'AI 问答', icon: '🤖' },
] as const

const activeTab = computed(() => (route.name as string) || 'sector')

function onTabClick(name: string) {
  if (name === activeTab.value) return
  router.push({ name })
}

// 切到 custom/scan 时检查自选分组 JSON 是否变更（迁移自 tabs.html checkAndReload）
watch(activeTab, async (name) => {
  if (name === 'custom' || name === 'scan') {
    try {
      const d = await checkCustomReload()
      if (d.reloaded) {
        ElMessage.success(`自选分组已更新：${d.group_count} 分组 / ${d.stock_count} 只股票`)
      }
    } catch (e: any) {
      ElMessage.warning('检查自选分组更新失败: ' + (e?.message || e))
    }
  }
})
</script>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.tabbar {
  display: flex;
  gap: 0;
  background: #fff;
  padding: 8px 16px 0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border-bottom: 1px solid var(--color-border);
  align-items: center;
  flex-shrink: 0;
}
.tab {
  padding: 10px 22px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  color: #6b7280;
  background: transparent;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}
.tab:hover {
  color: #3b82f6;
  background: #f9fafb;
}
.tab.active {
  color: var(--color-primary);
  background: var(--color-bg);
  border-color: var(--color-border);
  position: relative;
}
.tab.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: var(--color-bg);
}
.content {
  flex: 1;
  overflow: auto;
  background: var(--color-bg);
}
</style>
