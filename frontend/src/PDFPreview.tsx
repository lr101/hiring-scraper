import { useEffect, useRef, useState } from 'react'
import type { PDFDocumentProxy, PDFDocumentLoadingTask, RenderTask } from 'pdfjs-dist'
import workerURL from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

// Draw the actual exported PDF. Mobile browsers may offer only an Open button
// for PDF iframes; this viewer does not depend on a native PDF plugin.
export default function PDFPreview({ url }: { url: string }) {
  const container = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [width, setWidth] = useState(0)
  const [ready, setReady] = useState(false)
  const [pageText, setPageText] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    let loading: PDFDocumentLoadingTask | undefined
    setDocument(null); setPageNumber(1); setReady(false); setError('')
    void import('pdfjs-dist').then(async pdfjs => {
      if (cancelled) return
      pdfjs.GlobalWorkerOptions.workerSrc = workerURL
      loading = pdfjs.getDocument({ url, enableXfa: false, useWasm: false })
      const pdf = await loading.promise
      if (!cancelled) setDocument(pdf)
    }).catch(() => {
      if (!cancelled) setError('Could not display this PDF. You can still download your documents.')
    })
    return () => { cancelled = true; void loading?.destroy().catch(() => {}) }
  }, [url, attempt])

  useEffect(() => {
    const node = container.current
    if (!node) return
    const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!document || !width || !canvas.current) return
    let cancelled = false
    let render: RenderTask | undefined
    setReady(false); setError(''); setPageText('')
    void (async () => {
      const page = await document.getPage(pageNumber)
      if (cancelled) return
      const original = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: Math.min(width, 1000) / original.width })
      // Bound canvas memory on high-DPI phones and very large viewports.
      const scale = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(2000000 / (viewport.width * viewport.height)))
      const node = canvas.current!
      node.width = Math.max(1, Math.floor(viewport.width * scale))
      node.height = Math.max(1, Math.floor(viewport.height * scale))
      render = page.render({ canvas: node, viewport, transform: [scale, 0, 0, scale, 0, 0] })
      await render.promise
      const text = await page.getTextContent()
      if (cancelled) return
      setPageText(text.items.flatMap(item => 'str' in item ? [item.str] : []).join(' '))
      setReady(true)
    })().catch(() => {
      if (!cancelled) setError('Could not display this PDF page. You can still download your documents.')
    })
    return () => { cancelled = true; render?.cancel() }
  }, [document, pageNumber, width])

  return <div ref={container} className="cv-pdf-viewer" role="region" aria-label="CV preview" title="Original template PDF preview" aria-busy={!ready && !error}>
    {error ? <div className="cv-preview-message"><p role="alert">{error}</p><button onClick={() => setAttempt(value => value + 1)}>Retry preview</button></div>
      : <>
        {!ready && <p role="status" className="cv-preview-message">Rendering CV preview…</p>}
        {document && <nav className="cv-preview-pages" aria-label="CV preview pages">
          <button aria-label="Previous CV page" disabled={!ready || pageNumber === 1} onClick={() => setPageNumber(value => value - 1)}>←</button>
          <span>Page {pageNumber} of {document.numPages}</span>
          <button aria-label="Next CV page" disabled={!ready || pageNumber === document.numPages} onClick={() => setPageNumber(value => value + 1)}>→</button>
        </nav>}
      </>}
    <canvas ref={canvas} hidden={!ready || !!error} role="img" aria-label={`CV page ${pageNumber} of ${document?.numPages ?? 0}`} aria-description={pageText} />
  </div>
}
