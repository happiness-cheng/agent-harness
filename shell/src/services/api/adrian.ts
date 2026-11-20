/**
 * 本地 Adrian 后端适配层（SHELL-002）。
 *
 * 上游 chat-demo 原本直连 DeepSeek 的 Anthropic 兼容接口；这里把它替换成
 * 本仓库 FastAPI 的 /api/*，并把我们的 SSE 增量翻译成上游认识的消息事件。
 *
 * 约定：
 * - 会话映射：上游每个会话（conversation_id）对应服务器一条 session，
 *   映射存在 localStorage，删除浏览器数据后由服务器侧历史另行恢复（后续批次）。
 * - 只发文字：附件/语音/联网搜索本轮不接，遇到附件明确报错，不假装支持。
 */

export type AdrianEvent = Record<string, unknown>

type Bootstrap = { csrf: string }

let csrf = ''
let bootstrapping: Promise<void> | null = null

export class AdrianError extends Error {
  readonly kind: 'network' | 'stream' | 'protocol'
  readonly status?: number

  constructor(kind: 'network' | 'stream' | 'protocol', message: string, status?: number) {
    super(message)
    this.name = 'AdrianError'
    this.kind = kind
    this.status = status
  }
}

/** 取 CSRF 令牌并让后台下发会话 Cookie；失败时下次调用重试。 */
export const ensureBootstrap = async (): Promise<void> => {
  if (csrf) return
  bootstrapping ??= (async () => {
    const response = await fetch('/api/bootstrap', { credentials: 'same-origin' })
    if (!response.ok) {
      throw new AdrianError('network', `本地后台不可用（${response.status}）`, response.status)
    }
    const value = (await response.json()) as Bootstrap
    csrf = value.csrf
  })().catch((error: unknown) => {
    bootstrapping = null
    throw error
  })
  await bootstrapping
}

const post = async (path: string, body: unknown): Promise<Response> => {
  await ensureBootstrap()
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Harness-CSRF': csrf },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new AdrianError('stream', `本地后台拒绝请求（${response.status}）`, response.status)
  }
  return response
}

/** 供界面模块复用：带 CSRF 的 JSON POST（模型切换等）。 */
export const postJson = (path: string, body: unknown): Promise<Response> => post(path, body)

const sessionKey = (conversationId: string) => `adrian:session:${conversationId}`

/** 上游会话 id → 服务器 session id；未建立映射时返回 null（新会话尚未发送过）。 */
export const resolveSessionId = (conversationId: string): string | null => {
  try {
    return localStorage.getItem(sessionKey(conversationId))
  } catch {
    return null
  }
}

/** 服务器会话直接以 session_id 作为本地会话 id，这里补一条恒等映射。 */
export const bindSessionId = (sessionId: string): void => {
  try {
    localStorage.setItem(sessionKey(sessionId), sessionId)
  } catch {
    // 存储不可用时不阻塞
  }
}

/** 改名写回服务器；调用方决定失败时是否提示。 */
export const renameServerSession = async (sessionId: string, label: string): Promise<void> => {
  await post(`/api/sessions/${encodeURIComponent(sessionId)}/label`, { label })
}

const hiddenKey = 'adrian:hidden-sessions'

const readHidden = (): string[] => {
  try {
    const parsed = JSON.parse(localStorage.getItem(hiddenKey) ?? '[]') as unknown
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

/** 只在本机列表隐藏服务器会话；服务器数据不动。 */
export const isSessionHidden = (sessionId: string): boolean => readHidden().includes(sessionId)

export const hideSession = (sessionId: string): void => {
  const hidden = new Set(readHidden())
  hidden.add(sessionId)
  try {
    localStorage.setItem(hiddenKey, JSON.stringify([...hidden]))
  } catch {
    // 存储不可用时只是刷新后仍会重现
  }
}

export type ModelOption = { id: string; label: string }
export type ModelStatus = { selectable?: boolean; current?: string | null; options?: ModelOption[] }

const pendingModelKey = 'adrian:pending-model'

/** 欢迎页选的模型：还没有服务器会话，先存在本机，创建会话时带给后端。 */
export const readPendingModel = (): string | null => {
  try {
    return localStorage.getItem(pendingModelKey)
  } catch {
    return null
  }
}

export const savePendingModel = (model: string): void => {
  try {
    localStorage.setItem(pendingModelKey, model)
  } catch {
    // 存储不可用时只影响新会话默认值，不阻塞发送
  }
}

/** 没有会话时的模型目录与默认值；后端未启用可选模型时返回 selectable=false。 */
export const fetchModelStatus = async (): Promise<ModelStatus | null> => {
  await ensureBootstrap()
  const response = await fetch('/api/models', { credentials: 'same-origin' })
  if (!response.ok) return null
  return (await response.json()) as ModelStatus
}

export type WorkMode = 'plan' | 'build'

const pendingModeKey = 'adrian:pending-mode'

/** 欢迎页选的模式（Plan/Build）；没有会话时先存本机，创建会话时带给后端。 */
export const readPendingMode = (): WorkMode | null => {
  try {
    const value = localStorage.getItem(pendingModeKey)
    return value === 'plan' || value === 'build' ? value : null
  } catch {
    return null
  }
}

export const savePendingMode = (mode: WorkMode): void => {
  try {
    localStorage.setItem(pendingModeKey, mode)
  } catch {
    // 存储不可用时只影响新会话默认值
  }
}

const sessionOf = async (conversationId: string, label: string): Promise<string> => {
  const key = sessionKey(conversationId)
  try {
    const existing = localStorage.getItem(key)
    if (existing) return existing
  } catch {
    // 存储不可用时退化为每次新建会话，不阻塞对话
  }
  const model = readPendingModel()
  const mode = readPendingMode()
  const body: { label: string; model?: string; mode?: WorkMode } = { label: label || '新会话' }
  if (model) body.model = model
  if (mode) body.mode = mode
  const response = await post('/api/sessions', body)
  const value = (await response.json()) as { session_id: string }
  try {
    localStorage.setItem(key, value.session_id)
  } catch {
    // 同上
  }
  return value.session_id
}

type Block = { type?: string; text?: string }

const lastUserText = (payload: { messages?: unknown }): string => {
  const messages = Array.isArray(payload.messages) ? payload.messages : []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: string; content?: unknown } | undefined
    if (!message || message.role !== 'user') continue
    const content = message.content
    if (typeof content === 'string') return content.trim()
    if (Array.isArray(content)) {
      const blocks = content as Block[]
      if (blocks.some((block) => block.type === 'image' || block.type === 'document')) {
        throw new AdrianError('protocol', '当前界面暂不支持附件，请只发送文字。')
      }
      const text = blocks
        .filter((block) => block.type === 'text')
        .map((block) => block.text ?? '')
        .join('\n')
        .trim()
      return text
    }
    return ''
  }
  return ''
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/**
 * 发送一条用户消息并产出上游认识的消息事件。
 * 409 表示后台还没建立流（worker 尚未启动），短暂重试而不是直接失败。
 */
export async function* adrianStream(
  payload: { messages?: unknown; conversation_id?: string },
  signal: AbortSignal
): AsyncGenerator<AdrianEvent> {
  const conversationId =
    typeof payload.conversation_id === 'string' && payload.conversation_id
      ? payload.conversation_id
      : crypto.randomUUID()
  const text = lastUserText(payload)
  if (!text) throw new AdrianError('protocol', '没有可发送的文字内容。')

  const sessionId = await sessionOf(conversationId, text.slice(0, 20))
  const stopRemote = () => {
    void post(`/api/sessions/${sessionId}/control`, { kind: 'stop' }).catch(() => undefined)
  }
  signal.addEventListener('abort', stopRemote, { once: true })

  await post(`/api/sessions/${sessionId}/input`, { text })

  let response: Response | null = null
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (signal.aborted) return
    const candidate = await fetch(`/api/sessions/${sessionId}/stream`, {
      credentials: 'same-origin',
      signal,
    })
    if (candidate.ok && candidate.body) {
      response = candidate
      break
    }
    if (candidate.status !== 409) {
      throw new AdrianError('stream', `流式通道不可用（${candidate.status}）`, candidate.status)
    }
    await sleep(100)
  }
  if (!response || !response.body) {
    throw new AdrianError('stream', '后台没有开始回答。')
  }

  yield {
    type: 'message_start',
    message: {
      id: `msg_${conversationId}`,
      type: 'message',
      role: 'assistant',
      model: 'deepseek-flash',
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 0, output_tokens: 0 },
    },
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let started = false
  let outputLength = 0
  let finished = false

  try {
    while (!finished) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      let split = buffer.indexOf('\n\n')
      while (split >= 0) {
        const raw = buffer.slice(0, split)
        buffer = buffer.slice(split + 2)
        split = buffer.indexOf('\n\n')
        for (const line of raw.split('\n')) {
          if (line.startsWith('event: done')) {
            finished = true
            continue
          }
          if (!line.startsWith('data:')) continue
          const data = line.slice(5).trim()
          if (!data) continue
          let parsed: { text?: unknown }
          try {
            parsed = JSON.parse(data) as { text?: unknown }
          } catch {
            continue
          }
          if (typeof parsed.text !== 'string' || !parsed.text) continue
          if (!started) {
            started = true
            yield { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }
          }
          outputLength += parsed.text.length
          yield { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: parsed.text } }
        }
      }
    }
  } finally {
    reader.releaseLock()
    signal.removeEventListener('abort', stopRemote)
  }

  if (!started) {
    // 模型没有产出任何文字：明确报错，不把空回答显示成成功。
    throw new AdrianError('stream', '模型没有返回内容。')
  }
  yield { type: 'content_block_stop', index: 0 }
  yield {
    type: 'message_delta',
    delta: { stop_reason: 'end_turn', stop_sequence: null, title: text.slice(0, 20) },
    usage: { input_tokens: 0, output_tokens: outputLength },
  }
  yield { type: 'message_stop' }
}
