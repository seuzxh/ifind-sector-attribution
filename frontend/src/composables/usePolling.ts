/**
 * 轮询 composable：带竞态守卫（仅最新响应允许处理）。
 * 复刻原 index.html 的 refreshSeq 机制：播放/轮询乱序时丢弃过期响应。
 *
 * 用法：
 *   const { start, stop, triggerNow, currentSeq } = usePolling(async (mySeq) => {
 *     const data = await fetchDashboard()
 *     if (mySeq !== currentSeq()) return  // 过期，丢弃
 *     render(data)
 *   }, 3000, { shouldTick: () => autoFollow.value })
 */
import { ref, onUnmounted } from 'vue'

export interface PollingOptions {
  /** 仅控制定时轮询；triggerNow 始终执行。 */
  shouldTick?: () => boolean
}

export function usePolling(
  fn: (seq: number) => Promise<void>,
  intervalMs = 3000,
  opts: PollingOptions = {},
) {
  let timer: ReturnType<typeof setInterval> | null = null
  // 序号计数器：每次发起请求自增，回调里比对判断是否过期
  let seq = 0
  const active = ref(false)

  async function runNow() {
    const mySeq = ++seq
    try {
      await fn(mySeq)
    } catch (e) {
      console.warn('[usePolling] error:', e)
    }
  }

  function start() {
    if (timer) return
    active.value = true
    timer = setInterval(() => {
      if (opts.shouldTick?.() === false) return
      void runNow()
    }, intervalMs)
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
    return runNow()
  }

  /** 当前最新序号（回调里与此比对判断响应是否过期） */
  function currentSeq() {
    return seq
  }

  /** 占用一个新序号，供轮询之外的异步操作共用竞态守卫。 */
  function nextSeq() {
    return ++seq
  }

  onUnmounted(stop)

  return { start, stop, triggerNow, currentSeq, nextSeq, active }
}
