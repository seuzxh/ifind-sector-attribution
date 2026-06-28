/** AI 问答相关接口：MCP 工具、LLM 模型、SSE 对话 */
import http from './client'

// MCP 工具
export interface McpTool {
  name: string
  description: string
  server: string
  inputSchema?: Record<string, any>
}

export function getMcpTools(): Promise<{ count: number; tools: McpTool[]; error?: string }> {
  return http.get('/api/mcp/tools')
}

export function callMcpTool(server: string, tool: string, query: string): Promise<{ ok: boolean; result?: string; error?: string }> {
  return http.post('/api/mcp/call', { server, tool, arguments: { query } })
}

// LLM 模型
export interface ChatModel {
  id: string
  name: string
  status?: string | null
}
export interface ModelListResult {
  models: ChatModel[]
  current: string
  default: string
  error?: string | null
  warn?: string | null
}

export function listModels(): Promise<ModelListResult> {
  return http.get('/api/llm/models')
}
export function switchModel(model: string): Promise<{ ok: boolean; current: string; previous: string }> {
  return http.post('/api/llm/model', { model })
}
export function resetModel(): Promise<{ ok: boolean; current: string }> {
  return http.post('/api/llm/model/reset')
}

// ===== SSE 对话流 =====
// /api/chat 返回 SSE，每条事件 data: {type, ...}。type: mode|delta|tools|error|done
export interface ChatSseEvent {
  type: 'mode' | 'delta' | 'tools' | 'error' | 'done'
  mode?: 'llm' | 'fallback'
  model?: string
  text?: string
  tools?: McpTool[]
}

/**
 * 发送对话，流式回调每个 SSE 事件。
 * 用 fetch ReadableStream 解析 SSE（比 EventSource 更可控，支持 POST）。
 */
export async function streamChat(
  message: string,
  onEvent: (e: ChatSseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const evt = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const dataLine = evt.split('\n').find(l => l.startsWith('data:'))
      if (!dataLine) continue
      try { onEvent(JSON.parse(dataLine.slice(5).trim())) } catch { /* 忽略解析错误 */ }
    }
  }
}

/** 简短模型名（去掉 -YYMMDD 后缀） */
export function shortModelName(id: string): string {
  return (id || '').replace(/-\d{6}$/, '')
}
