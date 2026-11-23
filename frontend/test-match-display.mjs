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

  const noMatch = renderToStaticMarkup(React.createElement(JobFeedCard, {
    job: { id: 2, title: 'Engineer', company_name: 'Example', location_text: null, profile_match: null },
    href: '/jobs/2',
    profileId: 3,
    onAction() {},
  }))
  assert.doesNotMatch(noMatch, /Updating match…|fit<\/span>/)

  const { default: JobEvidence } = await server.ssrLoadModule('/src/JobEvidence.tsx')
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

  const localEvidence = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: {
      score: 40,
      task_fit: 85,
      fit_tier: 'possible',
      eligible: true,
      mandatory_review: true,
      matched_skills: [],
      missing_skills: [],
      reasons: [],
      conflicts: [],
      unknowns: [],
      gates: { eligibility: { status: 'unknown', reason: 'Required degree is unverified.' } },
      duty_evidence: {
        score: 50,
        coverage: 0.5,
        duties: [{
          job_quote: 'Maintain delivery schedules.',
          status: 'transferable',
          evidence_source: 'cv_career_facts.education[0].description',
          evidence_quote: 'Planned production timelines.',
          evidence_context: 'academic',
        }],
      },
    },
  }))
  assert.match(localEvidence, /aria-label="Job score breakdown"/)
  assert.match(localEvidence, /aria-label="Task fit"[^>]*aria-valuenow="85"/)
  assert.match(localEvidence, /aria-label="Match score"[^>]*aria-valuenow="40"/)
  assert.match(localEvidence, /Task fit before mandatory requirements and preferences/)
  assert.match(localEvidence, /85\/100/)
  assert.match(localEvidence, /Required degree is unverified/)
  assert.match(localEvidence, /Documented duty fit/)
  assert.match(localEvidence, /Planned production timelines/)
  assert.match(localEvidence, /academic/)
  assert.match(localEvidence, /50% of extracted duties/)
  assert.doesNotMatch(localEvidence, /AI assessed|Local estimate|Role evidence ceiling|capped at 59/)
  const ledger = renderToStaticMarkup(React.createElement(JobEvidence, {
    enrichment: { skills: [], languages: [], warnings: [], seniority: null, experience_years: null },
    match: {
      score: 70, eligible: true, fit_tier: 'possible', language_review: true,
      matched_skills: [], missing_skills: [], reasons: [], conflicts: [], unknowns: [],
      calibration: { raw: 30, calibrated: 70, version: 'v2-fixture' },
      explanation: [
        { signal: 'duty_coverage', contribution: 20, detail: 'Duty evidence', job_quote: 'Build APIs', profile_quote: 'Built APIs' },
        { signal: 'skill_overlap', contribution: 10, detail: 'Skill overlap' },
        { signal: 'role_alignment', contribution: 4, detail: 'Role preference' },
        { signal: 'domain_alignment', contribution: 1, detail: 'Domain alignment' },
        { signal: 'penalty:language_visa_flag', contribution: -5, detail: 'Language verification' },
      ],
      prerequisite_conditions: [
        { kind: 'language_cefr', status: 'unknown', reason: 'Verify German level', job_quote: 'Deutsch C1' },
      ],
    },
  }))
  assert.match(ledger, /Raw score[^<]*<[^>]*>30/)
  assert.match(ledger, /Calibrated score[^<]*<[^>]*>70\/100/)
  assert.match(ledger, /Build APIs/)
  assert.match(ledger, /Built APIs/)
  assert.match(ledger, /Other contributions/)
  assert.match(ledger, /Language verification/)
  assert.match(ledger, /Deutsch C1/)
  assert.match(ledger, /Verify language proficiency/)
  assert.doesNotMatch(localEvidence, /Raw score|Calibrated score/)
  console.log('Local match display assertions passed')
} finally {
  await server.close()
}
