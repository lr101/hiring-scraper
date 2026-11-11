import assert from 'node:assert/strict'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const server = await createServer({ root: import.meta.dirname, server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: JobFeedCard } = await server.ssrLoadModule('/src/JobFeedCard.tsx')
  const render = source => renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: { id: 1, title: 'Engineer', company_name: 'Example', location_text: null,
      profile_match: { score: 75, matched_skills: [], score_source: source } },
    href: '/jobs/1', onAction() {},
  }))
  assert.match(render('llm'), /AI assessed/)
  assert.doesNotMatch(render('local'), /AI assessed/)
  assert.doesNotMatch(render(undefined), /AI assessed/)
  assert.match(render('local'), /Local estimate/)
  assert.match(render(undefined), /Local estimate/)
  assert.match(render('llm'), /75\/100 fit/)
  const { default: JobEvidence } = await server.ssrLoadModule('/src/JobEvidence.tsx')
  const evidence = review => renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: { score: 85, score_source: 'llm', fit_tier: 'unlikely', eligible: true,
      mandatory_review: review, matched_skills: [], missing_skills: [], reasons: [], conflicts: [],
      unknowns: ['Verify work authorization.'] },
  }))
  assert.match(evidence(true), /Verify mandatory eligibility/)
  assert.doesNotMatch(evidence(true), /<h2>Little match evidence/)
  assert.match(evidence(false), /<h2>Little match evidence/)
  const localEvidence = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: { score: 40, task_fit: 85, score_source: 'local', fit_tier: 'possible', eligible: true,
      mandatory_review: true, matched_skills: [], missing_skills: [], reasons: [], conflicts: [], unknowns: [],
      gates: { eligibility: { status: 'unknown', reason: 'Required degree is unverified.' } } },
  }))
  const calculation = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: { score: 50, score_source: 'llm', eligible: true, matched_skills: [], missing_skills: [],
      reasons: [], conflicts: [], unknowns: [], gates: { language: { status: 'unknown', reason: 'Confirm business German.' } },
      llm_match: { weighted_task_fit: 85, qualification_fit: 50, preference_fit: null,
        dimensions: { skills: { score: 90, evidence: 'Documented product skills.' },
          experience: { score: 80, evidence: 'Relevant project delivery.' } },
        weights: { skills: 0.30, experience: 0.25 }, role_evidence_score: 50,
        dimension_completeness: 55, requirement_completeness: 50, requirement_coverage: 100, requirements: [] } },
  }))
  assert.match(calculation, /aria-label="Job score breakdown"/)
  assert.match(calculation, /aria-label="Task assessment"[^>]*aria-valuenow="85"/)
  assert.match(calculation, /aria-label="Role evidence ceiling"[^>]*aria-valuenow="50"/)
  assert.match(calculation, /aria-label="Final qualification score"[^>]*aria-valuenow="50"/)
  assert.match(calculation, /Preference fit.*Unknown/s)
  assert.match(localEvidence, /aria-label="Local shortlist score"[^>]*aria-valuenow="40"/)
  assert.match(localEvidence, /aria-label="Task fit"[^>]*aria-valuenow="85"/)
  assert.match(calculation, /90 × 30%/)
  assert.match(calculation, /80 × 25%/)
  assert.match(calculation, /85.*50.*50/s)
  assert.match(calculation, /Documented product skills/)
  assert.match(calculation, /Confirm business German/)
  assert.match(localEvidence, /Task fit before mandatory requirements and preferences/)
  assert.match(localEvidence, /85\/100/)
  assert.match(localEvidence, /Required degree is unverified/)
  assert.doesNotMatch(localEvidence, /capped at 59/)
} finally {
  await server.close()
}
