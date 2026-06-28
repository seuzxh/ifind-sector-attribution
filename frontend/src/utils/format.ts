/** 格式化工具（迁移自原 index.html fmt/fmtPct） */

/** 数值格式化：null/undefined/NaN → '-'；正数加 +，保留 2 位小数 */
export function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(n as number)) return '-'
  return (n > 0 ? '+' : '') + Number(n).toFixed(2)
}

/** 百分比格式化：fmt(n) + '%' */
export function fmtPct(n: number | null | undefined): string {
  return fmt(n) + '%'
}

/** 涨跌色 class：涨红跌绿（A 股惯例） */
export function changeCls(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(n as number)) return ''
  return n > 0 ? 'up' : n < 0 ? 'down' : ''
}

/** 加速度指示：>0.01 ▲红，<-0.01 ▼绿，≈0 灰 */
export function accelText(a: number | null | undefined): { text: string; cls: string } {
  if (a === null || a === undefined || isNaN(a as number)) return { text: '-', cls: 'text-faint' }
  if (a > 0.01) return { text: '▲' + fmt(a), cls: 'up' }
  if (a < -0.01) return { text: '▼' + fmt(a), cls: 'down' }
  return { text: fmt(a), cls: 'text-faint' }
}

/** 评分对应背景色（top 用红系，bottom/zt 用绿系） */
export function scoreColor(score: number, tab: 'top' | 'bottom' | 'zt'): string {
  if (tab === 'top') {
    return score >= 2 ? '#dc2626' : score >= 1 ? '#ef4444' : '#f87171'
  }
  return score <= -2 ? '#059669' : score <= -1 ? '#10b981' : '#34d399'
}

/** 排名徽章 class */
export function rankClass(i: number): string {
  return i === 0 ? 'r1' : i === 1 ? 'r2' : i === 2 ? 'r3' : 'rN'
}
