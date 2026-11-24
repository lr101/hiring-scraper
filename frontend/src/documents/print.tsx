import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { CvView } from './CvView.tsx'
import { LetterView } from './LetterView.tsx'
import { syncInlineSeparators } from './inlineSeparators.ts'
import { parseCvDocument, parseLetterDocument } from './types.ts'

export interface RadiusPrintPayload {
  kind: 'cv' | 'letter'
  document: unknown
  photo?: string | null
}

declare global {
  interface Window {
    __RADIUS_DOCUMENT__?: RadiusPrintPayload
    __RADIUS_RENDER__?: (payload: RadiusPrintPayload) => Promise<void>
    __RADIUS_SYNC_LAYOUT__?: () => void
  }
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve())
  })
}

async function waitForImages(container: HTMLElement): Promise<void> {
  const images = Array.from(container.querySelectorAll('img'))
  await Promise.all(
    images.map(async (img) => {
      if (img.complete) return
      if (typeof img.decode === 'function') {
        try {
          await img.decode()
          return
        } catch {
          // Fall through to event wait / resolve even on broken image so print does not hang.
        }
      }
      await new Promise<void>((resolve) => {
        img.addEventListener('load', () => resolve(), { once: true })
        img.addEventListener('error', () => resolve(), { once: true })
      })
    }),
  )
}

const rootElement = document.getElementById('root')
let reactRoot: Root | null = null
let renderGeneration = 0

async function renderPayload(payload: RadiusPrintPayload): Promise<void> {
  const generation = ++renderGeneration
  delete document.body.dataset.ready
  delete document.body.dataset.error

  if (!rootElement) {
    document.body.dataset.error = 'Missing #root element'
    document.body.dataset.ready = 'error'
    return
  }

  try {
    if (!reactRoot) {
      reactRoot = createRoot(rootElement)
    }

    if (payload.kind === 'cv') {
      const doc = parseCvDocument(payload.document)
      document.documentElement.lang = doc.language
      reactRoot.render(
        <StrictMode>
          <CvView document={doc} photoUrl={payload.photo ?? null} />
        </StrictMode>,
      )
    } else if (payload.kind === 'letter') {
      const doc = parseLetterDocument(payload.document)
      document.documentElement.lang = doc.language
      reactRoot.render(
        <StrictMode>
          <LetterView document={doc} />
        </StrictMode>,
      )
    } else {
      throw new Error(`Unsupported print document kind: ${String((payload as { kind?: unknown }).kind)}`)
    }

    await nextFrame()
    if (document.fonts?.ready) {
      await document.fonts.ready
    }
    await waitForImages(rootElement)
    await nextFrame()
    syncInlineSeparators(rootElement)
    await nextFrame()

    if (generation === renderGeneration) {
      document.body.dataset.ready = '1'
    }
  } catch (error) {
    if (generation === renderGeneration) {
      document.body.dataset.error = error instanceof Error ? error.message : String(error)
      document.body.dataset.ready = 'error'
    }
  }
}

window.__RADIUS_RENDER__ = renderPayload
window.__RADIUS_SYNC_LAYOUT__ = () => {
  syncInlineSeparators(rootElement)
}

window.addEventListener('radius-document-ready', () => {
  if (window.__RADIUS_DOCUMENT__) {
    void renderPayload(window.__RADIUS_DOCUMENT__)
  }
})

if (window.__RADIUS_DOCUMENT__) {
  void renderPayload(window.__RADIUS_DOCUMENT__)
}
