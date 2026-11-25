#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

printf '%s\n' 'Checking workflow and shell syntax'
actionlint .github/workflows/*.yml
shellcheck scripts/ci/*.sh
python scripts/ci/release_notes.py

printf '%s\n' 'Compiling Python source and running regression tests'
python -m compileall -q hiring_scraper scripts/ci
# Every database test runs on PostgreSQL (tests/pg.py migrates and empties it).
python scripts/ci/test_database.py
python -m unittest discover -s tests -v

printf '%s\n' 'Checking profile behavior with Node and the real Python matcher'
node frontend/test-match-display.mjs
node tests/frontend_profile_defaults.mjs
node tests/frontend_profile_languages.mjs
node tests/frontend_profile_onboarding.mjs
node tests/frontend_applications.mjs
node tests/frontend_application_interactions.mjs
node tests/frontend_application_form.mjs
node tests/frontend_application_documents.mjs
node tests/frontend_documents_editing.mjs
node tests/frontend_documents_types.mjs
node tests/frontend_todays_focus.mjs
npm --prefix extensions/application-companion run check
npm --prefix extensions/application-companion test
# The app image already runs the TypeScript/Vite build and its output is served
# by the PostgreSQL smoke stack. Browser shards build their own tested bundle.
