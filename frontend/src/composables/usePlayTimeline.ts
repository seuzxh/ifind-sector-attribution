/**
 * 时间轴播放 composable：自动快进分时时间轴（逐分钟演进看板）。
 * 复刻原 index.html 的 playTimer/stepPlay/getPlayInterval 逻辑。
 *
 * 用法（realtime 看板）：
 *   const { playing, speedMs, currentIndex, start, stop, setSpeed } = usePlayTimeline({
 *     max: () => availableTimes.value.length - 1,
 *     onStep: (idx) => { currentIndex.value = idx; refresh() },
 *   })
 */
import { ref, onUnmounted } from 'vue'

export interface PlayTimelineOptions {
  /** 最大索引（闭区间），动态计算（传函数） */
  max: () => number
  /** 每步回调（索引变化时） */
  onStep: (index: number) => void
  /** 初始速度（每步间隔 ms），默认 800 (2x) */
  initialSpeed?: number
}

export function usePlayTimeline(opts: PlayTimelineOptions) {
  const playing = ref(false)
  const speedMs = ref(opts.initialSpeed ?? 800)
  const currentIndex = ref(0)
  let timer: ReturnType<typeof setInterval> | null = null

  function step() {
    const max = opts.max()
    if (currentIndex.value >= max) {
      stop()
      return
    }
    currentIndex.value += 1
    opts.onStep(currentIndex.value)
  }

  function start() {
    if (playing.value) return
    playing.value = true
    timer = setInterval(step, speedMs.value)
  }

  function stop() {
    playing.value = false
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function setSpeed(ms: number) {
    speedMs.value = ms
    if (playing.value) {
      stop()
      start() // 重启以应用新速度
    }
  }

  /** 跳到指定索引（拖动滑块） */
  function jumpTo(index: number) {
    currentIndex.value = index
  }

  onUnmounted(stop)

  return { playing, speedMs, currentIndex, start, stop, setSpeed, jumpTo }
}
