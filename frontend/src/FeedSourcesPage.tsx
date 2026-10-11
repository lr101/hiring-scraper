import { useCallback, useEffect, useState, type FormEvent } from 'react'
import SiteHeader from './SiteHeader'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`

type Feed = {
  id: number; company_id: number; company_name: string; company_website: string | null
  provider: string; feed_url: string; board_url: string | null; enabled: boolean
  status: string; job_count: number | null; last_checked_at: string | null
  next_scan_at: string | null; last_error: string | null
}
type Company = { id: number; name: string; website_url: string | null }

function providerLabel(provider: string) {
  if (provider === 'browser_html') return 'JavaScript page · Chromium'
  if (provider === 'external_html') return 'Public HTML page'
  if (provider === 'html_jobs') return 'Company HTML page'
  if (provider === 'rss') return 'RSS / Atom feed'
  return `${provider.replaceAll('_', ' ')} feed`
}

type ManagedProvider = 'external_html' | 'browser_html' | 'rss'

function statusLabel(feed: Feed) {
  if (feed.status === 'source_replaced') return 'Replaced source'
  if (!feed.enabled) return 'Paused'
  const labels: Record<string, string> = {
    pending: 'Waiting to scan', parsed: 'Listings found', complete_empty: 'No listings found',
    windowed: 'Scanned · rolling feed', windowed_empty: 'No items in feed window',
    incomplete: 'Partly scanned', blocked: 'Access denied', bot_challenge: 'Bot challenge',
    throttled: 'Rate limited', schema_error: 'Could not read listings', browser_error: 'Browser could not render',
    unsupported: 'Browser runtime unavailable', failed: 'Could not scan', not_modified: 'Recently checked',
  }
  return labels[feed.status] ?? feed.status.replaceAll('_', ' ')
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = await response.json()
    return payload.detail || fallback
  } catch {
    return fallback
  }
}

function dateLabel(value: string | null) {
  if (!value) return 'Not checked yet'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export default function FeedSourcesPage() {
  const query = new URLSearchParams(window.location.search)
  const initialCompanyId = query.get('company_id') ?? ''
  const [feeds, setFeeds] = useState<Feed[]>([])
  const [companyId] = useState(initialCompanyId)
  const [companyName, setCompanyName] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [feedUrl, setFeedUrl] = useState('')
  const [provider, setProvider] = useState<ManagedProvider>('external_html')
  const [editedUrls, setEditedUrls] = useState<Record<number, string>>({})
  const [editedProviders, setEditedProviders] = useState<Record<number, ManagedProvider>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadFeeds = useCallback(async () => {
    const response = await fetch(apiUrl('/api/v1/feeds'))
    if (!response.ok) throw new Error(await responseError(response, 'Could not load job feeds.'))
    const payload = await response.json()
    setFeeds(payload.items)
    setEditedUrls(Object.fromEntries(payload.items.map((feed: Feed) => [feed.id, feed.feed_url])))
    setEditedProviders(Object.fromEntries(payload.items.filter((feed: Feed) => ['external_html', 'browser_html', 'rss'].includes(feed.provider))
      .map((feed: Feed) => [feed.id, feed.provider as ManagedProvider])))
  }, [])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch(apiUrl('/api/v1/feeds')).then(async response => {
        if (!response.ok) throw new Error(await responseError(response, 'Could not load job feeds.'))
        return response.json()
      }),
      companyId ? fetch(apiUrl(`/api/v1/companies/${companyId}`)).then(async response => {
        if (!response.ok) throw new Error(await responseError(response, 'Could not load this company.'))
        return response.json()
      }) : Promise.resolve(null),
    ]).then(([feedPayload, company]: [{ items: Feed[] }, Company | null]) => {
      if (cancelled) return
      setFeeds(feedPayload.items)
      setEditedUrls(Object.fromEntries(feedPayload.items.map(feed => [feed.id, feed.feed_url])))
      setEditedProviders(Object.fromEntries(feedPayload.items.filter(feed => ['external_html', 'browser_html', 'rss'].includes(feed.provider))
        .map(feed => [feed.id, feed.provider as ManagedProvider])))
      if (company) {
        setCompanyName(company.name)
        setWebsiteUrl(company.website_url ?? '')
      }
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load job feeds.')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [companyId])

  async function createFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true); setError(''); setNotice('')
    try {
      const response = await fetch(apiUrl('/api/v1/feeds'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_id: companyId ? Number(companyId) : undefined,
          company_name: companyName, website_url: websiteUrl || undefined,
          feed_url: feedUrl, provider,
        }),
      })
      if (!response.ok) throw new Error(await responseError(response, 'Could not add this page.'))
      await loadFeeds()
      setFeedUrl('')
      setNotice('Job source added. Its first scan is queued.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add this page.')
    } finally { setSaving(false) }
  }

  async function updateFeed(feed: Feed, changes: Partial<Pick<Feed, 'feed_url' | 'provider' | 'enabled'>>) {
    setError(''); setNotice('')
    const response = await fetch(apiUrl(`/api/v1/feeds/${feed.id}`), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    })
    if (!response.ok) throw new Error(await responseError(response, 'Could not update this feed.'))
    await loadFeeds()
  }

  async function scanNow(feed: Feed) {
    setError(''); setNotice('')
    const response = await fetch(apiUrl(`/api/v1/feeds/${feed.id}/scan`), { method: 'POST' })
    if (!response.ok) throw new Error(await responseError(response, 'Could not queue a scan.'))
    await loadFeeds()
    setNotice(`A scan for ${feed.company_name} is queued.`)
  }

  async function runAction(action: () => Promise<void>) {
    try { await action() } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update this feed.')
    }
  }

  return <div className="app-shell">
    <SiteHeader active="feeds" />
    <main className="content feed-sources-page">
      <section className="feed-page-heading">
        <p className="eyebrow">SOURCE MANAGEMENT</p>
        <h1>Job feeds</h1>
        <p>Add public job pages or publisher-provided RSS / Atom feeds and manage their scans. Sources can be paused or scanned on demand. The scraper reads listings only; it does not submit applications.</p>
      </section>

      <section className="detail-panel feed-create-panel">
        <p className="eyebrow">ADD A SOURCE</p>
        <h2>Connect a job source</h2>
        <form className="feed-create-form" onSubmit={createFeed}>
          {!companyId && <label>Company or source name
            <input required minLength={2} maxLength={500} value={companyName} onChange={event => setCompanyName(event.target.value)} placeholder="e.g. Company Careers or Job Board" />
          </label>}
          {!companyId && <label>Company website <span>(optional)</span>
            <input type="url" value={websiteUrl} onChange={event => setWebsiteUrl(event.target.value)} placeholder="https://example.de" />
          </label>}
          {companyId && <p className="feed-company-context">Adding a page for <strong>{companyName || 'this company'}</strong></p>}
          <label>Job listings URL
            <input required type="url" value={feedUrl} onChange={event => setFeedUrl(event.target.value)} placeholder="https://example.de/karriere/stellenangebote or an RSS feed URL" />
          </label>
          <label>Page type
            <select value={provider} onChange={event => setProvider(event.target.value as ManagedProvider)}>
              <option value="external_html">Public HTML page</option>
              <option value="browser_html">JavaScript page · Chromium</option>
              <option value="rss">RSS / Atom feed</option>
            </select>
          </label>
          <button className="search-button feed-add-button" disabled={saving} type="submit">{saving ? 'Adding…' : 'Add source'} <span aria-hidden="true">→</span></button>
        </form>
        <p className="feed-browser-note">Use RSS / Atom when the publisher offers a job feed, or Chromium when listings appear only after JavaScript runs. Feed windows are treated as partial, so omitted jobs are not closed. If a site returns a bot or CAPTCHA challenge, Radius records <em>Bot challenge</em> as the scan reason and stops. Browser rendering does not solve challenges or sign in to sites.</p>
      </section>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
      {notice && <p className="feed-notice" role="status">{notice}</p>}

      <section className="detail-panel feed-list-panel">
        <div className="feed-list-heading"><div><p className="eyebrow">SAVED SOURCES</p><h2>{feeds.length} {feeds.length === 1 ? 'feed' : 'feeds'}</h2></div></div>
        {loading ? <div className="loading-state"><span className="spinner" /> Loading feeds…</div>
          : feeds.length ? <div className="managed-feed-list">{feeds.map(feed => {
            const manuallyManaged = ['external_html', 'browser_html', 'rss'].includes(feed.provider)
            const regionalImport = feed.provider === 'arbeitsagentur'
            const selectedProvider = editedProviders[feed.id] ?? (feed.provider as ManagedProvider)
            return <article className="managed-feed-row" key={feed.id}>
              <div className="managed-feed-meta">
                <div><h3>{feed.company_name}</h3><span className={`feed-status-pill ${feed.enabled ? '' : 'is-paused'}`}>{statusLabel(feed)}</span></div>
                <p>{providerLabel(feed.provider)} · {feed.job_count ?? 0} jobs · Last checked {dateLabel(feed.last_checked_at)}</p>
                {feed.last_error && feed.enabled && <p className="feed-last-error">{feed.last_error}</p>}
              </div>
              {manuallyManaged ? <>
                <label className="managed-feed-url">Listing URL
                  <input type="url" value={editedUrls[feed.id] ?? feed.feed_url} onChange={event => setEditedUrls(current => ({ ...current, [feed.id]: event.target.value }))} />
                </label>
                <label className="managed-feed-url">Scraper type
                  <select value={selectedProvider} onChange={event => setEditedProviders(current => ({ ...current, [feed.id]: event.target.value as ManagedProvider }))}>
                    <option value="external_html">Public HTML page</option>
                    <option value="browser_html">JavaScript page · Chromium</option>
                    <option value="rss">RSS / Atom feed</option>
                  </select>
                </label>
              </> : <p className="managed-feed-readonly">Source page: <a href={feed.board_url || feed.feed_url} target="_blank" rel="noreferrer">{feed.board_url || feed.feed_url} ↗</a></p>}
              <div className="managed-feed-actions">
                {manuallyManaged && <>
                  <button type="button" onClick={() => void runAction(() => updateFeed(feed, {
                    feed_url: editedUrls[feed.id] ?? feed.feed_url,
                    provider: selectedProvider,
                  }))}>Save changes</button>
                  <button type="button" disabled={!feed.enabled} onClick={() => void runAction(() => scanNow(feed))}>Scan now</button>
                </>}
                {!regionalImport && <button type="button" onClick={() => void runAction(() => updateFeed(feed, { enabled: !feed.enabled }))}>{feed.enabled ? 'Pause' : 'Resume'}</button>}
                {regionalImport && <span className="managed-feed-readonly">Refreshed by the regional importer</span>}
              </div>
            </article>
          })}</div> : <div className="empty-state"><span className="empty-icon">↗</span><strong>No job feeds yet</strong><p>Add a company careers page, public job board or RSS / Atom feed to start collecting listings.</p></div>}
      </section>
    </main>
  </div>
}
