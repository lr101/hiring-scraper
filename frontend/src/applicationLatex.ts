import { parseTexCV, validateTailoredCV } from '../../extensions/application-companion/tex-cv.js'
import { canvasLatexInstructions as sharedCanvasLatexInstructions } from '../../extensions/application-companion/latex-workflow.js'
import template from '../../hiring_scraper/app/latex_template.json' with { type: 'json' }

export type LatexDocuments = { cv?: string; cover_letter?: string }

export function validateLatexDocuments(value: unknown): LatexDocuments | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid LaTeX documents in application package.')
  const result: LatexDocuments = {}
  for (const [key, source] of Object.entries(value)) {
    if (!['cv', 'cover_letter'].includes(key) || typeof source !== 'string' || !source.trim() || source.length > 100000
      || (!(key === 'cv' && source.includes('% radius:entry ')) && (!(source.startsWith(template.preamble) || (key === 'cover_letter' && source.startsWith(template.letter_preamble))) || !source.endsWith(template.ending)))) {
      throw new Error('Invalid LaTeX source. Use the supplied Radius template and complete document text.')
    }
    if (key === 'cv' && source.includes('% radius:entry ')) parseTexCV(source)
    result[key as keyof LatexDocuments] = source
  }
  return result
}

export function canvasLatexInstructions(language = 'en', originalCV = '') {
  return sharedCanvasLatexInstructions(template, language, originalCV)
}

// Browser-only CVs have no saved server template. Validate against their local
// original and return the source for a direct download without an API roundtrip.
export function localCVSource(originalCV: string, documents?: LatexDocuments): string | null {
  if (!originalCV.includes('% radius:entry ')) return null
  validateTailoredCV(originalCV, documents?.cv)
  return documents!.cv!
}
