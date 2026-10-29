import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { buildApplicationPrompt, parseApplicationPackage, readApplicationReturn } from '../frontend/src/applicationHandoff.ts'
import { buildApplicationPrompt as localPrompt, parseReturnedPackage, saveDraft, readDraft } from '../frontend/src/applicationPreparation.ts'

const template = JSON.parse(readFileSync(new URL('../hiring_scraper/app/latex_template.json', import.meta.url)))
const sources = { cv: template.preamble + '\\section*{José — Canvas CV}\nR\\&D in Genève 2021-2024.\\par\n' + template.ending,
  cover_letter: template.letter_preamble + 'Dear team,\\par\nMy Canvas letter.\\par\n' + template.ending }
const context = { job_id: 42, profile_id: 9, cv_updated_at: 'cv-v1' }
const pack = { format: 'radius-application-v1', ...context, cv: '# José\nResearch in Genève', cover_letter: 'Dear team,\nMy letter.', application_answers: 'None', review_notes: 'Personalized.', latex_documents: sources }
assert.throws(() => parseApplicationPackage(JSON.stringify({...pack, cover_letter: String.raw`Dear \\textbf{team}`}), context), /cover letter.*LaTeX/i)
const parsed = parseApplicationPackage(JSON.stringify(pack), context)
assert.deepEqual(parsed.latexDocuments, sources, 'Canvas sources must survive package parsing verbatim')
for (const bad of [null, [], { cv: 42 }, { cv: 'x'.repeat(100001) }, { unknown: sources.cv }, { cv: '[TODO write CV]' }]) {
  assert.throws(() => parseApplicationPackage(JSON.stringify({ ...pack, latex_documents: bad }), context), /latex|draft|placeholder|unfinished/i)
}
const job = { id: 42, title: 'Researcher', company_name: 'Labs', url: 'https://example.org/job', description: 'Research.' }
const base = 'https://radius.example/jobs/42/apply?profile_id=9'
const prompt = buildApplicationPrompt({ job, language: 'English', handoff: { ...context, return_url: base } })
const recipe = prompt.match(/RETURN_LINK_RECIPE_BEGIN\n([\s\S]*?)\nRETURN_LINK_RECIPE_END/)[1]
const output = execFileSync('python3', ['-c', 'import json,sys\napplication_package=json.load(sys.stdin)\n' + recipe], { input: JSON.stringify(pack), encoding: 'utf8' }).trim()
const url = output.match(/^\[Return to Radius with my documents\]\((.*)\)$/)[1]
const decoded = await readApplicationReturn(new URL(url).hash)
assert.deepEqual(parseApplicationPackage(decoded, context).latexDocuments, sources)
const local = await parseReturnedPackage(url, { jobId: 42, profileId: 9, cvUpdatedAt: 'cv-v1' })
assert.deepEqual(local.latex_documents, sources)
const data = new Map()
const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) }
assert.equal(saveDraft(storage, 42, 9, pack, context), true)
assert.deepEqual(readDraft(storage, 42, 9, 'cv-v1').latex_documents, sources)
const localBrief = localPrompt({ text: 'José research CV', updated_at: 'cv-v1' }, { job: { ...job, location_text: null, employment_type: null, work_arrangement: null }, profile: { id: 9, name: 'José' }, returnUrl: base })
const localRecipe = localBrief.match(/RETURN_LINK_RECIPE_BEGIN\n([\s\S]*?)\nRETURN_LINK_RECIPE_END/)
assert.ok(localRecipe, 'Browser-local preparation also needs a verified compressed Canvas return link')
const localOutput = execFileSync('python3', ['-c', 'import json,sys\napplication_package=json.load(sys.stdin)\n' + localRecipe[1]], { input: JSON.stringify(pack), encoding: 'utf8' }).trim()
const localURL = localOutput.match(/^\[Return to Radius with my documents\]\((.*)\)$/)[1]
assert.deepEqual(JSON.parse(await readApplicationReturn(new URL(localURL).hash)).latex_documents, sources)
console.log('Canvas LaTeX survives executed return-link recipe, saved/local imports, and draft reload; malformed sources are rejected')

// A custom original template survives import verbatim; rebuilding its layout
// or changing anything outside descriptions must fail at the import boundary.
const marked = String.raw`\documentclass[10pt,letterpaper]{article}
\begin{document}
% radius:entry {"id":"work-1","kind":"work","company":"Labs","title":"Engineer","location":"Bonn","start":"2021","end":null,"current":true}
Engineer, Bonn, 2021–present.
% radius:responsibilities
\begin{itemize}
\item Built tools.
\end{itemize}
% radius:end
\end{document}`
const customPack = { ...pack, latex_documents: { ...sources, cv: marked.replace('Built tools.', 'Built useful tools.') } }
assert.equal(parseApplicationPackage(JSON.stringify(customPack), context, undefined, undefined, marked).latexDocuments.cv, customPack.latex_documents.cv)
assert.throws(() => parseApplicationPackage(JSON.stringify({ ...customPack, latex_documents: { ...sources, cv: customPack.latex_documents.cv.replace('letterpaper', 'a4paper') } }), context, undefined, undefined, marked), /structure|layout/i)
assert.throws(() => parseApplicationPackage(JSON.stringify({ ...customPack, latex_documents: { ...sources, cover_letter: marked } }), context, undefined, undefined, marked), /template|latex/i)
assert.equal(saveDraft(storage, 42, 9, customPack, context), true)
assert.equal(readDraft(storage, 42, 9, 'cv-v1').latex_documents.cv, customPack.latex_documents.cv)
console.log('Custom CV template survives import/draft storage; layout changes and custom-template letters are rejected')

await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...customPack, latex_documents: { ...sources, cv: customPack.latex_documents.cv.replace('letterpaper', 'a4paper') } }), {jobId: 42, profileId: 9, cvUpdatedAt: 'cv-v1', originalCV: marked}), /structure|layout/i)
const { localCVSource } = await import('../frontend/src/applicationLatex.ts')
assert.equal(localCVSource(marked, customPack.latex_documents), customPack.latex_documents.cv)
assert.throws(() => localCVSource(marked, {cv: customPack.latex_documents.cv.replace('letterpaper', 'a4paper')}), /structure|layout/i)

const { downloadApplicationDocument } = await import('../frontend/src/applicationDocumentExport.ts')
const previousDocument = globalThis.document
const previousFetch = globalThis.fetch
const clicked = []
globalThis.document = { createElement: tag => {
 assert.equal(tag, 'a')
 return { click() { clicked.push({href: this.href, name: this.download}) } }
} }
globalThis.fetch = () => { throw new Error('Browser-only original CV must not be sent to the API for source download') }
try {
 await downloadApplicationDocument(customPack, 'cv', 'tex', 'my-cv.tex', marked)
 assert.equal(clicked.length, 1)
 assert.equal(clicked[0].name, 'my-cv.tex')
 assert.equal(await (await previousFetch(clicked[0].href)).text(), customPack.latex_documents.cv)
 await assert.rejects(() => downloadApplicationDocument({...customPack, latex_documents: {cv: marked.replace('letterpaper', 'a4paper')}}, 'cv', 'tex', 'my-cv.tex', marked), /structure|layout/i)
 assert.equal(clicked.length, 1, 'Invalid source must never create a download')
} finally {
 globalThis.fetch = previousFetch
 globalThis.document = previousDocument
}
console.log('Browser-only source export produces exact validated bytes without an API request')

// Photo bytes accompany CV render requests but never leak into letter exports.
const photoRequests = []
globalThis.document = { createElement: () => ({ click() {} }) }
globalThis.fetch = async (url, init) => { photoRequests.push({url, body: JSON.parse(init.body)}); return new Response(new Blob(['file'])) }
try {
 await downloadApplicationDocument(pack, 'cv', 'tex_bundle', 'cv.zip', undefined, 'data:image/png;base64,PHOTO')
 assert.equal(photoRequests[0].body.photo_data_url, 'data:image/png;base64,PHOTO')
 assert.ok(photoRequests[0].url.endsWith('kind=cv&format=tex_bundle'))
 await downloadApplicationDocument(pack, 'cover_letter', 'latex_pdf', 'letter.pdf', undefined, 'data:image/png;base64,PHOTO')
 assert.equal(photoRequests[1].body.photo_data_url, undefined)
} finally { globalThis.fetch = previousFetch; globalThis.document = previousDocument }
console.log('CV photo is included in render bundle requests and excluded from letters')
