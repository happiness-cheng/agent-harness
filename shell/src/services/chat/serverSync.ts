/**
 * 服务器会话同步（SHELL-003）。
 *
 * 上游把会话存在浏览器 IndexedDB；这里在首屏把 `/api/sessions` 里的历史导入本地，
 * 并使用**同一个 id**（服务器 session_id 就是本地会话 id），这样发送时
 * `conversation_id` 可以直接当服务器会话用，不再新建。
 *
 * 只导入有文字消息的会话；工具消息不进入上游消息模型（会破坏其分支结构），跳过。
 */

import * as db from './db.js'
import { useChatStore } from './store.js'
import type { Session, SessionSummary } from './types.js'
import { ensureBootstrap, isSessionHidden } from '../api/adrian.js'

type ServerSession = { session_id: string; label?: string; status?: string }
type ServerMessage = { role?: string; content?: string }

const mappingKey = (id: string) => `adrian:session:${id}`

const markMapping = (id: string) => {
  try {
    localStorage.setItem(mappingKey(id), id)
  } catch {
    // 存储不可用时仅影响下次刷新，不阻塞本次展示
  }
}

const toMessages = (messages: ServerMessage[], sessionId: string) => {
  const result: Session['messages'] = []
  messages.forEach((message, index) => {
    const content = typeof message.content === 'string' ? message.content.trim() : ''
    if (!content) return
    const createdAt = Date.now() - (messages.length - index) * 1000
    const id = `${sessionId}:${index}`
    if (message.role === 'user') {
      result.push({ id, role: 'user', content, files: [], createdAt })
    } else if (message.role === 'assistant') {
      result.push({
        id,
        role: 'assistant',
        createdAt,
        model: 'deepseek-flash',
        prefix: true,
        status: 'done',
        blocks: [{ type: 'text', text: content }],
      })
    }
  })
  return result
}

const fetchJson = async <T>(path: string): Promise<T | null> => {
  try {
    const response = await fetch(path, { credentials: 'same-origin' })
    if (!response.ok) return null
    return (await response.json()) as T
  } catch {
    return null
  }
}

/** 把服务器历史并入本地列表；失败时保持原样，不阻塞界面。 */
export const syncServerSessions = async (): Promise<void> => {
  try {
    await ensureBootstrap()
  } catch {
    return
  }
  const sessions = await fetchJson<ServerSession[]>('/api/sessions')
  if (!Array.isArray(sessions)) return

  const known = new Set(useChatStore.getState().sessions.map((session) => session.id))
  const added: SessionSummary[] = []
  for (const item of sessions) {
    const id = item.session_id
    if (!id) continue
    if (isSessionHidden(id)) continue
    if (known.has(id)) {
      markMapping(id)
      continue
    }
    const detail = await fetchJson<{ messages?: ServerMessage[]; label?: string }>(
      `/api/sessions/${encodeURIComponent(id)}`
    )
    const messages = toMessages(detail?.messages ?? [], id)
    if (messages.length === 0) continue
    const now = Date.now()
    const session: Session = {
      id,
      title: (item.label || detail?.label || '历史会话').slice(0, 80),
      createdAt: now - messages.length * 1000,
      updatedAt: now,
      messages,
    }
    try {
      await db.saveSession(session)
    } catch {
      continue
    }
    markMapping(id)
    added.push({ id: session.id, title: session.title, createdAt: session.createdAt, updatedAt: session.updatedAt })
  }

  if (added.length > 0) {
    const merged = [...useChatStore.getState().sessions, ...added].sort(
      (left, right) => right.updatedAt - left.updatedAt
    )
    useChatStore.setState({ sessions: merged })
  }
}