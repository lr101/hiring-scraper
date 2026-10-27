import { useEffect, useState } from 'react'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
type Connection = {
  enabled: boolean; available: boolean; login_state: string; login_error?: string
  user_code?: string; verification_url?: string
}

export default function ChatGPTConnection({ onChange, preparing = false }: { onChange?: (enabled: boolean) => void; preparing?: boolean }) {
  const [connection, setConnection] = useState<Connection | null>(null)
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function refresh() {
      try {
        const response = await fetch(`${API}/api/v1/application-preparation`, { signal: controller.signal, cache: 'no-store' })
        if (!response.ok) throw new Error('Could not check your ChatGPT connection.')
        const data: Connection = await response.json()
        if (controller.signal.aborted) return
        setConnection(data); onChange?.(data.enabled === true)
        if (['starting', 'pending'].includes(data.login_state)) timer = setTimeout(() => void refresh(), 1000)
      } catch {
        if (!controller.signal.aborted) { setError('Could not check your ChatGPT connection. Reload to try again.'); onChange?.(false) }
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [revision, onChange])

  async function change(action: 'login' | 'logout') {
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API}/api/v1/application-preparation/${action}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      const data = await response.json()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update your ChatGPT connection.')
      setConnection(data); onChange?.(data.enabled === true); setRevision(value => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update your ChatGPT connection.') }
    finally { setBusy(false) }
  }

  const pending = ['starting', 'pending'].includes(connection?.login_state ?? '')
  return <div className="chatgpt-connection" role="group" aria-label="ChatGPT subscription connection">
    {!connection ? <p className="finish-help">Checking your ChatGPT connection…</p>
      : connection.enabled ? <>
        <p className="finish-help">Using your ChatGPT subscription on this Radius instance, across your devices.</p>
        <div className="finish-actions"><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage ChatGPT usage ↗</a>
          <button className="finish-text-button" disabled={busy || preparing} onClick={() => void change('logout')}>Disconnect ChatGPT</button></div>
      </> : pending ? <>
        {connection.user_code && connection.verification_url === 'https://auth.openai.com/codex/device' ? <>
          <p>Open ChatGPT and enter this one-time code:</p>
          <p><code aria-label="ChatGPT sign-in code">{connection.user_code}</code></p>
          <a className="primary-button" href={connection.verification_url} target="_blank" rel="noreferrer">Open ChatGPT sign-in</a>
          <p className="finish-help" role="status">Waiting for your approval in ChatGPT. This sign-in expires after 15 minutes.</p>
        </> : <p className="finish-help" role="status">Starting ChatGPT sign-in…</p>}
      </> : <>
        <button disabled={busy || !connection.available} onClick={() => void change('login')}>{busy ? 'Starting sign-in…' : 'Continue with ChatGPT'}</button>
        <p className="finish-help">{connection.available ? 'Connect your ChatGPT subscription once to prepare documents here. No API key is needed.' : 'The subscription runtime is missing. Use the current Radius release image or install its pinned Codex runtime.'}</p>
        {connection.login_state === 'expired' && <p className="finish-help">The sign-in expired. Continue with ChatGPT to get a new code.</p>}
      </>}
    {(error || connection?.login_error) && <p className="preparation-error" role="alert">{error || connection?.login_error}</p>}
  </div>
}
