/**
 * 模型选择器（SHELL-003 / SHELL-006）。
 *
 * 会话建立后：GET /api/sessions/{id} 拿 current/options，POST /api/sessions/{id}/model 切换。
 * 欢迎页（还没有服务器会话）：GET /api/models 拿目录，选择先存本机，
 * 创建会话时由 adrian.ts 通过 POST /api/sessions 的 model 字段带给后端。
 */

import { memo, useEffect, useState } from 'react'
import {
  ensureBootstrap,
  fetchModelStatus,
  postJson,
  readPendingModel,
  resolveSessionId,
  savePendingModel,
  type ModelStatus,
} from '../../services/api/adrian.js'

export const ModelPicker = memo(function ModelPicker({ sessionId }: { sessionId: string | null }) {
  const [status, setStatus] = useState<ModelStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const serverId = sessionId ? resolveSessionId(sessionId) : null
  const welcome = serverId === null

  useEffect(() => {
    let cancelled = false
    if (welcome) {
      // 新会话欢迎页：用无会话的模型目录，并用本机记住的选择作为当前值。
      void (async () => {
        try {
          const catalog = await fetchModelStatus()
          if (!catalog || cancelled) return
          const pending = readPendingModel()
          const known = (catalog.options ?? []).some((option) => option.id === pending)
          setStatus({ ...catalog, current: known ? pending : catalog.current })
        } catch {
          // 后端不可用时保持隐藏，不显示假选项
        }
      })()
      return () => {
        cancelled = true
      }
    }
    void (async () => {
      try {
        await ensureBootstrap()
        const response = await fetch(`/api/sessions/${encodeURIComponent(serverId)}`, {
          credentials: 'same-origin',
        })
        if (!response.ok) return
        const snapshot = (await response.json()) as { model?: ModelStatus }
        if (!cancelled) setStatus(snapshot.model ?? null)
      } catch {
        // 后端不可用时保持隐藏，不显示假选项
      }
    })()
    return () => {
      cancelled = true
    }
  }, [welcome, serverId])

  if (!status?.selectable || !status.options?.length) return null

  const change = async (value: string) => {
    setBusy(true)
    setError('')
    try {
      if (welcome) {
        savePendingModel(value)
        setStatus((previous) => (previous ? { ...previous, current: value } : previous))
        return
      }
      await ensureBootstrap()
      const response = await postJson(`/api/sessions/${encodeURIComponent(serverId)}/model`, { model: value })
      if (!response.ok) throw new Error(String(response.status))
      setStatus((previous) => (previous ? { ...previous, current: value } : previous))
    } catch {
      setError('切换失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <label
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, opacity: 0.85 }}
      title={welcome ? '选择新会话使用的模型（发送第一条消息时生效）' : '选择本会话使用的模型'}
    >
      <span>模型</span>
      <select
        value={status.current ?? ''}
        disabled={busy}
        onChange={(event) => void change(event.target.value)}
        style={{
          fontSize: 12,
          padding: '2px 6px',
          borderRadius: 6,
          border: '1px solid currentColor',
          background: 'transparent',
          color: 'inherit',
        }}
      >
        {status.options.map((option) => (
          <option key={option.id} value={option.id} style={{ color: '#111' }}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? <span style={{ color: '#c0392b' }}>{error}</span> : null}
    </label>
  )
})
