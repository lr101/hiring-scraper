import { canvasLatexInstructions as sharedCanvasLatexInstructions } from '../../extensions/application-companion/latex-workflow.js'
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
  return sharedCanvasLatexInstructions(template)
}
