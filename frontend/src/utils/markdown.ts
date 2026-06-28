/** 最小 Markdown 渲染（表格/代码/加粗/换行）—— 迁移自 chat.html renderMarkdown */

function escapeHtml(s: string): string {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string))
}

export function renderMarkdown(md: string): string {
  const s = escapeHtml(md)
  const lines = s.split('\n')
  const out: string[] = []
  let i = 0
  const parseRow = (r: string) => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim())

  while (i < lines.length) {
    const ln = lines[i]
    // 表格起点：当前行有 |，下一行是分隔行 |---|
    if (ln.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const hdrs = parseRow(ln)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|')) { rows.push(parseRow(lines[i])); i++ }
      let tbl = '<table><thead><tr>' + hdrs.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>'
      for (const r of rows) tbl += '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>'
      tbl += '</tbody></table>'
      out.push(tbl)
      continue
    }
    // 行内：`code` 和 **加粗**
    out.push(
      lines[i]
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>'),
    )
    i++
  }
  // 空行 → 段落分隔；表格内的 <br> 清掉
  return out.join('\n').replace(/\n/g, '<br>').replace(/(<table>[\s\S]*?<\/table>)/g, m => m.replace(/<br>/g, ''))
}
