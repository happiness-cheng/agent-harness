/**
 * 模式选择器（SHELL-007）：Plan / Build。
 *
 * 会话建立后：GET /api/sessions/{id} 读 work_state.mode，
 * POST /api/sessions/{id}/mode 切换。
 * 欢迎页（还没有服务器会话）：选择先存本机，创建会话时由 adrian.ts 带给后端；
 * Build 不会自动确认目标——没有确认目标前写类工具仍会被授权门拒绝。
 */

import { memo, useEffect, useState } from 'react'
import {
  ensureBootstrap,
  postJson,
  readPendingMode,
  resolveSessionId,
  savePendingMode,
  type WorkMode,
} from '../../services/api/adrian.js'
import { useTranslate } from '../../i18n/index.js'

export const ModePicker = memo(function ModePicker({
  sessionId,
  disabled = false,
}: {
  sessionId: string | null
  disabled?: boolean
}) {
  const t = useTranslate()
  const [mode, setMode] = useState<WorkMode>('plan')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const serverId = sessionId ? resolveSessionId(sessionId) : null
  const welcome = serverId === null

  useEffect(() => {
    let cancelled = false
    if (welcome) {
      setMode(readPendingMode() ?? 'plan')
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
        const snapshot = (await response.json()) as { work_state?: { mode?: string } }
        const current = snapshot.work_state?.mode
        if (!cancelled && (current === 'plan' || current === 'build')) setMode(current)
      } catch {
        // 后端不可用时保持默认 Plan，不显示假状态
      }
    })()
    return () => {
      cancelled = true
    }
  }, [welcome, serverId])

  const change = async (value: WorkMode) => {
    setBusy(true)
    setError('')
    try {
      if (welcome) {
        savePendingMode(value)
        setMode(value)
        return
      }
      await ensureBootstrap()
      const response = await postJson(`/api/sessions/${encodeURIComponent(serverId)}/mode`, { mode: value })
      if (!response.ok) throw new Error(String(response.status))
      setMode(value)
    } catch {
      setError(t('modeSwitchFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <label
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, opacity: 0.85 }}
      title={welcome ? t('modeWelcomeHint') : t('modeSessionHint')}
    >
      <span>{t('modeLabel')}</span>
      <select
        value={mode}
        disabled={busy || disabled}
        onChange={(event) => void change(event.target.value as WorkMode)}
        style={{
          fontSize: 12,
          padding: '2px 6px',
          borderRadius: 6,
          border: '1px solid currentColor',
          background: 'transparent',
          color: 'inherit',
        }}
      >
        <option value="plan" style={{ color: '#111' }}>
          {t('modePlan')}
        </option>
        <option value="build" style={{ color: '#111' }}>
          {t('modeBuild')}
        </option>
      </select>
      {error ? <span style={{ color: '#c0392b' }}>{error}</span> : null}
    </label>
  )
})
