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
  assert.match(render('llm'), /AI matched/)
  assert.doesNotMatch(render('local'), /AI matched/)
  assert.doesNotMatch(render(undefined), /AI matched/)
} finally {
  await server.close()
}
