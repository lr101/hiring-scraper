import assert from 'node:assert/strict'

const handoff = await import('../frontend/src/applicationHandoff.ts').catch(() => null)
assert.equal(typeof handoff?.buildApplicationPrompt, 'function', 'Job-specific application handoff must be implemented')
const { buildApplicationPrompt, parseApplicationResponse, documentHTML, documentPlainText } = handoff
const job = { id: 42, title: 'Project coordinator', company_name: 'Example GmbH', url: 'https://example.org/jobs/42',
  description: '<h2>Your role</h2><p>Coordinate &amp; plan projects.</p><script>steal()</script>',
  location_text: 'Heidelberg', company_website: 'https://example.org' }
const profile = { name: 'Career profile', summary: 'Product coordination', skills: ['CAD'],
  skill_evidence: { CAD: { context: 'academic', note: 'University prototype' } },
  language_levels: { German: 'B2' }, education: [], certifications: [] }

// Losing the posting, evidence context, or response contract breaks a usable round trip.
const prompt = buildApplicationPrompt({ job, profile, language: 'English', cvText: 'My original CV', notes: 'Part-time preferred' })
assert.doesNotMatch(prompt, /\[CONFIRM:|only when I confirm|requiring confirmation/i,
  'A finished personalized application must not require another confirmation or contain confirmation placeholders')
for (const value of ['https://example.org/jobs/42', 'Example GmbH', 'https://example.org', 'Coordinate & plan projects.',
  'German', 'B2', 'academic', 'University prototype', 'My original CV', 'Part-time preferred',
  'radius-application-v1', 'cover_letter', 'application_answers', 'review_notes']) assert.ok(prompt.includes(value), value)
assert.ok(!prompt.includes('<script>') && !prompt.includes('steal()'), 'Scripts are not job-description evidence')
assert.match(prompt, /do not invent/i)
assert.match(buildApplicationPrompt({ job: { ...job, description: null }, language: 'English' }), /description.*not available/i)
assert.match(buildApplicationPrompt({ job, language: 'English' }), /personal context already available/i)
assert.doesNotMatch(prompt, /I must attach|Use my attached original CV/i, 'A saved, extracted CV must not require a repeated attachment')
assert.match(prompt, /original_cv_text.*factual source/i)
assert.equal(handoff.applicationStartLink(42, 9), '/jobs/42/apply?profile_id=9')
assert.equal(handoff.applicationStartLink(42), '/jobs/42/apply')
const template = [{ id: 0, text: 'Alex' }, { id: 7, text: 'Original bullet' }]
const templatePrompt = buildApplicationPrompt({ job, language: 'English', cvText: 'Original CV', templateParagraphs: template })
assert.match(templatePrompt, /JSON array of.*paragraph/i)
assert.ok(templatePrompt.includes('"id": 7'))
assert.deepEqual(handoff.parseTemplateChanges('[{"id":7,"text":"Verified edited bullet"}]', template), [{ id: 7, text: 'Verified edited bullet' }])
for (const bad of ['ordinary CV', '[{"id":999,"text":"Unknown"}]', '[{"id":7,"text":9}]', '[{"id":7,"text":"A"},{"id":7,"text":"B"}]']) {
  assert.throws(() => handoff.parseTemplateChanges(bad, template), /template|paragraph|JSON/i)
}

const response = '<<<CV>>>\n# Alex Example\n## Experience\n- Coordinated projects\n<<<COVER_LETTER>>>\nDear team,\n<<<APPLICATION_ANSWERS>>>\nNotice: unknown\n<<<REVIEW_NOTES>>>\nVerify the dates.'
const parsed = parseApplicationResponse(response)
assert.equal(parsed.cv, '# Alex Example\n## Experience\n- Coordinated projects')
assert.equal(parsed.coverLetter, 'Dear team,')
assert.equal(parsed.answers, 'Notice: unknown')
assert.equal(parsed.reviewNotes, 'Verify the dates.')
assert.deepEqual(parseApplicationResponse('```markdown\n' + response + '\n```'), parsed)
// Malformed output must not silently replace existing user edits with a partial import.
for (const bad of ['ordinary answer', response.replace('<<<CV>>>', ''), response + '\n<<<CV>>>\nDuplicate',
  response.replace('<<<COVER_LETTER>>>', '<<<REVIEW_NOTES>>>'), response.replace('Dear team,', ''),
  'Some introduction\n' + response]) assert.throws(() => parseApplicationResponse(bad), /format|section|empty/i)

// AI content is rendered as escaped text, never executable HTML or remote images.
const html = documentHTML('# Alex <script>alert(1)</script>\n## Experience\n- A & B\n![pixel](https://tracker.invalid/x)', 'CV')
assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'))
assert.ok(html.includes('<h1>Alex '))
assert.ok(!html.includes('<script>') && !html.includes('<img') && !html.includes('src='))
assert.ok(html.includes('@page') && html.includes('A4'))
assert.equal(documentPlainText('# Alex\n## Experience\n- **Product coordination**'), 'Alex\nExperience\n• Product coordination')
console.log('Application prompt, response round trip, malformed imports and safe document export passed')

// The new return contract is one copyable block, bound to a job, profile and CV.
assert.equal(typeof handoff.parseApplicationPackage, 'function', 'A single application package must import into the document editor')
const context = { job_id: 42, profile_id: 9, cv_updated_at: '2026-10-16T12:00:00Z' }
const applicationPackage = { format: 'radius-application-v1', ...context,
  cv: [{ id: 7, text: 'Koordination für München – 你好 🚀' }],
  cover_letter: 'Dear team,\nTruthful application.', application_answers: 'Notice: unknown', review_notes: 'Check dates.' }
const imported = handoff.parseApplicationPackage(JSON.stringify(applicationPackage), context, template)
assert.equal(imported.documents.coverLetter, 'Dear team,\nTruthful application.')
assert.deepEqual(imported.changes, [{ id: 7, text: 'Koordination für München – 你好 🚀' }])
const fenced = 'Your application package:\n```json\n' + JSON.stringify(applicationPackage) + '\n```\nUse Copy on this block.'
assert.deepEqual(handoff.parseApplicationPackage(fenced, context, template), imported)
for (const change of [{ job_id: 43 }, { profile_id: 10 }, { cv_updated_at: 'stale' }, { format: 'other' },
  { cover_letter: '' }, { cv: [{ id: 9999, text: 'Unknown' }] }, { cv: null }, { review_notes: 123 }]) {
  assert.throws(() => handoff.parseApplicationPackage(JSON.stringify({ ...applicationPackage, ...change }), context, template))
}
assert.throws(() => handoff.parseApplicationPackage(fenced + '\n```json\n{}\n```', context, template), /one|single|format/i)
const textPackage = { ...applicationPackage, cv: '# Complete CV' }
assert.equal(handoff.parseApplicationPackage(JSON.stringify(textPackage), context).documents.cv, '# Complete CV')
assert.throws(() => handoff.parseApplicationPackage(JSON.stringify(applicationPackage), context), /CV|template/i)
const base = 'https://radius.example/jobs/42/apply?profile_id=9'
const returnURL = handoff.buildApplicationReturnURL(base, applicationPackage)
assert.ok(returnURL.startsWith(base + '#application='))
assert.equal(new URL(returnURL).search, '?profile_id=9', 'Documents must stay out of the server query')
assert.equal(handoff.applicationReturnInput(returnURL, base), JSON.stringify(applicationPackage))
assert.deepEqual(handoff.parseApplicationPackage(handoff.decodeApplicationReturn(new URL(returnURL).hash), context, template), imported)
assert.equal(handoff.decodeApplicationReturn('#profile-cv'), null)
assert.throws(() => handoff.decodeApplicationReturn('#application=%E0%A4%A'), /link|decode/i)
for (const bad of [returnURL.replace('radius.example', 'attacker.invalid'), returnURL.replace('/42/', '/43/'),
  returnURL.replace('profile_id=9', 'profile_id=10'), 'javascript:alert(1)']) {
  assert.throws(() => handoff.applicationReturnInput(bad, base), /link|package|format/i)
}
assert.equal(handoff.buildApplicationReturnURL(base, { ...applicationPackage, cover_letter: 'x'.repeat(30000) }), null,
  'Long returns must use a file or the copy block instead of a fragile link')
console.log('Single-block package, Unicode return link, context binding, malformed input and link length tests passed')

// Unfinished drafting instructions must never become download-ready documents.
for (const change of [
  { cv: [{ id: 7, text: '[CONFIRM: employment dates]' }] },
  { cover_letter: 'Dear team,\n[INSERT relevant achievement]' },
  { application_answers: 'Please confirm your notice period before sending.' },
  { review_notes: 'Awaiting your approval before using this application.' },
]) {
  assert.throws(() => handoff.parseApplicationPackage(JSON.stringify({ ...applicationPackage, ...change }), context, template), /unfinished|placeholder|drafting/i)
}
assert.throws(() => parseApplicationResponse(response.replace('Dear team,', '[TODO: complete letter]')), /unfinished|placeholder|drafting/i)
assert.doesNotMatch(buildApplicationPrompt({ job: { ...job, description: null }, language: 'English' }), /ask me to paste|only when I confirm/i)
console.log('Unfinished model output is rejected rather than exported, including legacy replies')

// Losing compressed decoding would break the link-only return from ChatGPT.
assert.equal(typeof handoff.readApplicationReturn, 'function', 'Compressed return links must open without copying JSON')
const { deflateSync } = await import('node:zlib')
const compressedHash = (text) => '#applicationz=' + deflateSync(Buffer.from(text)).toString('base64url')
const compressed = compressedHash(JSON.stringify(applicationPackage))
assert.equal(await handoff.readApplicationReturn(compressed), JSON.stringify(applicationPackage))
assert.equal(await handoff.readApplicationReturn(new URL(returnURL).hash), JSON.stringify(applicationPackage))
assert.equal(await handoff.readApplicationReturn('#other'), null)
assert.equal(await handoff.readApplicationReturnInput(base + compressed, base), JSON.stringify(applicationPackage))
await assert.rejects(() => handoff.readApplicationReturnInput(base.replace('/42/', '/43/') + compressed, base), /different application/i)
for (const invalid of ['#applicationz=broken', '#applicationz=+not_base64', compressed.slice(0, -5),
  compressedHash('x'.repeat(200001)), compressedHash('ü'.repeat(110000))]) {
  await assert.rejects(() => handoff.readApplicationReturn(invalid), /link|large|decode|package/i)
}
// Execute the actual handoff recipe, catching Python/JS encoding mismatches and long-link fallback.
const { execFileSync } = await import('node:child_process')
const linkPrompt = buildApplicationPrompt({ job, language: 'English', templateParagraphs: template, handoff: { ...context, return_url: base } })
const recipe = linkPrompt.match(/RETURN_LINK_RECIPE_BEGIN\n([\s\S]*?)\nRETURN_LINK_RECIPE_END/)[1]
const generated = execFileSync('python3', ['-c', 'import json\napplication_package = json.loads(' + JSON.stringify(JSON.stringify(applicationPackage)) + ')\n' + recipe], { encoding: 'utf8' }).trim()
const generatedURL = generated.match(/^\[Return to Radius with my documents\]\((.*)\)$/)?.[1]
assert.ok(generatedURL?.startsWith(base + '#applicationz='), 'ChatGPT recipe should create a compressed return link')
assert.deepEqual(JSON.parse(await handoff.readApplicationReturn(new URL(generatedURL).hash)), applicationPackage)
console.log('Compressed Python return links, legacy compatibility, Unicode, malformed/truncated input and bounded decompression passed')
// A result that cannot fit must never emit a truncated link disguised as success.
const { createHash } = await import('node:crypto')
const longText = Array.from({ length: 1000 }, (_, index) => createHash('sha256').update(String(index)).digest('hex')).join('')
const tooLong = { ...applicationPackage, cover_letter: longText }
const fallback = execFileSync('python3', ['-c', 'import json,sys\napplication_package = json.load(sys.stdin)\n' + recipe], { input: JSON.stringify(tooLong), encoding: 'utf8' }).trim()
assert.equal(fallback, 'RETURN_FILE_REQUIRED')
await assert.rejects(() => handoff.readApplicationReturn('#applicationz=' + deflateSync(Buffer.from([0xff, 0xfe])).toString('base64url')), /decode/i)
console.log('Unusually large non-repetitive packages fall back to a file; invalid UTF-8 is rejected')

// Saved instructions and one-off instructions must survive into the actual handoff,
// outside quoted website/CV data; otherwise the prompt tells ChatGPT to ignore them.
const savedInstructions = 'Write in German.\nPrefer direct sentences and explain my research motivation.'
const writingPrompt = buildApplicationPrompt({ job, profile: { ...profile, custom_instructions: savedInstructions }, language: 'the language of the job posting', notes: 'Keep this letter under 250 words.' })
const preferencesMatch = writingPrompt.match(/WRITING PREFERENCES \(applicant instructions\):\n([\s\S]*?)\n\nSOURCE PAYLOAD/)
assert.ok(preferencesMatch, 'User writing preferences must be separate from untrusted source material')
assert.deepEqual(JSON.parse(preferencesMatch[1]), {
  document_language: { mode: 'match_job' },
  profile_instructions: savedInstructions,
  application_instructions: 'Keep this letter under 250 words.',
})
const explicitPrompt = buildApplicationPrompt({ job, profile: { ...profile, custom_instructions: 'Write in German.' }, language: 'English' })
const explicitPreferences = JSON.parse(explicitPrompt.match(/WRITING PREFERENCES \(applicant instructions\):\n([\s\S]*?)\n\nSOURCE PAYLOAD/)[1])
assert.deepEqual(explicitPreferences.document_language, { mode: 'explicit', language: 'English' })
const noPreferences = buildApplicationPrompt({ job, language: 'the language of the job posting' })
assert.equal(JSON.parse(noPreferences.match(/WRITING PREFERENCES \(applicant instructions\):\n([\s\S]*?)\n\nSOURCE PAYLOAD/)[1]).profile_instructions, null)
console.log('Saved and per-application writing preferences reach the prompt independently of language selection and quoted source data')

// Generic names make applications easy to confuse; company text must also be safe
// on Windows/Android and must not turn into a path or a different extension.
assert.equal(typeof handoff.applicationDocumentFilename, 'function', 'Exports must be named for the employer')
assert.equal(handoff.applicationDocumentFilename('rösberg', 'cv', 'pdf'), 'rösberg-CV.pdf')
assert.equal(handoff.applicationDocumentFilename('Example GmbH', 'cv', 'docx'), 'Example-GmbH-CV.docx')
assert.equal(handoff.applicationDocumentFilename('ACME / Research: Europe?', 'coverLetter', 'pdf'), 'ACME-Research-Europe-Cover-letter.pdf')
assert.equal(handoff.applicationDocumentFilename(' ../<>:"|?*\u0000 ', 'cv', 'docx'), 'Application-CV.docx')
assert.equal(handoff.applicationDocumentFilename('株式会社みらい', 'answers', 'pdf'), '株式会社みらい-Application-answers.pdf')
assert.ok(handoff.applicationDocumentFilename('A'.repeat(500), 'cv', 'pdf').length < 120)
console.log('Company-based document filenames preserve names and reject path/control characters')
