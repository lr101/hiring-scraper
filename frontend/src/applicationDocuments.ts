import type { ApplicationDocument, CvDocument, Finding } from './documents/types.ts'

export type Language = 'en' | 'de'
export type DocumentKind = 'cv' | 'letter'
export type Mode = 'full' | 'fast'
export type PreparationStage = 'cv' | 'letter' | 'review'
export type Version = {
  version: number
  revision: string
  language: Language
  origin: string
  stale_inputs: boolean
  created_at: string
}
export type SavedDocument = Version & { body: ApplicationDocument; findings: Finding[] }
export type Documents = Record<DocumentKind, SavedDocument | null>
export type Structure = { document: CvDocument | null; source: 'stored' | 'imported'; errors: string[]; revision: string | null }
export type RevisionRequest = { document: DocumentKind; unit_id: string; instruction: string; base_version: number }
export type Revision = {
  document: DocumentKind
  unit_id: string
  base_version: number
  base_revision: string
  original_text: string
  text: string
  findings: Finding[]
  applied: boolean
}
export type Task = {
  id: string
  kind: 'prepare' | 'revise' | 'legacy'
  state: 'running' | 'cancelling' | 'cancelled' | 'failed' | 'completed'
  stage: string
  attempts: number
  error_code: string | null
  error: string | null
  next_attempt_at: string | null
  stages: Record<string, unknown>
  preparation?: { language: Language; mode: Mode }
  revision?: RevisionRequest
  held?: Partial<Record<DocumentKind, Pick<SavedDocument, 'body' | 'findings'> & { latest_version: number; stale_inputs?: boolean }>>
  result?: unknown
}

export const isActive = (task: Task | null) => task?.state === 'running' || task?.state === 'cancelling'

export function preparationStages(mode: Mode = 'full'): PreparationStage[] {
  return mode === 'fast' ? ['cv', 'letter'] : ['cv', 'letter', 'review']
}

export function taskErrorMessage(task: Task) {
  switch (task.error_code) {
    case 'base_cv_unavailable': return 'Review and save the base CV for this language, then retry.'
    case 'job_closed': return 'This posting is closed. Check the original posting before applying.'
    case 'not_found': return 'The profile or job is no longer available.'
    case 'document_missing': return 'This document version is no longer available. Open the latest version to revise it.'
    case 'lease_expired': return 'The preparation worker stopped responding. Retry to resume.'
    case 'database_contention': return 'Radius could not save the result while the database was busy.'
    case 'render_unavailable': return 'PDF rendering is unavailable on this server.'
    case 'not_connected':
    case 'auth_required':
    case 'not_authenticated':
    case 'http_401': return 'Reconnect ChatGPT, then retry.'
    case 'usage_limit': return 'The connected ChatGPT account has reached its usage limit. Try again when the limit resets.'
    case 'runtime_unavailable': return 'The ChatGPT connection is unavailable. Check the connection and retry.'
    case 'timeout': return 'ChatGPT did not respond in time.'
    case 'busy': return 'The ChatGPT connection is busy.'
    case 'provider_error': return 'The AI service returned an error.'
    case 'invalid_json':
    case 'invalid_response': return 'The AI service returned an invalid response.'
    default: return task.error || 'Preparation could not finish. Try again.'
  }
}

export function incomingDocument(current: Pick<Version, 'revision' | 'created_at'>,
  incoming: Pick<Version, 'revision' | 'created_at'>, hasLocalEdits: boolean) {
  if (incoming.revision === current.revision) return 'metadata'
  if (incoming.created_at < current.created_at) return 'ignore'
  return hasLocalEdits ? 'conflict' : 'replace'
}

export function applicationStartLink(jobId: number, profileId?: number | null) {
  return `/jobs/${jobId}/apply${profileId ? `?profile_id=${profileId}` : ''}`
}

export function applicationPostingURL(value: string | null | undefined) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

export class ApplicationHttpError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function responseError(response: Response) {
  const data = await response.json().catch(() => null)
  return new ApplicationHttpError(response.status,
    typeof data?.detail === 'string' ? data.detail : `Radius could not complete this request (HTTP ${response.status}).`)
}

export async function applicationRequest<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { cache: 'no-store', ...options })
  if (!response.ok) throw await responseError(response)
  return response.json()
}

export function jsonRequest(method: 'POST' | 'PUT', value: unknown, signal?: AbortSignal): RequestInit {
  return { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }
}

function waitForRender(interval: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted()
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, interval)
    function abort() {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

export async function pollDocumentPdf(
  url: string, signal: AbortSignal, retry = false,
  { request = fetch, interval = 2000 }: { request?: typeof fetch; interval?: number } = {},
): Promise<Blob> {
  for (let attempt = 0; attempt < 150; attempt++) {
    signal.throwIfAborted()
    const response = await request(retry && attempt === 0 ? `${url}&retry=true` : url, { cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) })
    if (response.status === 202) {
      await waitForRender(interval, signal)
      continue
    }
    if (!response.ok) throw await responseError(response)
    if (!response.headers.get('Content-Type')?.startsWith('application/pdf')) {
      throw new Error('Radius did not return a PDF. Try the download again.')
    }
    return response.blob()
  }
  throw new Error('The PDF is taking longer than expected. Try downloading again shortly.')
}
