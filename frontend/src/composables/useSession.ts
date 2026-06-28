/**
 * 会话感知 composable：盘前/非交易日停止轮询，到点自动恢复。
 * 复刻原 index.html checkSession + scheduleResume 逻辑：
 *   - 非交易日 / 盘前(<09:15) → 停轮询，定时探测到点恢复
 *   - 集合竞价 / 盘中 / 收盘后 → 启动轮询
 *
 * 用法（realtime 看板）：
 *   const { status, shouldPoll, refresh } = useSession()
 *   watch(shouldPoll, (v) => v ? startPolling() : stopPolling())
 */
import { ref, onMounted, onUnmounted } from 'vue'
import { getSessionStatus, type SessionStatus } from '@/api/session'

export function useSession(checkIntervalMs = 60000) {
  const status = ref<SessionStatus | null>(null)
  const error = ref<string | null>(null)
  let timer: ReturnType<typeof setInterval> | null = null

  // 是否应该轮询：有数据时段（集合竞价~收盘）才轮询
  const shouldPoll = ref(false)

  async function refresh() {
    try {
      const s = await getSessionStatus()
      if (s && typeof s === 'object' && 'phase' in s) {
        status.value = s
        error.value = null
        // auction/pre_morning/morning/lunch/afternoon/closed 都可能有数据（closed 收盘后回看）
        // 仅 pre_open（盘前<09:15）和非交易日停轮询
        shouldPoll.value = s.is_trading_day && s.phase !== 'pre_open'
      }
    } catch (e: any) {
      error.value = e?.message || String(e)
      shouldPoll.value = true // 接口异常时保守轮询（让原有 error 文案兜底）
    }
  }

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, checkIntervalMs)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { status, error, shouldPoll, refresh }
}
