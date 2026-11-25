import assert from 'node:assert/strict'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const server = await createServer({ root: import.meta.dirname, server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: JobFeedCard } = await server.ssrLoadModule('/src/JobFeedCard.tsx')
  const card = renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: {
      id: 1,
      title: 'Engineer',
      company_name: 'Example',
      location_text: null,
      profile_match: { score: 75, matched_skills: ['Python'], fit_tier: 'recommended' },
    },
    href: '/jobs/1',
    profileId: 3,
    onAction() {},
  }))
  assert.match(card, /75\/100 fit/)
  assert.match(card, /match-recommended/)
  assert.doesNotMatch(card, /AI assessed|Local estimate|Updating match/)

  const refreshing = renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: {
      id: 1,
      title: 'Engineer',
      company_name: 'Example',
      location_text: null,
      profile_match: { score: 99, matched_skills: [], refreshing: true },
    },
    href: '/jobs/1',
    profileId: 3,
    onAction() {},
  }))
  assert.match(refreshing, /Updating match…/)
  assert.match(refreshing, /99\/100 fit/)
  assert.match(refreshing, /match-recommended/)
  assert.doesNotMatch(refreshing, /Local estimate|AI assessed|None identified yet/)

  const pending = renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: {
      id: 1,
      title: 'Engineer',
      company_name: 'Example',
      location_text: null,
      profile_match: { score: 0, matched_skills: [], pending: true },
    },
    href: '/jobs/1',
    profileId: 3,
    onAction() {},
  }))
  assert.match(pending, /Analysing job…/)
  assert.doesNotMatch(pending, /0\/100 fit/)

  const noMatch = renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: { id: 2, title: 'Engineer', company_name: 'Example', location_text: null, profile_match: null },
    href: '/jobs/2',
    profileId: 3,
    onAction() {},
  }))
  assert.doesNotMatch(noMatch, /Updating match…|fit<\/span>/)

  const { AnalysisProgressView, remainingLabel } = await server.ssrLoadModule('/src/AnalysisProgress.tsx')
  const progress = renderToStaticMarkup(React.createElement(AnalysisProgressView, { data: {
    checked_at: '2026-11-24T12:00:00Z',
    postings: { done: 300, queued: 90, retrying: 6, failed: 4, skipped: 12500, last_hour: 30 },
    matches: { done: 120, queued: 0, retrying: 0, last_hour: 12 },
  } }))
  assert.match(progress, /Matching<\/h2>.*Working through the queue/)
  assert.match(progress, /Postings read.*<b>300<\/b> of 400 open postings/)
  assert.match(progress, /role="progressbar"[^>]*aria-valuemax="400"[^>]*aria-valuenow="300"/)
  assert.match(progress, /class="is-done" style="width:75%"/)
  assert.match(progress, /90 queued · 6 retrying later · 4 failed/)
  assert.match(progress, /about 3 h left/)
  assert.match(progress, /class="analysis-progress-skipped">12,500 skipped \(older or low similarity\)<\/p>/)
  assert.doesNotMatch(progress, /is-skipped/)
  assert.match(progress, /Matches scored.*<b>120<\/b> of 120 profile matches/)
  assert.equal(remainingLabel({ done: 0, queued: 5, retrying: 0, last_hour: 0 }), 'starting…')
  assert.equal(remainingLabel({ done: 5, queued: 10, retrying: 0, last_hour: 60 }), 'about 10 min left')
  assert.equal(remainingLabel({ done: 5, queued: 0, retrying: 0, skipped: 9999, last_hour: 60 }), null)
  const idle = renderToStaticMarkup(React.createElement(AnalysisProgressView, { data: {
    checked_at: '', postings: { done: 3, queued: 0, retrying: 0, failed: 0, skipped: 0, last_hour: 0 },
    matches: { done: 3, queued: 0, retrying: 0, last_hour: 0 } } }))
  assert.match(idle, /Up to date/)
  assert.doesNotMatch(idle, /analysis-progress-skipped/)

  const { default: JobEvidence, JobPostingAnalysis } = await server.ssrLoadModule('/src/JobEvidence.tsx')
  const postingEvidence = (state, analysis = null) =>
    renderToStaticMarkup(React.createElement(JobPostingAnalysis, { state, analysis }))
  const analysedPosting = postingEvidence('ready', {
    occupation: 'archival science',
    industry: 'museums',
    duties: [{ line: 1, text: 'Catalogue new acquisitions.' }],
    requirements: [
      { line: 2, text: 'Archival training required.', need: 'must', kind: 'education' },
      { line: 3, text: 'Latin is a plus.', need: 'nice', kind: 'language' },
    ],
  })
  assert.match(analysedPosting, /What this job asks for/)
  assert.match(analysedPosting, /archival science.*museums/)
  assert.match(analysedPosting, /Duties.*<q>Catalogue new acquisitions\.<\/q>/)
  assert.match(analysedPosting, /Must-haves.*Education.*<q>Archival training required\.<\/q>/)
  assert.match(analysedPosting, /Nice-to-haves.*Language.*<q>Latin is a plus\.<\/q>/)
  const earlier = { occupation: 'archival science', industry: 'museums', duties: [],
    requirements: [{ line: 2, text: '<b>Archival</b> training required.', need: 'must', kind: 'education' }] }
  const outdated = (refreshing) =>
    renderToStaticMarkup(React.createElement(JobPostingAnalysis, { state: 'outdated', analysis: earlier, refreshing }))
  assert.match(outdated(true), /role="status">Earlier analysis — updating…/)
  assert.match(outdated(false), /role="status">Earlier analysis</)
  assert.match(outdated(false), /<q>&lt;b&gt;Archival&lt;\/b&gt; training required\.<\/q>/)
  assert.doesNotMatch(outdated(false), /<b>Archival/)
  assert.match(postingEvidence('pending'), /role="status">Reading this posting…/)
  assert.match(postingEvidence('failed'), /Could not read this posting/)
  assert.match(postingEvidence('unavailable'), /Posting analysis is unavailable/)

  const enrichedEvidence = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [{ name: 'Legacy skill', evidence: 'Legacy evidence', source: 'description' }],
      warnings: ['Salary not stated'],
      seniority: { value: 'senior', evidence: 'Senior Archivist', source: 'title' },
      experience_years: { value: 3, evidence: '3 years of experience', source: 'description' },
      languages: [{ value: 'German', level: 'C1', evidence: 'Deutsch C1', source: 'description' }] },
  }))
  assert.match(enrichedEvidence, /Experience level:.*Senior Archivist/)
  assert.match(enrichedEvidence, /Minimum experience:.*3 years of experience/)
  assert.match(enrichedEvidence, /Language requirements.*Deutsch C1/)
  assert.match(enrichedEvidence, /Information gaps.*Salary not stated/)
  assert.doesNotMatch(enrichedEvidence, /Evidence from the job post|Legacy skill/)
  const pendingEvidence = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: { score: 0, pending: true, eligible: true, uncertain: true, label: '', matched_skills: [],
      missing_skills: [], reasons: [], conflicts: [], unknowns: [] },
  }))
  assert.match(pendingEvidence, /Analysing this job/)
  assert.doesNotMatch(pendingEvidence, /0\/100|No skills recognized yet/)
  const pendingConflict = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: { score: 0, pending: true, eligible: false, uncertain: true, label: '', matched_skills: [],
      missing_skills: [], reasons: [], conflicts: ['German C2 required; your level is B1'], unknowns: [],
      prerequisite_conditions: [{ kind: 'language_cefr', status: 'fail', reason: 'German level too low',
        job_quote: 'Deutsch auf Muttersprachniveau' }] },
  }))
  assert.match(pendingConflict, /Requirement or preference conflict/)
  assert.match(pendingConflict, /German C2 required/)
  assert.match(pendingConflict, /Deutsch auf Muttersprachniveau/)
  assert.doesNotMatch(pendingConflict, /appears here and in Journey/)
  const evidence = review => renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: {
      score: 85,
      fit_tier: 'unlikely',
      eligible: true,
      mandatory_review: review,
      matched_skills: [],
      missing_skills: [],
      reasons: [],
      conflicts: [],
      unknowns: ['Verify work authorization.'],
    },
  }))
  assert.match(evidence(true), /Verify mandatory eligibility/)
  assert.doesNotMatch(evidence(true), /<h2>Little match evidence/)
  assert.match(evidence(false), /<h2>Little match evidence/)

  const breakdown = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: {
      score: 57, eligible: true, fit_tier: 'possible', language_review: true,
      matched_skills: [], missing_skills: [], reasons: [], conflicts: [], unknowns: [],
      requirement_gaps: ['Technische Ausbildung'],
      job_context: { occupation: 'surface engineering', industry: 'automotive' },
      calibration: { raw: 47.6, calibrated: 57, version: 'v3-fixture' },
      explanation: [
        { signal: 'duties', contribution: 20.5, max_points: 32, supported: 3, total: 5, detail: 'Duty evidence' },
        { signal: 'must', contribution: 9.1, max_points: 28, supported: 1, total: 3, detail: 'Mandatory requirements' },
        { signal: 'role', contribution: 12, max_points: 17, supported: null, total: null, detail: 'Role fit' },
        { signal: 'domain', contribution: 10, max_points: 17, supported: null, total: null, detail: 'Field fit' },
        { signal: 'penalty:work_style', contribution: -4, max_points: null, detail: 'Work style differs' },
      ],
      duty_evidence: { score: 60, coverage: 0.6, duties: [
        { job_quote: 'Maintain delivery schedules.', status: 'transferable', evidence_source: 'cv', evidence_quote: 'Planned production timelines.' },
        { job_quote: 'Run coating audits.', status: 'unverified', evidence_source: '', evidence_quote: '' },
      ] },
      requirement_observations: [
        { kind: 'education', requirement: 'Technische Ausbildung', status: 'unknown', job_quote: 'Technische Ausbildung',
          evidence_source: '', evidence_quote: '', modality: 'must', strength: 'none' },
        { kind: 'skill', requirement: 'SAP', status: 'supported', job_quote: 'SAP', evidence_source: 'skills',
          evidence_quote: 'SAP S/4HANA', modality: 'nice', strength: 'direct' },
      ],
      prerequisite_conditions: [
        { kind: 'language_cefr', status: 'unknown', reason: 'Verify German level', job_quote: 'Deutsch C1' },
      ],
      soft_requirements: [{ text: 'Strukturierte und selbstständige Arbeitsweise', modality: 'must' }],
    },
  }))
  assert.match(breakdown, /aria-label="How the match score was built"/)
  assert.match(breakdown, /aria-label="Personal qualities"[\s\S]*Strukturierte und selbstständige Arbeitsweise[\s\S]*cover letter/)
  assert.match(breakdown, /<strong>57<\/strong>/)
  assert.match(breakdown, /Main duties/)
  assert.match(breakdown, /3 of 5 shown in your CV/)
  assert.match(breakdown, /20\.5<\/strong><small> \/ 32/)
  assert.match(breakdown, /Work style[\s\S]*\u22124/)
  assert.match(breakdown, /Points <strong>47\.6<\/strong>/)
  assert.match(breakdown, /Match score <strong>57<\/strong>/)
  assert.match(breakdown, /Planned production timelines/)
  assert.match(breakdown, /aria-label="Partly shown"/)
  assert.match(breakdown, /aria-label="Not shown yet"/)
  assert.match(breakdown, /aria-label="Nice-to-haves"[\s\S]*SAP S\/4HANA/)
  assert.match(breakdown, /Deutsch C1/)
  assert.match(breakdown, /Verify language proficiency/)
  // The checklist already lists unmet must-haves, so the separate gap list is not repeated.
  assert.doesNotMatch(breakdown, /Qualification gaps to check/)
  assert.doesNotMatch(breakdown, /AI assessed|Local estimate/)
  console.log('Local match display assertions passed')

  const { CvView } = await server.ssrLoadModule('/src/documents/CvView.tsx')
  const unit = (id, text) => ({ id, text, lead_in: '', origin: 'source', sources: [], role: '', hidden: false })
  const cv = renderToStaticMarkup(React.createElement(CvView, { document: {
    format: 'radius-cv-v2', language: 'en', template: 'classic-serif-v1', page: 'a4', photo: false,
    header: { name: 'Alex Example', contacts: [] }, headline: unit('headline', 'Product'),
    sections: [{ id: 'experience', kind: 'experience', title: 'Experience', group: '', layout: 'bullets', units: [], entries: [
      { id: 'work-1', kind: 'work', label: '', intro: [], units: [unit('w1', 'Did things.')],
        locked: { company: 'Example GmbH', title: 'Product Manager', location: 'Heidelberg', start: '2023-07', end: '2025-04', current: false } },
      { id: 'work-2', kind: 'work', label: '', intro: [], units: [],
        locked: { company: 'Other AG', title: 'Analyst', location: '', start: '2025-05', end: null, current: true } },
    ] }],
  } }))
  // Role first with the location, then the employer with MM/YYYY dates.
  assert.match(cv, /doc-cv-entry-primary">Product Manager<\/span><span class="doc-cv-entry-location">Heidelberg/)
  assert.match(cv, /doc-cv-entry-secondary">Example GmbH<\/span><span class="doc-cv-entry-dates">07\/2023 – 04\/2025/)
  assert.match(cv, /doc-cv-entry-dates">05\/2025 – present/)
  console.log('CV entry header assertions passed')

  // The theme helper reads the stored preference while rendering.
  globalThis.window ??= { localStorage: { getItem: () => null, setItem() {} } }
  const { default: SettingsPage } = await server.ssrLoadModule('/src/SettingsPage.tsx')
  const settings = renderToStaticMarkup(React.createElement(SettingsPage))
  const order = ['<h1>Settings</h1>', 'aria-label="Settings sections"', 'aria-label="Status"', 'id="matching-heading"',
    'id="applications-heading"', 'id="sources-heading"', 'id="appearance-heading"']
  const positions = order.map(marker => settings.indexOf(marker))
  assert.ok(positions.every(position => position >= 0), `missing settings landmark: ${order[positions.indexOf(-1)]}`)
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, 'settings sections keep their order')
  assert.match(settings, /href="#applications">Applications</)
  assert.match(settings, /role="radiogroup" aria-labelledby="theme-heading"/)
  assert.doesNotMatch(settings, /class="eyebrow"/)
  console.log('Settings layout assertions passed')
} finally {
  await server.close()
}
