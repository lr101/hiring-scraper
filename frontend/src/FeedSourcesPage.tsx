import { useCallback, useEffect, useState, type FormEvent } from 'react'
import SiteHeader from './SiteHeader'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`
const ATS_PROVIDERS = ['greenhouse', 'lever', 'personio', 'ashby', 'recruitee', 'smartrecruiters']
const MANAGED_PROVIDERS = ['external_html', 'browser_html', 'rss', 'arbeitsagentur', ...ATS_PROVIDERS]

type LocationFilter = {
  query: string; city: string; label: string; radius_km: number
  latitude: number | null; longitude: number | null; precision: string | null; country_code: string
}
type SourceConfig = { location_filter?: LocationFilter; terms?: string[]; uses_default_terms?: boolean; managed_adapter?: boolean }
type Feed = {
  id: number; company_id: number; company_name: string; company_website: string | null; company_career_url: string | null
  company_source: string
  provider: string; feed_url: string; board_url: string | null; source_config?: SourceConfig
  enabled: boolean; status: string; job_count: number | null; last_checked_at: string | null
  next_scan_at: string | null; last_error: string | null
}
type Company = { id: number; name: string; website_url: string | null }
type ManagedProvider = 'external_html' | 'browser_html' | 'rss' | 'arbeitsagentur' | 'ats'

function providerLabel(provider: string) {
  if (provider === 'browser_html') return 'JavaScript page · Chromium'
  if (provider === 'external_html') return 'Public HTML page'
  if (provider === 'html_jobs') return 'Company HTML page'
  if (provider === 'rss') return 'RSS / Atom feed'
  if (provider === 'arbeitsagentur') return 'Agentur für Arbeit scheduled search'
  if (ATS_PROVIDERS.includes(provider)) return `${provider.replaceAll('_', ' ')} adapter`
  return `${provider.replaceAll('_', ' ')} feed`
}

function isManagedProvider(provider: string) {
  return MANAGED_PROVIDERS.includes(provider)
}

function editorProvider(provider: string): ManagedProvider {
  return ATS_PROVIDERS.includes(provider) ? 'ats' : provider as ManagedProvider
}

function editorUrl(feed: Feed) {
  return ATS_PROVIDERS.includes(feed.provider) || feed.provider === 'arbeitsagentur'
    ? feed.board_url || feed.feed_url : feed.feed_url
}

function statusLabel(feed: Feed) {
  if (feed.status === 'source_replaced') return 'Replaced source'
  if (!feed.enabled) return 'Paused'
  const labels: Record<string, string> = {
    pending: 'Waiting to scan', parsed: 'Listings found', complete_empty: 'No listings found',
    windowed: 'Scanned · rolling feed', windowed_empty: 'No items in feed window',
    incomplete: 'Partly scanned', blocked: 'Access denied', bot_challenge: 'Bot or CAPTCHA challenge',
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

function parseTerms(value: string) {
  return value.split(/[\n,]/).map(term => term.trim()).filter(Boolean)
}

function editorValues(items: Feed[]) {
  return {
    careerPages: Object.fromEntries(items.map(feed => [feed.id, feed.company_career_url ?? ''] as const)) as Record<number, string>,
    urls: Object.fromEntries(items.map(feed => [feed.id, editorUrl(feed)] as const)) as Record<number, string>,
    providers: Object.fromEntries(items.filter(feed => isManagedProvider(feed.provider))
      .map(feed => [feed.id, editorProvider(feed.provider)] as const)) as Record<number, ManagedProvider>,
    locations: Object.fromEntries(items.map(feed => [feed.id, feed.provider === 'arbeitsagentur' ? '' : feed.source_config?.location_filter?.query ?? ''] as const)) as Record<number, string>,
    radii: Object.fromEntries(items.map(feed => [feed.id, String(feed.provider === 'arbeitsagentur' ? 35 : feed.source_config?.location_filter?.radius_km ?? 35)] as const)) as Record<number, string>,
    terms: Object.fromEntries(items.map(feed => [feed.id, feed.source_config?.uses_default_terms
      ? '' : (feed.source_config?.terms ?? []).join('\n')] as const)) as Record<number, string>,
  }
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
  const [locationQuery, setLocationQuery] = useState('')
  const [radiusKm, setRadiusKm] = useState('35')
  const [terms, setTerms] = useState('')
  const [editedCareerPages, setEditedCareerPages] = useState<Record<number, string>>({})
  const [editedUrls, setEditedUrls] = useState<Record<number, string>>({})
  const [editedProviders, setEditedProviders] = useState<Record<number, ManagedProvider>>({})
  const [editedLocations, setEditedLocations] = useState<Record<number, string>>({})
  const [editedRadii, setEditedRadii] = useState<Record<number, string>>({})
  const [editedTerms, setEditedTerms] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadFeeds = useCallback(async () => {
    const response = await fetch(apiUrl('/api/v1/feeds'))
    if (!response.ok) throw new Error(await responseError(response, 'Could not load job feeds.'))
    const payload = await response.json()
    setFeeds(payload.items)
    const values = editorValues(payload.items)
    setEditedCareerPages(values.careerPages)
    setEditedUrls(values.urls)
    setEditedProviders(values.providers)
    setEditedLocations(values.locations)
    setEditedRadii(values.radii)
    setEditedTerms(values.terms)
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
      const values = editorValues(feedPayload.items)
      setEditedCareerPages(values.careerPages)
      setEditedUrls(values.urls)
      setEditedProviders(values.providers)
      setEditedLocations(values.locations)
      setEditedRadii(values.radii)
      setEditedTerms(values.terms)
      if (company) {
        setCompanyName(company.name)
        setWebsiteUrl(company.website_url ?? '')
      }
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load job feeds.')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [companyId])

  useEffect(() => {
    if (!feeds.some(feed => feed.enabled && feed.status === 'pending')) return
    const timer = window.setInterval(() => {
      void fetch(apiUrl('/api/v1/feeds')).then(async response => {
        if (!response.ok) throw new Error(await responseError(response, 'Could not refresh feed status.'))
        const payload = await response.json()
        setFeeds(payload.items)
      }).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh feed status.'))
    }, 4000)
    return () => window.clearInterval(timer)
  }, [feeds, loadFeeds])

  async function createFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true); setError(''); setNotice('')
    try {
      const trimmedLocation = locationQuery.trim()
      const response = await fetch(apiUrl('/api/v1/feeds'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_id: companyId ? Number(companyId) : undefined,
          company_name: companyName.trim() || undefined, website_url: websiteUrl || undefined,
          feed_url: feedUrl, provider,
          location_query: provider === 'arbeitsagentur' ? undefined : trimmedLocation || undefined,
          radius_km: provider === 'arbeitsagentur' || !trimmedLocation ? undefined : Number(radiusKm),
          terms: provider === 'arbeitsagentur' ? parseTerms(terms) : undefined,
        }),
      })
      if (!response.ok) throw new Error(await responseError(response, 'Could not add this page.'))
      await loadFeeds()
      setFeedUrl('')
      setNotice('Job source added. Its first scheduled scan is queued.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add this page.')
    } finally { setSaving(false) }
  }

  async function updateFeed(feed: Feed, changes: Record<string, unknown>) {
    setError(''); setNotice('')
    const response = await fetch(apiUrl(`/api/v1/feeds/${feed.id}`), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    })
    if (!response.ok) throw new Error(await responseError(response, 'Could not update this feed.'))
    await loadFeeds()
  }

  const visibleFeeds = companyId ? feeds.filter(feed => String(feed.company_id) === companyId) : feeds

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
    <SiteHeader active="settings" />
    <main className="content feed-sources-page">
      <section className="feed-page-heading">
        <p className="eyebrow">SOURCE MANAGEMENT</p>
        <h1>Job feeds</h1>
        <p>Add public job pages, publisher feeds, or a supported job-search adapter. Each source uses the scheduled feed scanner and can also be scanned on demand. The scraper reads listings only; it does not submit applications.</p>
      </section>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
      {notice && <p className="feed-notice" role="status">{notice}</p>}

      <section className="detail-panel feed-create-panel">
        <p className="eyebrow">ADD A SOURCE</p>
        <h2>Connect a job source</h2>
        <form className="feed-create-form" onSubmit={createFeed}>
          {!companyId && <label>Company or source name <span>(optional)</span>
            <input maxLength={500} value={companyName} onChange={event => setCompanyName(event.target.value)} placeholder="e.g. Company Careers or Job Board" />
          </label>}
          {!companyId && <label>Company website <span>(optional)</span>
            <input type="url" value={websiteUrl} onChange={event => setWebsiteUrl(event.target.value)} placeholder="https://example.de" />
          </label>}
          {companyId && <p className="feed-company-context">Adding a page for <strong>{companyName || 'this company'}</strong></p>}
          <label>{provider === 'ats' ? 'Company career page or ATS board URL' : 'Job listings URL'}
            <input required type="url" value={feedUrl} onChange={event => setFeedUrl(event.target.value)} placeholder={provider === 'arbeitsagentur' ? 'https://www.arbeitsagentur.de/jobsuche/' : provider === 'ats' ? 'https://careers.smartrecruiters.com/Company' : 'https://example.de/karriere/stellenangebote or an RSS feed URL'} />
          </label>
          <label>Page type
            <select value={provider} onChange={event => {
              const next = event.target.value as ManagedProvider
              setProvider(next)
              if (next === 'arbeitsagentur' && !feedUrl) setFeedUrl('https://www.arbeitsagentur.de/jobsuche/')
            }}>
              <option value="external_html">Public HTML page</option>
              <option value="browser_html">JavaScript page · Chromium</option>
              <option value="rss">RSS / Atom feed</option>
              <option value="ats">ATS career page · auto-detect</option>
              <option value="arbeitsagentur">Agentur für Arbeit · scheduled search</option>
            </select>
          </label>
          {provider !== 'arbeitsagentur' && <>
            <label>Location filter <span>(optional)</span>
              <input maxLength={200} value={locationQuery} onChange={event => setLocationQuery(event.target.value)} placeholder="e.g. Karlsruhe" />
            </label>
            <label>Search radius <span>(kilometres)</span>
              <input type="number" min={1} max={200} step={1} value={radiusKm} onChange={event => setRadiusKm(event.target.value)} />
            </label>
          </>}
          {provider === 'arbeitsagentur' && <p className="feed-browser-note">Search area follows the scheduled discovery scan. Germany-wide scans run without a location filter; city searches use the saved city and radius.</p>}
          {provider === 'arbeitsagentur' && <label className="regional-search-terms">Job terms <span>(optional; one per line or comma separated)</span>
            <textarea rows={3} maxLength={6000} value={terms} onChange={event => setTerms(event.target.value)} placeholder={'Softwareentwickler\nData Engineer'} />
          </label>}
          <button className="search-button feed-add-button" disabled={saving} type="submit">{saving ? 'Adding…' : 'Add source'} <span aria-hidden="true">→</span></button>
        </form>
        <p className="feed-browser-note">ATS auto-detect supports public Greenhouse, Lever, Personio, Ashby, Recruitee, and SmartRecruiters pages. The Agentur für Arbeit adapter follows each scheduled discovery area and runs with the regular feed scans. Leave its job terms blank to use the built-in role list. Other feeds apply a saved radius to listings with coordinates and use an exact city match when only a city label is available. If a source returns a bot or CAPTCHA challenge, its scheduled scan stops and records that reason.</p>
      </section>

      <section className="detail-panel feed-list-panel">
        <div className="feed-list-heading"><div><p className="eyebrow">{companyId ? 'COMPANY SOURCES' : 'SAVED SOURCES'}</p><h2>{visibleFeeds.length} {visibleFeeds.length === 1 ? 'feed' : 'feeds'}</h2></div>
          {companyId && <a className="page-link" href="/feeds">All feeds</a>}</div>
        {loading ? <div className="loading-state"><span className="spinner" /> Loading feeds…</div>
          : visibleFeeds.length ? <div className="managed-feed-list">{visibleFeeds.map(feed => {
            const isBaAdapter = feed.provider === 'arbeitsagentur' && (
              feed.source_config?.managed_adapter === true || feed.company_source === 'managed_feed' || Boolean(feed.source_config?.location_filter))
            const manuallyManaged = isManagedProvider(feed.provider) && (feed.provider !== 'arbeitsagentur' || isBaAdapter)
            const selectedProvider = editedProviders[feed.id] ?? editorProvider(feed.provider)
            const selectedTerms = selectedProvider === 'arbeitsagentur' ? parseTerms(editedTerms[feed.id] ?? '') : undefined
            return <article className="managed-feed-row" key={feed.id}>
              <div className="managed-feed-meta">
                <div><h3>{feed.company_name}</h3><span className={`feed-status-pill ${feed.enabled ? '' : 'is-paused'}`}>{statusLabel(feed)}</span></div>
                <p>{feed.provider === 'arbeitsagentur' && !isBaAdapter ? 'Agentur für Arbeit imported results' : providerLabel(feed.provider)} · {feed.job_count ?? 0} jobs · Last checked {dateLabel(feed.last_checked_at)}</p>
                {isBaAdapter && <p>Search area follows the scheduled discovery scan.</p>}
                {feed.provider !== 'arbeitsagentur' && feed.source_config?.location_filter && <p>Search area: {feed.source_config.location_filter.query} · {feed.source_config.location_filter.radius_km} km</p>}
                {feed.provider === 'arbeitsagentur' && !isBaAdapter && <p>Imported snapshot. Add the regional adapter above to schedule searches.</p>}
                {feed.last_error && feed.enabled && <p className="feed-last-error">{feed.last_error}</p>}
              </div>
              <label className="managed-feed-url">Career page URL
                <input type="url" autoCapitalize="none" autoCorrect="off" spellCheck={false}
                  value={editedCareerPages[feed.id] ?? ''}
                  onChange={event => setEditedCareerPages(current => ({ ...current, [feed.id]: event.target.value }))}
                  placeholder="https://company.de/karriere" />
              </label>
              {manuallyManaged ? <>
                <label className="managed-feed-url">Listing URL
                  <input type="url" value={editedUrls[feed.id] ?? editorUrl(feed)} onChange={event => setEditedUrls(current => ({ ...current, [feed.id]: event.target.value }))} />
                </label>
                <label className="managed-feed-url">Scraper type
                  <select value={selectedProvider} onChange={event => setEditedProviders(current => ({ ...current, [feed.id]: event.target.value as ManagedProvider }))}>
                    <option value="external_html">Public HTML page</option>
                    <option value="browser_html">JavaScript page · Chromium</option>
                    <option value="rss">RSS / Atom feed</option>
                    <option value="ats">ATS career page · auto-detect</option>
                    <option value="arbeitsagentur">Agentur für Arbeit · scheduled search</option>
                  </select>
                </label>
                {selectedProvider !== 'arbeitsagentur' && <>
                  <label className="managed-feed-url">Location filter <span>(optional)</span>
                    <input value={editedLocations[feed.id] ?? ''} onChange={event => setEditedLocations(current => ({ ...current, [feed.id]: event.target.value }))} placeholder="e.g. Karlsruhe" />
                  </label>
                  <label className="managed-feed-url">Search radius <span>(kilometres)</span>
                    <input type="number" min={1} max={200} step={1} value={editedRadii[feed.id] ?? '35'} onChange={event => setEditedRadii(current => ({ ...current, [feed.id]: event.target.value }))} />
                  </label>
                </>}
                {selectedProvider === 'arbeitsagentur' && <p className="managed-feed-readonly">Search area follows scheduled discovery scans.</p>}
                {selectedProvider === 'arbeitsagentur' && <label className="managed-feed-url regional-search-terms">Job terms <span>(optional)</span>
                  <textarea rows={2} value={editedTerms[feed.id] ?? ''} onChange={event => setEditedTerms(current => ({ ...current, [feed.id]: event.target.value }))} placeholder="Built-in role list" />
                </label>}
              </> : <p className="managed-feed-readonly">Source page: <a href={feed.board_url || feed.feed_url} target="_blank" rel="noreferrer">{feed.board_url || feed.feed_url} ↗</a></p>}
              <div className="managed-feed-actions">
                {manuallyManaged && <>
                  <button type="button" onClick={() => void runAction(() => updateFeed(feed, {
                    career_page_url: (editedCareerPages[feed.id] ?? '').trim() || null,
                    feed_url: editedUrls[feed.id] ?? editorUrl(feed),
                    provider: selectedProvider,
                    location_query: selectedProvider === 'arbeitsagentur' ? undefined : (editedLocations[feed.id] ?? '').trim() || null,
                    radius_km: selectedProvider === 'arbeitsagentur' ? undefined : (editedLocations[feed.id] ?? '').trim() ? Number(editedRadii[feed.id] ?? '35') : null,
                    terms: selectedTerms,
                  }))}>Save changes</button>
                  <button type="button" disabled={!feed.enabled} onClick={() => void runAction(() => scanNow(feed))}>Scan now</button>
                </>}
                {!manuallyManaged && <button type="button" onClick={() => void runAction(() => updateFeed(feed, {
                  career_page_url: (editedCareerPages[feed.id] ?? '').trim() || null,
                }))}>Save changes</button>}
                {(feed.provider !== 'arbeitsagentur' || isBaAdapter) && <button type="button" onClick={() => void runAction(() => updateFeed(feed, { enabled: !feed.enabled }))}>{feed.enabled ? 'Pause' : 'Resume'}</button>}
              </div>
            </article>
          })}</div> : <div className="empty-state"><span className="empty-icon">↗</span><strong>{companyId ? 'No feeds for this company yet' : 'No job feeds yet'}</strong><p>Add a company careers page, public job board or RSS / Atom feed to start collecting listings.</p></div>}
      </section>
    </main>
  </div>
}
