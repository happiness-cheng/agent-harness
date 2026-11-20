import { createRoot } from 'react-dom/client'
import { App } from './app/App.js'
import { setupTheme } from './app/theme.js'
import { ensureKatex } from './services/markdown/katex.js'
// SHELL-002：本地后端不需要浏览器里的 API Key；写入占位值以跳过上游的设置门槛，
// 并在首屏前预热 CSRF 与会话 Cookie。
import { ensureBootstrap } from './services/api/adrian.js'
import { useSettings } from './services/settings.js'
import { initChat } from './services/chat/store.js'
import { syncServerSessions } from './services/chat/serverSync.js'
import './app/theme.css'
import './app/global.css'

ensureKatex()
setupTheme()

if (!useSettings.getState().apiKey) {
  useSettings.setState({ apiKey: 'local-adrian' })
}
void ensureBootstrap()
// 本地会话先就绪，再把服务器历史并入侧栏（同一 id，发送时直接复用）
void initChat()
  .then(() => syncServerSessions())
  .catch(() => undefined)

createRoot(document.getElementById('root') as HTMLElement).render(<App />)