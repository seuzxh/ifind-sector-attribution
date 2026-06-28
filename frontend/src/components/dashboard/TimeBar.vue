<template>
  <div class="timebar">
    <span class="tb-label">⏱ 时刻</span>
    <button class="play-btn" :class="{ playing: playing }" @click="$emit('togglePlay')">
      {{ playing ? '⏸ 暂停' : '▶ 播放' }}
    </button>
    <input type="range" class="slider" :min="0" :max="maxIndex" :value="currentIndex"
           @input="onSliderInput" @change="onSliderChange" />
    <span class="tb-cur">{{ currentTimeText }}</span>
    <span class="tb-live" :class="{ paused: !autoFollow }">{{ autoFollow ? 'LIVE' : '已暂停' }}</span>
    <select class="speed-sel" :value="speedMs" @change="onSpeedChange">
      <option :value="1500">1.5x</option>
      <option :value="800">2x</option>
      <option :value="400">4x</option>
      <option :value="150">8x</option>
    </select>
    <button class="jump-btn" @click="$emit('jumpToLatest')">⏭ 回到最新</button>
    <span class="tb-mark">{{ markText }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  availableTimes: string[]
  currentIndex: number
  currentTimeText: string
  autoFollow: boolean
  playing: boolean
  speedMs: number
}>()

const emit = defineEmits<{
  (e: 'update:currentIndex', v: number): void
  (e: 'sliderChange', idx: number): void
  (e: 'togglePlay'): void
  (e: 'speedChange', ms: number): void
  (e: 'jumpToLatest'): void
}>()

const maxIndex = computed(() => Math.max(0, props.availableTimes.length - 1))
const markText = computed(() => {
  const t = props.availableTimes
  return t.length ? `${t[0]} ~ ${t[t.length - 1]}` : ''
})

function onSliderInput(e: Event) {
  const idx = Number((e.target as HTMLInputElement).value)
  emit('update:currentIndex', idx)
}
function onSliderChange(e: Event) {
  const idx = Number((e.target as HTMLInputElement).value)
  emit('sliderChange', idx)
}
function onSpeedChange(e: Event) {
  emit('speedChange', Number((e.target as HTMLSelectElement).value))
}
</script>

<style scoped>
.timebar {
  display: flex; align-items: center; gap: 8px; padding: 8px 16px;
  background: #fff; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap;
}
.tb-label { font-size: 13px; color: #6b7280; font-weight: 600; }
.play-btn, .jump-btn {
  padding: 5px 12px; border-radius: 6px; border: 1px solid #1e40af;
  background: #1e40af; color: #fff; font-size: 12px; cursor: pointer; white-space: nowrap;
}
.play-btn:hover, .jump-btn:hover { background: #1e3a8a; }
.play-btn.playing { background: #dc2626; border-color: #dc2626; }
.slider { flex: 1; min-width: 150px; cursor: pointer; }
.tb-cur { font-size: 13px; font-weight: 600; color: #1f2937; min-width: 50px; }
.tb-live { font-size: 11px; font-weight: 700; color: #dc2626; padding: 2px 6px; background: #fee2e2; border-radius: 4px; }
.tb-live.paused { color: #6b7280; background: #f3f4f6; }
.speed-sel { font-size: 12px; padding: 3px 6px; border-radius: 4px; border: 1px solid #d1d5db; }
.tb-mark { font-size: 11px; color: #9ca3af; }
</style>
