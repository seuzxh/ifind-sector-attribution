/** 交易时段状态（/api/session_status） */
import http from './client'

export interface SessionStatus {
  is_trading_day: boolean
  phase: 'pre_open' | 'auction' | 'pre_morning' | 'morning' | 'lunch' | 'afternoon' | 'closed'
  next_open_time: string | null
  next_trade_day: string | null
  now: string
}

export function getSessionStatus(): Promise<SessionStatus> {
  return http.get('/api/session_status')
}
