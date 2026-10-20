import template from '../../hiring_scraper/app/latex_template.json' with { type: 'json' }

export type LatexDocuments = { cv?: string; cover_letter?: string }

export function validateLatexDocuments(value: unknown): LatexDocuments | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid LaTeX documents in application package.')
  const result: LatexDocuments = {}
  for (const [key, source] of Object.entries(value)) {
    if (!['cv', 'cover_letter'].includes(key) || typeof source !== 'string' || !source.trim() || source.length > 100000
      || !source.startsWith(template.preamble) || !source.endsWith(template.ending)) {
      throw new Error('Invalid LaTeX source. Use the supplied Radius template and complete document text.')
    }
    result[key as keyof LatexDocuments] = source
  }
  return result
}

export function canvasLatexInstructions() {
  return `CANVAS LATEX WORKFLOW:
Open code Canvases named tailored-cv.tex and cover-letter.tex and save the complete LaTeX source in them. Use a plain code/text Canvas, not rendered rich text or a Markdown fence. Finish the application without pausing for approval. Keep Canvas source and the normal cv/cover_letter fields factually consistent; the latter remain the readable content and DOCX fallback. For a DOCX cv edit array, the CV Canvas must contain the ENTIRE edited CV, including unchanged paragraphs and known contact details, not just the changed paragraphs.
Use this exact preamble and ending for EACH document, replacing only the body between them. Keep layout/package/font declarations unchanged. The body may use only plain escaped text, balanced groups, \\section*{...}, \\subsection*{...}, \\textbf{...}, \\emph{...}, \\par, and \\begin{itemize} / \\item / \\end{itemize}. Escape literal backslash as \\textbackslash{}, braces as \\{ and \\}, and &, %, $, #, _ as \\&, \\%, \\$, \\#, \\_. Use \\textasciitilde{} and \\textasciicircum{} for ~ and ^. Do not use comments in the body, Markdown, unsupported commands, external assets, custom macros or additional packages. Preserve literal dates and Unicode names.
LATEX_PREAMBLE_BEGIN
${template.preamble}LATEX_PREAMBLE_END
LATEX_ENDING_BEGIN
${template.ending}LATEX_ENDING_END
After saving, read the latest exact source from each Canvas into application_package.latex_documents.cv and application_package.latex_documents.cover_letter. These values must be full strings, never Canvas IDs, shared-Canvas links, filenames or sandbox paths. Generate the return link from that completed package only AFTER the Canvases have been saved. The link is a snapshot of their contents; after any Canvas edit, synchronize the readable document fields and regenerate the package and link.
If Canvas is unavailable, create downloadable .tex files instead and put their same exact contents in latex_documents. State that Canvas was unavailable; do not pretend a Canvas was created. If tools cannot access the latest Canvas contents, do not guess: offer the .tex files and a complete application-package.json file containing those exact sources. A Canvas share URL alone cannot return application documents to Radius.`
}
