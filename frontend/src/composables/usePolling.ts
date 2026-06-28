/**
 * 轮询 composable：带竞态守卫（仅最新响应允许处理）。
 * 复刻原 index.html 的 refreshSeq 机制：播放/轮询乱序时丢弃过期响应。
 *
 * 用法：
 *   const { start, stop } = usePolling(async (seq) => {
 *     const data = await fetchDashboard()
 *     if (seq.value !== mySeq) return  // 过期，丢弃
 *     render(data)
 *   }, 3000)
 */
import { ref, onUnmounted } from 'vue'

export function usePolling(fn: (seq: number) => Promise<void>, intervalMs = 3000) {
  let timer: ReturnType<typeof setInterval> | null = null
  // 序号计数器：每次发起请求自增，回调里比对判断是否过期
  let seq = 0
  const active = ref(false)

  function tick() {
    const mySeq = ++seq
    fn(mySeq).catch((e) => console.warn('[usePolling] error:', e))
  }

  function start() {
    if (timer) return
    active.value = true
    timer = setInterval(tick, intervalMs)
  }

  function stop() {
    active.value = false
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 立即触发一次（不等待下一个 interval），用于手动刷新 */
  function triggerNow() {
    tick()
  }

  /** 当前最新序号（回调里与此比对判断响应是否过期） */
  function currentSeq() {
    return seq
  }

  onUnmounted(stop)

  return { start, stop, triggerNow, currentSeq, active }
}
