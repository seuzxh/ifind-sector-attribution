/** 交易日历 / 已入库日期 */
import http from './client'

export interface TradeCalendar {
  count: number
  trade_days: string[]
  today: string  // 服务端权威当前日期 YYYYMMDD
}

export function getTradeCalendar(year: number): Promise<TradeCalendar> {
  return http.get('/api/trade_calendar', { params: { year } })
}

export function getAvailableDates(): Promise<{ dates: string[] }> {
  return http.get('/api/dates')
}
