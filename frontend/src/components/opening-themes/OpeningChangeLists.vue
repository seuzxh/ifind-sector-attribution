<template>
  <aside class="change-lists">
    <section v-for="list in lists" :key="list.title" :aria-label="list.title">
      <h2>{{ list.title }} <small>前 10 题材 · 1分钟变化</small></h2>
      <ol><li v-for="(item, index) in list.items" :key="item.theme_code">
        <button :aria-pressed="selectedCode === item.theme_code" @click="$emit('select', item.theme_code)">
          <span class="rank">{{ index + 1 }}</span><span class="name">{{ item.theme_name }}</span>
          <strong :class="changeCls(item[list.metric])">{{ change(item[list.metric], list.factor) }}</strong>
        </button>
      </li></ol>
      <p v-if="!list.items.length">暂无符合条件的题材</p>
    </section>
  </aside>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { OpeningTheme } from '@/api/openingThemes'
import { fmt, changeCls } from '@/utils/format'
const props = defineProps<{ themes: OpeningTheme[]; accelerationCodes: string[]; breadthCodes: string[]; selectedCode: string }>()
defineEmits<{ select: [code: string] }>()
const lists = computed(() => {
  const byCode = new Map(props.themes.map(theme => [theme.theme_code, theme]))
  const ordered = (codes: string[]) => codes.flatMap(code => byCode.has(code) ? [byCode.get(code)!] : []).slice(0, 10)
  return [
    { title: '加速榜', items: ordered(props.accelerationCodes), metric: 'momentum_1m' as const, factor: 1 },
    { title: '扩散榜', items: ordered(props.breadthCodes), metric: 'breadth_delta_1m' as const, factor: 100 },
  ]
})
function change(value: number | null, factor: number) { return value === null ? '—' : `${fmt(value * factor)} pp` }
</script>
<style scoped>
.change-lists { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
section { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px #0000000f; }
h2 { font-size: 14px; margin-bottom: 8px; } small { font-size: 10px; color: #6b7280; font-weight: normal; }
ol { list-style: none; } button { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 6px; border: 0; border-bottom: 1px solid #e5e7eb; background: #fff; font: inherit; font-size: 12px; cursor: pointer; text-align: left; }
button:hover, button[aria-pressed=true] { background: #eff6ff; } .rank { color: #6b7280; width: 18px; } .name { flex: 1; } strong { white-space: nowrap; }
p { padding: 20px 0; color: #6b7280; font-size: 12px; }
</style>
