/**
 * 会话感知 composable：盘前/非交易日停止轮询，到点自动恢复。
 * 复刻原 index.html checkSession + scheduleResume 逻辑：
 *   - 非交易日 / 盘前(<09:15) → 停轮询，定时探测到点恢复
 *   - 集合竞价 / 盘中 / 收盘后 → 启动轮询
 *
 * 用法（realtime 看板）：
 *   const { status, shouldPoll, start, stop } = useSession()
 *   watch(shouldPoll, (v) => v ? startPolling() : stopPolling())
 */
import { ref, onUnmounted } from 'vue'
import { getSessionStatus, type SessionStatus } from '@/api/session'

export function useSession(checkIntervalMs = 60000, resumeIntervalMs = 10000) {
  const status = ref<SessionStatus | null>(null)
  const error = ref<string | null>(null)
  const active = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let generation = 0

  // 是否应该轮询：有数据时段（集合竞价~收盘）才轮询
  const shouldPoll = ref(false)

  async function check(expectedGeneration?: number) {
    try {
      const s = await getSessionStatus()
      if (
        expectedGeneration !== undefined
        && (!active.value || expectedGeneration !== generation)
      ) return
      if (s && typeof s === 'object' && 'phase' in s) {
        status.value = s
        error.value = null
        // auction/pre_morning/morning/lunch/afternoon/closed 都可能有数据（closed 收盘后回看）
        // 仅 pre_open（盘前<09:15）和非交易日停轮询
        shouldPoll.value = s.is_trading_day && s.phase !== 'pre_open'
      }
    } catch (e: any) {
      if (
        expectedGeneration !== undefined
        && (!active.value || expectedGeneration !== generation)
      ) return
      error.value = e?.message || String(e)
      shouldPoll.value = true // 接口异常时保守轮询（让原有 error 文案兜底）
    }
  }

  function scheduleNext(myGeneration: number) {
    if (!active.value || myGeneration !== generation) return
    const delay = shouldPoll.value ? checkIntervalMs : resumeIntervalMs
    timer = setTimeout(() => { void runCheck(myGeneration) }, delay)
  }

  async function runCheck(myGeneration: number) {
    if (!active.value || myGeneration !== generation) return
    await check(myGeneration)
    scheduleNext(myGeneration)
  }

  async function start() {
    if (active.value) return
    active.value = true
    const myGeneration = ++generation
    await runCheck(myGeneration)
  }

  function stop() {
    active.value = false
    generation += 1
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { status, error, shouldPoll, active, check, start, stop }
}
