import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { buildApplicationPrompt, parseApplicationPackage, readApplicationReturn } from '../frontend/src/applicationHandoff.ts'
import { buildApplicationPrompt as localPrompt, parseReturnedPackage, saveDraft, readDraft } from '../frontend/src/applicationPreparation.ts'

const template = JSON.parse(readFileSync(new URL('../hiring_scraper/app/latex_template.json', import.meta.url)))
const sources = { cv: template.preamble + '\\section*{José — Canvas CV}\nR\\&D in Genève 2021-2024.\\par\n' + template.ending,
  cover_letter: template.preamble + 'Dear team,\\par\nMy Canvas letter.\\par\n' + template.ending }
const context = { job_id: 42, profile_id: 9, cv_updated_at: 'cv-v1' }
const pack = { format: 'radius-application-v1', ...context, cv: '# José\nResearch in Genève', cover_letter: 'Dear team,\nMy letter.', application_answers: 'None', review_notes: 'Personalized.', latex_documents: sources }
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
